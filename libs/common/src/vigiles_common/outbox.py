"""Transactional Outbox relay for outgoing HTTP calls.

A service writes the call into its ``outbox`` table in the same
transaction as the state change, so there is never a "committed but not
sent" or "sent but rolled back" state. The relay:

1. leases due rows with ``FOR UPDATE SKIP LOCKED`` and ``locked_until`` -
   several instances never send the same row, and rows of a crashed
   instance are released when the lease expires,
2. sends them outside the transaction with ``Idempotency-Key = message_id``,
3. records the outcome: ``sent``, a retry with backoff, or ``dead``.
"""

import random
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

from vigiles_common.logging import bound_context, get_logger
from vigiles_common.retry import Decision, RetryPolicy, classify_exception, classify_status, parse_retry_after

log = get_logger(__name__)

Connect = Callable[[], AbstractContextManager[psycopg.Connection]]


@dataclass(frozen=True)
class Delivery:
    status: str  # sent | retry | dead
    error: str | None = None
    delay_s: float = 0.0


class OutboxRelay:
    def __init__(
        self,
        connect: Connect,
        http: httpx.Client,
        instance: str,
        token: Callable[[], str | None] = lambda: None,
        invalidate_token: Callable[[], None] = lambda: None,
        policy: RetryPolicy = RetryPolicy(),
        lease_s: float = 30.0,
        batch: int = 20,
        rng: random.Random | None = None,
        on_dead: Callable[[psycopg.Connection, dict[str, Any], str], None] | None = None,
    ) -> None:
        self.connect = connect
        self.http = http
        self.instance = instance
        self.token = token
        self.invalidate_token = invalidate_token
        self.policy = policy
        self.lease_s = lease_s
        self.batch = batch
        self.rng = rng or random.Random()
        self.on_dead = on_dead

    def lease(self) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.transaction():
            conn.row_factory = dict_row
            return conn.execute(
                """
                UPDATE outbox SET locked_by = %s, locked_until = now() + make_interval(secs => %s)
                WHERE id IN (
                    SELECT id FROM outbox
                    WHERE status = 'pending' AND next_attempt_at <= now()
                      AND (locked_until IS NULL OR locked_until < now())
                    ORDER BY id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (self.instance, self.lease_s, self.batch),
            ).fetchall()

    def deliver(self, row: dict[str, Any]) -> Delivery:
        headers = {**row["headers"], "Idempotency-Key": str(row["message_id"])}
        if row.get("correlation_id"):
            headers["X-Correlation-ID"] = row["correlation_id"]
        token = self.token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        attempt = row["attempts"]
        try:
            response = self.http.request(row["method"], row["target_url"], json=row["payload"], headers=headers)
        except Exception as exc:  # noqa: BLE001 - classified below
            if classify_exception(exc) is Decision.RETRY:
                return Delivery("retry", f"{type(exc).__name__}: {exc}", self.policy.delay(attempt, rng=self.rng))
            return Delivery("dead", f"{type(exc).__name__}: {exc}")
        if response.is_success:
            return Delivery("sent")
        error = f"HTTP {response.status_code}: {response.text[:300]}"
        decision = classify_status(response.status_code)
        if decision is Decision.REFRESH_AND_RETRY:
            self.invalidate_token()
            return Delivery("retry", error, 0.0)
        if decision is Decision.RETRY:
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            return Delivery("retry", error, self.policy.delay(attempt, retry_after, self.rng))
        return Delivery("dead", error)

    def record(self, row: dict[str, Any], delivery: Delivery) -> None:
        attempts = row["attempts"] + 1
        status = delivery.status
        if status == "retry" and self.policy.exhausted(attempts):
            status = "dead"
        with self.connect() as conn, conn.transaction():
            conn.row_factory = dict_row
            if status == "sent":
                conn.execute(
                    "UPDATE outbox SET status = 'sent', attempts = %s, sent_at = now(), locked_by = NULL, "
                    "locked_until = NULL, last_error = NULL WHERE id = %s",
                    (attempts, row["id"]),
                )
            elif status == "retry":
                conn.execute(
                    "UPDATE outbox SET attempts = %s, last_error = %s, locked_by = NULL, locked_until = NULL, "
                    "next_attempt_at = now() + make_interval(secs => %s) WHERE id = %s",
                    (attempts, delivery.error, delivery.delay_s, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE outbox SET status = 'dead', attempts = %s, last_error = %s, locked_by = NULL, "
                    "locked_until = NULL WHERE id = %s",
                    (attempts, delivery.error, row["id"]),
                )
                if self.on_dead:
                    self.on_dead(conn, row, delivery.error or "")
        with bound_context(correlation_id=row.get("correlation_id")):
            event = {"sent": "outbox_sent", "retry": "outbox_retry", "dead": "outbox_dead"}[status]
            (log.info if status == "sent" else log.warning)(
                event, outbox_id=row["id"], kind=row.get("kind"), target=row["target_url"],
                attempts=attempts, error=delivery.error, retry_in_s=round(delivery.delay_s, 2),
            )

    def run_once(self) -> int:
        rows = self.lease()
        for row in rows:
            self.record(row, self.deliver(row))
        return len(rows)

    def run(self, stop: threading.Event, interval_s: float = 1.0) -> None:
        while not stop.is_set():
            try:
                sent = self.run_once()
            except Exception:  # database down etc.; keep the worker alive
                log.exception("outbox_cycle_failed")
                sent = 0
            if sent < self.batch:
                stop.wait(interval_s)

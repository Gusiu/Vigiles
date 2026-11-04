"""Background workers of the registry: change notifier and outbox relay.

The notifier compares ``max(change_log.seq)`` with the last value it
announced and queues a webhook ("doorbell") to the field app in the
outbox. It also covers changes written directly by the importer. The
field app polls every 30 s anyway, so a lost doorbell only delays sync.
"""

import socket
import threading
from contextlib import contextmanager

import httpx
import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from vigiles_common.auth.modes import build_token_provider
from vigiles_common.logging import get_logger
from vigiles_common.outbox import OutboxRelay
from vigiles_registry.db import Database
from vigiles_registry.settings import RegistrySettings

log = get_logger(__name__)


def notify_changes(conn: psycopg.Connection, webhook_url: str) -> int | None:
    """Queue a doorbell when the feed grew. Returns the announced seq, if any."""
    with conn.transaction():
        cur = conn.cursor(row_factory=tuple_row)
        (last,) = cur.execute("SELECT last_seq FROM notifier_state FOR UPDATE").fetchone()
        (latest,) = cur.execute("SELECT coalesce(max(seq), 0) FROM change_log").fetchone()
        if latest <= last:
            return None
        # One pending doorbell is enough: update it instead of piling up
        # rows while the field app is down. Rows being sent are left alone.
        updated = conn.execute(
            """
            UPDATE outbox SET payload = %s
            WHERE id = (SELECT id FROM outbox WHERE kind = 'webhook' AND status = 'pending'
                        AND (locked_until IS NULL OR locked_until < now()) ORDER BY id LIMIT 1)
            """,
            (Jsonb({"latest_seq": latest}),),
        ).rowcount
        if not updated:
            conn.execute(
                "INSERT INTO outbox (kind, target_url, payload) VALUES ('webhook', %s, %s)",
                (webhook_url, Jsonb({"latest_seq": latest})),
            )
        conn.execute("UPDATE notifier_state SET last_seq = %s", (latest,))
    return latest


def start_workers(settings: RegistrySettings, database: Database, stop: threading.Event) -> list[threading.Thread]:
    webhook_url = settings.field_app_url.rstrip("/") + "/api/v1/webhooks/registry"
    tokens = build_token_provider(settings, "svc-registry")

    @contextmanager
    def connect():
        with database.pool.connection() as conn:
            yield conn

    def notifier() -> None:
        while not stop.is_set():
            try:
                with connect() as conn:
                    notify_changes(conn, webhook_url)
            except Exception:
                log.exception("notifier_cycle_failed")
            stop.wait(settings.notifier_interval_s)

    relay = OutboxRelay(
        connect,
        httpx.Client(timeout=settings.http_timeout_s),
        instance=f"registry-{socket.gethostname()}",
        token=tokens.token,
        invalidate_token=tokens.invalidate,
    )
    threads = [
        threading.Thread(target=notifier, name="notifier", daemon=True),
        threading.Thread(target=relay.run, args=(stop, settings.outbox_interval_s), name="outbox", daemon=True),
    ]
    for thread in threads:
        thread.start()
    return threads

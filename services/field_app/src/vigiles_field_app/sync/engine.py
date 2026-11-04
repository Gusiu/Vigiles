"""Synchronisation engine of the field app.

* push: send due ``pending_changes`` in creation order and record the
  result of each (applied, merged, conflict, rejected); transient errors
  keep the change pending with full-jitter backoff,
* pull: read the change feed from the stored cursor, apply entities with
  ``rev > server_rev`` (skipping the echo of our own pushes) and keep
  fields with local edits that the registry has not accepted yet,
* the loop runs on a webhook "doorbell" or every 30 s, and not at all
  while the technician has switched to offline work.
"""

import json
import random
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from vigiles_common.correlation import correlation_scope
from vigiles_common.logging import get_logger
from vigiles_common.retry import RetryPolicy, parse_retry_after
from vigiles_field_app.db import LocalDb
from vigiles_field_app.sync import translator
from vigiles_field_app.sync.client import RegistryClient, RegistryError
from vigiles_field_app.sync.queue import protected_fields

log = get_logger(__name__)

CURSOR = "registry_since"
OFFLINE = "offline"
_TRANSIENT_STATUS = {401, 429}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SyncEngine:
    def __init__(self, db: LocalDb, client: RegistryClient, policy: RetryPolicy = RetryPolicy(),
                 rng: random.Random | None = None) -> None:
        self.db = db
        self.client = client
        self.policy = policy
        self.rng = rng or random.Random()
        self.wake = threading.Event()
        self._lock = threading.Lock()  # one sync cycle at a time

    # -- state ---------------------------------------------------------------

    @property
    def offline(self) -> bool:
        return self.db.get_state(OFFLINE, "0") == "1"

    def set_offline(self, offline: bool) -> None:
        self.db.set_state(OFFLINE, "1" if offline else "0")
        if not offline:
            self.wake.set()

    def status(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            counts = dict(conn.execute("SELECT status, count(*) FROM pending_changes GROUP BY status").fetchall())
            last = conn.execute("SELECT ts, direction, outcome, details FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
            last_ok = conn.execute("SELECT ts FROM sync_log WHERE outcome = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "offline": self.offline,
            "pending": counts.get("pending", 0),
            "conflicts": counts.get("conflict", 0),
            "rejected": counts.get("rejected", 0),
            "cursor": int(self.db.get_state(CURSOR, "0")),
            "last_sync_ok": last_ok["ts"] if last_ok else None,
            "last_event": dict(last) if last else None,
        }

    # -- cycle ---------------------------------------------------------------

    def sync_once(self) -> dict[str, Any]:
        if self.offline:
            return {"skipped": "offline"}
        with self._lock, correlation_scope():
            pushed = self.push()
            pulled = self.pull()
            return {"pushed": pushed, "pulled": pulled}

    def run(self, stop: threading.Event, interval_s: float) -> None:
        log.info("sync_worker_started", interval_s=interval_s)
        while not stop.is_set():
            self.wake.clear()
            try:
                self.sync_once()
            except Exception:
                log.exception("sync_cycle_failed")
            self.wake.wait(self._next_wait(interval_s))
        log.info("sync_worker_stopped")

    def _next_wait(self, interval_s: float) -> float:
        """Wake up earlier when a pending change becomes due before the interval."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT min(next_attempt_at) FROM pending_changes WHERE status = 'pending'"
            ).fetchone()[0]
        if not row:
            return interval_s
        due = datetime.strptime(row, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        return max(0.05, min(interval_s, (due - _now()).total_seconds()))

    # -- push ----------------------------------------------------------------

    def push(self) -> int:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_changes WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY seq LIMIT 200",
                (_iso(_now()),),
            ).fetchall()
            if not rows:
                return 0
            conn.executemany("UPDATE pending_changes SET sending = 1 WHERE change_id = ?", [(r["change_id"],) for r in rows])
        batch = [
            {"change_id": r["change_id"], "entity": r["entity"], "id": r["entity_id"],
             "base_rev": r["base_rev"], "patch": json.loads(r["patch"])}
            for r in rows
        ]
        try:
            results = self.client.push(batch)
        except (httpx.TransportError, RegistryError) as exc:
            self._push_failed(rows, exc)
            return 0
        with self.db.transaction() as conn:
            by_id = {r["change_id"]: r for r in rows}
            for result in results:
                self._record_result(conn, by_id[str(result["change_id"])], result)
            self.db.log(conn, "push", "ok", f"{len(results)} change(s)")
        log.info("sync_pushed", changes=len(results), statuses=[r["status"] for r in results])
        return len(results)

    def _push_failed(self, rows: list[sqlite3.Row], exc: Exception) -> None:
        status = getattr(exc, "status", None)
        transient = status is None or status >= 500 or status in _TRANSIENT_STATUS
        retry_after = parse_retry_after(getattr(exc, "retry_after", None))
        with self.db.transaction() as conn:
            for row in rows:
                if transient:
                    delay = self.policy.delay(row["attempts"], retry_after, self.rng)
                    conn.execute(
                        "UPDATE pending_changes SET sending = 0, attempts = attempts + 1, last_error = ?, "
                        "next_attempt_at = ? WHERE change_id = ?",
                        (str(exc), _iso(_now() + timedelta(seconds=delay)), row["change_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE pending_changes SET sending = 0, status = 'rejected', attempts = attempts + 1, "
                        "last_error = ? WHERE change_id = ?",
                        (str(exc), row["change_id"]),
                    )
            self.db.log(conn, "push", "retry" if transient else "rejected", str(exc))
        log.warning("sync_push_failed", error=str(exc), transient=transient, changes=len(rows))

    def _record_result(self, conn: sqlite3.Connection, row: sqlite3.Row, result: dict[str, Any]) -> None:
        status = result["status"]
        local = {"applied": "sent", "merged": "sent", "conflict": "conflict", "rejected": "rejected"}[status]
        error = json.dumps(result.get("problem"), ensure_ascii=False) if status == "rejected" else None
        conflict_ids = ",".join(result.get("conflict_ids", [])) or None
        conn.execute(
            "UPDATE pending_changes SET status = ?, sending = 0, attempts = attempts + 1, last_error = ?, "
            "conflict_id = ? WHERE change_id = ?",
            (local, error, conflict_ids, row["change_id"]),
        )
        # Only a plain "applied" means the registry state equals ours at
        # new_rev. After "merged" or "conflict" the registry also holds
        # changes we have not seen, so the snapshot must come from the feed.
        if status == "applied" and "new_rev" in result:
            table = {"asset": "assets", "work_order": "tasks"}.get(row["entity"])
            if table:
                conn.execute(
                    f"UPDATE {table} SET server_rev = max(server_rev, ?) WHERE id = ?",  # noqa: S608 - fixed names
                    (result["new_rev"], row["entity_id"]),
                )
        if status == "rejected":
            log.warning("sync_change_rejected", change_id=row["change_id"], problem=result.get("problem"))

    # -- pull ----------------------------------------------------------------

    def pull(self) -> int:
        applied = 0
        while True:
            since = int(self.db.get_state(CURSOR, "0"))
            try:
                page = self.client.changes(since)
            except (httpx.TransportError, RegistryError) as exc:
                with self.db.transaction() as conn:
                    self.db.log(conn, "pull", "error", str(exc))
                log.warning("sync_pull_failed", error=str(exc))
                return applied
            with self.db.transaction() as conn:
                for change in page["changes"]:
                    applied += self._apply(conn, change)
                self.db.set_state(CURSOR, str(page["next_since"]), conn)
                self.db.log(conn, "pull", "ok", f"since {since}: {len(page['changes'])} change(s)")
            if not page["has_more"]:
                break
        if applied:
            log.info("sync_pulled", applied=applied)
        return applied

    def _apply(self, conn: sqlite3.Connection, change: dict[str, Any]) -> int:
        entity, data, rev = change["entity"], change["data"], change["rev"]
        if entity == "asset":
            return self._apply_asset(conn, data, rev)
        if entity == "work_order":
            return self._apply_task(conn, data, rev)
        if entity == "inspection":
            return conn.execute(
                "INSERT OR IGNORE INTO inspections (id, task_id, asset_id, result, findings, performed_at, technician) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data["id"], data.get("work_order_id"), data["asset_id"], data["result"], data.get("findings"),
                 data["performed_at"], data["technician"]),
            ).rowcount
        if entity == "conflict":
            return self._apply_conflict(conn, data)
        return 0

    def _apply_asset(self, conn: sqlite3.Connection, data: dict[str, Any], rev: int) -> int:
        row = conn.execute("SELECT server_rev FROM assets WHERE id = ?", (data["id"],)).fetchone()
        if row and rev <= row["server_rev"]:
            return 0  # echo of our own change or an older revision
        local = translator.asset_from_registry(data)
        if row is None:
            conn.execute(
                "INSERT INTO assets (id, code, label, condition, notes, server_rev) VALUES (:id, :code, :label, :condition, :notes, :server_rev)",
                local,
            )
            return 1
        keep = protected_fields(conn, "asset", data["id"])
        columns = ["code", "label", "server_rev"] + [c for c in ("condition", "notes") if c not in keep]
        conn.execute(
            f"UPDATE assets SET {', '.join(f'{c} = :{c}' for c in columns)} WHERE id = :id",  # noqa: S608 - fixed names
            local,
        )
        return 1

    def _apply_task(self, conn: sqlite3.Connection, data: dict[str, Any], rev: int) -> int:
        row = conn.execute("SELECT server_rev FROM tasks WHERE id = ?", (data["id"],)).fetchone()
        if row and rev <= row["server_rev"]:
            return 0
        asset = conn.execute("SELECT code FROM assets WHERE id = ?", (data["asset_id"],)).fetchone()
        local = translator.task_from_registry(data, asset["code"] if asset else None)
        if row is None:
            conn.execute(
                "INSERT INTO tasks (id, asset_id, title, reason, status, due_date, assigned_to, description, server_rev) "
                "VALUES (:id, :asset_id, :title, :reason, :status, :due_date, :assigned_to, :description, :server_rev)",
                local,
            )
            return 1
        columns = ["title", "reason", "due_date", "assigned_to", "description", "server_rev"]
        if "status" not in protected_fields(conn, "work_order", data["id"]):
            columns.append("status")
        conn.execute(
            f"UPDATE tasks SET {', '.join(f'{c} = :{c}' for c in columns)} WHERE id = :id",  # noqa: S608 - fixed names
            local,
        )
        return 1

    def _apply_conflict(self, conn: sqlite3.Connection, data: dict[str, Any]) -> int:
        if data["status"] != "resolved":
            return 0
        change = conn.execute(
            "SELECT change_id FROM pending_changes WHERE change_id = ? AND status = 'conflict'",
            (data["client_change_id"],),
        ).fetchone()
        if change is None:
            return 0
        # The resolved value is authoritative; take it even if the entity
        # revision carrying it was skipped while the conflict was open.
        if data["entity"] == "asset":
            local_field = translator.LOCAL_FIELD["asset"].get(data["field"])
            if local_field:
                value = data["resolved_value"]
                if local_field == "condition" and value is not None:
                    value = translator.CONDITION[value]
                conn.execute(f"UPDATE assets SET {local_field} = ? WHERE id = ?", (value, data["entity_id"]))  # noqa: S608
        conn.execute("UPDATE pending_changes SET status = 'resolved' WHERE change_id = ?", (change["change_id"],))
        return 1

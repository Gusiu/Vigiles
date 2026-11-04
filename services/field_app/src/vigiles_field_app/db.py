"""Local SQLite database of the field app - deliberately a different model
from the registry (PLAN.md 7.2). The app keeps working on it while the
registry or the network is unavailable."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id         TEXT PRIMARY KEY,
    code       TEXT NOT NULL,
    label      TEXT NOT NULL,          -- composed location, read only
    condition  TEXT NOT NULL CHECK (condition IN ('OK', 'NEEDS_REPAIR', 'FAILED', 'RETIRED')),
    notes      TEXT,
    server_rev INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,      -- work_order_id in the registry
    asset_id    TEXT NOT NULL,
    title       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('NEW', 'ASSIGNED', 'IN_PROGRESS', 'DONE', 'CANCELLED')),
    due_date    TEXT,
    assigned_to TEXT,
    description TEXT,
    server_rev  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS inspections (
    id           TEXT PRIMARY KEY,     -- UUID generated here, no id mapping needed
    task_id      TEXT,
    asset_id     TEXT NOT NULL,
    result       TEXT NOT NULL,
    findings     TEXT,
    performed_at TEXT NOT NULL,
    technician   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_changes (
    change_id       TEXT PRIMARY KEY,
    entity          TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    base_rev        INTEGER,
    patch           TEXT NOT NULL,     -- JSON in the registry model
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sent', 'conflict', 'rejected', 'resolved')),
    sending         INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_error      TEXT,
    conflict_id     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    seq             INTEGER
);
CREATE INDEX IF NOT EXISTS pending_changes_status_idx ON pending_changes (status, next_attempt_at);
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sync_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    direction TEXT NOT NULL,
    outcome   TEXT NOT NULL,
    details   TEXT
);
"""


class LocalDb:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
        sql = "INSERT INTO sync_state (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        if conn is not None:
            conn.execute(sql, (key, value))
            return
        with self.connect() as c:
            c.execute(sql, (key, value))

    def log(self, conn: sqlite3.Connection, direction: str, outcome: str, details: str = "") -> None:
        conn.execute("INSERT INTO sync_log (direction, outcome, details) VALUES (?, ?, ?)", (direction, outcome, details))

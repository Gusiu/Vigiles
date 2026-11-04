"""Local changes queued for the registry (``pending_changes``).

Consecutive edits of the same entity are folded into one unsent change,
keeping its ``base_rev``. Otherwise the second edit would look to the
registry like a concurrent change of the first one and conflict with it.
A change that is being sent is never modified.
"""

import json
import sqlite3
import uuid
from typing import Any


def queue_change(conn: sqlite3.Connection, entity: str, entity_id: str, base_rev: int | None,
                 patch: dict[str, Any]) -> str:
    row = conn.execute(
        """
        SELECT change_id, patch FROM pending_changes
        WHERE entity = ? AND entity_id = ? AND status = 'pending' AND sending = 0 AND attempts = 0
        ORDER BY seq DESC LIMIT 1
        """,
        (entity, entity_id),
    ).fetchone()
    if row and entity != "inspection":
        merged = {**json.loads(row["patch"]), **patch}
        conn.execute("UPDATE pending_changes SET patch = ? WHERE change_id = ?", (json.dumps(merged), row["change_id"]))
        return row["change_id"]
    change_id = str(uuid.uuid4())
    seq = conn.execute("SELECT coalesce(max(seq), 0) + 1 FROM pending_changes").fetchone()[0]
    conn.execute(
        "INSERT INTO pending_changes (change_id, entity, entity_id, base_rev, patch, seq) VALUES (?, ?, ?, ?, ?, ?)",
        (change_id, entity, entity_id, base_rev, json.dumps(patch), seq),
    )
    return change_id


def protected_fields(conn: sqlite3.Connection, entity: str, entity_id: str) -> set[str]:
    """Registry fields with local edits not yet accepted by the registry."""
    fields: set[str] = set()
    for row in conn.execute(
        "SELECT patch FROM pending_changes WHERE entity = ? AND entity_id = ? AND status IN ('pending', 'conflict')",
        (entity, entity_id),
    ):
        fields |= set(json.loads(row["patch"]))
    return fields

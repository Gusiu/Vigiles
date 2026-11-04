"""Change feed: ``GET /sync/changes?since=N``.

Each entry carries the entity snapshot at that revision. A client keeps
the highest revision per entity and skips revisions it already knows
(``rev <= server_rev``), which breaks the echo loop of its own changes.
"""

from typing import Any

import psycopg


def read_changes(conn: psycopg.Connection, since: int, limit: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT c.seq, c.entity, c.entity_id AS id, c.rev, c.op, h.data
        FROM change_log c
        JOIN entity_history h ON (h.entity, h.entity_id, h.rev) = (c.entity, c.entity_id, c.rev)
        WHERE c.seq > %s
        ORDER BY c.seq
        LIMIT %s
        """,
        (since, limit + 1),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    changes = [{**r, "id": str(r["id"])} for r in rows]
    return {
        "changes": changes,
        "next_since": changes[-1]["seq"] if changes else since,
        "has_more": has_more,
    }


def latest_seq(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT coalesce(max(seq), 0) AS seq FROM change_log").fetchone()["seq"]

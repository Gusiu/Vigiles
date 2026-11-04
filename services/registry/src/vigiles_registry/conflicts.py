"""Conflict queue: a manager picks the server value, the client value or
a value of their own. The choice is written like any office change and the
resolved conflict appears in the change feed, so the field app updates
its state and removes the conflict badge."""

from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from vigiles_common.problems import ProblemError
from vigiles_registry.events import record_event

_TABLES = {"asset": "assets", "work_order": "work_orders"}


def list_conflicts(conn: psycopg.Connection, status: str | None = "open") -> list[dict[str, Any]]:
    query = """
        SELECT c.*, a.code AS asset_code
        FROM conflicts c LEFT JOIN assets a ON c.entity = 'asset' AND a.id = c.entity_id
    """
    params: tuple = ()
    if status:
        query += " WHERE c.status = %s"
        params = (status,)
    return conn.execute(query + " ORDER BY c.created_at", params).fetchall()


def resolve_conflict(
    conn: psycopg.Connection,
    conflict_id: str,
    choice: Literal["server", "client", "custom"],
    value: Any,
    resolved_by: str,
) -> dict[str, Any]:
    conflict = conn.execute("SELECT * FROM conflicts WHERE id = %s FOR UPDATE", (conflict_id,)).fetchone()
    if conflict is None:
        raise ProblemError(404, "Not Found", f"conflict {conflict_id} does not exist")
    if conflict["status"] != "open":
        raise ProblemError(409, "Conflict", "conflict is already resolved")
    chosen = {"server": conflict["server_value"], "client": conflict["client_value"]}.get(choice, value)
    table = _TABLES[conflict["entity"]]
    conn.execute(
        sql.SQL("UPDATE {} SET {} = %s, updated_by = %s WHERE id = %s AND {} IS DISTINCT FROM %s").format(
            sql.Identifier(table), sql.Identifier(conflict["field"]), sql.Identifier(conflict["field"])
        ),
        (chosen, resolved_by, conflict["entity_id"], chosen),
    )
    resolved = conn.execute(
        """
        UPDATE conflicts SET status = 'resolved', resolved_by = %s, resolved_value = %s,
               resolved_at = now(), updated_by = %s
        WHERE id = %s RETURNING *
        """,
        (resolved_by, Jsonb(chosen), resolved_by, conflict_id),
    ).fetchone()
    record_event(conn, "conflict_resolved", "ok", conflict_id=str(conflict_id), choice=choice,
                 field=conflict["field"], resolved_by=resolved_by)
    return resolved

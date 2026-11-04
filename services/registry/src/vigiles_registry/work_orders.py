"""Work orders created by managers (UI) and by services (hub)."""

from typing import Any

import psycopg

from vigiles_common.correlation import current_correlation_id
from vigiles_common.problems import ProblemError
from vigiles_registry.events import record_event

OPEN = ("nowe", "przydzielone", "w_realizacji")


def find_asset(conn: psycopg.Connection, asset_code: str | None, asset_id: str | None) -> dict[str, Any]:
    if asset_id:
        row = conn.execute("SELECT id, code FROM assets WHERE id = %s", (asset_id,)).fetchone()
    else:
        row = conn.execute("SELECT id, code FROM assets WHERE code = %s", ((asset_code or "").upper(),)).fetchone()
    if row is None:
        raise ProblemError(422, "Unprocessable Content", f"asset {asset_code or asset_id} does not exist")
    return row


def create_work_order(
    conn: psycopg.Connection,
    *,
    asset_code: str | None = None,
    asset_id: str | None = None,
    reason: str,
    assigned_to: str | None = None,
    due_date: Any = None,
    description: str | None = None,
    source: str,
    created_by: str,
) -> tuple[dict[str, Any], bool]:
    """Return (order, created). An open order for the same asset and reason
    is returned instead of creating a duplicate."""
    asset = find_asset(conn, asset_code, asset_id)
    existing = conn.execute(
        "SELECT * FROM work_orders WHERE asset_id = %s AND reason = %s AND status = ANY(%s)",
        (asset["id"], reason, list(OPEN)),
    ).fetchone()
    if existing:
        return existing, False
    status = "przydzielone" if assigned_to else "nowe"
    try:
        with conn.transaction():
            order = conn.execute(
                """
                INSERT INTO work_orders (asset_id, reason, status, assigned_to, due_date, description,
                                         source, correlation_id, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
                """,
                (asset["id"], reason, status, assigned_to, due_date, description, source,
                 current_correlation_id(), created_by),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        # Created concurrently by another request: return that one.
        existing = conn.execute(
            "SELECT * FROM work_orders WHERE asset_id = %s AND reason = %s AND status = ANY(%s)",
            (asset["id"], reason, list(OPEN)),
        ).fetchone()
        return existing, False
    except psycopg.errors.CheckViolation as exc:
        raise ProblemError(422, "Unprocessable Content", exc.diag.message_primary) from exc
    record_event(conn, "work_order_created", "ok", work_order_id=str(order["id"]),
                 asset_code=asset["code"], reason=reason, source=source)
    return order, True


def assign_work_order(conn: psycopg.Connection, order_id: str, assigned_to: str | None,
                      due_date: Any, by: str) -> dict[str, Any]:
    order = conn.execute(
        """
        UPDATE work_orders SET assigned_to = %s, due_date = %s, updated_by = %s,
            status = CASE WHEN status = 'nowe' AND %s IS NOT NULL THEN 'przydzielone' ELSE status END
        WHERE id = %s AND status = ANY(%s) RETURNING *
        """,
        (assigned_to, due_date, by, assigned_to, order_id, list(OPEN)),
    ).fetchone()
    if order is None:
        raise ProblemError(409, "Conflict", "only open work orders can be assigned")
    return order


def cancel_work_order(conn: psycopg.Connection, order_id: str, by: str) -> dict[str, Any]:
    order = conn.execute(
        "UPDATE work_orders SET status = 'anulowane', updated_by = %s WHERE id = %s AND status = ANY(%s) RETURNING *",
        (by, order_id, list(OPEN)),
    ).fetchone()
    if order is None:
        raise ProblemError(409, "Conflict", "only open work orders can be cancelled")
    return order

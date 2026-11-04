"""``POST /sync/push``: apply a batch of client changes.

Each change runs in its own savepoint, so a rejected change does not undo
the others, and its result is stored in ``applied_changes`` - a retried
push with the same ``change_id`` returns the stored result (Idempotent
Receiver) instead of applying the change twice.
"""

import uuid
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from vigiles_registry.events import record_event
from vigiles_registry.sync.merge import PatchError, three_way_merge

_TABLES = {"asset": "assets", "work_order": "work_orders"}
_INSPECTION_FIELDS = {"work_order_id", "asset_id", "technician", "performed_at", "result", "findings"}


def _problem(detail: str, status: int = 422) -> dict[str, Any]:
    return {"status": "rejected", "problem": {"title": "Change rejected", "status": status, "detail": detail}}


def apply_push(conn: psycopg.Connection, changes: list[dict[str, Any]], client: str) -> list[dict[str, Any]]:
    results = []
    for change in changes:
        change_id = change["change_id"]
        stored = conn.execute(
            "SELECT result FROM applied_changes WHERE change_id = %s", (change_id,)
        ).fetchone()
        if stored:
            results.append({"change_id": change_id, **stored["result"]})
            continue
        try:
            with conn.transaction():
                result = _apply_one(conn, change)
        except PatchError as exc:
            result = _problem(str(exc))
        except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as exc:
            result = _problem(exc.diag.message_primary or str(exc))
        with conn.transaction():
            conn.execute(
                "INSERT INTO applied_changes (change_id, result) VALUES (%s, %s)",
                (change_id, Jsonb(result)),
            )
            record_event(
                conn, "sync_push_change", result["status"], change_id=str(change_id),
                entity=change["entity"], entity_id=str(change["id"]), client=client,
            )
        results.append({"change_id": change_id, **result})
    return results


def _apply_one(conn: psycopg.Connection, change: dict[str, Any]) -> dict[str, Any]:
    entity, entity_id, patch = change["entity"], change["id"], change["patch"]
    if entity == "inspection":
        return _create_inspection(conn, entity_id, patch)
    table = _TABLES.get(entity)
    if table is None:
        raise PatchError(f"entity {entity!r} cannot be changed through sync")

    current = conn.execute(
        sql.SQL("SELECT to_jsonb(t) AS data FROM {} t WHERE id = %s FOR UPDATE").format(sql.Identifier(table)),
        (entity_id,),
    ).fetchone()
    if current is None:
        return _problem(f"{entity} {entity_id} does not exist", 404)
    current = current["data"]
    base_rev = change.get("base_rev")
    base = conn.execute(
        "SELECT data FROM entity_history WHERE entity = %s AND entity_id = %s AND rev = %s",
        (entity, entity_id, base_rev),
    ).fetchone()
    if base is None:
        return _problem(f"unknown base_rev {base_rev} for {entity} {entity_id}", 409)

    merge = three_way_merge(entity, base["data"], current, patch)
    new_rev = current["rev"]
    if merge.apply:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(name)) for name in merge.apply
        )
        new_rev = conn.execute(
            sql.SQL(
                "UPDATE {} SET {}, updated_by = current_setting('vigiles.origin', true) "
                "WHERE id = %s RETURNING rev"
            ).format(sql.Identifier(table), assignments),
            [*merge.apply.values(), entity_id],
        ).fetchone()["rev"]
    if merge.kept_server:
        record_event(conn, "sync_office_field_kept", "ignored", entity=entity, entity_id=str(entity_id),
                     fields=merge.kept_server, change_id=str(change["change_id"]))

    if merge.conflicts:
        ids = []
        for name, base_value, server_value, client_value in merge.conflicts:
            ids.append(str(conn.execute(
                """
                INSERT INTO conflicts (entity, entity_id, field, base_value, server_value,
                                       client_value, client_change_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (entity, entity_id, name, Jsonb(base_value), Jsonb(server_value),
                 Jsonb(client_value), change["change_id"]),
            ).fetchone()["id"]))
        return {"status": "conflict", "conflict_ids": ids, "new_rev": new_rev}
    if merge.server_fields or merge.kept_server:
        return {"status": "merged", "new_rev": new_rev,
                "server_fields": sorted(set(merge.server_fields) | set(merge.kept_server))}
    return {"status": "applied", "new_rev": new_rev}


def _create_inspection(conn: psycopg.Connection, entity_id: Any, patch: dict[str, Any]) -> dict[str, Any]:
    unknown = set(patch) - _INSPECTION_FIELDS
    if unknown:
        raise PatchError(f"unknown inspection field(s): {', '.join(sorted(unknown))}")
    try:
        uuid.UUID(str(entity_id))
    except ValueError as exc:
        raise PatchError("inspection id must be a UUID generated by the client") from exc
    existing = conn.execute("SELECT rev FROM inspections WHERE id = %s", (entity_id,)).fetchone()
    if existing:
        return {"status": "applied", "new_rev": existing["rev"]}
    missing = {"asset_id", "technician", "performed_at", "result"} - set(patch)
    if missing:
        raise PatchError(f"missing inspection field(s): {', '.join(sorted(missing))}")
    rev = conn.execute(
        """
        INSERT INTO inspections (id, work_order_id, asset_id, technician, performed_at, result, findings, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, current_setting('vigiles.origin', true))
        RETURNING rev
        """,
        (entity_id, patch.get("work_order_id"), patch["asset_id"], patch["technician"],
         patch["performed_at"], patch["result"], patch.get("findings")),
    ).fetchone()["rev"]
    return {"status": "applied", "new_rev": rev}

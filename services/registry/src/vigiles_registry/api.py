"""Registry REST API (``/api/v1``), contract in PLAN.md section 6.1."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from vigiles_common.auth import Principal, require
from vigiles_common.correlation import current_correlation_id
from vigiles_registry import conflicts, work_orders
from vigiles_registry.db import Database
from vigiles_registry.events import record_event
from vigiles_registry.idempotency import require_key, run_once
from vigiles_registry.sync.feed import read_changes
from vigiles_registry.sync.push import apply_push

router = APIRouter(prefix="/api/v1")


def db(request: Request) -> Database:
    return request.app.state.db


DB = Annotated[Database, Depends(db)]


class WorkOrderIn(BaseModel):
    asset_code: str | None = None
    asset_id: UUID | None = None
    reason: Literal["przeglad_okresowy", "usterka_urzadzenia", "czyszczenie", "brak_lacznosci"]
    assigned_to: str | None = None
    due_date: date | None = None
    description: str | None = Field(default=None, max_length=2000)


class IncidentIn(BaseModel):
    kind: Literal["alarm_potwierdzony", "alarm_wstepny"]
    site: str
    zone: str
    detectors: list[str] = Field(min_length=1)
    first_at: datetime
    decided_at: datetime


class ChangeIn(BaseModel):
    change_id: UUID
    entity: str
    id: UUID
    base_rev: int | None = None
    patch: dict[str, Any]


class PushIn(BaseModel):
    changes: list[ChangeIn] = Field(max_length=500)


class ResolveIn(BaseModel):
    choice: Literal["server", "client", "custom"]
    value: Any = None


@router.get("/devices")
def list_devices(database: DB, _: Principal = Depends(require("devices:read"))) -> list[dict]:
    with database.transaction() as conn:
        return conn.execute(
            """
            SELECT d.device_id, d.vendor, d.gateway_id, a.code AS asset_code, s.code AS site, z.code AS zone
            FROM devices d
            LEFT JOIN assets a ON a.id = d.asset_id
            LEFT JOIN sites s ON s.id = a.site_id
            LEFT JOIN zones z ON z.id = a.zone_id
            ORDER BY d.device_id
            """
        ).fetchall()


@router.get("/assets")
def list_assets(database: DB, _: Principal = Depends(require("assets:read"))) -> list[dict]:
    with database.transaction() as conn:
        return jsonable_encoder(conn.execute("SELECT * FROM assets ORDER BY code").fetchall())


@router.post("/work-orders")
def create_work_order(
    body: WorkOrderIn,
    database: DB,
    principal: Principal = Depends(require("work-orders:create")),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    key = require_key(idempotency_key)
    with database.transaction(origin=principal.subject) as conn:
        def action() -> tuple[int, Any]:
            order, created = work_orders.create_work_order(
                conn, asset_code=body.asset_code, asset_id=str(body.asset_id) if body.asset_id else None,
                reason=body.reason, assigned_to=body.assigned_to, due_date=body.due_date,
                description=body.description, source=principal.subject, created_by=principal.subject,
            )
            return (201 if created else 200), order

        status, payload, _ = run_once(conn, principal.subject, key, action)
    return JSONResponse(payload, status_code=status)


@router.post("/incidents")
def create_incident(
    body: IncidentIn,
    database: DB,
    principal: Principal = Depends(require("incidents:create")),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    key = require_key(idempotency_key)
    with database.transaction(origin=principal.subject) as conn:
        def action() -> tuple[int, Any]:
            incident = conn.execute(
                """
                INSERT INTO incidents (kind, site, zone, detectors, first_at, decided_at, correlation_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
                """,
                (body.kind, body.site, body.zone, body.detectors, body.first_at, body.decided_at,
                 current_correlation_id()),
            ).fetchone()
            record_event(conn, "incident_created", "ok", incident_id=str(incident["id"]), kind=body.kind)
            return 201, incident

        status, payload, _ = run_once(conn, principal.subject, key, action)
    return JSONResponse(payload, status_code=status)


@router.get("/sync/changes")
def sync_changes(
    database: DB,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    _: Principal = Depends(require("sync:read")),
) -> dict:
    with database.transaction() as conn:
        return jsonable_encoder(read_changes(conn, since, limit))


@router.post("/sync/push")
def sync_push(body: PushIn, database: DB, principal: Principal = Depends(require("sync:push"))) -> dict:
    with database.transaction(origin=principal.subject) as conn:
        results = apply_push(conn, [c.model_dump() for c in body.changes], principal.subject)
    return jsonable_encoder({"results": results})


@router.get("/conflicts")
def get_conflicts(
    database: DB, status: Literal["open", "resolved"] | None = "open",
    _: Principal = Depends(require("conflicts:read")),
) -> list[dict]:
    with database.transaction() as conn:
        return jsonable_encoder(conflicts.list_conflicts(conn, status))


@router.post("/conflicts/{conflict_id}/resolve")
def resolve(
    conflict_id: UUID, body: ResolveIn, database: DB,
    principal: Principal = Depends(require("conflicts:resolve")),
) -> dict:
    with database.transaction(origin=principal.subject) as conn:
        return jsonable_encoder(
            conflicts.resolve_conflict(conn, str(conflict_id), body.choice, body.value, principal.subject)
        )


@router.get("/imports")
def imports(database: DB, _: Principal = Depends(require("imports:read"))) -> list[dict]:
    with database.transaction() as conn:
        return jsonable_encoder(conn.execute("SELECT * FROM import_runs ORDER BY id DESC").fetchall())

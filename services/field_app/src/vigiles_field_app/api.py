"""Field app API (PLAN.md 6.2)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vigiles_common.auth import Principal, require
from vigiles_common.logging import get_logger

router = APIRouter(prefix="/api/v1")
log = get_logger(__name__)


class Doorbell(BaseModel):
    latest_seq: int


class OfflineIn(BaseModel):
    offline: bool


@router.post("/webhooks/registry", status_code=202)
def registry_webhook(body: Doorbell, request: Request, _: Principal = Depends(require("webhook:notify"))) -> JSONResponse:
    log.info("webhook_received", latest_seq=body.latest_seq)
    request.app.state.engine.wake.set()
    return JSONResponse({"accepted": True}, status_code=202)


@router.get("/sync/status")
def sync_status(request: Request, _: Principal = Depends(require("tasks:read"))) -> dict:
    return request.app.state.engine.status()


@router.post("/sync/offline")
def set_offline(body: OfflineIn, request: Request, _: Principal = Depends(require("inspections:write"))) -> dict:
    request.app.state.engine.set_offline(body.offline)
    return request.app.state.engine.status()

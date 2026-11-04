"""Liveness and readiness endpoints.

``/health/live`` only says the process answers. ``/health/ready`` runs the
registered checks (for example a database ping) and returns ``503`` when
any of them fails, so orchestrators stop routing traffic to the instance.
"""

from collections.abc import Callable, Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from vigiles_common.logging import get_logger
from vigiles_common.problems import MEDIA_TYPE

ReadinessCheck = Callable[[], None]

log = get_logger(__name__)


def health_router(checks: Mapping[str, ReadinessCheck] | None = None) -> APIRouter:
    checks = dict(checks or {})
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready")
    def ready() -> JSONResponse:
        results: dict[str, str] = {}
        for name, check in checks.items():
            try:
                check()
                results[name] = "ok"
            except Exception as exc:  # noqa: BLE001 - any failure means not ready
                log.warning("readiness_check_failed", check=name, error=str(exc))
                results[name] = "failed"
        if all(r == "ok" for r in results.values()):
            return JSONResponse({"status": "ok", "checks": results})
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Service Unavailable",
                "status": 503,
                "checks": results,
            },
            status_code=503,
            media_type=MEDIA_TYPE,
            headers={"Retry-After": "5"},
        )

    return router

"""Errors as ``application/problem+json`` (RFC 9457).

``install_problem_handlers`` turns HTTP errors, request validation errors
and unexpected exceptions into problem documents. ``ServiceUnavailable``
maps to ``503`` with ``Retry-After``, which clients use to back off.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from vigiles_common.correlation import current_correlation_id
from vigiles_common.logging import get_logger

MEDIA_TYPE = "application/problem+json"

log = get_logger(__name__)


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        type_: str = "about:blank",
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type_
        self.headers = headers or {}
        self.extensions = extensions


class ServiceUnavailable(ProblemError):
    def __init__(self, detail: str, retry_after_s: int = 5) -> None:
        super().__init__(
            503,
            "Service Unavailable",
            detail,
            headers={"Retry-After": str(retry_after_s)},
        )


def problem_response(
    status: int,
    title: str,
    detail: str | None = None,
    type_: str = "about:blank",
    instance: str | None = None,
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    if correlation_id := current_correlation_id():
        body["correlation_id"] = correlation_id
    body.update(extensions)
    return JSONResponse(body, status_code=status, media_type=MEDIA_TYPE, headers=headers)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(
            exc.status,
            exc.title,
            exc.detail,
            exc.type,
            instance=request.url.path,
            headers=exc.headers,
            **exc.extensions,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        from http import HTTPStatus

        title = HTTPStatus(exc.status_code).phrase
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
        return problem_response(
            exc.status_code, title, detail, instance=request.url.path, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return problem_response(
            422,
            "Unprocessable Content",
            "Request validation failed",
            instance=request.url.path,
            errors=errors,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return problem_response(500, "Internal Server Error", instance=request.url.path)

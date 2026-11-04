"""Correlation Identifier: one ID follows a request through every service.

The middleware accepts ``X-Correlation-ID`` from the caller or generates a
new one, binds it to the log context and echoes it in the response. Code
that calls other services or writes outbox rows reads it with
``current_correlation_id()``.
"""

import re
import uuid
from contextvars import ContextVar

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Correlation-ID"
_HEADER_KEY = HEADER.lower().encode()
# Accept caller IDs only if they are short and safe to log and forward.
_VALID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_current: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current_correlation_id() -> str | None:
    return _current.get()


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def accept_or_generate(value: str | None) -> str:
    if value and _VALID.match(value):
        return value
    return new_correlation_id()


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(_HEADER_KEY)
        correlation_id = accept_or_generate(incoming.decode("latin-1") if incoming else None)
        token = _current.set(correlation_id)
        log_tokens = structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k != _HEADER_KEY]
                headers.append((_HEADER_KEY, correlation_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.reset_contextvars(**log_tokens)
            _current.reset(token)

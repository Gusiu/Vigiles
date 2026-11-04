"""Structured logging: one JSON object per line on stdout.

Every record carries ``service`` and, when bound, ``correlation_id``, so
logs from all services can be filtered by a single identifier.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog


class _Stdout:
    """Resolve ``sys.stdout`` on every write instead of capturing it once.

    Test runners and process supervisors may swap ``sys.stdout``; a logger
    holding the original stream would then write to a closed file.
    """

    def write(self, data: str) -> int:
        return sys.stdout.write(data)

    def flush(self) -> None:
        sys.stdout.flush()


def configure_logging(service: str, level: str = "INFO", fmt: str = "json") -> None:
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=_Stdout()),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service)

    # Route records from libraries (uvicorn, psycopg) through the same renderer.
    handler = logging.StreamHandler(_Stdout())
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processor=renderer)
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(name)


@contextmanager
def bound_context(**values: Any) -> Iterator[None]:
    """Bind values to every log record emitted inside the block."""
    tokens = structlog.contextvars.bind_contextvars(**values)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)

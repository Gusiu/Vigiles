"""``Idempotency-Key`` for creating calls from services: the first response
is stored per (client, key) and replayed for every retry of that call."""

from collections.abc import Callable
from typing import Any

import psycopg
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from vigiles_common.problems import ProblemError


def require_key(key: str | None) -> str:
    if not key or len(key) > 200:
        raise ProblemError(400, "Bad Request", "Idempotency-Key header is required (max 200 characters)")
    return key


def run_once(
    conn: psycopg.Connection, client: str, key: str, action: Callable[[], tuple[int, Any]]
) -> tuple[int, Any, bool]:
    """Return (status, body, replayed). Runs inside the caller's transaction."""
    # Serialise concurrent requests with the same key.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{client}:{key}",))
    stored = conn.execute(
        "SELECT status_code, response FROM idempotency_keys WHERE client_id = %s AND key = %s",
        (client, key),
    ).fetchone()
    if stored:
        return stored["status_code"], stored["response"], True
    status, body = action()
    body = jsonable_encoder(body)
    conn.execute(
        "INSERT INTO idempotency_keys (client_id, key, status_code, response) VALUES (%s, %s, %s, %s)",
        (client, key, status, Jsonb(body)),
    )
    return status, body, False

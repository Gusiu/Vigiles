"""Integration event log (``integration_events``): what crossed the
boundary of the registry, with the correlation ID of the flow."""

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from vigiles_common.correlation import current_correlation_id
from vigiles_common.logging import get_logger

log = get_logger(__name__)


def record_event(conn: psycopg.Connection, event: str, outcome: str, **details: Any) -> None:
    correlation_id = details.pop("correlation_id", None) or current_correlation_id()
    conn.execute(
        "INSERT INTO integration_events (service, event, correlation_id, outcome, details) "
        "VALUES ('registry', %s, %s, %s, %s)",
        (event, correlation_id, outcome, Jsonb(details)),
    )
    log.info(event, outcome=outcome, **details)

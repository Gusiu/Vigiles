"""Shared parts of the event consumers: envelope parsing, Idempotent
Receiver and the outbox towards the registry."""

import uuid
from typing import Any

import paho.mqtt.client as mqtt
import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from vigiles_common.envelope import NAMESPACE, Envelope
from vigiles_common.mqtt import PermanentError
from vigiles_hub.db import HubDatabase
from vigiles_hub.dlq import dead_letter
from vigiles_hub.events import EventLog


def parse(db: HubDatabase, events: EventLog, message: mqtt.MQTTMessage) -> Envelope:
    try:
        return Envelope.model_validate_json(message.payload)
    except ValidationError as exc:
        with db.transaction() as conn:
            dead_letter(conn, source_topic=message.topic, reason="invalid_envelope", raw=message.payload, error=str(exc))
            events.record(conn, "consumer", "dead_lettered", "invalid_envelope", source=message.topic)
        raise PermanentError("invalid envelope") from exc


def claim(conn: psycopg.Connection, consumer: str, message_id: str, instance: str) -> bool:
    """True the first time a consumer sees a message_id, False for a duplicate."""
    return conn.execute(
        "INSERT INTO processed_messages (consumer, message_id, instance) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING RETURNING 1",
        (consumer, message_id, instance),
    ).fetchone() is not None


def enqueue_call(conn: psycopg.Connection, *, kind: str, url: str, payload: dict[str, Any], key: str,
                 correlation_id: str | None, delay_s: float = 0, tag: str | None = None) -> None:
    """Outbox row for a registry call; key is stable, so the same decision
    never produces two rows and is the Idempotency-Key at the registry."""
    conn.execute(
        "INSERT INTO outbox (message_id, kind, target_url, payload, correlation_id, next_attempt_at) "
        "VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s)) ON CONFLICT (message_id) DO NOTHING",
        (str(uuid.uuid5(NAMESPACE, f"{kind}|{key}")), tag or kind, url, Jsonb(payload), correlation_id, delay_s),
    )


def connectivity_tag(gateway: str, session: str) -> str:
    return f"connectivity|{gateway}|{session}"

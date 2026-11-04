"""Wire Tap: integration events go to ``integration_events`` and to the
live console (SSE) without affecting the flow."""

import json
import queue
import threading
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from vigiles_common.logging import get_logger

log = get_logger(__name__)


class Broadcaster:
    """Fan-out of events to connected console clients."""

    def __init__(self, backlog: int = 200) -> None:
        self._subscribers: list[queue.Queue] = []
        self._recent: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._backlog = backlog

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._recent = [*self._recent[-(self._backlog - 1):], event]
            for subscriber in self._subscribers:
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    pass  # a slow console must not slow down the hub

    def subscribe(self) -> tuple[queue.Queue, list[dict[str, Any]]]:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
            return q, list(self._recent)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.remove(q)


class EventLog:
    def __init__(self, instance: str, broadcaster: Broadcaster) -> None:
        self.instance = instance
        self.broadcaster = broadcaster

    def record(self, conn: psycopg.Connection | None, stage: str, event: str, outcome: str,
               message_id: str | None = None, correlation_id: str | None = None, **details: Any) -> None:
        if conn is not None:
            conn.execute(
                "INSERT INTO integration_events (instance, stage, event, message_id, correlation_id, outcome, details) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (self.instance, stage, event, message_id, correlation_id, outcome, Jsonb(details)),
            )
        log.info(event, stage=stage, outcome=outcome, message_id=message_id, correlation_id=correlation_id, **details)
        self.broadcaster.publish({
            "ts": datetime.now(UTC).isoformat(), "instance": self.instance, "stage": stage, "event": event,
            "outcome": outcome, "message_id": message_id, "correlation_id": correlation_id,
            "details": json.loads(json.dumps(details, default=str)),
        })

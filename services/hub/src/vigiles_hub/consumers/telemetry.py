"""Telemetry channel with a Message Filter: only readings above the
threshold lead to a cleaning work order. The registry returns an already
open order instead of creating a duplicate (scenario S6)."""

import paho.mqtt.client as mqtt

from vigiles_common.correlation import correlation_scope
from vigiles_hub.consumers.base import claim, enqueue_call, parse
from vigiles_hub.consumers.rules import Thresholds, needs_cleaning


class TelemetryConsumer:
    name = "telemetry"

    def __init__(self, db, events, registry_url: str, thresholds: Thresholds, instance: str) -> None:
        self.db, self.events, self.thresholds, self.instance = db, events, thresholds, instance
        self.url = registry_url.rstrip("/") + "/api/v1/work-orders"

    def handle(self, message: mqtt.MQTTMessage) -> None:
        envelope = parse(self.db, self.events, message)
        with correlation_scope(envelope.correlation_id), self.db.transaction() as conn:
            if not claim(conn, self.name, envelope.message_id, self.instance):
                self.events.record(conn, self.name, "duplicate_dropped", "duplicate", envelope.message_id, envelope.correlation_id)
                return
            if not needs_cleaning(envelope, self.thresholds):
                return  # filtered out; recorded in processed_messages only
            enqueue_call(conn, kind="work_order", url=self.url, key=envelope.message_id,
                         correlation_id=envelope.correlation_id,
                         payload={"asset_code": envelope.asset_code, "reason": "czyszczenie",
                                  "description": f"Detector contamination {envelope.payload['contamination']:.0f}% "
                                                 f"(device {envelope.device_id})"})
            self.events.record(conn, self.name, "work_order_queued", "ok", envelope.message_id, envelope.correlation_id,
                               asset_code=envelope.asset_code, reason="czyszczenie")

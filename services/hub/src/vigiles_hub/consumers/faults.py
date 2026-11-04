"""Fault channel: a device fault becomes a service work order in the registry."""

import paho.mqtt.client as mqtt

from vigiles_common.correlation import correlation_scope
from vigiles_hub.consumers.base import claim, enqueue_call, parse
from vigiles_hub.consumers.rules import Thresholds, fault_reason

DESCRIPTIONS = {
    "DAMPER_SLOW_CLOSE": "Damper closes too slowly: {closing_time_s} s",
    "GATEWAY_OFFLINE": "Gateway {gateway} lost connection; device not supervised",
}


class FaultsConsumer:
    name = "faults"

    def __init__(self, db, events, registry_url: str, thresholds: Thresholds, instance: str) -> None:
        self.db, self.events, self.thresholds, self.instance = db, events, thresholds, instance
        self.url = registry_url.rstrip("/") + "/api/v1/work-orders"

    def handle(self, message: mqtt.MQTTMessage) -> None:
        envelope = parse(self.db, self.events, message)
        with correlation_scope(envelope.correlation_id), self.db.transaction() as conn:
            if not claim(conn, self.name, envelope.message_id, self.instance):
                self.events.record(conn, self.name, "duplicate_dropped", "duplicate", envelope.message_id, envelope.correlation_id)
                return
            reason = fault_reason(envelope, self.thresholds)
            if reason is None:
                self.events.record(conn, self.name, "fault_below_threshold", "ignored", envelope.message_id,
                                   envelope.correlation_id, payload=envelope.payload)
                return
            code = envelope.payload.get("code", "")
            description = DESCRIPTIONS.get(code, "Device fault {code}").format(**{"code": code, **envelope.payload})
            enqueue_call(conn, kind="work_order", url=self.url, key=envelope.message_id,
                         correlation_id=envelope.correlation_id,
                         payload={"asset_code": envelope.asset_code, "reason": reason,
                                  "description": f"{description} (device {envelope.device_id})"})
            self.events.record(conn, self.name, "work_order_queued", "ok", envelope.message_id, envelope.correlation_id,
                               asset_code=envelope.asset_code, reason=reason)

"""Ingress: raw vendor message -> canonical message on an event channel.

Normalizer -> Content Enricher -> Content-Based Router -> publish. The
ingress keeps no state of its own: it publishes the canonical message,
waits for PUBACK and only then the raw message is acknowledged. After a
crash in between the broker redelivers the raw message and the
deterministic message_id lets consumers drop the duplicate.
"""

import json
from datetime import UTC, datetime

import paho.mqtt.client as mqtt

from vigiles_common.correlation import correlation_scope
from vigiles_common.envelope import Envelope, message_id_for
from vigiles_common.mqtt import PermanentError, Publisher
from vigiles_hub.db import HubDatabase
from vigiles_hub.dlq import dead_letter
from vigiles_hub.events import EventLog
from vigiles_hub.ingress.enricher import DeviceCatalog
from vigiles_hub.ingress.router import NoRoute, route
from vigiles_hub.ingress.translators import InvalidMessage, translate


class DeadLettered(Exception):
    pass


class Ingress:
    def __init__(self, catalog: DeviceCatalog, publisher: Publisher, db: HubDatabase, events: EventLog) -> None:
        self.catalog = catalog
        self.publisher = publisher
        self.db = db
        self.events = events

    def handle(self, message: mqtt.MQTTMessage) -> None:
        try:
            self.process(message.topic, message.payload)
        except DeadLettered as exc:
            raise PermanentError(str(exc)) from exc

    def process(self, topic: str, raw: bytes) -> list[Envelope]:
        parts = topic.split("/")
        if len(parts) == 3 and parts[:2] == ["vigiles", "status"]:
            return self._status(parts[2], raw)
        if len(parts) != 5 or parts[:2] != ["vigiles", "raw"]:
            self._dead(topic, raw, "invalid_topic", f"unexpected topic {topic}")
        vendor, _gateway, device_id = parts[2:]
        try:
            event = translate(vendor, device_id, raw)
        except InvalidMessage as exc:
            self._dead(topic, raw, exc.reason, str(exc))
        with correlation_scope(event.message_id):
            device = self.catalog.get(device_id)
            if device is None:
                self._dead(topic, raw, "unknown_device", f"device {device_id} is not in the registry catalogue",
                           event.message_id)
            zone = device.get("zone") or event.zone
            if not device.get("site") or not zone:
                self._dead(topic, raw, "unknown_device", f"device {device_id} has no site or zone", event.message_id)
            envelope = Envelope(
                message_id=event.message_id, correlation_id=event.message_id, type=event.type,
                site=device["site"], zone=zone, device_id=device_id, asset_code=device.get("asset_code"),
                vendor=event.vendor, occurred_at=event.occurred_at, payload=event.payload,
            )
            return [self._publish(topic, raw, envelope)]

    def _publish(self, source: str, raw: bytes, envelope: Envelope) -> Envelope:
        try:
            target = route(envelope)
        except NoRoute as exc:
            self._dead(source, raw, "unknown_type", str(exc), envelope.message_id)
        self.publisher.publish(target, envelope.to_json())
        self.events.record(None, "ingress", "message_routed", "ok", envelope.message_id, envelope.correlation_id,
                           source=source, target=target, type=envelope.type, device_id=envelope.device_id)
        return envelope

    def _status(self, gateway_id: str, raw: bytes) -> list[Envelope]:
        try:
            status = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._dead(f"vigiles/status/{gateway_id}", raw, "invalid_payload", "status must be JSON")
        if status.get("status") != "offline":
            cancelled = 0
            if status.get("status") == "online":
                from vigiles_hub.consumers.base import connectivity_tag

                with self.db.transaction() as conn:
                    cancelled = conn.execute(
                        "DELETE FROM outbox WHERE kind = %s AND status = 'pending' AND attempts = 0",
                        (connectivity_tag(gateway_id, status.get("session", "")),),
                    ).rowcount
            self.events.record(None, "ingress", "gateway_status", "ok", gateway=gateway_id, status=status.get("status"),
                               cancelled_connectivity_orders=cancelled)
            return []
        # Last Will: the gateway lost its connection. Each of its devices is
        # now unsupervised; the deterministic id per gateway session keeps a
        # retained or redelivered will from creating duplicates.
        session = status.get("session", "")
        results = []
        for device in self.catalog.devices_of_gateway(gateway_id):
            message_id = message_id_for("gateway", device["device_id"], f"offline:{gateway_id}:{session}")
            with correlation_scope(message_id):
                envelope = Envelope(
                    message_id=message_id, correlation_id=message_id, type="fault", site=device["site"],
                    zone=device["zone"], device_id=device["device_id"], asset_code=device.get("asset_code"),
                    vendor=device["vendor"], occurred_at=datetime.now(UTC),
                    payload={"code": "GATEWAY_OFFLINE", "gateway": gateway_id, "session": session},
                )
                results.append(self._publish(f"vigiles/status/{gateway_id}", raw, envelope))
        return results

    def _dead(self, topic: str, raw: bytes, reason: str, error: str, message_id: str | None = None):
        with self.db.transaction() as conn:
            row = dead_letter(conn, source_topic=topic, reason=reason, raw=raw, error=error, message_id=message_id)
            self.events.record(conn, "ingress", "dead_lettered", reason, message_id, message_id,
                               source=topic, error=error, dead_letter_id=row["id"])
        try:
            self.publisher.publish(f"vigiles/dlq/{reason}", json.dumps(
                {"dead_letter_id": row["id"], "source_topic": topic, "error": error}).encode(), timeout_s=2)
        except (ConnectionError, TimeoutError, RuntimeError):
            pass  # the table is the source of truth; the topic is a preview
        raise DeadLettered(error)

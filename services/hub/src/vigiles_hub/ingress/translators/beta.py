"""Vendor Beta (fictional): nested JSON, ISO 8601 with offset, decimal
comma, no building zone and no message id.

    {"serial": "BETA-7F3K2", "time": "2026-11-04T11:15:00+01:00",
     "event": {"type": "fault", "code": "DAMPER_SLOW_CLOSE", "measured": "95,2", "unit": "s"}}
"""

from datetime import UTC, datetime

from vigiles_common.envelope import message_id_for
from vigiles_hub.ingress.translators import InvalidMessage, Translated, parse_json

_TYPES = {"fault": "fault", "alarm": "alarm", "telemetry": "telemetry"}
# How a measured value is named in the canonical payload, per event code.
_MEASURES = {"DAMPER_SLOW_CLOSE": "closing_time_s"}


def _number(value: object) -> float:
    try:
        return float(str(value).replace(",", "."))
    except ValueError as exc:
        raise InvalidMessage("invalid_payload", f"measured value {value!r} is not a number") from exc


def translate(device_id: str, raw: bytes) -> Translated:
    doc = parse_json(raw)
    event = doc.get("event")
    if not isinstance(event, dict) or "serial" not in doc or "time" not in doc:
        raise InvalidMessage("invalid_payload", "Beta message needs serial, time and event")
    if doc["serial"] != device_id:
        raise InvalidMessage("invalid_payload", f"device {doc['serial']!r} does not match topic device {device_id!r}")
    type_ = _TYPES.get(str(event.get("type", "")).lower())
    if type_ is None:
        raise InvalidMessage("unknown_type", f"Beta event type {event.get('type')!r} is not supported")
    try:
        occurred_at = datetime.fromisoformat(doc["time"])
    except (TypeError, ValueError) as exc:
        raise InvalidMessage("invalid_payload", f"time {doc['time']!r} is not ISO 8601") from exc
    if occurred_at.tzinfo is None:
        raise InvalidMessage("invalid_payload", "time must include a UTC offset")
    occurred_at = occurred_at.astimezone(UTC)
    code = str(event.get("code", "UNKNOWN")).upper()
    payload: dict = {"code": code}
    if "measured" in event:
        payload[_MEASURES.get(code, "value")] = _number(event["measured"])
        if event.get("unit") and code not in _MEASURES:
            payload["unit"] = event["unit"]
    return Translated(
        vendor="beta", device_id=doc["serial"], type=type_, occurred_at=occurred_at,
        message_id=message_id_for("beta", doc["serial"], occurred_at=occurred_at, type_=type_, code=code),
        payload=payload,
    )

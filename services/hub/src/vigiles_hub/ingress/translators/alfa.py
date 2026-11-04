"""Vendor Alfa (fictional): flat JSON, epoch milliseconds, upper-case codes,
zone included, message id included.

    {"id": 88121, "dev": "A-000123", "evt": "ALARM", "ts": 1793787300123, "zone": "Z3"}
    {"id": 88140, "dev": "A-000124", "evt": "TLM", "ts": 1793787360000, "contamination": 85}
"""

from datetime import UTC, datetime

from vigiles_common.envelope import message_id_for
from vigiles_hub.ingress.translators import InvalidMessage, Translated, parse_json

_TYPES = {"ALARM": "alarm", "TLM": "telemetry", "FAULT": "fault"}


def translate(device_id: str, raw: bytes) -> Translated:
    doc = parse_json(raw)
    try:
        vendor_id, dev, evt, ts = doc["id"], doc["dev"], doc["evt"], doc["ts"]
    except KeyError as exc:
        raise InvalidMessage("invalid_payload", f"Alfa message without {exc.args[0]!r}") from exc
    if dev != device_id:
        raise InvalidMessage("invalid_payload", f"device {dev!r} does not match topic device {device_id!r}")
    type_ = _TYPES.get(str(evt).upper())
    if type_ is None:
        raise InvalidMessage("unknown_type", f"Alfa event type {evt!r} is not supported")
    if not isinstance(ts, int | float):
        raise InvalidMessage("invalid_payload", "ts must be epoch milliseconds")
    occurred_at = datetime.fromtimestamp(ts / 1000, UTC)
    payload: dict = {}
    if type_ == "alarm":
        payload = {"code": "SMOKE"}
    elif type_ == "telemetry":
        if not isinstance(doc.get("contamination"), int | float):
            raise InvalidMessage("invalid_payload", "Alfa telemetry without numeric contamination")
        payload = {"contamination": float(doc["contamination"])}
    else:
        payload = {"code": str(doc.get("code", "UNKNOWN")).upper()}
    return Translated(
        vendor="alfa", device_id=dev, type=type_, occurred_at=occurred_at,
        message_id=message_id_for("alfa", dev, vendor_id), zone=doc.get("zone"), payload=payload,
    )

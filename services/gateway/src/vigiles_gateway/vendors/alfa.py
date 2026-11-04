"""Alfa: flat JSON, epoch milliseconds, upper-case codes, zone and id included."""

import json
from datetime import datetime


def message(vendor_id: int, device_id: str, evt: str, at: datetime, zone: str | None, **extra) -> bytes:
    body = {"id": vendor_id, "dev": device_id, "evt": evt, "ts": int(at.timestamp() * 1000)}
    if zone:
        body["zone"] = zone
    body.update(extra)
    return json.dumps(body).encode()

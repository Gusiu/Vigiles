"""Beta: nested JSON, local time with offset, decimal comma, no zone, no id."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/Warsaw")


def message(device_id: str, event_type: str, code: str, at: datetime, measured: float | None = None,
            unit: str | None = None) -> bytes:
    event: dict = {"type": event_type, "code": code}
    if measured is not None:
        event["measured"] = f"{measured}".replace(".", ",")
    if unit:
        event["unit"] = unit
    return json.dumps({"serial": device_id, "time": at.astimezone(LOCAL).replace(microsecond=0).isoformat(),
                       "event": event}).encode()

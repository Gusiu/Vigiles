"""Canonical Data Model: one envelope for every consumer of device events.

``message_id`` is deterministic (UUIDv5), so a message delivered again
has the same id and consumers can drop the duplicate:

* from (vendor, device_id, vendor message id) when the vendor sends one,
* otherwise from (vendor, device_id, occurred_at, type, code). Two
  identical events of one device within the same second collide; that is
  accepted and documented.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

NAMESPACE = uuid.UUID("0b8f2b8e-3c55-5d0e-9a5b-7a1c3d9e6f21")
EventType = Literal["alarm", "fault", "telemetry"]


def message_id_for(vendor: str, device_id: str, vendor_message_id: str | int | None = None,
                   occurred_at: datetime | None = None, type_: str | None = None, code: str | None = None) -> str:
    if vendor_message_id is not None:
        name = f"{vendor}|{device_id}|id|{vendor_message_id}"
    else:
        stamp = occurred_at.astimezone(UTC).replace(microsecond=0).isoformat() if occurred_at else ""
        name = f"{vendor}|{device_id}|{stamp}|{type_}|{code}"
    return str(uuid.uuid5(NAMESPACE, name))


class Envelope(BaseModel):
    schema_version: int = 1
    message_id: str
    correlation_id: str
    type: EventType
    site: str
    zone: str
    device_id: str
    asset_code: str | None = None
    vendor: str
    occurred_at: datetime
    normalized_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> bytes:
        return self.model_dump_json().encode()

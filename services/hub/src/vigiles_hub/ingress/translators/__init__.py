"""Normalizer: one Message Translator per vendor format.

A translator turns the raw payload of one vendor into a ``Translated``
event without site information; the Content Enricher adds that.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class InvalidMessage(ValueError):
    """The raw message cannot be understood; it goes to the dead letter channel."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True)
class Translated:
    vendor: str
    device_id: str
    type: str
    occurred_at: datetime
    message_id: str
    zone: str | None = None  # Alfa sends the zone, Beta does not
    payload: dict[str, Any] = field(default_factory=dict)


def translate(vendor: str, device_id: str, raw: bytes) -> Translated:
    from vigiles_hub.ingress.translators import alfa, beta

    translators = {"alfa": alfa.translate, "beta": beta.translate}
    if vendor not in translators:
        raise InvalidMessage("unknown_vendor", f"no translator for vendor {vendor!r}")
    return translators[vendor](device_id, raw)


def parse_json(raw: bytes) -> dict[str, Any]:
    import json

    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidMessage("invalid_payload", f"not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise InvalidMessage("invalid_payload", "payload must be a JSON object")
    return document

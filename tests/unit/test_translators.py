from datetime import UTC, datetime

import pytest

from vigiles_hub.ingress.translators import InvalidMessage, translate


def test_alfa_alarm() -> None:
    t = translate("alfa", "A-000123", b'{"id": 88121, "dev": "A-000123", "evt": "ALARM", "ts": 1793787300123, "zone": "Z3"}')

    assert (t.type, t.zone, t.payload) == ("alarm", "Z3", {"code": "SMOKE"})
    assert t.occurred_at == datetime(2026, 11, 4, 10, 15, 0, 123000, tzinfo=UTC)
    assert t.message_id == translate("alfa", "A-000123", b'{"id": 88121, "dev": "A-000123", "evt": "ALARM", "ts": 1, "zone": "Z3"}').message_id


def test_alfa_telemetry() -> None:
    t = translate("alfa", "A-000124", b'{"id": 88140, "dev": "A-000124", "evt": "TLM", "ts": 1793787360000, "contamination": 85}')
    assert (t.type, t.payload) == ("telemetry", {"contamination": 85.0})


def test_beta_fault_with_offset_and_decimal_comma() -> None:
    raw = ('{"serial": "BETA-7F3K2", "time": "2026-11-04T11:15:00+01:00", '
           '"event": {"type": "fault", "code": "DAMPER_SLOW_CLOSE", "measured": "95,2", "unit": "s"}}').encode()
    t = translate("beta", "BETA-7F3K2", raw)

    assert t.occurred_at == datetime(2026, 11, 4, 10, 15, tzinfo=UTC)
    assert t.payload == {"code": "DAMPER_SLOW_CLOSE", "closing_time_s": 95.2}
    assert t.zone is None
    assert t.message_id == translate("beta", "BETA-7F3K2", raw).message_id


@pytest.mark.parametrize(
    ("vendor", "device", "raw", "reason"),
    [
        ("alfa", "A-1", b"not json", "invalid_payload"),
        ("alfa", "A-1", b"[1]", "invalid_payload"),
        ("alfa", "A-1", b'{"id": 1, "dev": "A-1", "evt": "ALARM"}', "invalid_payload"),
        ("alfa", "A-1", b'{"id": 1, "dev": "A-2", "evt": "ALARM", "ts": 1}', "invalid_payload"),
        ("alfa", "A-1", b'{"id": 1, "dev": "A-1", "evt": "DANCE", "ts": 1}', "unknown_type"),
        ("alfa", "A-1", b'{"id": 1, "dev": "A-1", "evt": "TLM", "ts": 1}', "invalid_payload"),
        ("beta", "B-1", b'{"serial": "B-1", "time": "2026-11-04T11:15:00", "event": {"type": "fault"}}', "invalid_payload"),
        ("beta", "B-1", b'{"serial": "B-1", "time": "yesterday", "event": {"type": "fault"}}', "invalid_payload"),
        ("beta", "B-1", b'{"serial": "B-1", "time": "2026-11-04T11:15:00Z", "event": {"type": "fault", "measured": "x"}}', "invalid_payload"),
        ("gamma", "G-1", b"{}", "unknown_vendor"),
    ],
)
def test_invalid_messages(vendor: str, device: str, raw: bytes, reason: str) -> None:
    with pytest.raises(InvalidMessage) as info:
        translate(vendor, device, raw)
    assert info.value.reason == reason

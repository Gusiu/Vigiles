from datetime import datetime, timedelta, timezone

from vigiles_common.envelope import Envelope, message_id_for


def test_message_id_is_deterministic() -> None:
    assert message_id_for("alfa", "A-1", 88121) == message_id_for("alfa", "A-1", 88121)
    assert message_id_for("alfa", "A-1", 88121) != message_id_for("alfa", "A-1", 88122)
    assert message_id_for("alfa", "A-1", 88121) != message_id_for("alfa", "A-2", 88121)


def test_message_id_without_vendor_id_uses_time_type_and_code() -> None:
    t = datetime(2026, 11, 4, 11, 15, 0, 300000, tzinfo=timezone(timedelta(hours=1)))
    same_instant_utc = datetime(2026, 11, 4, 10, 15, 0, tzinfo=timezone.utc)

    a = message_id_for("beta", "BETA-1", occurred_at=t, type_="fault", code="DAMPER_SLOW_CLOSE")
    assert a == message_id_for("beta", "BETA-1", occurred_at=same_instant_utc, type_="fault", code="DAMPER_SLOW_CLOSE")
    assert a != message_id_for("beta", "BETA-1", occurred_at=t, type_="fault", code="OTHER")


def test_envelope_round_trip() -> None:
    env = Envelope(message_id="m", correlation_id="m", type="fault", site="B1", zone="Z3", device_id="BETA-7F3K2",
                   asset_code="KL-B1-3-002", vendor="beta", occurred_at=datetime(2026, 11, 4, 10, 15, tzinfo=timezone.utc),
                   payload={"code": "DAMPER_SLOW_CLOSE", "closing_time_s": 95.2})
    assert Envelope.model_validate_json(env.to_json()) == env

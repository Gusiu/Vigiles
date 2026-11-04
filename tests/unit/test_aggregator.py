from datetime import UTC, datetime, timedelta

from vigiles_common.envelope import Envelope
from vigiles_hub.consumers.rules import (
    Thresholds,
    WindowView,
    add_detector,
    expired,
    fault_reason,
    matches_window,
    needs_cleaning,
)

T0 = datetime(2026, 11, 4, 10, 15, tzinfo=UTC)
W = timedelta(seconds=60)


def test_event_matches_window_by_event_time_in_both_directions() -> None:
    window = WindowView(T0, ("A-1",), "open")

    assert matches_window(window, T0 + timedelta(seconds=60), W)
    assert matches_window(window, T0 - timedelta(seconds=30), W)  # arrived late, happened earlier
    assert not matches_window(window, T0 + timedelta(seconds=61), W)


def test_two_different_detectors_confirm() -> None:
    window = WindowView(T0, ("A-1",), "open")

    assert add_detector(window, "A-1") == (("A-1",), False)
    assert add_detector(window, "A-2") == (("A-1", "A-2"), True)


def test_window_expires_after_lateness() -> None:
    closes_at = T0 + W
    lateness = timedelta(seconds=10)

    assert not expired(closes_at, closes_at + timedelta(seconds=10), lateness)
    assert expired(closes_at, closes_at + timedelta(seconds=11), lateness)


def _env(type_: str, **payload) -> Envelope:
    return Envelope.model_construct(message_id="m", correlation_id="m", type=type_, site="B1", zone="Z3",
                                    device_id="d", vendor="beta", occurred_at=T0, payload=payload)


def test_damper_threshold() -> None:
    thresholds = Thresholds()
    assert fault_reason(_env("fault", code="DAMPER_SLOW_CLOSE", closing_time_s=95.2), thresholds) == "usterka_urzadzenia"
    assert fault_reason(_env("fault", code="DAMPER_SLOW_CLOSE", closing_time_s=45), thresholds) is None
    assert fault_reason(_env("fault", code="GATEWAY_OFFLINE"), thresholds) == "brak_lacznosci"


def test_contamination_filter() -> None:
    thresholds = Thresholds()
    assert needs_cleaning(_env("telemetry", contamination=80), thresholds)
    assert not needs_cleaning(_env("telemetry", contamination=79.9), thresholds)

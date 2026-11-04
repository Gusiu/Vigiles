"""Decisions of the consumers, free of I/O so they can be unit-tested.

Thresholds are demonstration values, not values from a standard.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from vigiles_common.envelope import Envelope


@dataclass(frozen=True)
class Thresholds:
    damper_closing_time_s: float = 60.0
    contamination_pct: float = 80.0


def fault_reason(envelope: Envelope, thresholds: Thresholds) -> str | None:
    """Work order reason for a fault event, or None when no order is needed."""
    code = envelope.payload.get("code")
    if code == "GATEWAY_OFFLINE":
        return "brak_lacznosci"
    if code == "DAMPER_SLOW_CLOSE":
        closing = envelope.payload.get("closing_time_s")
        return "usterka_urzadzenia" if closing is not None and closing > thresholds.damper_closing_time_s else None
    return "usterka_urzadzenia"


def needs_cleaning(envelope: Envelope, thresholds: Thresholds) -> bool:
    """Message Filter: only readings at or above the threshold pass."""
    value = envelope.payload.get("contamination")
    return value is not None and value >= thresholds.contamination_pct


@dataclass(frozen=True)
class WindowView:
    opened_at: datetime
    detectors: tuple[str, ...]
    status: str


def matches_window(window: WindowView, occurred_at: datetime, width: timedelta) -> bool:
    """An event belongs to a window when |occurred_at - opened_at| <= W
    (event time, not arrival time: $share does not keep global order)."""
    return abs(occurred_at - window.opened_at) <= width


def add_detector(window: WindowView, device_id: str) -> tuple[tuple[str, ...], bool]:
    """Return the new detector list and whether the window is now confirmed
    (at least two different detectors)."""
    detectors = window.detectors if device_id in window.detectors else (*window.detectors, device_id)
    return detectors, len(set(detectors)) >= 2


def expired(closes_at: datetime, now: datetime, lateness: timedelta) -> bool:
    return now > closes_at + lateness

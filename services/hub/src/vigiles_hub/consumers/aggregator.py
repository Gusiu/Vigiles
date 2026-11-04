"""Aggregator: coincidence of alarms in one zone (scenario S5).

* correlation key (site, zone), completeness: >= 2 different detectors,
  timeout: window W after the first alarm (event time),
* one open window per zone (partial unique index); a concurrent insert by
  the other replica raises unique_violation and the transaction is retried,
* confirmed -> incident ``alarm_potwierdzony``; a window that ends with
  one detector -> ``alarm_wstepny`` from the sweeper,
* the incident Idempotency-Key is window id + outcome.

Modelled on the dependency on more than one alarm signal in EN 54-2
(7.12) for demonstration only; alarms in a real building are handled by a
certified fire alarm control panel.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import paho.mqtt.client as mqtt
import psycopg

from vigiles_common.correlation import correlation_scope
from vigiles_common.logging import get_logger
from vigiles_hub.consumers.base import claim, enqueue_call, parse
from vigiles_hub.consumers.rules import WindowView, add_detector, matches_window

log = get_logger(__name__)


class AggregatorConsumer:
    name = "aggregator"

    def __init__(self, db, events, registry_url: str, instance: str, window_s: float = 60.0,
                 lateness_s: float = 10.0, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.db, self.events, self.instance = db, events, instance
        self.width = timedelta(seconds=window_s)
        self.lateness = timedelta(seconds=lateness_s)
        self.clock = clock
        self.url = registry_url.rstrip("/") + "/api/v1/incidents"

    def handle(self, message: mqtt.MQTTMessage) -> None:
        envelope = parse(self.db, self.events, message)
        for attempt in range(5):
            try:
                with correlation_scope(envelope.correlation_id):
                    self._apply(envelope)
                return
            except psycopg.errors.UniqueViolation:
                # The other replica opened the window for this zone first.
                log.info("aggregator_window_race_retry", attempt=attempt, zone=envelope.zone)
        raise RuntimeError("could not settle the alarm window after 5 attempts")

    def _incident(self, conn, window: dict, kind: str, decided_at: datetime) -> None:
        outcome = "confirmed" if kind == "alarm_potwierdzony" else "expired"
        enqueue_call(conn, kind="incident", url=self.url, key=f"{window['id']}:{outcome}",
                     correlation_id=window.get("correlation_id"),
                     payload={"kind": kind, "site": window["site"], "zone": window["zone"],
                              "detectors": list(window["detectors"]), "first_at": window["opened_at"].isoformat(),
                              "decided_at": decided_at.isoformat()})

    def _apply(self, envelope) -> None:
        with self.db.transaction() as conn:
            if not claim(conn, self.name, envelope.message_id, self.instance):
                self.events.record(conn, self.name, "duplicate_dropped", "duplicate", envelope.message_id, envelope.correlation_id)
                return
            at = envelope.occurred_at
            window = conn.execute(
                """
                SELECT * FROM alarm_windows
                WHERE site = %s AND zone = %s AND status IN ('open', 'confirmed')
                  AND opened_at BETWEEN %s AND %s
                ORDER BY status = 'open' DESC, opened_at DESC LIMIT 1 FOR UPDATE
                """,
                (envelope.site, envelope.zone, at - self.width, at + self.width),
            ).fetchone()
            if window and matches_window(WindowView(window["opened_at"], tuple(window["detectors"]), window["status"]), at, self.width):
                detectors, confirmed = add_detector(
                    WindowView(window["opened_at"], tuple(window["detectors"]), window["status"]), envelope.device_id)
                if window["status"] == "open" and confirmed:
                    now = self.clock()
                    window = conn.execute(
                        "UPDATE alarm_windows SET detectors = %s, status = 'confirmed', decided_at = %s WHERE id = %s RETURNING *",
                        (list(detectors), now, window["id"]),
                    ).fetchone()
                    self._incident(conn, window, "alarm_potwierdzony", now)
                    self.events.record(conn, self.name, "alarm_confirmed", "ok", envelope.message_id, window["correlation_id"],
                                       zone=envelope.zone, detectors=list(detectors), window_id=str(window["id"]))
                else:
                    conn.execute("UPDATE alarm_windows SET detectors = %s WHERE id = %s", (list(detectors), window["id"]))
                    self.events.record(conn, self.name, "alarm_joined_window", "ok", envelope.message_id,
                                       window["correlation_id"], zone=envelope.zone, window_status=window["status"])
                return
            # Close an open window of this zone that this event is already past.
            stale = conn.execute(
                "SELECT * FROM alarm_windows WHERE site = %s AND zone = %s AND status = 'open' FOR UPDATE",
                (envelope.site, envelope.zone),
            ).fetchone()
            if stale and at > stale["closes_at"]:
                self._expire(conn, stale)
            elif stale:
                # Much older than the open window (very late delivery): logged, not aggregated.
                self.events.record(conn, self.name, "alarm_too_late", "ignored", envelope.message_id, envelope.correlation_id)
                return
            window = conn.execute(
                """
                INSERT INTO alarm_windows (site, zone, opened_at, closes_at, detectors, status, correlation_id)
                VALUES (%s, %s, %s, %s, %s, 'open', %s) RETURNING *
                """,
                (envelope.site, envelope.zone, at, at + self.width, [envelope.device_id], envelope.correlation_id),
            ).fetchone()
            self.events.record(conn, self.name, "alarm_window_opened", "ok", envelope.message_id, envelope.correlation_id,
                               zone=envelope.zone, window_id=str(window["id"]))

    def _expire(self, conn, window: dict) -> None:
        now = self.clock()
        conn.execute("UPDATE alarm_windows SET status = 'expired', decided_at = %s WHERE id = %s", (now, window["id"]))
        self._incident(conn, window, "alarm_wstepny", now)
        self.events.record(conn, self.name, "alarm_preliminary", "ok", None, window["correlation_id"],
                           zone=window["zone"], detectors=list(window["detectors"]), window_id=str(window["id"]))

    def sweep(self) -> int:
        """Expire windows past closes_at + lateness. Each replica runs it;
        SKIP LOCKED makes them share the work without blocking."""
        now = self.clock()
        with self.db.transaction() as conn:
            windows = conn.execute(
                "SELECT * FROM alarm_windows WHERE status = 'open' AND closes_at + %s < %s FOR UPDATE SKIP LOCKED",
                (self.lateness, now),
            ).fetchall()
            for window in windows:
                with correlation_scope(window["correlation_id"]):
                    self._expire(conn, window)
        return len(windows)

    def run_sweeper(self, stop: threading.Event, interval_s: float) -> None:
        while not stop.is_set():
            try:
                self.sweep()
            except Exception:
                log.exception("sweeper_failed")
            stop.wait(interval_s)

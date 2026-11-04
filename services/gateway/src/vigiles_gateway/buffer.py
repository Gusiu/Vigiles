"""Store-and-forward: every message goes to SQLite first and is deleted
only after the broker acknowledged it (PUBACK). While the broker is
unreachable the buffer grows; after reconnecting the backlog is sent in
order. A message may be sent twice around a disconnect - consumers drop
the duplicate by its deterministic message_id."""

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import paho.mqtt.client as mqtt

from vigiles_common.logging import get_logger
from vigiles_common.mqtt import MqttSettings, make_client

log = get_logger(__name__)


class Buffer:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, payload BLOB NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            yield conn
        finally:
            conn.close()

    def enqueue(self, topic: str, payload: bytes) -> int:
        with self._conn() as conn:
            return conn.execute("INSERT INTO messages (topic, payload) VALUES (?, ?)", (topic, payload)).lastrowid

    def batch(self, limit: int, exclude: set[int]) -> list[tuple[int, str, bytes]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id, topic, payload FROM messages ORDER BY id LIMIT ?", (limit + len(exclude),)).fetchall()
        return [r for r in rows if r[0] not in exclude][:limit]

    def delete(self, message_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    def size(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT count(*) FROM messages").fetchone()[0]

    def next_id(self, name: str, start: int = 88000) -> int:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
            value = (row[0] if row else start) + 1
            conn.execute("INSERT INTO counters (name, value) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET value = excluded.value", (name, value))
            conn.execute("COMMIT")
            return value


class Forwarder:
    """Publishes the buffer to the broker with QoS 1. Reconnects with
    backoff 1-30 s. The Last Will marks the gateway offline when the
    connection breaks without a clean disconnect (crash, kill, network)."""

    def __init__(self, settings: MqttSettings, gateway_id: str, buffer: Buffer, session: str,
                 max_in_flight: int = 20, ack_timeout_s: float = 10.0, reconnect_hold_s: float = 3.0) -> None:
        import json

        self.buffer = buffer
        self.gateway_id = gateway_id
        self.status_topic = f"vigiles/status/{gateway_id}"
        self.online = json.dumps({"status": "online", "session": session}).encode()
        offline = json.dumps({"status": "offline", "session": session}).encode()
        self.client, self.props = make_client(settings, gateway_id, durable=False, will=(self.status_topic, offline))
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.settings = settings
        self.max_in_flight = max_in_flight
        self.ack_timeout_s = ack_timeout_s
        self.reconnect_hold_s = reconnect_hold_s
        self._send_after = 0.0
        self._sent_at: dict[int, float] = {}  # paho mid -> monotonic time of publish
        self.connected = threading.Event()
        self.wake = threading.Event()
        self._in_flight: dict[int, int] = {}  # paho mid -> buffer id
        self._early_acks: set[int] = set()
        self._announce = False  # PUBACK seen before the mid was recorded
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="forwarder", daemon=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("gateway_connect_failed", reason=str(reason_code))
            return
        # Publishing from a paho callback while another thread publishes can
        # deadlock inside paho; the forwarder thread announces "online".
        self._announce = True
        # Mosquitto 2.0.18 restores sessions but not $share subscriptions
        # after a restart: until the hub consumers subscribe again, messages
        # for them are dropped. Hold the backlog briefly after (re)connecting
        # so they can. See docs/resilience.md.
        self._send_after = time.monotonic() + self.reconnect_hold_s
        self.connected.set()
        self.wake.set()
        log.info("gateway_connected", backlog=self.buffer.size())

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected.clear()
        with self._lock:
            self._in_flight.clear()  # not acknowledged: will be sent again
            self._sent_at.clear()
            self._early_acks.clear()
        log.warning("gateway_disconnected", reason=str(reason_code), backlog=self.buffer.size())

    def _on_publish(self, client, userdata, mid, reason_code, properties) -> None:
        with self._lock:
            buffer_id = self._in_flight.pop(mid, None)
            self._sent_at.pop(mid, None)
            if buffer_id is None:
                self._early_acks.add(mid)
        if buffer_id is not None:
            self.buffer.delete(buffer_id)
            self.wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.connected.is_set():
                self.connected.wait(1)
                continue
            if time.monotonic() < self._send_after:
                self.wake.wait(0.2)
                continue
            if self._announce:
                self._announce = False
                self.client.publish(self.status_topic, self.online, qos=1, retain=True)
            with self._lock:
                # Watchdog: a message without PUBACK for too long is sent
                # again (consumers drop the duplicate by message_id). This
                # guarantees progress whatever stalled the first attempt.
                now = time.monotonic()
                for mid in [m for m, at in self._sent_at.items() if now - at > self.ack_timeout_s]:
                    self._sent_at.pop(mid)
                    stale = self._in_flight.pop(mid, None)
                    log.warning("gateway_ack_timeout_resend", buffer_id=stale)
                free = self.max_in_flight - len(self._in_flight)
                sending = set(self._in_flight.values())
            if free > 0:
                for buffer_id, topic, payload in self.buffer.batch(free, sending):
                    # Never hold our lock while calling paho: its network
                    # thread takes paho's lock and then ours in on_publish.
                    info = self.client.publish(topic, payload, qos=1)
                    if info.rc != mqtt.MQTT_ERR_SUCCESS:
                        log.warning("gateway_publish_failed", rc=info.rc)
                        break
                    with self._lock:
                        acked = info.mid in self._early_acks
                        self._early_acks.discard(info.mid)
                        if not acked:
                            self._in_flight[info.mid] = buffer_id
                            self._sent_at[info.mid] = time.monotonic()
                    if acked:
                        self.buffer.delete(buffer_id)
            self.wake.wait(0.5)
            self.wake.clear()

    def start(self) -> None:
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=5, clean_start=True,
                                  properties=self.props)
        self.client.loop_start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()
        self._thread.join(timeout=5)
        # A clean disconnect does not trigger the Last Will (PLAN.md pitfall 9).
        self.client.disconnect()
        self.client.loop_stop()

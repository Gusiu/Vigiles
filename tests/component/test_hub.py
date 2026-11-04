"""Hub with a real Mosquitto and PostgreSQL; the registry is a stub."""

import json
import threading
from datetime import UTC, datetime, timedelta

import paho.mqtt.client as mqtt
import psycopg
import pytest
from fastapi.testclient import TestClient
from paho.mqtt.enums import CallbackAPIVersion
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from harness.broker import PASSWORDS
from harness.devices import CATALOG, alfa, beta_damper
from harness.eventually import eventually
from harness.registry import SERVICE_TOKENS


class RegistryStub:
    def __init__(self, server: HTTPServer) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self.devices = list(CATALOG)
        self._lock = threading.Lock()
        server.expect_request("/api/v1/devices").respond_with_handler(lambda r: Response(json.dumps(self.devices), content_type="application/json"))
        server.expect_request("/api/v1/work-orders", method="POST").respond_with_handler(self._record)
        server.expect_request("/api/v1/incidents", method="POST").respond_with_handler(self._record)

    def _record(self, request: Request) -> Response:
        with self._lock:
            self.calls.append((request.path, request.json, dict(request.headers)))
        return Response(json.dumps({"id": "x"}), status=201, content_type="application/json")

    def of(self, path: str) -> list[dict]:
        with self._lock:
            return [body for p, body, _ in self.calls if p == path]


@pytest.fixture
def registry_stub(httpserver: HTTPServer) -> RegistryStub:
    return RegistryStub(httpserver)


@pytest.fixture
def hub(fresh_broker, hub_db, registry_stub, httpserver):
    from vigiles_hub.app import create_app
    from vigiles_hub.settings import HubSettings

    settings = HubSettings(
        hub_instance="hub-t", hub_database_url=hub_db, registry_url=httpserver.url_for(""), auth_mode="static",
        service_tokens=SERVICE_TOKENS, session_secret="test", mqtt_host=fresh_broker.host,
        mqtt_port=fresh_broker.port, mqtt_hub_password=PASSWORDS["hub"], alarm_window_s=2, alarm_lateness_s=0.5,
        sweeper_interval_s=0.2, outbox_interval_s=0.1, catalog_min_refresh_s=0, gateway_offline_grace_s=0.5,
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def gateway(fresh_broker):
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="test-gw-01", protocol=mqtt.MQTTv5)
    client.username_pw_set("gw-01", PASSWORDS["gw-01"])
    client.connect(fresh_broker.host, fresh_broker.port)
    client.loop_start()

    def publish(topic: str, payload: bytes, retain: bool = False) -> None:
        client.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)

    yield publish
    client.disconnect()
    client.loop_stop()


def _hub_sql(hub: TestClient, sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(hub.app.state.settings.hub_database_url) as conn:
        return conn.execute(sql, params).fetchall()


def _ready(hub: TestClient) -> None:
    """All four consumers subscribed (SUBACK received) before the first message."""
    consumers = hub.app.state.consumers
    eventually(lambda: _check(len(consumers) == 4 and all(c.subscribed.is_set() for c in consumers)), timeout=10,
               interval=0.05)


def _check(condition: bool) -> None:
    assert condition


def test_s4_damper_fault_becomes_work_order(hub, gateway, registry_stub) -> None:
    _ready(hub)
    gateway(*beta_damper(closing_time_s=95.2))

    eventually(lambda: _check(len(registry_stub.of("/api/v1/work-orders")) == 1), timeout=15)
    [(path, body, headers)] = registry_stub.calls
    assert body["asset_code"] == "KL-B1-3-002"
    assert body["reason"] == "usterka_urzadzenia"
    assert "95.2" in body["description"]
    assert headers["Authorization"] == "Bearer t-hub"
    assert headers["Idempotency-Key"] and headers["X-Correlation-Id"]
    events = [r[0] for r in _hub_sql(hub, "SELECT event FROM integration_events WHERE correlation_id = %s ORDER BY id",
                                      (headers["X-Correlation-Id"],))]
    assert "work_order_queued" in events


def test_below_threshold_fault_is_ignored(hub, gateway, registry_stub) -> None:
    _ready(hub)
    gateway(*beta_damper(closing_time_s=30))

    eventually(lambda: _check(_hub_sql(hub, "SELECT count(*) FROM processed_messages WHERE consumer = 'faults'") == [(1,)]), timeout=15)
    assert registry_stub.calls == []


def test_duplicate_delivery_has_one_effect(hub, gateway, registry_stub) -> None:
    _ready(hub)
    message = beta_damper(at=datetime(2026, 11, 4, 10, 15, tzinfo=UTC))
    gateway(*message)
    gateway(*message)

    eventually(lambda: _check(_hub_sql(hub, "SELECT count(*) FROM integration_events WHERE event = 'duplicate_dropped'") == [(1,)]), timeout=15)
    eventually(lambda: _check(len(registry_stub.of("/api/v1/work-orders")) == 1), timeout=10)
    assert _hub_sql(hub, "SELECT count(*) FROM outbox") == [(1,)]


def test_s5_two_detectors_confirm_one_is_preliminary(hub, gateway, registry_stub) -> None:
    _ready(hub)
    now = datetime.now(UTC)
    gateway(*alfa("A-000123", "ALARM", now))
    gateway(*alfa("A-000124", "ALARM", now + timedelta(seconds=1)))

    eventually(lambda: _check(len(registry_stub.of("/api/v1/incidents")) == 1), timeout=15)
    [incident] = registry_stub.of("/api/v1/incidents")
    assert incident["kind"] == "alarm_potwierdzony"
    assert sorted(incident["detectors"]) == ["A-000123", "A-000124"]

    # A single detector later: the window expires into a preliminary alarm.
    gateway(*alfa("A-000123", "ALARM", now + timedelta(seconds=10)))
    eventually(lambda: _check(len(registry_stub.of("/api/v1/incidents")) == 2), timeout=15)
    assert registry_stub.of("/api/v1/incidents")[1]["kind"] == "alarm_wstepny"


def test_s6_contamination_filter(hub, gateway, registry_stub) -> None:
    _ready(hub)
    gateway(*alfa("A-000124", "TLM", contamination=40))
    gateway(*alfa("A-000124", "TLM", contamination=85))

    eventually(lambda: _check(len(registry_stub.of("/api/v1/work-orders")) == 1), timeout=15)
    assert registry_stub.of("/api/v1/work-orders")[0]["reason"] == "czyszczenie"
    assert _hub_sql(hub, "SELECT count(*) FROM processed_messages WHERE consumer = 'telemetry'") == [(2,)]


def test_s7_unknown_device_dead_letter_and_replay(hub, gateway, registry_stub) -> None:
    _ready(hub)
    gateway(*alfa("A-000999", "TLM", contamination=95))
    eventually(lambda: _check(_hub_sql(hub, "SELECT reason FROM dead_letters") == [("unknown_device",)]), timeout=15)
    # Processing continues after the dead letter.
    gateway(*alfa("A-000124", "TLM", contamination=90))
    eventually(lambda: _check(len(registry_stub.of("/api/v1/work-orders")) == 1), timeout=15)

    registry_stub.devices.append({**CATALOG[0], "device_id": "A-000999", "asset_code": "CZ-B1-3-003"})
    [(dead_id,)] = _hub_sql(hub, "SELECT id FROM dead_letters")
    assert hub.post(f"/api/v1/dead-letters/{dead_id}/replay").status_code == 401
    hub.post("/login", data={"user": "jan.technik", "next": "/"})
    assert hub.post(f"/api/v1/dead-letters/{dead_id}/replay").status_code == 403
    hub.post("/login", data={"user": "admin", "next": "/"})
    response = hub.post(f"/api/v1/dead-letters/{dead_id}/replay")
    assert response.status_code == 200, response.text

    eventually(lambda: _check(len(registry_stub.of("/api/v1/work-orders")) == 2), timeout=15)
    assert registry_stub.of("/api/v1/work-orders")[1]["asset_code"] == "CZ-B1-3-003"
    assert _hub_sql(hub, "SELECT replayed_at IS NOT NULL FROM dead_letters") == [(True,)]


def test_invalid_payload_is_dead_lettered(hub, gateway) -> None:
    _ready(hub)
    gateway("vigiles/raw/alfa/gw-01/A-000123", b"{broken")
    eventually(lambda: _check(_hub_sql(hub, "SELECT reason FROM dead_letters") == [("invalid_payload",)]), timeout=15)


def test_c7_gateway_last_will_creates_connectivity_orders(hub, gateway, registry_stub) -> None:
    _ready(hub)
    gateway("vigiles/status/gw-01", json.dumps({"status": "offline", "session": "s-1"}).encode(), retain=True)

    eventually(lambda: _check(len(registry_stub.of("/api/v1/work-orders")) == 3), timeout=15)
    assert {b["reason"] for b in registry_stub.of("/api/v1/work-orders")} == {"brak_lacznosci"}


def test_console_and_api_permissions(hub) -> None:
    assert hub.get("/api/v1/outbox").status_code == 401
    hub.post("/login", data={"user": "anna.kierownik", "next": "/"})
    assert hub.get("/api/v1/outbox").status_code == 200
    assert hub.get("/api/v1/dead-letters").status_code == 403
    assert hub.get("/ui/console").status_code == 200



def test_will_after_broker_restart_is_cancelled_when_same_session_returns(hub, gateway, registry_stub) -> None:
    _ready(hub)
    hub.app.state.faults.offline_grace_s = 30
    from vigiles_hub.consumers.base import connectivity_tag

    tag = connectivity_tag("gw-01", "s-2")
    gateway("vigiles/status/gw-01", json.dumps({"status": "offline", "session": "s-2"}).encode())
    eventually(lambda: _check(_hub_sql(hub, "SELECT count(*) FROM outbox WHERE kind = %s", (tag,)) == [(3,)]), timeout=10)

    gateway("vigiles/status/gw-01", json.dumps({"status": "online", "session": "s-2"}).encode())
    eventually(lambda: _check(_hub_sql(hub, "SELECT count(*) FROM outbox WHERE kind = %s", (tag,)) == [(0,)]), timeout=10)
    assert registry_stub.of("/api/v1/work-orders") == []

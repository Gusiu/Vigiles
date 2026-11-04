import json
import queue
import uuid

import paho.mqtt.client as mqtt
import pytest
from fastapi.testclient import TestClient
from paho.mqtt.enums import CallbackAPIVersion

from harness.broker import PASSWORDS, mosquitto
from harness.eventually import eventually

KEY = {"X-Api-Key": "t-key"}


def _settings(broker, tmp_path):
    from vigiles_gateway.app import GatewaySettings

    return GatewaySettings(gateway_api_key="t-key", gateway_buffer_path=tmp_path / "gw.sqlite",
                           mqtt_host=broker.host, mqtt_port=broker.port, mqtt_gw01_password=PASSWORDS["gw-01"])


def _listen(broker) -> tuple[mqtt.Client, "queue.Queue[tuple[str, bytes]]"]:
    got: queue.Queue = queue.Queue()
    c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"l-{uuid.uuid4().hex[:6]}", protocol=mqtt.MQTTv5)
    c.username_pw_set("explorer", PASSWORDS["explorer"])
    ready: queue.Queue = queue.Queue()
    c.on_connect = lambda cl, *a: cl.subscribe("vigiles/#", qos=1)
    c.on_subscribe = lambda *a: ready.put(True)
    c.on_message = lambda cl, ud, m: got.put((m.topic, m.payload))
    c.connect(broker.host, broker.port)
    c.loop_start()
    ready.get(timeout=5)
    return c, got


def _drain(got: queue.Queue) -> list[tuple[str, bytes]]:
    items = []
    while True:
        try:
            items.append(got.get(timeout=0.3))
        except queue.Empty:
            return items


def test_scenarios_publish_vendor_formats(fresh_broker, tmp_path) -> None:
    from vigiles_gateway.app import create_app

    listener, got = _listen(fresh_broker)
    with TestClient(create_app(_settings(fresh_broker, tmp_path))) as gw:
        assert gw.post("/scenario/damper-fault", json={}).status_code == 401
        eventually(lambda: _check(gw.get("/buffer", headers=KEY).json()["connected"]), timeout=10)
        assert gw.post("/scenario/damper-fault", json={"device_id": "BETA-7F3K2", "closing_time_s": 95.2}, headers=KEY).status_code == 202
        assert gw.post("/scenario/contamination", json={"device_id": "A-000124", "value": 85}, headers=KEY).status_code == 202
        eventually(lambda: _check(gw.get("/buffer", headers=KEY).json()["buffered"] == 0), timeout=10)
    messages = dict(_drain(got))
    beta = json.loads(messages["vigiles/raw/beta/gw-01/BETA-7F3K2"])
    assert beta["event"] == {"type": "fault", "code": "DAMPER_SLOW_CLOSE", "measured": "95,2", "unit": "s"}
    assert beta["time"].endswith("+01:00")
    alfa = json.loads(messages["vigiles/raw/alfa/gw-01/A-000124"])
    assert alfa["evt"] == "TLM" and alfa["contamination"] == 85
    assert json.loads(messages["vigiles/status/gw-01"])["status"] == "online"
    listener.loop_stop()


def test_store_and_forward_when_broker_is_down(tmp_path) -> None:
    from harness.broker import free_port
    from vigiles_gateway.app import create_app

    port = free_port()
    state = tmp_path / "state"
    with mosquitto(state, port) as broker:
        app = create_app(_settings(broker, tmp_path))
        with TestClient(app) as gw:
            eventually(lambda: _check(gw.get("/buffer", headers=KEY).json()["connected"]), timeout=10)
    # The broker is gone now; the gateway keeps accepting device messages.
    with TestClient(create_app(_settings(broker, tmp_path))) as gw:
        for _ in range(5):
            gw.post("/scenario/contamination", json={"device_id": "A-000123", "value": 10}, headers=KEY)
        assert gw.get("/buffer", headers=KEY).json() == {"buffered": 5, "connected": False}
        with mosquitto(state, port) as broker:
            listener, got = _listen(broker)
            eventually(lambda: _check(gw.get("/buffer", headers=KEY).json()["buffered"] == 0), timeout=40)
            raw = [t for t, _ in _drain(got) if t.startswith("vigiles/raw/")]
            assert len(raw) >= 5
            listener.loop_stop()


def _check(condition: bool) -> None:
    assert condition

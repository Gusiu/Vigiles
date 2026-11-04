"""Observed behaviour of Mosquitto 2.0.18 across a restart (documented in
docs/resilience.md): the session of a durable client is restored, its
plain subscriptions too, but its shared ($share) subscriptions are not -
messages published before the client subscribes again find no receiver.
"""

import queue
import time

import pytest

from harness.broker import PASSWORDS, free_port, mosquitto
from vigiles_common.mqtt import MqttSettings, make_client


def _after_restart(tmp_path, topic: str) -> tuple[bool, bool]:
    port, state = free_port(), tmp_path / "state"
    with mosquitto(state, port) as broker:
        hub = MqttSettings(broker.host, broker.port, "hub", PASSWORDS["hub"])
        subscribed: queue.Queue = queue.Queue()
        client, props = make_client(hub, "durable-test")
        client.on_connect = lambda c, u, f, rc, p: c.subscribe(topic, qos=1)
        client.on_subscribe = lambda *a: subscribed.put(True)
        client.connect(broker.host, broker.port, clean_start=False, properties=props)
        client.loop_start()
        subscribed.get(timeout=5)
        client.disconnect()
        client.loop_stop()
    with mosquitto(state, port) as broker:
        gw = MqttSettings(broker.host, broker.port, "gw-01", PASSWORDS["gw-01"])
        publisher, pprops = make_client(gw, "publisher-test", durable=False)
        publisher.connect(broker.host, broker.port, properties=pprops)
        publisher.loop_start()
        publisher.publish("vigiles/raw/alfa/gw-01/X", b"{}", qos=1).wait_for_publish(5)
        publisher.disconnect()
        publisher.loop_stop()
        got: queue.Queue = queue.Queue()
        session: dict = {}
        client, props = make_client(MqttSettings(broker.host, broker.port, "hub", PASSWORDS["hub"]), "durable-test")
        client.on_connect = lambda c, u, f, rc, p: session.update(present=f.session_present)
        client.on_message = lambda c, u, m: got.put(m.topic)
        client.connect(broker.host, broker.port, clean_start=False, properties=props)
        client.loop_start()
        deadline = time.monotonic() + 2
        while got.empty() and time.monotonic() < deadline:
            time.sleep(0.05)
        client.disconnect()
        client.loop_stop()
        return session.get("present", False), not got.empty()


def test_plain_subscription_survives_restart(tmp_path) -> None:
    assert _after_restart(tmp_path, "vigiles/raw/#") == (True, True)


def test_shared_subscription_is_not_restored(tmp_path) -> None:
    present, delivered = _after_restart(tmp_path, "$share/g/vigiles/raw/#")
    assert present is True
    if delivered:
        pytest.fail("Mosquitto now restores $share subscriptions; revisit GATEWAY_RECONNECT_HOLD_S and docs/resilience.md")

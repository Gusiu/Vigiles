"""Broker security: no anonymous access, ACL per user (PLAN.md 11.4)."""

import queue
import uuid

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from harness.broker import PASSWORDS


def client(broker, user: str | None, password: str | None = None) -> tuple[mqtt.Client, int]:
    c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"test-{uuid.uuid4().hex[:8]}", protocol=mqtt.MQTTv5)
    if user:
        c.username_pw_set(user, password if password is not None else PASSWORDS[user])
    result: queue.Queue = queue.Queue()
    c.on_connect = lambda cl, ud, flags, rc, props: result.put(rc)
    c.connect(broker.host, broker.port)
    c.loop_start()
    rc = result.get(timeout=5)
    return c, rc.value


@pytest.mark.parametrize(("user", "password"), [(None, None), ("hub", "wrong"), ("nobody", "x")])
def test_rejects_anonymous_and_bad_credentials(broker, user, password) -> None:
    c, rc = client(broker, user, password)
    c.loop_stop()
    assert rc != 0


def _received(broker, topic_filter: str, publisher: str, topic: str) -> bool:
    sub, rc = client(broker, "explorer")
    assert rc == 0
    got: queue.Queue = queue.Queue()
    sub.on_message = lambda cl, ud, msg: got.put(msg.topic)
    subscribed: queue.Queue = queue.Queue()
    sub.on_subscribe = lambda *args: subscribed.put(True)
    sub.subscribe(topic_filter, qos=1)
    subscribed.get(timeout=5)
    pub, rc = client(broker, publisher)
    assert rc == 0
    pub.publish(topic, b"{}", qos=1).wait_for_publish(5)
    try:
        return got.get(timeout=1.0) == topic
    except queue.Empty:
        return False
    finally:
        for c in (sub, pub):
            c.disconnect()
            c.loop_stop()


@pytest.mark.parametrize(
    ("publisher", "topic", "allowed"),
    [
        ("gw-01", "vigiles/raw/alfa/gw-01/A-000123", True),
        ("gw-01", "vigiles/status/gw-01", True),
        ("gw-01", "vigiles/raw/alfa/gw-02/A-000123", False),
        ("gw-01", "vigiles/events/alarm/B1/Z3", False),
        ("hub", "vigiles/events/alarm/B1/Z3", True),
        ("hub", "vigiles/dlq/unknown_device", True),
        ("hub", "vigiles/raw/alfa/gw-01/A-000123", False),
        ("explorer", "vigiles/events/alarm/B1/Z3", False),
    ],
)
def test_acl(broker, publisher: str, topic: str, allowed: bool) -> None:
    assert _received(broker, "vigiles/#", publisher, topic) is allowed

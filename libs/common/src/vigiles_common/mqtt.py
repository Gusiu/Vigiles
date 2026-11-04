"""MQTT 5 connections with durable sessions and acknowledgement after commit.

Rules from PLAN.md section 18 implemented here:

* a fixed ``client_id`` per connection (two processes with the same id
  would keep kicking each other off the broker),
* ``clean_start=False`` with Session Expiry Interval > 0, otherwise the
  broker drops the session and buffers nothing while we are away,
* the paho network thread never waits for the database: ``on_message``
  only queues the message; one worker per connection processes messages
  in arrival order and calls ``client.ack()`` after the handler returned
  (that is, after its transaction committed),
* Receive Maximum equals the queue size, so the broker never sends more
  unacknowledged messages than the worker can hold.

A handler that raises ``PermanentError`` has already dealt with the
message (e.g. moved it to the dead letter channel) and it is acked. Any
other exception means "try again": the worker retries the same message
with backoff and does not ack it, so the broker keeps it.
"""

import queue
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from vigiles_common.logging import get_logger
from vigiles_common.retry import RetryPolicy

log = get_logger(__name__)

SESSION_EXPIRY_S = 24 * 3600


@dataclass(frozen=True)
class MqttSettings:
    host: str
    port: int
    username: str
    password: str


def make_client(settings: MqttSettings, client_id: str, durable: bool = True, receive_maximum: int = 20,
                manual_ack: bool = False, will: tuple[str, bytes] | None = None) -> tuple[mqtt.Client, Properties]:
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv5,
                         manual_ack=manual_ack)
    client.username_pw_set(settings.username, settings.password)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    if will:
        client.will_set(will[0], will[1], qos=1, retain=True)
    props = Properties(PacketTypes.CONNECT)
    if durable:
        props.SessionExpiryInterval = SESSION_EXPIRY_S
    props.ReceiveMaximum = receive_maximum
    return client, props


class Consumer:
    """One durable subscription processed by one worker thread."""

    def __init__(self, settings: MqttSettings, client_id: str, topic: str,
                 handler: Callable[[mqtt.MQTTMessage], None], queue_size: int = 20,
                 policy: RetryPolicy = RetryPolicy(base_s=0.5, cap_s=5.0)) -> None:
        self.settings = settings
        self.client_id = client_id
        self.topic = topic
        self.handler = handler
        self.queue: queue.Queue[mqtt.MQTTMessage] = queue.Queue(maxsize=queue_size)
        self.policy = policy
        self.client, self.connect_props = make_client(settings, client_id, receive_maximum=queue_size, manual_ack=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.connected = threading.Event()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._work, name=f"mqtt-{client_id}", daemon=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("mqtt_connect_failed", client_id=self.client_id, reason=str(reason_code))
            return
        client.subscribe(self.topic, qos=1)
        self.connected.set()
        log.info("mqtt_connected", client_id=self.client_id, topic=self.topic,
                 session_present=flags.session_present)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected.clear()
        if not self._stop.is_set():
            log.warning("mqtt_disconnected", client_id=self.client_id, reason=str(reason_code))

    def _on_message(self, client, userdata, message: mqtt.MQTTMessage) -> None:
        self.queue.put(message)  # never blocks for long: Receive Maximum == queue size

    def _work(self) -> None:
        rng = random.Random()
        while not self._stop.is_set():
            try:
                message = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            attempt = 0
            while not self._stop.is_set():
                try:
                    self.handler(message)
                    break
                except PermanentError:
                    break
                except Exception:
                    log.exception("mqtt_handler_failed_will_retry", client_id=self.client_id,
                                  topic=message.topic, attempt=attempt)
                    self._stop.wait(self.policy.delay(attempt, rng=rng))
                    attempt += 1
            else:
                return  # stopping: leave the message unacknowledged for redelivery
            self.client.ack(message.mid, message.qos)

    def start(self) -> None:
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=15,
                                  clean_start=False, properties=self.connect_props)
        self.client.loop_start()
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._worker.join(timeout=10)
        self.client.disconnect()
        self.client.loop_stop()


class PermanentError(Exception):
    """The message cannot be processed and was handled (e.g. dead-lettered)."""


class Publisher:
    """Connection used to publish with QoS 1 and wait for PUBACK."""

    def __init__(self, settings: MqttSettings, client_id: str, durable: bool = False) -> None:
        self.client, props = make_client(settings, client_id, durable=durable)
        self.connected = threading.Event()
        self.client.on_connect = lambda c, u, f, rc, p: self.connected.set() if not rc.is_failure else None
        self.client.on_disconnect = lambda *a: self.connected.clear()
        self.client.connect_async(settings.host, settings.port, keepalive=15, clean_start=not durable,
                                  properties=props)
        self.client.loop_start()

    def publish(self, topic: str, payload: bytes, timeout_s: float = 10.0, retain: bool = False) -> None:
        if not self.connected.wait(timeout_s):
            raise ConnectionError("broker not connected")
        info = self.client.publish(topic, payload, qos=1, retain=retain)
        info.wait_for_publish(timeout_s)
        if not info.is_published():
            raise TimeoutError(f"no PUBACK for {topic} within {timeout_s} s")

    def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

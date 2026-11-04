"""Hub process: MQTT consumers, outbox relay, sweeper, wire tap, API and console.

Each consumer is its own MQTT connection with a fixed client id
``<instance>-<consumer>``; replicas share the work through ``$share``
subscriptions (Competing Consumers).
"""

import json
import threading
from datetime import UTC, datetime
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from vigiles_common.auth.modes import build_token_provider, build_verifier
from vigiles_common.correlation import CorrelationIdMiddleware
from vigiles_common.health import health_router
from vigiles_common.logging import configure_logging, get_logger
from vigiles_common.migrations import apply_migrations
from vigiles_common.mqtt import Consumer, MqttSettings, Publisher, make_client
from vigiles_common.outbox import OutboxRelay
from vigiles_common.problems import install_problem_handlers
from vigiles_hub import console
from vigiles_hub.consumers.aggregator import AggregatorConsumer
from vigiles_hub.consumers.faults import FaultsConsumer
from vigiles_hub.consumers.rules import Thresholds
from vigiles_hub.consumers.telemetry import TelemetryConsumer
from vigiles_hub.db import HubDatabase
from vigiles_hub.dlq import dead_letter
from vigiles_hub.events import Broadcaster, EventLog
from vigiles_hub.ingress.enricher import DeviceCatalog
from vigiles_hub.ingress.pipeline import Ingress
from vigiles_hub.settings import HubSettings

MIGRATIONS = Path(__file__).parent / "migrations"
log = get_logger(__name__)

SUBSCRIPTIONS = {
    "ingress": ["$share/ingress/vigiles/raw/#", "$share/ingress/vigiles/status/+"],
    "aggregator": "$share/aggregator/vigiles/events/alarm/#",
    "faults": "$share/faults/vigiles/events/fault/#",
    "telemetry": "$share/telemetry/vigiles/events/telemetry/#",
}


def create_app(settings: HubSettings | None = None) -> FastAPI:
    settings = settings or HubSettings()
    instance = settings.hub_instance
    db = HubDatabase(settings.hub_database_url)
    broadcaster = Broadcaster()
    events = EventLog(instance, broadcaster)
    tokens = build_token_provider(settings, "svc-hub")
    http = httpx.Client(timeout=settings.http_timeout_s)
    mqtt_settings = MqttSettings(settings.mqtt_host, settings.mqtt_port, "hub", settings.mqtt_hub_password)

    def fetch_devices() -> list[dict]:
        response = http.get(settings.registry_url.rstrip("/") + "/api/v1/devices",
                            headers={"Authorization": f"Bearer {tokens.token()}"})
        response.raise_for_status()
        return response.json()

    catalog = DeviceCatalog(fetch_devices, ttl_s=settings.catalog_ttl_s, min_refresh_s=settings.catalog_min_refresh_s)
    publisher = Publisher(mqtt_settings, f"{instance}-publisher")
    ingress = Ingress(catalog, publisher, db, events)
    thresholds = Thresholds(settings.damper_closing_time_s, settings.contamination_pct)
    aggregator = AggregatorConsumer(db, events, settings.registry_url, instance, settings.alarm_window_s,
                                    settings.alarm_lateness_s)
    handlers = {
        "ingress": ingress.handle,
        "aggregator": aggregator.handle,
        "faults": (faults := FaultsConsumer(db, events, settings.registry_url, thresholds, instance,
                                            settings.gateway_offline_grace_s)).handle,
        "telemetry": TelemetryConsumer(db, events, settings.registry_url, thresholds, instance).handle,
    }
    stop = threading.Event()

    @contextmanager
    def connect():
        with db.connection() as conn:
            yield conn

    def on_dead(conn, row, error) -> None:
        dead_letter(conn, source_topic=f"outbox:{row['id']}", reason="delivery_failed",
                    raw=json.dumps(row["payload"]), error=error, message_id=str(row["message_id"]))
        events.record(conn, "outbox", "delivery_failed", "dead", str(row["message_id"]), row.get("correlation_id"),
                      target=row["target_url"], error=error)

    relay = OutboxRelay(connect, http, instance, token=tokens.token, invalidate_token=tokens.invalidate,
                        on_dead=on_dead)

    def on_sent(original):
        def record(row, delivery):
            original(row, delivery)
            if delivery.status == "sent":
                with db.transaction() as conn:
                    events.record(conn, "outbox", "registry_call_sent", "ok", str(row["message_id"]),
                                  row.get("correlation_id"), kind=row["kind"])
        return record

    relay.record = on_sent(relay.record)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.open()
        consumers: list[Consumer] = app.state.consumers
        threads: list[threading.Thread] = []
        wiretap = None
        if settings.background_workers:
            catalog.warm_up()
            for name, topic in SUBSCRIPTIONS.items():
                consumer = Consumer(mqtt_settings, f"{instance}-{name}", topic, handlers[name])
                consumer.start()
                consumers.append(consumer)
            threads = [
                threading.Thread(target=relay.run, args=(stop, settings.outbox_interval_s), name="outbox", daemon=True),
                threading.Thread(target=aggregator.run_sweeper, args=(stop, settings.sweeper_interval_s),
                                 name="sweeper", daemon=True),
            ]
            for thread in threads:
                thread.start()
            wiretap = start_wiretap(mqtt_settings, f"{instance}-console", broadcaster, instance)
        yield
        stop.set()
        for consumer in consumers:
            consumer.stop()
        for thread in threads:
            thread.join(timeout=5)
        if wiretap:
            wiretap.disconnect()
            wiretap.loop_stop()
        publisher.close()
        db.close()

    app = FastAPI(title=f"Vigiles Hub ({instance})", version="5.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.events = events
    app.state.broadcaster = broadcaster
    app.state.ingress = ingress
    app.state.aggregator = aggregator
    app.state.relay = relay
    app.state.consumers = []
    app.state.faults = faults
    app.state.verifier = build_verifier(settings, audience="hub-api")
    app.add_middleware(CorrelationIdMiddleware)
    install_problem_handlers(app)
    app.include_router(health_router({"database": db.ping}))
    app.include_router(console.router)
    console.install_console(app, settings)
    return app


def start_wiretap(settings: MqttSettings, client_id: str, broadcaster: Broadcaster, instance: str):
    """Console view of all topics: QoS 0, no session, no effect on the flow."""
    client, props = make_client(settings, client_id, durable=False)
    client.on_connect = lambda c, u, f, rc, p: c.subscribe("vigiles/#", qos=0)

    def on_message(c, u, message) -> None:
        broadcaster.publish({"ts": datetime.now(UTC).isoformat(),
                             "instance": instance, "stage": "mqtt", "event": message.topic, "outcome": "seen",
                             "message_id": None, "correlation_id": None,
                             "details": {"payload": message.payload[:300].decode("utf-8", "replace")}})

    client.on_message = on_message
    client.connect_async(settings.host, settings.port, keepalive=15, clean_start=True, properties=props)
    client.loop_start()
    return client


def migrate() -> None:
    settings = HubSettings()
    configure_logging(f"{settings.hub_instance}", settings.log_level, settings.log_format)
    apply_migrations(settings.hub_database_url, MIGRATIONS)


def main() -> None:
    import uvicorn

    settings = HubSettings()
    configure_logging(settings.hub_instance, settings.log_level, settings.log_format)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.hub_port, log_config=None)

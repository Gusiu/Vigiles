"""The local stack for e2e and chaos tests: registry, field app and
importer as real processes on a throwaway database and data folder."""

import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg

from harness.db import Database, DatabaseSettings, throwaway_database
from harness.nodes import NodeController, supervisor
from harness.registry import SERVICE_TOKENS

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"
REGISTRY_PORT, FIELD_APP_PORT, HUB_PORTS, GATEWAY_PORT, MQTT_PORT, ISSUER_PORT = 18001, 18002, (18003, 18004), 18005, 11883, 18006
ISSUER = f"http://127.0.0.1:{ISSUER_PORT}"
GATEWAY_KEY = {"X-Api-Key": "t-gateway"}


@dataclass
class Stack:
    db: Database
    data_dir: Path
    field_db_path: Path
    nodes: NodeController
    registry_url: str = f"http://127.0.0.1:{REGISTRY_PORT}"
    field_app_url: str = f"http://127.0.0.1:{FIELD_APP_PORT}"
    gateway_url: str = f"http://127.0.0.1:{GATEWAY_PORT}"
    hub_db_url: str = ""

    def hub_sql(self, sql: str, params: tuple = ()) -> list[tuple]:
        with psycopg.connect(self.hub_db_url) as conn:
            return conn.execute(sql, params).fetchall()

    def scenario(self, name: str, body: dict | None = None) -> dict:
        response = httpx.post(f"{self.gateway_url}/scenario/{name}", json=body or {}, headers=GATEWAY_KEY, timeout=10)
        response.raise_for_status()
        return response.json()

    def gateway_buffer(self) -> int:
        return httpx.get(f"{self.gateway_url}/buffer", headers=GATEWAY_KEY, timeout=5).json()["buffered"]

    def drop(self, sample: str) -> None:
        shutil.copy(SAMPLES / sample, self.data_dir / "inbox" / f".{sample}.part")
        (self.data_dir / "inbox" / f".{sample}.part").rename(self.data_dir / "inbox" / sample)

    def registry_sql(self, sql: str, params: tuple = ()) -> list[tuple]:
        with psycopg.connect(self.db.owner_url) as conn:
            return conn.execute(sql, params).fetchall()

    def field_sql(self, sql: str, params: tuple = ()) -> list[tuple]:
        with sqlite3.connect(self.field_db_path, timeout=10) as conn:
            return conn.execute(sql, params).fetchall()

    def ui(self, service: str, user: str) -> httpx.Client:
        base = self.registry_url if service == "registry" else self.field_app_url
        client = httpx.Client(base_url=base, follow_redirects=True, timeout=10)
        client.post("/login", data={"user": user, "next": "/"}).raise_for_status()
        return client


@contextmanager
def local_stack(tmp: Path, messaging: bool = False) -> Iterator[Stack]:
    from harness.broker import ENV as MQTT_ENV
    from harness.db import with_database

    settings = DatabaseSettings()
    with throwaway_database(settings) as db, throwaway_database(settings, owner="hub_app") as hub_db:
        hub_url = with_database(settings.hub_database_url, hub_db.name)
        data_dir = tmp / "data"
        for sub in ("inbox", "archive", "error"):
            (data_dir / sub).mkdir(parents=True)
        field_db = tmp / "field.sqlite"
        env = {
            "REGISTRY_DATABASE_URL": db.owner_url,
            "IMPORTER_DATABASE_URL": db.importer_url,
            "IMPORTER_DATA_DIR": str(data_dir),
            "IMPORTER_POLL_INTERVAL_S": "0.5",
            "IMPORTER_SETTLE_S": "0",
            "REGISTRY_PORT": str(REGISTRY_PORT),
            "FIELD_APP_PORT": str(FIELD_APP_PORT),
            "REGISTRY_URL": f"http://127.0.0.1:{REGISTRY_PORT}",
            "FIELD_APP_URL": f"http://127.0.0.1:{FIELD_APP_PORT}",
            "FIELD_APP_DB_PATH": str(field_db),
            # Local mode authenticates like production, with the dev issuer
            # standing in for Keycloak.
            "AUTH_MODE": "dev",
            "SERVICE_TOKENS": SERVICE_TOKENS,
            "OIDC_ISSUER": ISSUER,
            "OIDC_JWKS_URL": f"{ISSUER}/protocol/openid-connect/certs",
            "OIDC_TOKEN_URL": f"{ISSUER}/protocol/openid-connect/token",
            "OIDC_SECRET_SVC_HUB": "t-secret-hub",
            "OIDC_SECRET_SVC_FIELD_APP": "t-secret-field",
            "OIDC_SECRET_SVC_REGISTRY": "t-secret-registry",
            "DEV_ISSUER_PORT": str(ISSUER_PORT),
            "DEV_ISSUER_KEY": str(tmp / "dev_issuer.pem"),
            "RETRY_BASE_S": "0.2",
            "RETRY_CAP_S": "2",
            "LOG_FORMAT": "json",
            # messaging
            **MQTT_ENV,
            "MQTT_HOST": "127.0.0.1",
            "MQTT_PORT": str(MQTT_PORT),
            "MOSQUITTO_STATE_DIR": str(tmp / "mosquitto"),
            "HUB_DATABASE_URL": hub_url,
            "ALARM_WINDOW_S": "5",
            "ALARM_LATENESS_S": "1",
            "SWEEPER_INTERVAL_S": "0.5",
            "OUTBOX_INTERVAL_S": "0.2",
            "CATALOG_MIN_REFRESH_S": "0",
            "GATEWAY_OFFLINE_GRACE_S": "20",
            "GATEWAY_API_KEY": GATEWAY_KEY["X-Api-Key"],
            "GATEWAY_PORT": str(GATEWAY_PORT),
            "GATEWAY_BUFFER_PATH": str(tmp / "gateway.sqlite"),
            "GATEWAY_SITE_CONFIG": str(SAMPLES / "site-config.json"),
        }
        nodes = supervisor.load_nodes()
        nodes["registry"].port = REGISTRY_PORT
        nodes["field-app"].port = FIELD_APP_PORT
        nodes["field-app"].env = {}
        nodes["mosquitto"].port = MQTT_PORT
        for name, port in zip(("hub-1", "hub-2"), HUB_PORTS):
            nodes[name].port = port
            nodes[name].env = {"HUB_INSTANCE": name, "HUB_PORT": str(port)}
        nodes["dev-issuer"].port = ISSUER_PORT
        nodes["gateway"].port = GATEWAY_PORT
        nodes["gateway"].env = {}
        if not messaging:
            for name in ("mosquitto", "hub-1", "hub-2", "gateway"):
                del nodes[name]
        local = supervisor.Supervisor(nodes, state_dir=tmp / ".local", env=env)
        try:
            local.up()
            yield Stack(db=db, data_dir=data_dir, field_db_path=field_db, nodes=NodeController("local", local),
                        hub_db_url=hub_url)
        finally:
            local.down()
            for log in sorted((tmp / ".local" / "logs").glob("*.log")):
                if os.environ.get("VIGILES_SHOW_LOGS"):
                    print(f"--- {log.name}\n{log.read_text()[-4000:]}")

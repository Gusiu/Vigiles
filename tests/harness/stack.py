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
REGISTRY_PORT, FIELD_APP_PORT = 18001, 18002


@dataclass
class Stack:
    db: Database
    data_dir: Path
    field_db_path: Path
    nodes: NodeController
    registry_url: str = f"http://127.0.0.1:{REGISTRY_PORT}"
    field_app_url: str = f"http://127.0.0.1:{FIELD_APP_PORT}"

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
def local_stack(tmp: Path, outage_s: float | None = None) -> Iterator[Stack]:
    with throwaway_database(DatabaseSettings()) as db:
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
            "AUTH_MODE": "static",
            "SERVICE_TOKENS": SERVICE_TOKENS,
            "RETRY_BASE_S": "0.2",
            "RETRY_CAP_S": "2",
            "LOG_FORMAT": "json",
        }
        nodes = supervisor.load_nodes()
        nodes["registry"].port = REGISTRY_PORT
        nodes["field-app"].port = FIELD_APP_PORT
        nodes["field-app"].env = {}
        local = supervisor.Supervisor(nodes, state_dir=tmp / ".local", env=env)
        try:
            local.up()
            yield Stack(db=db, data_dir=data_dir, field_db_path=field_db, nodes=NodeController("local", local))
        finally:
            local.down()
            for log in sorted((tmp / ".local" / "logs").glob("*.log")):
                if os.environ.get("VIGILES_SHOW_LOGS"):
                    print(f"--- {log.name}\n{log.read_text()[-4000:]}")

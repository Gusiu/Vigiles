"""Prepare local infrastructure without Docker (cloud sessions, CI-less runs).

Idempotent: installs PostgreSQL 16 and Mosquitto 2 from apt when missing,
starts PostgreSQL, sets the superuser password and applies
``infra/postgres/init.sql``. Mosquitto is started by the supervisor. Run as root or with sudo available:

    python scripts/local_up.py
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ["postgresql-16", "mosquitto"]
PG_VERSION, PG_CLUSTER = "16", "main"


def sudo(cmd: list[str]) -> list[str]:
    return cmd if os.geteuid() == 0 else ["sudo", *cmd]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def load_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy(ROOT / ".env.example", env_file)
        print("created .env from .env.example - change the passwords for anything but local use")
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def ensure_packages() -> None:
    missing = [
        p
        for p in PACKAGES
        if subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", p], capture_output=True, text=True
        ).stdout
        != "install ok installed"
    ]
    if not missing:
        return
    run(sudo(["apt-get", "update", "-q"]))
    run(
        sudo(["apt-get", "install", "-y", "-q", "--no-install-recommends", *missing]),
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, name: str, timeout_s: float = 30) -> None:
    deadline = time.monotonic() + timeout_s
    while not port_open(port):
        if time.monotonic() > deadline:
            sys.exit(f"{name} did not open port {port} within {timeout_s:.0f} s")
        time.sleep(0.5)


def ensure_postgres(env: dict[str, str]) -> None:
    clusters = subprocess.run(["pg_lsclusters", "-h"], capture_output=True, text=True).stdout.split()
    if PG_VERSION not in clusters[:1] and f"{PG_VERSION} {PG_CLUSTER}" not in " ".join(clusters):
        run(sudo(["pg_createcluster", PG_VERSION, PG_CLUSTER]))
    if not port_open(5432):
        run(sudo(["pg_ctlcluster", PG_VERSION, PG_CLUSTER, "start"]))
        wait_for_port(5432, "PostgreSQL")
    psql = sudo(["runuser", "-u", "postgres", "--", "psql", "-q", "-v", "ON_ERROR_STOP=1"])
    run(
        [*psql, "-v", f"pw={env['POSTGRES_PASSWORD']}"],
        input="ALTER ROLE postgres PASSWORD :'pw';\n",
        text=True,
    )
    run(
        [
            *psql,
            "-v", f"registry_app_password={env['REGISTRY_DB_PASSWORD']}",
            "-v", f"importer_password={env['IMPORTER_DB_PASSWORD']}",
            "-v", f"hub_app_password={env['HUB_DB_PASSWORD']}",
        ],
        input=(ROOT / "infra/postgres/init.sql").read_text(encoding="utf-8"),
        text=True,
    )


def main() -> None:
    env = load_env()
    ensure_packages()
    ensure_postgres(env)
    # Mosquitto runs with the project configuration (passwords, ACL,
    # persistence) as the supervisor node "mosquitto", not as a daemon here.
    print("local infrastructure ready: PostgreSQL on 5432; start the rest with scripts/supervisor.py up")


if __name__ == "__main__":
    main()

"""A Mosquitto instance with the project configuration for tests."""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASSWORDS = {"gw-01": "t-gw", "hub": "t-hub-mqtt", "explorer": "t-explorer"}
ENV = {"MQTT_GW01_PASSWORD": "t-gw", "MQTT_HUB_PASSWORD": "t-hub-mqtt", "MQTT_EXPLORER_PASSWORD": "t-explorer"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Broker:
    host: str
    port: int
    state_dir: Path


@contextmanager
def mosquitto(state_dir: Path, port: int | None = None) -> Iterator[Broker]:
    port = port or free_port()
    env = {**os.environ, **ENV, "MQTT_PORT": str(port), "MOSQUITTO_STATE_DIR": str(state_dir)}
    log = (state_dir.parent / f"mosquitto-{port}.log").open("ab")
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/run_mosquitto.py")], env=env, stdout=log,
                               stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while True:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(f"mosquitto did not start, see {log.name}")
        time.sleep(0.05)
    try:
        yield Broker("127.0.0.1", port, state_dir)
    finally:
        process.terminate()
        process.wait(timeout=10)

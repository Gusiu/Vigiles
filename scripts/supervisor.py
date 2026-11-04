"""Run the Vigiles processes locally, without Docker.

    python scripts/supervisor.py up | down | status
    python scripts/supervisor.py stop|start|kill <node>

Nodes are defined in ``local-nodes.yaml``. PIDs are kept in
``.local/pids/``, logs in ``.local/logs/<node>.log``. ``kill`` sends
SIGKILL (a crash), ``stop`` SIGTERM (a graceful shutdown). The chaos
tests drive the same class through ``NodeController(local)``.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BIN = Path(sys.executable).parent


@dataclass
class Node:
    name: str
    command: list[str]
    before: list[list[str]] = field(default_factory=list)
    port: int | None = None
    health: str | None = None
    env: dict[str, str] = field(default_factory=dict)


def load_nodes(path: Path = ROOT / "local-nodes.yaml") -> dict[str, Node]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: Node(name=name, **spec) for name, spec in data["nodes"].items()}


def _executable(command: list[str]) -> list[str]:
    if command[0] == "python":
        return [sys.executable, *command[1:]]
    candidate = BIN / command[0]
    return [str(candidate), *command[1:]] if candidate.exists() else command


class Supervisor:
    def __init__(self, nodes: dict[str, Node], state_dir: Path = ROOT / ".local",
                 env: dict[str, str] | None = None, cwd: Path = ROOT) -> None:
        self.nodes = nodes
        self.pids = state_dir / "pids"
        self.logs = state_dir / "logs"
        self.env = env or {}
        self.cwd = cwd
        self.pids.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)

    def _env(self, node: Node) -> dict[str, str]:
        return {**os.environ, **self.env, **node.env, "PYTHONUNBUFFERED": "1"}

    def pid(self, name: str) -> int | None:
        path = self.pids / f"{name}.pid"
        if not path.exists():
            return None
        pid = int(path.read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            path.unlink(missing_ok=True)
            return None
        # A zombie child counts as stopped.
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                path.unlink(missing_ok=True)
                return None
        except ChildProcessError:
            pass
        return pid

    def healthy(self, name: str) -> bool:
        node = self.nodes[name]
        if self.pid(name) is None:
            return False
        if node.port is None:
            return True
        if node.health:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{node.port}{node.health}", timeout=1) as r:
                    return r.status == 200
            except OSError:
                return False
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", node.port)) == 0

    def start(self, name: str, wait_s: float = 30) -> None:
        if self.pid(name):
            return
        node = self.nodes[name]
        env = self._env(node)
        log = (self.logs / f"{name}.log").open("ab")
        for step in node.before:
            subprocess.run(_executable(step), cwd=self.cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        process = subprocess.Popen(_executable(node.command), cwd=self.cwd, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        (self.pids / f"{name}.pid").write_text(str(process.pid))
        deadline = time.monotonic() + wait_s
        while not self.healthy(name):
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited with {process.returncode}; see {self.logs / (name + '.log')}")
            if time.monotonic() > deadline:
                raise RuntimeError(f"{name} not healthy within {wait_s:.0f} s")
            time.sleep(0.2)

    def _signal(self, name: str, sig: int, wait_s: float = 15) -> None:
        pid = self.pid(name)
        if pid is None:
            return
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + wait_s
        while self.pid(name) is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        if self.pid(name) is not None:
            os.killpg(pid, signal.SIGKILL)
            time.sleep(0.2)
        (self.pids / f"{name}.pid").unlink(missing_ok=True)

    def stop(self, name: str) -> None:
        self._signal(name, signal.SIGTERM)

    def kill(self, name: str) -> None:
        self._signal(name, signal.SIGKILL)

    def up(self) -> None:
        for name in self.nodes:
            self.start(name)

    def down(self) -> None:
        for name in reversed(list(self.nodes)):
            self.stop(name)

    def status(self) -> dict[str, str]:
        return {
            name: ("healthy" if self.healthy(name) else "running" if self.pid(name) else "stopped")
            for name in self.nodes
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["up", "down", "status", "start", "stop", "kill"])
    parser.add_argument("node", nargs="?")
    args = parser.parse_args()
    supervisor = Supervisor(load_nodes())
    if args.action in ("start", "stop", "kill"):
        if args.node not in supervisor.nodes:
            parser.error(f"node must be one of: {', '.join(supervisor.nodes)}")
        getattr(supervisor, args.action)(args.node)
    elif args.action in ("up", "down"):
        getattr(supervisor, args.action)()
    for name, state in supervisor.status().items():
        print(f"{name:12} {state}")


if __name__ == "__main__":
    main()

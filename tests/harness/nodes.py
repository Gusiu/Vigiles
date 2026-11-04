"""Starting, stopping and killing nodes for e2e and chaos tests.

``NodeController`` has two backends with the same interface:

* ``local`` - processes run by ``scripts/supervisor.py`` (cloud sessions),
* ``compose`` - ``docker compose stop/kill/start`` (Windows PC, CI).
"""

import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_supervisor():
    spec = importlib.util.spec_from_file_location("vigiles_supervisor", ROOT / "scripts" / "supervisor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


supervisor = _load_supervisor()


class NodeController:
    def __init__(self, backend: str = "local", local: "supervisor.Supervisor | None" = None) -> None:
        if backend not in ("local", "compose"):
            raise ValueError(backend)
        self.backend = backend
        self.local = local

    def _compose(self, *args: str) -> None:
        subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True)

    def _cluster(self, action: str) -> None:
        # Local mode: PostgreSQL is the system cluster installed by local_up.py.
        sudo = [] if os.geteuid() == 0 else ["sudo"]
        subprocess.run([*sudo, "pg_ctlcluster", "16", "main", action], check=True)
        if action == "start":
            subprocess.run(["pg_isready", "-h", "127.0.0.1", "-t", "30"], check=True, capture_output=True)

    def stop(self, node: str) -> None:
        if self.backend == "compose":
            self._compose("stop", node)
        elif node == "postgres":
            self._cluster("stop")
        else:
            self.local.stop(node)

    def kill(self, node: str) -> None:
        if self.backend == "compose":
            self._compose("kill", node)
        elif node == "postgres":
            self._cluster("stop")
        else:
            self.local.kill(node)

    def start(self, node: str) -> None:
        if self.backend == "compose":
            self._compose("start", node)
        elif node == "postgres":
            self._cluster("start")
        else:
            self.local.start(node)

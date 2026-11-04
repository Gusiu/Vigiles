"""Starting, stopping and killing nodes for e2e and chaos tests.

``NodeController`` has two backends with the same interface:

* ``local`` - processes run by ``scripts/supervisor.py`` (cloud sessions),
* ``compose`` - ``docker compose stop/kill/start`` (Windows PC, CI).
"""

import importlib.util
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

    def stop(self, node: str) -> None:
        self._compose("stop", node) if self.backend == "compose" else self.local.stop(node)

    def kill(self, node: str) -> None:
        self._compose("kill", node) if self.backend == "compose" else self.local.kill(node)

    def start(self, node: str) -> None:
        self._compose("start", node) if self.backend == "compose" else self.local.start(node)

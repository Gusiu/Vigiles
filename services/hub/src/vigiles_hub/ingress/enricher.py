"""Content Enricher: site, zone and asset code from the registry device
catalogue (``GET /api/v1/devices``), cached with a TTL. If the registry is
unavailable the stale catalogue is used; an unknown device triggers one
early refresh (it may just have been imported) before it is rejected."""

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from vigiles_common.logging import get_logger

log = get_logger(__name__)


class DeviceCatalog:
    def __init__(self, fetch: Callable[[], list[dict[str, Any]]], ttl_s: float = 60.0,
                 min_refresh_s: float = 2.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.fetch = fetch
        self.ttl_s = ttl_s
        self.min_refresh_s = min_refresh_s
        self.clock = clock
        self._devices: dict[str, dict[str, Any]] = {}
        self._loaded_at: float | None = None
        self._attempted_at: float | None = None
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        self._attempted_at = self.clock()
        try:
            devices = self.fetch()
        except (httpx.HTTPError, OSError) as exc:
            log.warning("device_catalog_refresh_failed", error=str(exc), cached=len(self._devices))
            return
        self._devices = {d["device_id"]: d for d in devices}
        self._loaded_at = self.clock()
        log.info("device_catalog_loaded", devices=len(self._devices))

    def get(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            now = self.clock()
            stale = self._loaded_at is None or now - self._loaded_at > self.ttl_s
            if stale and (self._attempted_at is None or now - self._attempted_at > self.min_refresh_s):
                self._refresh()
            device = self._devices.get(device_id)
            if device is None and (self._attempted_at is None or self.clock() - self._attempted_at > self.min_refresh_s):
                self._refresh()
                device = self._devices.get(device_id)
            return device

    def devices_of_gateway(self, gateway_id: str) -> list[dict[str, Any]]:
        self.get("")  # refresh when stale
        with self._lock:
            return [d for d in self._devices.values() if d.get("gateway_id") == gateway_id]

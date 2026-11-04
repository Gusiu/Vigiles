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


class CatalogUnavailable(Exception):
    """No catalogue has ever been loaded and the registry cannot be reached.
    Not a property of the message: it is retried, never dead-lettered."""


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
        self._last_ok = False
        self._lock = threading.Lock()

    def _refresh(self) -> bool:
        self._attempted_at = self.clock()
        try:
            devices = self.fetch()
        except (httpx.HTTPError, OSError) as exc:
            log.warning("device_catalog_refresh_failed", error=str(exc), cached=len(self._devices))
            self._last_ok = False
            return False
        self._devices = {d["device_id"]: d for d in devices}
        self._loaded_at = self.clock()
        self._last_ok = True
        log.info("device_catalog_loaded", devices=len(self._devices))
        return True

    def _may_refresh(self) -> bool:
        return self._attempted_at is None or self.clock() - self._attempted_at >= self.min_refresh_s

    def get(self, device_id: str) -> dict[str, Any] | None:
        """The device, or None when a successful refresh confirms it is unknown.

        Raises CatalogUnavailable when the device is not cached and the
        registry could not be asked: the message must wait, not be
        dead-lettered as unknown.
        """
        with self._lock:
            stale = self._loaded_at is None or self.clock() - self._loaded_at > self.ttl_s
            if stale and self._may_refresh():
                self._refresh()
            device = self._devices.get(device_id)
            if device is None and self._may_refresh():
                self._refresh()  # it may just have been imported
                device = self._devices.get(device_id)
            if device is None and not self._last_ok:
                raise CatalogUnavailable("device not in the cached catalogue and the registry is unreachable")
            return device

    def warm_up(self) -> None:
        """Load the catalogue at start (best effort), which also obtains a token."""
        with self._lock:
            self._refresh()

    def devices_of_gateway(self, gateway_id: str) -> list[dict[str, Any]]:
        self.get("")  # refresh when stale; raises CatalogUnavailable if never loaded
        with self._lock:
            return [d for d in self._devices.values() if d.get("gateway_id") == gateway_id]

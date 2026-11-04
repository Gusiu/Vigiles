"""Raw device messages in the vendor formats (PLAN.md 6.6) for tests."""

import itertools
import json
import time
from datetime import UTC, datetime, timedelta, timezone

_ids = itertools.count(int(time.time() * 1000) % 1_000_000_000)

CATALOG = [
    {"device_id": "A-000123", "vendor": "alfa", "gateway_id": "gw-01", "asset_code": "CZ-B1-3-001", "site": "B1", "zone": "Z3"},
    {"device_id": "A-000124", "vendor": "alfa", "gateway_id": "gw-01", "asset_code": "CZ-B1-3-002", "site": "B1", "zone": "Z3"},
    {"device_id": "BETA-7F3K2", "vendor": "beta", "gateway_id": "gw-01", "asset_code": "KL-B1-3-002", "site": "B1", "zone": "Z3"},
]


def alfa(device: str, evt: str, at: datetime | None = None, **extra) -> tuple[str, bytes]:
    at = at or datetime.now(UTC)
    body = {"id": next(_ids), "dev": device, "evt": evt, "ts": int(at.timestamp() * 1000), "zone": "Z3", **extra}
    return f"vigiles/raw/alfa/gw-01/{device}", json.dumps(body).encode()


def beta_damper(device: str = "BETA-7F3K2", closing_time_s: float = 95.2, at: datetime | None = None) -> tuple[str, bytes]:
    local = (at or datetime.now(UTC)).astimezone(timezone(timedelta(hours=1))).replace(microsecond=0)
    body = {"serial": device, "time": local.isoformat(),
            "event": {"type": "fault", "code": "DAMPER_SLOW_CLOSE", "measured": f"{closing_time_s}".replace(".", ","), "unit": "s"}}
    return f"vigiles/raw/beta/gw-01/{device}", json.dumps(body).encode()

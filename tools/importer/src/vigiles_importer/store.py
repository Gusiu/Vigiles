"""Database writes of the importer. All functions run inside the caller's
transaction, so a file is imported completely or not at all."""

from dataclasses import dataclass, field

import psycopg

from vigiles_importer.asset_rows import AssetRecord
from vigiles_importer.site_config import ParsedSiteConfig, RejectedItem

# Office-owned columns the importer maintains. Field-owned columns
# (condition, last_inspected_at) are never written. notes is shared with
# technicians, so it is only set when the asset is created.
_ASSET_COLUMNS = (
    "asset_type", "site_id", "building", "floor", "room", "partition", "installation",
    "diameter_mm", "seal_system", "fire_rating", "installed_at", "installer",
)

_UPSERT_ASSET = f"""
INSERT INTO assets (code, {", ".join(_ASSET_COLUMNS)}, notes, updated_by)
VALUES (%(code)s, {", ".join(f"%({c})s" for c in _ASSET_COLUMNS)}, %(notes)s, 'importer')
ON CONFLICT (code) DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in _ASSET_COLUMNS)},
    updated_by = EXCLUDED.updated_by
WHERE ({", ".join(f"assets.{c}" for c in _ASSET_COLUMNS)})
      IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in _ASSET_COLUMNS)})
RETURNING (xmax = 0) AS inserted
"""


@dataclass
class WriteStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[RejectedItem] = field(default_factory=list)

    def count(self, cursor: psycopg.Cursor) -> None:
        row = cursor.fetchone()
        if row is None:
            self.unchanged += 1
        elif row[0]:
            self.inserted += 1
        else:
            self.updated += 1


def begin_import(conn: psycopg.Connection) -> None:
    # Read by the row trigger to record who changed the data.
    conn.execute("SET LOCAL vigiles.origin = 'importer'")


def ensure_site(conn: psycopg.Connection, code: str) -> int:
    conn.execute("INSERT INTO sites (code) VALUES (%s) ON CONFLICT (code) DO NOTHING", (code,))
    return conn.execute("SELECT id FROM sites WHERE code = %s", (code,)).fetchone()[0]


def upsert_assets(conn: psycopg.Connection, records: list[AssetRecord]) -> WriteStats:
    stats = WriteStats()
    site_ids: dict[str, int] = {}
    for record in records:
        if record.building not in site_ids:
            site_ids[record.building] = ensure_site(conn, record.building)
        params = record.model_dump()
        params["site_id"] = site_ids[record.building]
        stats.count(conn.execute(_UPSERT_ASSET, params))
    return stats


def apply_site_config(conn: psycopg.Connection, config: ParsedSiteConfig) -> WriteStats:
    stats = WriteStats()
    site = config.site
    conn.execute(
        """
        INSERT INTO sites (code, name, address) VALUES (%s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, address = EXCLUDED.address
        WHERE (sites.name, sites.address) IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.address)
        """,
        (site.code, site.name, site.address),
    )
    site_id = conn.execute("SELECT id FROM sites WHERE code = %s", (site.code,)).fetchone()[0]

    for zone in config.zones:
        stats.count(
            conn.execute(
                """
                INSERT INTO zones (site_id, code, name, floor) VALUES (%s, %s, %s, %s)
                ON CONFLICT (site_id, code) DO UPDATE SET name = EXCLUDED.name, floor = EXCLUDED.floor
                WHERE (zones.name, zones.floor) IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.floor)
                RETURNING (xmax = 0)
                """,
                (site_id, zone.code, zone.name, zone.floor),
            )
        )

    for index, device in enumerate(config.devices):
        zone_row = conn.execute(
            "SELECT id FROM zones WHERE site_id = %s AND code = %s", (site_id, device.zone)
        ).fetchone()
        asset_row = conn.execute(
            "SELECT id, site_id FROM assets WHERE code = %s", (device.asset_code,)
        ).fetchone()
        problem = None
        if zone_row is None:
            problem = f"zone {device.zone} does not exist in site {site.code}"
        elif asset_row is None:
            problem = f"asset {device.asset_code} is not in the registry; import the asset list first"
        elif asset_row[1] != site_id:
            problem = f"asset {device.asset_code} belongs to another site"
        if problem:
            stats.rejected.append(
                RejectedItem(f"devices[{index}]", device.model_dump(), f"{device.device_id}: {problem}")
            )
            continue
        conn.execute(
            "UPDATE assets SET zone_id = %s WHERE id = %s AND zone_id IS DISTINCT FROM %s",
            (zone_row[0], asset_row[0], zone_row[0]),
        )
        stats.count(
            conn.execute(
                """
                INSERT INTO devices (device_id, vendor, asset_id, gateway_id) VALUES (%s, %s, %s, %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    vendor = EXCLUDED.vendor, asset_id = EXCLUDED.asset_id,
                    gateway_id = EXCLUDED.gateway_id
                WHERE (devices.vendor, devices.asset_id, devices.gateway_id)
                      IS DISTINCT FROM (EXCLUDED.vendor, EXCLUDED.asset_id, EXCLUDED.gateway_id)
                RETURNING (xmax = 0)
                """,
                (device.device_id, device.vendor, asset_row[0], device.gateway),
            )
        )
    return stats


def finished_run(conn: psycopg.Connection, sha256: str) -> tuple[int, str] | None:
    """A completed run of the same content, if any. Failed runs may be retried."""
    return conn.execute(
        "SELECT id, status FROM import_runs WHERE sha256 = %s AND status <> 'failed'", (sha256,)
    ).fetchone()


def record_run(
    conn: psycopg.Connection,
    *,
    file_name: str,
    sha256: str,
    fmt: str,
    rows_total: int,
    rows_ok: int,
    rows_rejected: int,
    status: str,
    started_at,
    error: str | None = None,
) -> int:
    return conn.execute(
        """
        INSERT INTO import_runs (file_name, sha256, format, rows_total, rows_ok, rows_rejected,
                                 status, started_at, finished_at, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
        ON CONFLICT (sha256) DO UPDATE SET
            file_name = EXCLUDED.file_name, rows_total = EXCLUDED.rows_total,
            rows_ok = EXCLUDED.rows_ok, rows_rejected = EXCLUDED.rows_rejected,
            status = EXCLUDED.status, started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at, error = EXCLUDED.error
        WHERE import_runs.status = 'failed'
        RETURNING id
        """,
        (file_name, sha256, fmt, rows_total, rows_ok, rows_rejected, status, started_at, error),
    ).fetchone()[0]

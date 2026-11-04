"""Minimal SQL migration runner.

Migrations are numbered files (``001_initial.sql``) applied in order, each
in its own transaction, and recorded in ``schema_migrations`` with a
checksum. Editing an applied file is an error: add a new migration
instead. An advisory lock lets several replicas start at the same time.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from vigiles_common.logging import get_logger

_FILE = re.compile(r"^(\d{3,})_([a-z0-9_]+)\.sql$")
_LOCK_ID = 727_001  # arbitrary, constant per database

log = get_logger(__name__)


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def discover(directory: Path) -> list[Migration]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILE.match(path.name)
        if not match:
            raise MigrationError(f"unexpected file name {path.name!r}, expected NNN_name.sql")
        migrations.append(Migration(match[1], match[2], path.read_text(encoding="utf-8")))
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration versions in {directory}")
    return migrations


def apply_migrations(database_url: str, directory: Path) -> list[str]:
    """Apply pending migrations and return the versions applied by this call."""
    migrations = discover(directory)
    applied_now: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    name text NOT NULL,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            done = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
            for migration in migrations:
                if migration.version in done:
                    if done[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.version}_{migration.name} changed after "
                            "it was applied; add a new migration instead"
                        )
                    continue
                with conn.transaction():
                    conn.execute(migration.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) "
                        "VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                log.info("migration_applied", version=migration.version, name=migration.name)
                applied_now.append(migration.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied_now

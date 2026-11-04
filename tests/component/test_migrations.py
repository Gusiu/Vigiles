from pathlib import Path

import psycopg
import pytest

from harness.db import Database
from vigiles_common.migrations import MigrationError, apply_migrations


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_applies_pending_migrations_once(empty_db: Database, tmp_path: Path) -> None:
    _write(tmp_path, "001_first.sql", "CREATE TABLE a (id int);")
    _write(tmp_path, "002_second.sql", "CREATE TABLE b (id int);")

    assert apply_migrations(empty_db.owner_url, tmp_path) == ["001", "002"]
    assert apply_migrations(empty_db.owner_url, tmp_path) == []

    _write(tmp_path, "003_third.sql", "CREATE TABLE c (id int);")
    assert apply_migrations(empty_db.owner_url, tmp_path) == ["003"]


def test_failed_migration_is_rolled_back(empty_db: Database, tmp_path: Path) -> None:
    _write(tmp_path, "001_ok.sql", "CREATE TABLE a (id int);")
    _write(tmp_path, "002_broken.sql", "CREATE TABLE b (id int); SELECT no_such_function();")

    with pytest.raises(psycopg.errors.UndefinedFunction):
        apply_migrations(empty_db.owner_url, tmp_path)

    with psycopg.connect(empty_db.owner_url) as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        table_b = conn.execute("SELECT to_regclass('b')").fetchone()[0]
    assert versions == ["001"]
    assert table_b is None


def test_changed_applied_migration_is_rejected(empty_db: Database, tmp_path: Path) -> None:
    _write(tmp_path, "001_first.sql", "CREATE TABLE a (id int);")
    apply_migrations(empty_db.owner_url, tmp_path)
    _write(tmp_path, "001_first.sql", "CREATE TABLE a (id bigint);")

    with pytest.raises(MigrationError, match="changed after it was applied"):
        apply_migrations(empty_db.owner_url, tmp_path)


def test_bad_file_name_is_rejected(empty_db: Database, tmp_path: Path) -> None:
    _write(tmp_path, "first.sql", "SELECT 1;")

    with pytest.raises(MigrationError, match="NNN_name.sql"):
        apply_migrations(empty_db.owner_url, tmp_path)

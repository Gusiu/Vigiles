from collections.abc import Iterator

import pytest

from harness.db import Database, DatabaseSettings, throwaway_database


@pytest.fixture(scope="session")
def db_settings() -> DatabaseSettings:
    return DatabaseSettings()


@pytest.fixture
def empty_db(db_settings: DatabaseSettings) -> Iterator[Database]:
    with throwaway_database(db_settings) as db:
        yield db


@pytest.fixture
def registry_db(empty_db: Database) -> Database:
    from vigiles_common.migrations import apply_migrations
    from vigiles_registry.cli import MIGRATIONS_DIR

    apply_migrations(empty_db.owner_url, MIGRATIONS_DIR)
    return empty_db

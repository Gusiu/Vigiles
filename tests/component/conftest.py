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

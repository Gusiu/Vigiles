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


from harness.registry import registry_settings  # noqa: E402


@pytest.fixture
def seeded_db(registry_db: Database, tmp_path) -> Database:
    """Registry database with the sample asset list and site configuration."""
    import shutil
    from datetime import date
    from pathlib import Path

    from vigiles_importer.pipeline import Importer

    samples = Path(__file__).resolve().parents[2] / "data" / "samples"
    importer = Importer(registry_db.importer_url, tmp_path / "seed", settle_s=0, today=lambda: date(2026, 11, 4))
    for name in ("zestawienie_B1.csv", "site-config.json"):
        shutil.copy(samples / name, importer.inbox / name)
    importer.process_inbox()
    return registry_db


@pytest.fixture
def registry(seeded_db: Database):
    from fastapi.testclient import TestClient

    from vigiles_registry.app import create_app

    with TestClient(create_app(registry_settings(seeded_db.owner_url))) as client:
        yield client


@pytest.fixture
def field_app(registry, tmp_path):
    """Field app wired to the in-process registry (no background threads)."""
    from fastapi.testclient import TestClient

    from harness.registry import SERVICE_TOKENS
    from vigiles_field_app.app import create_app
    from vigiles_field_app.settings import FieldAppSettings

    settings = FieldAppSettings(
        field_app_db_path=tmp_path / "field.sqlite", auth_mode="static", service_tokens=SERVICE_TOKENS,
        background_workers=False, session_secret="test", retry_base_s=0.01, retry_cap_s=0.02,
    )
    with TestClient(create_app(settings, registry_http=registry)) as client:
        yield client


@pytest.fixture(scope="session")
def broker(tmp_path_factory):
    from harness.broker import mosquitto

    with mosquitto(tmp_path_factory.mktemp("mosquitto") / "state") as running:
        yield running


@pytest.fixture
def fresh_broker(tmp_path):
    """A broker of its own, so durable sessions never leak between tests."""
    from harness.broker import mosquitto

    with mosquitto(tmp_path / "mqtt-state") as running:
        yield running


@pytest.fixture
def hub_db(db_settings: DatabaseSettings):
    """Throwaway hub database owned by hub_app, migrated; yields its URL."""
    from harness.db import with_database
    from vigiles_common.migrations import apply_migrations
    from vigiles_hub.app import MIGRATIONS

    with throwaway_database(db_settings, owner="hub_app") as db:
        url = with_database(db_settings.hub_database_url, db.name)
        apply_migrations(url, MIGRATIONS)
        yield url

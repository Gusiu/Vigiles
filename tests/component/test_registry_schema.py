import psycopg
import pytest

from harness.db import Database

UPSERT = """
INSERT INTO assets (code, asset_type, room)
VALUES (%(code)s, 'przejscie_instalacyjne', %(room)s)
ON CONFLICT (code) DO UPDATE SET room = EXCLUDED.room
WHERE assets.room IS DISTINCT FROM EXCLUDED.room
"""


def _asset(conn: psycopg.Connection, code: str) -> tuple:
    return conn.execute(
        "SELECT rev, room, origin, condition FROM assets WHERE code = %s", (code,)
    ).fetchone()


def test_rev_bumps_only_on_real_change(registry_db: Database) -> None:
    with psycopg.connect(registry_db.importer_url) as conn:
        conn.execute(UPSERT, {"code": "PI-1", "room": "2.14"})
        assert _asset(conn, "PI-1")[:2] == (1, "2.14")

        conn.execute(UPSERT, {"code": "PI-1", "room": "2.14"})
        assert _asset(conn, "PI-1")[:2] == (1, "2.14")

        conn.execute(UPSERT, {"code": "PI-1", "room": "2.14A"})
        assert _asset(conn, "PI-1")[:2] == (2, "2.14A")


def test_origin_comes_from_transaction_setting(registry_db: Database) -> None:
    with psycopg.connect(registry_db.importer_url) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL vigiles.origin = 'importer'")
            conn.execute(UPSERT, {"code": "PI-2", "room": "1.01"})
        assert _asset(conn, "PI-2")[2] == "importer"
        assert _asset(conn, "PI-2")[3] == "sprawny"


@pytest.mark.parametrize(
    ("column", "value"),
    [("fire_rating", "EI 75"), ("condition", "zepsuty"), ("asset_type", "gasnica"), ("diameter_mm", 0)],
)
def test_check_constraints(registry_db: Database, column: str, value: object) -> None:
    with psycopg.connect(registry_db.importer_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"INSERT INTO assets (code, asset_type, {column}) VALUES ('X-1', 'klapa_ppoz', %s)"
                if column != "asset_type"
                else "INSERT INTO assets (code, asset_type) VALUES ('X-1', %s)",
                (value,),
            )


def test_importer_cannot_delete_or_change_schema(registry_db: Database) -> None:
    with psycopg.connect(registry_db.importer_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM assets")
    with psycopg.connect(registry_db.importer_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE intruder (id int)")


def test_migrate_command(empty_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    from vigiles_registry.cli import main

    monkeypatch.setenv("REGISTRY_DATABASE_URL", empty_db.owner_url)
    main(["migrate"])
    main(["migrate"])

    with psycopg.connect(empty_db.owner_url) as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)

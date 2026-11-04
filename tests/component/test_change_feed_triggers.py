import psycopg

from harness.db import Database


def test_every_real_change_is_recorded(registry_db: Database) -> None:
    with psycopg.connect(registry_db.importer_url) as conn:
        conn.execute("SET vigiles.origin = 'importer'")
        conn.execute("INSERT INTO assets (code, asset_type, room) VALUES ('PI-1', 'klapa_ppoz', '1')")
        conn.execute("UPDATE assets SET room = '2' WHERE code = 'PI-1'")
        # Importer has no direct access to history, but the trigger records it.
    with psycopg.connect(registry_db.owner_url) as conn:
        history = conn.execute(
            "SELECT rev, data->>'room', origin FROM entity_history WHERE entity = 'asset' ORDER BY rev"
        ).fetchall()
        feed = conn.execute("SELECT entity, rev, op, origin FROM change_log ORDER BY seq").fetchall()
    assert history == [(1, "1", "importer"), (2, "2", "importer")]
    assert feed == [("asset", 1, "insert", "importer"), ("asset", 2, "update", "importer")]


def test_work_orders_are_versioned_and_unique_while_open(registry_db: Database) -> None:
    with psycopg.connect(registry_db.owner_url) as conn:
        asset_id = conn.execute(
            "INSERT INTO assets (code, asset_type) VALUES ('KL-1', 'klapa_ppoz') RETURNING id"
        ).fetchone()[0]
        order = conn.execute(
            "INSERT INTO work_orders (asset_id, reason) VALUES (%s, 'czyszczenie') RETURNING id", (asset_id,)
        ).fetchone()[0]
        try:
            with conn.transaction():
                conn.execute("INSERT INTO work_orders (asset_id, reason) VALUES (%s, 'czyszczenie')", (asset_id,))
            raise AssertionError("duplicate open order accepted")
        except psycopg.errors.UniqueViolation:
            pass
        conn.execute("UPDATE work_orders SET status = 'zakonczone' WHERE id = %s", (order,))
        conn.execute("INSERT INTO work_orders (asset_id, reason) VALUES (%s, 'czyszczenie')", (asset_id,))
        rev = conn.execute("SELECT rev FROM work_orders WHERE id = %s", (order,)).fetchone()[0]
        entities = conn.execute("SELECT entity FROM change_log ORDER BY seq").fetchall()
    assert rev == 2
    assert entities == [("asset",), ("work_order",), ("work_order",), ("work_order",)]

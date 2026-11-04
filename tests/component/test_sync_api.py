import uuid

import psycopg
from fastapi.testclient import TestClient

from harness.registry import FIELD, HUB
from harness.db import Database


def changes(client: TestClient, since: int = 0, limit: int = 500) -> dict:
    response = client.get(f"/api/v1/sync/changes?since={since}&limit={limit}", headers=FIELD)
    assert response.status_code == 200, response.text
    return response.json()


def latest(feed: dict, entity: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for change in feed["changes"]:
        if change["entity"] == entity:
            result[change["data"].get("code", change["id"])] = change
    return result


def push(client: TestClient, *items: dict) -> list[dict]:
    response = client.post("/api/v1/sync/push", json={"changes": list(items)}, headers=FIELD)
    assert response.status_code == 200, response.text
    return response.json()["results"]


def change(entity: str, entity_id: str, base_rev: int | None, **patch) -> dict:
    return {"change_id": str(uuid.uuid4()), "entity": entity, "id": entity_id, "base_rev": base_rev, "patch": patch}


def office(db: Database, sql: str, params: tuple) -> None:
    with psycopg.connect(db.owner_url) as conn:
        conn.execute("SELECT set_config('vigiles.origin', 'anna.kierownik', false)")
        conn.execute(sql, params)


def test_feed_contains_imported_assets_and_pages(registry: TestClient) -> None:
    first = changes(registry, limit=5)
    assert len(first["changes"]) == 5 and first["has_more"]
    rest = changes(registry, since=first["next_since"])
    assert not rest["has_more"]
    codes = set(latest(first, "asset")) | set(latest(rest, "asset"))
    assert "PI-B1-2-014" in codes and len(codes) == 11
    assert changes(registry, since=rest["next_since"])["changes"] == []


def test_plan_example_merge_then_conflict(registry: TestClient, seeded_db: Database) -> None:
    asset = latest(changes(registry), "asset")["PI-B1-2-014"]
    asset_id, rev = asset["id"], asset["rev"]

    office(seeded_db, "UPDATE assets SET room = '2.14A' WHERE id = %s", (asset_id,))
    [merged] = push(registry, change("asset", asset_id, rev, condition="do_naprawy", notes="pęknięta masa"))
    assert merged["status"] == "merged"
    assert merged["server_fields"] == ["room"]
    assert merged["new_rev"] == rev + 2

    office(seeded_db, "UPDATE assets SET notes = 'do weryfikacji' WHERE id = %s", (asset_id,))
    [conflict] = push(registry, change("asset", asset_id, merged["new_rev"], notes="masa wymieniona"))
    assert conflict["status"] == "conflict"
    assert conflict["new_rev"] == merged["new_rev"] + 1  # unchanged by the conflicting push

    [open_conflict] = _conflicts(seeded_db)
    assert open_conflict["server_value"] == "do weryfikacji"
    assert open_conflict["client_value"] == "masa wymieniona"
    assert open_conflict["base_value"] == "pęknięta masa"


def _conflicts(db: Database) -> list[dict]:
    with psycopg.connect(db.owner_url, row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute("SELECT * FROM conflicts WHERE status = 'open'").fetchall()


def test_retried_change_is_applied_once(registry: TestClient, seeded_db: Database) -> None:
    asset = latest(changes(registry), "asset")["CZ-B1-3-001"]
    item = change("asset", asset["id"], asset["rev"], condition="niesprawny")

    first = push(registry, item)
    again = push(registry, item)

    assert first == again
    assert first[0]["status"] == "applied"
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT rev FROM assets WHERE id = %s", (asset["id"],)).fetchone() == (asset["rev"] + 1,)


def test_own_change_comes_back_with_known_rev(registry: TestClient) -> None:
    feed = changes(registry)
    asset = latest(feed, "asset")["CZ-B1-3-002"]
    [result] = push(registry, change("asset", asset["id"], asset["rev"], notes="sprawdzona"))

    echo = changes(registry, since=feed["next_since"])["changes"]
    assert [(c["id"], c["rev"]) for c in echo] == [(asset["id"], result["new_rev"])]


def test_invalid_changes_are_rejected_individually(registry: TestClient) -> None:
    asset = latest(changes(registry), "asset")["KL-B1-3-001"]
    results = push(
        registry,
        change("asset", asset["id"], asset["rev"], rev=99),
        change("asset", asset["id"], 999, condition="do_naprawy"),
        change("asset", asset["id"], asset["rev"], condition="zepsuty"),
        change("asset", str(uuid.uuid4()), 1, condition="do_naprawy"),
        change("asset", asset["id"], asset["rev"], condition="do_naprawy"),
    )
    assert [r["status"] for r in results] == ["rejected", "rejected", "rejected", "rejected", "applied"]
    assert results[1]["problem"]["status"] == 409
    assert results[3]["problem"]["status"] == 404


def test_inspection_and_work_order_completion(registry: TestClient, seeded_db: Database) -> None:
    created = registry.post(
        "/api/v1/work-orders",
        json={"asset_code": "PI-B1-2-014", "reason": "przeglad_okresowy", "assigned_to": "jan.technik"},
        headers={**HUB, "Idempotency-Key": "k-1"},
    ).json()
    asset = latest(changes(registry), "asset")["PI-B1-2-014"]
    inspection_id = str(uuid.uuid4())
    results = push(
        registry,
        change("inspection", inspection_id, None, work_order_id=created["id"], asset_id=asset["id"],
               technician="jan.technik", performed_at="2026-11-04T10:00:00Z", result="do_naprawy",
               findings="pęknięta masa"),
        change("asset", asset["id"], asset["rev"], condition="do_naprawy", last_inspected_at="2026-11-04T10:00:00Z"),
        change("work_order", created["id"], created["rev"], status="zakonczone"),
    )
    assert [r["status"] for r in results] == ["applied", "applied", "applied"]
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT status FROM work_orders WHERE id = %s", (created["id"],)).fetchone() == ("zakonczone",)
        assert conn.execute("SELECT result FROM inspections WHERE id = %s", (inspection_id,)).fetchone() == ("do_naprawy",)

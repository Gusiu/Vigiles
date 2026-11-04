import json

import httpx
import psycopg
from fastapi.testclient import TestClient

from harness.db import Database
from harness.registry import FIELD, HUB, REGISTRY
from vigiles_field_app import actions


def engine(field_app: TestClient):
    return field_app.app.state.engine


def local(field_app: TestClient, sql: str, params: tuple = ()) -> list[dict]:
    with field_app.app.state.db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def order(registry: TestClient, code: str = "PI-B1-2-014", key: str = "k1") -> dict:
    response = registry.post(
        "/api/v1/work-orders", headers={**HUB, "Idempotency-Key": key},
        json={"asset_code": code, "reason": "przeglad_okresowy", "assigned_to": "jan.technik"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def office_update(db: Database, sql: str, params: tuple) -> None:
    with psycopg.connect(db.owner_url) as conn:
        conn.execute("SELECT set_config('vigiles.origin', 'anna.kierownik', false)")
        conn.execute(sql, params)


def test_pull_brings_assets_and_tasks_translated(registry: TestClient, field_app: TestClient) -> None:
    created = order(registry)
    engine(field_app).sync_once()

    [asset] = local(field_app, "SELECT * FROM assets WHERE code = 'PI-B1-2-014'")
    assert asset["condition"] == "OK" and asset["label"] == "B1, floor 2, room 2.14, ściana"
    [task] = local(field_app, "SELECT * FROM tasks")
    assert task["id"] == created["id"]
    assert task["status"] == "ASSIGNED" and task["title"] == "Periodic inspection: PI-B1-2-014"
    assert engine(field_app).sync_once() == {"pushed": 0, "pulled": 0}


def test_s2_inspection_result_reaches_registry(registry: TestClient, field_app: TestClient, seeded_db: Database) -> None:
    created = order(registry)
    engine(field_app).sync_once()
    db = field_app.app.state.db

    actions.start_task(db, created["id"])
    actions.submit_inspection(db, created["id"], "NEEDS_REPAIR", "pęknięta masa", "jan.technik")
    result = engine(field_app).sync_once()

    assert result["pushed"] == 3
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT status FROM work_orders WHERE id = %s", (created["id"],)).fetchone() == ("zakonczone",)
        assert conn.execute("SELECT condition, last_inspected_at IS NOT NULL FROM assets WHERE code = 'PI-B1-2-014'").fetchone() == ("do_naprawy", True)
        assert conn.execute("SELECT result, findings, technician FROM inspections").fetchall() == [("do_naprawy", "pęknięta masa", "jan.technik")]
    # Own changes come back in the feed with revisions the app already knows.
    assert result["pulled"] == 0
    assert local(field_app, "SELECT status FROM tasks") == [{"status": "DONE"}]
    assert local(field_app, "SELECT DISTINCT status FROM pending_changes") == [{"status": "sent"}]


def test_s3_offline_conflict_and_resolution(registry: TestClient, field_app: TestClient, seeded_db: Database) -> None:
    eng = engine(field_app)
    eng.sync_once()
    [asset] = local(field_app, "SELECT id FROM assets WHERE code = 'PI-B1-2-014'")

    eng.set_offline(True)
    actions.edit_notes(field_app.app.state.db, asset["id"], "masa wymieniona")
    office_update(seeded_db, "UPDATE assets SET notes = 'do weryfikacji' WHERE id = %s", (asset["id"],))
    assert eng.sync_once() == {"skipped": "offline"}

    eng.set_offline(False)
    eng.sync_once()
    assert eng.status()["conflicts"] == 1
    # The local edit is kept and marked while the conflict is open.
    assert local(field_app, "SELECT notes FROM assets WHERE id = ?", (asset["id"],)) == [{"notes": "masa wymieniona"}]

    [conflict] = _open_conflicts(seeded_db)
    with registry.app.state.db.transaction(origin="anna.kierownik") as conn:
        from vigiles_registry.conflicts import resolve_conflict

        resolve_conflict(conn, str(conflict[0]), "custom", "masa wymieniona, do weryfikacji", "anna.kierownik")
    eng.sync_once()

    assert eng.status()["conflicts"] == 0
    assert local(field_app, "SELECT notes FROM assets WHERE id = ?", (asset["id"],)) == [{"notes": "masa wymieniona, do weryfikacji"}]


def _open_conflicts(db: Database) -> list[tuple]:
    with psycopg.connect(db.owner_url) as conn:
        return conn.execute("SELECT id FROM conflicts WHERE status = 'open'").fetchall()


def test_disjoint_changes_merge_without_conflict(registry: TestClient, field_app: TestClient, seeded_db: Database) -> None:
    eng = engine(field_app)
    eng.sync_once()
    [asset] = local(field_app, "SELECT id FROM assets WHERE code = 'PI-B1-2-014'")

    actions.edit_notes(field_app.app.state.db, asset["id"], "sprawdzone")
    office_update(seeded_db, "UPDATE assets SET room = '2.14A' WHERE id = %s", (asset["id"],))
    eng.sync_once()
    eng.sync_once()

    assert eng.status()["conflicts"] == 0
    [row] = local(field_app, "SELECT notes, label FROM assets WHERE id = ?", (asset["id"],))
    assert row == {"notes": "sprawdzone", "label": "B1, floor 2, room 2.14A, ściana"}
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT notes, room FROM assets WHERE id = %s", (asset["id"],)).fetchone() == ("sprawdzone", "2.14A")


def test_consecutive_edits_are_folded(field_app: TestClient) -> None:
    eng = engine(field_app)
    eng.sync_once()
    [asset] = local(field_app, "SELECT id FROM assets WHERE code = 'PI-B1-2-015'")
    db = field_app.app.state.db

    actions.edit_notes(db, asset["id"], "one")
    actions.edit_notes(db, asset["id"], "two")

    [change] = local(field_app, "SELECT patch FROM pending_changes")
    assert json.loads(change["patch"]) == {"notes": "two"}


def test_registry_down_keeps_changes_pending_then_delivers(field_app: TestClient, registry: TestClient, seeded_db: Database) -> None:
    eng = engine(field_app)
    eng.sync_once()
    [asset] = local(field_app, "SELECT id FROM assets WHERE code = 'CZ-B1-3-001'")
    actions.edit_notes(field_app.app.state.db, asset["id"], "czujka zakurzona")

    working = eng.client.http
    eng.client.http = httpx.Client(base_url="http://127.0.0.1:1", timeout=0.5)
    eng.sync_once()
    [pending] = local(field_app, "SELECT status, attempts, last_error FROM pending_changes")
    assert (pending["status"], pending["attempts"]) == ("pending", 1)
    assert pending["last_error"]

    eng.client.http = working
    from harness.eventually import eventually

    def delivered() -> None:
        eng.sync_once()
        assert local(field_app, "SELECT status FROM pending_changes") == [{"status": "sent"}]

    eventually(delivered, timeout=5, interval=0.05)
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT notes FROM assets WHERE id = %s", (asset["id"],)).fetchone() == ("czujka zakurzona",)


def test_webhook_requires_registry_token_and_wakes_engine(field_app: TestClient) -> None:
    eng = engine(field_app)
    eng.wake.clear()

    assert field_app.post("/api/v1/webhooks/registry", json={"latest_seq": 3}).status_code == 401
    assert field_app.post("/api/v1/webhooks/registry", json={"latest_seq": 3}, headers=FIELD).status_code == 403
    assert field_app.post("/api/v1/webhooks/registry", json={"latest_seq": "x"}, headers=REGISTRY).status_code == 422
    assert field_app.post("/api/v1/webhooks/registry", json={"latest_seq": 3}, headers=REGISTRY).status_code == 202
    assert eng.wake.is_set()


def test_field_ui_flow(registry: TestClient, field_app: TestClient) -> None:
    created = order(registry)
    engine(field_app).sync_once()
    field_app.post("/login", data={"user": "jan.technik", "next": "/ui/tasks"})

    assert "Periodic inspection: PI-B1-2-014" in field_app.get("/ui/tasks").text
    assert field_app.post(f"/ui/tasks/{created['id']}/start").status_code == 200
    field_app.post(f"/ui/tasks/{created['id']}/inspection", data={"result": "OK", "findings": "bez uwag"})
    assert "pending: <strong>3</strong>" in field_app.get("/ui/sync").text
    assert field_app.get("/ui/assets").status_code == 200

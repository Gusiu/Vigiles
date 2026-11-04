import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from harness.registry import FIELD, HUB


def _asset(client: TestClient, code: str) -> dict:
    feed = client.get("/api/v1/sync/changes?since=0", headers=FIELD).json()["changes"]
    return [c for c in feed if c["entity"] == "asset" and c["data"]["code"] == code][-1]


def _conflict(client: TestClient, db: Database) -> tuple[dict, str]:
    asset = _asset(client, "PI-B1-2-014")
    with psycopg.connect(db.owner_url) as conn:
        conn.execute("UPDATE assets SET notes = 'biuro' WHERE id = %s", (asset["id"],))
    result = client.post("/api/v1/sync/push", headers=FIELD, json={"changes": [{
        "change_id": str(uuid.uuid4()), "entity": "asset", "id": asset["id"], "base_rev": asset["rev"],
        "patch": {"notes": "teren"}}]}).json()["results"][0]
    assert result["status"] == "conflict"
    return asset, result["conflict_ids"][0]


@pytest.mark.parametrize(("choice", "value", "expected"), [("server", None, "biuro"), ("client", None, "teren"), ("custom", "oboje", "oboje")])
def test_resolution_writes_value_and_reaches_feed(
    registry: TestClient, seeded_db: Database, choice: str, value: str | None, expected: str
) -> None:
    asset, conflict_id = _conflict(registry, seeded_db)
    since = registry.get("/api/v1/sync/changes?since=0&limit=1000", headers=FIELD).json()["next_since"]
    from vigiles_registry import conflicts

    with registry.app.state.db.transaction(origin="anna.kierownik") as conn:
        resolved = conflicts.resolve_conflict(conn, conflict_id, choice, value, "anna.kierownik")

    assert resolved["status"] == "resolved"
    feed = registry.get(f"/api/v1/sync/changes?since={since}", headers=FIELD).json()["changes"]
    entities = {c["entity"]: c for c in feed}
    assert entities["conflict"]["data"]["status"] == "resolved"
    if expected != "biuro":
        assert entities["asset"]["data"]["notes"] == expected
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT notes FROM assets WHERE id = %s", (asset["id"],)).fetchone() == (expected,)


def test_conflicts_are_listed_and_resolved_once(registry: TestClient, seeded_db: Database) -> None:
    _, conflict_id = _conflict(registry, seeded_db)
    from vigiles_registry import conflicts
    from vigiles_common.problems import ProblemError

    with registry.app.state.db.transaction() as conn:
        assert [str(c["id"]) for c in conflicts.list_conflicts(conn)] == [conflict_id]
        conflicts.resolve_conflict(conn, conflict_id, "server", None, "anna.kierownik")
    with registry.app.state.db.transaction() as conn:
        with pytest.raises(ProblemError) as info:
            conflicts.resolve_conflict(conn, conflict_id, "client", None, "anna.kierownik")
    assert info.value.status == 409


def test_hub_cannot_resolve_conflicts(registry: TestClient, seeded_db: Database) -> None:
    _, conflict_id = _conflict(registry, seeded_db)
    response = registry.post(f"/api/v1/conflicts/{conflict_id}/resolve", json={"choice": "server"}, headers=HUB)
    assert response.status_code == 403

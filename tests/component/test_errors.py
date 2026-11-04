import psycopg
from fastapi.testclient import TestClient

from harness.db import Database
from harness.registry import HUB, registry_settings
from vigiles_registry.app import create_app


def test_invalid_body_is_422_problem(registry: TestClient) -> None:
    response = registry.post(
        "/api/v1/work-orders", json={"asset_code": "PI-B1-2-014", "reason": "na_oko"},
        headers={**HUB, "Idempotency-Key": "k"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"][0]["loc"] == ["body", "reason"]


def test_unknown_asset_is_422(registry: TestClient) -> None:
    response = registry.post(
        "/api/v1/work-orders", json={"asset_code": "NOPE", "reason": "czyszczenie"},
        headers={**HUB, "Idempotency-Key": "k"},
    )
    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_missing_idempotency_key_is_400(registry: TestClient) -> None:
    response = registry.post("/api/v1/work-orders", json={"asset_code": "PI-B1-2-014", "reason": "czyszczenie"}, headers=HUB)
    assert response.status_code == 400


def test_idempotency_key_replays_first_response(registry: TestClient, seeded_db: Database) -> None:
    body = {"asset_code": "PI-B1-2-014", "reason": "czyszczenie"}
    first = registry.post("/api/v1/work-orders", json=body, headers={**HUB, "Idempotency-Key": "fault-1"})
    again = registry.post("/api/v1/work-orders", json=body, headers={**HUB, "Idempotency-Key": "fault-1"})
    other_key = registry.post("/api/v1/work-orders", json=body, headers={**HUB, "Idempotency-Key": "fault-2"})

    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json() == again.json()
    # A different key for the same open (asset, reason) returns the existing order.
    assert other_key.status_code == 200
    assert other_key.json()["id"] == first.json()["id"]
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT count(*) FROM work_orders").fetchone() == (1,)


def test_incident_is_idempotent(registry: TestClient) -> None:
    body = {"kind": "alarm_potwierdzony", "site": "B1", "zone": "Z3", "detectors": ["A-000123", "A-000124"],
            "first_at": "2026-11-04T10:15:00Z", "decided_at": "2026-11-04T10:15:20Z"}
    first = registry.post("/api/v1/incidents", json=body, headers={**HUB, "Idempotency-Key": "w1:confirmed"})
    again = registry.post("/api/v1/incidents", json=body, headers={**HUB, "Idempotency-Key": "w1:confirmed"})

    assert first.status_code == 201 and first.json() == again.json()
    # The correlation ID is stored even when the caller did not send one.
    assert first.json()["correlation_id"] == first.headers["x-correlation-id"]


def test_database_down_is_503_with_retry_after(empty_db: Database) -> None:
    url = psycopg.conninfo.make_conninfo(empty_db.owner_url, port=1)
    with TestClient(create_app(registry_settings(url))) as client:
        ready = client.get("/health/ready")
        api = client.get("/api/v1/devices", headers=HUB)

    assert ready.status_code == 503
    assert api.status_code == 503
    assert api.headers["retry-after"]
    assert api.json()["detail"] == "registry database unavailable"


def test_errors_are_logged_as_integration_events(registry: TestClient, seeded_db: Database) -> None:
    registry.post("/api/v1/work-orders", json={"asset_code": "PI-B1-2-014", "reason": "czyszczenie"},
                  headers={**HUB, "Idempotency-Key": "e", "X-Correlation-ID": "corr-42"})
    with psycopg.connect(seeded_db.owner_url) as conn:
        rows = conn.execute("SELECT event, outcome FROM integration_events WHERE correlation_id = 'corr-42'").fetchall()
    assert rows == [("work_order_created", "ok")]

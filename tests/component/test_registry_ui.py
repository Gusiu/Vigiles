import psycopg
from fastapi.testclient import TestClient

from harness.db import Database


def login(client: TestClient, user: str) -> None:
    assert client.post("/login", data={"user": user, "next": "/ui/assets"}, follow_redirects=False).status_code == 303


def test_ui_requires_login(registry: TestClient) -> None:
    response = registry.get("/ui/assets", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_manager_creates_work_order_and_sees_assets(registry: TestClient, seeded_db: Database) -> None:
    login(registry, "anna.kierownik")

    assert "PI-B1-2-014" in registry.get("/ui/assets").text
    page = registry.post("/ui/work-orders", data={"asset_code": "pi-b1-2-014", "reason": "przeglad_okresowy",
                                                  "assigned_to": "jan.technik"}).text
    assert "Work order created" in page
    with psycopg.connect(seeded_db.owner_url) as conn:
        assert conn.execute("SELECT status, assigned_to, origin FROM work_orders").fetchall() == [
            ("przydzielone", "jan.technik", "anna.kierownik")
        ]
    for path in ("/ui/conflicts", "/ui/incidents", "/ui/imports"):
        assert registry.get(path).status_code == 200


def test_technician_is_forbidden(registry: TestClient) -> None:
    login(registry, "jan.technik")

    assert registry.get("/ui/conflicts").status_code == 403
    assert registry.post("/ui/conflicts/00000000-0000-0000-0000-000000000000/resolve", data={"choice": "server"}).status_code == 403


def test_static_assets_are_served_locally(registry: TestClient) -> None:
    assert registry.get("/static/htmx-2.0.4.min.js").status_code == 200

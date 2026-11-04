import pytest
from fastapi.testclient import TestClient

from harness.registry import FIELD, HUB, REGISTRY

ENDPOINTS = [
    ("GET", "/api/v1/devices", "devices:read"),
    ("GET", "/api/v1/assets", "assets:read"),
    ("POST", "/api/v1/work-orders", "work-orders:create"),
    ("POST", "/api/v1/incidents", "incidents:create"),
    ("GET", "/api/v1/sync/changes", "sync:read"),
    ("POST", "/api/v1/sync/push", "sync:push"),
    ("GET", "/api/v1/conflicts", "conflicts:read"),
    ("POST", "/api/v1/conflicts/00000000-0000-0000-0000-000000000000/resolve", "conflicts:resolve"),
    ("GET", "/api/v1/imports", "imports:read"),
]


@pytest.mark.parametrize(("method", "path", "_"), ENDPOINTS)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Token t-field"}])
def test_request_without_valid_token_is_401(registry: TestClient, method: str, path: str, _: str, headers: dict) -> None:
    response = registry.request(method, path, headers=headers, json={})

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize(("method", "path", "permission"), ENDPOINTS)
@pytest.mark.parametrize(
    ("headers", "granted"),
    [
        (FIELD, {"sync:read", "sync:push"}),
        (HUB, {"devices:read", "work-orders:create", "incidents:create"}),
        (REGISTRY, set()),
    ],
)
def test_service_permissions(
    registry: TestClient, method: str, path: str, permission: str, headers: dict, granted: set
) -> None:
    response = registry.request(method, path, headers=headers, json={})

    if permission in granted:
        assert response.status_code not in (401, 403)
    else:
        assert response.status_code == 403


def test_health_is_public(registry: TestClient) -> None:
    assert registry.get("/health/live").status_code == 200
    assert registry.get("/health/ready").json()["checks"] == {"database": "ok"}

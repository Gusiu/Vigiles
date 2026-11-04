"""Role x endpoint -> 200/401/403 for all three APIs (PLAN.md 11.2), with
JWTs validated exactly as in AUTH_MODE=dev and oidc."""

import pytest

from harness import tokens
from vigiles_common.auth import ROLE_PERMISSIONS

ROLES = ["technik", "kierownik", "zarzadca", "admin"]
SERVICES = ["svc-hub", "svc-field-app", "svc-registry"]

REGISTRY = [
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
FIELD_APP = [
    ("POST", "/api/v1/webhooks/registry", "webhook:notify"),
    ("GET", "/api/v1/sync/status", "tasks:read"),
    ("POST", "/api/v1/sync/offline", "inspections:write"),
]
HUB = [
    ("GET", "/api/v1/dead-letters", "dlq:read"),
    ("POST", "/api/v1/dead-letters/1/replay", "dlq:replay"),
    ("GET", "/api/v1/outbox", "console:read"),
]


def _credentials(audience: str):
    for role in ROLES:
        yield role, tokens.user(f"user-{role}", [role], audience)
    for client in SERVICES:
        yield client, tokens.service(client)


def expected(name: str, permission: str, audience: str) -> int | None:
    """401 for a token meant for another API, 403 without the permission,
    None when access is granted (the endpoint then answers on its merits)."""
    from vigiles_dev_issuer import SERVICE_CLIENTS

    if name in SERVICE_CLIENTS and SERVICE_CLIENTS[name][1] != audience:
        return 401
    return None if permission in ROLE_PERMISSIONS[name] else 403


def _check(client, endpoints, audience) -> None:
    for name, headers in _credentials(audience):
        for method, path, permission in endpoints:
            status = client.request(method, path, headers=headers, json={}).status_code
            want = expected(name, permission, audience)
            if want is None:
                assert status not in (401, 403), (name, path, status)
            else:
                assert status == want, (name, path, status)
    for method, path, _ in endpoints:
        assert client.request(method, path, json={}).status_code == 401


def test_registry_matrix(registry) -> None:
    registry.app.state.verifier = tokens.verifier("registry-api")
    _check(registry, REGISTRY, "registry-api")


def test_field_app_matrix(field_app) -> None:
    field_app.app.state.verifier = tokens.verifier("field-app-api")
    _check(field_app, FIELD_APP, "field-app-api")


def test_hub_matrix(hub_api) -> None:
    hub_api.app.state.verifier = tokens.verifier("hub-api")
    _check(hub_api, HUB, "hub-api")


def test_token_for_another_api_is_rejected(registry) -> None:
    registry.app.state.verifier = tokens.verifier("registry-api")
    # A webhook token of the registry is meant for the field app only.
    assert registry.get("/api/v1/devices", headers=tokens.service("svc-registry")).status_code == 401
    assert registry.get("/api/v1/devices", headers=tokens.user("anna", ["admin"], "account")).status_code == 401


@pytest.fixture
def hub_api(hub_db):
    from fastapi.testclient import TestClient

    from harness.registry import SERVICE_TOKENS
    from vigiles_hub.app import create_app
    from vigiles_hub.settings import HubSettings

    settings = HubSettings(hub_database_url=hub_db, service_tokens=SERVICE_TOKENS, mqtt_hub_password="x",
                           background_workers=False, session_secret="test")
    with TestClient(create_app(settings)) as client:
        yield client

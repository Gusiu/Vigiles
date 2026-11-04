import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from vigiles_common.auth import (
    AuthError,
    Principal,
    StaticTokenVerifier,
    parse_service_tokens,
    permissions_for,
    require,
)
from vigiles_common.problems import install_problem_handlers


def test_parse_service_tokens() -> None:
    assert parse_service_tokens("svc-hub=a, svc-field-app=b,") == {"svc-hub": "a", "svc-field-app": "b"}
    with pytest.raises(ValueError):
        parse_service_tokens("svc-hub")


def test_static_verifier() -> None:
    verifier = StaticTokenVerifier({"svc-field-app": "secret"})

    principal = verifier.verify("secret")
    assert principal.subject == "svc-field-app"
    assert principal.can("sync:push") and not principal.can("conflicts:resolve")
    with pytest.raises(AuthError):
        verifier.verify("guess")


def test_role_mapping() -> None:
    assert permissions_for(["technik"]) == {"tasks:read", "inspections:write"}
    assert {"dlq:replay", "conflicts:resolve", "tasks:read", "assets:write"} <= permissions_for(["admin"])
    assert "assets:write" in permissions_for(["kierownik"])
    assert permissions_for(["unknown"]) == frozenset()


def _client() -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)
    app.state.verifier = StaticTokenVerifier({"svc-field-app": "t-field", "svc-hub": "t-hub"})

    @app.get("/push")
    def push(principal: Principal = Depends(require("sync:push"))) -> dict:
        return {"subject": principal.subject}

    return TestClient(app)


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Basic abc"}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Authorization": "Bearer t-hub"}, 403),
        ({"Authorization": "Bearer t-field"}, 200),
    ],
)
def test_require(headers: dict, status: int) -> None:
    response = _client().get("/push", headers=headers)

    assert response.status_code == status
    if status == 401:
        assert response.headers["www-authenticate"].startswith("Bearer")
        assert response.headers["content-type"] == "application/problem+json"

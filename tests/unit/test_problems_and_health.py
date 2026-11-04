import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from vigiles_common.correlation import HEADER, CorrelationIdMiddleware
from vigiles_common.health import health_router
from vigiles_common.problems import MEDIA_TYPE, ServiceUnavailable, install_problem_handlers


class Payload(BaseModel):
    count: int


def _app(db_ok: bool = True) -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    install_problem_handlers(app)

    def db_check() -> None:
        if not db_ok:
            raise ConnectionError("connection refused")

    app.include_router(health_router({"database": db_check}))

    @app.get("/missing")
    def missing() -> None:
        raise HTTPException(404, "asset not found")

    @app.post("/payload")
    def payload(body: Payload) -> dict:
        return body.model_dump()

    @app.get("/db-down")
    def db_down() -> None:
        raise ServiceUnavailable("database unavailable", retry_after_s=7)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_http_error_is_problem_json(client: TestClient) -> None:
    response = client.get("/missing", headers={HEADER: "c-1"})

    assert response.status_code == 404
    assert response.headers["content-type"] == MEDIA_TYPE
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "asset not found",
        "instance": "/missing",
        "correlation_id": "c-1",
    }


def test_validation_error_is_422_with_field_errors(client: TestClient) -> None:
    response = client.post("/payload", json={"count": "many"})

    assert response.status_code == 422
    assert response.headers["content-type"] == MEDIA_TYPE
    body = response.json()
    assert body["title"] == "Unprocessable Content"
    assert body["errors"][0]["loc"] == ["body", "count"]


def test_service_unavailable_sets_retry_after(client: TestClient) -> None:
    response = client.get("/db-down")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "7"
    assert response.json()["detail"] == "database unavailable"


def test_unexpected_error_hides_internals(client: TestClient) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    assert "secret" not in response.text


def test_live_and_ready_when_healthy(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_ready_fails_when_check_fails() -> None:
    client = TestClient(_app(db_ok=False))

    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.headers["retry-after"] == "5"
    assert ready.json()["checks"] == {"database": "failed"}

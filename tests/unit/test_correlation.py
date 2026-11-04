import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vigiles_common.correlation import HEADER, CorrelationIdMiddleware, current_correlation_id


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/echo")
    def echo() -> dict:
        return {"correlation_id": current_correlation_id()}

    return TestClient(app)


def test_incoming_id_is_kept_and_visible_to_handlers(client: TestClient) -> None:
    response = client.get("/echo", headers={HEADER: "order-42"})

    assert response.headers[HEADER] == "order-42"
    assert response.json() == {"correlation_id": "order-42"}


def test_missing_id_is_generated(client: TestClient) -> None:
    response = client.get("/echo")

    generated = response.headers[HEADER]
    assert uuid.UUID(generated)
    assert response.json() == {"correlation_id": generated}


@pytest.mark.parametrize("bad", ["", "x" * 129, "has space", "new\\nline"])
def test_unsafe_id_is_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/echo", headers={HEADER: bad})

    assert response.headers[HEADER] != bad
    assert uuid.UUID(response.headers[HEADER])


def test_id_does_not_leak_outside_request(client: TestClient) -> None:
    client.get("/echo", headers={HEADER: "order-42"})

    assert current_correlation_id() is None


def test_correlation_scope_for_background_work() -> None:
    from vigiles_common.correlation import correlation_scope

    with correlation_scope("job-7") as cid:
        assert cid == current_correlation_id() == "job-7"
    with correlation_scope() as generated:
        assert uuid.UUID(generated)
    assert current_correlation_id() is None

import random
import threading
from contextlib import contextmanager

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from harness.db import Database
from harness.eventually import eventually
from vigiles_common.outbox import OutboxRelay
from vigiles_common.retry import RetryPolicy
from vigiles_registry.workers import notify_changes

FAST = RetryPolicy(base_s=0.01, cap_s=0.05, max_attempts=4)


def _connect(db: Database):
    @contextmanager
    def connect():
        with psycopg.connect(db.owner_url) as conn:
            yield conn

    return connect


def _queue(db: Database, url: str, payload: dict | None = None) -> None:
    with psycopg.connect(db.owner_url) as conn:
        conn.execute(
            "INSERT INTO outbox (kind, target_url, payload, correlation_id) VALUES ('webhook', %s, %s, 'corr-1')",
            (url, Jsonb(payload or {"latest_seq": 1})),
        )


def _rows(db: Database) -> list[tuple]:
    with psycopg.connect(db.owner_url) as conn:
        return conn.execute("SELECT status, attempts, last_error IS NOT NULL FROM outbox ORDER BY id").fetchall()


def _relay(db: Database, timeout: float = 2.0, **kwargs) -> OutboxRelay:
    return OutboxRelay(_connect(db), httpx.Client(timeout=timeout), "test", token=lambda: "t-registry",
                       policy=FAST, rng=random.Random(1), **kwargs)


def test_sends_with_auth_idempotency_and_correlation(registry_db: Database, httpserver: HTTPServer) -> None:
    seen: list[Request] = []
    httpserver.expect_request("/hook", method="POST").respond_with_handler(
        lambda r: seen.append(r) or Response("ok")
    )
    _queue(registry_db, httpserver.url_for("/hook"))

    assert _relay(registry_db).run_once() == 1

    assert _rows(registry_db) == [("sent", 1, False)]
    [request] = seen
    assert request.headers["Authorization"] == "Bearer t-registry"
    assert request.headers["X-Correlation-ID"] == "corr-1"
    assert request.headers["Idempotency-Key"]
    assert request.json == {"latest_seq": 1}


def test_503_is_retried_until_success(registry_db: Database, httpserver: HTTPServer) -> None:
    responses = iter([Response("busy", status=503), Response("busy", status=503, headers={"Retry-After": "0"}), Response("ok")])
    httpserver.expect_request("/hook").respond_with_handler(lambda _: next(responses))
    _queue(registry_db, httpserver.url_for("/hook"))
    relay = _relay(registry_db)

    def delivered() -> None:
        relay.run_once()
        assert _rows(registry_db)[0][0] == "sent"

    eventually(delivered, timeout=5, interval=0.05)
    assert _rows(registry_db) == [("sent", 3, False)]


def test_timeout_is_retried(registry_db: Database, httpserver: HTTPServer) -> None:
    import time

    calls = 0

    def slow(_: Request) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            time.sleep(0.5)
        return Response("ok")

    httpserver.expect_request("/hook").respond_with_handler(slow)
    _queue(registry_db, httpserver.url_for("/hook"))
    relay = _relay(registry_db, timeout=0.2)

    relay.run_once()
    assert _rows(registry_db)[0][:2] == ("pending", 1)

    def delivered() -> None:
        relay.run_once()
        assert _rows(registry_db)[0][0] == "sent"

    eventually(delivered, timeout=5, interval=0.05)


def test_connection_refused_is_retried_then_dead(registry_db: Database) -> None:
    dead: list[str] = []
    _queue(registry_db, "http://127.0.0.1:1/hook")
    relay = _relay(registry_db, on_dead=lambda conn, row, error: dead.append(error))

    def exhausted() -> None:
        relay.run_once()
        assert _rows(registry_db)[0][0] == "dead"

    eventually(exhausted, timeout=5, interval=0.05)
    assert _rows(registry_db) == [("dead", 4, True)]
    assert "ConnectError" in dead[0]


@pytest.mark.parametrize("status", [400, 403, 404, 409, 422])
def test_permanent_errors_are_not_retried(registry_db: Database, httpserver: HTTPServer, status: int) -> None:
    httpserver.expect_request("/hook").respond_with_data("no", status=status)
    _queue(registry_db, httpserver.url_for("/hook"))

    _relay(registry_db).run_once()

    assert _rows(registry_db) == [("dead", 1, True)]


def test_two_relays_never_send_the_same_row(registry_db: Database, httpserver: HTTPServer) -> None:
    received: list[str] = []
    lock = threading.Lock()

    def handler(request: Request) -> Response:
        with lock:
            received.append(request.headers["Idempotency-Key"])
        return Response("ok")

    httpserver.expect_request("/hook").respond_with_handler(handler)
    for i in range(30):
        _queue(registry_db, httpserver.url_for("/hook"), {"latest_seq": i})
    relays = [_relay(registry_db), _relay(registry_db)]
    threads = [threading.Thread(target=lambda r=r: [r.run_once() for _ in range(5)]) for r in relays]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(received) == 30 == len(set(received))


def test_notifier_queues_one_doorbell_per_burst(registry_db: Database) -> None:
    with psycopg.connect(registry_db.owner_url) as conn:
        assert notify_changes(conn, "http://field/hook") is None
        conn.execute("INSERT INTO assets (code, asset_type) VALUES ('A-1', 'klapa_ppoz')")
        conn.commit()
        first = notify_changes(conn, "http://field/hook")
        conn.execute("INSERT INTO assets (code, asset_type) VALUES ('A-2', 'klapa_ppoz')")
        conn.commit()
        second = notify_changes(conn, "http://field/hook")
        assert notify_changes(conn, "http://field/hook") is None
        rows = conn.execute("SELECT payload FROM outbox WHERE status = 'pending'").fetchall()
    assert second > first
    assert rows == [({"latest_seq": second},)]

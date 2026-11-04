"""Failure scenarios C1 (with the hub), C5 and C6 (PLAN.md 10.3)."""

import os
import time

import httpx

from harness.eventually import eventually
from harness.stack import Stack

OUTAGE_S = float(os.environ.get("CHAOS_OUTAGE_S", "5"))


def _check(condition: bool) -> None:
    assert condition


def _outage(started: float) -> None:
    remaining = OUTAGE_S - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)  # the outage itself, not a wait for consistency


def _tasks_for_three_assets(stack: Stack) -> list[str]:
    manager = stack.ui("registry", "anna.kierownik")
    for code in ("PI-B1-2-014", "PI-B1-2-015", "PI-B1-2-016"):
        manager.post("/ui/work-orders", data={"asset_code": code, "reason": "przeglad_okresowy", "assigned_to": "jan.technik"})
    eventually(lambda: _check(stack.field_sql("SELECT count(*) FROM tasks") == [(3,)]), timeout=20)
    return [r[0] for r in stack.field_sql("SELECT id FROM tasks")]


def _warm_up_hubs(stack: Stack) -> None:
    """Traffic below the thresholds until both replicas processed messages,
    i.e. both hold the device catalogue and a token, as in normal operation."""

    def both_replicas_busy() -> None:
        stack.scenario("contamination", {"device_id": "A-000123", "value": 10})
        instances = {r[0] for r in stack.hub_sql("SELECT DISTINCT instance FROM processed_messages")}
        assert instances == {"hub-1", "hub-2"}

    eventually(both_replicas_busy, timeout=30, interval=0.3)


def test_c1_registry_down_with_device_faults_and_inspections(full_stack: Stack) -> None:
    order_ids = _tasks_for_three_assets(full_stack)
    _warm_up_hubs(full_stack)
    technician = full_stack.ui("field-app", "jan.technik")

    full_stack.nodes.stop("registry")
    started = time.monotonic()
    # Beta sends no message id: faults of one device within the same second
    # share a message_id (documented collision), so one fault per device here.
    full_stack.scenario("damper-fault", {"device_id": "BETA-7F3K2", "closing_time_s": 91})
    full_stack.scenario("contamination", {"device_id": "A-000123", "value": 88})
    full_stack.scenario("contamination", {"device_id": "A-000124", "value": 91})
    for order_id in order_ids:
        technician.post(f"/ui/tasks/{order_id}/inspection", data={"result": "OK", "findings": "bez uwag"})

    # Both outboxes grow while the registry is away.
    eventually(lambda: _check(full_stack.hub_sql("SELECT count(*) FROM outbox WHERE status = 'pending' AND attempts > 0")[0][0] == 3),
               timeout=30)
    assert full_stack.field_sql("SELECT count(*) FROM pending_changes WHERE status = 'pending'") == [(9,)]
    _outage(started)
    full_stack.nodes.start("registry")

    def delivered_once() -> None:
        assert full_stack.hub_sql("SELECT status, count(*) FROM outbox GROUP BY status") == [("sent", 3)]
        assert full_stack.field_sql("SELECT DISTINCT status FROM pending_changes") == [("sent",)]
        orders = dict(full_stack.registry_sql("SELECT reason, count(*) FROM work_orders GROUP BY reason"))
        assert orders == {"przeglad_okresowy": 3, "usterka_urzadzenia": 1, "czyszczenie": 2}
        assert full_stack.registry_sql("SELECT count(*) FROM inspections") == [(3,)]

    eventually(delivered_once, timeout=90)
    # Every hub call was answered once at the registry (Idempotency-Key).
    assert full_stack.registry_sql("SELECT count(*) FROM idempotency_keys WHERE client_id = 'svc-hub'") == [(3,)]


def test_c5_postgres_down(full_stack: Stack) -> None:
    order_ids = _tasks_for_three_assets(full_stack)
    technician = full_stack.ui("field-app", "jan.technik")

    full_stack.nodes.stop("postgres")
    try:
        started = time.monotonic()
        full_stack.scenario("damper-fault", {"device_id": "BETA-7F3K2", "closing_time_s": 97})
        # The registry reports the outage instead of failing obscurely.
        response = httpx.get(f"{full_stack.registry_url}/health/ready", timeout=10)
        assert response.status_code == 503 and response.headers["retry-after"]
        # The field app keeps working on its own database.
        assert technician.post(f"/ui/tasks/{order_ids[0]}/inspection", data={"result": "OK"}).status_code == 200
        assert httpx.get(f"{full_stack.field_app_url}/health/ready", timeout=10).status_code == 200
        _outage(started)
    finally:
        full_stack.nodes.start("postgres")

    def processed() -> None:
        assert full_stack.registry_sql(
            "SELECT count(*) FROM work_orders WHERE reason = 'usterka_urzadzenia'") == [(1,)]
        assert full_stack.registry_sql("SELECT count(*) FROM inspections") == [(1,)]

    eventually(processed, timeout=90)


def test_c6_identity_provider_down(full_stack: Stack) -> None:
    # Warm up: every service has made a call, so it holds a token and the keys.
    _tasks_for_three_assets(full_stack)
    _warm_up_hubs(full_stack)
    full_stack.nodes.stop("dev-issuer")
    try:
        full_stack.scenario("damper-fault", {"device_id": "BETA-7F3K2", "closing_time_s": 99})

        def order_reaches_field_app() -> None:
            assert full_stack.field_sql("SELECT count(*) FROM tasks WHERE reason = 'usterka_urzadzenia'") == [(1,)]

        eventually(order_reaches_field_app, timeout=30)
    finally:
        full_stack.nodes.start("dev-issuer")

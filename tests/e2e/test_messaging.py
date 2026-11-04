"""Scenarios S4-S7 (PLAN.md section 5) on the full local stack."""

import httpx

from harness.eventually import eventually
from harness.stack import Stack


def _check(condition: bool) -> None:
    assert condition


def test_s4_damper_fault_reaches_technician(full_stack: Stack) -> None:
    full_stack.scenario("damper-fault", {"device_id": "BETA-7F3K2", "closing_time_s": 95.2})

    def order_in_registry() -> None:
        rows = full_stack.registry_sql(
            "SELECT w.reason, w.source, w.correlation_id FROM work_orders w JOIN assets a ON a.id = w.asset_id "
            "WHERE a.code = 'KL-B1-3-002'")
        assert len(rows) == 1 and rows[0][:2] == ("usterka_urzadzenia", "svc-hub")

    eventually(order_in_registry, timeout=30)
    [(order_id, correlation_id)] = full_stack.registry_sql("SELECT id::text, correlation_id FROM work_orders")
    # S2 continues: the order reaches the field app.
    eventually(lambda: _check(full_stack.field_sql("SELECT reason FROM tasks WHERE id = ?", (order_id,)) == [("usterka_urzadzenia",)]),
               timeout=15)
    # One correlation id from the device message through hub to registry.
    eventually(lambda: _check({"faults", "outbox"} <= {
        r[0] for r in full_stack.hub_sql("SELECT stage FROM integration_events WHERE correlation_id = %s", (correlation_id,))}),
        timeout=10)


def test_s5_alarm_coincidence(full_stack: Stack) -> None:
    full_stack.scenario("alarm", {"zone": "Z3", "detectors": 1, "interval_s": 0})
    eventually(lambda: _check(full_stack.registry_sql("SELECT kind FROM incidents") == [("alarm_wstepny",)]), timeout=30)

    full_stack.scenario("alarm", {"zone": "Z3", "detectors": 2, "interval_s": 1})

    def confirmed() -> None:
        kinds = sorted(r[0] for r in full_stack.registry_sql("SELECT kind FROM incidents"))
        assert kinds == ["alarm_potwierdzony", "alarm_wstepny"]

    eventually(confirmed, timeout=30)
    [(detectors,)] = full_stack.registry_sql("SELECT detectors FROM incidents WHERE kind = 'alarm_potwierdzony'")
    assert sorted(detectors) == ["A-000123", "A-000124"]


def test_s6_contamination_without_duplicates(full_stack: Stack) -> None:
    full_stack.scenario("contamination", {"device_id": "A-000124", "value": 85})
    full_stack.scenario("contamination", {"device_id": "A-000124", "value": 90})
    full_stack.scenario("contamination", {"device_id": "A-000124", "value": 40})

    eventually(lambda: _check(full_stack.hub_sql("SELECT count(*) FROM processed_messages WHERE consumer = 'telemetry'") == [(3,)]),
               timeout=30)
    eventually(lambda: _check(full_stack.hub_sql("SELECT count(*) FROM outbox WHERE status = 'sent'") == [(2,)]), timeout=30)
    assert full_stack.registry_sql("SELECT reason, count(*) FROM work_orders GROUP BY reason") == [("czyszczenie", 1)]


def test_s7_unknown_device_dlq_import_and_replay(full_stack: Stack) -> None:
    full_stack.scenario("unknown-device")
    eventually(lambda: _check(full_stack.hub_sql("SELECT reason FROM dead_letters") == [("unknown_device",)]), timeout=30)

    full_stack.drop("site-config-v2.json")
    eventually(lambda: _check(full_stack.registry_sql("SELECT count(*) FROM devices") == [(4,)]), timeout=30)

    hub = httpx.Client(base_url="http://127.0.0.1:18003", follow_redirects=True, timeout=10)
    hub.post("/login", data={"user": "admin", "next": "/"})
    [(dead_id,)] = full_stack.hub_sql("SELECT id FROM dead_letters")
    response = hub.post(f"/api/v1/dead-letters/{dead_id}/replay")
    assert response.status_code == 200, response.text

    def cleaning_order() -> None:
        rows = full_stack.registry_sql(
            "SELECT w.reason FROM work_orders w JOIN assets a ON a.id = w.asset_id WHERE a.code = 'CZ-B1-3-003'")
        assert rows == [("czyszczenie",)]

    eventually(cleaning_order, timeout=30)

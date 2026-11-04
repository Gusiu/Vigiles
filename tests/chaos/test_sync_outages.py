"""Failure scenarios of the 4.0 sync (PLAN.md 10.3): C4 and C1 without the hub.

The outage is shortened from the 60 s of the plan; set CHAOS_OUTAGE_S to
change it. What matters is that work continues during the outage and
everything arrives exactly once afterwards.
"""

import os
import time

from harness.eventually import eventually
from harness.stack import Stack

OUTAGE_S = float(os.environ.get("CHAOS_OUTAGE_S", "5"))


def _assets_synced(stack: Stack) -> None:
    stack.drop("zestawienie_B1.csv")
    eventually(lambda: _check(stack.field_sql("SELECT count(*) FROM assets") == [(11,)]), timeout=30)


def test_c4_field_app_down_while_orders_are_created(stack: Stack) -> None:
    _assets_synced(stack)
    stack.nodes.stop("field-app")
    manager = stack.ui("registry", "anna.kierownik")
    for code in ("PI-B1-2-014", "PI-B1-2-015", "KL-B1-3-001"):
        manager.post("/ui/work-orders", data={"asset_code": code, "reason": "przeglad_okresowy", "assigned_to": "jan.technik"})

    def webhook_waiting() -> None:
        [(attempts,)] = stack.registry_sql("SELECT max(attempts) FROM outbox WHERE status = 'pending'")
        assert attempts and attempts >= 1

    eventually(webhook_waiting, timeout=15)
    stack.nodes.start("field-app")

    eventually(lambda: _check(stack.field_sql("SELECT count(*) FROM tasks WHERE status = 'ASSIGNED'") == [(3,)]), timeout=20)


def test_c1_registry_down_while_technician_works(stack: Stack) -> None:
    _assets_synced(stack)
    manager = stack.ui("registry", "anna.kierownik")
    codes = ("PI-B1-2-014", "PI-B1-2-015", "KL-B1-3-001")
    for code in codes:
        manager.post("/ui/work-orders", data={"asset_code": code, "reason": "przeglad_okresowy", "assigned_to": "jan.technik"})
    eventually(lambda: _check(stack.field_sql("SELECT count(*) FROM tasks") == [(3,)]), timeout=15)
    order_ids = [r[0] for r in stack.field_sql("SELECT id FROM tasks")]
    technician = stack.ui("field-app", "jan.technik")

    stack.nodes.stop("registry")
    started = time.monotonic()
    for order_id in order_ids:
        response = technician.post(f"/ui/tasks/{order_id}/inspection", data={"result": "OK", "findings": "bez uwag"})
        assert response.status_code == 200
    # Work goes on locally and the changes wait in the field app outbox.
    assert stack.field_sql("SELECT count(*) FROM pending_changes WHERE status = 'pending'") == [(9,)]
    eventually(lambda: _check(stack.field_sql("SELECT max(attempts) FROM pending_changes")[0][0] >= 1), timeout=15)
    remaining = OUTAGE_S - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)  # the outage itself, not a wait for consistency
    stack.nodes.start("registry")

    def delivered_once() -> None:
        assert stack.registry_sql("SELECT count(*) FROM inspections") == [(3,)]
        assert stack.registry_sql("SELECT count(*) FROM work_orders WHERE status = 'zakonczone'") == [(3,)]
        assert stack.field_sql("SELECT DISTINCT status FROM pending_changes") == [("sent",)]

    eventually(delivered_once, timeout=30)
    assert stack.registry_sql("SELECT count(*) FROM applied_changes") == [(9,)]


def _check(condition: bool) -> None:
    assert condition

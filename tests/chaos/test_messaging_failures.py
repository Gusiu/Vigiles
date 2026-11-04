"""Failure scenarios C2, C3 and C7 (PLAN.md 10.3) on the full local stack."""

import os
import time

from harness.eventually import eventually
from harness.stack import Stack

OUTAGE_S = float(os.environ.get("CHAOS_OUTAGE_S", "5"))


def _check(condition: bool) -> None:
    assert condition


def _telemetry_processed(stack: Stack) -> int:
    return stack.hub_sql("SELECT count(*) FROM processed_messages WHERE consumer = 'telemetry'")[0][0]


def test_c2_hub_replica_killed_during_burst(full_stack: Stack) -> None:
    full_stack.scenario("burst", {"count": 200})
    eventually(lambda: _check(_telemetry_processed(full_stack) >= 20), timeout=30, interval=0.05)
    full_stack.nodes.kill("hub-1")

    # hub-2 keeps processing new messages while hub-1 is down.
    before = _telemetry_processed(full_stack)
    full_stack.scenario("burst", {"count": 20})
    eventually(lambda: _check(_telemetry_processed(full_stack) > before), timeout=30)
    full_stack.nodes.start("hub-1")

    # Messages in flight at hub-1 arrive after its restart (or were handed
    # to hub-2 by the broker): 220 effects either way, none twice.
    eventually(lambda: _check(_telemetry_processed(full_stack) == 220), timeout=90)
    instances = dict(full_stack.hub_sql("SELECT instance, count(*) FROM processed_messages GROUP BY instance"))
    assert instances.get("hub-2", 0) > 0
    assert full_stack.hub_sql("SELECT count(DISTINCT message_id) FROM processed_messages WHERE consumer = 'telemetry'") == [(220,)]
    assert full_stack.hub_sql("SELECT count(*) FROM outbox") == [(0,)]  # all below the threshold


def test_c3_broker_down_gateway_buffers(full_stack: Stack) -> None:
    full_stack.nodes.stop("mosquitto")
    started = time.monotonic()
    for value in (85, 86, 87):
        full_stack.scenario("contamination", {"device_id": "A-000124", "value": value})
    full_stack.scenario("damper-fault", {"device_id": "BETA-7F3K2", "closing_time_s": 99})
    assert full_stack.gateway_buffer() == 4
    remaining = OUTAGE_S - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)  # the outage itself
    full_stack.nodes.start("mosquitto")

    eventually(lambda: _check(full_stack.gateway_buffer() == 0), timeout=60)
    eventually(lambda: _check(_telemetry_processed(full_stack) == 3), timeout=60)

    def orders() -> None:
        rows = dict(full_stack.registry_sql("SELECT reason, count(*) FROM work_orders GROUP BY reason"))
        assert rows == {"czyszczenie": 1, "usterka_urzadzenia": 1}

    eventually(orders, timeout=60)


def test_c7_gateway_killed_last_will(full_stack: Stack) -> None:
    eventually(lambda: _check(full_stack.gateway_buffer() == 0), timeout=10)
    full_stack.nodes.kill("gateway")  # SIGKILL: a clean stop would not send the Last Will

    def connectivity_orders() -> None:
        rows = full_stack.registry_sql(
            "SELECT a.code FROM work_orders w JOIN assets a ON a.id = w.asset_id WHERE w.reason = 'brak_lacznosci' ORDER BY 1")
        assert [r[0] for r in rows] == ["CZ-B1-3-001", "CZ-B1-3-002", "KL-B1-3-002"]

    eventually(connectivity_orders, timeout=30)

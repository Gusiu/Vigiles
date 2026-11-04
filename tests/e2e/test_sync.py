"""Scenarios S1-S3 (PLAN.md section 5) on the running local stack."""

import re

from harness.eventually import eventually
from harness.stack import Stack


def _imported(stack: Stack) -> None:
    stack.drop("zestawienie_B1.csv")

    def in_both_systems() -> None:
        assert stack.registry_sql("SELECT count(*) FROM assets") == [(11,)]
        assert stack.field_sql("SELECT count(*) FROM assets") == [(11,)]

    eventually(in_both_systems, timeout=30)


def test_s1_import_reaches_registry_and_field_app(stack: Stack) -> None:
    _imported(stack)
    stack.drop("zestawienie_B1_bledy.csv")

    def report() -> None:
        assert (stack.data_dir / "error" / "zestawienie_B1_bledy.rejected.csv").exists()
        assert stack.field_sql("SELECT count(*) FROM assets") == [(13,)]

    eventually(report, timeout=30)
    assert stack.field_sql("SELECT condition, label FROM assets WHERE code = 'PI-B1-2-014'") == [
        ("OK", "B1, floor 2, room 2.14, ściana")
    ]


def test_s2_work_order_inspection_round_trip(stack: Stack) -> None:
    _imported(stack)
    manager = stack.ui("registry", "anna.kierownik")
    technician = stack.ui("field-app", "jan.technik")

    page = manager.post("/ui/work-orders", data={"asset_code": "PI-B1-2-014", "reason": "przeglad_okresowy",
                                                 "assigned_to": "jan.technik"}).text
    assert "Work order created" in page
    [(order_id,)] = stack.registry_sql("SELECT id::text FROM work_orders")

    eventually(lambda: _assert(stack.field_sql("SELECT status FROM tasks WHERE id = ?", (order_id,)) == [("ASSIGNED",)]),
               timeout=10)
    assert "Periodic inspection: PI-B1-2-014" in technician.get("/ui/tasks").text

    technician.post(f"/ui/tasks/{order_id}/start")
    technician.post(f"/ui/tasks/{order_id}/inspection", data={"result": "NEEDS_REPAIR", "findings": "pęknięta masa"})

    def closed_in_registry() -> None:
        assert stack.registry_sql("SELECT status FROM work_orders WHERE id = %s", (order_id,)) == [("zakonczone",)]
        assert stack.registry_sql("SELECT condition FROM assets WHERE code = 'PI-B1-2-014'") == [("do_naprawy",)]
        assert stack.registry_sql("SELECT result, findings FROM inspections") == [("do_naprawy", "pęknięta masa")]

    eventually(closed_in_registry, timeout=15)
    assert "zakonczone" in manager.get("/ui/work-orders").text


def test_s3_offline_conflict_resolved_by_manager(stack: Stack) -> None:
    _imported(stack)
    manager = stack.ui("registry", "anna.kierownik")
    technician = stack.ui("field-app", "jan.technik")
    [(asset_id,)] = stack.registry_sql("SELECT id::text FROM assets WHERE code = 'PI-B1-2-014'")

    technician.post("/ui/sync/offline", data={"offline": "1"})
    technician.post(f"/ui/assets/{asset_id}/notes", data={"notes": "masa wymieniona"})
    manager.post(f"/ui/assets/{asset_id}/notes", data={"notes": "do weryfikacji"})
    technician.post("/ui/sync/offline", data={"offline": "0"})

    eventually(lambda: _assert(stack.registry_sql("SELECT count(*) FROM conflicts WHERE status = 'open'") == [(1,)]), timeout=10)
    eventually(lambda: _assert("in conflict" in technician.get("/ui/assets").text), timeout=10)
    conflicts_page = manager.get("/ui/conflicts").text
    assert "masa wymieniona" in conflicts_page and "do weryfikacji" in conflicts_page

    [(conflict_id,)] = stack.registry_sql("SELECT id::text FROM conflicts")
    manager.post(f"/ui/conflicts/{conflict_id}/resolve", data={"choice": "client"})

    def resolved_everywhere() -> None:
        assert stack.registry_sql("SELECT notes FROM assets WHERE id = %s", (asset_id,)) == [("masa wymieniona",)]
        assert stack.field_sql("SELECT notes FROM assets WHERE id = ?", (asset_id,)) == [("masa wymieniona",)]
        assert "in conflict" not in technician.get("/ui/assets").text

    eventually(resolved_everywhere, timeout=15)


def test_s3_parallel_changes_of_different_fields_merge(stack: Stack) -> None:
    _imported(stack)
    technician = stack.ui("field-app", "jan.technik")
    [(asset_id,)] = stack.registry_sql("SELECT id::text FROM assets WHERE code = 'PI-B1-2-015'")

    technician.post("/ui/sync/offline", data={"offline": "1"})
    technician.post(f"/ui/assets/{asset_id}/notes", data={"notes": "sprawdzone"})
    stack.registry_sql("UPDATE assets SET room = '2.14B' WHERE id = %s RETURNING id", (asset_id,))
    technician.post("/ui/sync/offline", data={"offline": "0"})

    def merged() -> None:
        assert stack.registry_sql("SELECT notes, room FROM assets WHERE id = %s", (asset_id,)) == [("sprawdzone", "2.14B")]
        [(label,)] = stack.field_sql("SELECT label FROM assets WHERE id = ?", (asset_id,))
        assert re.search(r"room 2\.14B", label)

    eventually(merged, timeout=15)
    assert stack.registry_sql("SELECT count(*) FROM conflicts") == [(0,)]


def _assert(condition: bool) -> None:
    assert condition

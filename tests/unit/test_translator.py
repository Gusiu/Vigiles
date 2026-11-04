import pytest

from vigiles_field_app.sync import translator as t

REGISTRY_ASSET = {
    "id": "a1", "code": "PI-B1-2-014", "building": "B1", "floor": "2", "room": "2.14", "partition": "ściana",
    "condition": "do_naprawy", "notes": "pęknięta masa", "rev": 9,
}


def test_asset_from_registry() -> None:
    assert t.asset_from_registry(REGISTRY_ASSET) == {
        "id": "a1", "code": "PI-B1-2-014", "label": "B1, floor 2, room 2.14, ściana",
        "condition": "NEEDS_REPAIR", "notes": "pęknięta masa", "server_rev": 9,
    }


@pytest.mark.parametrize(("registry", "local"), list(t.CONDITION.items()))
def test_condition_round_trip(registry: str, local: str) -> None:
    assert t.asset_patch_to_registry({"condition": local}) == {"condition": registry}


@pytest.mark.parametrize(("registry", "local"), list(t.STATUS.items()))
def test_status_round_trip(registry: str, local: str) -> None:
    assert t.task_status_to_registry(t.STATUS[registry]) == registry


def test_task_from_registry() -> None:
    task = t.task_from_registry(
        {"id": "w1", "asset_id": "a1", "reason": "usterka_urzadzenia", "status": "przydzielone",
         "due_date": "2026-11-10", "assigned_to": "jan.technik", "rev": 2},
        "KL-B1-3-002",
    )
    assert task["title"] == "Device fault: KL-B1-3-002"
    assert (task["status"], task["server_rev"]) == ("ASSIGNED", 2)


def test_label_skips_missing_parts() -> None:
    assert t.label({"building": "B1", "floor": None, "room": "korytarz"}) == "B1, room korytarz"

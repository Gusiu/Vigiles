import pytest

from vigiles_registry.sync.merge import PatchError, three_way_merge

REV7 = {"room": "2.14", "condition": "sprawny", "notes": None, "fire_rating": "EI 60"}


def test_plan_example_disjoint_fields_merge() -> None:
    # Section 8, step 3: office changed room (rev 8), field changes condition and notes from rev 7.
    current = {**REV7, "room": "2.14A"}
    result = three_way_merge("asset", REV7, current, {"condition": "do_naprawy", "notes": "pęknięta masa"})

    assert result.apply == {"condition": "do_naprawy", "notes": "pęknięta masa"}
    assert result.conflicts == []
    assert result.server_fields == ["room"]


def test_plan_example_shared_field_conflicts() -> None:
    # Step 5: base rev 9 has the field's notes, office changed notes in rev 10.
    rev9 = {**REV7, "room": "2.14A", "condition": "do_naprawy", "notes": "pęknięta masa"}
    current = {**rev9, "notes": "do weryfikacji"}
    result = three_way_merge("asset", rev9, current, {"notes": "masa wymieniona"})

    assert result.apply == {}
    assert result.conflicts == [("notes", "pęknięta masa", "do weryfikacji", "masa wymieniona")]


def test_field_owned_overlap_client_wins() -> None:
    current = {**REV7, "condition": "niesprawny"}
    result = three_way_merge("asset", REV7, current, {"condition": "do_naprawy"})

    assert result.apply == {"condition": "do_naprawy"}


def test_office_owned_overlap_server_stays() -> None:
    current = {**REV7, "fire_rating": "EI 90"}
    result = three_way_merge("asset", REV7, current, {"fire_rating": "EI 120"})

    assert result.apply == {}
    assert result.kept_server == ["fire_rating"]


def test_same_value_on_both_sides_is_not_a_conflict() -> None:
    current = {**REV7, "notes": "ok"}
    result = three_way_merge("asset", REV7, current, {"notes": "ok"})

    assert result.apply == {} and result.conflicts == []


def test_unchanged_patch_field_is_ignored() -> None:
    result = three_way_merge("asset", REV7, REV7, {"condition": "sprawny"})

    assert result.apply == {}


def test_timestamps_in_different_formats_compare_equal() -> None:
    base = {**REV7, "last_inspected_at": "2026-11-04T10:00:00+00:00"}
    result = three_way_merge("asset", base, base, {"last_inspected_at": "2026-11-04T10:00:00Z"})

    assert result.apply == {}


@pytest.mark.parametrize(
    ("entity", "patch", "message"),
    [
        ("asset", {"rev": 3}, "unknown or read-only"),
        ("asset", {}, "empty"),
        ("incident", {"status": "closed"}, "cannot be changed"),
        ("work_order", {"status": "anulowane"}, "only be set by the office"),
        ("asset", {"last_inspected_at": "yesterday"}, "invalid date"),
    ],
)
def test_invalid_patches(entity: str, patch: dict, message: str) -> None:
    with pytest.raises(PatchError, match=message):
        three_way_merge(entity, {}, {}, patch)


def test_work_order_completion_from_field() -> None:
    base = {"status": "przydzielone", "assigned_to": "jan.technik"}
    current = {"status": "przydzielone", "assigned_to": "jan.technik", "due_date": "2026-11-10"}
    result = three_way_merge("work_order", base, current, {"status": "zakonczone"})

    assert result.apply == {"status": "zakonczone"}
    assert result.server_fields == ["due_date"]

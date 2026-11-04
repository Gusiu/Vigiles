"""Message Translator between the registry model and the field app model.

| Registry                         | Field app                                |
|----------------------------------|------------------------------------------|
| condition sprawny, do_naprawy... | OK, NEEDS_REPAIR, FAILED, RETIRED        |
| work order status nowe, ...      | NEW, ASSIGNED, IN_PROGRESS, DONE, ...    |
| building, floor, room, partition | label (read only)                        |
| rev                              | server_rev                               |
"""

from typing import Any

CONDITION = {"sprawny": "OK", "do_naprawy": "NEEDS_REPAIR", "niesprawny": "FAILED", "wycofany": "RETIRED"}
STATUS = {
    "nowe": "NEW", "przydzielone": "ASSIGNED", "w_realizacji": "IN_PROGRESS",
    "zakonczone": "DONE", "anulowane": "CANCELLED",
}
RESULT = {"OK": "sprawny", "NEEDS_REPAIR": "do_naprawy", "FAILED": "niesprawny"}
REASON = {
    "przeglad_okresowy": "Periodic inspection",
    "usterka_urzadzenia": "Device fault",
    "czyszczenie": "Cleaning",
    "brak_lacznosci": "No connection",
}

CONDITION_TO_REGISTRY = {v: k for k, v in CONDITION.items()}
STATUS_TO_REGISTRY = {v: k for k, v in STATUS.items()}


def label(data: dict[str, Any]) -> str:
    parts = [data.get("building")]
    if data.get("floor"):
        parts.append(f"floor {data['floor']}")
    if data.get("room"):
        parts.append(f"room {data['room']}")
    if data.get("partition"):
        parts.append(data["partition"])
    return ", ".join(p for p in parts if p)


def asset_from_registry(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data["id"],
        "code": data["code"],
        "label": label(data),
        "condition": CONDITION[data["condition"]],
        "notes": data.get("notes"),
        "server_rev": data["rev"],
    }


def task_from_registry(data: dict[str, Any], asset_code: str | None) -> dict[str, Any]:
    reason = REASON.get(data["reason"], data["reason"])
    return {
        "id": data["id"],
        "asset_id": data["asset_id"],
        "title": f"{reason}: {asset_code}" if asset_code else reason,
        "reason": data["reason"],
        "status": STATUS[data["status"]],
        "due_date": data.get("due_date"),
        "assigned_to": data.get("assigned_to"),
        "description": data.get("description"),
        "server_rev": data["rev"],
    }


def asset_patch_to_registry(patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(patch)
    if "condition" in out:
        out["condition"] = CONDITION_TO_REGISTRY[out["condition"]]
    return out


def task_status_to_registry(status: str) -> str:
    return STATUS_TO_REGISTRY[status]


# Local fields affected by a pending registry patch, used to keep local
# edits when an older server state arrives in the feed.
LOCAL_FIELD = {"asset": {"condition": "condition", "notes": "notes"}, "work_order": {"status": "status"}}

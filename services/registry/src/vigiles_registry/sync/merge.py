"""Three-way merge with field ownership (PLAN.md section 8).

``base`` is the entity at the revision the client started from, ``current``
the server state now, ``patch`` the fields the client wants to change.

1. server_changed: fields where current != base,
2. client_changed: fields in the patch where patch != base,
3. overlap: both changed the field to different values,
4. overlap is decided by ownership: field-owned -> client wins,
   office-owned -> server value stays (logged), shared -> conflict,
5. the remaining client changes are applied.

No last-write-wins: a concurrent edit of a shared field is never lost.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

FIELD, OFFICE, SHARED = "field", "office", "shared"

OWNERSHIP: dict[str, dict[str, str]] = {
    "asset": {
        "condition": FIELD,
        "last_inspected_at": FIELD,
        "notes": SHARED,
        **{
            f: OFFICE
            for f in (
                "code", "asset_type", "building", "floor", "room", "partition", "installation",
                "diameter_mm", "seal_system", "fire_rating", "installed_at", "installer",
                "next_inspection_due",
            )
        },
    },
    "work_order": {
        "status": FIELD,
        **{f: OFFICE for f in ("assigned_to", "due_date", "reason", "description")},
    },
}

# Work order statuses a technician may set (the rest belong to the office).
FIELD_STATUSES = {"w_realizacji", "zakonczone"}
_TIMESTAMPS = {"last_inspected_at"}
_DATES = {"installed_at", "next_inspection_due", "due_date"}


class PatchError(ValueError):
    pass


def normalize(name: str, value: Any) -> Any:
    """Make values from JSON comparable with values from to_jsonb()."""
    if value is None or not isinstance(value, str):
        return value
    try:
        if name in _TIMESTAMPS:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.isoformat()
        if name in _DATES:
            return date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise PatchError(f"{name}: invalid date or time {value!r}") from exc
    return value


@dataclass
class MergeResult:
    apply: dict[str, Any] = field(default_factory=dict)
    conflicts: list[tuple[str, Any, Any, Any]] = field(default_factory=list)  # field, base, server, client
    kept_server: list[str] = field(default_factory=list)  # office fields the client may not override
    server_fields: list[str] = field(default_factory=list)  # fields changed on the server meanwhile


def validate_patch(entity: str, patch: dict[str, Any]) -> dict[str, Any]:
    ownership = OWNERSHIP.get(entity)
    if ownership is None:
        raise PatchError(f"entity {entity!r} cannot be changed through sync")
    if not patch:
        raise PatchError("patch is empty")
    unknown = sorted(set(patch) - set(ownership))
    if unknown:
        raise PatchError(f"unknown or read-only field(s): {', '.join(unknown)}")
    if entity == "work_order" and "status" in patch and patch["status"] not in FIELD_STATUSES:
        raise PatchError(f"status {patch['status']!r} can only be set by the office")
    return {name: normalize(name, value) for name, value in patch.items()}


def three_way_merge(
    entity: str, base: dict[str, Any], current: dict[str, Any], patch: dict[str, Any]
) -> MergeResult:
    patch = validate_patch(entity, patch)
    ownership = OWNERSHIP[entity]
    b = {k: normalize(k, base.get(k)) for k in ownership}
    c = {k: normalize(k, current.get(k)) for k in ownership}

    server_changed = {k for k in ownership if c[k] != b[k]}
    client_changed = {k for k, v in patch.items() if v != b[k]}
    overlap = {k for k in server_changed & client_changed if patch[k] != c[k]}

    result = MergeResult(server_fields=sorted(server_changed - client_changed))
    for name in sorted(client_changed):
        value = patch[name]
        if name in overlap:
            owner = ownership[name]
            if owner == FIELD:
                result.apply[name] = value
            elif owner == OFFICE:
                result.kept_server.append(name)
            else:
                result.conflicts.append((name, b[name], c[name], value))
        elif value != c[name]:
            result.apply[name] = value
    return result

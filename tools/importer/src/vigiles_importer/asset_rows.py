"""Validation of asset list rows.

Each row becomes an ``AssetRecord`` or a rejection with a readable reason.
One bad row never stops the file: valid rows are imported and rejected
rows go to the report, together with the line number in the source file.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ValidationError, ValidationInfo, field_validator

from vigiles_importer.csv_reader import CsvTable
from vigiles_importer.headers import HeaderMap, map_headers
from vigiles_importer.text import blank_to_none, slug

AssetType = Literal["przejscie_instalacyjne", "drzwi_ppoz", "klapa_ppoz", "czujka_dymu"]

# Fire resistance classes accepted for EI ratings, in minutes.
FIRE_RATING_CLASSES = (15, 20, 30, 45, 60, 90, 120, 180, 240)

_ASSET_TYPES: dict[str, str] = {
    "przejscie_instalacyjne": "przejscie_instalacyjne",
    "przejscie": "przejscie_instalacyjne",
    "drzwi_ppoz": "drzwi_ppoz",
    "drzwi_przeciwpozarowe": "drzwi_ppoz",
    "klapa_ppoz": "klapa_ppoz",
    "klapa_przeciwpozarowa": "klapa_ppoz",
    "czujka_dymu": "czujka_dymu",
    "czujka": "czujka_dymu",
}
_SCIENTIFIC = re.compile(r"^\d+(?:[.,]\d+)?E[+-]?\d+$", re.IGNORECASE)
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9./_-]{0,63}$")
_RATING = re.compile(r"^EI\s*(\d+)$", re.IGNORECASE)
_NUMBER = re.compile(r"^\d+(?:[.,]0+)?$")


class AssetRecord(BaseModel):
    """Office-owned fields of an asset, as delivered by the contractor."""

    code: str
    asset_type: AssetType
    building: str
    floor: str | None = None
    room: str | None = None
    partition: str | None = None
    installation: str | None = None
    diameter_mm: int | None = None
    seal_system: str | None = None
    fire_rating: str | None = None
    installed_at: date | None = None
    installer: str | None = None
    notes: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, value: str | None) -> str:
        value = blank_to_none(value)
        if value is None:
            raise ValueError("element number is missing")
        if _SCIENTIFIC.match(value):
            raise ValueError(
                f"element number {value!r} was turned into scientific notation by Excel and "
                "cannot be recovered; format the column as text and export again"
            )
        value = value.upper()
        if not _CODE.match(value):
            raise ValueError(f"element number {value!r} contains invalid characters")
        return value

    @field_validator("asset_type", mode="before")
    @classmethod
    def _asset_type(cls, value: str | None) -> str:
        value = blank_to_none(value)
        if value is None:
            raise ValueError("element type is missing")
        known = _ASSET_TYPES.get(slug(value))
        if known is None:
            raise ValueError(
                f"unknown element type {value!r}; expected one of: przejście instalacyjne, "
                "drzwi ppoż., klapa ppoż., czujka dymu"
            )
        return known

    @field_validator("building", mode="before")
    @classmethod
    def _building(cls, value: str | None) -> str:
        value = blank_to_none(value)
        if value is None:
            raise ValueError("building is missing")
        return value.upper()

    @field_validator(
        "floor", "room", "partition", "installation", "seal_system", "installer", "notes",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return blank_to_none(value)

    @field_validator("diameter_mm", mode="before")
    @classmethod
    def _diameter(cls, value: str | None) -> int | None:
        value = blank_to_none(value)
        if value is None:
            return None
        if not _NUMBER.match(value):
            raise ValueError(f"diameter {value!r} is not a whole number of millimetres")
        number = int(re.split(r"[.,]", value)[0])
        if number <= 0:
            raise ValueError("diameter must be greater than zero")
        return number

    @field_validator("fire_rating", mode="before")
    @classmethod
    def _fire_rating(cls, value: str | None) -> str | None:
        value = blank_to_none(value)
        if value is None:
            return None
        match = _RATING.match(value)
        if not match:
            raise ValueError(f"fire rating {value!r} is not in the form 'EI 60'")
        minutes = int(match[1])
        if minutes not in FIRE_RATING_CLASSES:
            classes = ", ".join(map(str, FIRE_RATING_CLASSES))
            raise ValueError(f"EI {minutes} is not a standard fire rating class ({classes})")
        return f"EI {minutes}"

    @field_validator("installed_at", mode="before")
    @classmethod
    def _installed_at(cls, value: str | None, info: ValidationInfo) -> date | None:
        value = blank_to_none(value)
        if value is None:
            return None
        parsed = None
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(f"installation date {value!r} is not in the form dd.mm.yyyy")
        today = (info.context or {}).get("today") or date.today()
        if parsed > today:
            raise ValueError(f"installation date {value} is in the future")
        return parsed


@dataclass(frozen=True)
class RejectedRow:
    line: int
    cells: list[str]
    reason: str


@dataclass(frozen=True)
class ParsedAssets:
    records: list[tuple[int, AssetRecord]]
    rejected: list[RejectedRow]
    headers: HeaderMap


def _reason(error: ValidationError, labels: dict[str, str]) -> str:
    parts = []
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else ""
        message = item["msg"].removeprefix("Value error, ")
        parts.append(f"{labels.get(field, field)}: {message}" if field else message)
    return "; ".join(parts)


def parse_assets(table: CsvTable, today: date | None = None) -> ParsedAssets:
    headers = map_headers(table.header)
    records: list[tuple[int, AssetRecord]] = []
    rejected: list[RejectedRow] = []
    first_line_of: dict[str, int] = {}

    for line, cells in table.rows:
        values = {
            field: cells[index] if index < len(cells) else None
            for field, index in headers.columns.items()
        }
        try:
            record = AssetRecord.model_validate(values, context={"today": today})
        except ValidationError as exc:
            rejected.append(RejectedRow(line, cells, _reason(exc, headers.labels)))
            continue
        if record.code in first_line_of:
            rejected.append(
                RejectedRow(
                    line,
                    cells,
                    f"{headers.labels['code']}: duplicate element number {record.code}, "
                    f"first seen on line {first_line_of[record.code]}",
                )
            )
            continue
        first_line_of[record.code] = line
        records.append((line, record))

    return ParsedAssets(records=records, rejected=rejected, headers=headers)

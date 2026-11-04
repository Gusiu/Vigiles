"""Validation of the site configuration file (``site-config.json``).

The site header must be valid or the file is rejected as a whole. Zones
and devices are validated one by one; invalid items are reported and the
rest is imported. References to zones and assets that are not in the file
are resolved against the database later.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, field_validator

from vigiles_importer.csv_reader import FileFormatError
from vigiles_importer.text import blank_to_none


def _required_code(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is missing")
    return value.strip().upper()


class SiteInfo(BaseModel):
    code: str
    name: str | None = None
    address: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, value: Any) -> str:
        return _required_code(value, "site code")

    @field_validator("name", "address", mode="before")
    @classmethod
    def _text(cls, value: Any) -> Any:
        return blank_to_none(value) if isinstance(value, str) else value


class ZoneInfo(BaseModel):
    code: str
    name: str | None = None
    floor: int | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, value: Any) -> str:
        return _required_code(value, "zone code")


class DeviceInfo(BaseModel):
    device_id: str
    vendor: Literal["alfa", "beta"]
    asset_code: str
    zone: str
    gateway: str

    @field_validator("device_id", "asset_code", "zone", mode="before")
    @classmethod
    def _codes(cls, value: Any, info) -> str:
        return _required_code(value, info.field_name)

    @field_validator("vendor", "gateway", mode="before")
    @classmethod
    def _lower(cls, value: Any, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} is missing")
        return value.strip().lower()


@dataclass(frozen=True)
class RejectedItem:
    path: str
    item: Any
    reason: str


@dataclass(frozen=True)
class ParsedSiteConfig:
    site: SiteInfo
    zones: list[ZoneInfo]
    devices: list[DeviceInfo]
    rejected: list[RejectedItem]

    @property
    def total(self) -> int:
        return len(self.zones) + len(self.devices) + len(self.rejected)


def _reason(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg'].removeprefix('Value error, ')}"
        if e["loc"]
        else e["msg"]
        for e in error.errors()
    )


def parse_site_config(data: bytes) -> ParsedSiteConfig:
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise FileFormatError("JSON file must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise FileFormatError(f"invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(document, dict):
        raise FileFormatError("top level of the file must be an object")
    try:
        site = SiteInfo.model_validate(document.get("site"))
    except ValidationError as exc:
        raise FileFormatError(f"site: {_reason(exc)}") from exc

    rejected: list[RejectedItem] = []
    zones: list[ZoneInfo] = []
    devices: list[DeviceInfo] = []

    for key, model, target, identity in (
        ("zones", ZoneInfo, zones, "code"),
        ("devices", DeviceInfo, devices, "device_id"),
    ):
        items = document.get(key, [])
        if not isinstance(items, list):
            raise FileFormatError(f"{key} must be a list")
        seen: dict[str, int] = {}
        for index, item in enumerate(items):
            path = f"{key}[{index}]"
            try:
                parsed = model.model_validate(item)
            except ValidationError as exc:
                rejected.append(RejectedItem(path, item, _reason(exc)))
                continue
            ident = getattr(parsed, identity)
            if ident in seen:
                rejected.append(
                    RejectedItem(path, item, f"duplicate {identity} {ident}, first at {key}[{seen[ident]}]")
                )
                continue
            seen[ident] = index
            target.append(parsed)

    return ParsedSiteConfig(site=site, zones=zones, devices=devices, rejected=rejected)

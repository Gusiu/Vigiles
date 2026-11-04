import json

import pytest

from vigiles_importer.csv_reader import FileFormatError
from vigiles_importer.site_config import parse_site_config

SITE = {"code": "b1", "name": "Budynek biurowy B1", "address": "ul. Przykładowa 1"}
DEVICE = {"device_id": "A-000123", "vendor": "alfa", "asset_code": "CZ-B1-3-001", "zone": "Z3", "gateway": "gw-01"}


def _doc(**overrides) -> bytes:
    doc = {"site": SITE, "zones": [{"code": "Z3", "name": "Piętro 3", "floor": 3}], "devices": [DEVICE]}
    doc.update(overrides)
    return json.dumps(doc, ensure_ascii=False).encode()


def test_valid_file() -> None:
    parsed = parse_site_config(_doc())

    assert parsed.site.code == "B1"
    assert parsed.zones[0].floor == 3
    assert parsed.devices[0].vendor == "alfa"
    assert parsed.rejected == []
    assert parsed.total == 2


def test_bom_is_accepted() -> None:
    assert parse_site_config(b"\xef\xbb\xbf" + _doc()).site.code == "B1"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"{not json", "invalid JSON at line 1"),
        (b"[]", "must be an object"),
        (json.dumps({"zones": []}).encode(), "site"),
        (json.dumps({"site": {"code": " "}}).encode(), "site code is missing"),
        (json.dumps({"site": SITE, "devices": {}}).encode(), "devices must be a list"),
        ("{}".encode("utf-16"), "UTF-8"),
    ],
)
def test_file_level_errors(data: bytes, message: str) -> None:
    with pytest.raises(FileFormatError, match=message):
        parse_site_config(data)


def test_invalid_items_are_rejected_individually() -> None:
    devices = [
        DEVICE,
        {**DEVICE, "device_id": "X-1", "vendor": "gamma"},
        {**DEVICE, "device_id": "X-2", "asset_code": ""},
        {**DEVICE},
    ]
    parsed = parse_site_config(_doc(devices=devices, zones=[{"code": "Z3"}, {"name": "no code"}]))

    assert [d.device_id for d in parsed.devices] == ["A-000123"]
    reasons = {r.path: r.reason for r in parsed.rejected}
    assert "vendor" in reasons["devices[1]"]
    assert "asset_code is missing" in reasons["devices[2]"]
    assert "duplicate device_id A-000123" in reasons["devices[3]"]
    assert reasons["zones[1]"] == "code: Field required"

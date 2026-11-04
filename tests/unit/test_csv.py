import codecs
from datetime import date

import pytest

from vigiles_importer.asset_rows import parse_assets
from vigiles_importer.csv_reader import FileFormatError, read_csv
from vigiles_importer.headers import field_for, map_headers

HEADER = (
    "Nr elementu;Typ;Budynek;Kondygnacja;Pomieszczenie;Przegroda;Instalacja;Średnica [mm];"
    "System uszczelnienia;Klasa EI;Data montażu;Brygada;Uwagi"
)
GOOD = "PI-B1-2-014;przejście instalacyjne;B1;2;2.14;ściana;rura PVC;110;kołnierz ogniochronny;EI 60;14.03.2025;B-2;"
TODAY = date(2026, 11, 4)


def _csv(*rows: str, header: str = HEADER, sep: str = ";") -> str:
    lines = [header, *rows]
    return "\r\n".join(line.replace(";", sep) for line in lines) + "\r\n"


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        (_csv(GOOD).encode("utf-8"), "utf-8"),
        (codecs.BOM_UTF8 + _csv(GOOD).encode("utf-8"), "utf-8-sig"),
        (_csv(GOOD).encode("cp1250"), "cp1250"),
    ],
)
def test_reads_supported_encodings(data: bytes, encoding: str) -> None:
    table = read_csv(data)
    parsed = parse_assets(table, TODAY)

    assert table.encoding == encoding
    assert parsed.rejected == []
    [(line, record)] = parsed.records
    assert line == 2
    assert record.code == "PI-B1-2-014"
    assert record.asset_type == "przejscie_instalacyjne"
    assert record.partition == "ściana"
    assert record.seal_system == "kołnierz ogniochronny"
    assert record.diameter_mm == 110
    assert record.fire_rating == "EI 60"
    assert record.installed_at == date(2025, 3, 14)
    assert record.notes is None


def test_reads_comma_separated_file() -> None:
    table = read_csv(_csv(GOOD, sep=",").encode())

    assert table.delimiter == ","
    assert parse_assets(table, TODAY).records[0][1].code == "PI-B1-2-014"


def test_quoted_comma_in_comma_separated_file() -> None:
    header = "Nr elementu,Typ,Budynek,Uwagi"
    table = read_csv(f'{header}\r\nD-1,drzwi ppoż.,B1,"lewe, przy windzie"\r\n'.encode())

    assert parse_assets(table, TODAY).records[0][1].notes == "lewe, przy windzie"


def test_trailing_empty_rows_are_ignored() -> None:
    table = read_csv((_csv(GOOD) + ";;;;;;;;;;;;\r\n;;;;;;;;;;;;\r\n").encode())

    assert len(table.rows) == 1


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("Średnica [mm]", "diameter_mm"),
        ("srednica (mm)", "diameter_mm"),
        ("  ŚREDNICA  ", "diameter_mm"),
        ("Data  montażu", "installed_at"),
        ("﻿Nr elementu", "code"),
        ("klasa EI", "fire_rating"),
    ],
)
def test_header_aliases(header: str, field: str) -> None:
    assert field_for(header) == field


def test_unknown_columns_are_ignored() -> None:
    mapping = map_headers(["Nr elementu", "Typ", "Budynek", "Kolor"])

    assert mapping.ignored == ["Kolor"]


def test_missing_required_column_fails_whole_file() -> None:
    with pytest.raises(FileFormatError, match="missing required column"):
        map_headers(["Typ", "Budynek"])


def test_two_columns_for_one_field_fail() -> None:
    with pytest.raises(FileFormatError, match="both map to code"):
        map_headers(["Nr elementu", "Kod", "Typ", "Budynek"])


@pytest.mark.parametrize(
    "data",
    [b"", b"\r\n\r\n", b"just one header without separators\r\n", b"\x81\x81;x\r\n"],
)
def test_unreadable_files(data: bytes) -> None:
    with pytest.raises(FileFormatError):
        read_csv(data)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (";przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;EI 60;14.03.2025;B-2;", "element number is missing"),
        ("PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;EI 75;14.03.2025;B-2;", "EI 75 is not a standard"),
        ("PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;EI 60;14.03.2027;B-2;", "in the future"),
        ("1,23E+11;przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;EI 60;14.03.2025;B-2;", "scientific notation"),
        ("PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;sto;k;EI 60;14.03.2025;B-2;", "not a whole number"),
        ("PI-1;gaśnica;B1;2;2.14;ściana;rura;110;k;EI 60;14.03.2025;B-2;", "unknown element type"),
        ("PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;EI 60;2025/03/14;B-2;", "dd.mm.yyyy"),
        ("PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;R 60;14.03.2025;B-2;", "form 'EI 60'"),
        ("PI-1;przejście instalacyjne;;2;2.14;ściana;rura;110;k;EI 60;14.03.2025;B-2;", "building is missing"),
    ],
)
def test_invalid_rows_are_rejected_with_reason(row: str, reason: str) -> None:
    parsed = parse_assets(read_csv(_csv(GOOD.replace("PI-B1-2-014", "OK-1"), row).encode()), TODAY)

    assert [r.code for _, r in parsed.records] == ["OK-1"]
    [rejected] = parsed.rejected
    assert rejected.line == 3
    assert reason in rejected.reason


def test_reason_uses_column_name_from_file() -> None:
    row = "PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;EI 75;14.03.2025;B-2;"
    parsed = parse_assets(read_csv(_csv(row).encode()), TODAY)

    assert parsed.rejected[0].reason.startswith("Klasa EI: ")


def test_several_errors_in_one_row_are_all_reported() -> None:
    row = "PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;sto;k;EI 75;14.03.2025;B-2;"
    reason = parse_assets(read_csv(_csv(row).encode()), TODAY).rejected[0].reason

    assert "Średnica [mm]" in reason and "Klasa EI" in reason


def test_duplicate_number_keeps_first_and_rejects_later() -> None:
    parsed = parse_assets(read_csv(_csv(GOOD, GOOD.replace("110", "160")).encode()), TODAY)

    assert [line for line, _ in parsed.records] == [2]
    assert "first seen on line 2" in parsed.rejected[0].reason


@pytest.mark.parametrize(
    ("value", "expected"),
    [("EI60", "EI 60"), ("ei 120", "EI 120"), (" EI 30 ", "EI 30"), ("", None)],
)
def test_fire_rating_normalisation(value: str, expected: str | None) -> None:
    row = f"PI-1;przejście instalacyjne;B1;2;2.14;ściana;rura;110;k;{value};14.03.2025;B-2;"
    assert parse_assets(read_csv(_csv(row).encode()), TODAY).records[0][1].fire_rating == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("drzwi ppoż.", "drzwi_ppoz"), ("Klapa ppoż", "klapa_ppoz"), ("CZUJKA DYMU", "czujka_dymu")],
)
def test_asset_type_normalisation(value: str, expected: str) -> None:
    row = f"X-1;{value};B1;;;;;;;;;;"
    assert parse_assets(read_csv(_csv(row).encode()), TODAY).records[0][1].asset_type == expected


def test_lowercase_code_is_uppercased_and_diameter_with_zero_decimal_accepted() -> None:
    row = "pi-b1-2-015;przejście instalacyjne;b1;2;2.15;strop;rura;110,0;k;EI 60;2025-03-14;B-2;"
    record = parse_assets(read_csv(_csv(row).encode()), TODAY).records[0][1]

    assert (record.code, record.building, record.diameter_mm) == ("PI-B1-2-015", "B1", 110)

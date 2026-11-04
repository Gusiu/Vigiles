import csv
import shutil
import threading
from datetime import date
from pathlib import Path

import psycopg
import pytest

from harness.db import Database
from harness.eventually import eventually
from vigiles_importer.cli import main
from vigiles_importer.pipeline import Importer, Outcome

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"
TODAY = date(2026, 11, 4)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def importer(registry_db: Database, data_dir: Path) -> Importer:
    return Importer(registry_db.importer_url, data_dir, settle_s=0, today=lambda: TODAY)


def drop(importer: Importer, sample: str, name: str | None = None) -> Path:
    target = importer.inbox / (name or sample)
    shutil.copy(SAMPLES / sample, target)
    return target


def query(db: Database, sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(db.owner_url) as conn:
        return conn.execute(sql, params).fetchall()


def test_csv_file_lands_in_registry(importer: Importer, registry_db: Database) -> None:
    drop(importer, "zestawienie_B1.csv")

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.IMPORTED
    assert result.rows_ok == 11
    row = query(
        registry_db,
        "SELECT a.asset_type, s.code, a.room, a.partition, a.diameter_mm, a.fire_rating, "
        "a.installed_at, a.condition, a.rev, a.origin FROM assets a JOIN sites s ON s.id = a.site_id "
        "WHERE a.code = 'PI-B1-2-014'",
    )
    assert row == [
        ("przejscie_instalacyjne", "B1", "2.14", "ściana", 110, "EI 60", date(2025, 3, 14), "sprawny", 1, "importer")
    ]
    assert query(registry_db, "SELECT status, rows_ok, rows_rejected FROM import_runs") == [("completed", 11, 0)]
    assert not any(importer.inbox.iterdir())
    assert (importer.archive / "zestawienie_B1.csv").exists()


def test_same_file_twice_is_skipped(importer: Importer, registry_db: Database) -> None:
    drop(importer, "zestawienie_B1.csv")
    importer.process_inbox()
    drop(importer, "zestawienie_B1.csv", "zestawienie_B1_kopia.csv")

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.DUPLICATE
    assert query(registry_db, "SELECT count(*) FROM import_runs") == [(1,)]
    assert len(list(importer.archive.iterdir())) == 2


def test_reimport_with_same_data_keeps_rev(importer: Importer, registry_db: Database) -> None:
    drop(importer, "zestawienie_B1.csv")
    importer.process_inbox()
    # Same rows, different bytes (extra empty line) -> new run, no changes.
    content = (SAMPLES / "zestawienie_B1.csv").read_bytes() + b";;;;;;;;;;;;\r\n"
    (importer.inbox / "zestawienie_B1_v2.csv").write_bytes(content)

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.IMPORTED
    assert query(registry_db, "SELECT max(rev) FROM assets") == [(1,)]


def test_changed_row_bumps_rev_and_keeps_field_data(importer: Importer, registry_db: Database) -> None:
    drop(importer, "zestawienie_B1.csv")
    importer.process_inbox()
    with psycopg.connect(registry_db.owner_url) as conn:
        conn.execute(
            "UPDATE assets SET condition = 'do_naprawy', notes = 'pęknięta masa', "
            "last_inspected_at = now() WHERE code = 'PI-B1-2-014'"
        )
    content = (SAMPLES / "zestawienie_B1.csv").read_bytes().replace(b";2.14;\x9cciana;rura PVC", b";2.14A;\x9cciana;rura PVC")
    (importer.inbox / "zestawienie_B1_v2.csv").write_bytes(content)

    importer.process_inbox()

    [(room, condition, notes, inspected, rev)] = query(
        registry_db,
        "SELECT room, condition, notes, last_inspected_at IS NOT NULL, rev FROM assets WHERE code = 'PI-B1-2-014'",
    )
    assert (room, condition, notes, inspected) == ("2.14A", "do_naprawy", "pęknięta masa", True)
    assert rev == 3  # 1 import, 2 field update, 3 office change from the second import
    assert query(registry_db, "SELECT max(rev) FROM assets WHERE code <> 'PI-B1-2-014'") == [(1,)]


def test_file_with_errors_produces_report(importer: Importer, registry_db: Database) -> None:
    drop(importer, "zestawienie_B1_bledy.csv")

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.IMPORTED_WITH_ERRORS
    assert (result.rows_ok, result.rows_rejected) == (2, 6)
    assert sorted(r[0] for r in query(registry_db, "SELECT code FROM assets")) == ["PI-B1-4-001", "PI-B1-4-005"]
    report = importer.errors / "zestawienie_B1_bledy.rejected.csv"
    with report.open(encoding="cp1250", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=";"))
    assert rows[0][-2:] == ["wiersz", "powod"]
    reasons = " | ".join(r[-1] for r in rows[1:])
    for expected in ("missing", "duplicate", "EI 75", "future", "scientific notation", "whole number"):
        assert expected in reasons
    assert (importer.archive / "zestawienie_B1_bledy.csv").exists()
    assert query(registry_db, "SELECT status, rows_total FROM import_runs") == [("completed_with_errors", 8)]


def test_file_with_only_bad_rows_goes_to_error(importer: Importer) -> None:
    header = (SAMPLES / "zestawienie_B1.csv").read_bytes().split(b"\r\n")[0]
    (importer.inbox / "zle.csv").write_bytes(header + b"\r\n;czujka dymu;B1;;;;;;;;;;\r\n")

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.IMPORTED_WITH_ERRORS
    assert (importer.errors / "zle.csv").exists()
    assert (importer.errors / "zle.rejected.csv").exists()


def test_unreadable_file_fails_and_can_be_retried(importer: Importer, registry_db: Database) -> None:
    (importer.inbox / "broken.csv").write_bytes(b"Typ;Budynek\r\nczujka dymu;B1\r\n")

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.FAILED
    assert "missing required column" in (importer.errors / "broken.csv.error.txt").read_text()
    assert query(registry_db, "SELECT status FROM import_runs") == [("failed",)]
    # The same content dropped again is attempted again, not skipped as a duplicate.
    (importer.inbox / "broken.csv").write_bytes(b"Typ;Budynek\r\nczujka dymu;B1\r\n")
    assert importer.process_inbox()[0].outcome is Outcome.FAILED


def test_unsupported_extension(importer: Importer) -> None:
    (importer.inbox / "notes.txt").write_text("hello")

    assert importer.process_inbox()[0].outcome is Outcome.FAILED
    assert (importer.errors / "notes.txt").exists()


def test_site_config_after_asset_list(importer: Importer, registry_db: Database) -> None:
    drop(importer, "zestawienie_B1.csv")
    importer.process_inbox()
    drop(importer, "site-config.json")

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.IMPORTED
    assert query(registry_db, "SELECT name, address FROM sites WHERE code = 'B1'") == [
        ("Budynek biurowy B1", "ul. Przykładowa 1")
    ]
    devices = query(
        registry_db,
        "SELECT d.device_id, d.vendor, a.code, z.code, d.gateway_id FROM devices d "
        "JOIN assets a ON a.id = d.asset_id JOIN zones z ON z.id = a.zone_id ORDER BY 1",
    )
    assert devices == [
        ("A-000123", "alfa", "CZ-B1-3-001", "Z3", "gw-01"),
        ("A-000124", "alfa", "CZ-B1-3-002", "Z3", "gw-01"),
        ("BETA-7F3K2", "beta", "KL-B1-3-002", "Z3", "gw-01"),
    ]


def test_site_config_rejects_devices_of_unknown_assets(importer: Importer, registry_db: Database) -> None:
    drop(importer, "site-config.json")

    [result] = importer.process_inbox()

    assert result.outcome is Outcome.IMPORTED_WITH_ERRORS
    assert (result.rows_ok, result.rows_rejected) == (1, 3)
    report = (importer.errors / "site-config.rejected.json").read_text(encoding="utf-8")
    assert "import the asset list first" in report
    assert query(registry_db, "SELECT count(*) FROM devices") == [(0,)]


def test_database_down_leaves_file_in_inbox_until_next_cycle(registry_db: Database, data_dir: Path) -> None:
    unreachable = psycopg.conninfo.make_conninfo(registry_db.importer_url, port=1, connect_timeout=1)
    down = Importer(unreachable, data_dir, settle_s=0, today=lambda: TODAY)
    drop(down, "zestawienie_B1.csv")
    drop(down, "site-config.json")

    results = down.process_inbox()

    assert [r.outcome for r in results] == [Outcome.DEFERRED]
    assert "database unavailable" in results[0].message
    assert sorted(p.name for p in down.inbox.iterdir()) == ["site-config.json", "zestawienie_B1.csv"]

    up = Importer(registry_db.importer_url, data_dir, settle_s=0, today=lambda: TODAY)
    assert [r.outcome for r in up.process_inbox()] == [Outcome.IMPORTED, Outcome.IMPORTED]


def test_watch_mode_picks_up_new_files(importer: Importer, registry_db: Database) -> None:
    stop = threading.Event()
    worker = threading.Thread(target=importer.watch, args=(0.2, stop), daemon=True)
    worker.start()
    try:
        drop(importer, "zestawienie_B1.csv")

        def imported() -> None:
            assert query(registry_db, "SELECT count(*) FROM assets") == [(11,)]

        eventually(imported, timeout=10, interval=0.2)
    finally:
        stop.set()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_cli_run_exit_codes(
    registry_db: Database, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IMPORTER_DATABASE_URL", registry_db.importer_url)
    monkeypatch.setenv("IMPORTER_DATA_DIR", str(data_dir))

    assert main(["run", str(SAMPLES / "zestawienie_B1.csv")]) == 0
    assert (SAMPLES / "zestawienie_B1.csv").exists()  # files outside the inbox are not moved
    assert main(["run", str(SAMPLES / "zestawienie_B1.csv")]) == 0  # duplicate
    assert main(["run", str(SAMPLES / "zestawienie_B1_bledy.csv")]) == 2
    assert main(["run", "no-such-file.csv"]) == 1
    assert "duplicate" in capsys.readouterr().err

    monkeypatch.setenv("IMPORTER_DATABASE_URL", psycopg.conninfo.make_conninfo(registry_db.importer_url, port=1))
    assert main(["run", str(SAMPLES / "site-config.json")]) == 1

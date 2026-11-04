"""File transfer pipeline: inbox -> validation -> registry database.

Lifecycle of a file in ``data/``:

* ``inbox/``   - waiting; files stay here while the database is unavailable,
* ``archive/`` - imported (fully or partly), or identical to an earlier import,
* ``error/``   - nothing could be imported, plus the reports:
  ``<name>.rejected.csv`` / ``<name>.rejected.json`` with rejected rows and
  ``<name>.error.txt`` when the file as a whole was unreadable.

The database transaction commits before the file is moved. If the process
dies in between, the next cycle finds the same SHA-256 in ``import_runs``
and only archives the file, so nothing is imported twice.
"""

import hashlib
import json
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path

import psycopg

from vigiles_common.correlation import correlation_scope
from vigiles_common.logging import bound_context, get_logger
from vigiles_importer import store
from vigiles_importer.asset_rows import parse_assets
from vigiles_importer.csv_reader import FileFormatError, read_csv, write_csv
from vigiles_importer.site_config import parse_site_config

log = get_logger(__name__)

SUPPORTED = {".csv": "csv", ".json": "json"}
_IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload")


class Outcome(Enum):
    IMPORTED = "imported"
    IMPORTED_WITH_ERRORS = "imported_with_errors"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    DEFERRED = "deferred"  # database unavailable, file left for the next cycle


@dataclass(frozen=True)
class FileResult:
    path: Path
    outcome: Outcome
    rows_ok: int = 0
    rows_rejected: int = 0
    message: str = ""


class Importer:
    def __init__(
        self,
        database_url: str,
        data_dir: Path,
        settle_s: float = 2.0,
        today: Callable[[], date] = date.today,
        connect_timeout_s: int = 5,
    ) -> None:
        self.database_url = database_url
        self.data_dir = data_dir
        self.inbox = data_dir / "inbox"
        self.archive = data_dir / "archive"
        self.errors = data_dir / "error"
        self.settle_s = settle_s
        self.today = today
        self.connect_timeout_s = connect_timeout_s
        for directory in (self.inbox, self.archive, self.errors):
            directory.mkdir(parents=True, exist_ok=True)

    # -- polling -----------------------------------------------------------

    def pending_files(self) -> list[Path]:
        now = datetime.now().timestamp()
        files = []
        # Asset lists first: device entries in a site configuration refer to assets.
        order = {".csv": 0, ".json": 1}
        for path in sorted(self.inbox.iterdir(), key=lambda p: (order.get(p.suffix.lower(), 2), p.name)):
            if not path.is_file() or path.name.startswith(".") or path.name.endswith(_IGNORED_SUFFIXES):
                continue
            if now - path.stat().st_mtime < self.settle_s:
                continue  # probably still being written
            files.append(path)
        return files

    def process_inbox(self) -> list[FileResult]:
        results = []
        for path in self.pending_files():
            result = self.import_file(path)
            results.append(result)
            if result.outcome is Outcome.DEFERRED:
                break  # the database is down; the remaining files would fail too
        return results

    def watch(self, interval_s: float, stop: threading.Event) -> None:
        log.info("watch_started", inbox=str(self.inbox), interval_s=interval_s)
        while not stop.is_set():
            try:
                self.process_inbox()
            except Exception:  # keep polling; a single bad cycle must not stop the importer
                log.exception("watch_cycle_failed")
            stop.wait(interval_s)
        log.info("watch_stopped")

    # -- single file -------------------------------------------------------

    def import_file(self, path: Path) -> FileResult:
        with correlation_scope(), bound_context(file=path.name):
            return self._import_file(path)

    def _import_file(self, path: Path) -> FileResult:
        fmt = SUPPORTED.get(path.suffix.lower())
        if fmt is None:
            message = f"unsupported file type {path.suffix!r}; expected .csv or .json"
            self._write_error(path, message)
            self._move(path, self.errors)
            log.error("import_failed", reason=message)
            return FileResult(path, Outcome.FAILED, message=message)

        data = path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        started_at = datetime.now(UTC)
        log.info("import_started", format=fmt, sha256=sha256, bytes=len(data))

        try:
            with psycopg.connect(self.database_url, connect_timeout=self.connect_timeout_s) as conn:
                previous = store.finished_run(conn, sha256)
                if previous:
                    self._move(path, self.archive)
                    log.info("import_skipped_duplicate", previous_run_id=previous[0])
                    return FileResult(path, Outcome.DUPLICATE, message=f"same content as run {previous[0]}")
                conn.rollback()
                try:
                    return self._load(conn, path, data, fmt, sha256, started_at)
                except FileFormatError as exc:
                    conn.rollback()
                    with conn.transaction():
                        store.record_run(
                            conn, file_name=path.name, sha256=sha256, fmt=fmt, rows_total=0,
                            rows_ok=0, rows_rejected=0, status="failed",
                            started_at=started_at, error=str(exc),
                        )
                    self._write_error(path, str(exc))
                    self._move(path, self.errors)
                    log.error("import_failed", reason=str(exc))
                    return FileResult(path, Outcome.FAILED, message=str(exc))
        except psycopg.OperationalError as exc:
            message = f"database unavailable, file left in inbox for the next attempt: {_first_line(exc)}"
            log.error("import_deferred", reason=message)
            return FileResult(path, Outcome.DEFERRED, message=message)

    def _load(
        self, conn: psycopg.Connection, path: Path, data: bytes, fmt: str, sha256: str, started_at: datetime
    ) -> FileResult:
        if fmt == "csv":
            table = read_csv(data)
            parsed = parse_assets(table, self.today())
            if parsed.headers.ignored:
                log.warning("columns_ignored", columns=parsed.headers.ignored)
            with conn.transaction():
                store.begin_import(conn)
                stats = store.upsert_assets(conn, [r for _, r in parsed.records])
                rows_ok, rows_rejected = len(parsed.records), len(parsed.rejected)
                status = "completed" if rows_rejected == 0 else "completed_with_errors"
                run_id = store.record_run(
                    conn, file_name=path.name, sha256=sha256, fmt=fmt,
                    rows_total=rows_ok + rows_rejected, rows_ok=rows_ok,
                    rows_rejected=rows_rejected, status=status, started_at=started_at,
                )
            if parsed.rejected:
                self._write_rejected_csv(path, table, parsed.rejected)
        else:
            config = parse_site_config(data)
            with conn.transaction():
                store.begin_import(conn)
                stats = store.apply_site_config(conn, config)
                rejected = config.rejected + stats.rejected
                rows_rejected = len(rejected)
                rows_ok = config.total - rows_rejected
                status = "completed" if rows_rejected == 0 else "completed_with_errors"
                run_id = store.record_run(
                    conn, file_name=path.name, sha256=sha256, fmt=fmt, rows_total=config.total,
                    rows_ok=rows_ok, rows_rejected=rows_rejected, status=status,
                    started_at=started_at,
                )
            if rejected:
                self._write_rejected_json(path, rejected)

        target = self.errors if rows_ok == 0 and rows_rejected > 0 else self.archive
        self._move(path, target)
        log.info(
            "import_finished", run_id=run_id, status=status, rows_ok=rows_ok,
            rows_rejected=rows_rejected, inserted=stats.inserted, updated=stats.updated,
            unchanged=stats.unchanged,
        )
        outcome = Outcome.IMPORTED if rows_rejected == 0 else Outcome.IMPORTED_WITH_ERRORS
        return FileResult(path, outcome, rows_ok=rows_ok, rows_rejected=rows_rejected)

    # -- files -------------------------------------------------------------

    def _move(self, path: Path, directory: Path) -> None:
        """Move files that came from the inbox; leave files given by path elsewhere."""
        if path.parent.resolve() != self.inbox.resolve():
            return
        target = directory / path.name
        if target.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            target = directory / f"{path.stem}.{stamp}{path.suffix}"
        shutil.move(path, target)

    def _write_rejected_csv(self, path: Path, table, rejected) -> None:
        report = self.errors / f"{path.stem}.rejected.csv"
        rows = [
            [*cells, *[""] * (len(table.header) - len(cells)), str(r.line), r.reason]
            for r in rejected
            for cells in [r.cells[: len(table.header)]]
        ]
        write_csv(report, [*table.header, "wiersz", "powod"], rows, table.encoding, table.delimiter)
        log.warning("rows_rejected", report=str(report), count=len(rejected))

    def _write_rejected_json(self, path: Path, rejected) -> None:
        report = self.errors / f"{path.stem}.rejected.json"
        body = {"rejected": [{"path": r.path, "reason": r.reason, "item": r.item} for r in rejected]}
        report.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log.warning("items_rejected", report=str(report), count=len(rejected))

    def _write_error(self, path: Path, message: str) -> None:
        (self.errors / f"{path.name}.error.txt").write_text(message + "\n", encoding="utf-8")


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__

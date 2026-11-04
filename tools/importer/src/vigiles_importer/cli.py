"""Command line of the importer.

    vigiles-import run <file> [<file> ...]   import the given files now
    vigiles-import run                       import everything waiting in the inbox
    vigiles-import watch [--interval 10]     poll the inbox until stopped

Exit codes of ``run``: 0 - all imported or already imported, 2 - some rows
rejected (see data/error), 1 - a file failed or the database is unavailable.
"""

import argparse
import signal
import sys
import threading
from pathlib import Path

from vigiles_common.logging import configure_logging
from vigiles_importer.pipeline import Importer, Outcome
from vigiles_importer.settings import ImporterSettings


def build_importer(settings: ImporterSettings) -> Importer:
    return Importer(
        settings.importer_database_url,
        settings.importer_data_dir,
        settle_s=settings.importer_settle_s,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vigiles-import", description="Load CSV and JSON files into the registry")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="import files once")
    run.add_argument("files", nargs="*", type=Path, help="files to import (default: the inbox)")
    watch = commands.add_parser("watch", help="poll the inbox periodically")
    watch.add_argument("--interval", type=float, help="seconds between polls (default 10)")
    args = parser.parse_args(argv)

    settings = ImporterSettings()
    configure_logging(settings.service_name, settings.log_level, settings.log_format)
    importer = build_importer(settings)

    if args.command == "watch":
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        importer.watch(args.interval or settings.importer_poll_interval_s, stop)
        return 0

    if args.files:
        missing = [str(f) for f in args.files if not f.is_file()]
        if missing:
            print(f"file not found: {', '.join(missing)}", file=sys.stderr)
            return 1
        results = []
        for path in args.files:
            results.append(importer.import_file(path))
            if results[-1].outcome is Outcome.DEFERRED:
                break
    else:
        importer.settle_s = 0
        results = importer.process_inbox()

    for result in results:
        details = f" ({result.message})" if result.message else ""
        counts = f" ok={result.rows_ok} rejected={result.rows_rejected}" if result.rows_ok or result.rows_rejected else ""
        print(f"{result.path.name}: {result.outcome.value}{counts}{details}", file=sys.stderr)

    outcomes = {r.outcome for r in results}
    if outcomes & {Outcome.FAILED, Outcome.DEFERRED}:
        return 1
    if Outcome.IMPORTED_WITH_ERRORS in outcomes:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

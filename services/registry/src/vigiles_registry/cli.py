"""Registry command line: ``vigiles-registry migrate``."""

import argparse
from pathlib import Path

from vigiles_common.logging import configure_logging, get_logger
from vigiles_common.migrations import apply_migrations
from vigiles_registry.settings import RegistrySettings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vigiles-registry")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending database migrations")
    parser.parse_args(argv)

    settings = RegistrySettings()
    configure_logging(settings.service_name, settings.log_level, settings.log_format)
    applied = apply_migrations(settings.registry_database_url, MIGRATIONS_DIR)
    get_logger().info("migrations_done", applied=applied)

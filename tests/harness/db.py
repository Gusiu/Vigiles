"""Throwaway PostgreSQL databases for component tests.

Requires the roles from infra/postgres/init.sql and the connection
settings from .env (see .env.example). Run ``python scripts/local_up.py``
first in a local environment.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from psycopg import sql
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pg_admin_url: str
    registry_database_url: str
    importer_database_url: str


def with_database(url: str, name: str) -> str:
    info = psycopg.conninfo.conninfo_to_dict(url)
    info["dbname"] = name
    return psycopg.conninfo.make_conninfo(**info)


@dataclass(frozen=True)
class Database:
    name: str
    admin_url: str
    owner_url: str
    importer_url: str


@contextmanager
def throwaway_database(settings: DatabaseSettings, owner: str = "registry_app") -> Iterator[Database]:
    name = f"vigiles_test_{uuid.uuid4().hex[:12]}"
    ident = sql.Identifier(name)
    with psycopg.connect(settings.pg_admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(ident, sql.Identifier(owner)))
        admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(ident))
        admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO importer").format(ident))
    try:
        yield Database(
            name=name,
            admin_url=with_database(settings.pg_admin_url, name),
            owner_url=with_database(settings.registry_database_url, name),
            importer_url=with_database(settings.importer_database_url, name),
        )
    finally:
        with psycopg.connect(settings.pg_admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))

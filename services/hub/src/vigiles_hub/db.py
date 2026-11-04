from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from vigiles_common.problems import ServiceUnavailable


class HubDatabase:
    def __init__(self, url: str) -> None:
        self.pool = ConnectionPool(url, min_size=1, max_size=10, open=False, timeout=5,
                                   kwargs={"row_factory": dict_row, "connect_timeout": 3})

    def open(self) -> None:
        self.pool.open(wait=False)

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with self.pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        with self.pool.connection() as conn, conn.transaction():
            yield conn

    @contextmanager
    def api_transaction(self) -> Iterator[psycopg.Connection]:
        try:
            with self.transaction() as conn:
                yield conn
        except (psycopg.OperationalError, PoolTimeout) as exc:
            raise ServiceUnavailable("hub database unavailable") from exc

    def ping(self) -> None:
        with self.pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")

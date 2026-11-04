"""Connection handling. Every write transaction sets ``vigiles.origin`` so
the row triggers know who made the change."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from vigiles_common.problems import ServiceUnavailable


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10) -> None:
        self.pool = ConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row, "connect_timeout": 3},
            open=False,
            timeout=5,
        )

    def open(self) -> None:
        self.pool.open(wait=False)

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def transaction(self, origin: str | None = None) -> Iterator[psycopg.Connection]:
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    if origin:
                        conn.execute("SELECT set_config('vigiles.origin', %s, true)", (origin,))
                    yield conn
        except (psycopg.OperationalError, TimeoutError) as exc:
            raise ServiceUnavailable("registry database unavailable") from exc
        except Exception as exc:
            from psycopg_pool import PoolTimeout

            if isinstance(exc, PoolTimeout):
                raise ServiceUnavailable("registry database unavailable") from exc
            raise

    def ping(self) -> None:
        with self.pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")

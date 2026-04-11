from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover
    psycopg = None
    dict_row = None


def ensure_psycopg_available() -> None:
    if psycopg is None or dict_row is None:  # pragma: no cover
        raise RuntimeError(
            "Postgres backend requires psycopg. Install psycopg before using STORAGE_BACKEND=postgres."
        )


@contextmanager
def postgres_connection(dsn: str) -> Iterator["psycopg.Connection"]:
    ensure_psycopg_available()
    connection = psycopg.connect(dsn, row_factory=dict_row)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()

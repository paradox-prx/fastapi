import os
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

DB_DSN = os.getenv("SUPABASE_DB_DSN", "").strip()
if not DB_DSN:
    raise RuntimeError("Missing SUPABASE_DB_DSN env var.")

_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=DB_DSN,
            min_size=1,
            max_size=5,
            kwargs={
                "autocommit": True,
                "prepare_threshold": None,
            },
        )
    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def fetch_all(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def execute_returning(sql: str, params: Iterable[Any] = ()) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Expected row from RETURNING query.")
            return row

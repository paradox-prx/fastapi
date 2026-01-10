import os
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import Json, RealDictCursor

DB_DSN = os.getenv("SUPABASE_DB_DSN", "").strip()
if not DB_DSN:
    raise RuntimeError("Missing SUPABASE_DB_DSN env var.")

_pool: Optional[pool.SimpleConnectionPool] = None


def get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.SimpleConnectionPool(
            1,
            5,
            dsn=DB_DSN,
        )
    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        yield conn
    finally:
        pool.putconn(conn)


def _adapt_params(params: Iterable[Any]) -> tuple:
    adapted = []
    for value in params:
        if isinstance(value, (dict, list)):
            adapted.append(Json(value))
        else:
            adapted.append(value)
    return tuple(adapted)


def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, _adapt_params(params))
            row = cur.fetchone()
            return dict(row) if row else None


def fetch_all(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, _adapt_params(params))
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, _adapt_params(params))


def execute_returning(sql: str, params: Iterable[Any] = ()) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, _adapt_params(params))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Expected row from RETURNING query.")
            return dict(row)

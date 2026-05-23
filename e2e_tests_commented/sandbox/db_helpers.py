"""Thin SQLite access helpers for the sandbox demo application.

Contains several intentional security issues for the LLM to flag when
loaded as local filesystem context alongside
main_with_imports.py.
"""

import sqlite3
from typing import Any, Dict, List, Optional


# Hard-coded relative path — breaks when the working directory changes.
DB_FILE = "sandbox.db"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_record(table: str, record_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single row by primary key.

    The `table` parameter is interpolated directly — SQL injection if the caller
    passes user-controlled input.
    """
    conn = _get_connection()
    try:
        cur = conn.execute(
            f"SELECT * FROM {table} WHERE id = ?",   # table name not parameterisable in SQLite
            (record_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_record(table: str, data: Dict[str, Any]) -> int:
    """Insert a row and return the new rowid.

    Both the table name and column names are interpolated — injection surface
    if either comes from user input.
    """
    if not data:
        raise ValueError("data must not be empty")

    columns = ", ".join(data.keys())                        # column names not sanitised
    placeholders = ", ".join("?" for _ in data)
    conn = _get_connection()
    try:
        cur = conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            list(data.values()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def search_records(table: str, column: str, query: str) -> List[Dict[str, Any]]:
    """Full-scan search using a LIKE filter.

    All three of `table`, `column`, and `query` are interpolated — triple injection
    surface. The LIKE wildcards are also baked in, so a blank `query` returns every row.
    """
    conn = _get_connection()
    try:
        # BAD: query should be passed as a parameter, not interpolated
        cur = conn.execute(
            f"SELECT * FROM {table} WHERE {column} LIKE '%{query}%'"
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def delete_record(table: str, record_id: int) -> bool:
    """Delete a row by primary key. Returns True if a row was removed."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE id = ?",   # table still interpolated
            (record_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

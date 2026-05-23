"""Thin SQLite access helpers for the sandbox demo application.

"""

import sqlite3
from typing import Any, Dict, List, Optional


DB_FILE = "sandbox.db"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_record(table: str, record_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single row by primary key.

    """
    conn = _get_connection()
    try:
        cur = conn.execute(
            f"SELECT * FROM {table} WHERE id = ?",
            (record_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_record(table: str, data: Dict[str, Any]) -> int:
    """Insert a row and return the new rowid.

    """
    if not data:
        raise ValueError("data must not be empty")

    columns = ", ".join(data.keys())
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

    """
    conn = _get_connection()
    try:
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
            f"DELETE FROM {table} WHERE id = ?",
            (record_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

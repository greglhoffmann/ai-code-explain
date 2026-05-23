"""Database models and persistence layer using SQLModel + SQLite."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select


class Snippet(SQLModel, table=True):
    """Persisted record for a single code analysis run."""

    id: Optional[int] = Field(default=None, primary_key=True)
    language: str
    original_code: str
    explanation: Optional[str] = None
    optimized_code: Optional[str] = None
    # Stored as serialized JSON strings to remain schema-stable
    static_complexity_json: Optional[str] = None
    llm_complexity_json: Optional[str] = None
    semgrep_findings_json: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Engine / table bootstrap
# ---------------------------------------------------------------------------

_DB_PATH = Path.home() / ".ai_code_explain" / "history.db"


def get_engine(db_path: Path = _DB_PATH):
    """Return a SQLite engine, creating the database file if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", echo=False)


def create_tables(db_path: Path = _DB_PATH) -> None:
    """Create all tables (idempotent)."""
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------


def save_snippet(snippet: Snippet, db_path: Path = _DB_PATH) -> Snippet:
    """Persist a Snippet record and return it with its assigned id."""
    engine = get_engine(db_path)
    with Session(engine) as session:
        session.add(snippet)
        session.commit()
        session.refresh(snippet)
        return snippet


def load_all_snippets(db_path: Path = _DB_PATH) -> list[Snippet]:
    """Return all snippets ordered by creation time descending."""
    engine = get_engine(db_path)
    with Session(engine) as session:
        results = session.exec(
            select(Snippet).order_by(Snippet.created_at.desc())  # type: ignore[arg-type]
        ).all()
        return list(results)


def load_snippet_by_id(snippet_id: int, db_path: Path = _DB_PATH) -> Optional[Snippet]:
    """Return a single Snippet by primary key, or None if not found."""
    engine = get_engine(db_path)
    with Session(engine) as session:
        return session.get(Snippet, snippet_id)


def update_snippet(snippet: Snippet, db_path: Path = _DB_PATH) -> Snippet:
    """Merge updated fields into an existing Snippet record."""
    engine = get_engine(db_path)
    with Session(engine) as session:
        session.add(snippet)
        session.commit()
        session.refresh(snippet)
        return snippet


# ---------------------------------------------------------------------------
# JSON convenience helpers
# ---------------------------------------------------------------------------


def deserialize_json_field(value: Optional[str]) -> Any:
    """Safely deserialize a JSON string field; returns None if blank/invalid."""
    if not value or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None

"""Tests for the SQLite persistence layer (database.py)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from ai_code_explain.database import (
    Snippet,
    create_tables,
    deserialize_json_field,
    load_all_snippets,
    load_snippet_by_id,
    save_snippet,
    update_snippet,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snippet(**kwargs) -> Snippet:
    defaults = dict(
        language="python",
        original_code="def foo(): pass",
        explanation="Does nothing.",
        optimized_code="def foo(): ...",
        static_complexity_json=json.dumps({"static_estimate": {"time": "O(1)", "space": "O(1)", "confidence": "high"}}),
        llm_complexity_json=json.dumps({"llm_adjusted_estimate": {"time": "O(1)", "space": "O(1)", "confidence": "high", "reasoning": "trivial"}}),
        semgrep_findings_json=json.dumps([]),
    )
    defaults.update(kwargs)
    return Snippet(**defaults)


# ---------------------------------------------------------------------------
# create_tables
# ---------------------------------------------------------------------------


class TestCreateTables:
    def test_creates_db_file(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        assert tmp_db_path.exists()

    def test_idempotent(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        create_tables(tmp_db_path)  # second call must not raise
        assert tmp_db_path.exists()


# ---------------------------------------------------------------------------
# save_snippet / load_snippet_by_id
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    def test_save_assigns_id(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        snippet = _make_snippet()
        saved = save_snippet(snippet, tmp_db_path)
        assert saved.id is not None
        assert saved.id > 0

    def test_load_by_id_found(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        saved = save_snippet(_make_snippet(), tmp_db_path)
        loaded = load_snippet_by_id(saved.id, tmp_db_path)
        assert loaded is not None
        assert loaded.id == saved.id
        assert loaded.original_code == "def foo(): pass"

    def test_load_by_id_not_found(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        result = load_snippet_by_id(9999, tmp_db_path)
        assert result is None

    def test_roundtrip_preserves_json_fields(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        original_static = json.dumps({"static_estimate": {"time": "O(n)", "space": "O(1)", "confidence": "high"}})
        snippet = _make_snippet(static_complexity_json=original_static)
        saved = save_snippet(snippet, tmp_db_path)
        loaded = load_snippet_by_id(saved.id, tmp_db_path)
        assert loaded.static_complexity_json == original_static

    def test_language_stored_correctly(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        saved = save_snippet(_make_snippet(language="javascript"), tmp_db_path)
        loaded = load_snippet_by_id(saved.id, tmp_db_path)
        assert loaded.language == "javascript"

    def test_roundtrip_preserves_llm_optimization_payload(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        snippet = _make_snippet(
            llm_complexity_json=json.dumps(
                {
                    "llm_adjusted_estimate": {"time": "O(n)", "space": "O(1)", "confidence": "high", "reasoning": "fine"},
                    "improvements": [
                        {
                            "category": "readability",
                            "impact": "low",
                            "behavior_change_risk": "low",
                            "description": "Use const.",
                            "tradeoffs": "None.",
                            "optimized_code": "const x = 1;",
                        }
                    ],
                }
            )
        )
        saved = save_snippet(snippet, tmp_db_path)
        loaded = load_snippet_by_id(saved.id, tmp_db_path)
        assert loaded is not None
        loaded_payload = deserialize_json_field(loaded.llm_complexity_json)
        assert loaded_payload["improvements"][0]["description"] == "Use const."


# ---------------------------------------------------------------------------
# load_all_snippets
# ---------------------------------------------------------------------------


class TestLoadAllSnippets:
    def test_empty_database(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        assert load_all_snippets(tmp_db_path) == []

    def test_returns_all_snippets(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        save_snippet(_make_snippet(original_code="code 1"), tmp_db_path)
        save_snippet(_make_snippet(original_code="code 2"), tmp_db_path)
        snippets = load_all_snippets(tmp_db_path)
        assert len(snippets) == 2

    def test_ordered_by_created_at_desc(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        s1 = save_snippet(_make_snippet(original_code="first"), tmp_db_path)
        s2 = save_snippet(_make_snippet(original_code="second"), tmp_db_path)
        snippets = load_all_snippets(tmp_db_path)
        # Most recent first
        assert snippets[0].id == s2.id
        assert snippets[1].id == s1.id


# ---------------------------------------------------------------------------
# update_snippet
# ---------------------------------------------------------------------------


class TestUpdateSnippet:
    def test_update_modifies_field(self, tmp_db_path: Path):
        create_tables(tmp_db_path)
        saved = save_snippet(_make_snippet(), tmp_db_path)
        saved.explanation = "Updated explanation."
        updated = update_snippet(saved, tmp_db_path)
        reloaded = load_snippet_by_id(updated.id, tmp_db_path)
        assert reloaded.explanation == "Updated explanation."


# ---------------------------------------------------------------------------
# deserialize_json_field
# ---------------------------------------------------------------------------


class TestDeserializeJsonField:
    def test_none_input(self):
        assert deserialize_json_field(None) is None

    def test_empty_string(self):
        assert deserialize_json_field("") is None

    def test_valid_json_object(self):
        result = deserialize_json_field('{"key": "value"}')
        assert result == {"key": "value"}

    def test_valid_json_array(self):
        result = deserialize_json_field('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_invalid_json_returns_none(self):
        assert deserialize_json_field("not-json{{{") is None

    def test_whitespace_only_string(self):
        assert deserialize_json_field("   ") is None

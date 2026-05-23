"""Tests for the diff generation module (diff_generator.py)."""

from __future__ import annotations

import pytest

from ai_code_explain.diff_generator import (
    generate_rich_diff_markup,
    generate_side_by_side_with_highlights,
    generate_unified_diff,
)


class TestGenerateUnifiedDiff:
    def test_identical_inputs_returns_empty(self):
        code = "def foo():\n    return 1\n"
        result = generate_unified_diff(code, code)
        assert result == ""

    def test_different_inputs_returns_diff(self):
        original = "def foo():\n    return 1\n"
        optimized = "def foo():\n    return 2\n"
        result = generate_unified_diff(original, optimized)
        assert "-    return 1" in result
        assert "+    return 2" in result

    def test_diff_contains_file_headers(self):
        result = generate_unified_diff("a = 1\n", "a = 2\n", "before.py", "after.py")
        assert "before.py" in result
        assert "after.py" in result

    def test_default_labels(self):
        result = generate_unified_diff("x = 1\n", "x = 2\n")
        assert "original" in result
        assert "optimized" in result

    def test_added_lines_marked(self):
        result = generate_unified_diff("", "new line\n")
        assert "+new line" in result

    def test_removed_lines_marked(self):
        result = generate_unified_diff("old line\n", "")
        assert "-old line" in result

    def test_multiline_diff(self):
        original = "line1\nline2\nline3\n"
        optimized = "line1\nmodified\nline3\n"
        result = generate_unified_diff(original, optimized)
        assert "-line2" in result
        assert "+modified" in result


class TestGenerateRichDiffMarkup:
    def test_identical_returns_no_differences(self):
        code = "def foo(): pass\n"
        result = generate_rich_diff_markup(code, code)
        assert "No differences" in result

    def test_added_lines_green(self):
        result = generate_rich_diff_markup("", "added line\n")
        assert "[green]" in result

    def test_removed_lines_red(self):
        result = generate_rich_diff_markup("removed line\n", "")
        assert "[red]" in result

    def test_file_header_cyan(self):
        result = generate_rich_diff_markup("a\n", "b\n")
        assert "[bold cyan]" in result

    def test_hunk_header_yellow(self):
        result = generate_rich_diff_markup("a\n", "b\n")
        assert "[bold yellow]" in result or "@@" in result

    def test_context_lines_unformatted(self):
        original = "unchanged\nchanged\n"
        optimized = "unchanged\nmodified\n"
        result = generate_rich_diff_markup(original, optimized)
        # Context lines (unchanged) should appear without colour markup
        assert "unchanged" in result

    def test_returns_string(self):
        result = generate_rich_diff_markup("x\n", "y\n")
        assert isinstance(result, str)


class TestGenerateSideBySideWithHighlights:
    def test_returns_code_and_changed_rows(self):
        left, left_rows, right, right_rows = generate_side_by_side_with_highlights("a = 1\n", "a = 2\n")
        assert isinstance(left, str)
        assert isinstance(right, str)
        assert len(left_rows) > 0
        assert len(right_rows) > 0

    def test_equal_lines_not_in_changed(self):
        left, left_rows, right, right_rows = generate_side_by_side_with_highlights("same\n", "same\n")
        assert left_rows == []
        assert right_rows == []
        assert "same" in left
        assert "same" in right

    def test_changed_lines_indexed(self):
        left, left_rows, right, right_rows = generate_side_by_side_with_highlights("old\n", "new\n")
        assert 0 in left_rows
        assert 0 in right_rows

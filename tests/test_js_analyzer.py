"""Tests for the JavaScript tree-sitter analyzer (analyzers/js_analyzer.py).

Tests are written to cover both the happy path (tree-sitter installed) and
the graceful-degradation path (tree-sitter not available).  The
``_TREE_SITTER_AVAILABLE`` flag is patched for the degradation tests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_code_explain.analyzers import js_analyzer
from ai_code_explain.analyzers.js_analyzer import analyze_javascript
from ai_code_explain.models import StaticMetadata


# ---------------------------------------------------------------------------
# Graceful degradation when tree-sitter is absent
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_returns_static_metadata_when_unavailable(self):
        with patch.object(js_analyzer, "_TREE_SITTER_AVAILABLE", False):
            result = analyze_javascript("const x = 1;")
        assert isinstance(result, StaticMetadata)
        assert result.language == "javascript"

    def test_unknown_complexity_when_unavailable(self):
        with patch.object(js_analyzer, "_TREE_SITTER_AVAILABLE", False):
            result = analyze_javascript("const x = 1;")
        assert result.baseline_time_complexity == "unknown"
        assert result.baseline_space_complexity == "unknown"

    def test_empty_hotspots_when_unavailable(self):
        with patch.object(js_analyzer, "_TREE_SITTER_AVAILABLE", False):
            result = analyze_javascript("function f() {}")
        assert result.hotspots == []


# ---------------------------------------------------------------------------
# Happy-path tests (skip when tree-sitter genuinely not installed)
# ---------------------------------------------------------------------------

pytestmark_ts = pytest.mark.skipif(
    not js_analyzer._TREE_SITTER_AVAILABLE,
    reason="tree-sitter-javascript not installed",
)


@pytestmark_ts
class TestJSAnalyzerHappyPath:
    def test_returns_static_metadata(self):
        result = analyze_javascript("const x = 1;")
        assert isinstance(result, StaticMetadata)
        assert result.language == "javascript"

    def test_function_declaration_detected(self):
        code = "function greet(name) { return 'Hello ' + name; }"
        result = analyze_javascript(code)
        assert "greet" in result.function_names
        assert any(h.node_type == "function" for h in result.hotspots)

    def test_arrow_function_detected(self):
        code = "const double = (x) => x * 2;"
        result = analyze_javascript(code)
        assert any(h.node_type == "function" for h in result.hotspots)

    def test_for_loop_counted(self):
        code = "for (let i = 0; i < 10; i++) { console.log(i); }"
        result = analyze_javascript(code)
        assert result.loops >= 1

    def test_while_loop_counted(self):
        code = "let i = 0; while (i < 5) { i++; }"
        result = analyze_javascript(code)
        assert result.loops >= 1

    def test_nested_loop_flag(self):
        code = (
            "for (let i = 0; i < n; i++) {\n"
            "  for (let j = 0; j < n; j++) {\n"
            "    sum += matrix[i][j];\n"
            "  }\n"
            "}\n"
        )
        result = analyze_javascript(code)
        assert result.nested_loops is True
        assert any(h.node_type == "nested_loop" for h in result.hotspots)

    def test_sort_call_detected(self):
        code = "const sorted = arr.sort((a, b) => a - b);"
        result = analyze_javascript(code)
        assert result.sort_operations >= 1
        assert any(h.node_type == "sort" for h in result.hotspots)

    def test_array_transform_detected(self):
        code = "const doubled = items.map(x => x * 2);"
        result = analyze_javascript(code)
        assert result.comprehensions >= 1  # array transforms stored in comprehensions field

    def test_object_literal_hashmap_usage(self):
        code = "const obj = { key: 'value' };"
        result = analyze_javascript(code)
        assert result.hashmap_usage is True

    def test_no_loops_baseline_O1(self):
        code = "const x = 1 + 2;"
        result = analyze_javascript(code)
        assert result.baseline_time_complexity == "O(1)"

    def test_single_loop_baseline_On(self):
        code = "for (let i = 0; i < n; i++) { doSomething(); }"
        result = analyze_javascript(code)
        assert result.baseline_time_complexity == "O(n)"

    def test_nested_loop_baseline_On2(self):
        code = (
            "for (let i = 0; i < n; i++) {\n"
            "  for (let j = 0; j < n; j++) {}\n"
            "}\n"
        )
        result = analyze_javascript(code)
        assert result.baseline_time_complexity == "O(n^2)"

    def test_sort_baseline_Onlogn(self):
        code = "arr.sort();"
        result = analyze_javascript(code)
        assert result.baseline_time_complexity == "O(n log n)"

    def test_hotspot_node_ids_unique(self):
        code = (
            "function process(items) {\n"
            "  for (let i = 0; i < items.length; i++) {\n"
            "    for (let j = 0; j < items.length; j++) {\n"
            "      items.sort();\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
        result = analyze_javascript(code)
        ids = [h.node_id for h in result.hotspots]
        assert len(ids) == len(set(ids))

    def test_import_statement_captured(self):
        code = "import fs from 'fs';\nconst x = 1;"
        result = analyze_javascript(code)
        assert "fs" in result.imports

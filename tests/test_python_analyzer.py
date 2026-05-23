"""Tests for the Python AST static analyzer (analyzers/python_analyzer.py)."""

from __future__ import annotations

import pytest

from ai_code_explain.analyzers.python_analyzer import analyze_python
from ai_code_explain.models import ASTSpan, StaticMetadata


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _hotspot_types(meta: StaticMetadata) -> list[str]:
    return [h.node_type for h in meta.hotspots]


def _hotspot_names(meta: StaticMetadata) -> list[str]:
    return [h.node_id for h in meta.hotspots]


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_static_metadata(self, simple_python_code: str):
        result = analyze_python(simple_python_code)
        assert isinstance(result, StaticMetadata)
        assert result.language == "python"


# ---------------------------------------------------------------------------
# Function detection
# ---------------------------------------------------------------------------


class TestFunctionDetection:
    def test_single_function(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert "add" in meta.function_names

    def test_multiple_functions(self):
        code = "def foo(): pass\ndef bar(): pass\n"
        meta = analyze_python(code)
        assert "foo" in meta.function_names
        assert "bar" in meta.function_names

    def test_function_hotspot_recorded(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert "function" in _hotspot_types(meta)

    def test_function_hotspot_has_correct_name(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        func_hotspots = [h for h in meta.hotspots if h.node_type == "function"]
        assert func_hotspots[0].name == "add"

    def test_function_hotspot_lines(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        func_hotspot = next(h for h in meta.hotspots if h.node_type == "function")
        assert func_hotspot.start_line == 1
        assert func_hotspot.end_line >= 1

    def test_async_function_detected(self):
        code = "async def fetch(url):\n    return await get(url)\n"
        meta = analyze_python(code)
        assert "fetch" in meta.function_names
        assert "function" in _hotspot_types(meta)


# ---------------------------------------------------------------------------
# Loop detection
# ---------------------------------------------------------------------------


class TestLoopDetection:
    def test_for_loop_counted(self, loop_python_code: str):
        meta = analyze_python(loop_python_code)
        assert meta.loops >= 1

    def test_while_loop_counted(self):
        code = "def run():\n    i = 0\n    while i < 10:\n        i += 1\n"
        meta = analyze_python(code)
        assert meta.loops >= 1

    def test_loop_hotspot_recorded(self, loop_python_code: str):
        meta = analyze_python(loop_python_code)
        assert "loop" in _hotspot_types(meta)

    def test_no_loops_when_absent(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert meta.loops == 0
        assert meta.nested_loops is False


# ---------------------------------------------------------------------------
# Nested loop detection
# ---------------------------------------------------------------------------


class TestNestedLoopDetection:
    def test_nested_loop_flag(self, nested_loop_python_code: str):
        meta = analyze_python(nested_loop_python_code)
        assert meta.nested_loops is True

    def test_nested_loop_hotspot(self, nested_loop_python_code: str):
        meta = analyze_python(nested_loop_python_code)
        assert "nested_loop" in _hotspot_types(meta)

    def test_single_loop_not_nested(self, loop_python_code: str):
        meta = analyze_python(loop_python_code)
        assert meta.nested_loops is False
        assert "nested_loop" not in _hotspot_types(meta)

    def test_loop_count_includes_nested(self, nested_loop_python_code: str):
        meta = analyze_python(nested_loop_python_code)
        assert meta.loops >= 2  # both loops counted


# ---------------------------------------------------------------------------
# Recursion detection
# ---------------------------------------------------------------------------


class TestRecursionDetection:
    def test_direct_recursion_detected(self, recursive_python_code: str):
        meta = analyze_python(recursive_python_code)
        assert meta.recursive_calls is True

    def test_recursion_hotspot(self, recursive_python_code: str):
        meta = analyze_python(recursive_python_code)
        assert "recursion" in _hotspot_types(meta)

    def test_no_recursion_for_non_recursive(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert meta.recursive_calls is False

    def test_method_call_not_false_positive(self):
        code = "def process(items):\n    other_func(items)\n"
        meta = analyze_python(code)
        assert meta.recursive_calls is False


# ---------------------------------------------------------------------------
# Sort detection
# ---------------------------------------------------------------------------


class TestSortDetection:
    def test_dot_sort_method(self):
        code = "def f(data):\n    data.sort()\n"
        meta = analyze_python(code)
        assert meta.sort_operations >= 1

    def test_sorted_builtin(self):
        code = "result = sorted([3, 1, 2])\n"
        meta = analyze_python(code)
        assert meta.sort_operations >= 1

    def test_both_sort_methods(self, sort_python_code: str):
        meta = analyze_python(sort_python_code)
        assert meta.sort_operations >= 2

    def test_sort_hotspot(self, sort_python_code: str):
        meta = analyze_python(sort_python_code)
        assert "sort" in _hotspot_types(meta)

    def test_no_sort_when_absent(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert meta.sort_operations == 0


# ---------------------------------------------------------------------------
# Comprehension detection
# ---------------------------------------------------------------------------


class TestComprehensionDetection:
    def test_list_comprehension(self):
        code = "squares = [x**2 for x in range(10)]\n"
        meta = analyze_python(code)
        assert meta.comprehensions >= 1

    def test_set_comprehension(self):
        code = "evens = {x for x in range(10) if x % 2 == 0}\n"
        meta = analyze_python(code)
        assert meta.comprehensions >= 1

    def test_dict_comprehension(self):
        code = "mapping = {k: v for k, v in items}\n"
        meta = analyze_python(code)
        assert meta.comprehensions >= 1

    def test_generator_expression(self):
        code = "total = sum(x**2 for x in range(10))\n"
        meta = analyze_python(code)
        assert meta.comprehensions >= 1

    def test_all_comprehension_types(self, comprehension_python_code: str):
        meta = analyze_python(comprehension_python_code)
        assert meta.comprehensions == 4  # list, set, dict, generator

    def test_comprehension_hotspot(self):
        code = "squares = [x**2 for x in range(10)]\n"
        meta = analyze_python(code)
        assert "comprehension" in _hotspot_types(meta)


# ---------------------------------------------------------------------------
# Hashmap detection
# ---------------------------------------------------------------------------


class TestHashmapDetection:
    def test_dict_literal(self, hashmap_python_code: str):
        meta = analyze_python(hashmap_python_code)
        assert meta.hashmap_usage is True

    def test_dict_constructor(self):
        code = "d = dict()\n"
        meta = analyze_python(code)
        assert meta.hashmap_usage is True

    def test_no_hashmap_when_absent(self):
        code = "x = [1, 2, 3]\n"
        meta = analyze_python(code)
        assert meta.hashmap_usage is False


# ---------------------------------------------------------------------------
# Import detection
# ---------------------------------------------------------------------------


class TestImportDetection:
    def test_simple_import(self):
        code = "import os\n"
        meta = analyze_python(code)
        assert "os" in meta.imports

    def test_from_import(self):
        code = "from pathlib import Path\n"
        meta = analyze_python(code)
        assert any("Path" in imp for imp in meta.imports)

    def test_multiple_imports(self):
        code = "import os\nimport sys\nfrom pathlib import Path\n"
        meta = analyze_python(code)
        assert "os" in meta.imports
        assert "sys" in meta.imports

    def test_no_imports_when_absent(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert meta.imports == []


# ---------------------------------------------------------------------------
# Baseline complexity estimation
# ---------------------------------------------------------------------------


class TestBaselineComplexity:
    def test_no_loops_is_O1(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert meta.baseline_time_complexity == "O(1)"

    def test_single_loop_is_On(self, loop_python_code: str):
        meta = analyze_python(loop_python_code)
        assert meta.baseline_time_complexity == "O(n)"

    def test_nested_loop_is_On2(self, nested_loop_python_code: str):
        meta = analyze_python(nested_loop_python_code)
        assert meta.baseline_time_complexity == "O(n^2)"

    def test_sort_is_Onlogn(self):
        code = "def f(data):\n    return sorted(data)\n"
        meta = analyze_python(code)
        assert meta.baseline_time_complexity == "O(n log n)"

    def test_recursion_is_exponential(self, recursive_python_code: str):
        meta = analyze_python(recursive_python_code)
        assert "2^n" in meta.baseline_time_complexity or "n^2" in meta.baseline_time_complexity

    def test_hashmap_gives_On_space(self, hashmap_python_code: str):
        meta = analyze_python(hashmap_python_code)
        assert meta.baseline_space_complexity == "O(n)"

    def test_nested_loop_with_sort_complexity(self):
        code = (
            "def f(data):\n"
            "    for i in data:\n"
            "        for j in data:\n"
            "            data.sort()\n"
        )
        meta = analyze_python(code)
        assert "n^2" in meta.baseline_time_complexity

    def test_no_structures_gives_O1_space(self, simple_python_code: str):
        meta = analyze_python(simple_python_code)
        assert meta.baseline_space_complexity == "O(1)"


# ---------------------------------------------------------------------------
# Hotspot uniqueness
# ---------------------------------------------------------------------------


class TestHotspotUniqueness:
    def test_node_ids_are_unique(self, nested_loop_python_code: str):
        meta = analyze_python(nested_loop_python_code)
        ids = [h.node_id for h in meta.hotspots]
        assert len(ids) == len(set(ids))

    def test_hotspot_line_numbers_positive(self, loop_python_code: str):
        meta = analyze_python(loop_python_code)
        for hotspot in meta.hotspots:
            assert hotspot.start_line >= 1
            assert hotspot.end_line >= hotspot.start_line


# ---------------------------------------------------------------------------
# Syntax error handling
# ---------------------------------------------------------------------------


class TestSyntaxErrorHandling:
    def test_syntax_error_returns_metadata(self):
        result = analyze_python("def broken(:\n    pass\n")
        assert isinstance(result, StaticMetadata)
        assert result.language == "python"
        assert result.baseline_time_complexity == "unknown"

    def test_empty_string(self):
        result = analyze_python("")
        assert isinstance(result, StaticMetadata)
        assert result.loops == 0

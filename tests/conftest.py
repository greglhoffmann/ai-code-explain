"""Shared pytest fixtures for the ai_code_explain test suite."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ai_code_explain.models import (
    ASTSpan,
    AnalysisResult,
    ComplexityEstimate,
    Improvement,
    StaticMetadata,
)


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> Path:
    """Return a path to a temporary SQLite database file."""
    return tmp_path / "test_history.db"


# ---------------------------------------------------------------------------
# Code snippet fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_python_code() -> str:
    return "def add(a, b):\n    return a + b\n"


@pytest.fixture()
def loop_python_code() -> str:
    return (
        "def process(items):\n"
        "    result = []\n"
        "    for item in items:\n"
        "        result.append(item * 2)\n"
        "    return result\n"
    )


@pytest.fixture()
def nested_loop_python_code() -> str:
    return (
        "def matrix_mul(a, b):\n"
        "    for i in range(len(a)):\n"
        "        for j in range(len(b)):\n"
        "            pass\n"
    )


@pytest.fixture()
def recursive_python_code() -> str:
    return (
        "def fib(n):\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    return fib(n - 1) + fib(n - 2)\n"
    )


@pytest.fixture()
def sort_python_code() -> str:
    return (
        "def sort_items(data):\n"
        "    data.sort()\n"
        "    return sorted(data)\n"
    )


@pytest.fixture()
def comprehension_python_code() -> str:
    return (
        "squares = [x**2 for x in range(10)]\n"
        "even_squares = {x**2 for x in range(10) if x % 2 == 0}\n"
        "mapping = {x: x**2 for x in range(10)}\n"
        "gen = (x**2 for x in range(10))\n"
    )


@pytest.fixture()
def hashmap_python_code() -> str:
    return (
        "def count_words(text):\n"
        "    counts = {}\n"
        "    for word in text.split():\n"
        "        counts[word] = counts.get(word, 0) + 1\n"
        "    return counts\n"
    )


@pytest.fixture()
def sample_static_metadata() -> StaticMetadata:
    return StaticMetadata(
        language="python",
        loops=2,
        nested_loops=True,
        sort_operations=1,
        recursive_calls=False,
        hashmap_usage=True,
        comprehensions=1,
        imports=["os", "mymodule"],
        function_names=["process"],
        hotspots=[
            ASTSpan(
                node_id="func_1",
                node_type="function",
                name="process",
                start_line=1,
                end_line=10,
                label="def process()",
            ),
            ASTSpan(
                node_id="nested_loop_2",
                node_type="nested_loop",
                name="nested_loop",
                start_line=5,
                end_line=8,
                label="Performance hotspot: nested loop",
            ),
        ],
        baseline_time_complexity="O(n^2)",
        baseline_space_complexity="O(n)",
    )


@pytest.fixture()
def sample_complexity() -> ComplexityEstimate:
    return ComplexityEstimate(
        static_time="O(n^2)",
        static_space="O(n)",
        static_confidence="high",
        llm_time="O(n log n)",
        llm_space="O(n)",
        llm_confidence="medium",
        llm_reasoning="Sorting dominates",
    )


@pytest.fixture()
def sample_improvement() -> Improvement:
    return Improvement(
        category="algorithmic",
        impact="high",
        behavior_change_risk="low",
        description="Use hashmap instead of nested loop",
        tradeoffs="Uses more memory",
        optimized_code="def optimized(): pass",
    )


@pytest.fixture()
def sample_analysis_result(
    sample_static_metadata: StaticMetadata,
    sample_complexity: ComplexityEstimate,
    sample_improvement: Improvement,
) -> AnalysisResult:
    return AnalysisResult(
        language="python",
        original_code="def foo(): pass",
        static_metadata=sample_static_metadata,
        semgrep_findings=[{"rule": "test-rule", "severity": "warning", "message": "test", "line": 1}],
        explanation="This function does X.",
        referenced_hotspots=[{"node_id": "func_1", "reason": "Main function"}],
        complexity=sample_complexity,
        improvements=[sample_improvement],
        optimized_code="def foo(): pass  # optimized",
        diff_text="[green]+optimized[/green]",
    )

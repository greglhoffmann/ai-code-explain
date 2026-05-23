"""Tests for shared data-transfer models (models.py)."""

from __future__ import annotations

import pytest

from ai_code_explain.models import (
    ASTSpan,
    AnalysisResult,
    ComplexityEstimate,
    Improvement,
    StaticMetadata,
)


class TestASTSpan:
    def test_required_fields(self):
        span = ASTSpan(
            node_id="loop_1",
            node_type="loop",
            name="loop",
            start_line=3,
            end_line=7,
        )
        assert span.node_id == "loop_1"
        assert span.node_type == "loop"
        assert span.name == "loop"
        assert span.start_line == 3
        assert span.end_line == 7

    def test_label_defaults_to_empty_string(self):
        span = ASTSpan(node_id="x", node_type="function", name="f", start_line=1, end_line=1)
        assert span.label == ""

    def test_label_can_be_set(self):
        span = ASTSpan(node_id="x", node_type="function", name="f", start_line=1, end_line=1, label="my label")
        assert span.label == "my label"


class TestStaticMetadata:
    def test_defaults(self):
        meta = StaticMetadata(language="python")
        assert meta.loops == 0
        assert meta.nested_loops is False
        assert meta.sort_operations == 0
        assert meta.recursive_calls is False
        assert meta.hashmap_usage is False
        assert meta.comprehensions == 0
        assert meta.async_patterns == 0
        assert meta.imports == []
        assert meta.function_names == []
        assert meta.hotspots == []
        assert meta.baseline_time_complexity == "O(n)"
        assert meta.baseline_space_complexity == "O(n)"

    def test_populated_fields(self):
        span = ASTSpan(node_id="f1", node_type="function", name="f", start_line=1, end_line=5)
        meta = StaticMetadata(
            language="javascript",
            loops=3,
            nested_loops=True,
            sort_operations=2,
            recursive_calls=True,
            hashmap_usage=True,
            comprehensions=1,
            async_patterns=2,
            imports=["fs", "path"],
            function_names=["main"],
            hotspots=[span],
            baseline_time_complexity="O(n^2)",
            baseline_space_complexity="O(n)",
        )
        assert meta.language == "javascript"
        assert meta.loops == 3
        assert meta.hotspots[0].node_id == "f1"

    def test_mutable_list_defaults_are_independent(self):
        """Each instance gets its own list — no shared default mutable."""
        meta1 = StaticMetadata(language="python")
        meta2 = StaticMetadata(language="python")
        meta1.imports.append("os")
        assert meta2.imports == []


class TestComplexityEstimate:
    def test_required_fields(self):
        c = ComplexityEstimate(static_time="O(n)", static_space="O(1)")
        assert c.static_time == "O(n)"
        assert c.static_space == "O(1)"
        assert c.static_confidence == "high"
        assert c.llm_time is None
        assert c.llm_space is None
        assert c.llm_confidence is None
        assert c.llm_reasoning is None

    def test_all_fields(self):
        c = ComplexityEstimate(
            static_time="O(n^2)",
            static_space="O(n)",
            static_confidence="high",
            llm_time="O(n log n)",
            llm_space="O(n)",
            llm_confidence="medium",
            llm_reasoning="Sort dominates",
        )
        assert c.llm_reasoning == "Sort dominates"


class TestImprovement:
    def test_all_fields(self):
        imp = Improvement(
            category="algorithmic",
            impact="high",
            behavior_change_risk="low",
            description="Use a hashmap",
            tradeoffs="More memory",
            optimized_code="d = {}",
        )
        assert imp.category == "algorithmic"
        assert imp.impact == "high"
        assert imp.behavior_change_risk == "low"


class TestAnalysisResult:
    def test_defaults(self):
        meta = StaticMetadata(language="python")
        result = AnalysisResult(
            language="python",
            original_code="x = 1",
            static_metadata=meta,
            semgrep_findings=[],
        )
        assert result.explanation == ""
        assert result.referenced_hotspots == []
        assert result.complexity is None
        assert result.block_complexities == []
        assert result.improvements == []
        assert result.optimized_code == ""
        assert result.diff_text == ""
        assert result.local_context is None
        assert result.optimization_warnings == []

    def test_populated(self, sample_analysis_result: AnalysisResult):
        assert sample_analysis_result.language == "python"
        assert len(sample_analysis_result.semgrep_findings) == 1
        assert sample_analysis_result.complexity is not None
        assert sample_analysis_result.complexity.llm_time == "O(n log n)"

"""Tests for the LLM gateway module (llm_gateway.py).

All OpenAI API calls are mocked — no real network requests are made.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_code_explain.llm_gateway import (
    _call_complexity,
    _call_explanation,
    _call_optimization,
    _format_patterns,
    get_last_llm_debug,
    _hotspot_payload,
    _safe_json,
    _select_blocks,
    _static_metadata_payload,
    run_llm_pipeline,
)
from ai_code_explain.models import (
    ASTSpan,
    ComplexityEstimate,
    Improvement,
    StaticMetadata,
)


# ---------------------------------------------------------------------------
# Helpers to build mock OpenAI response objects
# ---------------------------------------------------------------------------


def _mock_response(content: str) -> MagicMock:
    """Build a minimal mock that mimics openai.ChatCompletion response."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# _safe_json
# ---------------------------------------------------------------------------


class TestSafeJson:
    def test_valid_object(self):
        assert _safe_json('{"a": 1}') == {"a": 1}

    def test_valid_array_wrapped_in_object(self):
        assert _safe_json('{"items": [1, 2]}') == {"items": [1, 2]}

    def test_invalid_json_returns_empty_dict(self):
        assert _safe_json("not json") == {}

    def test_empty_string_returns_empty_dict(self):
        assert _safe_json("") == {}

    def test_null_json_returns_empty_dict(self):
        # "null" is valid JSON but not a dict
        assert _safe_json("null") == {}


# ---------------------------------------------------------------------------
# _format_patterns
# ---------------------------------------------------------------------------


class TestFormatPatterns:
    def test_no_patterns_detected(self):
        meta = StaticMetadata(language="python")
        result = _format_patterns(meta)
        assert "No significant patterns" in result

    def test_loops_in_output(self):
        meta = StaticMetadata(language="python", loops=2)
        result = _format_patterns(meta)
        assert "2 loop(s)" in result

    def test_nested_loops_in_output(self):
        meta = StaticMetadata(language="python", nested_loops=True)
        result = _format_patterns(meta)
        assert "Nested loops" in result

    def test_sort_operations_in_output(self):
        meta = StaticMetadata(language="python", sort_operations=1)
        result = _format_patterns(meta)
        assert "sort operation" in result

    def test_recursive_calls_in_output(self):
        meta = StaticMetadata(language="python", recursive_calls=True)
        result = _format_patterns(meta)
        assert "Recursive" in result

    def test_hashmap_in_output(self):
        meta = StaticMetadata(language="python", hashmap_usage=True)
        result = _format_patterns(meta)
        assert "Hash map" in result

    def test_comprehensions_in_output(self):
        meta = StaticMetadata(language="python", comprehensions=3)
        result = _format_patterns(meta)
        assert "comprehension" in result

    def test_async_patterns_in_output(self):
        meta = StaticMetadata(language="python", async_patterns=2)
        result = _format_patterns(meta)
        assert "async" in result

    def test_all_patterns_combined(self, sample_static_metadata: StaticMetadata):
        result = _format_patterns(sample_static_metadata)
        assert "loop(s)" in result
        assert "Nested" in result
        assert "sort" in result
        assert "Hash map" in result


# ---------------------------------------------------------------------------
# _hotspot_payload
# ---------------------------------------------------------------------------


class TestHotspotPayload:
    def test_empty_list(self):
        assert _hotspot_payload([]) == []

    def test_serialises_fields(self):
        span = ASTSpan(
            node_id="func_1",
            node_type="function",
            name="process",
            start_line=4,
            end_line=18,
            label="def process()",
        )
        payload = _hotspot_payload([span])
        assert len(payload) == 1
        d = payload[0]
        assert d["node_id"] == "func_1"
        assert d["type"] == "function"
        assert d["name"] == "process"
        assert d["start_line"] == 4
        assert d["end_line"] == 18
        assert d["label"] == "def process()"

    def test_multiple_spans(self, sample_static_metadata: StaticMetadata):
        payload = _hotspot_payload(sample_static_metadata.hotspots)
        assert len(payload) == 2


# ---------------------------------------------------------------------------
# _static_metadata_payload
# ---------------------------------------------------------------------------


class TestStaticMetadataPayload:
    def test_contains_required_keys(self, sample_static_metadata: StaticMetadata):
        payload = _static_metadata_payload(sample_static_metadata)
        assert "language" in payload
        assert "loops" in payload
        assert "nested_loops" in payload
        assert "baseline_complexity" in payload

    def test_baseline_nested_correctly(self, sample_static_metadata: StaticMetadata):
        payload = _static_metadata_payload(sample_static_metadata)
        assert payload["baseline_complexity"]["time"] == "O(n^2)"
        assert payload["baseline_complexity"]["space"] == "O(n)"


# ---------------------------------------------------------------------------
# _select_blocks
# ---------------------------------------------------------------------------


def _span(node_id: str, start: int, end: int) -> ASTSpan:
    return ASTSpan(node_id=node_id, node_type="loop", name=node_id, start_line=start, end_line=end)


class TestSelectBlocks:
    def test_empty_returns_empty(self):
        assert _select_blocks([]) == []

    def test_returns_up_to_max_blocks(self):
        spans = [_span(f"s{i}", i * 10, i * 10 + 5) for i in range(20)]
        result = _select_blocks(spans, max_blocks=8)
        assert len(result) <= 8

    def test_largest_selected_first(self):
        small = _span("small", 1, 3)      # size 2
        large = _span("large", 10, 30)    # size 20
        result = _select_blocks([small, large], max_blocks=1)
        assert result == [large]

    def test_no_overlapping_spans(self):
        # Two overlapping spans: only the bigger one should be picked
        a = _span("a", 1, 20)   # size 19
        b = _span("b", 5, 25)   # size 20 — overlaps a
        result = _select_blocks([a, b], max_blocks=8)
        assert len(result) == 1
        assert result[0].node_id == "b"  # b is larger

    def test_non_overlapping_both_selected(self):
        a = _span("a", 1, 10)
        b = _span("b", 11, 20)
        result = _select_blocks([a, b], max_blocks=8)
        assert len(result) == 2

    def test_adjacent_spans_not_considered_overlapping(self):
        # end_line == next start_line - 1 should be fine (non-overlapping)
        a = _span("a", 1, 5)
        b = _span("b", 6, 10)
        result = _select_blocks([a, b], max_blocks=8)
        assert len(result) == 2

    def test_exact_overlap_boundary(self):
        # start_line == end_line of another span counts as overlap
        a = _span("a", 1, 10)
        b = _span("b", 10, 20)  # shares line 10
        result = _select_blocks([a, b], max_blocks=8)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _call_explanation
# ---------------------------------------------------------------------------


class TestCallExplanation:
    def _make_client(self, response_content: str) -> MagicMock:
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response(response_content)
        return client

    def test_returns_explanation_and_hotspots(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({
            "explanation": "This code processes a list.",
            "referenced_hotspots": [{"node_id": "func_1", "reason": "main logic"}],
        })
        client = self._make_client(payload)
        explanation, hotspots = _call_explanation(
            client, "test-model", "def foo(): pass", sample_static_metadata, [], None
        )
        assert explanation == "This code processes a list."
        assert hotspots[0]["node_id"] == "func_1"

    def test_empty_hotspots_when_not_referenced(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({"explanation": "Simple code.", "referenced_hotspots": []})
        client = self._make_client(payload)
        _, hotspots = _call_explanation(
            client, "test-model", "x = 1", sample_static_metadata, [], None
        )
        assert hotspots == []

    def test_invalid_json_returns_empty_explanation(self, sample_static_metadata: StaticMetadata):
        client = self._make_client("not-json")
        explanation, hotspots = _call_explanation(
            client, "test-model", "x = 1", sample_static_metadata, [], None
        )
        assert explanation == ""
        assert hotspots == []

    def test_local_context_included_in_prompt(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({"explanation": "With context.", "referenced_hotspots": []})
        client = self._make_client(payload)
        _call_explanation(
            client, "test-model", "x = 1", sample_static_metadata, [], "### utils.py\n```\ndef helper(): pass\n```"
        )
        call_args = client.chat.completions.create.call_args
        user_message = call_args.kwargs["messages"][1]["content"]
        assert "Local Filesystem Context" in user_message

    def test_no_local_context_section_when_none(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({"explanation": "No context.", "referenced_hotspots": []})
        client = self._make_client(payload)
        _call_explanation(
            client, "test-model", "x = 1", sample_static_metadata, [], None
        )
        call_args = client.chat.completions.create.call_args
        user_message = call_args.kwargs["messages"][1]["content"]
        assert "Local Filesystem Context" not in user_message

    def test_none_choices_returns_empty_result(self, sample_static_metadata: StaticMetadata):
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(choices=None)
        explanation, hotspots = _call_explanation(
            client, "test-model", "x = 1", sample_static_metadata, [], None
        )
        assert explanation == ""
        assert hotspots == []

    def test_strips_leading_colon_from_explanation(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({
            "explanation": ":\n### Summary\nHello",
            "referenced_hotspots": [],
        })
        client = self._make_client(payload)
        explanation, _ = _call_explanation(
            client, "test-model", "x = 1", sample_static_metadata, [], None
        )
        assert explanation.startswith("### Summary")


# ---------------------------------------------------------------------------
# _call_complexity
# ---------------------------------------------------------------------------


class TestCallComplexity:
    def _make_client(self, response_content: str) -> MagicMock:
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response(response_content)
        return client

    def test_returns_complexity_estimate(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({
            "time": "O(n log n)",
            "space": "O(n)",
            "confidence": "medium",
            "reasoning": "Sorting dominates.",
        })
        client = self._make_client(payload)
        result = _call_complexity(client, "test-model", "def f(d): d.sort()", sample_static_metadata)
        assert isinstance(result, ComplexityEstimate)
        assert result.llm_time == "O(n log n)"
        assert result.llm_confidence == "medium"
        assert result.llm_reasoning == "Sorting dominates."

    def test_static_baseline_preserved_in_result(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({"time": "O(1)", "space": "O(1)", "confidence": "low", "reasoning": ""})
        client = self._make_client(payload)
        result = _call_complexity(client, "test-model", "x = 1", sample_static_metadata)
        assert result.static_time == sample_static_metadata.baseline_time_complexity
        assert result.static_space == sample_static_metadata.baseline_space_complexity

    def test_invalid_json_returns_none_llm_fields(self, sample_static_metadata: StaticMetadata):
        client = self._make_client("bad json")
        result = _call_complexity(client, "test-model", "x = 1", sample_static_metadata)
        assert result.llm_time is None
        assert result.llm_confidence is None

    def test_none_choices_returns_none_llm_fields(self, sample_static_metadata: StaticMetadata):
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(choices=None)
        result = _call_complexity(client, "test-model", "x = 1", sample_static_metadata)
        assert result.llm_time is None
        assert result.llm_confidence is None

    def test_splits_single_line_bullet_reasoning(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({
            "time": "O(n)",
            "space": "O(1)",
            "confidence": "medium",
            "reasoning": "- scan array - constant extra memory - no recursion",
        })
        client = self._make_client(payload)
        result = _call_complexity(client, "test-model", "x = 1", sample_static_metadata)
        assert result.llm_reasoning == "- scan array\n- constant extra memory\n- no recursion"


# ---------------------------------------------------------------------------
# _call_optimization
# ---------------------------------------------------------------------------


class TestCallOptimization:
    def _make_client(self, response_content: str) -> MagicMock:
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response(response_content)
        return client

    def test_returns_improvements_and_code(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({
            "improvements": [
                {
                    "category": "algorithmic",
                    "impact": "high",
                    "behavior_change_risk": "low",
                    "description": "Use hashmap",
                    "tradeoffs": "More memory",
                    "optimized_code": "def fast(): pass",
                }
            ]
        })
        client = self._make_client(payload)
        improvements, best_code = _call_optimization(
            client, "test-model", "def slow(): pass", sample_static_metadata, []
        )
        assert len(improvements) == 1
        assert improvements[0].category == "algorithmic"
        assert best_code == "def fast(): pass"

    def test_fallback_to_first_improvement_when_no_high_impact(
        self, sample_static_metadata: StaticMetadata
    ):
        payload = json.dumps({
            "improvements": [
                {
                    "category": "readability",
                    "impact": "low",
                    "behavior_change_risk": "low",
                    "description": "rename var",
                    "tradeoffs": "none",
                    "optimized_code": "def cleaner(): pass",
                }
            ]
        })
        client = self._make_client(payload)
        _, best_code = _call_optimization(
            client, "test-model", "def original(): pass", sample_static_metadata, []
        )
        assert best_code == "def cleaner(): pass"

    def test_original_code_returned_when_no_improvements(
        self, sample_static_metadata: StaticMetadata
    ):
        payload = json.dumps({"improvements": []})
        client = self._make_client(payload)
        _, best_code = _call_optimization(
            client, "test-model", "def original(): pass", sample_static_metadata, []
        )
        assert best_code == "def original(): pass"

    def test_invalid_json_returns_original_code(self, sample_static_metadata: StaticMetadata):
        client = self._make_client("malformed")
        _, best_code = _call_optimization(
            client, "test-model", "def original(): pass", sample_static_metadata, []
        )
        assert best_code == "def original(): pass"

    def test_none_choices_returns_original_code(self, sample_static_metadata: StaticMetadata):
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(choices=None)
        improvements, best_code = _call_optimization(
            client, "test-model", "def original(): pass", sample_static_metadata, []
        )
        assert improvements == []
        assert best_code == "def original(): pass"

    def test_debug_payload_captured_for_optimization(self, sample_static_metadata: StaticMetadata):
        payload = json.dumps({"improvements": [], "full_optimized_code": "def original(): pass"})
        client = self._make_client(payload)
        _call_optimization(client, "test-model", "def original(): pass", sample_static_metadata, [])
        debug = get_last_llm_debug("optimization")
        assert debug.get("model") == "test-model"
        assert isinstance(debug.get("raw_content"), str)
        assert "improvements" in debug.get("parsed", {})


# ---------------------------------------------------------------------------
# run_llm_pipeline (full integration, mocked at API layer)
# ---------------------------------------------------------------------------


class TestRunLlmPipeline:
    """Tests for run_llm_pipeline orchestration.

    Patches the individual _call_* functions rather than the raw API client
    so the tests are not sensitive to thread scheduling order.
    """

    def _make_complexity(self) -> ComplexityEstimate:
        return ComplexityEstimate(
            static_time="O(n)", static_space="O(1)", static_confidence="high",
            llm_time="O(n)", llm_space="O(1)", llm_confidence="high", llm_reasoning="one loop",
        )

    def _make_improvement(self) -> "Improvement":
        from ai_code_explain.models import Improvement
        return Improvement(
            category="readability", impact="low", behavior_change_risk="low",
            description="cleanup", tradeoffs="none", optimized_code="def optimized(): pass",
        )

    def test_pipeline_calls_three_llm_stages(self, sample_static_metadata: StaticMetadata):
        with (
            patch("ai_code_explain.llm_gateway._get_client", return_value=MagicMock()),
            patch("ai_code_explain.llm_gateway._call_explanation", return_value=("Does stuff.", [])) as mock_expl,
            patch("ai_code_explain.llm_gateway._call_complexity", return_value=self._make_complexity()) as mock_compl,
            patch("ai_code_explain.llm_gateway._call_optimization", return_value=([self._make_improvement()], "def optimized(): pass")) as mock_optim,
            patch("ai_code_explain.llm_gateway._call_block_complexities", return_value=[]),
        ):
            explanation, hotspots, complexity, improvements, optimized, block_complexities = run_llm_pipeline(
                code="def foo(): pass",
                metadata=sample_static_metadata,
                semgrep_findings=[],
                local_context=None,
            )

        mock_expl.assert_called_once()
        mock_compl.assert_called_once()
        mock_optim.assert_called_once()
        assert explanation == "Does stuff."
        assert isinstance(complexity, ComplexityEstimate)
        assert len(improvements) == 1
        assert block_complexities == []

    def test_pipeline_returns_correct_types(self, sample_static_metadata: StaticMetadata):
        with (
            patch("ai_code_explain.llm_gateway._get_client", return_value=MagicMock()),
            patch("ai_code_explain.llm_gateway._call_explanation", return_value=("Test.", [])),
            patch("ai_code_explain.llm_gateway._call_complexity", return_value=self._make_complexity()),
            patch("ai_code_explain.llm_gateway._call_optimization", return_value=([], "x = 1")),
            patch("ai_code_explain.llm_gateway._call_block_complexities", return_value=[]),
        ):
            explanation, hotspots, complexity, improvements, optimized, block_complexities = run_llm_pipeline(
                "x = 1", sample_static_metadata, [], None
            )

        assert isinstance(explanation, str)
        assert isinstance(hotspots, list)
        assert isinstance(complexity, ComplexityEstimate)
        assert isinstance(improvements, list)
        assert isinstance(optimized, str)

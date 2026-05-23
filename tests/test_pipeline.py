"""Tests for the end-to-end analysis pipeline (pipeline.py).

All external I/O (LLM calls, Semgrep, database) is mocked so tests run
fast and deterministically without network or installed-tool dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_code_explain.models import (
    AnalysisResult,
    ComplexityEstimate,
    Improvement,
    StaticMetadata,
)
from ai_code_explain.pipeline import _persist, run_pipeline


# ---------------------------------------------------------------------------
# Shared mock builders
# ---------------------------------------------------------------------------


def _make_complexity() -> ComplexityEstimate:
    return ComplexityEstimate(
        static_time="O(n)",
        static_space="O(1)",
        static_confidence="high",
        llm_time="O(n)",
        llm_space="O(1)",
        llm_confidence="high",
        llm_reasoning="Simple loop.",
    )


def _make_improvement() -> Improvement:
    return Improvement(
        category="readability",
        impact="low",
        behavior_change_risk="low",
        description="Use a list comprehension.",
        tradeoffs="none",
        optimized_code="result = [x * 2 for x in items]",
    )


def _make_llm_return(code: str = "def foo(): pass"):
    """Return a tuple matching the run_llm_pipeline signature."""
    return (
        "This function does basic processing.",
        [{"node_id": "func_1", "reason": "Main function"}],
        _make_complexity(),
        [_make_improvement()],
        f"{code}  # optimized",
        [],
    )


# ---------------------------------------------------------------------------
# run_pipeline — happy path
# ---------------------------------------------------------------------------


SIMPLE_PYTHON = "def process(items):\n    for item in items:\n        pass\n"
SIMPLE_JS = """
function fetchUserData(userId) {
    var url = "https://api.example.com/users/" + userId;
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, false);
    xhr.send();

    if (xhr.status == 200) {
        var data = eval(xhr.responseText);
        return data;
    }
    return null;
}

function mergeConfig(defaults, userConfig) {
    return Object.assign(defaults, JSON.parse(userConfig));
}
""".strip()


class TestRunPipelineHappyPath:
    """Tests for successful end-to-end pipeline execution."""

    def _patch_all(self, tmp_db_path: Path, source_code: str = SIMPLE_PYTHON):
        """Return a context manager that patches all external I/O."""
        import contextlib

        llm_return = _make_llm_return(source_code)

        @contextlib.contextmanager
        def _ctx():
            mock_client = MagicMock()
            with (
                patch("ai_code_explain.pipeline.run_semgrep", return_value=[]) as mock_semgrep,
                patch("ai_code_explain.pipeline.fetch_local_import_context", return_value=(None, [])) as mock_local_context,
                patch("ai_code_explain.pipeline._get_client", return_value=mock_client),
                patch("ai_code_explain.pipeline._call_explanation", return_value=(llm_return[0], llm_return[1])) as mock_expl,
                patch("ai_code_explain.pipeline._call_complexity", return_value=llm_return[2]) as mock_compl,
                patch("ai_code_explain.pipeline._call_optimization", return_value=(llm_return[3], llm_return[4])) as mock_optim,
                patch("ai_code_explain.pipeline._call_block_complexities", return_value=[]),
                patch("ai_code_explain.pipeline.create_tables"),
                patch("ai_code_explain.pipeline.save_snippet") as mock_save,
            ):
                mock_save.return_value = MagicMock(id=1)
                yield {
                    "semgrep": mock_semgrep,
                    "local_context": mock_local_context,
                    "explanation": mock_expl,
                    "complexity": mock_compl,
                    "optimization": mock_optim,
                    "save": mock_save,
                }

        return _ctx()

    def test_returns_analysis_result(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert isinstance(result, AnalysisResult)

    def test_language_detected_correctly(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert result.language == "python"

    def test_language_hint_respected(self, tmp_db_path: Path):
        js_code = "const x = 1;"
        with self._patch_all(tmp_db_path, js_code) as mocks:
            result = run_pipeline(js_code, language_hint="javascript")
        assert result.language == "javascript"
        assert result.detection_confidence == "explicit"

    def test_detection_confidence_populated(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert result.detection_confidence in ("explicit", "high", "medium", "low")

    def test_original_code_preserved(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert result.original_code == SIMPLE_PYTHON

    def test_semgrep_called_with_correct_language(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            run_pipeline(SIMPLE_PYTHON)
        mocks["semgrep"].assert_called_once_with(SIMPLE_PYTHON, "python")

    def test_llm_explanation_called(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            run_pipeline(SIMPLE_PYTHON)
        mocks["explanation"].assert_called_once()

    def test_llm_optimization_receives_semgrep_findings(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            run_pipeline(SIMPLE_PYTHON)
        # semgrep_findings is the 5th positional arg to _call_optimization
        call_args = mocks["optimization"].call_args
        assert isinstance(call_args.args[4], list)  # semgrep_findings

    def test_diff_text_populated(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert isinstance(result.diff_text, str)

    def test_explanation_populated(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert result.explanation == "This function does basic processing."

    def test_complexity_is_estimate(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert isinstance(result.complexity, ComplexityEstimate)

    def test_improvements_list(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            result = run_pipeline(SIMPLE_PYTHON)
        assert isinstance(result.improvements, list)
        assert len(result.improvements) == 1

    def test_save_snippet_called(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            run_pipeline(SIMPLE_PYTHON)
        mocks["save"].assert_called_once()

    def test_local_context_called(self, tmp_db_path: Path):
        with self._patch_all(tmp_db_path) as mocks:
            run_pipeline(SIMPLE_PYTHON)
        mocks["local_context"].assert_called_once()


# ---------------------------------------------------------------------------
# Pipeline — execution order
# ---------------------------------------------------------------------------


class TestPipelineExecutionOrder:
    """Optimization and explanation LLM calls must receive Semgrep results."""

    def test_optimization_receives_semgrep_findings(self, tmp_db_path: Path):
        """_call_optimization must be called with the semgrep findings as input."""
        captured: dict = {}

        def record_semgrep(code, lang):
            return [{"rule": "r1", "severity": "warning", "message": "bad", "line": 1}]

        def record_optimization(client, model, code, metadata, semgrep_findings, analysis_mode="concise"):
            captured["semgrep_findings"] = semgrep_findings
            return [], "def process(items):\n    return [item for item in items]\n"

        llm_return = _make_llm_return(SIMPLE_PYTHON)
        with (
            patch("ai_code_explain.pipeline.run_semgrep", side_effect=record_semgrep),
            patch("ai_code_explain.pipeline.fetch_local_import_context", return_value=(None, [])),
            patch("ai_code_explain.pipeline._get_client", return_value=MagicMock()),
            patch("ai_code_explain.pipeline._call_explanation", return_value=(llm_return[0], llm_return[1])),
            patch("ai_code_explain.pipeline._call_complexity", return_value=llm_return[2]),
            patch("ai_code_explain.pipeline._call_optimization", side_effect=record_optimization),
            patch("ai_code_explain.pipeline._call_block_complexities", return_value=[]),
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", return_value=MagicMock(id=1)),
        ):
            run_pipeline(SIMPLE_PYTHON)

        assert captured["semgrep_findings"] == [
            {"rule": "r1", "severity": "warning", "message": "bad", "line": 1}
        ]

    def test_unchanged_optimized_code_raises(self, tmp_db_path: Path):
        llm_return = _make_llm_return(SIMPLE_PYTHON)
        with (
            patch("ai_code_explain.pipeline.run_semgrep", return_value=[{"rule": "r1", "severity": "warning", "message": "bad", "line": 1}]),
            patch("ai_code_explain.pipeline.fetch_local_import_context", return_value=(None, [])),
            patch("ai_code_explain.pipeline._get_client", return_value=MagicMock()),
            patch("ai_code_explain.pipeline._call_explanation", return_value=(llm_return[0], llm_return[1])),
            patch("ai_code_explain.pipeline._call_complexity", return_value=llm_return[2]),
            patch("ai_code_explain.pipeline._call_optimization", return_value=(llm_return[3], SIMPLE_PYTHON)),
            patch("ai_code_explain.pipeline.get_last_llm_debug", side_effect=[
                {"finish_reason": "stop", "parsed": {"explanation": "ok"}, "raw_content": '{"explanation":"ok"}'},
                {"finish_reason": "stop", "parsed": {"improvements": []}, "raw_content": '{"improvements":[]}'},
            ]),
            patch("ai_code_explain.pipeline._call_block_complexities", return_value=[]),
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", return_value=MagicMock(id=1)),
        ):
            with pytest.raises(RuntimeError, match="Optimization debug:"):
                run_pipeline(SIMPLE_PYTHON)

    def test_invalid_optimized_python_triggers_warning(self, tmp_db_path: Path):
        llm_return = _make_llm_return(SIMPLE_PYTHON)
        with (
            patch("ai_code_explain.pipeline.run_semgrep", return_value=[]),
            patch("ai_code_explain.pipeline.fetch_local_import_context", return_value=(None, [])),
            patch("ai_code_explain.pipeline._get_client", return_value=MagicMock()),
            patch("ai_code_explain.pipeline._call_explanation", return_value=(llm_return[0], llm_return[1])),
            patch("ai_code_explain.pipeline._call_complexity", return_value=llm_return[2]),
            patch("ai_code_explain.pipeline._call_optimization", return_value=(llm_return[3], "def broken(:\n    pass")),
            patch("ai_code_explain.pipeline._call_block_complexities", return_value=[]),
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", return_value=MagicMock(id=1)),
        ):
            result = run_pipeline(SIMPLE_PYTHON, language_hint="python")

        assert result.optimization_warnings
        assert "failed syntax check" in result.optimization_warnings[0]

    def test_empty_llm_outputs_raise_explicit_error(self, tmp_db_path: Path):
        llm_return = _make_llm_return(SIMPLE_JS)
        with (
            patch("ai_code_explain.pipeline.run_semgrep", return_value=[
                {"rule": "javascript.browser.security.eval-detected.eval-detected", "severity": "warning", "message": "Detected the use of eval().", "line": 10},
                {"rule": "javascript.lang.security.insecure-object-assign.insecure-object-assign", "severity": "warning", "message": "Potential mass assignment risk.", "line": 18},
            ]),
            patch("ai_code_explain.pipeline.fetch_local_import_context", return_value=(None, [])),
            patch("ai_code_explain.pipeline._get_client", return_value=MagicMock()),
            patch("ai_code_explain.pipeline._call_explanation", return_value=("", [])),
            patch("ai_code_explain.pipeline._call_complexity", return_value=llm_return[2]),
            patch("ai_code_explain.pipeline._call_optimization", return_value=([], SIMPLE_JS)),
            patch("ai_code_explain.pipeline._call_block_complexities", return_value=[]),
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", return_value=MagicMock(id=1)),
        ):
            with pytest.raises(RuntimeError, match="LLM explanation returned empty content"):
                run_pipeline(SIMPLE_JS, language_hint="javascript")

    def test_empty_llm_outputs_are_persisted_before_raise(self, tmp_db_path: Path):
        llm_return = _make_llm_return(SIMPLE_JS)
        with (
            patch("ai_code_explain.pipeline.run_semgrep", return_value=[]),
            patch("ai_code_explain.pipeline.fetch_local_import_context", return_value=(None, [])),
            patch("ai_code_explain.pipeline._get_client", return_value=MagicMock()),
            patch("ai_code_explain.pipeline._call_explanation", return_value=("", [])),
            patch("ai_code_explain.pipeline._call_complexity", return_value=llm_return[2]),
            patch("ai_code_explain.pipeline._call_optimization", return_value=([], SIMPLE_JS)),
            patch("ai_code_explain.pipeline._call_block_complexities", return_value=[]),
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", return_value=MagicMock(id=1)) as mock_save,
        ):
            with pytest.raises(RuntimeError, match="LLM explanation returned empty content"):
                run_pipeline(SIMPLE_JS, language_hint="javascript")

        mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# _persist — JSON column separation
# ---------------------------------------------------------------------------


class TestPersist:
    """Static and LLM complexity must be stored in separate DB columns."""

    def test_static_and_llm_stored_separately(self):
        from ai_code_explain.models import ASTSpan

        meta = StaticMetadata(
            language="python",
            baseline_time_complexity="O(n^2)",
            baseline_space_complexity="O(n)",
        )
        result = AnalysisResult(
            language="python",
            original_code="def foo(): pass",
            static_metadata=meta,
            semgrep_findings=[],
            complexity=ComplexityEstimate(
                static_time="O(n^2)",
                static_space="O(n)",
                llm_time="O(n)",
                llm_space="O(1)",
                llm_confidence="medium",
                llm_reasoning="optimised",
            ),
        )

        saved_snippets: list = []

        def capture_save(snippet, db_path=None):
            saved_snippets.append(snippet)
            snippet.id = 42
            return snippet

        with (
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", side_effect=capture_save),
        ):
            _persist(result)

        assert len(saved_snippets) == 1
        snippet = saved_snippets[0]

        static_data = json.loads(snippet.static_complexity_json)
        llm_data = json.loads(snippet.llm_complexity_json)

        # Static baseline must be in static column
        assert static_data["static_estimate"]["time"] == "O(n^2)"

        # LLM estimate must be in llm column, NOT in static column
        assert "llm_adjusted_estimate" in llm_data
        assert llm_data["llm_adjusted_estimate"]["time"] == "O(n)"
        assert "llm_adjusted_estimate" not in static_data

    def test_semgrep_findings_serialised(self):
        meta = StaticMetadata(language="python")
        findings = [{"rule": "r1", "severity": "warning", "message": "bad", "line": 3}]
        result = AnalysisResult(
            language="python",
            original_code="x = 1",
            static_metadata=meta,
            semgrep_findings=findings,
        )

        saved_snippets: list = []

        def capture_save(snippet, db_path=None):
            saved_snippets.append(snippet)
            snippet.id = 1
            return snippet

        with (
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", side_effect=capture_save),
        ):
            _persist(result)

        stored = json.loads(saved_snippets[0].semgrep_findings_json)
        assert stored[0]["rule"] == "r1"

    def test_no_complexity_stores_empty_llm_json(self):
        meta = StaticMetadata(language="python")
        result = AnalysisResult(
            language="python",
            original_code="x = 1",
            static_metadata=meta,
            semgrep_findings=[],
            complexity=None,
        )

        saved_snippets: list = []

        def capture_save(snippet, db_path=None):
            saved_snippets.append(snippet)
            snippet.id = 1
            return snippet

        with (
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", side_effect=capture_save),
        ):
            _persist(result)

        llm_data = json.loads(saved_snippets[0].llm_complexity_json)
        assert llm_data == {}

    def test_improvements_stored_in_llm_json(self):
        meta = StaticMetadata(language="javascript")
        result = AnalysisResult(
            language="javascript",
            original_code="const x = 1;",
            static_metadata=meta,
            semgrep_findings=[],
            improvements=[
                Improvement(
                    category="readability",
                    impact="medium",
                    behavior_change_risk="low",
                    description="Use const consistently.",
                    tradeoffs="None.",
                    optimized_code="const x = 1;",
                )
            ],
        )

        saved_snippets: list = []

        def capture_save(snippet, db_path=None):
            saved_snippets.append(snippet)
            snippet.id = 1
            return snippet

        with (
            patch("ai_code_explain.pipeline.create_tables"),
            patch("ai_code_explain.pipeline.save_snippet", side_effect=capture_save),
        ):
            _persist(result)

        llm_data = json.loads(saved_snippets[0].llm_complexity_json)
        assert "improvements" in llm_data
        assert llm_data["improvements"][0]["description"] == "Use const consistently."

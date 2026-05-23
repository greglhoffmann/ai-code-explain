"""Tests for the Semgrep integration (semgrep_runner.py)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ai_code_explain.semgrep_runner import _parse_semgrep_output, run_semgrep


# ---------------------------------------------------------------------------
# _parse_semgrep_output
# ---------------------------------------------------------------------------


class TestParseSemgrepOutput:
    def test_empty_string_returns_empty_list(self):
        assert _parse_semgrep_output("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _parse_semgrep_output("   \n  ") == []

    def test_invalid_json_returns_empty_list(self):
        assert _parse_semgrep_output("not-json{{") == []

    def test_valid_empty_results(self):
        payload = json.dumps({"results": []})
        assert _parse_semgrep_output(payload) == []

    def test_single_result_normalised(self):
        payload = json.dumps({
            "results": [
                {
                    "check_id": "my-rule",
                    "extra": {"severity": "WARNING", "message": "Found an issue"},
                    "start": {"line": 5},
                }
            ]
        })
        findings = _parse_semgrep_output(payload)
        assert len(findings) == 1
        f = findings[0]
        assert f["rule"] == "my-rule"
        assert f["severity"] == "warning"   # lower-cased
        assert f["message"] == "Found an issue"
        assert f["line"] == 5

    def test_multiple_results(self):
        payload = json.dumps({
            "results": [
                {
                    "check_id": "rule-1",
                    "extra": {"severity": "ERROR", "message": "msg1"},
                    "start": {"line": 1},
                },
                {
                    "check_id": "rule-2",
                    "extra": {"severity": "INFO", "message": "msg2"},
                    "start": {"line": 10},
                },
            ]
        })
        findings = _parse_semgrep_output(payload)
        assert len(findings) == 2
        assert findings[0]["rule"] == "rule-1"
        assert findings[1]["rule"] == "rule-2"

    def test_missing_optional_fields_default(self):
        payload = json.dumps({"results": [{}]})
        findings = _parse_semgrep_output(payload)
        assert findings[0]["rule"] == "unknown"
        assert findings[0]["severity"] == "info"
        assert findings[0]["message"] == ""
        assert findings[0]["line"] == 0

    def test_json_without_results_key_returns_empty(self):
        payload = json.dumps({"errors": []})
        assert _parse_semgrep_output(payload) == []


# ---------------------------------------------------------------------------
# run_semgrep — mocked subprocess
# ---------------------------------------------------------------------------


class TestRunSemgrep:
    def _make_completed_process(self, stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
        mock = MagicMock()
        mock.stdout = stdout
        mock.returncode = returncode
        mock.stderr = stderr
        return mock

    def test_semgrep_not_found_returns_empty(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("semgrep not found")):
            result = run_semgrep("def foo(): pass", "python")
        assert result == []

    def test_semgrep_timeout_returns_timeout_finding(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="semgrep", timeout=60)):
            result = run_semgrep("def foo(): pass", "python")
        assert len(result) == 1
        assert result[0]["rule"] == "semgrep-timeout"

    def test_successful_run_with_findings(self):
        payload = json.dumps({
            "results": [
                {
                    "check_id": "perf-rule",
                    "extra": {"severity": "WARNING", "message": "slow loop"},
                    "start": {"line": 3},
                }
            ]
        })
        mock_result = self._make_completed_process(stdout=payload)
        with patch("subprocess.run", return_value=mock_result):
            findings = run_semgrep("for x in lst: pass", "python")
        assert len(findings) == 1
        assert findings[0]["rule"] == "perf-rule"

    def test_successful_run_no_findings(self):
        payload = json.dumps({"results": []})
        mock_result = self._make_completed_process(stdout=payload)
        with patch("subprocess.run", return_value=mock_result):
            result = run_semgrep("x = 1", "python")
        assert result == []

    def test_temp_file_cleaned_up_on_success(self, tmp_path):
        """The temporary file must be deleted after analysis."""
        payload = json.dumps({"results": []})
        mock_result = self._make_completed_process(stdout=payload)
        created_paths: list[str] = []

        original_run = subprocess.run

        def capturing_run(args, **kwargs):
            # Record the temp file path from the subprocess args
            created_paths.append(args[-1])
            return mock_result

        with patch("subprocess.run", side_effect=capturing_run):
            run_semgrep("x = 1", "python")

        # The temp file should have been deleted
        import os
        for path in created_paths:
            assert not os.path.exists(path), f"Temp file not cleaned up: {path}"

    def test_temp_file_cleaned_up_on_error(self, tmp_path):
        """Temp file must also be deleted when Semgrep raises an error."""
        created_paths: list[str] = []

        def failing_run(args, **kwargs):
            created_paths.append(args[-1])
            raise FileNotFoundError("semgrep not found")

        with patch("subprocess.run", side_effect=failing_run):
            run_semgrep("x = 1", "python")

        import os
        for path in created_paths:
            assert not os.path.exists(path)

    def test_js_file_gets_js_extension(self):
        """JavaScript snippets should use a .js temp file."""
        payload = json.dumps({"results": []})
        mock_result = self._make_completed_process(stdout=payload)
        used_paths: list[str] = []

        def capturing_run(args, **kwargs):
            used_paths.append(args[-1])
            return mock_result

        with patch("subprocess.run", side_effect=capturing_run):
            run_semgrep("const x = 1;", "javascript")

        assert used_paths and used_paths[0].endswith(".js")

    def test_python_file_gets_py_extension(self):
        payload = json.dumps({"results": []})
        mock_result = self._make_completed_process(stdout=payload)
        used_paths: list[str] = []

        def capturing_run(args, **kwargs):
            used_paths.append(args[-1])
            return mock_result

        with patch("subprocess.run", side_effect=capturing_run):
            run_semgrep("def foo(): pass", "python")

        assert used_paths and used_paths[0].endswith(".py")

    def test_no_shell_true_in_subprocess_call(self):
        """Security: shell=True must never be used."""
        payload = json.dumps({"results": []})
        mock_result = self._make_completed_process(stdout=payload)
        call_kwargs: dict = {}

        def capturing_run(args, **kwargs):
            call_kwargs.update(kwargs)
            return mock_result

        with patch("subprocess.run", side_effect=capturing_run):
            run_semgrep("x = 1", "python")

        assert call_kwargs.get("shell") is not True

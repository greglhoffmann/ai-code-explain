"""Semgrep static analysis integration.

Responsibilities (deterministic — no LLM):
- Write the code snippet to a temporary file
- Run Semgrep with the auto ruleset targeting Python/JavaScript patterns
- Parse and normalize JSON output into a list of finding dicts
- Return findings for persistence and LLM prompt grounding

Security note: Semgrep is invoked as a subprocess with a sandboxed
temporary file only. No shell=True is used. The temporary file is
deleted after analysis completes.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


# Default rulesets — auto selects language-appropriate rules
_SEMGREP_RULESETS = ["auto"]

# Maximum time (seconds) Semgrep is allowed to run per snippet
_SEMGREP_TIMEOUT_SECONDS = 60


def run_semgrep(source_code: str, language: str) -> list[dict]:
    """Run Semgrep on a code snippet and return normalized findings.

    Args:
        source_code: Raw source text to analyze.
        language: "python" or "javascript".

    Returns:
        List of finding dicts with keys: rule, severity, message, line.
        Returns an empty list if Semgrep is not installed or times out.
    """
    extension = ".py" if language == "python" else ".js"

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=extension,
        delete=False,
        encoding="utf-8",
    ) as tmp_file:
        tmp_file.write(source_code)
        tmp_path = Path(tmp_file.name)

    try:
        result = subprocess.run(  # noqa: S603 — no shell=True, controlled args
            [
                "semgrep",
                "--config",
                "auto",
                "--json",
                "--quiet",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=_SEMGREP_TIMEOUT_SECONDS,
        )
        findings = _parse_semgrep_output(result.stdout)
        # Surface parse errors so the user knows semgrep ran but couldn't fully analyse.
        # Exit code 4 = parse/config error; stderr may carry details.
        if result.returncode == 4 or (not findings and result.stderr.strip()):
            stderr_snippet = result.stderr.strip().splitlines()[0][:120] if result.stderr.strip() else "unknown error"
            findings.append(
                {
                    "rule": "semgrep-parse-warning",
                    "severity": "info",
                    "message": f"Semgrep could not fully parse this file (syntax errors present). "
                               f"Findings may be incomplete. Detail: {stderr_snippet}",
                    "line": 0,
                }
            )
        return findings
    except FileNotFoundError:
        # Semgrep binary not found — return empty findings gracefully
        return []
    except subprocess.TimeoutExpired:
        return [
            {
                "rule": "semgrep-timeout",
                "severity": "info",
                "message": "Semgrep timed out during analysis",
                "line": 0,
            }
        ]
    except Exception:  # pylint: disable=broad-except
        return []
    finally:
        # Always clean up the temporary file
        tmp_path.unlink(missing_ok=True)


def _parse_semgrep_output(raw_json: str) -> list[dict]:
    """Parse Semgrep's JSON output into a normalized list of findings.

    Args:
        raw_json: The raw stdout from `semgrep --json`.

    Returns:
        List of normalized finding dicts.
    """
    if not raw_json.strip():
        return []

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return []

    findings: list[dict] = []
    for result in data.get("results", []):
        findings.append(
            {
                "rule": result.get("check_id", "unknown"),
                "severity": result.get("extra", {}).get("severity", "info").lower(),
                "message": result.get("extra", {}).get("message", ""),
                "line": result.get("start", {}).get("line", 0),
            }
        )

    return findings

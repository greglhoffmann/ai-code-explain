"""End-to-end analysis pipeline orchestration.

Execution DAG (dependency-aware parallelism):
  1.  Detect language + parse AST  (sync, fast)
  2a. Semgrep analysis             (IO/subprocess — parallel)
  2b. Local filesystem context     (in-process; parallel with 2a)
  2c. Complexity LLM call          (no external deps — starts immediately)
  3.  Optimization LLM call        (waits for 2a: needs semgrep findings)
  4.  Explanation LLM call         (waits for 2a + 2b: needs both)
  5.  Optimized syntax check       (deterministic)
  6.  Collect all results, generate diff, persist
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .analyzers.dispatcher import analyze
from .database import Snippet, create_tables, save_snippet
from .diff_generator import generate_rich_diff_markup
from .llm_gateway import (
    _call_block_complexities,
    _call_complexity,
    _call_explanation,
    _call_optimization,
    _get_client,
    _get_model,
    get_last_llm_debug,
)
from .local_context import fetch_local_import_context
from .models import AnalysisResult
from .semgrep_runner import run_semgrep


def run_pipeline(
    source_code: str,
    language_hint: str = "",
    sandbox_dir: Path | None = None,
    model_mode: str = "fast",
    analysis_mode: str = "concise",
    progress_callback: Callable[[str], None] | None = None,
    include_block_complexity: bool | None = None,
) -> AnalysisResult:
    """Execute the complete analysis pipeline for a single snippet.

    Args:
        source_code: Raw source text submitted by the user.
        language_hint: Optional language override ("python" or "javascript").
        sandbox_dir: Optional filesystem sandbox root for local context lookup.
        model_mode: ``"fast"`` (default) for the low-latency Laguna M.1 model;
                    ``"reasoning"`` for the higher-capability model.
        analysis_mode: ``"concise"`` (default) for shorter LLM outputs;
                   ``"detailed"`` for expanded explanations/reasoning.
        include_block_complexity: Explicit override for per-block LLM analysis.
                      If ``None``, env var controls behavior.

    Returns:
        A fully populated AnalysisResult ready for UI rendering and persistence.
    """
    def _cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    # Step 1 — Detect language and parse AST
    _cb("Static analysis…")
    metadata, language, detection_confidence = analyze(source_code, language_hint)

    client = _get_client()
    model = _get_model(model_mode)

    # Steps 2a-2c — Fan out: Semgrep, local context lookup, and complexity LLM run concurrently.
    # Semgrep and local context lookup are IO-bound; complexity has no external deps.
    _cb("Semgrep + local context + LLM complexity…")
    with ThreadPoolExecutor(max_workers=5) as executor:
        fut_semgrep = executor.submit(run_semgrep, source_code, language)
        fut_local_context = executor.submit(fetch_local_import_context, metadata, sandbox_dir)
        fut_complexity = executor.submit(_call_complexity, client, model, source_code, metadata, analysis_mode)

        # Step 3 — Optimization: waits only for Semgrep (not local context)
        semgrep_findings = fut_semgrep.result()
        _cb("LLM: optimizing…")
        fut_optimization = executor.submit(
            _call_optimization, client, model, source_code, metadata, semgrep_findings, analysis_mode
        )

        # Step 4 — Explanation: waits for Semgrep + local context
        local_context, sandbox_warnings = fut_local_context.result()
        _cb("LLM: explaining…")
        fut_explanation = executor.submit(
            _call_explanation,
            client,
            model,
            source_code,
            metadata,
            semgrep_findings,
            local_context,
            analysis_mode,
        )

        complexity = fut_complexity.result()
        improvements, optimized_code = fut_optimization.result()
        explanation, referenced_hotspots = fut_explanation.result()

    # Step 5 — Optional per-block complexity (parallelized internally)
    block_complexities = []
    enable_block = include_block_complexity
    if enable_block is None:
        enable_block = os.environ.get("AI_CODE_EXPLAIN_BLOCK_COMPLEXITY", "0").strip() in {"1", "true", "yes"}
    if enable_block:
        _cb("LLM: per-block complexity…")
        block_complexities = _call_block_complexities(client, model, source_code, metadata)

    optimization_warnings: list[str] = []
    try:
        _validate_llm_outputs(
            explanation=explanation,
            optimized_code=optimized_code,
            source_code=source_code,
            language=language,
            semgrep_findings=semgrep_findings,
            improvements=improvements,
            model=model,
            analysis_mode=analysis_mode,
        )
    except RuntimeError as exc:
        # Persist partial results so failed runs still appear in snippet history.
        optimization_warnings.append(f"LLM analysis failed: {exc}")
        failed_result = AnalysisResult(
            language=language,
            original_code=source_code,
            static_metadata=metadata,
            semgrep_findings=semgrep_findings,
            explanation=explanation,
            referenced_hotspots=referenced_hotspots,
            complexity=complexity,
            block_complexities=block_complexities,
            improvements=improvements,
            optimized_code=optimized_code,
            diff_text=generate_rich_diff_markup(source_code, optimized_code),
            local_context=local_context,
            detection_confidence=detection_confidence,
            sandbox_warnings=sandbox_warnings,
            optimization_warnings=optimization_warnings,
        )
        _persist(failed_result)
        raise

    # Step 6 — optimized-code syntax validation
    optimization_warnings.extend(_validate_optimized_code_syntax(optimized_code, language))
    if optimization_warnings:
        _cb("Warning: optimized output has syntax issues")

    # Step 7 — Diff generation (deterministic)
    diff_markup = generate_rich_diff_markup(source_code, optimized_code)

    result = AnalysisResult(
        language=language,
        original_code=source_code,
        static_metadata=metadata,
        semgrep_findings=semgrep_findings,
        explanation=explanation,
        referenced_hotspots=referenced_hotspots,
        complexity=complexity,
        block_complexities=block_complexities,
        improvements=improvements,
        optimized_code=optimized_code,
        diff_text=diff_markup,
        local_context=local_context,
        detection_confidence=detection_confidence,
        sandbox_warnings=sandbox_warnings,
        optimization_warnings=optimization_warnings,
    )

    # Step 8 — Persist to SQLite
    _persist(result)

    return result


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _persist(result: AnalysisResult) -> Snippet:
    """Serialise an AnalysisResult and save it to the database.

    Static and LLM complexity JSON are stored in separate columns so the
    deterministic baseline can never be overwritten by the LLM estimate.
    """
    create_tables()

    # Build static complexity JSON
    static_complexity = {
        "static_estimate": {
            "time": result.static_metadata.baseline_time_complexity,
            "space": result.static_metadata.baseline_space_complexity,
            "confidence": "high",
        },
        "hotspots": [
            asdict(h) for h in result.static_metadata.hotspots
        ],
    }

    # Build LLM complexity JSON
    llm_complexity: dict = {}
    if result.complexity:
        c = result.complexity
        llm_complexity = {
            "llm_adjusted_estimate": {
                "time": c.llm_time,
                "space": c.llm_space,
                "confidence": c.llm_confidence,
                "reasoning": c.llm_reasoning,
            },
            "block_estimates": [asdict(block) for block in result.block_complexities],
        }
    if result.referenced_hotspots:
        llm_complexity["referenced_hotspots"] = result.referenced_hotspots
    if result.improvements:
        llm_complexity["improvements"] = [asdict(improvement) for improvement in result.improvements]
    if result.optimization_warnings:
        llm_complexity["optimization_warnings"] = result.optimization_warnings

    snippet = Snippet(
        language=result.language,
        original_code=result.original_code,
        explanation=result.explanation,
        optimized_code=result.optimized_code,
        static_complexity_json=json.dumps(static_complexity),
        llm_complexity_json=json.dumps(llm_complexity),
        semgrep_findings_json=json.dumps(result.semgrep_findings),
    )

    return save_snippet(snippet)


def _validate_optimized_code_syntax(code: str, language: str) -> list[str]:
    """Run deterministic syntax validation for optimized code and return warnings.

    This stage is always executed. It does not block persistence or diff
    generation; warnings are surfaced to the user output.
    """
    warnings: list[str] = []
    lang = (language or "").strip().lower()

    if not code.strip():
        warnings.append("Optimized output is empty; syntax validation skipped.")
        return warnings

    if lang == "python":
        try:
            ast.parse(code)
        except SyntaxError as exc:
            line = exc.lineno or 0
            msg = exc.msg or "invalid syntax"
            warnings.append(
                f"Optimized Python output failed syntax check at line {line}: {msg}."
            )
        return warnings

    if lang == "javascript":
        try:
            import tree_sitter_javascript as tsjava
            from tree_sitter import Language, Parser

            parser = Parser(Language(tsjava.language()))
            tree = parser.parse(code.encode("utf-8"))
            if getattr(tree.root_node, "has_error", False):
                warnings.append(
                    "Optimized JavaScript output failed syntax check (tree-sitter parse errors detected)."
                )
        except Exception as exc:  # pylint: disable=broad-except
            warnings.append(f"Optimized JavaScript syntax check unavailable: {exc}")
        return warnings

    warnings.append(f"Optimized syntax check skipped: unsupported language '{language}'.")
    return warnings


def _validate_llm_outputs(
    explanation: str,
    optimized_code: str,
    source_code: str,
    language: str,
    semgrep_findings: list[dict],
    improvements: list,
    model: str,
    analysis_mode: str,
) -> None:
    """Fail fast when the LLM returns degenerate analysis output."""
    exp_debug = get_last_llm_debug("explanation")
    opt_debug = get_last_llm_debug("optimization")

    if not explanation or not explanation.strip():
        raise RuntimeError(
            f"LLM explanation returned empty content for model '{model}' in '{analysis_mode}' mode. "
            f"Explanation debug: {_format_llm_debug(exp_debug)}"
        )

    if not optimized_code or not optimized_code.strip():
        raise RuntimeError(
            f"LLM optimization returned empty code for model '{model}' in '{analysis_mode}' mode. "
            f"Optimization debug: {_format_llm_debug(opt_debug)}"
        )

    if semgrep_findings and optimized_code == source_code:
        finding_count = len(semgrep_findings)
        severity_hits = sum(1 for finding in semgrep_findings if str(finding.get("severity", "")).lower() in {"warning", "error"})
        raise RuntimeError(
            "LLM optimization returned the original source unchanged despite "
            f"{finding_count} Semgrep finding(s) ({severity_hits} warning/error). "
            f"Model '{model}' in '{analysis_mode}' mode did not produce a changed optimized snippet for {language}. "
            f"Optimization debug: {_format_llm_debug(opt_debug)} "
            f"Explanation debug: {_format_llm_debug(exp_debug)}"
        )


def _format_llm_debug(payload: dict) -> str:
    """Render a one-line debug summary from captured LLM response data."""
    if not payload:
        return "<no debug payload captured>"

    raw = str(payload.get("raw_content", "")).strip().replace("\n", "\\n")

    parsed = payload.get("parsed", {})
    parsed_keys = ",".join(sorted(parsed.keys())) if isinstance(parsed, dict) and parsed else "<none>"
    finish_reason = payload.get("finish_reason") or "<none>"
    provider_model = payload.get("provider_model") or "<none>"
    prompt_chars = payload.get("prompt_chars") or "<none>"
    error_blob = payload.get("error")
    error_summary = str(error_blob).replace("\n", " ") if error_blob is not None else "<none>"
    usage = payload.get("usage", {}) if isinstance(payload.get("usage"), dict) else {}
    token_summary = (
        f"prompt={usage.get('prompt_tokens')},completion={usage.get('completion_tokens')},total={usage.get('total_tokens')}"
        if usage else "prompt=<none>,completion=<none>,total=<none>"
    )
    return (
        f"finish_reason={finish_reason}; provider_model={provider_model}; parsed_keys={parsed_keys}; "
        f"prompt_chars={prompt_chars}; tokens={token_summary}; error={error_summary}; raw_content={raw}"
    )

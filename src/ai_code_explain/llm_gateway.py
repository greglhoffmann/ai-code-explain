"""LLM gateway — OpenRouter API integration.

Responsibilities:
- Assemble structured prompts grounded with AST metadata and Semgrep findings
- Call the OpenRouter API (OpenAI-compatible) to obtain:
    1. Explanation with referenced hotspots
    2. Complexity refinement (llm_adjusted_estimate)
    3. Optimization suggestions with optimized_code
- Parse and validate JSON responses
- Return typed result objects

All prompts are grounded with deterministic data; the LLM is never
asked to invent AST spans or line numbers.
"""

from __future__ import annotations

import json
import os
import re
from threading import Lock
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from openai import OpenAI

from .models import (
    ASTSpan,
    BlockComplexity,
    ComplexityEstimate,
    Improvement,
    StaticMetadata,
)

# ---------------------------------------------------------------------------
# Client bootstrap
# ---------------------------------------------------------------------------

# Fast model: low latency, coding-optimised (Poolside Laguna M.1)
_DEFAULT_FAST_MODEL = "poolside/laguna-m.1:free"
# Reasoning model: deeper analysis, larger parameter count (NVIDIA Nemotron 3 Super) **NOTE: prompts are logged by the provider for this model, so only use with non-sensitive code**
_DEFAULT_REASONING_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

# Legacy env var kept for backward compatibility — overrides both slots when set.
_LEGACY_MODEL_ENV = "OPENROUTER_MODEL"


_cached_client: OpenAI | None = None
_last_llm_debug_lock = Lock()
_last_llm_debug: dict[str, dict[str, Any]] = {
    "explanation": {},
    "optimization": {},
    "complexity": {},
}


def get_last_llm_debug(kind: str) -> dict[str, Any]:
    """Return the latest captured debug payload for an LLM stage."""
    with _last_llm_debug_lock:
        value = _last_llm_debug.get(kind, {})
        return dict(value) if isinstance(value, dict) else {}


def _record_llm_debug(
    kind: str,
    model: str,
    response: Any,
    raw: str,
    parsed: dict[str, Any],
    prompt_chars: int = 0,
) -> None:
    """Store a compact debug snapshot for the latest LLM stage call."""
    usage = getattr(response, "usage", None)
    usage_dict: dict[str, Any] = {}
    if usage is not None:
        usage_dict = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    finish_reason = None
    choices = getattr(response, "choices", None)
    if choices and len(choices) > 0:
        finish_reason = getattr(choices[0], "finish_reason", None)

    snapshot = {
        "kind": kind,
        "model": model,
        "provider_model": getattr(response, "model", None),
        "response_id": getattr(response, "id", None),
        "finish_reason": finish_reason,
        "prompt_chars": prompt_chars,
        "raw_content": raw,
        "parsed": parsed if isinstance(parsed, dict) else {},
        "usage": usage_dict,
        "error": getattr(response, "error", None),
    }
    with _last_llm_debug_lock:
        _last_llm_debug[kind] = snapshot


def _get_client() -> OpenAI:
    """Return a cached OpenAI-compatible client pointing at OpenRouter."""
    global _cached_client
    if _cached_client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        _cached_client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    return _cached_client


def _get_model(mode: str = "fast") -> str:
    """Resolve the model ID for a given mode.

    Priority order:
    1. ``OPENROUTER_MODEL`` env var (single-model override — applies to both modes)
    2. ``OPENROUTER_FAST_MODEL`` / ``OPENROUTER_REASONING_MODEL`` env vars
    3. Built-in defaults

    Args:
        mode: ``"fast"`` for the low-latency model, ``"reasoning"`` for the
              higher-capability reasoning model.
    """
    default = os.environ.get(_LEGACY_MODEL_ENV)
    if default:
        return default
    if mode == "reasoning":
        return os.environ.get("OPENROUTER_REASONING_MODEL", _DEFAULT_REASONING_MODEL)
    return os.environ.get("OPENROUTER_FAST_MODEL", _DEFAULT_FAST_MODEL)


# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------


def _hotspot_payload(hotspots: list[ASTSpan]) -> list[dict]:
    """Serialise hotspots to plain dicts suitable for JSON prompt embedding."""
    return [
        {
            "node_id": h.node_id,
            "type": h.node_type,
            "name": h.name,
            "label": h.label,
            "start_line": h.start_line,
            "end_line": h.end_line,
        }
        for h in hotspots
    ]


def _static_metadata_payload(meta: StaticMetadata) -> dict:
    """Serialise static analysis metadata for prompt embedding."""
    return {
        "language": meta.language,
        "loops": meta.loops,
        "nested_loops": meta.nested_loops,
        "sort_operations": meta.sort_operations,
        "recursive_calls": meta.recursive_calls,
        "hashmap_usage": meta.hashmap_usage,
        "comprehensions": meta.comprehensions,
        "async_patterns": meta.async_patterns,
        "baseline_complexity": {
            "time": meta.baseline_time_complexity,
            "space": meta.baseline_space_complexity,
        },
    }


# ---------------------------------------------------------------------------
# Prompt 1 — Explanation
# ---------------------------------------------------------------------------

_EXPLANATION_SYSTEM = """\
You are an expert software engineer performing code analysis.
Return ONLY valid JSON — no markdown fences, no extra prose.
"""

_EXPLANATION_USER_TEMPLATE = """\
Explain the provided code as a markdown-formatted analysis using these exact section headings:

### Summary
A 1-2 sentence description of the overall purpose.

### Key Behaviors
Bullet list covering important control flow, data structures, and algorithmic patterns.

### Issues / Optimizations
Bullet list for any error-prone areas, security concerns, or optimization opportunities.
Omit this section entirely if there are none.

Use the AST metadata and static analysis as grounding context.
When discussing important functions, complexity drivers, optimization targets,
or risky logic blocks, reference the relevant hotspot node_id from the
provided hotspots list.
Never return an empty explanation string. If the snippet is simple, still
summarize the purpose and the main control-flow or data-flow behaviors.

{verbosity_instruction}

### Code
```
{code}
```

### AST Metadata
{metadata_json}

### Hotspots
{hotspots_json}

{semgrep_section}
{local_context_section}

Return a JSON object matching exactly:
{{
  "explanation": "<markdown string>",
  "referenced_hotspots": [
    {{ "node_id": "<string>", "reason": "<string>" }}
  ]
}}
"""


def _call_explanation(
    client: OpenAI,
    model: str,
    code: str,
    metadata: StaticMetadata,
    semgrep_findings: list[dict],
    local_context: str | None,
    analysis_mode: str = "concise",
) -> tuple[str, list[dict]]:
    """Call the LLM for a plain-English explanation.

    Returns:
        (explanation_text, referenced_hotspots_list)
    """
    semgrep_section = ""
    if semgrep_findings:
        semgrep_section = f"### Semgrep Findings\n{json.dumps(semgrep_findings, indent=2)}\n"

    local_context_section = ""
    if local_context:
        local_context_section = f"### Local Filesystem Context\n{local_context}\n"

    user_message = _EXPLANATION_USER_TEMPLATE.format(
        code=code,
        metadata_json=json.dumps(_static_metadata_payload(metadata), indent=2),
        hotspots_json=json.dumps(_hotspot_payload(metadata.hotspots), indent=2),
        semgrep_section=semgrep_section,
        local_context_section=local_context_section,
        verbosity_instruction=_verbosity_instruction(analysis_mode, "explanation"),
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _EXPLANATION_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    raw = _extract_response_content(response)
    data = _safe_json(raw)
    _record_llm_debug("explanation", model, response, raw, data, prompt_chars=len(user_message))
    explanation = _normalize_explanation_markdown(data.get("explanation", ""))
    return explanation, data.get("referenced_hotspots", [])


# ---------------------------------------------------------------------------
# Prompt 2 — Complexity Refinement
# ---------------------------------------------------------------------------

_COMPLEXITY_SYSTEM = """\
You are assisting with algorithmic complexity analysis.
Return ONLY valid JSON — no markdown fences, no extra prose.
"""

_COMPLEXITY_USER_TEMPLATE = """\
Static analysis detected the following patterns:
{detected_patterns}

Baseline complexity estimate:
- time: {static_time}
- space: {static_space}

### Code
```
{code}
```

Tasks:
1. Give your own independent estimate
2. Identify semantic patterns missed by static analysis
   (e.g., binary search, memoization, graph traversal, independent loops, dynamic programming)
3. Explain your reasoning
4. Provide confidence levels
5. Explain dominant runtime factors

Format the "reasoning" field as a concise markdown bullet list of the key factors
that drive the complexity (e.g. "- factor → complexity").

{verbosity_instruction}

Return a JSON object matching exactly:
{{
  "time": "<Big-O string>",
  "space": "<Big-O string>",
  "confidence": "high|medium|low",
  "reasoning": "<markdown bullet list string>"
}}
"""


def _call_complexity(
    client: OpenAI,
    model: str,
    code: str,
    metadata: StaticMetadata,
    analysis_mode: str = "concise",
) -> ComplexityEstimate:
    """Call the LLM to refine the static complexity baseline.

    Returns:
        A ComplexityEstimate with both static and LLM-adjusted values.
    """
    patterns = _format_patterns(metadata)

    user_message = _COMPLEXITY_USER_TEMPLATE.format(
        detected_patterns=patterns,
        static_time=metadata.baseline_time_complexity,
        static_space=metadata.baseline_space_complexity,
        code=code,
        verbosity_instruction=_verbosity_instruction(analysis_mode, "complexity"),
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _COMPLEXITY_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    raw = _extract_response_content(response)
    data = _safe_json(raw)
    _record_llm_debug("complexity", model, response, raw, data, prompt_chars=len(user_message))

    return ComplexityEstimate(
        static_time=metadata.baseline_time_complexity,
        static_space=metadata.baseline_space_complexity,
        static_confidence="high",
        llm_time=data.get("time"),
        llm_space=data.get("space"),
        llm_confidence=data.get("confidence"),
        llm_reasoning=_normalize_reasoning_markdown(data.get("reasoning")),
    )


def _normalize_explanation_markdown(text: Any) -> str:
    """Normalize explanation markdown to reduce provider-formatting artifacts."""
    if text is None:
        return ""

    normalized = str(text).replace("\r\n", "\n").strip()
    if "\\n" in normalized and "\n" not in normalized:
        normalized = normalized.replace("\\n", "\n")

    # Some models occasionally prepend a stray colon before markdown headings.
    if normalized.startswith(":"):
        normalized = normalized.lstrip(":").lstrip()

    return normalized


def _normalize_reasoning_markdown(text: Any) -> str:
    """Normalize complexity reasoning markdown and split mashed bullet lines."""
    if text is None:
        return ""

    normalized = str(text).replace("\r\n", "\n").strip()
    if "\\n" in normalized and "\n" not in normalized:
        normalized = normalized.replace("\\n", "\n")
    if normalized.startswith(":"):
        normalized = normalized.lstrip(":").lstrip()

    # Convert single-line bullet runs like "- a - b - c" into one bullet per line.
    if "\n" not in normalized and normalized.startswith("-"):
        normalized = re.sub(r"\s+-\s+", "\n- ", normalized)

    return normalized


def _format_patterns(meta: StaticMetadata) -> str:
    """Convert StaticMetadata flags into a bullet list for the prompt."""
    lines: list[str] = []
    if meta.loops:
        lines.append(f"- {meta.loops} loop(s) detected")
    if meta.nested_loops:
        lines.append("- Nested loops detected (O(n^2) risk)")
    if meta.sort_operations:
        lines.append(f"- {meta.sort_operations} sort operation(s) (O(n log n))")
    if meta.recursive_calls:
        lines.append("- Recursive calls detected (exponential risk)")
    if meta.hashmap_usage:
        lines.append("- Hash map usage (O(1) average lookup)")
    if meta.comprehensions:
        lines.append(f"- {meta.comprehensions} comprehension(s) / array transform(s)")
    if meta.async_patterns:
        lines.append(f"- {meta.async_patterns} async/await pattern(s)")
    return "\n".join(lines) if lines else "- No significant patterns detected"


# ---------------------------------------------------------------------------
# Prompt 3 — Optimization
# ---------------------------------------------------------------------------

_OPTIMIZATION_SYSTEM = """\
You are an expert software engineer performing code optimization review.
Return ONLY valid JSON — no markdown fences, no extra prose.
"""

_OPTIMIZATION_USER_TEMPLATE = """\
Suggest improvements that may enhance:
- readability
- maintainability
- performance
- idiomatic usage
- algorithmic complexity

You MAY suggest:
- better APIs or standard library improvements
- alternative algorithms
- better data structures

If behavior could change, explicitly explain risks, assumptions, and tradeoffs.
If Semgrep findings are present, return a changed full_optimized_code that
addresses at least one finding.
Do not return the original code unchanged unless you have no actionable
changes and you explain why in the improvements list.
When an optimization targets a specific region, reference the relevant
hotspot node_id from the provided hotspots list in the description or tradeoffs.

Format each "description" and "tradeoffs" field as a short markdown sentence or
bullet list. Each improvement should be specific and actionable.

### Code
```
{code}
```

### Static Analysis
{metadata_json}

### Hotspots
{hotspots_json}

### Semgrep Findings
{semgrep_json}

Return a JSON object matching exactly:
{{
    "full_optimized_code": "<full optimized source code string>",
    "improvements": [
    {{
      "category": "algorithmic|readability|idiomatic|api|data_structure",
      "impact": "high|medium|low",
      "behavior_change_risk": "high|medium|low",
      "description": "<markdown string>",
      "tradeoffs": "<markdown string>",
      "optimized_code": "<optional focused snippet or full source string>"
    }}
  ]
}}

Rules for code fields:
- "full_optimized_code" MUST always be a complete runnable snippet.
- For each improvement: include "optimized_code" only when it differs from
    "full_optimized_code"; otherwise set it to exactly
    "Refer to Canonical Optimized Source".
- Never return a patch fragment, diff hunk, or partial function body.
- If no meaningful change is needed, return the original full code unchanged.
"""


def _call_optimization(
    client: OpenAI,
    model: str,
    code: str,
    metadata: StaticMetadata,
    semgrep_findings: list[dict],
    analysis_mode: str = "concise",
) -> tuple[list[Improvement], str]:
    """Call the LLM for optimization suggestions.

    Returns:
        (improvements_list, best_optimized_code_string)
    """
    user_message = _OPTIMIZATION_USER_TEMPLATE.format(
        code=code,
        metadata_json=json.dumps(_static_metadata_payload(metadata), indent=2),
        hotspots_json=json.dumps(_hotspot_payload(metadata.hotspots), indent=2),
        semgrep_json=json.dumps(semgrep_findings, indent=2),
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _OPTIMIZATION_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    raw = _extract_response_content(response)
    data = _safe_json(raw)
    _record_llm_debug("optimization", model, response, raw, data, prompt_chars=len(user_message))

    _CANONICAL_SENTINEL = "refer to canonical optimized source"

    improvements: list[Improvement] = []
    best_code = _coerce_full_snippet(code, data.get("full_optimized_code", ""))
    for item in data.get("improvements", []):
        raw_item_code = item.get("optimized_code", "")
        # Preserve the sentinel string so the UI can display "Refer to Canonical
        # Optimized Source" rather than falling back to original code via _coerce_full_snippet.
        if raw_item_code.strip().lower() == _CANONICAL_SENTINEL:
            item_code = "Refer to Canonical Optimized Source"
        elif not raw_item_code.strip():
            item_code = ""
        else:
            item_code = _coerce_full_snippet(code, raw_item_code)
        improvements.append(
            Improvement(
                category=item.get("category", "readability"),
                impact=item.get("impact", "low"),
                behavior_change_risk=item.get("behavior_change_risk", "low"),
                description=item.get("description", ""),
                tradeoffs=item.get("tradeoffs", ""),
                optimized_code=item_code,
            )
        )
        # Use the first high-impact improvement as the canonical optimized code.
        # Skip sentinel values — they are not real code.
        if item.get("impact") == "high" and item_code not in (code, "", "Refer to Canonical Optimized Source"):
            best_code = item_code

    # Fallback: take the first non-original full snippet if no high-impact one was found.
    # Skip sentinel values.
    if best_code == code and improvements:
        first_code = improvements[0].optimized_code
        if first_code not in (code, "", "Refer to Canonical Optimized Source"):
            best_code = first_code

    return improvements, best_code


def _coerce_full_snippet(original: str, candidate: str) -> str:
    """Accept only full-snippet candidates; otherwise fall back to original code."""
    if not candidate or not candidate.strip():
        return original

    text = candidate.strip()
    lower = text.lower()

    # Heuristics to reject obvious fragments and diff output.
    if lower.startswith("@@") or lower.startswith("diff "):
        return original
    if text.startswith("+") or text.startswith("-"):
        return original

    # Very short outputs are usually fragments unless the original is also tiny.
    if len(text.splitlines()) < 2 and len(original.splitlines()) > 3:
        return original

    return candidate


def _estimate_block_static(node_type: str) -> tuple[str, str, str]:
    """Return deterministic static complexity estimate for a hotspot block."""
    if node_type == "nested_loop":
        return "O(n^2)", "O(1)", "high"
    if node_type == "sort":
        return "O(n log n)", "O(n)", "high"
    if node_type == "recursion":
        return "O(2^n)", "O(n)", "medium"
    if node_type in ("loop", "comprehension", "array_transform"):
        return "O(n)", "O(1)", "high"
    return "O(n)", "O(1)", "medium"


def _build_block_metadata(base: StaticMetadata, span: ASTSpan, time_c: str, space_c: str) -> StaticMetadata:
    """Create focused metadata for a single hotspot block complexity prompt."""
    return StaticMetadata(
        language=base.language,
        loops=1 if span.node_type in ("loop", "nested_loop") else 0,
        nested_loops=span.node_type == "nested_loop",
        sort_operations=1 if span.node_type == "sort" else 0,
        recursive_calls=span.node_type == "recursion",
        hashmap_usage=base.hashmap_usage,
        comprehensions=1 if span.node_type in ("comprehension", "array_transform") else 0,
        async_patterns=1 if span.node_type == "async_pattern" else 0,
        imports=base.imports,
        function_names=base.function_names,
        hotspots=[span],
        baseline_time_complexity=time_c,
        baseline_space_complexity=space_c,
    )


def _select_blocks(spans: list[ASTSpan], max_blocks: int = 8) -> list[ASTSpan]:
    """Return up to max_blocks largest non-overlapping spans (greedy by size)."""
    sorted_spans = sorted(spans, key=lambda s: s.end_line - s.start_line, reverse=True)
    selected: list[ASTSpan] = []
    for span in sorted_spans:
        if len(selected) >= max_blocks:
            break
        if not any(span.start_line <= s.end_line and span.end_line >= s.start_line for s in selected):
            selected.append(span)
    return selected


def _call_block_complexities(
    client: OpenAI,
    model: str,
    code: str,
    metadata: StaticMetadata,
) -> list[BlockComplexity]:
    """Run focused static+LLM complexity analysis for individual hotspot blocks in parallel."""
    lines = code.splitlines()
    if not lines:
        return []

    supported = {"function", "loop", "nested_loop", "sort", "recursion", "comprehension", "array_transform"}
    spans = [h for h in metadata.hotspots if h.node_type in supported and h.start_line > 0 and h.end_line >= h.start_line]
    spans = _select_blocks(spans)
    if not spans:
        return []

    def _analyze_span(span: ASTSpan) -> BlockComplexity | None:
        start_idx = max(0, span.start_line - 1)
        end_idx = min(len(lines), span.end_line)
        block_code = "\n".join(lines[start_idx:end_idx]).strip()
        if not block_code:
            return None
        static_time, static_space, static_conf = _estimate_block_static(span.node_type)
        block_meta = _build_block_metadata(metadata, span, static_time, static_space)
        llm = _call_complexity(client, model, block_code, block_meta)
        return BlockComplexity(
            block_id=span.node_id,
            node_type=span.node_type,
            label=span.label or span.name,
            start_line=span.start_line,
            end_line=span.end_line,
            static_time=static_time,
            static_space=static_space,
            static_confidence=static_conf,
            llm_time=llm.llm_time,
            llm_space=llm.llm_space,
            llm_confidence=llm.llm_confidence,
            llm_reasoning=llm.llm_reasoning,
        )

    with ThreadPoolExecutor(max_workers=min(len(spans), 4)) as executor:
        results = list(executor.map(_analyze_span, spans))
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _safe_json(raw: str) -> dict:
    """Parse JSON string and return a dict, falling back to {} on any failure.

    Guarantees a dict return type even when the JSON value is valid but not
    an object (e.g. ``null``, arrays, primitives).
    """
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_response_content(response: Any) -> str:
    """Safely extract first chat choice message content.

    Handles provider edge-cases where choices/message/content may be None.
    Returns a JSON-object fallback string so downstream parsing remains stable.
    """
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return "{}"
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            return "{}"
        content = getattr(message, "content", None)
        return content if isinstance(content, str) and content else "{}"
    except Exception:  # pylint: disable=broad-except
        return "{}"


def _verbosity_instruction(mode: str, prompt_type: str) -> str:
    """Return prompt instruction text for concise vs detailed output style."""
    normalized = (mode or "concise").strip().lower()
    if normalized not in {"concise", "detailed"}:
        normalized = "concise"

    if prompt_type == "explanation":
        if normalized == "detailed":
            return (
                "Output style: detailed. Keep Summary to 2-4 sentences; include 3+ "
                "Key Behaviors bullets; include Issues / Optimizations when relevant with "
                "concrete examples."
            )
        return (
            "Output style: concise. Keep Summary to 2 short sentences; include at most "
            "2 Key Behaviors bullets; include Issues / Optimizations (brief bullets) when relevant."
        )

    if prompt_type in {"complexity", "optimization"}: # no restriction for complexity or optimization prompts to avoid incomplete fixes or missing complexity factors.
        return ""

    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_llm_pipeline(
    code: str,
    metadata: StaticMetadata,
    semgrep_findings: list[dict],
    local_context: str | None = None,
    model_mode: str = "fast",
    analysis_mode: str = "concise",
    progress_callback: Callable[[str], None] | None = None,
    include_block_complexity: bool | None = None,
) -> tuple[str, list[dict], ComplexityEstimate, list[Improvement], str, list[BlockComplexity]]:
    """Execute the full three-stage LLM prompt pipeline in parallel.

    All three stages are dispatched concurrently via a ThreadPoolExecutor
    since they are independent (no inter-prompt data dependencies):
    1. Explanation synthesis
    2. Complexity refinement
    3. Optimization analysis

    Args:
        code: Original source code.
        metadata: StaticMetadata from AST analysis.
        semgrep_findings: Findings from Semgrep.
        local_context: Optional local filesystem context (in-process).
        model_mode: ``"fast"`` (default) uses the low-latency model;
                    ``"reasoning"`` uses the higher-capability, higher max. input/output token model.
        analysis_mode: ``"concise"`` (default) for short outputs, ``"detailed"`` for expanded outputs.
        include_block_complexity: Explicit override for per-block complexity.
                      If ``None``, falls back to env var
                      ``AI_CODE_EXPLAIN_BLOCK_COMPLEXITY``.

    Returns:
        Tuple of (explanation, referenced_hotspots, complexity, improvements,
        optimized_code, block_complexities).
    """
    client = _get_client()
    model = _get_model(model_mode)

    if progress_callback:
        progress_callback("LLM: running 3 analyses in parallel…")

    with ThreadPoolExecutor(max_workers=3) as executor:
        fut_explanation = executor.submit(
            _call_explanation, client, model, code, metadata, semgrep_findings, local_context, analysis_mode
        )
        fut_complexity = executor.submit(
            _call_complexity, client, model, code, metadata, analysis_mode
        )
        fut_optimization = executor.submit(
            _call_optimization, client, model, code, metadata, semgrep_findings, analysis_mode
        )

        explanation, referenced_hotspots = fut_explanation.result()
        complexity = fut_complexity.result()
        improvements, optimized_code = fut_optimization.result()

    block_complexities: list[BlockComplexity] = []
    enable_block_llm = include_block_complexity
    if enable_block_llm is None:
        enable_block_llm = os.environ.get("AI_CODE_EXPLAIN_BLOCK_COMPLEXITY", "0").strip() in {"1", "true", "yes"}
    if enable_block_llm:
        if progress_callback:
            progress_callback("LLM: computing per-block complexity…")
        block_complexities = _call_block_complexities(client, model, code, metadata)

    if progress_callback:
        progress_callback("LLM analysis complete.")

    return explanation, referenced_hotspots, complexity, improvements, optimized_code, block_complexities

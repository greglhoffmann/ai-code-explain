# Code Analysis Tool — Implementation Specification

## Architectural Principle
**Combine deterministic static analysis with LLM-assisted semantic reasoning** to improve explainability, optimization quality, and complexity estimation reliability. Keep all reliably-computable logic outside the LLM.

## Build Goal
Analyze Python/JavaScript snippets: explain in plain English, estimate time/space complexity (hybrid static + LLM), propose optimizations, show diffs, persist history locally.

---

## Mandatory Coding Standards
- Modular, concise (fewest LOC), clear (human-readable, no abbreviated names) code.
- No trivial one-line functions.
- Separation of concerns: AST parsing, complexity analysis, LLM interaction, UI rendering, and persistence in separate modules/classes.
- Type annotations and docstrings for all functions and methods, comments throughout non-obvious logic blocks.
- Readme with high-level architecture/explanation, process flow, and detailed operational/setup instructions (including all external dependencies).
- Always update the README when relevant code changes are made. The README should be the source of truth for how the system works and how to set it up.
- Never add fallbacks for legacy structures - no need for backwards compatibility/migration.

---

## DO NOT IMPLEMENT — Future Roadmap (document only)

| Feature | Potential value |
| ------- | --------------- |
| Git integration | Optimization lineage, patch tracking, repo-aware workflows, commit-level reasoning |
| Repo-wide code graph analysis | Call graph traversal, dependency impact analysis (possible: Serena, advanced tree-sitter) |
| Execution sandbox | Runtime benchmarking, empirical complexity validation, test execution |
| Embedding/RAG retrieval | Large repo semantic search, cross-file retrieval |
| Multi-agent orchestration | Planner/analyzer/reviewer agent pipeline |

---

## LLM Responsibilities (only)
- Natural language explanation synthesis
- Semantic interpretation and algorithm recognition
- Optimization ideation and tradeoff explanation
- Complexity semantic refinement

## Deterministic Responsibilities (outside LLM)
- AST parsing, loop/recursion detection, comprehension/import analysis
- **AST span extraction** (hotspot node IDs, stable line/column ranges)
- Diff generation
- Snippet persistence
- Semgrep findings
- Baseline complexity estimation

## Context Integration Constraint
Local context integrations MUST augment observability and contextual analysis only.
MUST NOT create autonomous agents, recursive orchestration, or uncontrolled tool execution.

---

## Required Tech Stack

| Layer | Choice |
| ----- | ------ |
| Language | Python |
| UI | Textual |
| Terminal Rendering | Rich |
| Python Parsing | `ast` (built-in) |
| JS Parsing | tree-sitter |
| Static Analysis | Semgrep |
| Persistence | SQLite |
| ORM | SQLModel |
| LLM Gateway | OpenRouter |
| Local Context | In-process local filesystem context (read-only, sandboxed) |
| Diff Rendering | Rich diff panels |

**Infrastructure:** Everything local. Only external dependency is the OpenRouter API.

---


## UI Layout

Use Textual to build a three-pane terminal GUI:
- **Left pane:** Snippet History Sidebar (navigable list)
- **Main pane:** Code Editor/Viewer (syntax-highlighted via Rich)
- **Bottom pane:** Tabbed analysis — Explanation, Complexity, Semgrep Findings, Diff
  - **Hotspot integration** each bottom pane tab (except diff) includes AST span hotspots to key functions, logic blocks, and complexity hotspots. Selecting a hotspot scrolls the code view to the corresponding region, highlights it, and focuses any related explanation text.

Keyboard-first navigation (no mouse required). Use Textual panels, tabs, and scrollable views.

---

## Database Schema

```sql
CREATE TABLE snippets (
  id                     INTEGER PRIMARY KEY,
  language               TEXT,
  original_code          TEXT,
  explanation            TEXT,
  optimized_code         TEXT,
  static_complexity_json TEXT,
  llm_complexity_json    TEXT,
  semgrep_findings_json  TEXT,
  created_at             DATETIME
);
```

Store BOTH `static_complexity_json` and `llm_complexity_json`. Never overwrite the static baseline with the LLM result.

---

## End-to-End Processing Flow

Execute in this exact order for every submitted snippet:

1. Detect language (Python or JavaScript)
2. Parse AST — Python: `ast` module; JavaScript: tree-sitter
3. Run static complexity estimation → produce `static_complexity_json`
4. Run Semgrep analysis → produce `semgrep_findings_json`
5. Run in-process local filesystem contextual lookup *(optional — only when imports reference local files)*
6. Assemble structured prompt with AST metadata + static results + Semgrep findings
7. Call LLM via OpenRouter → produce explanation, `llm_complexity_json`, `optimized_code`
8. Generate diff (original vs optimized)
9. Persist all results to SQLite
10. Render: Explanation tab, Complexity tab, Diff tab, Findings tab

---

## Static Analysis Layer

### Python — use `ast` to detect:
- loops (for/while)
- nested loops
- recursion (calls matching enclosing function name)
- comprehensions
- imports
- function definitions
- collection allocations
- sort operations (`.sort()`, `sorted()`)

### JavaScript — use tree-sitter to detect:
- loops (for/while/do-while)
- nested loops
- async patterns (await, Promise)
- recursion
- array transformations
- map/filter/reduce calls
- sorting (`.sort()`)

### AST Span Extraction

During AST traversal, extract a stable span record for every significant node. These are passed into LLM prompts as `hotspots` and used to drive UI highlighting.

```json
{
  "node_id": "func_1",
  "type": "function",
  "name": "process_users",
  "start_line": 4,
  "end_line": 18
}
```

**Supported node types for spans:**

| Category | Types |
| -------- | ----- |
| Functions | function definitions, methods |
| Logic blocks | loops, conditionals, recursion regions, comprehensions |
| Performance hotspots | nested loops, repeated membership checks, sorting, recursion |

**MUST:** derive all spans from AST metadata.
**MUST NOT:** let the LLM invent spans, use regex-based highlighting, or infer regions from generated prose.

---

## Complexity System

### Deterministic Baseline Rules

| Pattern             | Estimate         |
| ------------------- | ---------------- |
| Single loop         | O(n)             |
| Nested loops        | O(n^2)           |
| Sorting             | O(n log n)       |
| Binary search       | O(log n)         |
| Recursive branching | Exponential risk |
| Hash lookups        | O(1) avg         |

AST alone misses semantic intent (binary search, memoization, graph traversal, independent loops, dynamic programming). Static analyzer produces the baseline; LLM refines/adjusts.

### Static Metadata JSON (produced by AST walker, passed to LLM prompt)

```json
{
  "loops": 2,
  "nested_loops": true,
  "sort_operations": 1,
  "recursive_calls": false,
  "hashmap_usage": true,
  "baseline_complexity": {
    "time": "O(n^2)",
    "space": "O(n)"
  }
}
```

### Stored Complexity JSON (both fields persisted to DB)

```json
{
  "static_estimate": {
    "time": "O(n^2)",
    "space": "O(n)",
    "confidence": "high"
  },
  "llm_adjusted_estimate": {
    "time": "O(n log n)",
    "space": "O(n)",
    "confidence": "medium",
    "reasoning": "Sorting dominates runtime"
  }
}
```


---

## Prompt Templates

### Prompt 1 — Explanation

```
You are an expert software engineer.

Explain the provided code in plain English in 2-4 concise sentences.

Focus on:
- overall purpose
- important control flow
- key data structures
- algorithmic behavior

Use the AST metadata and static analysis as grounding context.

When discussing important functions, complexity drivers, optimization targets,
or risky logic blocks, reference the relevant hotspot node_id from the
provided hotspots list.
```

**Hotspot input passed to the prompt:**
```json
{
  "hotspots": [
    {
      "node_id": "loop_2",
      "type": "nested_loop",
      "label": "Nested iteration over users/orders",
      "start_line": 12,
      "end_line": 19
    }
  ]
}
```

**Expected LLM output includes `referenced_hotspots`:**
```json
{
  "explanation": "...",
  "referenced_hotspots": [
    {
      "node_id": "loop_2",
      "reason": "Dominates runtime complexity"
    }
  ]
}
```

### Prompt 2 — Complexity Refinement

```
You are assisting with complexity analysis.

Static analysis detected:
- {detected_patterns}

Baseline estimate:
- time: {static_time}
- space: {static_space}

Tasks:
1. Confirm or revise the estimate
2. Identify semantic patterns missed by static analysis
   (e.g., binary search, memoization, graph traversal, independent loops, dynamic programming)
3. Explain your reasoning
4. Provide confidence levels
5. Explain dominant runtime factors

Return structured JSON matching the llm_adjusted_estimate schema.
```

### Prompt 3 — Optimization

```
Suggest improvements that may enhance:
- readability
- maintainability
- performance
- idiomatic usage
- algorithmic complexity

You MAY suggest:
- better APIs
- standard library improvements
- alternative algorithms
- better data structures

If behavior could change, explicitly explain:
- risks
- assumptions
- tradeoffs

Return structured JSON only, matching the optimization output schema.
```

---

## Optimization Output Schema

```json
{
  "improvements": [
    {
      "category": "algorithmic",
      "impact": "high",
      "behavior_change_risk": "low",
      "description": "Replace nested iteration with hashmap lookup",
      "tradeoffs": "Uses additional memory",
      "optimized_code": "..."
    }
  ]
}
```

`category`: `algorithmic` | `readability` | `idiomatic` | `api` | `data_structure`
`impact`: `high` | `medium` | `low`
`behavior_change_risk`: `high` | `medium` | `low`

---

## Semgrep Integration

Run Semgrep deterministically before calling the LLM. Pass findings as structured context in the prompt.

Detect:
- nested loop inefficiencies
- repeated list membership checks
- unsafe patterns
- anti-patterns
- performance smells

Example finding shape:

```json
{
  "rule": "nested-loop-performance",
  "severity": "warning",
  "message": "Repeated list membership checks detected"
}
```

Store all findings as `semgrep_findings_json` in the DB. Render in the Findings tab.

---

## Local Filesystem Context — Spec and Security Constraints

Trigger local filesystem context lookup only when a snippet contains imports referencing local files.

```python
from utils import normalize_user
# -> local context lookup inspects utils.py for context
```

Use to improve: contextual explanation accuracy, optimization quality, complexity reasoning.

### REQUIRED Security Constraints — document in README and in code comments

```
Local filesystem context access is sandboxed to the active project directory only.
```

| Permission              | Allowed |
| ----------------------- | ------- |
| Read files              | YES     |
| List directory          | YES     |
| Shell execution         | NO      |
| Arbitrary traversal     | NO      |
| External commands       | NO      |
| Write access            | NO      |

---

## LLM Integration

**Gateway:** OpenRouter (OpenAI-compatible API)
**Preferred models:** Qwen, DeepSeek, Gemini — strong coding performance, low cost, fast inference

**Prompt pipeline order:**
1. Explanation synthesis
2. Complexity refinement (uses static metadata as input)
3. Optimization analysis

Use structured JSON output for all LLM responses. Ground every call with deterministic AST metadata and Semgrep findings.

---

## Diff Rendering

Use Rich/Textual side-by-side diff panels. Render `original_code` vs `optimized_code` with:
- syntax highlighting
- inline additions/removals marked
- scrollable view in the Diff tab

---

## Visual Highlighting

Use Rich syntax regions and line highlighting to render hotspot categories in the code view:

| Category | Visual treatment |
| -------- | ---------------- |
| Function definitions | panel border emphasis |
| Performance hotspots | highlighted line range |
| Optimization targets | distinct highlight color |
| Risky logic blocks | warning-style emphasis |

---

## README Requirements

Include all of the following sections:
- Architecture diagram
- Prompt strategy (multi-stage pipeline description)
- Static vs LLM complexity discussion (why both estimates are stored)
- Hallucination mitigation approach (deterministic grounding)
- Local filesystem context security constraints (sandboxing, read-only access)
- Semgrep integration rationale
- Future extensibility (roadmap — DO NOT IMPLEMENT items listed above)


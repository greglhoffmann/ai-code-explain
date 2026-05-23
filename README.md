# AI Code Explain

A terminal-based code analysis tool combining **deterministic static analysis** with **LLM-assisted semantic reasoning** to explain, estimate complexity, and optimize Python and JavaScript code snippets.

---

## Architecture Diagram

```
 code-explain                                      code-explain --analyze FILE
 (interactive TUI)                                 (non-interactive CLI)
┌───────────────────────────────────────────┐       │
│        User Interface (Textual TUI)       │       │
│┌────────┐┌───────────────────────────────┐│       │
││ Snippet││ Code Viewer (syntax highlight)││       │
││ History│├───────────────────────────────┤│       │
││ Sidebar││ Analysis Tabs:                ││       │
││        ││  Explanation │ Complexity     ││       │
││        ││  Semgrep     │ Diff           ││       │
│└────────┘└───────────────────────────────┘│       │
└───────┬───────────────────────────────────┘       │
        │                                           │
        │ paste / edit snippet                      │ reads file from disk
        │ language: heuristic                       │ language: extension → heuristic
        │ sandbox: LOCAL_CONTEXT_SANDBOX_DIR                  │ sandbox: file's parent directory
        │                                           │
        └──────────────┬────────────────────────────┘
                       │
              pipeline.run_pipeline()
                       │
              Step 1: AST complexity + metadata
                       │
                       ├──────────────┬───────────────┐
                       │              │               │
                       ▼              ▼               ▼
          Semgrep (2a) ─── Local FS Context (2b)   Complexity LLM (2c)
                       │              │                
                       │              │
                       ▼              │
                Optimization LLM (3)  │
                  waits on Semgrep    │
                                      ▼
                                Explanation LLM (4)
                   waits on Semgrep + local context
                                      │
                                      ▼
                         Collect + diff + persist (5)
                                      │
                                      ▼
                               SQLite (SQLModel ORM)
```

---

## Entry Points

The tool has two distinct modes of operation — both run the same analysis pipeline:

| Mode | Command | Use when |
|---|---|---|
| **Interactive TUI** | `code-explain` | You want a persistent session — browse history, paste or edit snippets inline, navigate results across four tabs with keyboard shortcuts. Snippet history is saved to SQLite. Language is auto-detected from content heuristics (no file path available). |
| **CLI / non-interactive** | `code-explain --analyze FILE` | You want to analyze a file on disk and print results to stdout — scriptable, CI-friendly. Language is inferred from the file extension first (`.py` → Python, `.js`/`.ts`/`.mjs` → JavaScript), falling back to heuristics. The sandbox for local-import resolution is automatically set to the file's parent directory. |

Both modes persist results to the same SQLite history database.

---

## Process Flow

1. User submits a Python or JavaScript snippet (TUI or `--analyze FILE`)
2. **Language detection** — file extension (when `--analyze FILE`) takes precedence; falls back to regex heuristic keyword analysis
3. **Static AST analysis** — loops, nesting, recursion, comprehensions, sorts, imports, hotspot spans
4. **DAG fan-out (parallel)** — Semgrep runs for deterministic findings, in-process local filesystem context lookup runs for local-import context, and the complexity LLM call starts immediately from source + static metadata.
5. **DAG dependent LLM stages** — optimization waits for Semgrep findings, and explanation is grounded by local filesystem context + Semgrep findings.
6. **Mandatory optimized-code syntax check** — deterministic parse validation runs on the LLM-optimized output; failures are surfaced as user-visible warnings.
7. **Optional block-level complexity** — per-hotspot LLM complexity parallelization (when enabled)
8. **Diff generation** — `difflib` unified diff (original → optimized); regenerated on history reload from stored `original_code` + `optimized_code`
9. **Persistence** — static and LLM complexity stored separately in SQLite; diff is regenerated on load
10. **UI rendering** — Explanation, Complexity, Semgrep, Diff tabs (all scrollable) with hotspot highlighting/navigation

---

## Key Features

### AI Tooling Choices

This project combines several AI + AI-adjacent tools selected for specific roles rather than relying on a single model call:

- **OpenRouter LLMs via OpenAI-compatible client** for explanation, optimization, and semantic time/space complexity estimation. This was chosen for model flexibility, low cost, and straightforward toggling between 'fast' and 'reasoning' LLMs with shared application code and keys.
- **Semgrep** for deterministic security/performance findings. This was chosen to provide rule-based, reproducible signals the LLMs can ground against, especially for known vulnerable or non-idiomatic patterns.
- **Python `ast` + tree-sitter JavaScript** for deterministic structural parsing and hotspot extraction. This was chosen to generate exact span IDs and time/space complexity cues before prompting, so the LLM grounds on verified structure.
- **Local filesystem context (sandboxed, read-only)** for selective grounding on the snippet's/file's local imports. This is implemented in-process (no external server/protocol client call) to improve explanation accuracy with multi-file context while enforcing strict path-boundary controls.

The benefit of this setup is that AI generation is always downstream of deterministic evidence: model output improves readability and synthesis, while static tooling protects accuracy and LLM failure observability.

---

### DAG-Aware Parallel Execution

The runtime uses dependency-aware parallelism, not a single all-independent fan-out.

- After static analysis: Semgrep, local filesystem context lookup, and complexity LLM start in parallel.
- Optimization LLM starts after Semgrep completes (it depends on findings).
- Explanation LLM starts after Semgrep and local filesystem context complete.
- This shortens wall-clock time by overlapping independent work while preserving required data dependencies.

In code, this is orchestrated with a `ThreadPoolExecutor` in `run_pipeline` and explicit future joins at each dependency boundary.

---

### Static vs LLM Complexity

Two separate complexity estimates are stored and displayed:

| Estimate | Method | Stored in |
|---|---|---|
| `static_estimate` | Deterministic AST pattern matching | `static_complexity_json` column |
| `llm_adjusted_estimate` | LLM semantic refinement | `llm_complexity_json` column |

**Why both?** Static analysis is reliable and reproducible but misses semantic intent: it cannot distinguish a binary search from a linear scan, or detect memoization. The LLM understands algorithmic context but can hallucinate. Storing both lets users see where they diverge and apply appropriate trust.

**The static baseline is never overwritten by the LLM result.** Each occupies a separate DB column.

---

### Hallucination Mitigation

Every LLM prompt is grounded with deterministic data computed before the LLM is called:

- **AST metadata JSON** — loop counts, nesting flags, sort counts, recursion flags
- **Hotspot spans** — exact line/column ranges extracted from the AST (never invented by the LLM)
- **Semgrep findings** — concrete rule violations with line numbers
- **Local filesystem context** — actual file contents (not summaries)

The LLM is instructed to reference hotspot `node_id` values from the provided list. It doesn't invent spans or line numbers because those are validated against the AST before being passed to the prompt.

JSON-only response format is enforced (`response_format: json_object`) for all three prompts.

---

### Local Filesystem Context Security Constraints

The local filesystem context integration is **sandboxed** and **read-only**. It is implemented directly in this process (no external server call):

| Permission | Allowed |
|---|---|
| Read files within sandbox | YES |
| List directory within sandbox | YES |
| Shell execution | NO |
| Arbitrary filesystem traversal | NO |
| External commands | NO |
| Write access | NO |

**Path traversal prevention:** All paths are canonicalized with `Path.resolve()` and then validated with `relative_to(sandbox_dir)` before any file is opened. Paths that resolve to existing files *outside* the sandbox are blocked and surfaced as warnings rather than silently ignored — see **Sandbox warnings** below.

**Trigger condition:** local filesystem context lookup is performed only when the snippet contains imports that resolve to local files. Standard library and known third-party packages are skipped.

**Sandbox directory:** When using `--analyze FILE`, the sandbox is automatically set to the analyzed file's parent directory so local imports resolve correctly relative to the file's location. This can be overridden with the `LOCAL_CONTEXT_SANDBOX_DIR` environment variable. In TUI mode (pasted snippets), `LOCAL_CONTEXT_SANDBOX_DIR` is used if set, otherwise the current working directory.

**Sandbox warnings:** If an import resolves to a file that *exists on disk* but is *outside* the sandbox boundary, a warning is displayed in the CLI output and in the Semgrep tab of the TUI. This distinguishes a misconfigured sandbox (or a path-traversal attempt in the snippet) from a simple missing-file miss. Imports that don't resolve to any file at all are silently ignored.

**File limits:** At most 3 local files are read per analysis (up to 4 KB each, ~12 KB total injected context). This is a prompt-quality ceiling, not a security boundary — raising it risks diluting LLM attention on the actual snippet and inflating token cost.

---

### Semgrep Integration

Semgrep runs **before** the LLM call, ensuring findings are available as grounding context.

**Detected patterns include:**
- Nested loop inefficiencies
- Repeated list membership checks
- Unsafe patterns
- Anti-patterns
- Performance smells

Semgrep is invoked as a subprocess with a temporary file — `shell=True` is **never** used.

Findings are stored in `semgrep_findings_json` and rendered in the **Semgrep** tab. They are also included in the optimization and explanation prompts so the LLM can reference specific rule violations.

Semgrep uses tree-sitter for parsing, which has partial error recovery. For **severely broken syntax** (missing colons, mismatched parentheses, etc.), Semgrep may produce no findings or fewer findings because it cannot reliably identify code structures to apply rules to. This is **expected behavior**.

When Semgrep returns a parse error (exit code 4) or stderr output on an empty result set, an informational `semgrep-parse-warning` finding is shown in the Semgrep tab so you know the analysis ran but was impaired.

### Optimized Output Syntax Validation

Optimized code is always syntax-checked in a deterministic stage before final output:

- Python: validated with `ast.parse`
- JavaScript: validated with tree-sitter parse error detection

If validation fails, the run continues, but a warning is surfaced in both CLI output and the TUI Semgrep tab.

---

### Analysis Output/Formatting

LLM responses are requested in **markdown format** and rendered with Textual's `Markdown` widget:

- **Explanation tab** — bulleted *Key Behaviors* and *Risks / Issues* sections
- **Complexity tab** — bullet-list reasoning for the complexity estimate
- **Semgrep tab** — severity-tagged Semgrep rules with line numbers and optimization/sandbox warnings
- **Diff tab** — split original vs optimized panes with changed-line background highlighting

The code viewer uses a read-only `TextArea`, so text remains selectable after analysis. Clicking a hotspot creates an actual selection range in the viewer, which highlights the span with a background color.

---

## Setup & Installation

### Prerequisites

- Python 3.11+
- [Semgrep](https://semgrep.dev/docs/getting-started/) (`pip install semgrep` or via your package manager)
- An [OpenRouter](https://openrouter.ai/) API key

### Install

```bash
git clone <repo>
cd ai-code-explain
pip install -e .
```

For Python syntax highlighting in the TUI editor, install the optional `tui` extra:

```bash
pip install -e ".[tui]"
```

JavaScript/TypeScript syntax highlighting is available out of the box — `tree-sitter-javascript` is a core dependency.

Without the `tui` extra, the Python editor falls back to plain text — all analysis features still work.

### Configure

```bash
cp .env.example .env
# Edit .env and set OPENROUTER_API_KEY
```

Available environment variables:

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | *(required)* | OpenRouter API key. One key covers all models. |
| `OPENROUTER_FAST_MODEL` | `poolside/laguna-m.1:free` | Model used in `--model fast` mode (low latency, coding-optimised) |
| `OPENROUTER_REASONING_MODEL` | `nvidia/nemotron-3-super-120b-a12b:free` | Model used in `--model reasoning` mode (higher capability, higher max. input/output tokens) |
| `OPENROUTER_MODEL` | *(unset)* | Single-model override — forces a specific model for both modes when set |
| `AI_CODE_EXPLAIN_BLOCK_COMPLEXITY` | `0` | Enable per-block LLM complexity analysis (`1/true/yes`). Selects up to 8 AST-defined blocks (largest non-overlapping) **Warning:** can add several extra LLM calls and noticeably increase runtime. |
| `LOCAL_CONTEXT_SANDBOX_DIR` | analyzed file's parent dir (CLI) / CWD (TUI) | Root for local filesystem context access |

**NOTE: prompts are logged by the provider of both default (free) models, so only use with non-sensitive code**

### Run

```bash
# Launch interactive TUI (fast model by default)
code-explain

# Analyze a file non-interactively (fast model)
code-explain --analyze path/to/script.py

# Toggle concise/detailed output in CLI
code-explain --analyze path/to/script.py --analysis-mode detailed

# Use the reasoning model for deeper analysis
code-explain --analyze path/to/script.py --model reasoning

# With explicit language hint
code-explain --analyze path/to/script.js --language javascript

# Write optimized output directly to a file
code-explain --analyze path/to/script.py --write-optimized path/to/script.optimized.py

# Or omit OUT_FILE to use default export path:
# .code_explain_exports/snippet_latest_optimized.<ext>
code-explain --analyze path/to/script.py --write-optimized
```

### Troubleshooting: `code-explain` not found
If the shell reports `code-explain` is not recognized after `pip install -e .`, the Python `Scripts` directory may not be on your `PATH`.

**Find the Scripts directory:**
```bash
py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
# e.g. C:\Users\<you>\AppData\Local\Python\pythoncore-3.14-64\Scripts
```

**Add it permanently (Windows PowerShell):**
```powershell
$s = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
[Environment]::SetEnvironmentVariable("PATH", "$([Environment]::GetEnvironmentVariable('PATH','User'));$s", "User")
```

**Add it permanently (macOS / Linux):**
```bash
echo 'export PATH="$(python3 -c \"import sysconfig; print(sysconfig.get_path(\'scripts\'))\""):$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Open a new terminal after adding to PATH, then verify with `code-explain --help`.

> **Also check:** if the install itself fails with `BackendUnavailable: Cannot import 'setuptools.backends.legacy'`, run `pip install setuptools` first — this backend requires setuptools to be present in the build environment.

---

## TUI Keyboard Reference

| Key | Action |
|---|---|
| `Ctrl+N` | New snippet (opens inline editor) |
| `Ctrl+S` | Submit snippet for analysis (in editor) |
| `Escape` | Cancel editor |
| `Ctrl+R` | Re-run analysis on current snippet |
| `Ctrl+E` | Export optimized code to `.ai_code_explain_exports/` |
| `Ctrl+T` | Toggle model: fast (Laguna M.1) ↔ reasoning (Nemotron 3 Super) |
| `Ctrl+Y` | Toggle analysis mode: concise ↔ detailed |
| `Ctrl+B` | Toggle block-level LLM complexity (may significantly increase runtime) |
| `1` | Switch to Explanation tab |
| `2` | Switch to Complexity tab |
| `3` | Switch to Semgrep tab |
| `4` | Switch to Diff tab |
| `Tab` / `Shift+Tab` | Move focus between panes |
| `Enter` | Select history item / hotspot |
| `Ctrl+Q` | Quit |

---

## Project Structure

```
src/ai_code_explain/
├── main.py                # Entry point, CLI argument handling
├── pipeline.py            # End-to-end orchestration (DAG-aware parallel flow)
├── database.py            # SQLModel/SQLite persistence layer
├── models.py              # Shared data-transfer types (ASTSpan, StaticMetadata, etc.)
├── llm_gateway.py         # OpenRouter API client + 3-prompt pipeline
├── semgrep_runner.py      # Semgrep subprocess integration
├── diff_generator.py      # difflib-based diff + Rich markup
├── local_context.py         # In-process local filesystem context fetcher (sandboxed)
├── analyzers/
│   ├── dispatcher.py      # Language detection + analyzer dispatch
│   ├── python_analyzer.py # Python AST walker (ast module)
│   └── js_analyzer.py     # JavaScript CST walker (tree-sitter)
└── ui/
    └── app.py             # Textual TUI application
```

---

## External Dependencies

| Package | Purpose |
|---|---|
| `textual` | Terminal UI framework |
| `rich` | Syntax highlighting, diff rendering, console output |
| `sqlmodel` | SQLite ORM (wraps SQLAlchemy + Pydantic) |
| `openai` | OpenAI-compatible client for OpenRouter |
| `tree-sitter` + `tree-sitter-javascript` | JavaScript AST parsing |
| `semgrep` | Static analysis pattern detection |
| `python-dotenv` | `.env` file loading |
| `httpx` | HTTP transport (used by openai client) |

### Dev / Test Dependencies

| Package | Purpose |
|---|---|
| `pytest` | Test runner |
| `pytest-cov` | Coverage measurement and enforcement |
| `pytest-mock` | `mocker` fixture (wraps `unittest.mock`) |

---

## Unit Tests

### Running the Test Suite

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests with coverage
pytest

# Run with verbose output
pytest -v

# Run a specific test module
pytest tests/test_python_analyzer.py -v
```

### Coverage Requirements

The suite is configured to **fail if coverage drops below 90%** (`--cov-fail-under=90`). UI code (`src/ai_code_explain/ui/*`) is excluded from coverage measurement because Textual widgets require a running event loop.

### Test Modules

| Module | What it covers |
|---|---|
| `tests/test_models.py` | All dataclass fields, defaults, mutable list independence |
| `tests/test_database.py` | create/save/load/update round-trips, JSON deserialisation edge cases |
| `tests/test_diff_generator.py` | Unified diff and Rich markup — identical inputs, additions, removals |
| `tests/test_python_analyzer.py` | AST walker: functions, loops, nesting, recursion, comprehensions, complexity baselines, syntax errors |
| `tests/test_js_analyzer.py` | tree-sitter walker: graceful degradation, functions, loops, sort, array transforms, complexity baselines |
| `tests/test_dispatcher.py` | Language detection heuristics, confidence levels, hint handling, dispatcher routing |
| `tests/test_semgrep_runner.py` | Subprocess integration, timeout handling, JSON parsing, temp-file cleanup, no `shell=True` |
| `tests/test_local_context.py` | Sandbox path traversal prevention, file read truncation, local import resolution, MAX_FILES limit |
| `tests/test_llm_gateway.py` | All 3 LLM call stages mocked, prompt content, JSON parsing, fallback behavior |
| `tests/test_pipeline.py` | End-to-end orchestration mocked, DAG dependency order, static/LLM JSON column separation |

### Test Design Principles

- **No real network calls** — all OpenRouter API calls are mocked via `unittest.mock.patch`.
- **No real Semgrep process** — subprocess is patched; timeout and `FileNotFoundError` paths are exercised explicitly.
- **Isolated databases** — every database test uses a `tmp_path`-scoped SQLite file.
- **tree-sitter optional** — JS analyzer tests that require tree-sitter are marked `pytest.mark.skipif` and skip cleanly when the library is not installed.

### E2E Test Fixtures

Two folders contain source files used for manual end-to-end testing of the full analysis pipeline:

| Folder | Purpose |
|---|---|
| `e2e_tests/` | **Clean inputs for LLM testing.** Contains the same files as `e2e_tests_commented/` but with all explanatory comments stripped out. Use these when running `code-explain --analyze` so the LLM receives no hints about what issues to find. |
| `e2e_tests_commented/` | **Annotated reference copies.** Every issue in each file is called out with inline comments (grade, OWASP class, antipattern name, complexity note, etc.). Use these to verify that the tool's output matches the known ground truth. |

Each numbered file targets a distinct failure mode:

| File | Scenario |
|---|---|
| `01_python_syntax_errors.py` | Multiple syntax errors — tests behavior when `ast.parse` raises `SyntaxError` |
| `02_python_security_vulns.py` | OWASP-class security vulnerabilities (SQL injection, command injection, path traversal, weak crypto, etc.) |
| `03_js_bad_patterns.js` | JavaScript antipatterns (synchronous XHR, `eval`, bitwise-vs-logical, callback hell, DOM XSS, prototype pollution) |
| `04_python_logic_smells.py` | Correct but problematic Python patterns (mutable defaults, God class, bare `except`, `O(n²)` dict comprehension) |
| `05_python_needs_optimization.py` | Correct code with clear performance improvements available (`O(n²)`/`O(n³)` algorithms, memory-inefficient file reads) |
| `06_python_bubble_sort.py` | Correct output but algorithmically weak (`O(n²)` bubble sort without short-circuit optimization) |
| `07_python_optimized_closest_pair.py` | Well-optimized divide-and-conquer geometry algorithm with richer complexity profile (`O(n log n)` with recursive merge constraints) |
| `sandbox/main_with_imports.py` | Multi-file scenario — exercises local-import resolution by pulling in `utils.py` and `db_helpers.py` as grounding context |

---

## Future Roadmap (Not Implemented)

| Feature | Potential Value |
|---|---|
| **Git integration** | Optimization lineage, patch tracking, repo-aware workflows, commit-level reasoning |
| **Repo-wide code graph analysis** | Call graph traversal, dependency impact analysis (possible: Serena, advanced tree-sitter) |
| **Execution sandbox** | Runtime benchmarking, empirical complexity validation, test execution |
| **Embedding/RAG retrieval** | Large repo semantic search, cross-file retrieval |
| **Multi-agent orchestration** | Planner/analyzer/reviewer agent pipeline |

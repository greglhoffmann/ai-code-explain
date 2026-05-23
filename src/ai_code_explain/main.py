"""Application entry point.

Bootstraps environment variables, initialises the database, and launches
the Textual TUI with the analysis pipeline wired in.

Usage:
    code-explain                     # launch TUI (interactive)
    code-explain --analyze FILE      # analyze a file non-interactively and print results
    code-explain --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .database import create_tables
from .pipeline import run_pipeline


_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",  # tree-sitter JS grammar handles TS syntax well enough
    ".jsx": "javascript",
    ".tsx": "javascript",
}


def _print_result_plain(result, analysis_mode: str = "concise") -> None:
    """Print an AnalysisResult to stdout in a readable text format."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax

    console = Console()

    conf = result.detection_confidence
    conf_part = f"  [dim](confidence: {conf})[/dim]" if conf and conf != "unknown" else ""
    mode_part = f"  [dim](mode: {analysis_mode})[/dim]"
    console.print(Panel(
        f"[bold cyan]Language:[/bold cyan] {result.language}{conf_part}{mode_part}",
        title="Analysis",
    ))

    # Code
    lexer = "python" if result.language == "python" else "javascript"
    console.print(Syntax(result.original_code, lexer, theme="monokai", line_numbers=True))

    # Explanation
    console.print(Panel(Markdown(result.explanation or "*No explanation*"), title="Explanation"))

    # Complexity
    if result.complexity:
        c = result.complexity
        reasoning = c.llm_reasoning or "N/A"
        complexity_md = (
            f"**Static:** Time `{c.static_time}`, Space `{c.static_space}`\n\n"
            f"**LLM:** Time `{c.llm_time}`, Space `{c.llm_space}` "
            f"(confidence: {c.llm_confidence})\n\n"
            f"**Reasoning:**\n\n{reasoning}"
        )
        console.print(Panel(Markdown(complexity_md), title="Complexity"))

    # Semgrep
    if result.semgrep_findings:
        findings_text = "\n".join(
            f"- [{f.get('severity','').upper()}] {f.get('rule','')} (L{f.get('line',0)}): {f.get('message','')}"
            for f in result.semgrep_findings
        )
        console.print(Panel(Markdown(findings_text), title="Semgrep Findings"))

    if result.optimization_warnings:
        warn_text = "\n".join(f"- {w}" for w in result.optimization_warnings)
        console.print(Panel(Markdown(warn_text), title="[yellow]Optimization Warnings[/yellow]"))

    # Sandbox Warnings
    if result.sandbox_warnings:
        blocked = "\n".join(f"- `{name}`" for name in result.sandbox_warnings)
        console.print(Panel(
            Markdown(f"The following imports resolved to files **outside the sandbox** and were skipped:\n\n{blocked}"),
            title="[yellow]Sandbox Warnings[/yellow]",
        ))

    # Diff
    if result.diff_text:
        console.print(Panel(result.diff_text, title="Diff (original → optimized)"))


def run() -> None:
    """CLI entry point — parse arguments and launch the appropriate mode."""
    load_dotenv()
    create_tables()

    parser = argparse.ArgumentParser(
        prog="code-explain",
        description="AI-assisted code analysis: explain, complexity, optimize.",
    )
    parser.add_argument(
        "--analyze",
        metavar="FILE",
        help="Analyze FILE non-interactively and print results to stdout.",
    )
    parser.add_argument(
        "--language",
        metavar="LANG",
        default="",
        help='Language hint: "python" or "javascript". Auto-detected if omitted.',
    )
    parser.add_argument(
        "--model",
        choices=["fast", "reasoning"],
        default="fast",
        help=(
            '"fast" (default): low-latency Laguna M.1; '
            '"reasoning": higher capability, higher max. input/output token Nemotron 3 Super.'
        ),
    )
    parser.add_argument(
        "--analysis-mode",
        choices=["concise", "detailed"],
        default="concise",
        help='Output verbosity: "concise" (default) or "detailed".',
    )
    parser.add_argument(
        "--write-optimized",
        metavar="OUT_FILE",
        nargs="?",
        const="__AUTO__",
        help=(
            "When used with --analyze, write optimized code to OUT_FILE. "
            "If OUT_FILE is omitted, writes to .code_explain_exports/"
            "snippet_latest_optimized.<ext>."
        ),
    )

    args = parser.parse_args()

    if args.analyze:
        # Non-interactive / CLI mode
        file_path = Path(args.analyze)
        if not file_path.is_file():
            print(f"Error: file not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        source_code = file_path.read_text(encoding="utf-8")
        language_hint = args.language or _EXTENSION_TO_LANGUAGE.get(file_path.suffix.lower(), "")  # try filepath before re detection if no explicit language provided

        def _cli_progress(msg: str) -> None:
            print(msg, file=sys.stderr)

        try:
            result = run_pipeline(
                source_code,
                language_hint,
                sandbox_dir=file_path.parent,
                model_mode=args.model,
                analysis_mode=args.analysis_mode,
                progress_callback=_cli_progress,
            )
        except RuntimeError as exc:
            print(f"LLM analysis failed: {exc}", file=sys.stderr)
            sys.exit(2)
        _print_result_plain(result, analysis_mode=args.analysis_mode)

        if args.write_optimized is not None:
            language = result.language or language_hint or "python"
            extension = ".py" if language == "python" else ".js"

            if args.write_optimized == "__AUTO__":
                export_dir = Path.cwd() / ".code_explain_exports"
                out_path = export_dir / f"snippet_latest_optimized{extension}"
            else:
                out_path = Path(args.write_optimized)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            optimized = result.optimized_code or source_code
            out_path.write_text(optimized, encoding="utf-8")
            print(f"Optimized code written to: {out_path}", file=sys.stderr)
    else:
        # Interactive TUI mode
        from .ui.app import CodeExplainApp

        app = CodeExplainApp(pipeline_callback=run_pipeline)
        app.run()


if __name__ == "__main__":
    run()

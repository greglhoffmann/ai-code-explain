"""Diff generation between original and optimized code.

Responsibilities (deterministic — no LLM):
- Generate a unified diff between two code strings
- Produce a Rich-renderable markup string with colour annotations
  suitable for display in the Diff tab
"""

from __future__ import annotations

import difflib


def generate_unified_diff(
    original_code: str,
    optimized_code: str,
    original_label: str = "original",
    optimized_label: str = "optimized",
) -> str:
    """Generate a unified diff between two code strings.

    Args:
        original_code: The source code before optimization.
        optimized_code: The source code after optimization.
        original_label: Label shown in the diff header for the original file.
        optimized_label: Label shown in the diff header for the optimized file.

    Returns:
        A multi-line unified diff string (empty string if inputs are identical).
    """
    original_lines = original_code.splitlines(keepends=True)
    optimized_lines = optimized_code.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            optimized_lines,
            fromfile=original_label,
            tofile=optimized_label,
            lineterm="",
        )
    )

    return "\n".join(diff_lines)


def generate_rich_diff_markup(
    original_code: str,
    optimized_code: str,
) -> str:
    """Generate a Rich markup string for side-by-side diff display.

    Lines added in the optimized version are prefixed with '[green]+[/green]',
    lines removed are prefixed with '[red]-[/red]', and context lines are
    prefixed with a space.

    Args:
        original_code: The source code before optimization.
        optimized_code: The source code after optimization.

    Returns:
        A Rich markup string suitable for use in a Rich Text/Panel widget.
    """
    original_lines = original_code.splitlines(keepends=True)
    optimized_lines = optimized_code.splitlines(keepends=True)

    output_lines: list[str] = []
    for line in difflib.unified_diff(
        original_lines,
        optimized_lines,
        fromfile="original",
        tofile="optimized",
        lineterm="",
    ):
        stripped = line.rstrip("\n")
        if stripped.startswith("+++") or stripped.startswith("---"):
            output_lines.append(f"[bold cyan]{stripped}[/bold cyan]")
        elif stripped.startswith("@@"):
            output_lines.append(f"[bold yellow]{stripped}[/bold yellow]")
        elif stripped.startswith("+"):
            output_lines.append(f"[green]{stripped}[/green]")
        elif stripped.startswith("-"):
            output_lines.append(f"[red]{stripped}[/red]")
        else:
            output_lines.append(stripped)

    return "\n".join(output_lines) if output_lines else "[dim]No differences[/dim]"


def generate_side_by_side_with_highlights(
    original_code: str,
    optimized_code: str,
) -> tuple[str, list[int], str, list[int]]:
    """Generate side-by-side code strings and the changed row indices for each side.

    Rows are padded with empty strings so both sides stay vertically aligned.
    Padding rows inside a changed block are also included in the changed-row sets
    so the highlight fills the gap uniformly.

    Returns:
        (left_code, left_changed_rows, right_code, right_changed_rows) where
        row indices are 0-based and correspond to lines in the returned code strings.
    """
    original_lines = original_code.splitlines()
    optimized_lines = optimized_code.splitlines()

    matcher = difflib.SequenceMatcher(a=original_lines, b=optimized_lines)

    left_code: list[str] = []
    right_code: list[str] = []
    left_changed: list[int] = []
    right_changed: list[int] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx in range(i1, i2):
                left_code.append(original_lines[idx])
                right_code.append(optimized_lines[j1 + idx - i1])
            continue

        left_chunk = original_lines[i1:i2]
        right_chunk = optimized_lines[j1:j2]
        chunk_len = max(len(left_chunk), len(right_chunk))

        for idx in range(chunk_len):
            left_changed.append(len(left_code))
            right_changed.append(len(right_code))

            left_code.append(left_chunk[idx] if idx < len(left_chunk) else "")
            right_code.append(right_chunk[idx] if idx < len(right_chunk) else "")

    if not left_code and not right_code:
        return "", [], "", []

    return "\n".join(left_code), left_changed, "\n".join(right_code), right_changed

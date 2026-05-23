"""Python AST-based static analysis.

Responsibilities (deterministic — no LLM):
- Parse Python source with the built-in `ast` module
- Detect loops, nested loops, recursion, comprehensions, imports,
  function definitions, collection allocations, sort operations, hash usage
- Extract stable AST span records (hotspots) for significant nodes
- Produce a baseline complexity estimate from detected patterns
"""

from __future__ import annotations

import ast
from typing import Optional

from ..models import ASTSpan, StaticMetadata


# ---------------------------------------------------------------------------
# Internal visitor
# ---------------------------------------------------------------------------


class _PythonASTVisitor(ast.NodeVisitor):
    """Walk a Python AST and collect analysis data."""

    def __init__(self, source_lines: list[str]) -> None:
        self._current_function: Optional[str] = None
        self._loop_depth: int = 0

        # counters / flags
        self.loops: int = 0
        self.nested_loops: bool = False
        self.sort_operations: int = 0
        self.recursive_calls: bool = False
        self.hashmap_usage: bool = False
        self.comprehensions: int = 0
        self.imports: list[str] = []
        self.function_names: list[str] = []

        # span list — order of discovery
        self.hotspots: list[ASTSpan] = []
        self._node_counter: int = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._node_counter += 1
        return f"{prefix}_{self._node_counter}"

    def _end_line(self, node: ast.AST) -> int:
        """Return the last line of a node (best effort)."""
        return getattr(node, "end_lineno", getattr(node, "lineno", 0))

    def _record_hotspot(
        self,
        node_id: str,
        node_type,  # NodeType literal
        name: str,
        node: ast.AST,
        label: str = "",
    ) -> None:
        self.hotspots.append(
            ASTSpan(
                node_id=node_id,
                node_type=node_type,
                name=name,
                start_line=getattr(node, "lineno", 0),
                end_line=self._end_line(node),
                label=label,
            )
        )

    # ------------------------------------------------------------------
    # Visitor methods
    # ------------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_names.append(node.name)
        span_id = self._next_id("func")
        self._record_hotspot(span_id, "function", node.name, node, f"def {node.name}()")
        outer_function = self._current_function
        self._current_function = node.name
        self.generic_visit(node)
        self._current_function = outer_function

    # Treat async functions identically for analysis purposes
    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def _visit_loop(self, node: ast.AST) -> None:
        self.loops += 1
        if self._loop_depth > 0:
            self.nested_loops = True
            span_id = self._next_id("nested_loop")
            self._record_hotspot(
                span_id,
                "nested_loop",
                "nested_loop",
                node,
                "Performance hotspot: nested loop",
            )
        else:
            span_id = self._next_id("loop")
            self._record_hotspot(span_id, "loop", "loop", node, "Loop body")
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Detect recursion: function calls matching the enclosing function name
        if self._current_function:
            called = None
            if isinstance(node.func, ast.Name):
                called = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called = node.func.attr
            if called and called == self._current_function:
                self.recursive_calls = True
                span_id = self._next_id("recursion")
                self._record_hotspot(
                    span_id,
                    "recursion",
                    self._current_function,
                    node,
                    f"Recursive call to {self._current_function}",
                )

        # Detect sort operations
        if (
            (isinstance(node.func, ast.Attribute) and node.func.attr in ("sort", "sorted"))
            or (isinstance(node.func, ast.Name) and node.func.id == "sorted")
        ):
            self.sort_operations += 1
            span_id = self._next_id("sort")
            self._record_hotspot(span_id, "sort", "sort", node, "Sort operation: O(n log n)")

        # Detect hashmap usage (dict() constructor or {} literal via dict calls)
        if isinstance(node.func, ast.Name) and node.func.id == "dict":
            self.hashmap_usage = True

        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        """Dict literals indicate hashmap usage."""
        self.hashmap_usage = True
        self.generic_visit(node)

    def _visit_comprehension(self, node: ast.AST, label: str) -> None:
        self.comprehensions += 1
        span_id = self._next_id("comprehension")
        self._record_hotspot(span_id, "comprehension", "comprehension", node, label)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, "List comprehension")

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, "Set comprehension")

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, "Dict comprehension")

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, "Generator expression")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            self.imports.append(f"{module}.{alias.name}" if module else alias.name)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _estimate_baseline(visitor: _PythonASTVisitor) -> tuple[str, str]:
    """Derive a deterministic baseline complexity from detected patterns.

    Returns (time_complexity, space_complexity) as Big-O strings.
    """
    if visitor.recursive_calls:
        time_complexity = "O(2^n)"  # Conservative worst-case for recursion
    elif visitor.nested_loops:
        if visitor.sort_operations:
            time_complexity = "O(n^2 log n)"
        else:
            time_complexity = "O(n^2)"
    elif visitor.sort_operations:
        time_complexity = "O(n log n)"
    elif visitor.loops:
        time_complexity = "O(n)"
    else:
        time_complexity = "O(1)"

    # Space complexity heuristic
    if visitor.hashmap_usage or visitor.comprehensions:
        space_complexity = "O(n)"
    elif visitor.recursive_calls:
        space_complexity = "O(n)"  # call stack
    else:
        space_complexity = "O(1)"

    return time_complexity, space_complexity


def analyze_python(source_code: str) -> StaticMetadata:
    """Parse and statically analyze a Python source snippet.

    Args:
        source_code: Raw Python source text.

    Returns:
        A populated StaticMetadata instance with all detected patterns and
        hotspot span records.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        # Return minimal metadata if the snippet has syntax errors
        return StaticMetadata(
            language="python",
            imports=[],
            function_names=[],
            hotspots=[],
            baseline_time_complexity="unknown",
            baseline_space_complexity="unknown",
        )

    source_lines = source_code.splitlines()
    visitor = _PythonASTVisitor(source_lines)
    visitor.visit(tree)

    time_complexity, space_complexity = _estimate_baseline(visitor)

    return StaticMetadata(
        language="python",
        loops=visitor.loops,
        nested_loops=visitor.nested_loops,
        sort_operations=visitor.sort_operations,
        recursive_calls=visitor.recursive_calls,
        hashmap_usage=visitor.hashmap_usage,
        comprehensions=visitor.comprehensions,
        imports=visitor.imports,
        function_names=visitor.function_names,
        hotspots=visitor.hotspots,
        baseline_time_complexity=time_complexity,
        baseline_space_complexity=space_complexity,
    )

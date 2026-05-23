"""JavaScript static analysis using tree-sitter.

Responsibilities (deterministic — no LLM):
- Parse JavaScript source with tree-sitter
- Detect loops (for/while/do-while), nested loops, async patterns,
  recursion, array transformations (map/filter/reduce), sort calls
- Extract stable AST span records (hotspots) for significant nodes
- Produce a baseline complexity estimate from detected patterns
"""

from __future__ import annotations

from typing import Optional

from ..models import ASTSpan, StaticMetadata

# tree-sitter imports — guarded so the module can be imported even if
# tree-sitter is not installed (the pipeline will degrade gracefully).
try:
    import tree_sitter_javascript as tsjava
    from tree_sitter import Language, Node, Parser

    _JS_LANGUAGE = Language(tsjava.language())
    _PARSER = Parser(_JS_LANGUAGE)
    _TREE_SITTER_AVAILABLE = True
except Exception:  # pragma: no cover
    _TREE_SITTER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Node-type constants used across the JavaScript grammar
# ---------------------------------------------------------------------------

_LOOP_TYPES = {"for_statement", "while_statement", "do_statement", "for_in_statement"}

_ASYNC_TYPES = {"await_expression"}

_SORT_METHOD = "sort"
_ARRAY_TRANSFORM_METHODS = {"map", "filter", "reduce", "forEach", "flatMap", "find", "findIndex"}


# ---------------------------------------------------------------------------
# Internal walker (recursive descent over tree-sitter CST)
# ---------------------------------------------------------------------------


class _JSWalker:
    """Walk a tree-sitter concrete syntax tree for a JavaScript snippet."""

    def __init__(self) -> None:
        self._loop_depth: int = 0
        self._current_function: Optional[str] = None
        self._node_counter: int = 0

        self.loops: int = 0
        self.nested_loops: bool = False
        self.sort_operations: int = 0
        self.recursive_calls: bool = False
        self.hashmap_usage: bool = False
        self.async_patterns: int = 0
        self.array_transforms: int = 0
        self.imports: list[str] = []
        self.function_names: list[str] = []
        self.hotspots: list[ASTSpan] = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._node_counter += 1
        return f"{prefix}_{self._node_counter}"

    def _node_name(self, node: "Node") -> str:
        """Extract the identifier name for a function/method node."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8") if child.text else ""
        return ""

    def _record_hotspot(
        self,
        node_id: str,
        node_type,
        name: str,
        node: "Node",
        label: str = "",
    ) -> None:
        start_line = node.start_point[0] + 1  # tree-sitter is 0-indexed
        end_line = node.end_point[0] + 1
        self.hotspots.append(
            ASTSpan(
                node_id=node_id,
                node_type=node_type,
                name=name,
                start_line=start_line,
                end_line=end_line,
                label=label,
            )
        )

    # ------------------------------------------------------------------
    # Recursive walk
    # ------------------------------------------------------------------

    def walk(self, node: "Node") -> None:
        """Recursively process a CST node and its children."""
        node_type = node.type

        # --- Function declarations / expressions ---
        if node_type in (
            "function_declaration",
            "function",
            "arrow_function",
            "method_definition",
        ):
            name = self._node_name(node)
            self.function_names.append(name)
            span_id = self._next_id("func")
            self._record_hotspot(span_id, "function", name, node, f"function {name}()")
            outer = self._current_function
            self._current_function = name
            for child in node.children:
                self.walk(child)
            self._current_function = outer
            return

        # --- Loop detection ---
        if node_type in _LOOP_TYPES:
            self.loops += 1
            if self._loop_depth > 0:
                self.nested_loops = True
                span_id = self._next_id("nested_loop")
                self._record_hotspot(
                    span_id, "nested_loop", "nested_loop", node, "Performance hotspot: nested loop"
                )
            else:
                span_id = self._next_id("loop")
                self._record_hotspot(span_id, "loop", "loop", node, "Loop body")
            self._loop_depth += 1
            for child in node.children:
                self.walk(child)
            self._loop_depth -= 1
            return

        # --- Async patterns ---
        if node_type in _ASYNC_TYPES:
            self.async_patterns += 1
            span_id = self._next_id("async")
            self._record_hotspot(span_id, "async_pattern", "await", node, "Async await expression")

        # --- Call expressions: sort / array transforms / recursion ---
        if node_type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                if func_node.type == "member_expression":
                    prop = func_node.child_by_field_name("property")
                    if prop is not None:
                        method_name = prop.text.decode("utf-8") if prop.text else ""
                        if method_name == _SORT_METHOD:
                            self.sort_operations += 1
                            span_id = self._next_id("sort")
                            self._record_hotspot(
                                span_id, "sort", "sort", node, "Sort: O(n log n)"
                            )
                        elif method_name in _ARRAY_TRANSFORM_METHODS:
                            self.array_transforms += 1
                            span_id = self._next_id("array_transform")
                            self._record_hotspot(
                                span_id,
                                "array_transform",
                                method_name,
                                node,
                                f"Array transform: .{method_name}()",
                            )
                elif func_node.type == "identifier":
                    # Detect recursion: identifier matches enclosing function name
                    called_name = func_node.text.decode("utf-8") if func_node.text else ""
                    if called_name and called_name == self._current_function:
                        self.recursive_calls = True
                        span_id = self._next_id("recursion")
                        self._record_hotspot(
                            span_id,
                            "recursion",
                            called_name,
                            node,
                            f"Recursive call to {called_name}",
                        )

        # --- Object literals indicate hashmap usage ---
        if node_type == "object":
            self.hashmap_usage = True

        # --- Import statements ---
        if node_type in ("import_statement", "import_declaration"):
            source_node = node.child_by_field_name("source")
            if source_node is not None:
                raw = source_node.text.decode("utf-8") if source_node.text else ""
                self.imports.append(raw.strip("'\""))

        # Recurse into children
        for child in node.children:
            self.walk(child)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _estimate_baseline(walker: _JSWalker) -> tuple[str, str]:
    """Derive a deterministic baseline complexity from detected patterns."""
    if walker.recursive_calls:
        time_complexity = "O(2^n)"
    elif walker.nested_loops:
        if walker.sort_operations:
            time_complexity = "O(n^2 log n)"
        else:
            time_complexity = "O(n^2)"
    elif walker.sort_operations:
        time_complexity = "O(n log n)"
    elif walker.loops or walker.array_transforms:
        time_complexity = "O(n)"
    else:
        time_complexity = "O(1)"

    if walker.hashmap_usage or walker.array_transforms:
        space_complexity = "O(n)"
    elif walker.recursive_calls:
        space_complexity = "O(n)"
    else:
        space_complexity = "O(1)"

    return time_complexity, space_complexity


def analyze_javascript(source_code: str) -> StaticMetadata:
    """Parse and statically analyze a JavaScript source snippet.

    Args:
        source_code: Raw JavaScript source text.

    Returns:
        A populated StaticMetadata instance with all detected patterns and
        hotspot span records.

    Note:
        If tree-sitter is unavailable the function returns minimal metadata
        with an "unknown" complexity so the pipeline can degrade gracefully.
    """
    if not _TREE_SITTER_AVAILABLE:
        return StaticMetadata(
            language="javascript",
            baseline_time_complexity="unknown",
            baseline_space_complexity="unknown",
        )

    encoded = source_code.encode("utf-8")
    tree = _PARSER.parse(encoded)
    walker = _JSWalker()
    walker.walk(tree.root_node)

    time_complexity, space_complexity = _estimate_baseline(walker)

    return StaticMetadata(
        language="javascript",
        loops=walker.loops,
        nested_loops=walker.nested_loops,
        sort_operations=walker.sort_operations,
        recursive_calls=walker.recursive_calls,
        hashmap_usage=walker.hashmap_usage,
        async_patterns=walker.async_patterns,
        comprehensions=walker.array_transforms,  # re-use comprehensions field for JS transforms
        imports=walker.imports,
        function_names=walker.function_names,
        hotspots=walker.hotspots,
        baseline_time_complexity=time_complexity,
        baseline_space_complexity=space_complexity,
    )

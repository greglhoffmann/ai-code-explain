"""Shared data-transfer types for the analysis pipeline.

Keeping these separate from database models avoids coupling
persistence concerns to the analysis/UI layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# AST Span / Hotspot
# ---------------------------------------------------------------------------

NodeType = Literal[
    "function",
    "loop",
    "nested_loop",
    "recursion",
    "comprehension",
    "sort",
    "conditional",
    "async_pattern",
    "array_transform",
]


@dataclass
class ASTSpan:
    """Stable source-location record for a significant AST node."""

    node_id: str
    node_type: NodeType
    name: str
    start_line: int
    end_line: int
    label: str = ""  # human-readable description used in UI/prompts


# ---------------------------------------------------------------------------
# Static Analysis Metadata
# ---------------------------------------------------------------------------


@dataclass
class StaticMetadata:
    """Summary produced by the deterministic AST walker."""

    language: str
    loops: int = 0
    nested_loops: bool = False
    sort_operations: int = 0
    recursive_calls: bool = False
    hashmap_usage: bool = False
    comprehensions: int = 0
    async_patterns: int = 0
    imports: list[str] = field(default_factory=list)
    function_names: list[str] = field(default_factory=list)
    hotspots: list[ASTSpan] = field(default_factory=list)
    baseline_time_complexity: str = "O(n)"
    baseline_space_complexity: str = "O(n)"


# ---------------------------------------------------------------------------
# Complexity Estimates
# ---------------------------------------------------------------------------


@dataclass
class ComplexityEstimate:
    """Paired static + LLM complexity estimates stored in the database."""

    static_time: str
    static_space: str
    static_confidence: str = "high"
    llm_time: Optional[str] = None
    llm_space: Optional[str] = None
    llm_confidence: Optional[str] = None
    llm_reasoning: Optional[str] = None


@dataclass
class BlockComplexity:
    """Per-block complexity estimate for a hotspot-aligned code span."""

    block_id: str
    node_type: NodeType
    label: str
    start_line: int
    end_line: int
    static_time: str
    static_space: str
    static_confidence: str = "medium"
    llm_time: Optional[str] = None
    llm_space: Optional[str] = None
    llm_confidence: Optional[str] = None
    llm_reasoning: Optional[str] = None


# ---------------------------------------------------------------------------
# Optimization Improvement
# ---------------------------------------------------------------------------

ImpactLevel = Literal["high", "medium", "low"]
RiskLevel = Literal["high", "medium", "low"]
ImprovementCategory = Literal[
    "algorithmic", "readability", "idiomatic", "api", "data_structure"
]


@dataclass
class Improvement:
    """A single suggested code improvement."""

    category: ImprovementCategory
    impact: ImpactLevel
    behavior_change_risk: RiskLevel
    description: str
    tradeoffs: str
    optimized_code: str


# ---------------------------------------------------------------------------
# Full Analysis Result
# ---------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    """All outputs produced by a single end-to-end pipeline run."""

    language: str
    original_code: str
    static_metadata: StaticMetadata
    semgrep_findings: list[dict]
    explanation: str = ""
    referenced_hotspots: list[dict] = field(default_factory=list)
    complexity: Optional[ComplexityEstimate] = None
    block_complexities: list[BlockComplexity] = field(default_factory=list)
    improvements: list[Improvement] = field(default_factory=list)
    optimized_code: str = ""
    diff_text: str = ""
    local_context: Optional[str] = None  # optional local filesystem context string
    detection_confidence: str = ""     # "explicit" | "high" | "medium" | "low"
    sandbox_warnings: list[str] = field(default_factory=list)  # imports blocked by sandbox
    optimization_warnings: list[str] = field(default_factory=list)  # optimized output validation warnings

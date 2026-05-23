"""Language detection and dispatcher for the static analysis layer."""

from __future__ import annotations

import re

from .python_analyzer import analyze_python
from .js_analyzer import analyze_javascript
from ..models import StaticMetadata


# ---------------------------------------------------------------------------
# Pre-compiled signal patterns
# ---------------------------------------------------------------------------
# Word boundaries (\b) prevent substring matches inside identifiers — e.g.
# "null" must not fire on "nullable", "None" must not fire on "NoneType".
# Patterns compiled once at import time for efficiency;

_PYTHON_SIGNAL_RE = re.compile(
    r"\bdef\b"           # function/method definition
    r"|\belif\b"         # Python-only conditional keyword
    r"|\bself\."         # instance attribute access
    r"|\bNone\b"         # Python null literal (JS uses null)
    r"|\bTrue\b"         # Python boolean (JS uses true, lowercase)
    r"|\bFalse\b"        # Python boolean (JS uses false, lowercase)
    r"|\bprint\("        # built-in print call
    r"|\bpass\b"         # no-op statement
    r"|\braise\b"        # exception raising
    r"|\bexcept\b"       # exception catching
    r"|\b__init__\b"     # dunder constructor
    r"|:[ \t]*\n"        # colon-terminated block header (if/for/def/class)
)

_JS_SIGNAL_RE = re.compile(
    r"\bfunction\b"      # function keyword (declaration or expression)
    r"|\bconst\b"        # block-scoped immutable binding
    r"|\blet\b"          # block-scoped mutable binding
    r"|\bvar\b"          # function-scoped binding
    r"|=>"               # arrow function
    r"|console\.log"     # JS logging idiom
    r"|==="              # strict equality
    r"|!=="              # strict inequality
    r"|\brequire\("      # CommonJS module import
    r"|module\.exports"  # CommonJS export
    r"|\btypeof\b"       # type introspection operator
    r"|\bnull\b"         # JS null literal (Python uses None)
    r"|\bundefined\b"    # JS undefined value
)


def detect_language(source_code: str, hint: str = "") -> tuple[str, str]:
    """Detect whether a snippet is Python or JavaScript and return a confidence label.

    Hint values are accepted first; auto-detection uses discriminating
    keyword signals that are unique (or heavily weighted) per language.
    Ambiguous tokens shared by both languages (e.g. ``import``) are
    intentionally excluded to avoid false positives.

    When signals are tied or absent, defaults to "python".

    Args:
        source_code: Raw source text.
        hint: Optional caller-supplied language hint ("python", "py",
              "javascript", or "js"). Overrides auto-detection.

    Returns:
        A ``(language, confidence)`` tuple where *language* is ``"python"`` or
        ``"javascript"`` and *confidence* is one of ``"explicit"``,
        ``"high"``, ``"medium"``, or ``"low"``.
    """
    normalized_hint = hint.strip().lower()
    if normalized_hint in ("python", "py"):
        return "python", "explicit"
    if normalized_hint in ("javascript", "js"):
        return "javascript", "explicit"

    python_signals = len(_PYTHON_SIGNAL_RE.findall(source_code))
    js_signals = len(_JS_SIGNAL_RE.findall(source_code))

    language = "javascript" if js_signals > python_signals else "python"

    delta = abs(python_signals - js_signals)
    if delta == 0:
        confidence = "low"
    elif delta <= 2:
        confidence = "medium"
    else:
        confidence = "high"

    return language, confidence


def analyze(source_code: str, language_hint: str = "") -> tuple[StaticMetadata, str, str]:
    """Detect language and dispatch to the correct static analyzer.

    Args:
        source_code: Raw source text.
        language_hint: Optional caller-supplied language override.

    Returns:
        A ``(metadata, language, confidence)`` tuple:
          - The populated ``StaticMetadata``
          - The detected language string
          - The detection confidence label (``"explicit" | "high" | "medium" | "low"``)
    """
    language, confidence = detect_language(source_code, language_hint)
    if language == "javascript":
        return analyze_javascript(source_code), language, confidence
    return analyze_python(source_code), language, confidence

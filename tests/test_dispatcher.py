"""Tests for language detection and analyzer dispatch (analyzers/dispatcher.py)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from ai_code_explain.analyzers.dispatcher import (
    _JS_SIGNAL_RE,
    _PYTHON_SIGNAL_RE,
    analyze,
    detect_language,
)
from ai_code_explain.models import StaticMetadata


# ---------------------------------------------------------------------------
# detect_language — hint overrides
# ---------------------------------------------------------------------------


class TestHintOverrides:
    @pytest.mark.parametrize("hint", ["python", "py", "Python", "PY", "PYTHON"])
    def test_python_hint_returns_python(self, hint: str):
        language, confidence = detect_language("const x = 1;", hint)
        assert language == "python"
        assert confidence == "explicit"

    @pytest.mark.parametrize("hint", ["javascript", "js", "JavaScript", "JS"])
    def test_js_hint_returns_javascript(self, hint: str):
        language, confidence = detect_language("def foo(): pass", hint)
        assert language == "javascript"
        assert confidence == "explicit"

    def test_unknown_hint_falls_through_to_autodetect(self):
        language, _confidence = detect_language("def foo():\n    return 1\n", hint="ruby")
        assert language == "python"


# ---------------------------------------------------------------------------
# detect_language — auto-detection
# ---------------------------------------------------------------------------


class TestAutoDetection:
    def test_python_code_detected(self):
        code = (
            "def process(items):\n"
            "    for item in items:\n"
            "        pass\n"
            "    return None\n"
        )
        language, _ = detect_language(code)
        assert language == "python"

    def test_javascript_code_detected(self):
        code = (
            "function process(items) {\n"
            "  const result = [];\n"
            "  for (let i = 0; i < items.length; i++) {\n"
            "    result.push(items[i] * 2);\n"
            "  }\n"
            "  return null;\n"
            "}\n"
        )
        language, _ = detect_language(code)
        assert language == "javascript"

    def test_python_recursive_function(self):
        code = (
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fibonacci(n-1) + fibonacci(n-2)\n"
        )
        language, _ = detect_language(code)
        assert language == "python"

    def test_js_arrow_function(self):
        code = "const double = (x) => x * 2;\nconst triple = (x) => x * 3;\n"
        language, _ = detect_language(code)
        assert language == "javascript"

    def test_js_const_let_var_signals(self):
        code = "const a = 1;\nlet b = 2;\nvar c = 3;\n"
        language, _ = detect_language(code)
        assert language == "javascript"

    def test_empty_code_defaults_to_python(self):
        language, confidence = detect_language("")
        assert language == "python"
        assert confidence == "low"

    def test_tied_signals_defaults_to_python(self):
        language, confidence = detect_language("x = 1 + 2\n")
        assert language == "python"
        assert confidence == "low"

    def test_python_class_with_self(self):
        code = (
            "class MyClass:\n"
            "    def __init__(self):\n"
            "        self.value = 0\n"
        )
        language, _ = detect_language(code)
        assert language == "python"

    def test_js_null_undefined_signals(self):
        code = "let x = null;\nlet y = undefined;\nconst z = typeof x;\n"
        language, _ = detect_language(code)
        assert language == "javascript"

    def test_js_strict_equality_operator(self):
        code = "if (a === b) { return true; } if (c !== d) { return false; }\n"
        language, _ = detect_language(code)
        assert language == "javascript"

    def test_python_elif_signal(self):
        code = "if x > 0:\n    pass\nelif x < 0:\n    pass\nelse:\n    pass\n"
        language, _ = detect_language(code)
        assert language == "python"

    def test_python_raise_except_signals(self):
        code = "def f():\n    try:\n        pass\n    except ValueError:\n        raise RuntimeError('err')\n"
        language, _ = detect_language(code)
        assert language == "python"

    def test_python_import_not_counted_as_discriminator(self):
        # 'import' is shared syntax — should NOT make JS code look like Python
        code = "import React from 'react';\nconst App = () => null;\n"
        language, _ = detect_language(code)
        assert language == "javascript"


# ---------------------------------------------------------------------------
# Signal counting helpers
# ---------------------------------------------------------------------------


class TestSignalPatterns:
    """Verify the compiled regex patterns match expected tokens."""

    def test_python_pattern_matches_def(self):
        assert len(_PYTHON_SIGNAL_RE.findall("def foo(): pass")) >= 1

    def test_python_pattern_matches_self(self):
        assert len(_PYTHON_SIGNAL_RE.findall("self.value = 1")) >= 1

    def test_python_pattern_matches_none(self):
        assert len(_PYTHON_SIGNAL_RE.findall("return None")) >= 1

    def test_python_pattern_no_match_on_empty(self):
        assert len(_PYTHON_SIGNAL_RE.findall("")) == 0

    def test_js_pattern_matches_const(self):
        assert len(_JS_SIGNAL_RE.findall("const x = 1;")) >= 1

    def test_js_pattern_matches_arrow(self):
        assert len(_JS_SIGNAL_RE.findall("const f = () => 1;")) >= 1

    def test_js_pattern_matches_null(self):
        assert len(_JS_SIGNAL_RE.findall("let x = null;")) >= 1

    def test_js_pattern_matches_require(self):
        assert len(_JS_SIGNAL_RE.findall("const fs = require('fs');")) >= 1

    def test_js_pattern_no_match_on_empty(self):
        assert len(_JS_SIGNAL_RE.findall("")) == 0

    def test_null_word_boundary_no_false_positive(self):
        # 'nullable' must not trigger the null signal
        assert len(_JS_SIGNAL_RE.findall("x = nullable")) == 0

    def test_none_word_boundary_no_false_positive(self):
        # 'NoneType' must not trigger the None signal
        assert len(_PYTHON_SIGNAL_RE.findall("x: NoneType = None")) == 1  # only the bare None


# ---------------------------------------------------------------------------
# confidence — returned as second element of detect_language tuple
# ---------------------------------------------------------------------------


class TestDetectionConfidence:
    def test_explicit_for_python_hint(self):
        _, confidence = detect_language("anything", "python")
        assert confidence == "explicit"

    def test_explicit_for_js_hint(self):
        _, confidence = detect_language("anything", "js")
        assert confidence == "explicit"

    def test_low_confidence_for_empty_code(self):
        _, confidence = detect_language("")
        assert confidence == "low"

    def test_high_confidence_for_clear_python(self):
        code = (
            "def process(items):\n"
            "    for item in items:\n"
            "        if item is None:\n"
            "            raise ValueError('bad')\n"
            "    elif True:\n"
            "        pass\n"
        )
        _, confidence = detect_language(code)
        assert confidence == "high"

    def test_high_confidence_for_clear_js(self):
        code = (
            "const process = (items) => {\n"
            "  let result = null;\n"
            "  const mapped = items.map(x => x * 2);\n"
            "  if (result === null || result === undefined) { return null; }\n"
            "};\n"
        )
        _, confidence = detect_language(code)
        assert confidence == "high"


# ---------------------------------------------------------------------------
# analyze() dispatcher
# ---------------------------------------------------------------------------


class TestAnalyzeDispatcher:
    def test_dispatches_to_python(self, simple_python_code: str):
        with patch("ai_code_explain.analyzers.dispatcher.analyze_python") as mock_py:
            mock_py.return_value = StaticMetadata(language="python")
            metadata, language, confidence = analyze(simple_python_code, "python")
        mock_py.assert_called_once_with(simple_python_code)
        assert language == "python"
        assert confidence == "explicit"
        assert metadata.language == "python"

    def test_dispatches_to_js(self):
        code = "const x = 1;"
        with patch("ai_code_explain.analyzers.dispatcher.analyze_javascript") as mock_js:
            mock_js.return_value = StaticMetadata(language="javascript")
            metadata, language, confidence = analyze(code, "javascript")
        mock_js.assert_called_once_with(code)
        assert language == "javascript"
        assert confidence == "explicit"

    def test_auto_dispatch_python_code(self, simple_python_code: str):
        metadata, language, confidence = analyze(simple_python_code)
        assert language == "python"
        assert metadata.language == "python"

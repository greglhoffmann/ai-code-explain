"""Tests for the local filesystem context module (local_context.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_code_explain.local_context import (
    _import_to_path,
    _is_stdlib_or_thirdparty,
    _resolve_local_imports,
    _safe_read,
    _safe_resolve,
    fetch_local_import_context,
)
from ai_code_explain.models import StaticMetadata


# ---------------------------------------------------------------------------
# _is_stdlib_or_thirdparty
# ---------------------------------------------------------------------------


class TestIsStdlibOrThirdparty:
    @pytest.mark.parametrize("name", [
        "os", "sys", "re", "json", "math", "pathlib", "typing",
        "collections", "itertools", "functools", "subprocess",
        "numpy", "pandas", "requests", "flask", "django",
        "sqlmodel", "openai", "textual", "rich",
    ])
    def test_known_packages_return_true(self, name: str):
        assert _is_stdlib_or_thirdparty(name) is True

    @pytest.mark.parametrize("name", [
        "mymodule", "utils", "helpers", "local_lib", "project_utils",
    ])
    def test_local_names_return_false(self, name: str):
        assert _is_stdlib_or_thirdparty(name) is False

    def test_submodule_root_is_checked(self):
        # "os.path" — root "os" is stdlib
        assert _is_stdlib_or_thirdparty("os.path") is True

    def test_relative_import_root_checked(self):
        # "./utils" — root "utils" is not stdlib
        assert _is_stdlib_or_thirdparty("./utils") is False


# ---------------------------------------------------------------------------
# _safe_resolve
# ---------------------------------------------------------------------------


class TestSafeResolve:
    def test_returns_path_for_existing_file_in_sandbox(self, tmp_path: Path):
        target = tmp_path / "utils.py"
        target.write_text("# utils")
        result, escaped = _safe_resolve(target, tmp_path)
        assert result == target.resolve()
        assert escaped is False

    def test_returns_none_for_nonexistent_file(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.py"
        result, escaped = _safe_resolve(missing, tmp_path)
        assert result is None

    def test_returns_none_for_file_outside_sandbox(self, tmp_path: Path):
        # Create a file outside the sandbox
        outside = tmp_path.parent / "outside.py"
        outside.write_text("# outside")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        try:
            result, escaped = _safe_resolve(outside, sandbox)
            assert result is None
            assert escaped is True
        finally:
            outside.unlink(missing_ok=True)

    def test_path_traversal_rejected(self, tmp_path: Path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "secret.py"
        outside.write_text("SECRET")
        traversal = sandbox / ".." / "secret.py"
        result, escaped = _safe_resolve(traversal, sandbox)
        assert result is None
        assert escaped is True


# ---------------------------------------------------------------------------
# _safe_read
# ---------------------------------------------------------------------------


class TestSafeRead:
    def test_reads_file_content(self, tmp_path: Path):
        target = tmp_path / "module.py"
        target.write_text("def helper(): pass\n")
        result = _safe_read(target, tmp_path)
        assert result == "def helper(): pass\n"

    def test_returns_none_for_outside_sandbox(self, tmp_path: Path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("secret")
        result = _safe_read(outside, sandbox)
        assert result is None

    def test_truncates_to_max_bytes(self, tmp_path: Path):
        from ai_code_explain.local_context import _MAX_FILE_BYTES
        target = tmp_path / "big.py"
        target.write_text("x" * (_MAX_FILE_BYTES + 1000))
        result = _safe_read(target, tmp_path)
        assert result is not None
        assert len(result) <= _MAX_FILE_BYTES

    def test_returns_none_for_nonexistent_file(self, tmp_path: Path):
        missing = tmp_path / "missing.py"
        result = _safe_read(missing, tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# _import_to_path
# ---------------------------------------------------------------------------


class TestImportToPath:
    def test_finds_python_module(self, tmp_path: Path):
        (tmp_path / "utils.py").write_text("# utils")
        result, escaped = _import_to_path("utils", tmp_path, "python")
        assert result is not None
        assert result.name == "utils.py"

    def test_finds_submodule_python(self, tmp_path: Path):
        sub = tmp_path / "helpers"
        sub.mkdir()
        (sub / "__init__.py").write_text("")
        # "helpers" as a dotted import
        result, escaped = _import_to_path("helpers.__init__", tmp_path, "python")
        assert result is not None

    def test_finds_js_module(self, tmp_path: Path):
        (tmp_path / "helpers.js").write_text("// helpers")
        result, escaped = _import_to_path("helpers", tmp_path, "javascript")
        assert result is not None
        assert result.name == "helpers.js"

    def test_returns_none_when_not_found(self, tmp_path: Path):
        result, escaped = _import_to_path("nonexistent", tmp_path, "python")
        assert result is None

    def test_dot_notation_resolves(self, tmp_path: Path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "module.py").write_text("")
        result, escaped = _import_to_path("pkg.module", tmp_path, "python")
        assert result is not None


# ---------------------------------------------------------------------------
# _resolve_local_imports
# ---------------------------------------------------------------------------


class TestResolveLocalImports:
    def test_skips_stdlib_imports(self, tmp_path: Path):
        results, blocked = _resolve_local_imports(["os", "sys", "json"], tmp_path, "python")
        assert results == []

    def test_finds_local_module(self, tmp_path: Path):
        (tmp_path / "mymodule.py").write_text("# local")
        results, blocked = _resolve_local_imports(["os", "mymodule"], tmp_path, "python")
        assert len(results) == 1
        assert results[0].name == "mymodule.py"

    def test_skips_missing_local_modules(self, tmp_path: Path):
        results, blocked = _resolve_local_imports(["nonexistent_module"], tmp_path, "python")
        assert results == []

    def test_empty_imports(self, tmp_path: Path):
        results, blocked = _resolve_local_imports([], tmp_path, "python")
        assert results == []


# ---------------------------------------------------------------------------
# fetch_local_import_context
# ---------------------------------------------------------------------------


class TestFetchLocalImportContext:
    def test_returns_none_when_no_local_imports(self, tmp_path: Path):
        meta = StaticMetadata(language="python", imports=["os", "sys"])
        context, warnings = fetch_local_import_context(meta, sandbox_dir=tmp_path)
        assert context is None

    def test_returns_context_for_local_imports(self, tmp_path: Path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        meta = StaticMetadata(language="python", imports=["utils"])
        context, warnings = fetch_local_import_context(meta, sandbox_dir=tmp_path)
        assert context is not None
        assert "utils.py" in context
        assert "helper" in context

    def test_returns_none_when_sandbox_does_not_exist(self, tmp_path: Path):
        nonexistent_sandbox = tmp_path / "does_not_exist"
        meta = StaticMetadata(language="python", imports=["utils"])
        context, warnings = fetch_local_import_context(meta, sandbox_dir=nonexistent_sandbox)
        assert context is None

    def test_returns_none_when_imports_empty(self, tmp_path: Path):
        meta = StaticMetadata(language="python", imports=[])
        context, warnings = fetch_local_import_context(meta, sandbox_dir=tmp_path)
        assert context is None

    def test_limits_to_max_files(self, tmp_path: Path):
        from ai_code_explain.local_context import _MAX_FILES
        for i in range(_MAX_FILES + 2):
            (tmp_path / f"local{i}.py").write_text(f"# module {i}")
        imports = [f"local{i}" for i in range(_MAX_FILES + 2)]
        meta = StaticMetadata(language="python", imports=imports)
        context, warnings = fetch_local_import_context(meta, sandbox_dir=tmp_path)
        # Result should include at most _MAX_FILES file sections
        if context:
            assert context.count("```") <= _MAX_FILES * 2  # each file block uses 2 backtick fences

    def test_context_format_has_filename(self, tmp_path: Path):
        (tmp_path / "helpers.py").write_text("def greet(): pass\n")
        meta = StaticMetadata(language="python", imports=["helpers"])
        context, warnings = fetch_local_import_context(meta, sandbox_dir=tmp_path)
        assert context is not None
        assert "helpers.py" in context

    def test_uses_LOCAL_CONTEXT_SANDBOX_DIR_env(self, tmp_path: Path, monkeypatch):
        (tmp_path / "utils.py").write_text("def foo(): pass\n")
        monkeypatch.setenv("LOCAL_CONTEXT_SANDBOX_DIR", str(tmp_path))
        meta = StaticMetadata(language="python", imports=["utils"])
        # Call without explicit sandbox_dir — should pick up env var
        context, warnings = fetch_local_import_context(meta, sandbox_dir=None)
        assert context is not None

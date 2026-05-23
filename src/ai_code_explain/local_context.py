"""Local filesystem context integration.

*NOTE: Does not call any external MCP server process/client API. Local context 
fetching is implemented as a pure local function that can be invoked directly 
by the main pipeline code without any network calls or subprocesses.*

Responsibilities:
- Scan import statements in a snippet for references to LOCAL modules
  (i.e. relative imports or bare module names that resolve to files
  within the active project sandbox directory)
- Read the identified local files via direct filesystem access
  (sandboxed — read-only, restricted to the project directory)
- Return a concise context string for inclusion in LLM prompts

SECURITY CONSTRAINTS (see README §Local Filesystem Context Security Constraints):
  - Access is sandboxed to the active project directory only.
  - Only read operations are permitted — no shell execution, no writes,
    no directory traversal outside the sandbox root.
  - Paths are canonicalized and checked against the sandbox root before
    any file is opened to prevent path-traversal attacks.
  - External commands are never invoked by this module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .models import StaticMetadata

# Maximum bytes to read from a single local file to keep prompt size manageable
_MAX_FILE_BYTES = 4_096

# Maximum number of local files to fetch per snippet
_MAX_FILES = 3


def fetch_local_import_context(
    metadata: StaticMetadata,
    sandbox_dir: Optional[Path] = None,
) -> tuple[Optional[str], list[str]]:
    """Look up local module files referenced by the snippet's imports.

    This is triggered ONLY when imports reference local files. Standard
    library and third-party package imports are ignored.

    Args:
        metadata: StaticMetadata produced by the AST analyzer.
        sandbox_dir: Root directory to constrain file access. Defaults to
                     the current working directory. All resolved paths must
                     remain within this directory.

    Returns:
        A 2-tuple of:
          - A formatted multi-file context string for LLM prompt grounding,
            or None if no local imports are found or the sandbox is unavailable.
          - A list of import names that resolved to files existing on disk but
            outside the sandbox (empty when no escapes were attempted).
    """
    if sandbox_dir is None:
        sandbox_dir_env = os.environ.get("LOCAL_CONTEXT_SANDBOX_DIR")
        sandbox_dir = Path(sandbox_dir_env).resolve() if sandbox_dir_env else Path.cwd().resolve()
    else:
        sandbox_dir = sandbox_dir.resolve()

    if not sandbox_dir.is_dir():
        return None, []

    local_files, sandbox_blocked = _resolve_local_imports(metadata.imports, sandbox_dir, metadata.language)

    if not local_files:
        return None, sandbox_blocked

    context_parts: list[str] = []
    for file_path in local_files[:_MAX_FILES]:
        content = _safe_read(file_path, sandbox_dir)
        if content:
            context_parts.append(f"### {file_path.name}\n```\n{content}\n```")

    return ("\n\n".join(context_parts) if context_parts else None), sandbox_blocked


def _resolve_local_imports(
    imports: list[str],
    sandbox_dir: Path,
    language: str,
) -> tuple[list[Path], list[str]]:
    """Map import names to existing local file paths inside the sandbox.

    Args:
        imports: List of import names/paths from the AST analyzer.
        sandbox_dir: The allowed root directory.
        language: "python" or "javascript".

    Returns:
        A 2-tuple of:
          - List of resolved Path objects that exist within the sandbox.
          - List of import names whose paths exist on disk but outside the
            sandbox (potential path-traversal attempts or misconfigured sandbox).
    """
    candidates: list[Path] = []
    sandbox_blocked: list[str] = []

    for import_name in imports:
        # Skip obvious standard library / third-party packages
        if _is_stdlib_or_thirdparty(import_name):
            continue

        resolved, escaped = _import_to_path(import_name, sandbox_dir, language)
        if resolved:
            candidates.append(resolved)
        elif escaped:
            sandbox_blocked.append(import_name)

    return candidates, sandbox_blocked


def _import_to_path(
    import_name: str,
    sandbox_dir: Path,
    language: str,
) -> tuple[Optional[Path], bool]:
    """Attempt to resolve an import name to a file path inside sandbox_dir.

    Args:
        import_name: The raw import string (e.g. "utils", "./helpers", "utils.normalize").
        sandbox_dir: Allowed root directory.
        language: "python" or "javascript".

    Returns:
        A 2-tuple of (resolved_path, escaped_sandbox).
        resolved_path is the safe Path if found within sandbox, else None.
        escaped_sandbox is True when any candidate existed on disk but outside
        the sandbox boundary — indicating a potential traversal or misconfigured
        sandbox that the caller should surface as a warning.
    """
    # Normalise dots to path separators for Python sub-module imports
    relative_part = import_name.replace(".", os.sep).lstrip(os.sep)

    extensions = [".py"] if language == "python" else [".js", ".mjs", ".ts"]

    escaped_any = False
    for ext in extensions:
        candidate = (sandbox_dir / relative_part).with_suffix(ext)
        safe, escaped = _safe_resolve(candidate, sandbox_dir)
        if safe:
            return safe, False
        escaped_any = escaped_any or escaped

        # Also try the bare name without extension transformation
        candidate2 = sandbox_dir / (import_name + ext)
        safe2, escaped2 = _safe_resolve(candidate2, sandbox_dir)
        if safe2:
            return safe2, False
        escaped_any = escaped_any or escaped2

    return None, escaped_any


def _safe_resolve(path: Path, sandbox_dir: Path) -> tuple[Optional[Path], bool]:
    """Resolve path and verify it is within sandbox_dir.

    Returns a 2-tuple of (safe_path, escaped_sandbox).
    safe_path is the resolved Path when it exists inside the sandbox, else None.
    escaped_sandbox is True specifically when the path resolves to an existing
    file on disk that is OUTSIDE the sandbox — the caller should treat this as a
    warning-worthy event, not just a silent miss.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return None, False

    if not resolved.is_file():
        return None, False

    try:
        # Ensure resolved path is inside sandbox — prevent path traversal
        resolved.relative_to(sandbox_dir)
        return resolved, False
    except ValueError:
        # File exists on disk but outside sandbox boundary
        return None, True


def _safe_read(file_path: Path, sandbox_dir: Path) -> Optional[str]:
    """Read a file safely, enforcing sandbox and size limits.

    Args:
        file_path: Absolute path to the file (already validated as within sandbox).
        sandbox_dir: Sandbox root — re-validated as a defence-in-depth measure.

    Returns:
        File content string truncated to _MAX_FILE_BYTES, or None on error.
    """
    # Defence-in-depth: re-validate the path before opening
    safe, _ = _safe_resolve(file_path, sandbox_dir)
    if safe is None:
        return None

    try:
        with safe.open("r", encoding="utf-8", errors="replace") as file_handle:
            return file_handle.read(_MAX_FILE_BYTES)
    except OSError:
        return None


_KNOWN_STDLIB_PREFIXES = {
    "os", "sys", "re", "json", "math", "time", "datetime", "pathlib",
    "typing", "collections", "itertools", "functools", "io", "abc",
    "ast", "inspect", "copy", "enum", "dataclasses", "logging",
    "unittest", "subprocess", "shutil", "tempfile", "hashlib",
    "urllib", "http", "email", "xml", "csv", "sqlite3", "threading",
    "multiprocessing", "asyncio", "concurrent", "socket", "ssl",
    # Common third-party packages unlikely to be local
    "numpy", "pandas", "requests", "flask", "django", "fastapi",
    "sqlmodel", "openai", "textual", "rich", "semgrep",
    "tree_sitter", "tree_sitter_javascript",
}


def _is_stdlib_or_thirdparty(import_name: str) -> bool:
    """Heuristically decide if an import is a stdlib or third-party package.

    Args:
        import_name: Import string to check.

    Returns:
        True if the import is likely stdlib or third-party (skip local lookup).
    """
    root_name = import_name.split(".")[0].split("/")[0].strip("./")
    return root_name in _KNOWN_STDLIB_PREFIXES

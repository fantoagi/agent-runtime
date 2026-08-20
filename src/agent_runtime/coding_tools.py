from __future__ import annotations

import fnmatch
import hashlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .domain import (
    ToolCapability,
    ToolDefinition,
    ToolExecutionError,
    ToolOutcomeUnknown,
    ToolValidationError,
)
from .tools import ToolContext, ToolRegistry, ToolResult, confined_path

DEFAULT_IGNORED_DIRECTORIES = frozenset(
    {
        ".agent-runtime",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".runtime-test-data",
        ".tox",
        ".venv",
        "__pycache__",
        "dist",
        "node_modules",
    }
)
DEFAULT_IGNORED_FILES = frozenset({".coverage", "coverage.json"})
_MAX_LIST_RESULTS = 2_000
_MAX_LIST_FILES_SCANNED = 20_000
_MAX_SEARCH_RESULTS = 1_000
_MAX_SEARCH_FILES = 10_000
_MAX_SEARCH_FILE_BYTES = 1_000_000
_MAX_SEARCH_LINE_CHARS = 500
_MAX_READ_LINES = 2_000
_MAX_READ_CHARS = 3_500
_MAX_PATCH_EDITS = 20
_MAX_PATCH_INPUT_CHARS = 1_000_000


def register_coding_tools(registry: ToolRegistry) -> tuple[ToolDefinition, ...]:
    """Register bounded workspace inspection and exact-edit tools."""

    definitions = (
        ToolDefinition(
            name="list_files",
            description=(
                "List files inside the workspace. Results are relative paths, sorted, bounded, "
                "and skip generated/runtime paths by default. If a broad result is truncated, "
                "narrow path or pattern and continue instead of stopping."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "recursive": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            capabilities=(ToolCapability.FILE_READ,),
        ),
        ToolDefinition(
            name="search_text",
            description=(
                "Search UTF-8 text files inside the workspace and return bounded path, line "
                "number, and matching line results. Prefer this when a target symbol or filename "
                "is already known instead of listing the entire workspace."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            capabilities=(ToolCapability.FILE_READ,),
        ),
        ToolDefinition(
            name="read_file_lines",
            description=(
                "Read a bounded line range from a UTF-8 workspace file with stable line numbers. "
                "Use next_start_line while has_more is true instead of reading a large file at once."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "max_lines": {"type": "integer"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            capabilities=(ToolCapability.FILE_READ,),
        ),
        ToolDefinition(
            name="replace_text",
            description=(
                "Replace an exact text fragment in an existing UTF-8 workspace file. "
                "The replacement count must match expected_replacements; this is not a "
                "unified-diff parser."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "expected_replacements": {"type": "integer"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            requires_approval=True,
            side_effecting=True,
            capabilities=(ToolCapability.FILE_WRITE,),
        ),
        ToolDefinition(
            name="apply_patch",
            description=(
                "Apply a bounded batch of exact text replacements across existing UTF-8 files. "
                "All matches are validated before writing; this is not a unified-diff parser."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "items": {"type": "object"},
                    }
                },
                "required": ["edits"],
                "additionalProperties": False,
            },
            requires_approval=True,
            side_effecting=True,
            capabilities=(ToolCapability.FILE_WRITE,),
        ),
    )
    registry.register(definitions[0], _list_files)
    registry.register(definitions[1], _search_text)
    registry.register(definitions[2], _read_file_lines)
    registry.register(definitions[3], _replace_text)
    registry.register(definitions[4], _apply_patch)
    return definitions


def _list_files(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    context.raise_if_cancelled()
    requested = str(arguments.get("path", "."))
    pattern = str(arguments.get("pattern", "*"))
    recursive = bool(arguments.get("recursive", True))
    max_results = _bounded_positive_int(
        arguments.get("max_results", 200), "max_results", _MAX_LIST_RESULTS
    )
    base = confined_path(context.workspace_path, requested)
    if not base.exists():
        raise ToolExecutionError(f"Path does not exist: {requested}")
    if not base.is_dir():
        raise ToolExecutionError(f"Path is not a directory: {requested}")

    results: list[str] = []
    scanned = 0
    scan_limited = False
    for path in _iter_workspace_files(base, recursive=recursive):
        context.raise_if_cancelled()
        scanned += 1
        if scanned > _MAX_LIST_FILES_SCANNED:
            scan_limited = True
            break
        relative = path.relative_to(context.workspace_path.resolve()).as_posix()
        candidate = path.name if "/" not in pattern and "\\" not in pattern else relative
        if fnmatch.fnmatchcase(candidate, pattern):
            results.append(relative)
    results.sort()
    truncated = scan_limited or len(results) > max_results
    results = results[:max_results]
    data = {
        "path": requested,
        "pattern": pattern,
        "recursive": recursive,
        "files": results,
        "count": len(results),
        "truncated": truncated,
    }
    return ToolResult(content=_render_file_list(data), data=data)


def _search_text(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    context.raise_if_cancelled()
    query = str(arguments["query"])
    if not query:
        raise ToolValidationError("search_text query must not be empty.")
    requested = str(arguments.get("path", "."))
    pattern = str(arguments.get("glob", "*"))
    case_sensitive = bool(arguments.get("case_sensitive", False))
    max_results = _bounded_positive_int(
        arguments.get("max_results", 100), "max_results", _MAX_SEARCH_RESULTS
    )
    base = confined_path(context.workspace_path, requested)
    if not base.exists():
        raise ToolExecutionError(
            _missing_path_message(context.workspace_path, requested, subject="Path")
        )

    matches: list[dict[str, Any]] = []
    files_scanned = 0
    files_skipped = 0
    truncated = False
    paths: Iterator[Path] = (
        iter((base,)) if base.is_file() else _iter_workspace_files(base, recursive=True)
    )
    needle = query if case_sensitive else query.casefold()
    for path in paths:
        context.raise_if_cancelled()
        if files_scanned >= _MAX_SEARCH_FILES:
            truncated = True
            break
        relative = _workspace_relative(context.workspace_path, path)
        candidate = path.name if "/" not in pattern and "\\" not in pattern else relative
        if not fnmatch.fnmatchcase(candidate, pattern):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            files_skipped += 1
            continue
        if size > _MAX_SEARCH_FILE_BYTES:
            files_skipped += 1
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            files_skipped += 1
            continue
        files_scanned += 1
        if b"\x00" in raw:
            files_skipped += 1
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            files_skipped += 1
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            haystack = line if case_sensitive else line.casefold()
            if needle not in haystack:
                continue
            matches.append(
                {
                    "path": relative,
                    "line": line_number,
                    "content": _truncate_line(line, _MAX_SEARCH_LINE_CHARS),
                }
            )
            if len(matches) >= max_results:
                truncated = True
                break
        if truncated:
            break

    data = {
        "query": query,
        "path": requested,
        "glob": pattern,
        "case_sensitive": case_sensitive,
        "matches": matches,
        "count": len(matches),
        "files_scanned": files_scanned,
        "files_skipped": files_skipped,
        "truncated": truncated,
    }
    return ToolResult(content=_render_search_results(data), data=data)


def _read_file_lines(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    context.raise_if_cancelled()
    requested = str(arguments["path"])
    start_line = _bounded_positive_int(arguments.get("start_line", 1), "start_line", 10_000_000)
    max_lines = _bounded_positive_int(arguments.get("max_lines", 200), "max_lines", _MAX_READ_LINES)
    max_chars = _bounded_positive_int(
        arguments.get("max_chars", 3_000), "max_chars", _MAX_READ_CHARS
    )
    if max_chars < 256:
        raise ToolValidationError("max_chars must be at least 256.")
    path = confined_path(context.workspace_path, requested)
    if not path.exists():
        raise ToolExecutionError(
            _missing_path_message(context.workspace_path, requested, subject="File")
        )
    if not path.is_file():
        raise ToolExecutionError(f"Path is not a file: {requested}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ToolExecutionError(f"File is not valid UTF-8: {requested}") from error
    lines = text.splitlines()
    selected = lines[start_line - 1 : start_line - 1 + max_lines]
    rendered: list[str] = []
    used = 0
    char_limited = False
    for number, line in enumerate(selected, start=start_line):
        formatted = f"{number:>6} | {line}"
        separator = 1 if rendered else 0
        remaining = max_chars - used - separator
        if remaining <= 0:
            char_limited = True
            break
        if len(formatted) > remaining:
            rendered.append(formatted[:remaining])
            used += separator + remaining
            char_limited = True
            break
        rendered.append(formatted)
        used += separator + len(formatted)
    end_line = start_line + len(rendered) - 1 if rendered else start_line - 1
    has_more = char_limited or end_line < len(lines)
    relative = _workspace_relative(context.workspace_path, path)
    body = "\n".join(rendered)
    next_start_line = end_line + 1 if has_more and rendered else None
    header = f"{relative} lines {start_line}-{end_line} of {len(lines)}" + (
        f" (more available; next_start_line={next_start_line})" if has_more else ""
    )
    content = header + ("\n" + body if body else "")
    if len(content) > max_chars:
        content = content[:max_chars]
        has_more = True
    return ToolResult(
        content=content,
        data={
            "path": relative,
            "start_line": start_line,
            "end_line": end_line,
            "next_start_line": next_start_line,
            "total_lines": len(lines),
            "has_more": has_more,
            "truncated": has_more,
            "sha256": _sha256_text(text),
        },
    )


def _replace_text(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    context.raise_if_cancelled()
    requested = str(arguments["path"])
    old_text = str(arguments["old_text"])
    new_text = str(arguments["new_text"])
    if not old_text:
        raise ToolValidationError("replace_text old_text must not be empty.")
    expected = _bounded_positive_int(
        arguments.get("expected_replacements", 1),
        "expected_replacements",
        1_000,
    )
    path = confined_path(context.workspace_path, requested)
    if not path.is_file():
        raise ToolExecutionError(f"File does not exist: {requested}")
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ToolExecutionError(f"File is not valid UTF-8: {requested}") from error
    actual = original.count(old_text)
    if actual != expected:
        raise ToolExecutionError(
            "Replacement count mismatch for "
            f"{requested}: expected {expected}, found {actual}. No file was changed."
        )
    updated = original.replace(old_text, new_text)
    before_hash = _sha256_text(original)
    after_hash = _sha256_text(updated)
    _atomic_write_text(path, updated, context)
    relative = _workspace_relative(context.workspace_path, path)
    data = {
        "path": relative,
        "status": "replaced",
        "replacements": actual,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "characters_before": len(original),
        "characters_after": len(updated),
    }
    return ToolResult(
        content=(
            f"Replaced {actual} occurrence(s) in {relative}. "
            f"sha256 {before_hash[:12]} -> {after_hash[:12]}"
        ),
        data=data,
    )


def _apply_patch(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    context.raise_if_cancelled()
    raw_edits = arguments["edits"]
    if not isinstance(raw_edits, list):
        raise ToolValidationError("apply_patch edits must be an array.")
    if not raw_edits or len(raw_edits) > _MAX_PATCH_EDITS:
        raise ToolValidationError(
            f"apply_patch edits must contain between 1 and {_MAX_PATCH_EDITS} items."
        )
    total_input = 0
    originals: dict[Path, str] = {}
    updated: dict[Path, str] = {}
    path_order: list[Path] = []
    replacement_counts: dict[Path, int] = {}
    allowed_keys = {"path", "old_text", "new_text", "expected_replacements"}
    for index, item in enumerate(raw_edits, start=1):
        context.raise_if_cancelled()
        if not isinstance(item, dict):
            raise ToolValidationError(f"apply_patch edit {index} must be an object.")
        unknown = set(item) - allowed_keys
        if unknown:
            raise ToolValidationError(
                f"apply_patch edit {index} has unsupported fields: {', '.join(sorted(unknown))}."
            )
        missing = {"path", "old_text", "new_text"} - set(item)
        if missing:
            raise ToolValidationError(
                f"apply_patch edit {index} is missing: {', '.join(sorted(missing))}."
            )
        requested = item["path"]
        old_text = item["old_text"]
        new_text = item["new_text"]
        if (
            not isinstance(requested, str)
            or not isinstance(old_text, str)
            or not isinstance(new_text, str)
        ):
            raise ToolValidationError(
                f"apply_patch edit {index} path, old_text, and new_text must be strings."
            )
        if not old_text:
            raise ToolValidationError(f"apply_patch edit {index} old_text must not be empty.")
        total_input += len(requested) + len(old_text) + len(new_text)
        if total_input > _MAX_PATCH_INPUT_CHARS:
            raise ToolValidationError("apply_patch input is too large.")
        expected = _bounded_positive_int(
            item.get("expected_replacements", 1),
            f"edit {index} expected_replacements",
            1_000,
        )
        path = confined_path(context.workspace_path, requested)
        if not path.is_file():
            raise ToolExecutionError(f"File does not exist: {requested}")
        if path not in originals:
            try:
                original = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise ToolExecutionError(f"File is not valid UTF-8: {requested}") from error
            originals[path] = original
            updated[path] = original
            replacement_counts[path] = 0
            path_order.append(path)
        current = updated[path]
        actual = current.count(old_text)
        if actual != expected:
            raise ToolExecutionError(
                f"Patch edit {index} count mismatch for {requested}: expected {expected}, "
                f"found {actual}. No file was changed."
            )
        updated[path] = current.replace(old_text, new_text)
        replacement_counts[path] += actual

    committed: list[Path] = []
    try:
        for path in path_order:
            context.raise_if_cancelled()
            _atomic_write_text(path, updated[path], context)
            committed.append(path)
    except BaseException as error:
        rollback_errors: list[str] = []
        for path in reversed(committed):
            try:
                _atomic_write_text(path, originals[path], None)
            except BaseException as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        if rollback_errors:
            raise ToolOutcomeUnknown(
                "apply_patch failed and rollback was incomplete: " + "; ".join(rollback_errors)
            ) from error
        if isinstance(error, ToolExecutionError):
            raise
        raise ToolExecutionError("apply_patch failed; committed files were rolled back.") from error

    files: list[dict[str, Any]] = []
    for path in path_order:
        relative = _workspace_relative(context.workspace_path, path)
        files.append(
            {
                "path": relative,
                "replacements": replacement_counts[path],
                "before_sha256": _sha256_text(originals[path]),
                "after_sha256": _sha256_text(updated[path]),
                "characters_before": len(originals[path]),
                "characters_after": len(updated[path]),
            }
        )
    summary = "\n".join(
        f"{item['path']}: {item['replacements']} replacement(s), "
        f"{item['before_sha256'][:12]} -> {item['after_sha256'][:12]}"
        for item in files
    )
    return ToolResult(
        content=f"Applied {len(raw_edits)} edit(s) across {len(files)} file(s).\n{summary}",
        data={
            "status": "patched",
            "edit_count": len(raw_edits),
            "file_count": len(files),
            "files": files,
        },
    )


def _iter_workspace_files(base: Path, *, recursive: bool) -> Iterator[Path]:
    root = base.resolve()
    if not recursive:
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.name.casefold() not in DEFAULT_IGNORED_FILES
            ):
                yield path
        return
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            (
                name
                for name in directories
                if name.casefold() not in DEFAULT_IGNORED_DIRECTORIES
                and not (Path(current) / name).is_symlink()
            ),
            key=str.casefold,
        )
        for name in sorted(files, key=str.casefold):
            path = Path(current) / name
            if path.is_symlink() or name.casefold() in DEFAULT_IGNORED_FILES:
                continue
            yield path


def _missing_path_message(workspace: Path, requested: str, *, subject: str) -> str:
    normalized = requested.replace("\\", "/").strip("/")
    normalized_folded = normalized.casefold()
    requested_name = Path(normalized).name.casefold()
    ranked: list[tuple[int, str]] = []
    scanned = 0
    try:
        for candidate in _iter_workspace_files(workspace.resolve(), recursive=True):
            scanned += 1
            if scanned > _MAX_SEARCH_FILES:
                break
            relative = _workspace_relative(workspace, candidate)
            relative_folded = relative.casefold()
            relative_without_suffix = Path(relative).with_suffix("").as_posix().casefold()
            score: int | None = None
            if relative_folded == normalized_folded:
                score = 0
            elif relative_without_suffix == normalized_folded:
                score = 1
            elif candidate.stem.casefold() == requested_name:
                score = 2
            elif candidate.name.casefold() == requested_name:
                score = 3
            elif relative_folded.endswith(normalized_folded):
                score = 4
            if score is not None:
                ranked.append((score, relative))
    except OSError:
        ranked = []
    suggestions = [
        relative
        for _, relative in sorted(
            set(ranked), key=lambda item: (item[0], item[1].casefold())
        )[:5]
    ]
    message = f"{subject} does not exist: {requested}. Use a workspace-relative path."
    if suggestions:
        message += " Possible matches: " + ", ".join(suggestions) + "."
    return message


def _workspace_relative(workspace: Path, path: Path) -> str:
    root = workspace.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ToolExecutionError("Requested path escapes the configured workspace.")
    return resolved.relative_to(root).as_posix()


def _bounded_positive_int(value: Any, name: str, upper_bound: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolValidationError(f"{name} must be an integer.")
    if value < 1 or value > upper_bound:
        raise ToolValidationError(f"{name} must be between 1 and {upper_bound}.")
    return value


def _atomic_write_text(path: Path, content: str, context: ToolContext | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if context is not None:
            context.raise_if_cancelled()
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate_line(value: str, limit: int) -> str:
    normalized = value.replace("\r", " ").replace("\n", " ")
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _render_file_list(data: dict[str, Any]) -> str:
    files = data["files"]
    header = f"Found {data['count']} file(s)"
    if data["truncated"]:
        header += " (truncated)"
    return header + ("\n" + "\n".join(files) if files else "")


def _render_search_results(data: dict[str, Any]) -> str:
    matches = data["matches"]
    header = f"Found {data['count']} match(es) in {data['files_scanned']} file(s)"
    if data["truncated"]:
        header += " (truncated)"
    lines = [f"{item['path']}:{item['line']}:{item['content']}" for item in matches]
    return header + ("\n" + "\n".join(lines) if lines else "")

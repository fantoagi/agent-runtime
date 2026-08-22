from __future__ import annotations

from pathlib import Path
from typing import Any

from .domain import ToolCapability, ToolDefinition, ToolExecutionError, ToolValidationError
from .sandbox import SandboxExecutor, SandboxRequest
from .tools import ToolContext, ToolRegistry, ToolResult, confined_path

_MAX_DIFF_CHARS = 200_000
_MAX_CONTEXT_LINES = 50


def register_git_tools(
    registry: ToolRegistry,
    sandbox: SandboxExecutor,
) -> tuple[ToolDefinition, ...]:
    """Register bounded read-only Git inspection tools when Git is allowlisted."""

    executable = _git_executable(sandbox)
    if executable is None:
        return ()
    definitions = (
        ToolDefinition(
            name="git_status",
            description=(
                "Show the current workspace Git branch and short working-tree status. "
                "This is read-only, includes untracked files by default, and does not run a shell."
            ),
            input_schema={
                "type": "object",
                "properties": {"include_untracked": {"type": "boolean"}},
                "additionalProperties": False,
            },
            capabilities=(ToolCapability.PROCESS_EXEC, ToolCapability.FILE_READ),
            sandbox_only=True,
        ),
        ToolDefinition(
            name="git_diff",
            description=(
                "Show a bounded, read-only Git diff for tracked workspace files. "
                "Use staged=true for the index; untracked file content is not included."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "staged": {"type": "boolean"},
                    "context_lines": {"type": "integer"},
                    "max_chars": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            capabilities=(ToolCapability.PROCESS_EXEC, ToolCapability.FILE_READ),
            sandbox_only=True,
        ),
    )

    async def git_status(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        context.raise_if_cancelled()
        include_untracked = bool(arguments.get("include_untracked", True))
        argv = (
            executable,
            "-c",
            "core.fsmonitor=false",
            "status",
            "--short",
            "--branch",
            f"--untracked-files={'all' if include_untracked else 'no'}",
        )
        result = await sandbox.execute(
            SandboxRequest(argv=argv), cancellation_token=context.cancellation_token
        )
        _require_git_success("git_status", result.exit_code, result.stderr)
        raw = result.stdout.rstrip()
        changed_lines = [line for line in raw.splitlines() if not line.startswith("##")]
        entries = _parse_status_entries(changed_lines)
        output = raw or "Working tree clean."
        return ToolResult(
            content=output,
            data={
                "status": "changes" if changed_lines else "clean",
                "workspace_dirty": bool(entries),
                "entries": entries,
                "include_untracked": include_untracked,
                "output": output,
                "exit_code": result.exit_code,
            },
        )

    async def git_diff(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        context.raise_if_cancelled()
        requested = str(arguments.get("path", "."))
        target = confined_path(context.workspace_path, requested)
        relative = target.relative_to(context.workspace_path.resolve()).as_posix() or "."
        staged = bool(arguments.get("staged", False))
        context_lines = _bounded_int(
            arguments.get("context_lines", 3), "context_lines", 0, _MAX_CONTEXT_LINES
        )
        max_chars = _bounded_int(
            arguments.get("max_chars", 50_000), "max_chars", 1_000, _MAX_DIFF_CHARS
        )
        argv = [
            executable,
            "-c",
            "core.fsmonitor=false",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            f"--unified={context_lines}",
        ]
        if staged:
            argv.append("--cached")
        argv.extend(("--", relative))
        result = await sandbox.execute(
            SandboxRequest(argv=tuple(argv)), cancellation_token=context.cancellation_token
        )
        _require_git_success("git_diff", result.exit_code, result.stderr)
        raw = result.stdout.rstrip()
        truncated = len(raw) > max_chars
        output = raw[:max_chars]
        if truncated:
            output += "\n... [git diff truncated]"
        if not output:
            output = "No tracked differences."
        return ToolResult(
            content=output,
            data={
                "path": relative,
                "staged": staged,
                "context_lines": context_lines,
                "truncated": truncated,
                "characters": len(raw),
                "has_changes": bool(raw),
                "exit_code": result.exit_code,
            },
        )

    registry.register(definitions[0], git_status, sandboxed=True)
    registry.register(definitions[1], git_diff, sandboxed=True)
    return definitions


def _parse_status_entries(lines: list[str]) -> list[dict[str, str | None]]:
    entries: list[dict[str, str | None]] = []
    for line in lines:
        if len(line) < 3:
            continue
        index_status = line[0]
        worktree_status = line[1]
        path = line[3:]
        original_path: str | None = None
        if index_status == "?" and worktree_status == "?":
            kind = "untracked"
        elif "R" in {index_status, worktree_status}:
            kind = "renamed"
            if " -> " in path:
                original_path, path = path.split(" -> ", 1)
        elif "D" in {index_status, worktree_status}:
            kind = "deleted"
        else:
            kind = "modified"
        entries.append(
            {
                "kind": kind,
                "path": path,
                "original_path": original_path,
                "index_status": index_status,
                "worktree_status": worktree_status,
            }
        )
    return entries


def _git_executable(sandbox: SandboxExecutor) -> str | None:
    allowed = sandbox.snapshot().get("allowed_executables", [])
    for value in allowed:
        path = Path(str(value))
        if path.name.casefold() in {"git", "git.exe"}:
            return str(path)
    return None


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolValidationError(f"{name} must be an integer.")
    if value < minimum or value > maximum:
        raise ToolValidationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _require_git_success(name: str, exit_code: int, stderr: str) -> None:
    if exit_code == 0:
        return
    detail = stderr.strip() or f"Git exited with code {exit_code}."
    raise ToolExecutionError(f"{name} failed: {detail}")

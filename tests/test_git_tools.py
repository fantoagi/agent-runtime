from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_runtime.domain import ToolExecutionError, ToolValidationError
from agent_runtime.git_tools import register_git_tools
from agent_runtime.sandbox import SandboxRequest, SandboxResult
from agent_runtime.tools import ToolContext, ToolRegistry


class FakeGitSandbox:
    def __init__(self, results: list[SandboxResult], *, include_git: bool = True) -> None:
        self.results = list(results)
        self.requests: list[SandboxRequest] = []
        self.include_git = include_git

    async def execute(self, request: SandboxRequest, *, cancellation_token=None) -> SandboxResult:
        del cancellation_token
        self.requests.append(request)
        return self.results.pop(0)

    def snapshot(self) -> dict[str, Any]:
        allowed = [r"C:\Program Files\Git\cmd\git.exe"] if self.include_git else ["python.exe"]
        return {"kind": "fake", "allowed_executables": allowed}

    def close(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def tool_context(workspace: Path) -> ToolContext:
    return ToolContext(run_id="run-git", step_id=1, workspace_path=workspace, metadata={})


def result(stdout: str = "", stderr: str = "", exit_code: int = 0) -> SandboxResult:
    return SandboxResult(
        argv=(), cwd=".", exit_code=exit_code, stdout=stdout, stderr=stderr, duration_ms=1.0
    )


@pytest.mark.asyncio
async def test_git_status_uses_read_only_bounded_command(workspace: Path) -> None:
    sandbox = FakeGitSandbox([result("## main\n M example.py\n")])
    tools = ToolRegistry()
    definitions = register_git_tools(tools, sandbox)
    assert [item.name for item in definitions] == ["git_status", "git_diff"]

    status = await tools.invoke("git_status", {"include_untracked": False}, tool_context(workspace))

    assert "M example.py" in status.content
    assert status.data is not None and status.data["status"] == "changes"
    argv = sandbox.requests[0].argv
    assert argv[0].endswith("git.exe")
    assert "status" in argv
    assert "--untracked-files=no" in argv
    assert tools.authorization("git_status").requires_approval is False
    await tools.aclose()


@pytest.mark.asyncio
async def test_git_status_treats_branch_header_only_as_clean(workspace: Path) -> None:
    sandbox = FakeGitSandbox([result("## main\n")])
    tools = ToolRegistry()
    register_git_tools(tools, sandbox)
    status = await tools.invoke("git_status", {}, tool_context(workspace))
    assert status.data is not None and status.data["status"] == "clean"
    await tools.aclose()


@pytest.mark.asyncio
async def test_git_diff_is_scoped_and_truncated(workspace: Path) -> None:
    (workspace / "src").mkdir()
    sandbox = FakeGitSandbox([result("x" * 2_000)])
    tools = ToolRegistry()
    register_git_tools(tools, sandbox)

    diff = await tools.invoke(
        "git_diff",
        {"path": "src", "staged": True, "context_lines": 5, "max_chars": 1_000},
        tool_context(workspace),
    )

    assert diff.data is not None
    assert diff.data["truncated"] is True
    assert diff.data["path"] == "src"
    assert diff.content.endswith("[git diff truncated]")
    argv = sandbox.requests[0].argv
    assert "--cached" in argv
    assert "--unified=5" in argv
    assert argv[-2:] == ("--", "src")
    await tools.aclose()


@pytest.mark.asyncio
async def test_git_diff_rejects_escape_and_invalid_limits(workspace: Path) -> None:
    sandbox = FakeGitSandbox([])
    tools = ToolRegistry()
    register_git_tools(tools, sandbox)
    with pytest.raises(ToolExecutionError, match="escapes"):
        await tools.invoke("git_diff", {"path": "../outside"}, tool_context(workspace))
    with pytest.raises(ToolValidationError, match="context_lines"):
        await tools.invoke("git_diff", {"context_lines": 51}, tool_context(workspace))
    await tools.aclose()


@pytest.mark.asyncio
async def test_git_failure_is_a_tool_error_and_missing_git_skips_registration(
    workspace: Path,
) -> None:
    missing = ToolRegistry()
    assert register_git_tools(missing, FakeGitSandbox([], include_git=False)) == ()
    await missing.aclose()

    sandbox = FakeGitSandbox([result(stderr="fatal: not a git repository", exit_code=128)])
    tools = ToolRegistry()
    register_git_tools(tools, sandbox)
    with pytest.raises(ToolExecutionError, match="not a git repository"):
        await tools.invoke("git_status", {}, tool_context(workspace))
    await tools.aclose()


@pytest.mark.asyncio
async def test_git_diff_empty_unstaged_and_type_validation(workspace: Path) -> None:
    sandbox = FakeGitSandbox([result("")])
    tools = ToolRegistry()
    register_git_tools(tools, sandbox)
    diff = await tools.invoke("git_diff", {}, tool_context(workspace))
    assert diff.content == "No tracked differences."
    assert diff.data is not None and diff.data["truncated"] is False
    assert "--cached" not in sandbox.requests[0].argv
    with pytest.raises(ToolValidationError, match="context_lines"):
        await tools.invoke("git_diff", {"context_lines": True}, tool_context(workspace))
    await tools.aclose()

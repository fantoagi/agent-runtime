from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from agent_runtime.coding_tools import register_coding_tools
from agent_runtime.domain import (
    AgentDefinition,
    ModelConfig,
    RunStatus,
    ToolCall,
    ToolExecutionError,
    ToolValidationError,
)
from agent_runtime.interactive import ChatOptions, InteractiveShell
from agent_runtime.local_config import LocalRuntimeSettings
from agent_runtime.local_runtime import create_configured_local_runtime
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.sandbox import LocalProcessSandbox, SandboxLimits, register_process_tool
from agent_runtime.tools import ToolContext, ToolRegistry, register_builtin_tools


def context(workspace: Path) -> ToolContext:
    return ToolContext(run_id="run-test", step_id=1, workspace_path=workspace, metadata={})


async def _invoke_list_files(workspace: Path) -> list[str]:
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        result = await tools.invoke("list_files", {"path": "."}, context(workspace))
    finally:
        await tools.aclose()
    assert result.data is not None
    return list(result.data["files"])


@pytest.mark.asyncio
async def test_list_files_is_sorted_bounded_and_skips_runtime_directories(
    workspace: Path,
) -> None:
    (workspace / "src").mkdir()
    (workspace / "src" / "b.py").write_text("b", encoding="utf-8")
    (workspace / "src" / "a.py").write_text("a", encoding="utf-8")
    (workspace / "src" / "note.txt").write_text("note", encoding="utf-8")
    (workspace / ".GIT").mkdir()
    (workspace / ".GIT" / "hidden.py").write_text("hidden", encoding="utf-8")
    (workspace / ".runtime-test-data").mkdir()
    (workspace / ".runtime-test-data" / "noise.py").write_text("noise", encoding="utf-8")
    (workspace / ".coverage").write_text("generated", encoding="utf-8")
    (workspace / "coverage.json").write_text("{}", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        result = await tools.invoke(
            "list_files",
            {"path": ".", "pattern": "*.py", "recursive": True, "max_results": 1},
            context(workspace),
        )
    finally:
        await tools.aclose()

    assert result.data is not None
    assert result.data["files"] == ["src/a.py"]
    assert result.data["truncated"] is True
    assert ".GIT/hidden.py" not in result.content
    assert ".runtime-test-data/noise.py" not in result.content
    all_files = await _invoke_list_files(workspace)
    assert ".coverage" not in all_files
    assert "coverage.json" not in all_files


@pytest.mark.asyncio
async def test_list_files_non_recursive_and_workspace_escape(workspace: Path) -> None:
    (workspace / "top.py").write_text("top", encoding="utf-8")
    (workspace / "nested").mkdir()
    (workspace / "nested" / "inside.py").write_text("inside", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        result = await tools.invoke(
            "list_files",
            {"path": ".", "pattern": "*.py", "recursive": False},
            context(workspace),
        )
        with pytest.raises(ToolExecutionError, match="escapes"):
            await tools.invoke("list_files", {"path": ".."}, context(workspace))
    finally:
        await tools.aclose()

    assert result.data is not None
    assert result.data["files"] == ["top.py"]


@pytest.mark.asyncio
async def test_search_text_returns_lines_and_honors_case_and_glob(workspace: Path) -> None:
    (workspace / "src").mkdir()
    (workspace / "src" / "runtime.py").write_text(
        "Runtime kernel\nother\nruntime state\n", encoding="utf-8"
    )
    (workspace / "src" / "ignored.txt").write_text("Runtime", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        insensitive = await tools.invoke(
            "search_text",
            {"query": "runtime", "path": "src", "glob": "*.py"},
            context(workspace),
        )
        sensitive = await tools.invoke(
            "search_text",
            {
                "query": "Runtime",
                "path": "src",
                "glob": "*.py",
                "case_sensitive": True,
            },
            context(workspace),
        )
    finally:
        await tools.aclose()

    assert insensitive.data is not None
    assert [item["line"] for item in insensitive.data["matches"]] == [1, 3]
    assert sensitive.data is not None
    assert [item["line"] for item in sensitive.data["matches"]] == [1]
    assert "ignored.txt" not in insensitive.content


@pytest.mark.asyncio
async def test_search_text_missing_path_suggests_workspace_relative_matches(
    workspace: Path,
) -> None:
    target = workspace / "src" / "agent_runtime" / "runtime.py"
    target.parent.mkdir(parents=True)
    target.write_text("class Runtime: pass\n", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        with pytest.raises(
            ToolExecutionError,
            match=r"Possible matches: src/agent_runtime/runtime\.py",
        ):
            await tools.invoke(
                "search_text",
                {"query": "Runtime", "path": "runtime.py"},
                context(workspace),
            )
    finally:
        await tools.aclose()


@pytest.mark.asyncio
async def test_search_text_skips_binary_and_large_files(workspace: Path) -> None:
    (workspace / "ok.txt").write_text("needle", encoding="utf-8")
    (workspace / "binary.txt").write_bytes(b"needle\x00binary")
    (workspace / "large.txt").write_bytes(b"needle" + b"x" * 1_000_001)
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        result = await tools.invoke(
            "search_text", {"query": "needle", "path": ".", "glob": "*.txt"}, context(workspace)
        )
    finally:
        await tools.aclose()

    assert result.data is not None
    assert [item["path"] for item in result.data["matches"]] == ["ok.txt"]
    assert result.data["files_skipped"] == 2


@pytest.mark.asyncio
async def test_replace_text_is_exact_atomic_and_reports_hashes(workspace: Path) -> None:
    target = workspace / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    tools = ToolRegistry()
    definitions = register_coding_tools(tools)
    replace_definition = next(item for item in definitions if item.name == "replace_text")
    try:
        result = await tools.invoke(
            "replace_text",
            {"path": "sample.py", "old_text": "value = 1", "new_text": "value = 2"},
            context(workspace),
        )
    finally:
        await tools.aclose()

    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert result.data is not None
    assert result.data["replacements"] == 1
    assert result.data["before_sha256"] != result.data["after_sha256"]
    assert not list(workspace.glob(".sample.py.*.tmp"))
    assert replace_definition.requires_approval is True
    assert replace_definition.side_effecting is True


@pytest.mark.asyncio
async def test_replace_text_rejects_zero_or_ambiguous_matches_without_writing(
    workspace: Path,
) -> None:
    target = workspace / "sample.txt"
    target.write_text("same same", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        with pytest.raises(ToolExecutionError, match="expected 1, found 0"):
            await tools.invoke(
                "replace_text",
                {"path": "sample.txt", "old_text": "missing", "new_text": "new"},
                context(workspace),
            )
        with pytest.raises(ToolExecutionError, match="expected 1, found 2"):
            await tools.invoke(
                "replace_text",
                {"path": "sample.txt", "old_text": "same", "new_text": "new"},
                context(workspace),
            )
    finally:
        await tools.aclose()

    assert target.read_text(encoding="utf-8") == "same same"


@pytest.mark.asyncio
async def test_read_file_lines_is_numbered_bounded_and_traceable(workspace: Path) -> None:
    target = workspace / "large.py"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        result = await tools.invoke(
            "read_file_lines",
            {"path": "large.py", "start_line": 2, "max_lines": 2, "max_chars": 1_000},
            context(workspace),
        )
    finally:
        await tools.aclose()

    assert "lines 2-3 of 4 (more available; next_start_line=4)" in result.content
    assert "2 | two" in result.content
    assert "3 | three" in result.content
    assert result.data is not None
    assert result.data["start_line"] == 2
    assert result.data["end_line"] == 3
    assert result.data["next_start_line"] == 4
    assert result.data["has_more"] is True
    assert len(result.content) <= 1_000
    assert len(result.data["sha256"]) == 64


@pytest.mark.asyncio
async def test_apply_patch_validates_all_edits_before_multi_file_write(workspace: Path) -> None:
    first = workspace / "first.py"
    second = workspace / "second.py"
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("name = 'old'\n", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        patched = await tools.invoke(
            "apply_patch",
            {
                "edits": [
                    {"path": "first.py", "old_text": "1", "new_text": "2"},
                    {"path": "second.py", "old_text": "old", "new_text": "new"},
                ]
            },
            context(workspace),
        )
        assert patched.data is not None
        assert patched.data["edit_count"] == 2
        assert patched.data["file_count"] == 2
        with pytest.raises(ToolExecutionError, match="No file was changed"):
            await tools.invoke(
                "apply_patch",
                {
                    "edits": [
                        {"path": "first.py", "old_text": "2", "new_text": "3"},
                        {"path": "second.py", "old_text": "missing", "new_text": "x"},
                    ]
                },
                context(workspace),
            )
    finally:
        await tools.aclose()

    assert first.read_text(encoding="utf-8") == "value = 2\n"
    assert second.read_text(encoding="utf-8") == "name = 'new'\n"


def make_settings(workspace: Path, *, enable_process: bool = True) -> LocalRuntimeSettings:
    state = workspace / ".agent-runtime"
    return LocalRuntimeSettings(
        config_path=workspace / "agent-runtime.toml",
        workspace=workspace,
        state_dir=state,
        agent_name="local",
        system_prompt="Inspect, edit, and verify code with tools.",
        provider="mock",
        model="arithmetic-demo",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_KEY",
        model_timeout_seconds=10.0,
        run_timeout_seconds=30.0,
        shutdown_timeout_seconds=5.0,
        max_inflight_runs=4,
        max_concurrent_model_requests=2,
        max_sync_tool_workers=2,
        max_pending_sync_tools=4,
        host="127.0.0.1",
        port=8000,
        log_level="INFO",
        log_file=state / "runtime.log",
        log_max_bytes=1024 * 1024,
        log_backup_count=1,
        enable_process_tool=enable_process,
        allowed_executables=(sys.executable,),
        process_timeout_seconds=10.0,
        process_max_output_bytes=100_000,
        process_max_concurrent=1,
    )


@pytest.mark.asyncio
async def test_configured_local_runtime_registers_coding_and_process_tools(
    workspace: Path,
) -> None:
    runtime = create_configured_local_runtime(make_settings(workspace))
    try:
        agent = runtime.list_agents()[0]
        names = [tool.name for tool in agent.tools]
        assert names == [
            "calculator",
            "list_files",
            "search_text",
            "read_file_lines",
            "read_text_file",
            "read_artifact",
            "replace_text",
            "apply_patch",
            "write_text_file",
            "run_process",
        ]
        assert runtime.sandbox_snapshot()["sandboxes"][0]["kind"] == "local-process"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_configured_local_runtime_registers_git_tools_when_git_is_allowlisted(
    workspace: Path,
) -> None:
    git = shutil.which("git")
    assert git is not None
    settings = replace(
        make_settings(workspace),
        allowed_executables=(sys.executable, git),
    )
    runtime = create_configured_local_runtime(settings)
    try:
        names = [tool.name for tool in runtime.list_agents()[0].tools]
        assert "git_status" in names
        assert "git_diff" in names
        assert names.index("git_diff") < names.index("run_process")
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_run_process_uses_allowlist_and_rejects_unknown_executable(
    workspace: Path,
) -> None:
    sandbox = LocalProcessSandbox(
        workspace,
        allowed_executables=[sys.executable],
        limits=SandboxLimits(
            timeout_seconds=10, max_output_bytes=100_000, max_concurrent_processes=1
        ),
    )
    tools = ToolRegistry()
    register_process_tool(tools, sandbox, handler_timeout_seconds=12)
    try:
        completed = await tools.invoke(
            "run_process",
            {"argv": [sys.executable, "-c", "print('coding-loop-ok')"]},
            context(workspace),
        )
        assert "coding-loop-ok" in completed.content
        with pytest.raises(ToolExecutionError, match=r"cannot be resolved|not allowed"):
            await tools.invoke(
                "run_process", {"argv": ["definitely-not-allowed", "--version"]}, context(workspace)
            )
    finally:
        await tools.aclose()


def test_register_process_tool_rejects_non_positive_handler_timeout(workspace: Path) -> None:
    sandbox = LocalProcessSandbox(workspace, allowed_executables=[sys.executable])
    tools = ToolRegistry()
    with pytest.raises(ValueError, match="handler_timeout_seconds"):
        register_process_tool(tools, sandbox, handler_timeout_seconds=0)


class ScriptedPromptReader:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    async def prompt(self, message: str) -> str:
        del message
        return next(self.values)


@pytest.mark.asyncio
async def test_interactive_workspace_tools_and_diff_commands(workspace: Path) -> None:
    runtime = create_configured_local_runtime(make_settings(workspace, enable_process=False))
    output = StringIO()
    shell = InteractiveShell(
        runtime,
        make_settings(workspace, enable_process=False),
        options=ChatOptions(),
        console=Console(file=output, force_terminal=False, color_system=None, width=160),
        prompt_reader=ScriptedPromptReader([]),
    )
    shell.session = runtime.create_session({"kind": "interactive-cli"})
    try:
        assert await shell._handle_command(
            type("Command", (), {"name": "/workspace", "arguments": ()})()
        )
        assert await shell._handle_command(
            type("Command", (), {"name": "/tools", "arguments": ()})()
        )
        assert await shell._handle_command(
            type("Command", (), {"name": "/diff", "arguments": ()})()
        )
    finally:
        await runtime.shutdown()

    rendered = output.getvalue()
    assert "Coding Workspace" in rendered
    assert "list_files" in rendered
    assert "replace_text" in rendered
    assert "No file changes" in rendered


@pytest.mark.asyncio
async def test_coding_agent_completes_inspect_edit_verify_loop(workspace: Path) -> None:
    target = workspace / "example.py"
    target.write_text("answer = 1\n", encoding="utf-8")
    tools = ToolRegistry()
    register_builtin_tools(tools)
    coding = register_coding_tools(tools)
    sandbox = LocalProcessSandbox(workspace, allowed_executables=[sys.executable])
    process = register_process_tool(tools, sandbox, handler_timeout_seconds=35)

    def responder(messages, definitions, config):
        del definitions, config
        if messages[-1].role == "user":
            return ModelResponse(tool_calls=[ToolCall("list", "list_files", {"pattern": "*.py"})])
        if messages[-1].name == "list_files":
            return ModelResponse(
                tool_calls=[ToolCall("read", "read_text_file", {"path": "example.py"})]
            )
        if messages[-1].name == "read_text_file":
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "replace",
                        "apply_patch",
                        {
                            "edits": [
                                {
                                    "path": "example.py",
                                    "old_text": "answer = 1",
                                    "new_text": "answer = 42",
                                }
                            ]
                        },
                    )
                ]
            )
        if messages[-1].name == "apply_patch":
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "verify",
                        "run_process",
                        {
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; assert 'answer = 42' in Path('example.py').read_text()",
                            ]
                        },
                    )
                ]
            )
        return ModelResponse(content="Updated example.py and verified it successfully.")

    runtime = Runtime(
        RuntimeConfig(workspace_path=workspace, database_path=workspace / "runtime.sqlite3"),
        provider=MockProvider(responder),
        tools=tools,
    )
    runtime.register_agent(
        AgentDefinition(
            name="coder",
            system_prompt="Inspect, edit, and verify the workspace.",
            tools=[tools.get("read_text_file").definition, *coding, process],
            model=ModelConfig(provider="mock", model="coding-loop"),
        )
    )
    session = runtime.create_session({"kind": "coding-loop-test"})
    run = await runtime.run(
        "coder",
        "Change answer to 42 and verify it",
        session_id=session.id,
    )
    assert run.status is RunStatus.WAITING_FOR_APPROVAL
    first = runtime.store.pending_approval(run.id)
    assert first is not None
    runtime.resolve_approval(first.id, True, "allow exact file replacement")
    run = await runtime.resume(run.id)
    assert run.status is RunStatus.WAITING_FOR_APPROVAL
    second = runtime.store.pending_approval(run.id)
    assert second is not None
    runtime.resolve_approval(second.id, True, "allow verification process")
    try:
        completed = await runtime.resume(run.id)
        output = StringIO()
        shell = InteractiveShell(
            runtime,
            make_settings(workspace, enable_process=False),
            options=ChatOptions(),
            console=Console(file=output, force_terminal=False, color_system=None, width=160),
            prompt_reader=ScriptedPromptReader([]),
        )
        shell.session = session
        shell._print_recent_changes()
        rendered_changes = output.getvalue()
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    assert completed.tool_call_count == 4
    assert target.read_text(encoding="utf-8") == "answer = 42\n"
    assert "verified" in (completed.result or "").lower()
    assert "apply_patch" in rendered_changes
    assert "example.py" in rendered_changes
    assert "replacement(s)" in rendered_changes
    assert " -> " in rendered_changes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edits, message",
    [
        ([], "between 1"),
        ([123], "must be an object"),
        ([{"path": "sample.py", "old_text": "x", "new_text": "y", "extra": 1}], "unsupported"),
        ([{"path": "sample.py", "old_text": "x"}], "missing"),
        ([{"path": 1, "old_text": "x", "new_text": "y"}], "must be strings"),
        ([{"path": "sample.py", "old_text": "", "new_text": "y"}], "must not be empty"),
        ([{"path": "missing.py", "old_text": "x", "new_text": "y"}], "does not exist"),
    ],
)
async def test_apply_patch_rejects_invalid_edit_shapes_before_writing(
    workspace: Path, edits: list[object], message: str
) -> None:
    target = workspace / "sample.py"
    target.write_text("x = 1\n", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        with pytest.raises(ToolExecutionError, match=message):
            await tools.invoke("apply_patch", {"edits": edits}, context(workspace))
    finally:
        await tools.aclose()
    assert target.read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.asyncio
async def test_apply_patch_supports_sequential_edits_to_same_file(workspace: Path) -> None:
    target = workspace / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        result = await tools.invoke(
            "apply_patch",
            {
                "edits": [
                    {"path": "sample.py", "old_text": "1", "new_text": "2"},
                    {"path": "sample.py", "old_text": "2", "new_text": "3"},
                ]
            },
            context(workspace),
        )
    finally:
        await tools.aclose()
    assert target.read_text(encoding="utf-8") == "value = 3\n"
    assert result.data is not None and result.data["file_count"] == 1


@pytest.mark.asyncio
async def test_missing_extension_path_suggests_matching_python_file(
    workspace: Path,
) -> None:
    target = workspace / "src" / "agent_runtime" / "providers.py"
    target.parent.mkdir(parents=True)
    target.write_text("class Provider:\n    pass\n", encoding="utf-8")
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        with pytest.raises(
            ToolExecutionError,
            match=r"Possible matches: src/agent_runtime/providers\.py",
        ):
            await tools.invoke(
                "search_text",
                {"query": "Provider", "path": "src/agent_runtime/providers"},
                context(workspace),
            )
    finally:
        await tools.aclose()


@pytest.mark.asyncio
async def test_search_text_unknown_max_lines_explains_correct_bounded_flow(
    workspace: Path,
) -> None:
    tools = ToolRegistry()
    register_coding_tools(tools)
    try:
        with pytest.raises(ToolValidationError) as captured:
            await tools.invoke(
                "search_text",
                {"query": "Runtime", "max_lines": 20},
                context(workspace),
            )
    finally:
        await tools.aclose()

    message = str(captured.value)
    assert "Allowed arguments:" in message
    assert "Use max_results to bound search matches" in message
    assert "read_file_lines for bounded line ranges" in message

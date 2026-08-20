from __future__ import annotations

from collections.abc import Iterable
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from agent_runtime.cli import build_parser
from agent_runtime.domain import (
    AgentDefinition,
    AgentRun,
    Message,
    ModelConfig,
    RunStatus,
    RuntimeEvent,
    ToolCall,
    utc_now,
)
from agent_runtime.interactive import (
    ChatOptions,
    DisplayMode,
    EventRenderer,
    InteractiveShell,
    SlashCommand,
    parse_slash_command,
)
from agent_runtime.local_config import LocalRuntimeSettings
from agent_runtime.local_runtime import create_configured_local_runtime
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry, register_builtin_tools


class ScriptedPromptReader:
    def __init__(self, values: Iterable[str | BaseException]) -> None:
        self.values = iter(values)

    async def prompt(self, message: str) -> str:
        del message
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value


def make_settings(workspace: Path) -> LocalRuntimeSettings:
    state_dir = workspace / "state"
    return LocalRuntimeSettings(
        config_path=workspace / "agent-runtime.toml",
        workspace=workspace,
        state_dir=state_dir,
        agent_name="local",
        system_prompt="Use tools when needed.",
        provider="mock",
        model="arithmetic-demo",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_API_KEY",
        model_timeout_seconds=10.0,
        run_timeout_seconds=10.0,
        shutdown_timeout_seconds=5.0,
        max_inflight_runs=4,
        max_concurrent_model_requests=2,
        max_sync_tool_workers=2,
        max_pending_sync_tools=4,
        host="127.0.0.1",
        port=8000,
        log_level="INFO",
        log_file=state_dir / "runtime.log",
        log_max_bytes=1024 * 1024,
        log_backup_count=1,
    )


def test_parse_slash_command() -> None:
    assert parse_slash_command("hello") is None
    assert parse_slash_command("/HELP") == SlashCommand("/help")
    assert parse_slash_command('/resume "session one"') == SlashCommand("/resume", ("session one",))


@pytest.mark.asyncio
async def test_interactive_print_mode_runs_real_runtime_path(workspace: Path) -> None:
    settings = make_settings(workspace)
    runtime = create_configured_local_runtime(settings)
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    shell = InteractiveShell(
        runtime,
        settings,
        options=ChatOptions(initial_prompt="19 * 23", print_only=True),
        console=console,
    )
    assert shell.prompt_reader is None
    try:
        exit_code = await shell.run()
        assert shell.session is not None
        runs = runtime.session_runs(shell.session.id)
        events = runtime.store.events_since(runs[0].id)
    finally:
        await runtime.shutdown()

    assert exit_code == 0
    assert "437" in output.getvalue()
    assert len(runs) == 1
    assert runs[0].status is RunStatus.COMPLETED
    assert any(event.type == "session.history.loaded" for event in events)


@pytest.mark.asyncio
async def test_interactive_commands_create_and_resume_sessions(workspace: Path) -> None:
    settings = make_settings(workspace)
    (workspace / "AGENTS.md").write_text("Use focused tests.", encoding="utf-8")
    runtime = create_configured_local_runtime(settings)
    output = StringIO()
    shell = InteractiveShell(
        runtime,
        settings,
        console=Console(file=output, force_terminal=False, color_system=None, width=120),
        prompt_reader=ScriptedPromptReader(
            [
                "/display verbose",
                "/status",
                "/tools",
                "/workspace",
                "/new",
                "/sessions",
                "/quit",
            ]
        ),
    )
    try:
        exit_code = await shell.run()
        session_count = runtime.store.count_sessions()
    finally:
        await runtime.shutdown()

    assert exit_code == 0
    rendered = output.getvalue()
    assert "Display mode: verbose" in rendered
    assert "Interactive Runtime Status" in rendered
    assert "verbose" in rendered
    assert "calculator" in rendered
    assert "read_file_lines" in rendered
    assert "apply_patch" in rendered
    assert "Project instructions" in rendered
    assert "AGENTS.md" in rendered
    assert "New session:" in rendered
    assert "Interactive Sessions" in rendered
    assert session_count == 2


@pytest.mark.asyncio
async def test_interactive_shell_resolves_tool_approval(workspace: Path) -> None:
    settings = make_settings(workspace)
    tools = ToolRegistry()
    register_builtin_tools(tools)

    def responder(messages, definitions, config):
        del definitions, config
        if messages[-1].role == "tool":
            return ModelResponse(content="file written")
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    "write-1",
                    "write_text_file",
                    {"path": "approved.txt", "content": "approved"},
                )
            ]
        )

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "approval.sqlite3",
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    runtime.register_agent(
        AgentDefinition(
            name="local",
            system_prompt="write when asked",
            tools=[tools.get("write_text_file").definition],
            model=ModelConfig(provider="mock", model="approval"),
        )
    )
    output = StringIO()
    shell = InteractiveShell(
        runtime,
        settings,
        options=ChatOptions(initial_prompt="write the file"),
        console=Console(file=output, force_terminal=False, color_system=None, width=120),
        prompt_reader=ScriptedPromptReader(["y", "/quit"]),
    )
    try:
        exit_code = await shell.run()
        assert shell.last_run_id is not None
        completed = runtime.store.get_run(shell.last_run_id)
    finally:
        await runtime.shutdown()

    assert exit_code == 0
    assert completed.status is RunStatus.COMPLETED
    assert completed.tool_call_count == 1
    assert (workspace / "approved.txt").read_text(encoding="utf-8") == "approved"
    rendered = output.getvalue()
    assert "Approval required · write_text_file" in rendered
    assert "Approved write_text_file" in rendered
    assert "write_text_file  approved.txt" in rendered
    assert "✓ write_text_file" in rendered
    assert "file written" in rendered
    assert "Run " in rendered
    assert rendered.index("Approved write_text_file") < rendered.index("✓ write_text_file")
    assert rendered.index("✓ write_text_file") < rendered.index("file written")


@pytest.mark.asyncio
async def test_session_history_is_loaded_only_when_requested(workspace: Path) -> None:
    captured: list[list[Message]] = []

    def responder(messages, tools, config):
        del tools, config
        captured.append([Message.from_dict(message.to_dict()) for message in messages])
        return ModelResponse(content=f"reply:{messages[-1].content}")

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "history.sqlite3",
        ),
        provider=MockProvider(responder),
        tools=ToolRegistry(),
    )
    agent = AgentDefinition(
        name="chat",
        system_prompt="system",
        tools=[],
        model=ModelConfig(provider="mock", model="chat"),
    )
    runtime.register_agent(agent)
    session = runtime.create_session({"kind": "interactive-cli"})
    try:
        first = await runtime.run(
            "chat",
            "first",
            {"include_session_history": True},
            session_id=session.id,
        )
        second = await runtime.run(
            "chat",
            "second",
            {"include_session_history": True, "session_history_limit": 20},
            session_id=session.id,
        )
        third = await runtime.run("chat", "third", session_id=session.id)
        second_events = runtime.store.events_since(second.id)
    finally:
        await runtime.shutdown()

    assert first.result == "reply:first"
    assert second.result == "reply:second"
    assert third.result == "reply:third"
    assert [(item.role, item.content) for item in captured[1]] == [
        ("system", "system"),
        ("user", "first"),
        ("assistant", "reply:first"),
        ("user", "second"),
    ]
    assert [(item.role, item.content) for item in captured[2]] == [
        ("system", "system"),
        ("user", "third"),
    ]
    history_event = next(event for event in second_events if event.type == "session.history.loaded")
    assert history_event.payload["run_count"] == 1


def test_event_renderer_hides_internal_events_and_streams_content() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()

    renderer.render(RuntimeEvent("e1", "run", 1, "context.built", utc_now(), {}))
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "model.delta",
            utc_now(),
            {"content": "hello"},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e3",
            "run",
            3,
            "tool.requested",
            utc_now(),
            {"tool_name": "read_text_file", "arguments": {"path": "README.md"}},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e4",
            "run",
            4,
            "tool.completed",
            utc_now(),
            {
                "tool_name": "read_text_file",
                "content": "README loaded",
            },
        )
    )
    renderer.render(
        RuntimeEvent(
            "e5",
            "run",
            5,
            "completion.verification_requested",
            utc_now(),
            {"unmet_requirements": ["post-change Git diff was not inspected"]},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e6",
            "run",
            6,
            "completion.evidence",
            utc_now(),
            {
                "status": "verified",
                "changed_files": ["example.py"],
                "diff_required": True,
                "diff_inspected": True,
                "validation_required": True,
                "validation_succeeded": True,
                "validations": [
                    {
                        "command": "python -m pytest tests/test_example.py -q",
                        "exit_code": 0,
                        "succeeded": True,
                    }
                ],
                "failed_tools": [],
                "rejected_tools": [],
                "unmet_requirements": [],
            },
        )
    )

    rendered = output.getvalue()
    assert "context.built" not in rendered
    assert "Assistant >" in rendered
    assert "hello" in rendered
    assert "read_text_file" in rendered
    assert "README loaded" in rendered
    assert "Inspecting workspace" in rendered
    assert "Verifying changes" in rendered
    assert "verifying changes before completion" in rendered
    assert "Task summary" in rendered
    assert "Status: verified" in rendered
    assert "example.py" in rendered
    assert "Git diff inspected" in rendered
    assert "python -m pytest tests/test_example.py -q" in rendered


def test_chat_display_mode_arguments_are_compatible() -> None:
    parser = build_parser()

    assert parser.parse_args(["chat"]).display_mode == "compact"
    assert parser.parse_args(["chat", "--compact"]).display_mode == "compact"
    assert parser.parse_args(["chat", "--verbose"]).display_mode == "verbose"

    with pytest.raises(SystemExit):
        parser.parse_args(["chat", "--compact", "--verbose"])


def test_event_renderer_buffers_streaming_markdown_without_printing_fences() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=100)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "model.delta",
            utc_now(),
            {"content": "Here is the result:\n```python\n"},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "model.delta",
            utc_now(),
            {"content": "print('hello')\n```"},
        )
    )
    run = AgentRun.create("local", "show code")
    run.status = RunStatus.COMPLETED
    run.result = "Here is the result:\n```python\nprint('hello')\n```"
    renderer.finish(run)

    rendered = output.getvalue()
    assert "Assistant >" in rendered
    assert "Here is the result:" in rendered
    assert "print('hello')" in rendered
    assert "```" not in rendered


def test_event_renderer_compact_mode_hides_large_tool_payloads() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console, mode=DisplayMode.COMPACT)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.requested",
            utc_now(),
            {
                "tool_name": "write_text_file",
                "arguments": {"path": "src/example.py", "content": "private body" * 100},
            },
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "tool.completed",
            utc_now(),
            {"tool_name": "write_text_file", "content": "line one\nline two"},
        )
    )

    rendered = output.getvalue()
    assert "write_text_file  src/example.py" in rendered
    assert "private body" not in rendered
    assert "line one line two" in rendered
    assert "Arguments" not in rendered
    assert "Result" not in rendered


def test_event_renderer_compact_mode_collapses_inspection_request_and_reuse() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console, mode=DisplayMode.COMPACT)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.requested",
            utc_now(),
            {
                "tool_name": "search_text",
                "arguments": {"query": "Runtime", "path": "src"},
            },
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "tool.completed",
            utc_now(),
            {"tool_name": "search_text", "content": "Found 1 match"},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e3",
            "run",
            3,
            "tool.reused",
            utc_now(),
            {
                "tool_name": "search_text",
                "arguments": {"query": "Runtime", "path": "src"},
                "reused_from_tool_execution_id": "tool-exec-1",
            },
        )
    )

    rendered = output.getvalue()
    assert rendered.count("search_text") == 2
    assert "✓ search_text" in rendered
    assert "reused earlier identical result" in rendered
    assert "● search_text" not in rendered


def test_event_renderer_verbose_mode_formats_structured_payloads() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console, mode=DisplayMode.VERBOSE)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.requested",
            utc_now(),
            {
                "tool_name": "search_text",
                "arguments": {"query": "Runtime", "path": "src"},
            },
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "tool.started",
            utc_now(),
            {"tool_name": "search_text"},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e3",
            "run",
            3,
            "tool.completed",
            utc_now(),
            {"tool_name": "search_text", "content": '{"matches": ["a.py:1"]}'},
        )
    )

    rendered = output.getvalue()
    assert "Arguments" in rendered
    assert '"query"' in rendered
    assert "running search_text" in rendered
    assert "Result" in rendered
    assert '"matches"' in rendered


def test_event_renderer_print_mode_emits_only_final_result() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=100)
    renderer = EventRenderer(console, quiet=True)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "model.delta",
            utc_now(),
            {"content": "draft response"},
        )
    )
    run = AgentRun.create("local", "task")
    run.status = RunStatus.COMPLETED
    run.result = "final response"
    renderer.finish(run)

    assert output.getvalue() == "final response\n"


def test_event_renderer_terminal_appends_each_markdown_block_once() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system="standard", width=80)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "model.delta",
            utc_now(),
            {"content": "README summary.\n\n## Result\n\n"},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "model.delta",
            utc_now(),
            {"content": "- first\n- second"},
        )
    )
    run = AgentRun.create("local", "task")
    run.status = RunStatus.COMPLETED
    run.result = "README summary.\n\n## Result\n\n- first\n- second"
    renderer.finish(run)

    rendered = output.getvalue()
    assert "Assistant" in rendered
    assert rendered.count("README summary.") == 1
    assert "Result" in rendered
    assert rendered.count("first") == 1
    assert rendered.count("second") == 1
    assert "```" not in rendered
    assert "\x1b[" in rendered
    assert "\x1b[1A" not in rendered
    assert "\x1b[2K" not in rendered


def test_event_renderer_waits_for_complete_fenced_code_block() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=80)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "model.delta",
            utc_now(),
            {"content": "```python\nprint('one')\n"},
        )
    )
    assert "print('one')" not in output.getvalue()

    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "model.delta",
            utc_now(),
            {"content": "print('two')\n```\n"},
        )
    )
    run = AgentRun.create("local", "task")
    run.status = RunStatus.COMPLETED
    run.result = "```python\nprint('one')\nprint('two')\n```"
    renderer.finish(run)

    rendered = output.getvalue()
    assert rendered.count("print('one')") == 1
    assert rendered.count("print('two')") == 1
    assert "```" not in rendered


def test_event_renderer_artifact_and_failure_summaries_are_bounded() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console, mode=DisplayMode.COMPACT)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.completed",
            utc_now(),
            {
                "tool_name": "run_process",
                "content": "preview that should not win",
                "artifact": {"relative_path": "run/tool-results/process.txt"},
            },
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "tool.failed",
            utc_now(),
            {"tool_name": "run_process", "error": "failure\n" + ("x" * 500)},
        )
    )

    rendered = output.getvalue()
    assert "result stored in run/tool-results/process.txt" in rendered
    assert "preview that should not win" not in rendered
    assert "failure" in rendered
    assert "x" * 250 not in rendered


def test_event_renderer_verbose_failure_preserves_bounded_multiline_detail() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console, mode=DisplayMode.VERBOSE)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.failed",
            utc_now(),
            {"tool_name": "run_process", "error": "line one\nline two"},
        )
    )

    rendered = output.getvalue()
    assert "Failure" in rendered
    assert "line one" in rendered
    assert "line two" in rendered



def test_event_renderer_projects_execution_phases_only_on_transition() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()

    events = [
        ("list_files", {"path": "."}),
        ("read_file_lines", {"path": "README.md"}),
        (
            "apply_patch",
            {
                "edits": [
                    {
                        "path": "src/example.py",
                        "old_text": "before",
                        "new_text": "after",
                    }
                ]
            },
        ),
        ("write_text_file", {"path": "notes.md", "content": "done"}),
        ("git_diff", {}),
        ("run_process", {"argv": ["python", "-m", "pytest", "-q"]}),
    ]
    for sequence, (tool_name, arguments) in enumerate(events, start=1):
        renderer.render(
            RuntimeEvent(
                f"e{sequence}",
                "run",
                sequence,
                "tool.requested",
                utc_now(),
                {"tool_name": tool_name, "arguments": arguments},
            )
        )

    rendered = output.getvalue()
    assert rendered.count("Inspecting workspace") == 1
    assert rendered.count("Editing workspace") == 1
    assert rendered.count("Verifying changes") == 1
    assert "Executing action" not in rendered


def test_event_renderer_labels_direct_validation_script_as_verifying() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.requested",
            utc_now(),
            {
                "tool_name": "run_process",
                "arguments": {
                    "argv": [
                        r"D:\AICoding\Agent\.venv\Scripts\python.exe",
                        "scripts/check_docs.py",
                    ]
                },
            },
        )
    )

    rendered = output.getvalue()
    assert "Verifying changes" in rendered
    assert "Executing action" not in rendered


def test_event_renderer_does_not_label_non_validation_process_as_verifying() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.requested",
            utc_now(),
            {"tool_name": "run_process", "arguments": {"argv": ["npm", "install"]}},
        )
    )

    rendered = output.getvalue()
    assert "Executing action" in rendered
    assert "Verifying changes" not in rendered


def test_event_renderer_shows_human_readable_process_approval() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "approval.requested",
            utc_now(),
            {
                "tool_execution_id": "tool-exec-1",
                "tool_name": "run_process",
                "arguments": {
                    "argv": ["python", "-m", "pytest", "tests/test_interactive.py", "-q"],
                    "cwd": ".",
                    "timeout_seconds": 120,
                    "env": {"PYTHONUTF8": "1"},
                },
                "authorization": {
                    "sandbox_required": True,
                    "capabilities": ["process_exec", "file_read", "file_write"],
                    "reason": "local process policy",
                },
            },
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "approval.resolved",
            utc_now(),
            {"tool_execution_id": "tool-exec-1", "approved": True},
        )
    )

    rendered = output.getvalue()
    assert "Approval required · run_process" in rendered
    assert "Run a sandboxed local process" in rendered
    assert "python -m pytest tests/test_interactive.py -q" in rendered
    assert "Working directory: ." in rendered
    assert "Timeout: 120 seconds" in rendered
    assert "Environment names: PYTHONUTF8" in rendered
    assert "sandbox required" in rendered
    assert "Approved run_process" in rendered
    assert '"PYTHONUTF8": "1"' not in rendered


def test_event_renderer_shows_patch_approval_without_full_patch_body() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "approval.requested",
            utc_now(),
            {
                "tool_execution_id": "tool-exec-2",
                "tool_name": "apply_patch",
                "arguments": {
                    "edits": [
                        {
                            "path": "src/example.py",
                            "old_text": "old behavior " + ("x" * 300),
                            "new_text": "new behavior " + ("y" * 300),
                        },
                        {
                            "path": "tests/test_example.py",
                            "old_text": "old assertion",
                            "new_text": "new assertion",
                        },
                    ]
                },
                "authorization": {
                    "sandbox_required": False,
                    "capabilities": ["file_write"],
                },
            },
        )
    )

    rendered = output.getvalue()
    assert "Apply exact edits to workspace files" in rendered
    assert "2 edit(s) across 2 file(s)" in rendered
    assert "src/example.py" in rendered
    assert "tests/test_example.py" in rendered
    assert "old behavior" in rendered
    assert "new behavior" in rendered
    assert "x" * 150 not in rendered
    assert "y" * 150 not in rendered


def test_event_renderer_replace_approval_focuses_the_actual_difference() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=100)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    shared = "This is a long shared sentence that makes a full preview hard to compare "
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "approval.requested",
            utc_now(),
            {
                "tool_execution_id": "tool-exec-focused-diff",
                "tool_name": "replace_text",
                "arguments": {
                    "path": "README.md",
                    "old_text": shared + "with an English period.",
                    "new_text": shared + "with a Chinese period。",
                    "expected_replacements": 1,
                },
            },
        )
    )

    rendered = output.getvalue()
    assert "README.md:" in rendered
    assert "- " in rendered
    assert "+ " in rendered
    assert "English period." in rendered
    assert "Chinese period。" in rendered
    assert rendered.count(shared) == 0


def test_event_renderer_marks_later_success_as_recovered_tool_error() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "tool.failed",
            utc_now(),
            {
                "tool_name": "read_artifact",
                "error": "max_chars must be between 256 and 4000",
            },
        )
    )
    renderer.render(
        RuntimeEvent(
            "e2",
            "run",
            2,
            "tool.completed",
            utc_now(),
            {"tool_name": "read_artifact", "content": "Artifact page loaded"},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e3",
            "run",
            3,
            "completion.evidence",
            utc_now(),
            {
                "status": "read_only",
                "changed_files": [],
                "diff_required": False,
                "diff_inspected": False,
                "validation_required": False,
                "validations": [],
                "failed_tools": ["read_artifact"],
                "rejected_tools": [],
                "unmet_requirements": [],
            },
        )
    )

    rendered = output.getvalue()
    assert "Recovered tool error: read_artifact" in rendered
    assert "Task incomplete" not in rendered
    assert "Failed tool: read_artifact" not in rendered


def test_event_renderer_keeps_latest_same_tool_failure_unresolved() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    for sequence, event_type in enumerate(
        ("tool.failed", "tool.completed", "tool.failed"),
        start=1,
    ):
        renderer.render(
            RuntimeEvent(
                f"e{sequence}",
                "run",
                sequence,
                event_type,
                utc_now(),
                {
                    "tool_name": "read_artifact",
                    "error": "artifact page is unavailable",
                    "content": "Artifact page loaded",
                },
            )
        )
    renderer.render(
        RuntimeEvent(
            "e4",
            "run",
            4,
            "completion.evidence",
            utc_now(),
            {
                "status": "read_only",
                "changed_files": [],
                "diff_required": False,
                "diff_inspected": False,
                "validation_required": False,
                "validations": [],
                "failed_tools": ["read_artifact"],
                "rejected_tools": [],
                "unmet_requirements": [],
            },
        )
    )

    rendered = output.getvalue()
    assert "Task incomplete" in rendered
    assert "Failed tool: read_artifact" in rendered
    assert "Recovered tool error: read_artifact" not in rendered


def test_event_renderer_reports_failed_read_only_turn_as_incomplete() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "completion.evidence",
            utc_now(),
            {
                "status": "read_only",
                "changed_files": [],
                "diff_required": False,
                "diff_inspected": False,
                "validation_required": False,
                "validations": [],
                "failed_tools": ["read_artifact"],
                "rejected_tools": [],
                "unmet_requirements": [],
            },
        )
    )

    rendered = output.getvalue()
    assert "Task incomplete" in rendered
    assert "Status: incomplete" in rendered
    assert "No changes applied" in rendered
    assert "Failed tool: read_artifact" in rendered
    assert "Git diff not required" not in rendered
    assert "Validation:" not in rendered


def test_event_renderer_task_summary_reports_unverified_work() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    renderer = EventRenderer(console)
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e1",
            "run",
            1,
            "completion.evidence",
            utc_now(),
            {
                "status": "unverified",
                "changed_files": ["src/example.py", "tests/test_example.py"],
                "diff_required": True,
                "diff_inspected": False,
                "validation_required": True,
                "validations": [
                    {
                        "command": "python -m pytest tests/test_example.py -q",
                        "exit_code": 1,
                        "succeeded": False,
                    }
                ],
                "failed_tools": ["run_process"],
                "rejected_tools": [],
                "unmet_requirements": ["post-change validation did not succeed"],
            },
        )
    )

    rendered = output.getvalue()
    assert "Task summary" in rendered
    assert "Status: unverified" in rendered
    assert "src/example.py" in rendered
    assert "Git diff not inspected" in rendered
    assert "python -m pytest tests/test_example.py -q (exit 1)" in rendered
    assert "Unmet: post-change validation did not succeed" in rendered
    assert "Failed tool: run_process" in rendered


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            DisplayMode.COMPACT,
            "Inspection budget reached; answering from collected evidence",
        ),
        (
            DisplayMode.VERBOSE,
            "inspection_calls=14 · consecutive_no_progress=3 · reason=no_progress",
        ),
    ],
)
def test_event_renderer_shows_convergence_finalization(
    mode: DisplayMode, expected: str
) -> None:
    output = StringIO()
    renderer = EventRenderer(
        Console(file=output, force_terminal=False, color_system=None, width=120),
        mode=mode,
    )
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e-convergence",
            "run",
            1,
            "convergence.finalization_requested",
            utc_now(),
            {
                "inspection_calls": 14,
                "consecutive_no_progress": 3,
                "reason": "no_progress",
            },
        )
    )

    assert expected in output.getvalue()

def test_event_renderer_shows_finalization_protocol_repair() -> None:
    output = StringIO()
    renderer = EventRenderer(
        Console(file=output, force_terminal=False, color_system=None, width=120),
        mode=DisplayMode.COMPACT,
    )
    renderer.begin_turn()
    renderer.render(
        RuntimeEvent(
            "e-repair",
            "run",
            1,
            "convergence.finalization_repair_requested",
            utc_now(),
            {"step": 2, "format": "dsml", "repair_attempt": 1},
        )
    )

    assert "Model returned tool syntax as text; retrying a plain-language answer" in (
        output.getvalue()
    )

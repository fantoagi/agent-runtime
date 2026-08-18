from __future__ import annotations

from collections.abc import Iterable
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from agent_runtime.domain import AgentDefinition, Message, ModelConfig, RunStatus, ToolCall
from agent_runtime.interactive import (
    ChatOptions,
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
            ["/status", "/tools", "/workspace", "/new", "/sessions", "/quit"]
        ),
    )
    try:
        exit_code = await shell.run()
        session_count = runtime.store.count_sessions()
    finally:
        await runtime.shutdown()

    assert exit_code == 0
    rendered = output.getvalue()
    assert "Interactive Runtime Status" in rendered
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
        options=ChatOptions(initial_prompt="write the file", print_only=True),
        console=Console(file=output, force_terminal=False, color_system=None, width=120),
        prompt_reader=ScriptedPromptReader(["y"]),
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

    from agent_runtime.domain import RuntimeEvent, utc_now

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
            "completion.verification_requested",
            utc_now(),
            {"unmet_requirements": ["post-change Git diff was not inspected"]},
        )
    )
    renderer.render(
        RuntimeEvent(
            "e5",
            "run",
            5,
            "completion.evidence",
            utc_now(),
            {
                "status": "verified",
                "changed_files": ["example.py"],
                "diff_inspected": True,
                "validation_succeeded": True,
            },
        )
    )

    rendered = output.getvalue()
    assert "context.built" not in rendered
    assert "Assistant > hello" in rendered
    assert "read_text_file" in rendered
    assert "README.md" in rendered
    assert "verifying changes before completion" in rendered
    assert "Completion evidence: verified" in rendered
    assert "validation passed" in rendered

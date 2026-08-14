from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_runtime.domain import (
    AgentDefinition,
    AgentRun,
    Checkpoint,
    Message,
    ModelConfig,
    RunStatus,
    Step,
    StepStatus,
    ToolCall,
    ToolDefinition,
    ToolExecution,
    ToolExecutionStatus,
    utc_now,
)
from agent_runtime.providers import (
    ModelResponse,
    ModelTokenDelta,
    MockProvider,
    MockStreamingProvider,
    ToolCallDelta,
)
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.storage import SQLiteStore
from agent_runtime.tools import ToolRegistry


def make_agent(*, approval: bool = False, two_tools: bool = False) -> AgentDefinition:
    tools = [
        ToolDefinition(
            name="echo",
            description="echo input",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            requires_approval=approval,
            side_effecting=approval,
        )
    ]
    if two_tools:
        tools.append(
            ToolDefinition(
                name="echo2",
                description="echo second input",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                requires_approval=approval,
                side_effecting=approval,
            )
        )
    return AgentDefinition(
        name="test-agent",
        system_prompt="test",
        tools=tools,
        model=ModelConfig(provider="mock", model="test"),
    )


def make_runtime(
    workspace: Path,
    responder,
    *,
    approval: bool = False,
    two_tools: bool = False,
    handler=None,
) -> Runtime:
    agent = make_agent(approval=approval, two_tools=two_tools)
    tools = ToolRegistry()
    tools.register(
        agent.tools[0],
        handler or (lambda arguments, context: f"echo:{arguments['value']}"),
    )
    if two_tools:
        tools.register(
            agent.tools[1],
            lambda arguments, context: f"echo2:{arguments['value']}",
        )
    return Runtime(
        RuntimeConfig(workspace_path=workspace, database_path=workspace / "runtime.sqlite3"),
        provider=MockProvider(responder),
        tools=tools,
    )


@pytest.mark.asyncio
async def test_runtime_completes_tool_loop_and_persists_events(workspace: Path) -> None:
    def responder(messages, tools, config):
        if messages[-1].role == "tool":
            return ModelResponse(content=f"done {messages[-1].content}")
        return ModelResponse(tool_calls=[ToolCall("call_1", "echo", {"value": "hello"})])

    runtime = make_runtime(workspace, responder)
    run = await runtime.run(make_agent(), "say hello")

    assert run.status == "completed"
    assert run.result == "done echo:hello"
    events = runtime.store.events_since(run.id)
    assert [event.type for event in events] == [
        "run.created",
        "run.started",
        "checkpoint.created",
        "model.requested",
        "model.completed",
        "checkpoint.created",
        "tool.requested",
        "tool.started",
        "tool.completed",
        "checkpoint.created",
        "step.completed",
        "checkpoint.created",
        "model.requested",
        "model.completed",
        "model.delta",
        "step.completed",
        "checkpoint.created",
        "run.completed",
    ]
    assert runtime.store.latest_checkpoint(run.id) is not None
    steps = runtime.store.latest_incomplete_step(run.id)
    assert steps is None


@pytest.mark.asyncio
async def test_approved_tool_resumes_from_checkpoint(workspace: Path) -> None:
    def responder(messages, tools, config):
        if messages[-1].role == "tool":
            return ModelResponse(content=f"approved result: {messages[-1].content}")
        return ModelResponse(tool_calls=[ToolCall("call_approval", "echo", {"value": "hello"})])

    runtime = make_runtime(workspace, responder, approval=True)
    agent = make_agent(approval=True)
    waiting = await runtime.run(agent, "say hello")
    assert waiting.status == "waiting_for_approval"
    approval = runtime.store.pending_approval(waiting.id)
    assert approval is not None
    execution = runtime.store.get_tool_execution_by_call(waiting.id, "call_approval")
    assert execution is not None
    assert execution.status is ToolExecutionStatus.WAITING_FOR_APPROVAL

    runtime.resolve_approval(approval.id, approved=True)
    completed = await runtime.resume(waiting.id)
    assert completed.status == "completed"
    assert completed.result == "approved result: echo:hello"
    assert runtime.store.get_tool_execution(execution.id).status is ToolExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_rejected_tool_is_reported_to_model(workspace: Path) -> None:
    def responder(messages, tools, config):
        if messages[-1].role == "tool":
            return ModelResponse(content=messages[-1].content)
        return ModelResponse(tool_calls=[ToolCall("call_rejection", "echo", {"value": "hello"})])

    runtime = make_runtime(workspace, responder, approval=True)
    waiting = await runtime.run(make_agent(approval=True), "say hello")
    approval = runtime.store.pending_approval(waiting.id)
    assert approval is not None
    runtime.resolve_approval(approval.id, approved=False, reason="not safe")

    completed = await runtime.resume(waiting.id)
    assert completed.status == "completed"
    assert "rejected by a human: not safe" in completed.result
    execution = runtime.store.get_tool_execution_by_call(waiting.id, "call_rejection")
    assert execution is not None
    assert execution.status is ToolExecutionStatus.REJECTED


@pytest.mark.asyncio
async def test_multiple_tool_calls_resume_after_approval(workspace: Path) -> None:
    calls = 0

    def responder(messages, tools, config):
        nonlocal calls
        calls += 1
        if messages[-1].role == "tool":
            return ModelResponse(content=" | ".join(message.content or "" for message in messages if message.role == "tool"))
        return ModelResponse(
            tool_calls=[
                ToolCall("call_1", "echo", {"value": "one"}),
                ToolCall("call_2", "echo2", {"value": "two"}),
            ]
        )

    runtime = make_runtime(workspace, responder, approval=True, two_tools=True)
    waiting = await runtime.run(make_agent(approval=True, two_tools=True), "two")
    assert waiting.status == "waiting_for_approval"
    first = runtime.store.pending_approval(waiting.id)
    assert first is not None
    runtime.resolve_approval(first.id, approved=True)

    waiting_again = await runtime.resume(waiting.id)
    assert waiting_again.status == "waiting_for_approval"
    second = runtime.store.pending_approval(waiting.id)
    assert second is not None and second.id != first.id
    runtime.resolve_approval(second.id, approved=True)

    completed = await runtime.resume(waiting.id)
    assert completed.status == "completed"
    assert "echo:one" in completed.result
    assert "echo2:two" in completed.result
    assert calls == 2


@pytest.mark.asyncio
async def test_completed_tool_execution_is_not_reexecuted_on_resume(workspace: Path) -> None:
    count = 0

    def handler(arguments, context):
        nonlocal count
        count += 1
        return "once"

    def responder(messages, tools, config):
        if messages[-1].role == "tool":
            return ModelResponse(content="done")
        return ModelResponse(tool_calls=[ToolCall("call_once", "echo", {"value": "x"})])

    agent = make_agent()
    runtime = make_runtime(workspace, responder, handler=handler)
    run = runtime.create_run(agent, "once")
    run.transition_to(RunStatus.RUNNING)
    run.step_count = 1
    run.tool_call_count = 1
    runtime.store.save_run(run)
    messages = [
        Message(role="system", content="test"),
        Message(role="user", content="once"),
        Message(
            role="assistant", tool_calls=[ToolCall("call_once", "echo", {"value": "x"})]
        ),
        Message(
            role="tool", name="echo", tool_call_id="call_once", content="once"
        ),
    ]
    step = Step.create(run.id, 1)
    step.status = StepStatus.WAITING_FOR_TOOLS
    step.assistant_message = messages[2]
    runtime.store.create_step_with_event(run, step, "model.requested", {"step": 1})
    execution = ToolExecution.create(
        run.id, step.id, 0, ToolCall("call_once", "echo", {"value": "x"}),
        requires_approval=False, side_effecting=False,
    )
    execution.status = ToolExecutionStatus.COMPLETED
    execution.result_content = "once"
    execution.completed_at = utc_now()
    runtime.store.create_tool_executions(step, [execution])
    runtime.store.save_checkpoint(Checkpoint.create(
        run.id, 1, messages, 1
    ))
    runtime.store.close()

    restarted = make_runtime(workspace, responder, handler=handler)
    restarted.register_agent(agent)
    recovered = await restarted.resume(run.id)
    assert recovered.status == "completed", restarted.store.get_run(run.id).error
    assert count == 0


@pytest.mark.asyncio
async def test_side_effecting_running_tool_becomes_unknown_and_pauses(workspace: Path) -> None:
    def responder(messages, tools, config):
        return ModelResponse(tool_calls=[ToolCall("call_side", "echo", {"value": "x"})])

    runtime = make_runtime(workspace, responder, approval=False)
    run = runtime.create_run(make_agent(), "unknown")
    run.status = RunStatus.RUNNING
    run.step_count = 1
    runtime.store.save_run(run)
    step = Step.create(run.id, 1)
    runtime.store.create_step_with_event(run, step, "model.requested", {"step": 1})
    execution = ToolExecution.create(
        run.id, step.id, 0, ToolCall("call_side", "echo", {"value": "x"}),
        requires_approval=False, side_effecting=True,
    )
    execution.status = ToolExecutionStatus.RUNNING
    runtime.store.create_tool_executions(step, [execution])
    runtime.store.close()

    restarted = make_runtime(workspace, responder, approval=False)
    restarted.register_agent(make_agent())
    recovered = await restarted.resume(run.id)
    assert recovered.status == "paused"
    unknown = restarted.store.get_tool_execution(execution.id)
    assert unknown.status is ToolExecutionStatus.UNKNOWN


@pytest.mark.asyncio
async def test_cancel_interrupts_active_async_tool(workspace: Path) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_handler(arguments, context):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def responder(messages, tools, config):
        return ModelResponse(tool_calls=[ToolCall("call_slow", "echo", {"value": "x"})])

    runtime = make_runtime(workspace, responder, handler=slow_handler)
    run = runtime.start(make_agent(), "slow")
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelled_run = runtime.cancel(run.id)
    assert cancelled_run.status == "cancelled"
    completed = await runtime.wait(run.id)
    assert completed.status == "cancelled"
    assert cancelled.is_set()
    execution = runtime.store.get_tool_execution_by_call(run.id, "call_slow")
    assert execution is not None
    assert execution.status is ToolExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_unknown_tool_can_be_confirmed_and_resumed(workspace: Path) -> None:
    def responder(messages, tools, config):
        if messages[-1].role == "tool":
            return ModelResponse(content=f"confirmed: {messages[-1].content}")
        return ModelResponse(tool_calls=[ToolCall("call_side", "echo", {"value": "x"})])

    runtime = make_runtime(workspace, responder)
    run = runtime.create_run(make_agent(), "unknown")
    run.transition_to(RunStatus.RUNNING)
    run.step_count = 1
    runtime.store.save_run(run)
    step = Step.create(run.id, 1)
    step.status = StepStatus.WAITING_FOR_TOOLS
    step.assistant_message = Message(
        role="assistant", tool_calls=[ToolCall("call_side", "echo", {"value": "x"})]
    )
    runtime.store.create_step_with_event(run, step, "model.requested", {"step": 1})
    execution = ToolExecution.create(
        run.id, step.id, 0, ToolCall("call_side", "echo", {"value": "x"}),
        requires_approval=False, side_effecting=True,
    )
    execution.status = ToolExecutionStatus.UNKNOWN
    execution.error = "uncertain"
    runtime.store.create_tool_executions(step, [execution])
    runtime.store.save_checkpoint(Checkpoint.create(
        run.id,
        1,
        [
            Message(role="system", content="test"),
            Message(role="user", content="unknown"),
            step.assistant_message,
        ],
        0,
    ))
    run.transition_to(RunStatus.PAUSED)
    runtime.store.save_run(run)

    runtime.resolve_unknown_tool(execution.id, "completed", result_content="already-written")
    completed = await runtime.resume(run.id)
    assert completed.status == "completed"
    assert completed.result == "confirmed: already-written"


def test_schema_migrates_existing_v01_database(workspace: Path) -> None:
    import sqlite3

    database = workspace / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            input TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            result TEXT,
            error TEXT,
            step_count INTEGER NOT NULL,
            tool_call_count INTEGER NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            sequence INTEGER NOT NULL,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, sequence)
        );
        CREATE TABLE checkpoints (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            step INTEGER NOT NULL,
            messages_json TEXT NOT NULL,
            tool_call_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE approvals (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            tool_call_json TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        """
    )
    connection.commit()
    connection.close()

    store = SQLiteStore(database)
    assert store.schema_version == 3
    columns = {
        row["name"]
        for row in store._connection.execute("PRAGMA table_info(approvals)").fetchall()
    }
    assert {"tool_execution_id", "kind"} <= columns
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"schema_migrations", "steps", "tool_executions", "run_relations"} <= tables
    store.close()


def test_atomic_run_event_write_rolls_back(workspace: Path) -> None:
    store = SQLiteStore(workspace / "atomic.sqlite3")
    run = AgentRun.create("agent", "input")
    store.create_run(run)
    run.status = RunStatus.RUNNING
    with pytest.raises(RuntimeError):
        store.save_run_with_event(
            run,
            "impossible",
            before_commit=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    assert store.get_run(run.id).status.value == "created"
    assert store.events_since(run.id) == []
    store.close()


@pytest.mark.asyncio
async def test_runtime_persists_each_stream_delta_and_final_message(workspace: Path) -> None:
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            event_poll_interval_seconds=0.01,
        ),
        provider=MockStreamingProvider(
            [
                ModelTokenDelta(content="stream "),
                ModelTokenDelta(content="answer", finish_reason="stop"),
            ]
        ),
        tools=ToolRegistry(),
    )
    agent = AgentDefinition(
        name="stream-agent",
        system_prompt="stream",
        tools=[],
        model=ModelConfig(provider="mock", model="stream-test"),
    )

    run = await runtime.run(agent, "hello")

    assert run.status is RunStatus.COMPLETED
    assert run.result == "stream answer"
    events = runtime.store.events_since(run.id)
    assert [event.type for event in events].count("model.delta") == 2
    assert [event.type for event in events].count("model.stream.started") == 1
    assert [event.type for event in events].count("model.stream.completed") == 1
    delta_contents = [event.payload["content"] for event in events if event.type == "model.delta"]
    assert delta_contents == ["stream ", "answer"]
    checkpoint = runtime.store.latest_checkpoint(run.id)
    assert checkpoint is not None
    assert checkpoint.messages[-1].content == "stream answer"


@pytest.mark.asyncio
async def test_runtime_reassembles_streamed_tool_call_before_execution(workspace: Path) -> None:
    class ToolStreamingProvider:
        async def stream(self, messages, tools, config):
            del tools, config
            if messages[-1].role == "tool":
                yield ModelTokenDelta(content=f"done {messages[-1].content}", finish_reason="stop")
                return
            yield ModelTokenDelta(
                tool_call_deltas=[
                    ToolCallDelta(
                        index=0,
                        id="stream_call",
                        name="echo",
                        arguments='{"value":"',
                    )
                ]
            )
            yield ModelTokenDelta(
                tool_call_deltas=[ToolCallDelta(index=0, arguments='hello"}')],
                finish_reason="tool_calls",
            )

    agent = make_agent()
    tools = ToolRegistry()
    tools.register(agent.tools[0], lambda arguments, context: f"echo:{arguments['value']}")
    runtime = Runtime(
        RuntimeConfig(workspace_path=workspace, database_path=workspace / "runtime.sqlite3"),
        provider=ToolStreamingProvider(),
        tools=tools,
    )

    run = await runtime.run(agent, "say hello")

    assert run.status is RunStatus.COMPLETED
    assert run.result == "done echo:hello"
    execution = runtime.store.get_tool_execution_by_call(run.id, "stream_call")
    assert execution is not None
    assert execution.tool_call.arguments == {"value": "hello"}

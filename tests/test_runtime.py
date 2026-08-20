from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_runtime.context import RUNTIME_FINALIZATION_REQUEST_MESSAGE_NAME
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
    MockProvider,
    MockStreamingProvider,
    ModelResponse,
    ModelTokenDelta,
    ToolCallDelta,
)
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.storage import SQLiteStore
from agent_runtime.tools import ToolRegistry, ToolResult


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
        "context.built",
        "model.completed",
        "checkpoint.created",
        "tool.policy.evaluated",
        "tool.requested",
        "tool.started",
        "tool.completed",
        "checkpoint.created",
        "step.completed",
        "checkpoint.created",
        "model.requested",
        "context.built",
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
async def test_runtime_reuses_identical_read_only_tool_result(workspace: Path) -> None:
    calls = 0

    def handler(arguments, context):
        nonlocal calls
        del arguments, context
        calls += 1
        return "Found Runtime in src/agent_runtime/runtime.py"

    def responder(messages, definitions, config):
        del definitions, config
        completed = sum(message.role == "tool" for message in messages)
        if completed == 0:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "search-1",
                        "search_text",
                        {"query": "Runtime", "path": "src"},
                    )
                ]
            )
        if completed == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "search-2",
                        "search_text",
                        {"query": "Runtime", "path": "src"},
                    )
                ]
            )
        assert "Runtime convergence note" in messages[-1].content
        return ModelResponse(content="done")

    definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(definition, handler)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace, database_path=workspace / "runtime.sqlite3"
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="converging-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
    )
    try:
        completed = await runtime.run(agent, "find Runtime")
        events = runtime.store.events_since(completed.id)
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    assert completed.tool_call_count == 2
    assert calls == 1
    assert sum(event.type == "tool.requested" for event in events) == 1
    reused = [event for event in events if event.type == "tool.reused"]
    assert len(reused) == 1
    assert reused[0].payload["tool_name"] == "search_text"


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

    resolved = runtime.resolve_unknown_tool(
        execution.id,
        "confirmed_succeeded",
        result_content="already-written",
        reason="Verified the external file exists.",
        resolved_by="operator:test",
    )
    assert runtime.store.get_run(run.id).status is RunStatus.PAUSED
    assert resolved.resolution_reason == "Verified the external file exists."
    assert resolved.resolved_by == "operator:test"
    assert runtime.store.events_since(run.id)[-1].type == "tool.outcome_confirmed"
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
    assert store.schema_version == 8
    columns = {
        row["name"]
        for row in store._connection.execute("PRAGMA table_info(approvals)").fetchall()
    }
    assert {"tool_execution_id", "kind"} <= columns
    tool_columns = {
        row["name"]
        for row in store._connection.execute("PRAGMA table_info(tool_executions)").fetchall()
    }
    assert {"resolution", "resolution_reason", "resolved_by", "resolved_at"} <= tool_columns
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


def test_unknown_side_effect_cannot_be_retried(workspace: Path) -> None:
    def responder(messages, tools, config):
        return ModelResponse(content="unused")

    runtime = make_runtime(workspace, responder)
    run = runtime.create_run(make_agent(), "unknown")
    run.transition_to(RunStatus.RUNNING)
    runtime.store.save_run(run)
    step = Step.create(run.id, 1)
    runtime.store.create_step_with_event(run, step, "model.requested", {"step": 1})
    execution = ToolExecution.create(
        run.id, step.id, 0, ToolCall("call_retry", "echo", {"value": "x"}),
        requires_approval=False, side_effecting=True,
    )
    execution.status = ToolExecutionStatus.UNKNOWN
    runtime.store.create_tool_executions(step, [execution])

    with pytest.raises(ValueError, match="cannot be retried automatically"):
        runtime.resolve_unknown_tool(execution.id, "retry", reason="try again")
    assert runtime.store.get_tool_execution(execution.id).status is ToolExecutionStatus.UNKNOWN


@pytest.mark.asyncio
async def test_runtime_finalizes_when_varied_searches_add_no_new_evidence(
    workspace: Path,
) -> None:
    handler_calls = 0
    model_calls = 0

    def handler(arguments, context):
        nonlocal handler_calls
        del arguments, context
        handler_calls += 1
        return ToolResult(
            content="src/agent_runtime/runtime.py:143: class Runtime",
            data={
                "matches": [
                    {
                        "path": "src/agent_runtime/runtime.py",
                        "line": 143,
                        "content": "class Runtime:",
                    }
                ]
            },
        )

    def responder(messages, definitions, config):
        nonlocal model_calls
        del config
        model_calls += 1
        if model_calls <= 3:
            assert definitions
            query_index = model_calls - 1
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        f"search-{model_calls}",
                        "search_text",
                        {
                            "query": ["Runtime", "class Runtime", "Runtime kernel"][
                                query_index
                            ],
                            "path": "src",
                        },
                    )
                ]
            )
        assert definitions == []
        assert all(message.role != "tool" for message in messages)
        assert all(not message.tool_calls for message in messages)
        assert any(
            message.role == "system"
            and message.content
            and "fresh finalization context" in message.content
            for message in messages
        )
        assert not any(
            message.role == "system" and message.content == "test"
            for message in messages
        )
        evidence = next(
            message.content or ""
            for message in messages
            if message.name == "runtime-finalization-evidence"
        )
        assert "src/agent_runtime/runtime.py:143: class Runtime" in evidence
        assert "Tool: search_text" in evidence
        assert messages[-1].role == "user"
        assert messages[-1].name == RUNTIME_FINALIZATION_REQUEST_MESSAGE_NAME
        assert messages[-1].content == "find Runtime"
        return ModelResponse(content="Runtime is implemented in runtime.py.")

    definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(definition, handler)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=2,
            convergence_finalization_inspection_calls=3,
            convergence_no_progress_calls=1,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="evidence-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
        max_steps=6,
    )
    try:
        completed = await runtime.run(agent, "find Runtime")
        events = runtime.store.events_since(completed.id)
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    assert completed.result == "Runtime is implemented in runtime.py."
    assert completed.step_count == 4
    assert handler_calls == 3
    assert model_calls == 4
    assert not any(event.type == "tool.reused" for event in events)
    warnings = [event for event in events if event.type == "convergence.warning"]
    finalizations = [
        event for event in events if event.type == "convergence.finalization_requested"
    ]
    contexts = [
        event
        for event in events
        if event.type == "convergence.finalization_context_built"
    ]
    assert len(warnings) == 1
    assert warnings[0].payload["reason"] == "no_progress"
    assert len(finalizations) == 1
    assert finalizations[0].payload == {
        "inspection_calls": 3,
        "consecutive_no_progress": 2,
        "reason": "no_progress",
    }
    assert len(contexts) == 1
    assert contexts[0].payload["fresh_context"] is True
    assert contexts[0].payload["source_tool_executions"] == 3
    assert contexts[0].payload["included_evidence"] == 3
    assert contexts[0].payload["deduplicated_evidence"] == 0
    assert contexts[0].payload["omitted_evidence"] == 0
    assert contexts[0].payload["evidence_characters"] > 0
    assert contexts[0].payload["step"] == 4


@pytest.mark.asyncio
async def test_fresh_finalization_context_bounds_and_deduplicates_evidence(
    workspace: Path,
) -> None:
    runtime = make_runtime(workspace, lambda messages, tools, config: ModelResponse())
    run = runtime.create_run(make_agent(), "summarize evidence")
    run.transition_to(RunStatus.RUNNING)
    runtime.store.save_run(run)
    step = Step.create(run.id, 1)
    runtime.store.create_step_with_event(run, step, "model.requested", {"step": 1})
    executions: list[ToolExecution] = []
    for position in range(3):
        execution = ToolExecution.create(
            run.id,
            step.id,
            position,
            ToolCall(f"call-{position}", "echo", {"value": "same"}),
            requires_approval=False,
            side_effecting=False,
        )
        execution.status = ToolExecutionStatus.COMPLETED
        execution.result_content = "fact " + ("x" * 4_000)
        executions.append(execution)
    runtime.store.create_tool_executions(step, executions)

    try:
        context, payload = runtime._build_finalization_context(
            run,
            [
                Message(role="system", content="always call tools"),
                Message(role="user", content="earlier question"),
                Message(role="assistant", content="earlier answer"),
                Message(
                    role="user",
                    content=run.input,
                    name="runtime-current-request",
                ),
                Message(
                    role="assistant",
                    tool_calls=[ToolCall("hidden", "echo", {"value": "same"})],
                ),
                Message(role="tool", content="hidden result", tool_call_id="hidden"),
            ],
        )
    finally:
        await runtime.shutdown()

    assert all(message.role != "tool" for message in context)
    assert all(not message.tool_calls for message in context)
    assert not any(message.content == "always call tools" for message in context)
    assert context[-1].content == "summarize evidence"
    evidence = next(
        message.content or ""
        for message in context
        if message.name == "runtime-finalization-evidence"
    )
    assert "earlier question" in evidence
    assert "earlier answer" in evidence
    assert "hidden result" not in evidence
    assert len(evidence) < 13_000
    assert payload["fresh_context"] is True
    assert payload["source_tool_executions"] == 3
    assert payload["included_evidence"] == 1
    assert payload["deduplicated_evidence"] == 2
    assert payload["omitted_evidence"] == 0


@pytest.mark.asyncio
async def test_runtime_treats_overlapping_file_ranges_as_no_progress(
    workspace: Path,
) -> None:
    def handler(arguments, context):
        del context
        start = int(arguments["start_line"])
        end = 10 if start == 1 else 8
        return ToolResult(
            content=f"runtime.py lines {start}-{end}",
            data={
                "path": "src/agent_runtime/runtime.py",
                "start_line": start,
                "end_line": end,
                "sha256": "a" * 64,
            },
        )

    def responder(messages, definitions, config):
        del definitions, config
        completed = sum(message.role == "tool" for message in messages)
        if completed == 0:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "read-1",
                        "read_file_lines",
                        {"path": "src/agent_runtime/runtime.py", "start_line": 1},
                    )
                ]
            )
        if completed == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "read-2",
                        "read_file_lines",
                        {"path": "src/agent_runtime/runtime.py", "start_line": 5},
                    )
                ]
            )
        return ModelResponse(content="done")

    definition = ToolDefinition(
        name="read_file_lines",
        description="read",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(definition, handler)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=10,
            convergence_finalization_inspection_calls=14,
            convergence_no_progress_calls=1,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="range-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
    )
    try:
        completed = await runtime.run(agent, "read Runtime")
        events = runtime.store.events_since(completed.id)
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    warnings = [event for event in events if event.type == "convergence.warning"]
    assert len(warnings) == 1
    assert warnings[0].payload["consecutive_no_progress"] == 1


@pytest.mark.asyncio
async def test_side_effecting_tool_resets_evidence_and_blocks_auto_finalization(
    workspace: Path,
) -> None:
    search_calls = 0

    def search_handler(arguments, context):
        nonlocal search_calls
        del arguments, context
        search_calls += 1
        return ToolResult(
            content="same evidence",
            data={
                "matches": [
                    {"path": "runtime.py", "line": 1, "content": "Runtime"}
                ]
            },
        )

    def responder(messages, definitions, config):
        del config
        completed = sum(message.role == "tool" for message in messages)
        if completed == 0:
            return ModelResponse(
                tool_calls=[ToolCall("search-1", "search_text", {"query": "Runtime"})]
            )
        if completed == 1:
            return ModelResponse(tool_calls=[ToolCall("write-1", "mutate", {})])
        if completed == 2:
            return ModelResponse(
                tool_calls=[ToolCall("search-2", "search_text", {"query": "Runtime"})]
            )
        assert definitions
        return ModelResponse(content="verified after change")

    search_definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    mutate_definition = ToolDefinition(
        name="mutate",
        description="change workspace",
        input_schema={"type": "object", "additionalProperties": False},
        side_effecting=True,
    )
    tools = ToolRegistry()
    tools.register(search_definition, search_handler)
    tools.register(mutate_definition, lambda arguments, context: "changed")
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=1,
            convergence_finalization_inspection_calls=2,
            convergence_no_progress_calls=1,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="editing-agent",
        system_prompt="test",
        tools=[search_definition, mutate_definition],
        model=ModelConfig(provider="mock", model="test"),
    )
    try:
        completed = await runtime.run(agent, "change and verify")
        events = runtime.store.events_since(completed.id)
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    assert search_calls == 2
    assert not any(event.type == "convergence.finalization_requested" for event in events)


@pytest.mark.asyncio
async def test_disabled_tools_cannot_be_executed_during_finalization(
    workspace: Path,
) -> None:
    handler_calls = 0

    def handler(arguments, context):
        nonlocal handler_calls
        del arguments, context
        handler_calls += 1
        return ToolResult(
            content="same",
            data={"matches": [{"path": "runtime.py", "line": 1}]},
        )

    def responder(messages, definitions, config):
        del config
        completed = sum(message.role == "tool" for message in messages)
        if completed < 3:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        f"search-{completed}",
                        "search_text",
                        {"query": f"Runtime {completed}"},
                    )
                ]
            )
        assert definitions == []
        return ModelResponse(
            tool_calls=[ToolCall("forbidden", "search_text", {"query": "again"})]
        )

    definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(definition, handler)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=2,
            convergence_finalization_inspection_calls=3,
            convergence_no_progress_calls=1,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="guard-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
    )
    try:
        failed = await runtime.run(agent, "loop")
        events = runtime.store.events_since(failed.id)
    finally:
        await runtime.shutdown()

    assert failed.status is RunStatus.FAILED
    assert "disabled tools" in (failed.error or "")
    assert handler_calls == 3
    assert not any(
        event.type == "tool.requested"
        and event.payload.get("tool_call_id") == "forbidden"
        for event in events
    )

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("textual_call", "expected_format"),
    [
        (
            '<|DSML|tool_calls>\n<|DSML|invoke name="search_text">\n'
            '<|DSML|parameter name="query" string="true">Runtime</|DSML|parameter>',
            "dsml",
        ),
        (
            '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="search_text">\n'
            '<｜｜DSML｜｜parameter name="query" string="true">'
            'Runtime</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n'
            '</｜｜DSML｜｜tool_calls>',
            "dsml",
        ),
        (
            '< | | DSML | | tool_calls>\n< | | DSML | | invoke '
            'name="search_text">\n< | | DSML | | parameter name="query" '
            'string="true">Runtime</ | | DSML | | parameter>\n'
            '</ | | DSML | | invoke>\n</ | | DSML | | tool_calls>',
            "dsml",
        ),
        (
            '<tool_call><function=search_text>{"query":"Runtime"}'
            "</function></tool_call>",
            "xml",
        ),
        ('{"name":"search_text","arguments":{"query":"Runtime"}}', "json"),
        (
            '```json\n{"tool_calls":[{"type":"function","function":'
            '{"name":"search_text","arguments":"{\\"query\\":\\"Runtime\\"}"}}]}\n```',
            "json",
        ),
    ],
)
async def test_finalization_repairs_textual_tool_call_once(
    workspace: Path,
    textual_call: str,
    expected_format: str,
) -> None:
    model_calls = 0
    handler_calls = 0
    definitions_seen: list[list[ToolDefinition]] = []

    def handler(arguments, context):
        nonlocal handler_calls
        del arguments, context
        handler_calls += 1
        return ToolResult(
            content="runtime.py:1: Runtime",
            data={"matches": [{"path": "runtime.py", "line": 1}]},
        )

    def responder(messages, definitions, config):
        nonlocal model_calls
        del config
        model_calls += 1
        definitions_seen.append(list(definitions))
        if model_calls == 1:
            return ModelResponse(
                tool_calls=[ToolCall("search-1", "search_text", {"query": "Runtime"})]
            )
        assert definitions == []
        if model_calls == 2:
            return ModelResponse(content=textual_call)
        assert messages[-1].role == "user"
        assert messages[-1].name == RUNTIME_FINALIZATION_REQUEST_MESSAGE_NAME
        assert messages[-1].content == "explain Runtime"
        assert all(message.role != "tool" for message in messages)
        assert all(not message.tool_calls for message in messages)
        assert any(
            message.role == "system"
            and message.content
            and "fresh finalization repair" in message.content
            for message in messages
        )
        assert not any(
            message.role == "system" and message.content == "test"
            for message in messages
        )
        evidence = next(
            message.content or ""
            for message in messages
            if message.name == "runtime-finalization-evidence"
        )
        assert "runtime.py:1: Runtime" in evidence
        return ModelResponse(content="Runtime coordinates model and tool execution.")

    definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(definition, handler)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=1,
            convergence_finalization_inspection_calls=1,
            convergence_no_progress_calls=1,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="textual-guard-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
        max_steps=3,
    )
    try:
        completed = await runtime.run(agent, "explain Runtime")
        events = runtime.store.events_since(completed.id)
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    assert completed.result == "Runtime coordinates model and tool execution."
    assert model_calls == 3
    assert handler_calls == 1
    assert definitions_seen[0]
    assert definitions_seen[1:] == [[], []]
    detections = [
        event
        for event in events
        if event.type == "convergence.textual_tool_call_detected"
    ]
    repairs = [
        event
        for event in events
        if event.type == "convergence.finalization_repair_requested"
    ]
    assert len(detections) == 1
    assert detections[0].payload["format"] == expected_format
    assert detections[0].payload["repair_attempt"] == 1
    assert len(repairs) == 1
    assert not any(
        event.type == "tool.requested" and event.payload.get("tool_call_id") != "search-1"
        for event in events
    )


@pytest.mark.asyncio
async def test_finalization_fails_after_repeated_textual_tool_call(
    workspace: Path,
) -> None:
    model_calls = 0
    handler_calls = 0
    textual_call = (
        '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="search_text">\n'
        '<｜｜DSML｜｜parameter name="query" string="true">'
        'again</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n'
        '</｜｜DSML｜｜tool_calls>'
    )

    def handler(arguments, context):
        nonlocal handler_calls
        del arguments, context
        handler_calls += 1
        return ToolResult(
            content="same",
            data={"matches": [{"path": "runtime.py", "line": 1}]},
        )

    def responder(messages, definitions, config):
        nonlocal model_calls
        del messages, config
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(
                tool_calls=[ToolCall("search-1", "search_text", {"query": "Runtime"})]
            )
        assert definitions == []
        return ModelResponse(content=textual_call)

    definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(definition, handler)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=1,
            convergence_finalization_inspection_calls=1,
            convergence_no_progress_calls=1,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="repeated-textual-guard-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
        max_steps=3,
    )
    try:
        failed = await runtime.run(agent, "explain Runtime")
        events = runtime.store.events_since(failed.id)
    finally:
        await runtime.shutdown()

    assert failed.status is RunStatus.FAILED
    assert failed.result is None
    assert "repeatedly returned a textual Tool Call" in (failed.error or "")
    assert model_calls == 3
    assert handler_calls == 1
    assert [
        event.payload["repair_attempt"]
        for event in events
        if event.type == "convergence.textual_tool_call_detected"
    ] == [1, 2]
    assert sum(
        event.type == "convergence.finalization_repair_requested" for event in events
    ) == 1
    assert sum(event.type == "tool.requested" for event in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "The token `<tool_call>` is an example marker, not a request to execute a tool.",
        '```json\n{"status":"ok","example":"<tool_call>"}\n```',
        '{"name":"search_text","arguments":{},"explanation":"example only"}',
        (
            '<|DSML|tool_calls>\n<|DSML|invoke name="search_text">\n'
            "This is only an explanation of the syntax."
        ),
        (
            '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="search_text">\n'
            "This is only an explanation of the full-width syntax."
        ),
        (
            '<tool_call><function=search_text>{"query":"Runtime"}'
            "</function></tool_call> followed by an explanation."
        ),
    ],
)
async def test_finalization_does_not_repair_tool_syntax_explanations(
    workspace: Path,
    answer: str,
) -> None:
    model_calls = 0

    def responder(messages, definitions, config):
        nonlocal model_calls
        del messages, config
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(
                tool_calls=[ToolCall("search-1", "search_text", {"query": "Runtime"})]
            )
        assert definitions == []
        return ModelResponse(content=answer)

    definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(
        definition,
        lambda arguments, context: ToolResult(
            content="same",
            data={"matches": [{"path": "runtime.py", "line": 1}]},
        ),
    )
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=1,
            convergence_finalization_inspection_calls=1,
            convergence_no_progress_calls=1,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="syntax-explanation-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
        max_steps=3,
    )
    try:
        completed = await runtime.run(agent, "explain tool syntax")
        events = runtime.store.events_since(completed.id)
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    assert completed.result == answer
    assert model_calls == 2
    assert not any(
        event.type == "convergence.textual_tool_call_detected" for event in events
    )


@pytest.mark.asyncio
async def test_streamed_textual_tool_call_is_buffered_and_repaired(
    workspace: Path,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0
            self.definitions_seen: list[list[ToolDefinition]] = []

        async def stream(self, messages, definitions, config):
            del messages, config
            self.calls += 1
            self.definitions_seen.append(list(definitions))
            if self.calls == 1:
                yield ModelTokenDelta(
                    tool_call_deltas=[
                        ToolCallDelta(
                            index=0,
                            id="search-1",
                            name="search_text",
                            arguments='{"query":"Runtime"}',
                        )
                    ],
                    finish_reason="tool_calls",
                )
                return
            if self.calls == 2:
                yield ModelTokenDelta(content="<｜｜DSML｜｜tool_calls>\n")
                yield ModelTokenDelta(
                    content='<｜｜DSML｜｜invoke name="search_text">\n',
                )
                yield ModelTokenDelta(
                    content=(
                        '<｜｜DSML｜｜parameter name="query" string="true">'
                        'Runtime</｜｜DSML｜｜parameter>\n'
                        '</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>'
                    ),
                    finish_reason="stop",
                )
                return
            yield ModelTokenDelta(content="Runtime coordinates ")
            yield ModelTokenDelta(content="execution.", finish_reason="stop")

    definition = ToolDefinition(
        name="search_text",
        description="search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    tools = ToolRegistry()
    tools.register(
        definition,
        lambda arguments, context: ToolResult(
            content="same",
            data={"matches": [{"path": "runtime.py", "line": 1}]},
        ),
    )
    provider = Provider()
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            convergence_warning_inspection_calls=1,
            convergence_finalization_inspection_calls=1,
            convergence_no_progress_calls=1,
        ),
        provider=provider,
        tools=tools,
    )
    agent = AgentDefinition(
        name="streamed-textual-guard-agent",
        system_prompt="test",
        tools=[definition],
        model=ModelConfig(provider="mock", model="test"),
        max_steps=3,
    )
    try:
        completed = await runtime.run(agent, "explain Runtime")
        events = runtime.store.events_since(completed.id)
    finally:
        await runtime.shutdown()

    assert completed.status is RunStatus.COMPLETED
    assert completed.result == "Runtime coordinates execution."
    assert provider.calls == 3
    assert provider.definitions_seen[0]
    assert provider.definitions_seen[1:] == [[], []]
    published_content = "".join(
        str(event.payload.get("content") or "")
        for event in events
        if event.type == "model.delta"
    )
    assert published_content == "Runtime coordinates execution."
    assert "DSML" not in published_content
    assert sum(event.type == "model.stream.completed" for event in events) == 3

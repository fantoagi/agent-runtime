from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from agent_runtime.context import ContextBuilder
from agent_runtime.domain import (
    AgentDefinition,
    MemoryRecord,
    MemoryScope,
    Message,
    ModelConfig,
    RunStatus,
    ToolCall,
    ToolDefinition,
    utc_now,
)
from agent_runtime.evals import EvalCase, EvalSuite, MemoryEvalRunner
from agent_runtime.observability import ObservabilityService
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_agent(*, tools=None) -> AgentDefinition:
    return AgentDefinition(
        name="memory-agent",
        system_prompt="Always use relevant memory.",
        tools=tools or [],
        model=ModelConfig(provider="mock", model="memory-test"),
    )


def make_runtime(workspace: Path, responder, *, tools=None, **config) -> Runtime:
    registry = ToolRegistry()
    for definition, handler in tools or []:
        registry.register(definition, handler)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            **config,
        ),
        provider=MockProvider(responder),
        tools=registry,
    )
    runtime.register_agent(make_agent(tools=[definition for definition, _ in tools or []]))
    return runtime


def test_context_builder_compacts_without_splitting_unfinished_tool_calls() -> None:
    builder = ContextBuilder(token_budget=90, recent_groups=2, summary_max_chars=200)
    unfinished = Message(
        role="assistant",
        tool_calls=[ToolCall("pending", "lookup", {"query": "important"})],
    )
    messages = [
        Message(role="system", content="system prompt must stay"),
        Message(role="user", content="old " * 120),
        Message(role="assistant", content="old answer " * 80),
        unfinished,
        Message(role="user", content="recent question"),
        Message(role="assistant", content="recent answer"),
    ]

    result = builder.build(messages)

    assert result.compacted
    assert result.omitted_messages >= 1
    assert result.messages[0].role == "system"
    assert any(
        message.role == "assistant" and message.tool_calls[0].id == "pending"
        for message in result.messages
        if message.tool_calls
    )
    assert result.messages[-1].content == "recent answer"
    assert any(message.name == "context-summary" for message in result.messages)


@pytest.mark.asyncio
async def test_session_memory_is_scoped_searchable_and_injected(workspace: Path) -> None:
    captured: list[list[Message]] = []

    def responder(messages, tools, config):
        del tools, config
        captured.append(messages)
        memory = next(message for message in messages if message.name == "memory")
        return ModelResponse(content=f"used:{memory.content}")

    runtime = make_runtime(workspace, responder)
    first = runtime.create_session({"user": "one"})
    second = runtime.create_session({"user": "two"})
    record = runtime.remember(
        "The preferred language is Python.",
        scope=MemoryScope.SESSION,
        scope_id=first.id,
    )
    runtime.remember(
        "The preferred language is Rust.",
        scope=MemoryScope.SESSION,
        scope_id=second.id,
    )

    run = await runtime.run(
        "memory-agent",
        "What is the preferred Python language?",
        session_id=first.id,
    )

    assert run.status is RunStatus.COMPLETED
    assert record.id in run.result
    assert [item.id for item in runtime.session_runs(first.id)] == [run.id]
    assert captured and any(message.name == "memory" for message in captured[0])
    search = runtime.search_memory("Python", session_id=first.id)
    assert [item.record.id for item in search] == [record.id]
    isolated = runtime.search_memory("Python", session_id=second.id)
    assert isolated == []
    event_types = [event.type for event in runtime.store.events_since(run.id)]
    assert "memory.search.started" in event_types
    assert "memory.search.completed" in event_types
    assert "context.built" in event_types


def test_agent_memory_scope_is_shared_across_sessions_but_not_global(workspace: Path) -> None:
    runtime = make_runtime(
        workspace,
        lambda messages, tools, config: ModelResponse(content="ok"),
    )
    first = runtime.create_session()
    second = runtime.create_session()
    record = runtime.remember(
        "The agent must explain recovery before retrying.",
        scope="agent",
        scope_id="memory-agent",
    )

    assert runtime.search_memory("recovery", agent_name="memory-agent")[0].record.id == record.id
    assert runtime.search_memory("recovery", session_id=first.id) == []
    assert runtime.search_memory("recovery", session_id=second.id) == []

@pytest.mark.asyncio
async def test_memory_lifecycle_source_trace_metrics_and_eval(workspace: Path) -> None:
    runtime = make_runtime(
        workspace,
        lambda messages, tools, config: ModelResponse(content="source complete"),
    )
    session = runtime.create_session()
    source = await runtime.run("memory-agent", "source", session_id=session.id)
    active = runtime.remember(
        "Recovery uses durable checkpoints.",
        scope="session",
        scope_id=session.id,
        source_run_id=source.id,
    )
    expired = MemoryRecord.create(
        MemoryScope.SESSION,
        session.id,
        "obsolete recovery note",
        expires_at=utc_now() - timedelta(seconds=1),
    )
    runtime.store.save_memory(expired)

    assert active.source_trace_id == source.metadata["trace_id"]
    assert runtime.purge_expired_memories() == 1
    runtime.forget_memory(active.id)
    assert runtime.search_memory("Recovery", session_id=session.id) == []

    runtime.remember(
        "SQLite checkpoints support recovery.",
        scope="session",
        scope_id=session.id,
    )
    suite = EvalSuite(
        name="memory-retrieval",
        cases=[
            EvalCase(
                name="checkpoint",
                input="SQLite recovery",
                expected_contains=["checkpoints support recovery"],
                expected_memory_count=1,
            )
        ],
    )
    report = await MemoryEvalRunner(runtime).run(suite, session_id=session.id)
    assert report.passed_cases == 1
    assert report.artifact_path is not None
    metrics = ObservabilityService(runtime.store).metrics()
    assert metrics.sessions == 1
    assert metrics.memories_total == 3
    assert metrics.memories_active == 1
    assert metrics.memories_deleted == 1
    assert metrics.memories_expired == 1
    assert "SQLite checkpoints support recovery." in (report.results[0].output or "")


@pytest.mark.asyncio
async def test_large_tool_result_is_artifactized_before_model_reuse(workspace: Path) -> None:
    definition = ToolDefinition(
        name="large",
        description="return large text",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    def responder(messages, tools, config):
        del tools, config
        if messages[-1].role == "tool":
            assert "Tool result stored as artifact" in (messages[-1].content or "")
            return ModelResponse(content="artifact observed")
        return ModelResponse(tool_calls=[ToolCall("large-call", "large", {})])

    runtime = make_runtime(
        workspace,
        responder,
        tools=[(definition, lambda arguments, context: "x" * 300)],
        large_tool_result_chars=128,
        large_tool_result_preview_chars=32,
    )
    run = await runtime.run("memory-agent", "get large result")

    assert run.result == "artifact observed"
    execution = runtime.store.tool_executions_for_run(run.id)[0]
    artifact = (execution.result_data or {})["_artifact"]
    assert Path(artifact["path"]).read_text(encoding="utf-8") == "x" * 300
    assert any(
        event.type == "tool.result.artifactized"
        for event in runtime.store.events_since(run.id)
    )

from __future__ import annotations

import pytest

from agent_runtime.domain import AgentDefinition, ModelConfig, ToolCall, ToolDefinition
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_agent(*, approval: bool = False) -> AgentDefinition:
    return AgentDefinition(
        name="test-agent",
        system_prompt="test",
        tools=[
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
            )
        ],
        model=ModelConfig(provider="mock", model="test"),
    )


def make_runtime(workspace, responder, *, approval: bool = False) -> Runtime:
    tools = ToolRegistry()
    tools.register(make_agent(approval=approval).tools[0], lambda arguments, context: f"echo:{arguments['value']}")
    return Runtime(
        RuntimeConfig(workspace_path=workspace, database_path=workspace / "runtime.sqlite3"),
        provider=MockProvider(responder),
        tools=tools,
    )


@pytest.mark.asyncio
async def test_runtime_completes_tool_loop_and_persists_events(workspace) -> None:
    def responder(messages, tools, config):
        if messages[-1].role == "tool":
            return ModelResponse(content=f"done {messages[-1].content}")
        return ModelResponse(tool_calls=[ToolCall("call_1", "echo", {"value": "hello"})])

    runtime = make_runtime(workspace, responder)
    agent = make_agent()
    run = await runtime.run(agent, "say hello")

    assert run.status == "completed"
    assert run.result == "done echo:hello"
    events = runtime.store.events_since(run.id)
    assert [event.type for event in events] == [
        "run.created",
        "run.started",
        "checkpoint.created",
        "model.requested",
        "model.completed",
        "tool.requested",
        "tool.started",
        "checkpoint.created",
        "tool.completed",
        "model.requested",
        "model.completed",
        "model.delta",
        "checkpoint.created",
        "run.completed",
    ]
    assert runtime.store.latest_checkpoint(run.id) is not None


@pytest.mark.asyncio
async def test_approved_tool_resumes_from_checkpoint(workspace) -> None:
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

    runtime.resolve_approval(approval.id, approved=True)
    completed = await runtime.resume(waiting.id)
    assert completed.status == "completed"
    assert completed.result == "approved result: echo:hello"


@pytest.mark.asyncio
async def test_rejected_tool_is_reported_to_model(workspace) -> None:
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

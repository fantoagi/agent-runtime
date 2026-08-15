from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from agent_runtime.api import create_app
from agent_runtime.domain import (
    AgentDefinition,
    AgentDefinitionUnavailable,
    Message,
    ModelConfig,
    RunStatus,
    ToolDefinition,
)
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_runtime(
    workspace: Path,
    provider: MockProvider,
    tools: ToolRegistry | None = None,
) -> Runtime:
    return Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            event_poll_interval_seconds=0.005,
            model_timeout_seconds=5,
        ),
        provider,
        tools or ToolRegistry(),
    )


async def pause_during_model(runtime: Runtime, agent_name: str) -> str:
    run = runtime.start(agent_name, "recover exact definition")
    for _ in range(200):
        if runtime.store.get_run(run.id).status is RunStatus.RUNNING:
            break
        await asyncio.sleep(0.001)
    runtime.pause(run.id)
    paused = await runtime.wait(run.id)
    assert paused.status is RunStatus.PAUSED
    return run.id


@pytest.mark.asyncio
async def test_paused_run_restores_agent_definition_without_registration(
    workspace: Path,
) -> None:
    entered = asyncio.Event()

    async def blocking(messages, tools, config):
        del messages, tools, config
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    first = make_runtime(workspace, MockProvider(blocking))
    first.register_agent(
        AgentDefinition(
            "snapshot-agent",
            "persisted system prompt",
            [],
            ModelConfig("mock", "snapshot-model"),
        )
    )
    run = first.start("snapshot-agent", "recover exact definition")
    await asyncio.wait_for(entered.wait(), timeout=1)
    first.pause(run.id)
    await first.wait(run.id)
    assert first.store.agent_definition_count() == 1
    await first.shutdown()

    observed: dict[str, str] = {}

    def responder(messages: list[Message], tools, config):
        del tools
        observed["system"] = messages[0].content or ""
        observed["model"] = config.model
        return ModelResponse(content="restored")

    second = make_runtime(workspace, MockProvider(responder))
    completed = await second.resume(run.id)
    assert completed.status is RunStatus.COMPLETED
    assert observed == {
        "system": "persisted system prompt",
        "model": "snapshot-model",
    }
    assert second.store.latest_agent_definition("snapshot-agent") is not None
    await second.shutdown()


@pytest.mark.asyncio
async def test_run_uses_original_snapshot_when_latest_definition_changed(
    workspace: Path,
) -> None:
    entered = asyncio.Event()

    async def blocking(messages, tools, config):
        del messages, tools, config
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    first = make_runtime(workspace, MockProvider(blocking))
    first.register_agent(AgentDefinition("versioned", "prompt-v1", []))
    run = first.start("versioned", "versioned recovery")
    await asyncio.wait_for(entered.wait(), timeout=1)
    first.pause(run.id)
    await first.wait(run.id)
    await first.shutdown()

    observed: list[str] = []

    def responder(messages: list[Message], tools, config):
        del tools, config
        observed.append(messages[0].content or "")
        return ModelResponse(content="done")

    second = make_runtime(workspace, MockProvider(responder))
    second.register_agent(AgentDefinition("versioned", "prompt-v2", []))
    completed = await second.resume(run.id)
    assert completed.status is RunStatus.COMPLETED
    assert observed == ["prompt-v1"]
    assert second.store.agent_definition_count() == 2
    await second.shutdown()


@pytest.mark.asyncio
async def test_sequential_workflow_restores_snapshot_agents_without_registration(
    workspace: Path,
) -> None:
    def responder(messages: list[Message], tools, config):
        del tools, config
        return ModelResponse(content=f"{messages[0].content}:{messages[-1].content}")

    first = make_runtime(workspace, MockProvider(responder))
    first.register_agent(AgentDefinition("planner", "planner-v1", []))
    first.register_agent(AgentDefinition("worker", "worker-v1", []))
    workflow_definition = {
        "name": "snapshot-workflow",
        "type": "sequential",
        "steps": [
            {"agent_name": "planner", "name": None, "input_prefix": ""},
            {"agent_name": "worker", "name": None, "input_prefix": ""},
        ],
    }
    parent = first.begin_workflow(
        "snapshot-workflow",
        "task",
        workflow_type="sequential",
        workflow_definition=workflow_definition,
    )
    first_child = await first.delegate(
        parent.id,
        "planner",
        "task",
        delegation_key="snapshot-workflow:step:0",
    )
    snapshot = first.store.workflow_snapshot(parent.id)
    assert snapshot is not None
    assert all(step.get("agent_definition_checksum") for step in snapshot["steps"])
    await first.shutdown()

    second = make_runtime(workspace, MockProvider(responder))
    completed = await second.resume(parent.id)
    children = second.store.child_runs(parent.id)
    assert completed.status is RunStatus.COMPLETED
    assert len(children) == 2
    assert children[0].id == first_child.id
    assert {agent.name for agent in second.list_agents()} == {"planner", "worker"}
    await second.shutdown()


@pytest.mark.asyncio
async def test_snapshot_recovery_fails_clearly_when_tool_handler_is_missing(
    workspace: Path,
) -> None:
    tool = ToolDefinition("required-tool", "required", {"type": "object"})
    tools = ToolRegistry()
    tools.register(tool, lambda arguments, context: "ok")
    entered = asyncio.Event()

    async def blocking(messages, definitions, config):
        del messages, definitions, config
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    first = make_runtime(workspace, MockProvider(blocking), tools)
    first.register_agent(AgentDefinition("tool-agent", "tool prompt", [tool]))
    run = first.start("tool-agent", "needs tool")
    await asyncio.wait_for(entered.wait(), timeout=1)
    first.pause(run.id)
    await first.wait(run.id)
    await first.shutdown()

    second = make_runtime(
        workspace,
        MockProvider(lambda *_: ModelResponse(content="must not run")),
        ToolRegistry(),
    )
    with pytest.raises(AgentDefinitionUnavailable, match="unavailable tool handlers"):
        await second.resume(run.id)
    assert second.store.get_run(run.id).status is RunStatus.PAUSED

    app = create_app(second)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/runs/{run.id}/resume")
    assert response.status_code == 409
    assert response.json()["code"] == "agent_definition_unavailable"
    assert response.json()["retryable"] is False
    await second.shutdown()


def test_agent_snapshot_validation_and_legacy_fallback_branches(workspace: Path) -> None:
    runtime = make_runtime(
        workspace,
        MockProvider(lambda *_: ModelResponse(content="unused")),
    )
    runtime.register_agent(AgentDefinition("branch-agent", "branch", []))

    assert runtime._freeze_workflow_definition({"type": "manual"}) == {
        "type": "manual"
    }
    pre_frozen = runtime._freeze_workflow_definition(
        {
            "steps": [
                {},
                {
                    "agent_name": "branch-agent",
                    "agent_definition_checksum": "already-frozen",
                },
            ]
        }
    )
    assert pre_frozen["steps"][1]["agent_definition_checksum"] == "already-frozen"

    legacy = runtime.store.get_run(runtime.create_run("branch-agent", "legacy").id)
    legacy.metadata.pop("agent_definition_checksum")
    assert runtime._resolve_run_agent(legacy).name == "branch-agent"

    with pytest.raises(AgentDefinitionUnavailable, match="was not found"):
        runtime._agent_from_checksum("missing-checksum")
    with pytest.raises(ValueError, match="must not be blank"):
        runtime.create_run("branch-agent", "blank", idempotency_key="   ")
    with pytest.raises(ValueError, match="must not exceed"):
        runtime.create_run("branch-agent", "long", idempotency_key="x" * 201)
    runtime.store.close()

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from agent_runtime.domain import AgentDefinition, Message, ModelConfig, ToolCall, ToolDefinition
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def agent(name: str = "crash-agent", tools: list[ToolDefinition] | None = None) -> AgentDefinition:
    return AgentDefinition(name, name, tools or [], ModelConfig("mock", "crash"))


def runtime_for(workspace: Path, provider: MockProvider, tools: ToolRegistry) -> Runtime:
    return Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            model_timeout_seconds=300,
            run_timeout_seconds=300,
        ),
        provider,
        tools,
    )


async def model_request(workspace: Path, ready: Path) -> None:
    run_id = ""

    async def responder(messages, tools, config):
        del messages, tools, config
        write_json(ready, {"scenario": "model_request", "run_id": run_id})
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    runtime = runtime_for(workspace, MockProvider(responder), ToolRegistry())
    runtime.register_agent(agent())
    run = runtime.start("crash-agent", "model crash")
    run_id = run.id
    await runtime.wait(run.id)


async def side_effect(workspace: Path, ready: Path) -> None:
    counter = workspace / "side-effect-count.txt"
    tool = ToolDefinition(
        "side-effect",
        "Crash after a durable external side effect.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        side_effecting=True,
    )

    def handler(arguments, context):
        del arguments
        count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
        counter.write_text(str(count + 1), encoding="utf-8")
        write_json(ready, {"scenario": "side_effect", "run_id": context.run_id})
        while True:
            time.sleep(1)

    def responder(messages: list[Message], tools, config):
        del tools, config
        if messages[-1].role == "tool":
            return ModelResponse(content=f"confirmed:{messages[-1].content}")
        return ModelResponse(tool_calls=[ToolCall("crash-call", "side-effect", {})])

    tools = ToolRegistry()
    tools.register(tool, handler, timeout_seconds=300)
    runtime = runtime_for(workspace, MockProvider(responder), tools)
    runtime.register_agent(agent(tools=[tool]))
    await runtime.run("crash-agent", "side effect crash")


async def approval(workspace: Path, ready: Path) -> None:
    tool = ToolDefinition(
        "approved-tool",
        "Approval recovery tool.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        requires_approval=True,
    )

    def responder(messages: list[Message], tools, config):
        del tools, config
        if messages[-1].role == "tool":
            return ModelResponse(content="approval recovered")
        return ModelResponse(tool_calls=[ToolCall("approval-call", "approved-tool", {})])

    tools = ToolRegistry()
    tools.register(tool, lambda arguments, context: "approved")
    runtime = runtime_for(workspace, MockProvider(responder), tools)
    runtime.register_agent(agent(tools=[tool]))
    run = runtime.start("crash-agent", "approval crash")
    waiting = await runtime.wait(run.id)
    pending = runtime.store.pending_approval(run.id)
    assert pending is not None
    write_json(
        ready,
        {"scenario": "approval", "run_id": waiting.id, "approval_id": pending.id},
    )
    await asyncio.Event().wait()


async def workflow(workspace: Path, ready: Path) -> None:
    def responder(messages: list[Message], tools, config):
        del tools, config
        return ModelResponse(content=f"{messages[0].content}({messages[-1].content})")

    runtime = runtime_for(workspace, MockProvider(responder), ToolRegistry())
    for name in ("planner", "worker", "reviewer"):
        runtime.register_agent(agent(name))
    definition = {
        "name": "crash-workflow",
        "type": "sequential",
        "steps": [
            {"agent_name": name, "name": None, "input_prefix": ""}
            for name in ("planner", "worker", "reviewer")
        ],
    }
    parent = runtime.begin_workflow(
        "crash-workflow",
        "request",
        workflow_type="sequential",
        workflow_definition=definition,
    )
    first = await runtime.delegate(
        parent.id,
        "planner",
        "request",
        delegation_key="crash-workflow:step:0",
    )
    write_json(
        ready,
        {"scenario": "workflow", "run_id": parent.id, "first_child_id": first.id},
    )
    await asyncio.Event().wait()


async def parallel_workflow(workspace: Path, ready: Path) -> None:
    def responder(messages: list[Message], tools, config):
        del tools, config
        return ModelResponse(content=f"{messages[0].content}({messages[-1].content})")

    runtime = runtime_for(workspace, MockProvider(responder), ToolRegistry())
    for name in ("researcher", "critic", "summarizer"):
        runtime.register_agent(agent(name))
    definition = {
        "name": "parallel-crash-workflow",
        "type": "parallel",
        "aggregation": "all",
        "max_concurrency": 2,
        "timeout_seconds": None,
        "steps": [
            {"agent_name": name, "name": None, "input_prefix": ""}
            for name in ("researcher", "critic", "summarizer")
        ],
    }
    parent = runtime.begin_workflow(
        "parallel-crash-workflow",
        "request",
        workflow_type="parallel",
        workflow_definition=definition,
    )
    first = await runtime.delegate(
        parent.id,
        "researcher",
        "request",
        delegation_key="parallel-crash-workflow:branch:0",
    )
    write_json(
        ready,
        {
            "scenario": "parallel_workflow",
            "run_id": parent.id,
            "first_child_id": first.id,
        },
    )
    await asyncio.Event().wait()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("model_request", "side_effect", "approval", "workflow", "parallel_workflow"))
    parser.add_argument("workspace", type=Path)
    parser.add_argument("ready", type=Path)
    arguments = parser.parse_args()
    arguments.workspace.mkdir(parents=True, exist_ok=True)
    await globals()[arguments.scenario](arguments.workspace, arguments.ready)


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime.domain import (
    AgentDefinition,
    Message,
    ModelConfig,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolExecutionStatus,
)
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry

SCENARIOS = ("model_request", "side_effect", "approval", "workflow", "parallel_workflow")


def wait_json(path: Path, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise TimeoutError(f"Crash worker did not reach barrier {path}.")


def definition(name: str = "crash-agent", tools: list[ToolDefinition] | None = None) -> AgentDefinition:
    return AgentDefinition(name, name, tools or [], ModelConfig("mock", "crash"))


def runtime_for(workspace: Path, provider: MockProvider, tools: ToolRegistry) -> Runtime:
    return Runtime(
        RuntimeConfig(workspace_path=workspace, database_path=workspace / "runtime.sqlite3"),
        provider,
        tools,
    )


async def recover(scenario: str, workspace: Path, barrier: dict[str, Any]) -> dict[str, Any]:
    run_id = str(barrier["run_id"])
    if scenario == "model_request":
        runtime = runtime_for(
            workspace,
            MockProvider(lambda messages, tools, config: ModelResponse(content="model recovered")),
            ToolRegistry(),
        )
        before = runtime.store.get_run(run_id)
        completed = await runtime.resume(run_id)
        result = {"before": before.status.value, "after": completed.status.value}
    elif scenario == "side_effect":
        tool = ToolDefinition(
            "side-effect",
            "Recovered side effect.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            side_effecting=True,
        )
        tools = ToolRegistry()
        tools.register(tool, lambda arguments, context: "must-not-run")

        def responder(messages: list[Message], tool_defs, config):
            del tool_defs, config
            if messages[-1].role == "tool":
                return ModelResponse(content=f"confirmed:{messages[-1].content}")
            return ModelResponse(tool_calls=[ToolCall("crash-call", "side-effect", {})])

        runtime = runtime_for(workspace, MockProvider(responder), tools)
        executions = runtime.store.tool_executions_for_run(run_id)
        assert len(executions) == 1 and executions[0].status is ToolExecutionStatus.UNKNOWN
        count_before = int((workspace / "side-effect-count.txt").read_text(encoding="utf-8"))
        runtime.resolve_unknown_tool(
            executions[0].id,
            "confirmed_succeeded",
            result_content="already-applied",
            reason="Crash Matrix verified the external marker.",
            resolved_by="crash-matrix",
        )
        completed = await runtime.resume(run_id)
        count_after = int((workspace / "side-effect-count.txt").read_text(encoding="utf-8"))
        assert count_before == count_after == 1
        result = {"before": "unknown", "after": completed.status.value, "side_effect_count": count_after}
    elif scenario == "approval":
        tool = ToolDefinition(
            "approved-tool",
            "Approval recovery tool.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            requires_approval=True,
        )
        tools = ToolRegistry()
        tools.register(tool, lambda arguments, context: "approved")

        def responder(messages: list[Message], tool_defs, config):
            del tool_defs, config
            if messages[-1].role == "tool":
                return ModelResponse(content="approval recovered")
            return ModelResponse(tool_calls=[ToolCall("approval-call", "approved-tool", {})])

        runtime = runtime_for(workspace, MockProvider(responder), tools)
        pending = runtime.store.pending_approval(run_id)
        assert pending is not None and pending.id == barrier["approval_id"]
        runtime.resolve_approval(pending.id, True, "Crash Matrix approval")
        completed = await runtime.resume(run_id)
        result = {"approval_id": pending.id, "after": completed.status.value}
    else:
        def responder(messages: list[Message], tools, config):
            del tools, config
            return ModelResponse(content=f"{messages[0].content}({messages[-1].content})")

        runtime = runtime_for(workspace, MockProvider(responder), ToolRegistry())
        completed = await runtime.resume(run_id)
        children = runtime.store.child_runs(run_id)
        assert len(children) == 3 and children[0].id == barrier["first_child_id"]
        result = {"after": completed.status.value, "child_count": len(children), "first_child_reused": True}
    assert runtime.store.get_run(run_id).status is RunStatus.COMPLETED
    await runtime.shutdown()
    return {"scenario": scenario, "run_id": run_id, "result": result}


def run_scenario(root: Path, scenario: str) -> dict[str, Any]:
    workspace = root / scenario
    workspace.mkdir(parents=True, exist_ok=True)
    ready = workspace / "ready.json"
    env = os.environ.copy()
    source = str(Path.cwd() / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "tests/crash_worker.py", scenario, str(workspace), str(ready)],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        barrier = wait_json(ready)
        process.kill()
        process.wait(timeout=10)
        return asyncio.run(recover(scenario, workspace, barrier))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process.returncode not in {0, 1, -9} and process.stderr is not None:
            error = process.stderr.read()
            if error:
                print(error, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the cross-platform forced-crash recovery matrix.")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()
    root = arguments.workspace or Path(".runtime-test-data") / f"crash-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"status": "passed", "repeat": arguments.repeat, "results": []}
    try:
        for iteration in range(arguments.repeat):
            iteration_root = root / f"iteration-{iteration + 1}"
            for scenario in SCENARIOS:
                report["results"].append(run_scenario(iteration_root, scenario))
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if arguments.output is not None:
            arguments.output.write_text(rendered, encoding="utf-8")
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    finally:
        if arguments.workspace is None:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

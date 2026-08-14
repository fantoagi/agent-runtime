from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_runtime.domain import AgentDefinition, ModelConfig, RunRelationType, RunStatus
from agent_runtime.evals import EvalCase, EvalSuite, WorkflowEvalRunner
from agent_runtime.observability import ObservabilityService
from agent_runtime.orchestration import (
    AgentRegistry,
    AggregationStrategy,
    ParallelWorkflow,
    SequentialWorkflow,
)
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_agent(name: str) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        system_prompt=name,
        tools=[],
        model=ModelConfig(provider="mock", model="multi-agent-test"),
    )


def make_runtime(workspace: Path, responder) -> Runtime:
    return Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            run_timeout_seconds=5,
            model_timeout_seconds=2,
        ),
        provider=MockProvider(responder),
        tools=ToolRegistry(),
    )


def test_agent_registry_rejects_conflicting_definitions() -> None:
    registry = AgentRegistry()
    first = make_agent("worker")
    registry.register(first)
    registry.register(first)
    with pytest.raises(ValueError, match="another definition"):
        registry.register(
            AgentDefinition(
                name="worker",
                system_prompt="changed",
                tools=[],
                model=first.model,
            )
        )
    assert registry.get("worker") is first
    assert [agent.name for agent in registry.list()] == ["worker"]


@pytest.mark.asyncio
async def test_delegate_persists_relation_and_reuses_stable_key(workspace: Path) -> None:
    def responder(messages, tools, config):
        return ModelResponse(content=f"{messages[0].content}:{messages[-1].content}")

    runtime = make_runtime(workspace, responder)
    worker = make_agent("worker")
    runtime.register_agent(worker)
    parent = runtime.begin_workflow(
        "manual-parent", "root input", workflow_type="manual"
    )
    with pytest.raises(ValueError, match="original Workflow"):
        await runtime.resume(parent.id)
    with pytest.raises(ValueError, match="pause is not supported"):
        runtime.pause(parent.id)

    first = await runtime.delegate(
        parent.id,
        "worker",
        "delegated input",
        delegation_key="stable-task-1",
    )
    second = await runtime.delegate(
        parent.id,
        "worker",
        "this input is ignored on recovery",
        delegation_key="stable-task-1",
    )

    assert first.id == second.id
    assert first.status is RunStatus.COMPLETED
    relation = runtime.store.get_run_relation(first.id)
    assert relation is not None
    assert relation.parent_run_id == parent.id
    assert relation.root_run_id == parent.id
    assert relation.relation_type is RunRelationType.DELEGATION
    assert len(runtime.store.child_runs(parent.id)) == 1
    assert first.metadata["trace_id"] != parent.metadata["trace_id"]
    assert first.metadata["root_trace_id"] == parent.metadata["root_trace_id"]
    parent_events = [event.type for event in runtime.store.events_since(parent.id)]
    assert parent_events.count("delegation.created") == 1
    assert parent_events.count("delegation.completed") == 1


@pytest.mark.asyncio
async def test_sequential_workflow_passes_results_and_recovers_idempotently(
    workspace: Path,
) -> None:
    def responder(messages, tools, config):
        return ModelResponse(content=f"{messages[0].content}({messages[-1].content})")

    runtime = make_runtime(workspace, responder)
    for name in ("planner", "worker", "reviewer"):
        runtime.register_agent(make_agent(name))
    workflow = SequentialWorkflow("delivery", ["planner", "worker", "reviewer"])

    parent = runtime.begin_workflow("delivery", "request", workflow_type="sequential")
    first = await runtime.delegate(
        parent.id,
        "planner",
        "request",
        delegation_key="delivery:step:0",
        relation_type=RunRelationType.WORKFLOW,
    )
    execution = await workflow.run(runtime, "request", parent_run_id=parent.id)

    assert execution.parent.status is RunStatus.COMPLETED
    assert execution.parent.result == "reviewer(worker(planner(request)))"
    assert len(runtime.store.child_runs(parent.id)) == 3
    assert runtime.store.latest_checkpoint(parent.id) is not None
    assert runtime.store.child_runs(parent.id)[0].id == first.id
    assert all(
        relation.relation_type is RunRelationType.WORKFLOW
        for relation in runtime.store.child_relations(parent.id)
    )

    tree = ObservabilityService(runtime.store).trace_tree(parent.id)
    assert tree.node_count == 4
    assert [child.run.agent_name for child in tree.root.children] == [
        "planner",
        "worker",
        "reviewer",
    ]
    metrics = ObservabilityService(runtime.store).metrics()
    assert metrics.root_runs == 1
    assert metrics.child_runs == 3
    assert metrics.workflow_runs == 1
    assert metrics.delegations == 3


@pytest.mark.asyncio
async def test_parallel_workflow_limits_concurrency_and_supports_aggregation(
    workspace: Path,
) -> None:
    active = 0
    maximum = 0

    async def responder(messages, tools, config):
        nonlocal active, maximum
        name = messages[0].content or ""
        if name == "failure":
            raise RuntimeError("branch failed")
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.03)
            return ModelResponse(content=f"{name}:{messages[-1].content}")
        finally:
            active -= 1

    runtime = make_runtime(workspace, responder)
    for name in ("one", "two", "three", "failure"):
        runtime.register_agent(make_agent(name))

    all_workflow = ParallelWorkflow(
        "parallel-all", ["one", "two", "three"], max_concurrency=2
    )
    all_execution = await all_workflow.run(runtime, "input")
    assert all_execution.parent.status is RunStatus.COMPLETED
    assert maximum == 2
    assert len(json.loads(all_execution.parent.result or "[]")) == 3

    strict = ParallelWorkflow("parallel-strict", ["one", "failure"])
    strict_execution = await strict.run(runtime, "input")
    assert strict_execution.parent.status is RunStatus.FAILED

    best_effort = ParallelWorkflow(
        "parallel-best",
        ["one", "failure"],
        aggregation=AggregationStrategy.BEST_EFFORT,
    )
    best_execution = await best_effort.run(runtime, "input")
    assert best_execution.parent.status is RunStatus.COMPLETED
    assert len(json.loads(best_execution.parent.result or "[]")) == 1


@pytest.mark.asyncio
async def test_first_success_and_parent_cancel_propagate_to_children(workspace: Path) -> None:
    async def responder(messages, tools, config):
        name = messages[0].content
        await asyncio.sleep(0.01 if name == "fast" else 1)
        return ModelResponse(content=f"{name}:done")

    runtime = make_runtime(workspace, responder)
    runtime.register_agent(make_agent("fast"))
    runtime.register_agent(make_agent("slow"))

    first = ParallelWorkflow(
        "race",
        ["slow", "fast"],
        aggregation=AggregationStrategy.FIRST_SUCCESS,
        max_concurrency=2,
    )
    execution = await first.run(runtime, "input")
    assert execution.parent.status is RunStatus.COMPLETED
    assert execution.parent.result == "fast:done"
    assert {child.status for child in execution.children} == {
        RunStatus.COMPLETED,
        RunStatus.CANCELLED,
    }

    cancellable = ParallelWorkflow("cancel-tree", ["slow", "slow"], max_concurrency=2)
    parent = cancellable.start(runtime, "input")
    for _ in range(100):
        if len(runtime.store.child_runs(parent.id)) == 2:
            break
        await asyncio.sleep(0.01)
    runtime.cancel(parent.id)
    completed = await runtime.wait(parent.id)
    assert completed.status is RunStatus.CANCELLED
    assert all(
        child.status is RunStatus.CANCELLED
        for child in runtime.store.child_runs(parent.id)
    )


@pytest.mark.asyncio
async def test_workflow_eval_records_parent_trace_and_child_count(workspace: Path) -> None:
    def responder(messages, tools, config):
        return ModelResponse(content=f"{messages[0].content}:{messages[-1].content}")

    runtime = make_runtime(workspace, responder)
    runtime.register_agent(make_agent("planner"))
    runtime.register_agent(make_agent("worker"))
    workflow = SequentialWorkflow("eval-flow", ["planner", "worker"])
    suite = EvalSuite(
        name="multi-agent",
        cases=[
            EvalCase(
                name="happy-path",
                input="task",
                expected_contains=["worker:planner:task"],
                expected_child_count=2,
            )
        ],
    )

    report = await WorkflowEvalRunner(runtime).run(suite, workflow)
    assert report.passed_cases == 1
    assert report.results[0].trace_id.startswith("trace_")
    assert any(
        assertion.evaluator == "expected_child_count" and assertion.passed
        for assertion in report.results[0].assertions
    )
    assert report.artifact_path is not None
    assert Path(report.artifact_path).is_file()

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .domain import TERMINAL_STATUSES, AgentDefinition, AgentRun, RunRelationType, RunStatus

if TYPE_CHECKING:
    from .runtime import Runtime


class AgentRegistry:
    """In-memory catalog of Agent definitions that may receive delegated work."""

    def __init__(
        self, validator: Callable[[AgentDefinition], object] | None = None
    ) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._validator = validator

    def register(self, agent: AgentDefinition) -> None:
        if not agent.name.strip():
            raise ValueError("Agent name must not be empty.")
        if self._validator is not None:
            self._validator(agent)
        existing = self._agents.get(agent.name)
        if existing is not None and existing != agent:
            raise ValueError(f"Agent {agent.name!r} is already registered with another definition.")
        self._agents[agent.name] = agent

    def get(self, name: str) -> AgentDefinition:
        try:
            return self._agents[name]
        except KeyError as error:
            raise KeyError(f"Agent {name!r} is not registered with this runtime.") from error

    def list(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def __contains__(self, name: str) -> bool:
        return name in self._agents


class AggregationStrategy(StrEnum):
    ALL = "all"
    BEST_EFFORT = "best_effort"
    FIRST_SUCCESS = "first_success"


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    agent: AgentDefinition | str
    name: str | None = None
    input_prefix: str = ""

    @property
    def agent_name(self) -> str:
        return self.agent.name if isinstance(self.agent, AgentDefinition) else self.agent

    def prepare_input(self, value: str) -> str:
        return f"{self.input_prefix}{value}" if self.input_prefix else value


@dataclass(slots=True)
class WorkflowExecution:
    parent: AgentRun
    children: list[AgentRun]

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent": self.parent.to_dict(),
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(slots=True)
class SequentialWorkflow:
    name: str
    steps: Sequence[WorkflowStep | AgentDefinition | str]

    def __post_init__(self) -> None:
        self.steps = tuple(_coerce_step(step) for step in self.steps)
        if not self.steps:
            raise ValueError("SequentialWorkflow requires at least one step.")

    async def run(
        self,
        runtime: Runtime,
        input_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
    ) -> WorkflowExecution:
        steps = tuple(_coerce_step(step) for step in self.steps)
        parent = runtime.begin_workflow(
            self.name,
            input_text,
            metadata=metadata,
            parent_run_id=parent_run_id,
            workflow_type="sequential",
            workflow_definition={
                "name": self.name,
                "type": "sequential",
                "steps": [
                    {
                        "agent_name": step.agent_name,
                        "name": step.name,
                        "input_prefix": step.input_prefix,
                    }
                    for step in steps
                ],
            },
        )
        if parent.status in TERMINAL_STATUSES:
            return WorkflowExecution(parent, runtime.store.child_runs(parent.id))
        children: list[AgentRun] = []
        current_input = input_text
        try:
            for index, step in enumerate(steps):
                if runtime.store.get_run(parent.id).status is RunStatus.CANCELLED:
                    break
                child = await runtime.delegate(
                    parent.id,
                    step.agent,
                    step.prepare_input(current_input),
                    delegation_key=f"{self.name}:step:{index}",
                    relation_type=RunRelationType.WORKFLOW,
                    metadata={
                        "workflow_name": self.name,
                        "workflow_type": "sequential",
                        "workflow_step": index,
                        "workflow_step_name": step.name or step.agent_name,
                    },
                )
                children.append(child)
                if child.status is not RunStatus.COMPLETED:
                    return WorkflowExecution(
                        runtime.finish_workflow(
                            parent.id,
                            status=RunStatus.FAILED,
                            error=f"Step {index} ({step.agent_name}) ended as {child.status.value}.",
                        ),
                        children,
                    )
                current_input = child.result or ""
            parent = runtime.store.get_run(parent.id)
            if parent.status is RunStatus.CANCELLED:
                return WorkflowExecution(parent, children)
            parent = runtime.finish_workflow(parent.id, result=current_input)
            return WorkflowExecution(parent, children)
        except asyncio.CancelledError:
            return WorkflowExecution(runtime.cancel(parent.id), children)
        except Exception as error:
            return WorkflowExecution(
                runtime.finish_workflow(parent.id, status=RunStatus.FAILED, error=str(error)),
                children,
            )

    def start(self, runtime: Runtime, input_text: str, *, metadata: dict[str, Any] | None = None) -> AgentRun:
        parent = runtime.create_workflow_run(
            self.name, input_text, metadata=metadata, workflow_type="sequential"
        )
        async def execute() -> AgentRun:
            return (
                await self.run(
                    runtime, input_text, metadata=metadata, parent_run_id=parent.id
                )
            ).parent

        runtime.track_task(parent.id, asyncio.create_task(execute()))
        return parent


@dataclass(slots=True)
class ParallelWorkflow:
    name: str
    steps: Sequence[WorkflowStep | AgentDefinition | str]
    aggregation: AggregationStrategy = AggregationStrategy.ALL
    max_concurrency: int = 4
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        self.steps = tuple(_coerce_step(step) for step in self.steps)
        self.aggregation = AggregationStrategy(self.aggregation)
        if not self.steps:
            raise ValueError("ParallelWorkflow requires at least one step.")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")

    async def run(
        self,
        runtime: Runtime,
        input_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
    ) -> WorkflowExecution:
        steps = tuple(_coerce_step(step) for step in self.steps)
        parent = runtime.begin_workflow(
            self.name,
            input_text,
            metadata=metadata,
            parent_run_id=parent_run_id,
            workflow_type="parallel",
            workflow_definition={
                "name": self.name,
                "type": "parallel",
                "aggregation": self.aggregation.value,
                "max_concurrency": self.max_concurrency,
                "timeout_seconds": self.timeout_seconds,
                "steps": [
                    {
                        "agent_name": step.agent_name,
                        "name": step.name,
                        "input_prefix": step.input_prefix,
                    }
                    for step in steps
                ],
            },
        )
        if parent.status in TERMINAL_STATUSES:
            return WorkflowExecution(parent, runtime.store.child_runs(parent.id))
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def execute(index: int, step: WorkflowStep) -> AgentRun:
            async with semaphore:
                return await runtime.delegate(
                    parent.id,
                    step.agent,
                    step.prepare_input(input_text),
                    delegation_key=f"{self.name}:branch:{index}",
                    relation_type=RunRelationType.WORKFLOW,
                    metadata={
                        "workflow_name": self.name,
                        "workflow_type": "parallel",
                        "workflow_branch": index,
                        "workflow_step_name": step.name or step.agent_name,
                    },
                )

        tasks = [asyncio.create_task(execute(index, step)) for index, step in enumerate(steps)]
        try:
            if self.aggregation is AggregationStrategy.FIRST_SUCCESS:
                children = await self._first_success(runtime, parent.id, tasks)
            else:
                gather = asyncio.gather(*tasks)
                children = await (
                    asyncio.wait_for(gather, timeout=self.timeout_seconds)
                    if self.timeout_seconds is not None
                    else gather
                )
            return self._finish(runtime, parent.id, list(children))
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            runtime.cancel_children(parent.id)
            return WorkflowExecution(
                runtime.finish_workflow(
                    parent.id,
                    status=RunStatus.FAILED,
                    error=f"Parallel workflow timed out after {self.timeout_seconds} seconds.",
                ),
                runtime.store.child_runs(parent.id),
            )
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return WorkflowExecution(runtime.cancel(parent.id), runtime.store.child_runs(parent.id))
        except Exception as error:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return WorkflowExecution(
                runtime.finish_workflow(parent.id, status=RunStatus.FAILED, error=str(error)),
                runtime.store.child_runs(parent.id),
            )

    async def _first_success(
        self,
        runtime: Runtime,
        parent_run_id: str,
        tasks: list[asyncio.Task[AgentRun]],
    ) -> list[AgentRun]:
        pending: set[asyncio.Task[AgentRun]] = set(tasks)
        completed: list[AgentRun] = []
        deadline = None
        if self.timeout_seconds is not None:
            deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while pending:
            timeout = None
            if deadline is not None:
                timeout = max(0.0, deadline - asyncio.get_running_loop().time())
            done, pending = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                raise TimeoutError
            for task in done:
                child = await task
                completed.append(child)
                if child.status is RunStatus.COMPLETED:
                    for remaining in pending:
                        remaining.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    runtime.cancel_children(parent_run_id)
                    return runtime.store.child_runs(parent_run_id)
        return completed

    def _finish(self, runtime: Runtime, parent_run_id: str, children: list[AgentRun]) -> WorkflowExecution:
        children = runtime.store.child_runs(parent_run_id)
        successes = [child for child in children if child.status is RunStatus.COMPLETED]
        failures = [child for child in children if child.status is not RunStatus.COMPLETED]
        if self.aggregation is AggregationStrategy.ALL and failures:
            parent = runtime.finish_workflow(
                parent_run_id,
                status=RunStatus.FAILED,
                error=f"{len(failures)} of {len(children)} branches did not complete.",
            )
        elif not successes:
            parent = runtime.finish_workflow(
                parent_run_id,
                status=RunStatus.FAILED,
                error="No parallel branch completed successfully.",
            )
        else:
            output = (
                successes[0].result or ""
                if self.aggregation is AggregationStrategy.FIRST_SUCCESS
                else json.dumps(
                    [
                    {"run_id": child.id, "agent_name": child.agent_name, "result": child.result}
                        for child in successes
                    ],
                    ensure_ascii=False,
                )
            )
            parent = runtime.finish_workflow(parent_run_id, result=output)
        return WorkflowExecution(parent, children)

    def start(self, runtime: Runtime, input_text: str, *, metadata: dict[str, Any] | None = None) -> AgentRun:
        parent = runtime.create_workflow_run(
            self.name, input_text, metadata=metadata, workflow_type="parallel"
        )
        async def execute() -> AgentRun:
            return (
                await self.run(
                    runtime, input_text, metadata=metadata, parent_run_id=parent.id
                )
            ).parent

        runtime.track_task(parent.id, asyncio.create_task(execute()))
        return parent


def _coerce_step(step: WorkflowStep | AgentDefinition | str) -> WorkflowStep:
    return step if isinstance(step, WorkflowStep) else WorkflowStep(step)

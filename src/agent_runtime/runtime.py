from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from .domain import (
    AgentDefinition,
    AgentRun,
    Approval,
    Checkpoint,
    Message,
    RunLimitExceeded,
    RunStatus,
    ToolCall,
    ToolExecutionError,
)
from .providers import ModelProvider
from .storage import ArtifactStore, SQLiteStore
from .tools import ToolContext, ToolRegistry


@dataclass(slots=True)
class RuntimeConfig:
    workspace_path: Path
    database_path: Path
    artifact_path: Path | None = None
    run_timeout_seconds: float = 300.0
    model_timeout_seconds: float = 90.0
    event_poll_interval_seconds: float = 0.05
    max_model_retries: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workspace_path = Path(self.workspace_path).resolve()
        self.database_path = Path(self.database_path).resolve()
        self.artifact_path = Path(self.artifact_path or self.database_path.parent / "artifacts").resolve()


class Runtime:
    def __init__(
        self,
        config: RuntimeConfig,
        provider: ModelProvider,
        tools: ToolRegistry,
        store: SQLiteStore | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tools = tools
        self.store = store or SQLiteStore(config.database_path)
        self.artifacts = ArtifactStore(config.artifact_path)
        self._agents: dict[str, AgentDefinition] = {}
        self._tasks: dict[str, asyncio.Task[AgentRun]] = {}
        self._cancel_requested: set[str] = set()

    def register_agent(self, agent: AgentDefinition) -> None:
        self.tools.definitions_for(agent.tools)
        self._agents[agent.name] = agent

    def create_run(
        self, agent: AgentDefinition | str, input_text: str, metadata: dict[str, Any] | None = None
    ) -> AgentRun:
        definition = self._resolve_agent(agent)
        run = AgentRun.create(definition.name, input_text, {**self.config.metadata, **(metadata or {})})
        self.store.create_run(run)
        self._event(run.id, "run.created", {"agent_name": definition.name, "input": input_text})
        return run

    async def run(
        self, agent: AgentDefinition | str, input_text: str, metadata: dict[str, Any] | None = None
    ) -> AgentRun:
        run = self.create_run(agent, input_text, metadata)
        return await self._execute(run.id)

    def start(
        self, agent: AgentDefinition | str, input_text: str, metadata: dict[str, Any] | None = None
    ) -> AgentRun:
        run = self.create_run(agent, input_text, metadata)
        self._tasks[run.id] = asyncio.create_task(self._execute(run.id))
        return run

    async def wait(self, run_id: str) -> AgentRun:
        task = self._tasks.get(run_id)
        if task is not None:
            return await task
        return self.store.get_run(run_id)

    async def resume(self, run_id: str) -> AgentRun:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return await existing
        run = self.store.get_run(run_id)
        if run.status not in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.WAITING_FOR_APPROVAL}:
            raise ValueError(f"Run {run_id} cannot be resumed from status {run.status}.")
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id))
        return await self._tasks[run_id]

    def pause(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.status != RunStatus.RUNNING:
            raise ValueError(f"Only running runs can be paused; run {run_id} is {run.status}.")
        run.transition_to(RunStatus.PAUSED)
        self.store.save_run(run)
        self._event(run_id, "run.paused")
        return run

    def cancel(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return run
        self._cancel_requested.add(run_id)
        if run.status != RunStatus.RUNNING:
            run.transition_to(RunStatus.CANCELLED)
            self.store.save_run(run)
            self._event(run_id, "run.cancelled")
        return run

    def resolve_approval(self, approval_id: str, approved: bool, reason: str | None = None) -> Approval:
        approval = self.store.resolve_approval(approval_id, approved, reason)
        self._event(
            approval.run_id,
            "approval.resolved",
            {"approval_id": approval.id, "approved": approved, "reason": reason},
        )
        return approval

    async def stream(
        self, run_id: str, after_sequence: int = 0, stop_when_inactive: bool = True
    ) -> AsyncIterator[Any]:
        sequence = after_sequence
        while True:
            events = self.store.events_since(run_id, sequence)
            for event in events:
                sequence = event.sequence
                yield event
            run = self.store.get_run(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
                return
            if stop_when_inactive and run.status in {RunStatus.PAUSED, RunStatus.WAITING_FOR_APPROVAL}:
                return
            await asyncio.sleep(self.config.event_poll_interval_seconds)

    async def _execute(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        agent = self._resolve_agent(run.agent_name)
        try:
            if run.status == RunStatus.CREATED:
                run.transition_to(RunStatus.RUNNING)
                self.store.save_run(run)
                self._event(run.id, "run.started")
                messages = [Message(role="system", content=agent.system_prompt), Message(role="user", content=run.input)]
                self._checkpoint(run, messages)
            else:
                messages = self._load_messages(run)
                if run.status == RunStatus.RUNNING:
                    self._event(run.id, "run.recovered")
                if run.status == RunStatus.PAUSED:
                    run.transition_to(RunStatus.RUNNING)
                    self.store.save_run(run)
                    self._event(run.id, "run.resumed")
                elif run.status == RunStatus.WAITING_FOR_APPROVAL:
                    if not await self._continue_approval(run, messages):
                        return self.store.get_run(run.id)

            async with asyncio.timeout(self.config.run_timeout_seconds):
                while True:
                    run = self.store.get_run(run.id)
                    if run.id in self._cancel_requested or run.status == RunStatus.CANCELLED:
                        self._cancel_requested.discard(run.id)
                        return self._mark_cancelled(run)
                    if run.status == RunStatus.PAUSED:
                        self._checkpoint(run, messages)
                        return run
                    if run.step_count >= agent.max_steps:
                        raise RunLimitExceeded(f"Run reached its maximum of {agent.max_steps} model steps.")

                    run.step_count += 1
                    self.store.save_run(run)
                    self._event(run.id, "model.requested", {"step": run.step_count, "model": agent.model.model})
                    response = await self._request_model(messages, agent)
                    self._event(
                        run.id,
                        "model.completed",
                        {
                            "step": run.step_count,
                            "finish_reason": response.finish_reason,
                            "usage": response.usage,
                            "has_tool_calls": bool(response.tool_calls),
                        },
                    )
                    assistant_message = Message(
                        role="assistant", content=response.content, tool_calls=response.tool_calls
                    )
                    messages.append(assistant_message)
                    if response.content:
                        self._event(run.id, "model.delta", {"step": run.step_count, "content": response.content})

                    if not response.tool_calls:
                        run.result = response.content or ""
                        run.transition_to(RunStatus.COMPLETED)
                        self.store.save_run(run)
                        self._checkpoint(run, messages)
                        self._event(run.id, "run.completed", {"result": run.result})
                        return run

                    for call in response.tool_calls:
                        if run.tool_call_count >= agent.max_tool_calls:
                            raise RunLimitExceeded(
                                f"Run reached its maximum of {agent.max_tool_calls} tool calls."
                            )
                        registered = self.tools.get(call.name)
                        if registered.definition.requires_approval:
                            approval = Approval.create(run.id, call)
                            self.store.create_approval(approval)
                            run.transition_to(RunStatus.WAITING_FOR_APPROVAL)
                            self.store.save_run(run)
                            self._checkpoint(run, messages)
                            self._event(
                                run.id,
                                "approval.requested",
                                {
                                    "approval_id": approval.id,
                                    "tool_call_id": call.id,
                                    "tool_name": call.name,
                                    "arguments": call.arguments,
                                },
                            )
                            return run
                        await self._invoke_tool(run, call, messages)
                        run = self.store.get_run(run.id)
                        if run.status == RunStatus.PAUSED:
                            self._checkpoint(run, messages)
                            return run
        except asyncio.CancelledError:
            run = self.store.get_run(run_id)
            return self._mark_cancelled(run)
        except Exception as error:
            run = self.store.get_run(run_id)
            if run.status not in {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}:
                run.error = str(error)
                run.transition_to(RunStatus.FAILED)
                self.store.save_run(run)
                self._checkpoint(run, self._load_messages(run))
                self._event(run.id, "run.failed", {"error": str(error), "error_type": type(error).__name__})
            return run
        finally:
            self._tasks.pop(run_id, None)

    async def _continue_approval(self, run: AgentRun, messages: list[Message]) -> bool:
        approval = self.store.pending_approval(run.id)
        if approval is not None:
            return False
        # The latest resolved approval belongs to the run. It can be idempotently continued exactly once.
        resolved = self._latest_resolved_approval(run.id)
        if resolved is None:
            raise RuntimeError("Run is waiting for approval but has no approval record.")
        run.transition_to(RunStatus.RUNNING)
        self.store.save_run(run)
        if resolved.status == "rejected":
            messages.append(
                Message(
                    role="tool",
                    name=resolved.tool_call.name,
                    tool_call_id=resolved.tool_call.id,
                    content=f"Tool call rejected by a human: {resolved.reason or 'no reason provided'}",
                )
            )
            self._checkpoint(run, messages)
            self._event(run.id, "tool.rejected", {"tool_call_id": resolved.tool_call.id, "reason": resolved.reason})
            return True
        await self._invoke_tool(run, resolved.tool_call, messages)
        return True

    def _latest_resolved_approval(self, run_id: str) -> Approval | None:
        # Store keeps its public API intentionally small; scan the event record for the last approval id.
        approvals = [event for event in self.store.events_since(run_id) if event.type == "approval.resolved"]
        if not approvals:
            return None
        return self.store.get_approval(approvals[-1].payload["approval_id"])

    async def _invoke_tool(self, run: AgentRun, call: ToolCall, messages: list[Message]) -> None:
        self._event(
            run.id,
            "tool.requested",
            {"step": run.step_count, "tool_call_id": call.id, "tool_name": call.name, "arguments": call.arguments},
        )
        self._event(run.id, "tool.started", {"tool_call_id": call.id, "tool_name": call.name})
        context = ToolContext(
            run_id=run.id,
            step_id=run.step_count,
            workspace_path=self.config.workspace_path,
            metadata=run.metadata,
        )
        try:
            result = await self.tools.invoke(call.name, call.arguments, context)
        except ToolExecutionError as error:
            messages.append(Message(role="tool", name=call.name, tool_call_id=call.id, content=f"ERROR: {error}"))
            run.tool_call_count += 1
            self.store.save_run(run)
            self._checkpoint(run, messages)
            self._event(
                run.id,
                "tool.failed",
                {"tool_call_id": call.id, "tool_name": call.name, "error": str(error)},
            )
            return
        messages.append(Message(role="tool", name=call.name, tool_call_id=call.id, content=result.content))
        run.tool_call_count += 1
        self.store.save_run(run)
        self._checkpoint(run, messages)
        self._event(
            run.id,
            "tool.completed",
            {"tool_call_id": call.id, "tool_name": call.name, "content": result.content},
        )

    async def _request_model(self, messages: list[Message], agent: AgentDefinition):
        last_error: Exception | None = None
        for attempt in range(self.config.max_model_retries + 1):
            try:
                return await asyncio.wait_for(
                    self.provider.complete(messages, self.tools.definitions_for(agent.tools), agent.model),
                    timeout=self.config.model_timeout_seconds,
                )
            except Exception as error:
                last_error = error
                if attempt >= self.config.max_model_retries:
                    break
                await asyncio.sleep(0.25 * (2**attempt))
        assert last_error is not None
        raise last_error

    def _load_messages(self, run: AgentRun) -> list[Message]:
        checkpoint = self.store.latest_checkpoint(run.id)
        if checkpoint is None:
            return [Message(role="system", content=self._resolve_agent(run.agent_name).system_prompt), Message(role="user", content=run.input)]
        return checkpoint.messages

    def _checkpoint(self, run: AgentRun, messages: list[Message]) -> None:
        checkpoint = Checkpoint.create(run.id, run.step_count, messages, run.tool_call_count)
        self.store.save_checkpoint(checkpoint)
        self._event(run.id, "checkpoint.created", {"checkpoint_id": checkpoint.id, "step": checkpoint.step})

    def _mark_cancelled(self, run: AgentRun) -> AgentRun:
        if run.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            run.transition_to(RunStatus.CANCELLED)
            self.store.save_run(run)
            self._event(run.id, "run.cancelled")
        return run

    def _event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.store.append_event(run_id, event_type, payload)

    def _resolve_agent(self, agent: AgentDefinition | str) -> AgentDefinition:
        if isinstance(agent, AgentDefinition):
            self.register_agent(agent)
            return agent
        try:
            return self._agents[agent]
        except KeyError as error:
            raise KeyError(f"Agent {agent!r} is not registered with this runtime.") from error




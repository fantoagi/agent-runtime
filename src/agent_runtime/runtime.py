from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from .domain import (
    AgentDefinition,
    AgentRun,
    Approval,
    Checkpoint,
    Message,
    RunLimitExceeded,
    RunStatus,
    Step,
    StepStatus,
    ToolCall,
    ToolExecution,
    ToolExecutionError,
    ToolExecutionStatus,
    ToolOutcomeUnknown,
    utc_now,
)
from .providers import (
    ModelProvider,
    ModelResponse,
    ModelTokenDelta,
    StreamingModelProvider,
    ToolCallDelta,
)
from .storage import ArtifactStore, SQLiteStore
from .tools import CancellationToken, ToolContext, ToolRegistry


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
        self.artifact_path = Path(
            self.artifact_path or self.database_path.parent / "artifacts"
        ).resolve()


class Runtime:
    def __init__(
        self,
        config: RuntimeConfig,
        provider: ModelProvider | StreamingModelProvider,
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
        self._cancellation_tokens: dict[str, CancellationToken] = {}

    def register_agent(self, agent: AgentDefinition) -> None:
        self.tools.definitions_for(agent.tools)
        self._agents[agent.name] = agent

    def create_run(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRun:
        definition = self._resolve_agent(agent)
        run = AgentRun.create(
            definition.name,
            input_text,
            {**self.config.metadata, **(metadata or {})},
        )
        self.store.create_run_with_event(
            run,
            "run.created",
            {"agent_name": definition.name, "input": input_text},
        )
        return run

    async def run(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRun:
        run = self.create_run(agent, input_text, metadata)
        return await self._execute(run.id)

    def start(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
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
        if run.status not in {
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.WAITING_FOR_APPROVAL,
        }:
            raise ValueError(
                f"Run {run_id} cannot be resumed from status {run.status}."
            )
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id))
        return await self._tasks[run_id]

    def pause(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.status != RunStatus.RUNNING:
            raise ValueError(
                f"Only running runs can be paused; run {run_id} is {run.status}."
            )
        run.transition_to(RunStatus.PAUSED)
        self.store.save_run_with_event(run, "run.paused")
        return run

    def cancel(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return run
        token = self._cancellation_tokens.get(run_id)
        if token is not None:
            token.cancel()
        run.transition_to(RunStatus.CANCELLED)
        self.store.save_run_with_event(run, "run.cancelled")
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        return run

    def resolve_approval(
        self, approval_id: str, approved: bool, reason: str | None = None
    ) -> Approval:
        approval = self.store.resolve_approval(approval_id, approved, reason)
        self._event(
            approval.run_id,
            "approval.resolved",
            {
                "approval_id": approval.id,
                "tool_execution_id": approval.tool_execution_id,
                "approved": approved,
                "reason": reason,
            },
        )
        return approval

    def resolve_unknown_tool(
        self,
        execution_id: str,
        outcome: Literal["completed", "retry", "failed"],
        *,
        result_content: str | None = None,
        error: str | None = None,
    ) -> ToolExecution:
        execution = self.store.get_tool_execution(execution_id)
        if execution.status != ToolExecutionStatus.UNKNOWN:
            raise ValueError(
                f"Tool execution {execution_id} is {execution.status}, not unknown."
            )
        if outcome == "completed":
            execution.status = ToolExecutionStatus.COMPLETED
            execution.result_content = result_content or (
                "Human review confirmed that the side effect completed."
            )
            execution.error = None
            execution.completed_at = utc_now()
        elif outcome == "retry":
            execution.status = ToolExecutionStatus.PENDING
            execution.error = None
            execution.started_at = None
            execution.completed_at = None
        elif outcome == "failed":
            execution.status = ToolExecutionStatus.FAILED
            execution.error = error or "Human review marked the tool execution as failed."
            execution.completed_at = utc_now()
        else:
            raise ValueError(f"Unsupported unknown-tool outcome: {outcome}")
        run = self.store.get_run(execution.run_id)
        if run.status == RunStatus.PAUSED:
            run.transition_to(RunStatus.RUNNING)
        self.store.resolve_unknown_execution(execution, run, outcome)
        return execution

    async def stream(
        self,
        run_id: str,
        after_sequence: int = 0,
        stop_when_inactive: bool = True,
    ) -> AsyncIterator[Any]:
        sequence = after_sequence
        while True:
            events = self.store.events_since(run_id, sequence)
            for event in events:
                sequence = event.sequence
                yield event
            run = self.store.get_run(run_id)
            if run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return
            if stop_when_inactive and run.status in {
                RunStatus.PAUSED,
                RunStatus.WAITING_FOR_APPROVAL,
            }:
                return
            await asyncio.sleep(self.config.event_poll_interval_seconds)

    async def _execute(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        agent = self._resolve_agent(run.agent_name)
        token = CancellationToken()
        self._cancellation_tokens[run_id] = token
        messages: list[Message] = []
        try:
            if run.status == RunStatus.CREATED:
                run.transition_to(RunStatus.RUNNING)
                self.store.save_run_with_event(run, "run.started")
                messages = [
                    Message(role="system", content=agent.system_prompt),
                    Message(role="user", content=run.input),
                ]
                self._checkpoint(run, messages)
            else:
                messages = self._load_messages(run)
                recovered = self.store.mark_running_tool_executions_unknown(run.id)
                unknown = [
                    execution
                    for execution in recovered
                    if execution.status == ToolExecutionStatus.UNKNOWN
                ]
                if unknown:
                    if run.status == RunStatus.WAITING_FOR_APPROVAL:
                        run.transition_to(RunStatus.RUNNING)
                    if run.status == RunStatus.RUNNING:
                        run.transition_to(RunStatus.PAUSED)
                    self.store.save_run_with_event(
                        run,
                        "run.paused",
                        {
                            "reason": "unknown_tool_outcome",
                            "tool_execution_ids": [item.id for item in unknown],
                        },
                    )
                    for execution in unknown:
                        self._event(
                            run.id,
                            "tool.outcome_unknown",
                            {
                                "tool_execution_id": execution.id,
                                "tool_call_id": execution.tool_call.id,
                                "tool_name": execution.tool_call.name,
                            },
                        )
                    return run
                if run.status == RunStatus.RUNNING:
                    self._event(run.id, "run.recovered")
                elif run.status == RunStatus.PAUSED:
                    run.transition_to(RunStatus.RUNNING)
                    self.store.save_run_with_event(run, "run.resumed")
                elif run.status == RunStatus.WAITING_FOR_APPROVAL:
                    if self.store.pending_approval(run.id) is not None:
                        return run
                    run.transition_to(RunStatus.RUNNING)
                    self.store.save_run_with_event(run, "run.resumed")

            async with asyncio.timeout(self.config.run_timeout_seconds):
                incomplete = self.store.latest_incomplete_step(run.id)
                if incomplete is not None:
                    self._ensure_assistant_message(messages, incomplete)
                    if not await self._process_step(
                        run, incomplete, messages, agent, token
                    ):
                        return self.store.get_run(run.id)

                while True:
                    run = self.store.get_run(run.id)
                    token.raise_if_cancelled()
                    if run.status == RunStatus.CANCELLED:
                        return run
                    if run.status == RunStatus.PAUSED:
                        self._checkpoint(run, messages)
                        return run
                    if run.step_count >= agent.max_steps:
                        raise RunLimitExceeded(
                            f"Run reached its maximum of {agent.max_steps} model steps."
                        )

                    run.step_count += 1
                    step = Step.create(run.id, run.step_count)
                    self.store.create_step_with_event(
                        run,
                        step,
                        "model.requested",
                        {"step": run.step_count, "model": agent.model.model},
                    )
                    response = await self._request_model(run, messages, agent, step.step_index)
                    assistant_message = Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                    step.assistant_message = assistant_message
                    messages.append(assistant_message)
                    model_payload = {
                        "step": run.step_count,
                        "finish_reason": response.finish_reason,
                        "usage": response.usage,
                        "has_tool_calls": bool(response.tool_calls),
                    }
                    delta_payload = (
                        {"step": run.step_count, "content": response.content}
                        if response.content and not response.raw_response.get("_streamed")
                        else None
                    )

                    if not response.tool_calls:
                        step.status = StepStatus.COMPLETED
                        run.result = response.content or ""
                        run.transition_to(RunStatus.COMPLETED)
                        checkpoint = Checkpoint.create(
                            run.id, run.step_count, messages, run.tool_call_count
                        )
                        self.store.complete_run_from_model(
                            run,
                            step,
                            checkpoint,
                            model_payload,
                            delta_payload=delta_payload,
                        )
                        return run

                    executions: list[ToolExecution] = []
                    for position, call in enumerate(response.tool_calls):
                        registered = self.tools.get(call.name)
                        executions.append(
                            ToolExecution.create(
                                run.id,
                                step.id,
                                position,
                                call,
                                requires_approval=(
                                    registered.definition.requires_approval
                                ),
                                side_effecting=(
                                    registered.definition.side_effecting
                                ),
                            )
                        )
                    step.status = StepStatus.WAITING_FOR_TOOLS
                    checkpoint = Checkpoint.create(
                        run.id, run.step_count, messages, run.tool_call_count
                    )
                    self.store.save_model_tool_plan(
                        step,
                        executions,
                        checkpoint,
                        model_payload,
                        delta_payload=delta_payload,
                    )
                    if not await self._process_step(
                        run, step, messages, agent, token
                    ):
                        return self.store.get_run(run.id)
        except asyncio.CancelledError:
            run = self.store.get_run(run_id)
            return self._mark_cancelled(run)
        except Exception as error:
            run = self.store.get_run(run_id)
            if run.status not in {
                RunStatus.COMPLETED,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
            }:
                run.error = str(error)
                run.transition_to(RunStatus.FAILED)
                self._checkpoint(run, messages or self._load_messages(run))
                self.store.save_run_with_event(
                    run,
                    "run.failed",
                    {"error": str(error), "error_type": type(error).__name__},
                )
            return run
        finally:
            self._tasks.pop(run_id, None)
            self._cancellation_tokens.pop(run_id, None)

    async def _process_step(
        self,
        run: AgentRun,
        step: Step,
        messages: list[Message],
        agent: AgentDefinition,
        token: CancellationToken,
    ) -> bool:
        executions = self.store.tool_executions_for_step(step.id)
        for execution in executions:
            run = self.store.get_run(run.id)
            token.raise_if_cancelled()
            if run.status == RunStatus.PAUSED:
                self._checkpoint(run, messages)
                return False
            if run.tool_call_count >= agent.max_tool_calls and execution.status not in {
                ToolExecutionStatus.COMPLETED,
                ToolExecutionStatus.FAILED,
                ToolExecutionStatus.REJECTED,
            }:
                raise RunLimitExceeded(
                    f"Run reached its maximum of {agent.max_tool_calls} tool calls."
                )

            if execution.status == ToolExecutionStatus.COMPLETED:
                self._append_execution_message(messages, execution)
                continue
            if execution.status in {
                ToolExecutionStatus.FAILED,
                ToolExecutionStatus.REJECTED,
            }:
                self._append_execution_message(messages, execution)
                continue
            if execution.status == ToolExecutionStatus.UNKNOWN:
                if run.status == RunStatus.RUNNING:
                    run.transition_to(RunStatus.PAUSED)
                    self.store.save_run_with_event(
                        run,
                        "run.paused",
                        {
                            "reason": "unknown_tool_outcome",
                            "tool_execution_ids": [execution.id],
                        },
                    )
                return False
            if execution.status == ToolExecutionStatus.WAITING_FOR_APPROVAL:
                approval = self.store.approval_for_execution(execution.id)
                if approval is None:
                    raise RuntimeError(
                        f"Tool execution {execution.id} is waiting without an approval."
                    )
                if approval.status == "pending":
                    return False
                if approval.status == "rejected":
                    execution.status = ToolExecutionStatus.REJECTED
                    execution.error = (
                        "Tool call rejected by a human: "
                        f"{approval.reason or 'no reason provided'}"
                    )
                    execution.completed_at = utc_now()
                    run.tool_call_count += 1
                    self._append_execution_message(messages, execution)
                    checkpoint = Checkpoint.create(
                        run.id, run.step_count, messages, run.tool_call_count
                    )
                    self.store.save_tool_execution_with_event(
                        execution,
                        "tool.rejected",
                        {
                            "tool_execution_id": execution.id,
                            "tool_call_id": execution.tool_call.id,
                            "reason": approval.reason,
                        },
                        run=run,
                        checkpoint=checkpoint,
                    )
                    continue
                execution.status = ToolExecutionStatus.PENDING
                self.store.save_tool_execution(execution)

            if execution.requires_approval:
                approval = self.store.approval_for_execution(execution.id)
                if approval is None:
                    approval = Approval.create(
                        run.id,
                        execution.tool_call,
                        tool_execution_id=execution.id,
                    )
                    execution.status = ToolExecutionStatus.WAITING_FOR_APPROVAL
                    run.transition_to(RunStatus.WAITING_FOR_APPROVAL)
                    self.store.create_approval_with_state(
                        approval,
                        run,
                        execution,
                        "approval.requested",
                        {
                            "approval_id": approval.id,
                            "tool_execution_id": execution.id,
                            "tool_call_id": execution.tool_call.id,
                            "tool_name": execution.tool_call.name,
                            "arguments": execution.tool_call.arguments,
                        },
                    )
                    self._checkpoint(run, messages)
                    return False

            await self._invoke_tool(run, execution, messages, token)
            latest = self.store.get_tool_execution(execution.id)
            if latest.status == ToolExecutionStatus.UNKNOWN:
                return False

        step.status = StepStatus.COMPLETED
        persisted_run = self.store.get_run(run.id)
        checkpoint = Checkpoint.create(
            persisted_run.id,
            persisted_run.step_count,
            messages,
            persisted_run.tool_call_count,
        )
        self.store.complete_step_with_checkpoint(step, checkpoint)
        return True

    async def _invoke_tool(
        self,
        run: AgentRun,
        execution: ToolExecution,
        messages: list[Message],
        token: CancellationToken,
    ) -> None:
        call = execution.tool_call
        self._event(
            run.id,
            "tool.requested",
            {
                "step": run.step_count,
                "tool_execution_id": execution.id,
                "tool_call_id": call.id,
                "tool_name": call.name,
                "arguments": call.arguments,
                "idempotency_key": execution.idempotency_key,
            },
        )
        execution.status = ToolExecutionStatus.RUNNING
        execution.started_at = utc_now()
        self.store.save_tool_execution_with_event(
            execution,
            "tool.started",
            {
                "tool_execution_id": execution.id,
                "tool_call_id": call.id,
                "tool_name": call.name,
            },
        )
        context = ToolContext(
            run_id=run.id,
            step_id=run.step_count,
            workspace_path=self.config.workspace_path,
            metadata=run.metadata,
            cancellation_token=token,
            idempotency_key=execution.idempotency_key,
        )
        try:
            result = await self.tools.invoke(call.name, call.arguments, context)
            self._after_tool_handler(execution)
        except asyncio.CancelledError:
            execution.status = (
                ToolExecutionStatus.UNKNOWN
                if execution.side_effecting
                else ToolExecutionStatus.CANCELLED
            )
            execution.error = (
                "Cancellation interrupted a side-effecting tool; outcome is unknown."
                if execution.side_effecting
                else "Tool execution was cancelled."
            )
            execution.completed_at = utc_now()
            self.store.save_tool_execution_with_event(
                execution,
                "tool.outcome_unknown"
                if execution.side_effecting
                else "tool.cancelled",
                {
                    "tool_execution_id": execution.id,
                    "tool_call_id": call.id,
                    "error": execution.error,
                },
            )
            raise
        except ToolOutcomeUnknown as error:
            execution.status = ToolExecutionStatus.UNKNOWN
            execution.error = str(error)
            execution.completed_at = utc_now()
            self.store.save_tool_execution_with_event(
                execution,
                "tool.outcome_unknown",
                {
                    "tool_execution_id": execution.id,
                    "tool_call_id": call.id,
                    "error": str(error),
                },
            )
            run = self.store.get_run(run.id)
            if run.status == RunStatus.RUNNING:
                run.transition_to(RunStatus.PAUSED)
                self.store.save_run_with_event(
                    run,
                    "run.paused",
                    {
                        "reason": "unknown_tool_outcome",
                        "tool_execution_ids": [execution.id],
                    },
                )
            return
        except ToolExecutionError as error:
            execution.status = ToolExecutionStatus.FAILED
            execution.error = str(error)
            execution.completed_at = utc_now()
            run.tool_call_count += 1
            self._append_execution_message(messages, execution)
            checkpoint = Checkpoint.create(
                run.id, run.step_count, messages, run.tool_call_count
            )
            self.store.save_tool_execution_with_event(
                execution,
                "tool.failed",
                {
                    "tool_execution_id": execution.id,
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "error": str(error),
                },
                run=run,
                checkpoint=checkpoint,
            )
            return

        execution.status = ToolExecutionStatus.COMPLETED
        execution.result_content = result.content
        execution.result_data = result.data
        execution.error = None
        execution.completed_at = utc_now()
        run.tool_call_count += 1
        self._append_execution_message(messages, execution)
        checkpoint = Checkpoint.create(
            run.id, run.step_count, messages, run.tool_call_count
        )
        self.store.save_tool_execution_with_event(
            execution,
            "tool.completed",
            {
                "tool_execution_id": execution.id,
                "tool_call_id": call.id,
                "tool_name": call.name,
                "content": result.content,
            },
            run=run,
            checkpoint=checkpoint,
        )

    def _after_tool_handler(self, execution: ToolExecution) -> None:
        """Failure-injection seam: called after a handler returns, before durable completion."""

    async def _request_model(
        self,
        run: AgentRun,
        messages: list[Message],
        agent: AgentDefinition,
        step_index: int,
    ) -> ModelResponse:
        last_error: Exception | None = None
        for attempt in range(self.config.max_model_retries + 1):
            try:
                stream = getattr(self.provider, "stream", None)
                if callable(stream):
                    self._event(
                        run.id,
                        "model.stream.started",
                        {"step": step_index, "attempt": attempt + 1},
                    )
                    response = await asyncio.wait_for(
                        self._consume_model_stream(
                            run,
                            stream(
                                messages,
                                self.tools.definitions_for(agent.tools),
                                agent.model,
                            ),
                            step_index,
                            attempt + 1,
                        ),
                        timeout=self.config.model_timeout_seconds,
                    )
                    self._event(
                        run.id,
                        "model.stream.completed",
                        {
                            "step": step_index,
                            "finish_reason": response.finish_reason,
                            "usage": response.usage,
                            "has_tool_calls": bool(response.tool_calls),
                        },
                    )
                    return response
                return await asyncio.wait_for(
                    self.provider.complete(
                        messages,
                        self.tools.definitions_for(agent.tools),
                        agent.model,
                    ),
                    timeout=self.config.model_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
                if callable(getattr(self.provider, "stream", None)):
                    self._event(
                        run.id,
                        "model.stream.failed",
                        {"step": step_index, "attempt": attempt + 1, "error": str(error)},
                    )
                if attempt >= self.config.max_model_retries:
                    break
                await asyncio.sleep(0.25 * (2**attempt))
        assert last_error is not None
        raise last_error

    async def _consume_model_stream(
        self,
        run: AgentRun,
        deltas: AsyncIterator[ModelTokenDelta],
        step_index: int,
        attempt: int,
    ) -> ModelResponse:
        content: list[str] = []
        calls: dict[int, ToolCallDelta] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        async for delta in deltas:
            if delta.content:
                content.append(delta.content)
            for item in delta.tool_call_deltas:
                existing = calls.setdefault(item.index, ToolCallDelta(item.index))
                existing.id = item.id or existing.id
                existing.name = item.name or existing.name
                existing.arguments += item.arguments
            finish_reason = delta.finish_reason or finish_reason
            usage.update(delta.usage)
            self._event(
                run.id,
                "model.delta",
                {
                    "step": step_index,
                    "attempt": attempt,
                    "content": delta.content,
                    "tool_call_deltas": [item.to_dict() for item in delta.tool_call_deltas],
                    "finish_reason": delta.finish_reason,
                    "usage": delta.usage,
                },
            )
        tool_calls = []
        for index in sorted(calls):
            item = calls[index]
            try:
                arguments = json.loads(item.arguments or "{}")
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid streamed tool arguments for index {index}.") from error
            tool_calls.append(
                ToolCall(
                    item.id or f"streamed_call_{index}",
                    item.name or "",
                    arguments,
                )
            )
        return ModelResponse(
            content="".join(content) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            raw_response={"_streamed": True},
        )

    def _load_messages(self, run: AgentRun) -> list[Message]:
        checkpoint = self.store.latest_checkpoint(run.id)
        if checkpoint is None:
            return [
                Message(
                    role="system",
                    content=self._resolve_agent(run.agent_name).system_prompt,
                ),
                Message(role="user", content=run.input),
            ]
        return checkpoint.messages

    def _checkpoint(self, run: AgentRun, messages: list[Message]) -> None:
        checkpoint = Checkpoint.create(
            run.id, run.step_count, messages, run.tool_call_count
        )
        self.store.save_checkpoint_with_event(checkpoint)

    def _mark_cancelled(self, run: AgentRun) -> AgentRun:
        if run.status not in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            run.transition_to(RunStatus.CANCELLED)
            self.store.save_run_with_event(run, "run.cancelled")
        return self.store.get_run(run.id)

    def _append_execution_message(
        self, messages: list[Message], execution: ToolExecution
    ) -> None:
        if any(
            message.role == "tool"
            and message.tool_call_id == execution.tool_call.id
            for message in messages
        ):
            return
        if execution.status == ToolExecutionStatus.COMPLETED:
            content = execution.result_content or ""
        elif execution.status == ToolExecutionStatus.REJECTED:
            content = execution.error or "Tool call rejected by a human."
        else:
            content = f"ERROR: {execution.error or 'Tool execution failed.'}"
        messages.append(
            Message(
                role="tool",
                name=execution.tool_call.name,
                tool_call_id=execution.tool_call.id,
                content=content,
            )
        )

    @staticmethod
    def _ensure_assistant_message(messages: list[Message], step: Step) -> None:
        if step.assistant_message is None:
            return
        call_ids = {call.id for call in step.assistant_message.tool_calls}
        for message in messages:
            if message.role == "assistant" and {
                call.id for call in message.tool_calls
            } == call_ids:
                return
        messages.append(step.assistant_message)

    def _event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.append_event(run_id, event_type, payload)

    def _resolve_agent(
        self, agent: AgentDefinition | str
    ) -> AgentDefinition:
        if isinstance(agent, AgentDefinition):
            self.register_agent(agent)
            return agent
        try:
            return self._agents[agent]
        except KeyError as error:
            raise KeyError(
                f"Agent {agent!r} is not registered with this runtime."
            ) from error

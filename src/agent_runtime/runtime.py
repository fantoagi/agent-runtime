from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .context import ContextBuilder, ContextBuildResult
from .domain import (
    TERMINAL_STATUSES,
    AgentDefinition,
    AgentDefinitionUnavailable,
    AgentRun,
    Approval,
    Checkpoint,
    MemoryRecord,
    MemoryScope,
    MemorySearchResult,
    Message,
    ProviderError,
    ProviderHTTPError,
    RunLimitExceeded,
    RunRelation,
    RunRelationType,
    RunStatus,
    RuntimeCapacityError,
    RuntimeClosedError,
    Session,
    Step,
    StepStatus,
    ToolCall,
    ToolExecution,
    ToolExecutionError,
    ToolExecutionStatus,
    ToolOutcomeUnknown,
    UnknownToolResolution,
    new_id,
    utc_now,
)
from .memory import MemoryStore
from .orchestration import AgentRegistry
from .providers import (
    ModelProvider,
    ModelResponse,
    ModelTokenDelta,
    StreamingModelProvider,
    ToolCallDelta,
)
from .storage import ArtifactStore, SQLiteStore
from .tools import CancellationToken, ToolContext, ToolRegistry

if TYPE_CHECKING:
    from .orchestration import WorkflowExecution


@dataclass(frozen=True, slots=True)
class RunSubmission:
    run: AgentRun
    replayed: bool


@dataclass(slots=True)
class RuntimeConfig:
    workspace_path: Path
    database_path: Path
    artifact_path: Path | None = None
    run_timeout_seconds: float = 300.0
    model_timeout_seconds: float = 90.0
    event_poll_interval_seconds: float = 0.05
    sse_heartbeat_seconds: float = 15.0
    max_model_retries: int = 2
    shutdown_timeout_seconds: float = 30.0
    max_sync_tool_workers: int = 8
    max_pending_sync_tools: int = 32
    max_inflight_runs: int = 64
    max_concurrent_model_requests: int = 8
    sqlite_busy_timeout_seconds: float = 5.0
    sqlite_synchronous: str = "FULL"
    sqlite_lock_retry_attempts: int = 3
    context_token_budget: int = 4096
    context_recent_groups: int = 4
    context_summary_max_chars: int = 1000
    memory_search_limit: int = 5
    memory_token_budget: int = 512
    large_tool_result_chars: int = 4000
    large_tool_result_preview_chars: int = 400
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workspace_path = Path(self.workspace_path).resolve()
        self.database_path = Path(self.database_path).resolve()
        self.artifact_path = Path(
            self.artifact_path or self.database_path.parent / "artifacts"
        ).resolve()
        if self.context_token_budget < 64:
            raise ValueError("context_token_budget must be at least 64.")
        if self.max_sync_tool_workers < 1:
            raise ValueError("max_sync_tool_workers must be at least 1.")
        if self.max_pending_sync_tools < self.max_sync_tool_workers:
            raise ValueError(
                "max_pending_sync_tools must be at least max_sync_tool_workers."
            )
        if self.max_inflight_runs < 1:
            raise ValueError("max_inflight_runs must be at least 1.")
        if self.max_concurrent_model_requests < 1:
            raise ValueError("max_concurrent_model_requests must be at least 1.")
        if self.sqlite_busy_timeout_seconds < 0:
            raise ValueError("sqlite_busy_timeout_seconds must not be negative.")
        if self.memory_search_limit < 0:
            raise ValueError("memory_search_limit must not be negative.")
        if self.large_tool_result_chars < 128:
            raise ValueError("large_tool_result_chars must be at least 128.")


class Runtime:
    def __init__(
        self,
        config: RuntimeConfig,
        provider: ModelProvider | StreamingModelProvider,
        tools: ToolRegistry,
        store: SQLiteStore | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tools = tools
        self.tools.configure_execution(
            max_sync_workers=config.max_sync_tool_workers,
            max_pending_sync_tools=config.max_pending_sync_tools,
        )
        self._owns_store = store is None
        self.store = store or SQLiteStore(
            config.database_path,
            busy_timeout_seconds=config.sqlite_busy_timeout_seconds,
            synchronous=config.sqlite_synchronous,
            lock_retry_attempts=config.sqlite_lock_retry_attempts,
        )
        self.memory = cast(MemoryStore, memory_store or self.store)
        assert config.artifact_path is not None
        self.artifacts = ArtifactStore(config.artifact_path)
        self.context_builder = ContextBuilder(
            config.context_token_budget,
            recent_groups=config.context_recent_groups,
            summary_max_chars=config.context_summary_max_chars,
            memory_token_budget=config.memory_token_budget,
        )
        self.agent_registry = AgentRegistry(
            validator=lambda agent: self.tools.definitions_for(agent.tools)
        )
        self._tasks: dict[str, asyncio.Task[AgentRun]] = {}
        self._cancellation_tokens: dict[str, CancellationToken] = {}
        self._model_capacity = asyncio.Semaphore(config.max_concurrent_model_requests)
        self._closing = False
        self._closed = False
        self._shutdown_run_ids: set[str] = set()
        self._pause_run_ids: set[str] = set()
        self._reconcile_incomplete_runs()

    @property
    def is_accepting(self) -> bool:
        return not self._closing and not self._closed

    @property
    def active_task_count(self) -> int:
        return sum(1 for task in self._tasks.values() if not task.done())

    def capacity_snapshot(self) -> dict[str, int]:
        return {
            "active_tasks": self.active_task_count,
            "max_inflight_runs": self.config.max_inflight_runs,
            "max_concurrent_model_requests": self.config.max_concurrent_model_requests,
        }

    async def __aenter__(self) -> Runtime:
        if self._closed:
            raise RuntimeClosedError("Runtime is already closed.")
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        await self.shutdown()

    def _ensure_accepting(self) -> None:
        if not self.is_accepting:
            raise RuntimeClosedError("Runtime is shutting down or already closed.")

    def _ensure_run_capacity(self) -> None:
        if self.active_task_count >= self.config.max_inflight_runs:
            raise RuntimeCapacityError(
                "Runtime in-flight run capacity is exhausted; retry after active work completes."
            )

    def _reconcile_incomplete_runs(self) -> None:
        for run in self.store.incomplete_runs():
            if run.status is not RunStatus.RUNNING:
                continue
            recovered = self.store.mark_running_tool_executions_unknown(run.id)
            unknown = [item for item in recovered if item.status is ToolExecutionStatus.UNKNOWN]
            if unknown:
                run.transition_to(RunStatus.PAUSED)
                self.store.save_run_with_event(
                    run,
                    "run.paused",
                    {
                        "reason": "startup_reconciliation",
                        "tool_execution_ids": [item.id for item in unknown],
                    },
                )

    async def shutdown(
        self,
        timeout_seconds: float | None = None,
        cancel_running: bool = False,
    ) -> None:
        if self._closed:
            return
        self._closing = True
        timeout = self.config.shutdown_timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + max(0.0, timeout)
        tasks = {run_id: task for run_id, task in self._tasks.items() if not task.done()}
        if cancel_running:
            for run_id, task in tasks.items():
                self._shutdown_run_ids.add(run_id)
                token = self._cancellation_tokens.get(run_id)
                if token is not None:
                    token.cancel()
                task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks.values(), timeout=max(0.0, timeout))
            del done
            if pending:
                for run_id, task in tasks.items():
                    if task not in pending:
                        continue
                    self._shutdown_run_ids.add(run_id)
                    token = self._cancellation_tokens.get(run_id)
                    if token is not None:
                        token.cancel()
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        close_provider = getattr(self.provider, "aclose", None)
        if callable(close_provider):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                await asyncio.wait_for(close_provider(), timeout=remaining)
            except TimeoutError:
                pass
        remaining = max(0.0, deadline - time.monotonic())
        await self.tools.aclose(timeout_seconds=remaining)
        if self._owns_store:
            self.store.close()
        self._closed = True

    def register_agent(self, agent: AgentDefinition) -> None:
        self.agent_registry.register(agent)
        self.store.save_agent_definition(agent)

    def list_agents(self) -> list[AgentDefinition]:
        return self.agent_registry.list()

    def create_session(self, metadata: dict[str, Any] | None = None) -> Session:
        return self.store.create_session(Session.create(metadata))

    def session_runs(self, session_id: str) -> list[AgentRun]:
        return self.store.session_runs(session_id)

    def remember(
        self,
        content: str,
        *,
        scope: MemoryScope | str,
        scope_id: str,
        source_run_id: str | None = None,
        ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        memory_scope = MemoryScope(scope)
        if memory_scope is MemoryScope.SESSION:
            self.store.get_session(scope_id)
        source_trace_id = None
        if source_run_id is not None:
            source_run = self.store.get_run(source_run_id)
            source_trace_id = str(
                source_run.metadata.get("trace_id") or source_run.id
            )
        expires_at = (
            utc_now() + timedelta(seconds=ttl_seconds)
            if ttl_seconds is not None
            else None
        )
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        record = MemoryRecord.create(
            memory_scope,
            scope_id,
            content,
            source_run_id=source_run_id,
            source_trace_id=source_trace_id,
            expires_at=expires_at,
            metadata=metadata,
        )
        self.memory.save_memory(record)
        if source_run_id is not None:
            self._event(
                source_run_id,
                "memory.created",
                {
                    "memory_id": record.id,
                    "scope": record.scope.value,
                    "scope_id": record.scope_id,
                    "expires_at": record.expires_at.isoformat()
                    if record.expires_at
                    else None,
                },
            )
        return record

    def search_memory(
        self,
        query: str,
        *,
        session_id: str | None = None,
        agent_name: str | None = None,
        limit: int | None = None,
        run_id: str | None = None,
    ) -> list[MemorySearchResult]:
        scopes: list[tuple[MemoryScope, str]] = []
        if session_id is not None:
            self.store.get_session(session_id)
            scopes.append((MemoryScope.SESSION, session_id))
        if agent_name is not None:
            scopes.append((MemoryScope.AGENT, agent_name))
        if not scopes:
            raise ValueError("Memory search requires session_id or agent_name.")
        search_limit = self.config.memory_search_limit if limit is None else limit
        if search_limit < 1:
            return []
        if run_id is not None:
            self._event(
                run_id,
                "memory.search.started",
                {
                    "query": query,
                    "scopes": [
                        {"scope": scope.value, "scope_id": scope_id}
                        for scope, scope_id in scopes
                    ],
                    "limit": search_limit,
                },
            )
        results = self.memory.search_memories(query, scopes, limit=search_limit)
        if run_id is not None:
            self._event(
                run_id,
                "memory.search.completed",
                {
                    "query": query,
                    "result_count": len(results),
                    "memory_ids": [result.record.id for result in results],
                },
            )
        return results

    def forget_memory(self, memory_id: str) -> MemoryRecord:
        record = self.memory.delete_memory(memory_id)
        if record.source_run_id is not None:
            self._event(
                record.source_run_id,
                "memory.deleted",
                {"memory_id": record.id},
            )
        return record

    def purge_expired_memories(self) -> int:
        return self.memory.purge_expired_memories()

    def create_run_submission(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RunSubmission:
        self._ensure_accepting()
        definition = self._resolve_agent(agent)
        requested_metadata = {**self.config.metadata, **(metadata or {})}
        session_id = session_id or requested_metadata.get("session_id")
        if session_id is not None:
            self.store.get_session(str(session_id))
            requested_metadata["session_id"] = str(session_id)
        fingerprint = self._request_fingerprint(
            definition.name, input_text, requested_metadata, session_id
        )
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        if normalized_key is not None:
            existing = self.store.idempotent_run(normalized_key, fingerprint)
            if existing is not None:
                return RunSubmission(existing, True)
        agent_checksum = self.store.save_agent_definition(definition)
        run_metadata = dict(requested_metadata)
        run_metadata["agent_definition_checksum"] = agent_checksum
        run_metadata.setdefault("trace_id", new_id("trace"))
        run = AgentRun.create(definition.name, input_text, run_metadata)
        run.metadata.setdefault("root_run_id", run.id)
        run.metadata.setdefault("root_trace_id", run.metadata["trace_id"])
        event_payload = {
            "agent_name": definition.name,
            "input": input_text,
            "trace_id": run.metadata["trace_id"],
            "session_id": session_id,
            "idempotency_key": normalized_key,
        }
        if normalized_key is None:
            self.store.create_run_with_event(
                run,
                "run.created",
                event_payload,
                session_id=str(session_id) if session_id is not None else None,
            )
            return RunSubmission(run, False)
        durable_run, replayed = self.store.create_run_idempotently(
            run,
            "run.created",
            event_payload,
            idempotency_key=normalized_key,
            request_fingerprint=fingerprint,
            session_id=str(session_id) if session_id is not None else None,
        )
        return RunSubmission(durable_run, replayed)

    def create_run(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRun:
        return self.create_run_submission(
            agent,
            input_text,
            metadata,
            session_id=session_id,
            idempotency_key=idempotency_key,
        ).run

    def create_workflow_run(
        self,
        workflow_name: str,
        input_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        workflow_type: str,
        workflow_definition: dict[str, Any] | None = None,
    ) -> AgentRun:
        self._ensure_accepting()
        run_metadata = {**self.config.metadata, **(metadata or {})}
        run_metadata.update(
            {
                "trace_id": run_metadata.get("trace_id") or new_id("trace"),
                "run_kind": "workflow",
                "workflow_name": workflow_name,
                "workflow_type": workflow_type,
            }
        )
        run = AgentRun.create(f"workflow:{workflow_name}", input_text, run_metadata)
        run.metadata.setdefault("root_run_id", run.id)
        run.metadata.setdefault("root_trace_id", run.metadata["trace_id"])
        self.store.create_run_with_event(
            run,
            "run.created",
            {
                "agent_name": run.agent_name,
                "input": input_text,
                "trace_id": run.metadata["trace_id"],
                "run_kind": "workflow",
                "workflow_name": workflow_name,
                "workflow_type": workflow_type,
                "session_id": run_metadata.get("session_id"),
            },
            session_id=(
                str(run_metadata["session_id"])
                if run_metadata.get("session_id") is not None
                else None
            ),
        )
        if workflow_definition is not None:
            self.store.save_workflow_snapshot(
                run.id, self._freeze_workflow_definition(workflow_definition)
            )
        return run

    def begin_workflow(
        self,
        workflow_name: str,
        input_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        workflow_type: str,
        workflow_definition: dict[str, Any] | None = None,
    ) -> AgentRun:
        self._ensure_accepting()
        run = (
            self.store.get_run(parent_run_id)
            if parent_run_id is not None
            else self.create_workflow_run(
                workflow_name,
                input_text,
                metadata=metadata,
                workflow_type=workflow_type,
                workflow_definition=workflow_definition,
            )
        )
        if workflow_definition is not None and self.store.workflow_snapshot(run.id) is None:
            self.store.save_workflow_snapshot(
                run.id, self._freeze_workflow_definition(workflow_definition)
            )
        if run.status is RunStatus.CREATED:
            run.transition_to(RunStatus.RUNNING)
            self.store.save_run_with_event(
                run,
                "workflow.started",
                {"workflow_name": workflow_name, "workflow_type": workflow_type},
            )
            self._checkpoint(
                run,
                [
                    Message(
                        role="system",
                        content=f"Durable {workflow_type} workflow: {workflow_name}",
                    ),
                    Message(role="user", content=run.input),
                ],
            )
        elif run.status is RunStatus.PAUSED:
            run.transition_to(RunStatus.RUNNING)
            self.store.save_run_with_event(
                run,
                "workflow.resumed",
                {"workflow_name": workflow_name, "workflow_type": workflow_type},
            )
        elif run.status is RunStatus.RUNNING:
            self._event(
                run.id,
                "workflow.recovered",
                {"workflow_name": workflow_name, "workflow_type": workflow_type},
            )
        elif run.status in TERMINAL_STATUSES:
            return run
        return run

    def finish_workflow(
        self,
        run_id: str,
        *,
        result: str | None = None,
        status: RunStatus = RunStatus.COMPLETED,
        error: str | None = None,
    ) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.status in TERMINAL_STATUSES:
            return run
        if status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError("Workflow terminal status must be completed, failed, or cancelled.")
        run.result = result
        run.error = error
        run.step_count = max(run.step_count, 1)
        run.transition_to(status)
        event_type = {
            RunStatus.COMPLETED: "workflow.completed",
            RunStatus.FAILED: "workflow.failed",
            RunStatus.CANCELLED: "workflow.cancelled",
        }[status]
        messages = [
            Message(
                role="system",
                content=(
                    f"Durable {run.metadata.get('workflow_type', 'multi-agent')} workflow: "
                    f"{run.metadata.get('workflow_name', run.agent_name)}"
                ),
            ),
            Message(role="user", content=run.input),
            Message(role="assistant", content=result or error or status.value),
        ]
        checkpoint = Checkpoint.create(run.id, run.step_count, messages, 0)
        self.store.save_run_checkpoint_with_event(
            run, checkpoint, event_type, {"result": result, "error": error}
        )
        return run

    async def delegate(
        self,
        parent_run_id: str,
        agent: AgentDefinition | str,
        input_text: str,
        *,
        delegation_key: str,
        relation_type: RunRelationType = RunRelationType.DELEGATION,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRun:
        if not delegation_key.strip():
            raise ValueError("delegation_key must not be empty.")
        definition = self._resolve_agent(agent)
        existing = self.store.get_delegation(parent_run_id, delegation_key)
        if existing is not None:
            child = self.store.get_run(existing.child_run_id)
            if child.status in TERMINAL_STATUSES:
                return child
            task = self._tasks.get(child.id)
            if task is not None and not task.done():
                return await task
            if child.status is RunStatus.CREATED:
                return await self._execute(child.id)
            return await self.resume(child.id)

        parent = self.store.get_run(parent_run_id)
        if parent.status is not RunStatus.RUNNING:
            raise ValueError(
                f"Parent run {parent_run_id} must be running before delegation; "
                f"current status is {parent.status.value}."
            )
        root_run_id = self.store.root_run_id(parent_run_id)
        root_run = self.store.get_run(root_run_id)
        child_metadata = {**self.config.metadata, **(metadata or {})}
        if parent.metadata.get("session_id") is not None:
            child_metadata.setdefault("session_id", parent.metadata["session_id"])
        child_metadata.update(
            {
                "agent_definition_checksum": self.store.save_agent_definition(definition),
                "trace_id": child_metadata.get("trace_id") or new_id("trace"),
                "root_trace_id": root_run.metadata.get("root_trace_id")
                or root_run.metadata.get("trace_id")
                or root_run.id,
                "parent_run_id": parent_run_id,
                "root_run_id": root_run_id,
                "delegation_key": delegation_key,
                "run_kind": "child",
            }
        )
        child = AgentRun.create(definition.name, input_text, child_metadata)
        relation = RunRelation.create(
            parent_run_id,
            child.id,
            root_run_id,
            delegation_key,
            relation_type=relation_type,
            metadata=metadata,
        )
        relation_payload = {
            "relation_id": relation.id,
            "parent_run_id": parent_run_id,
            "child_run_id": child.id,
            "root_run_id": root_run_id,
            "relation_type": relation.relation_type.value,
            "delegation_key": delegation_key,
            "agent_name": definition.name,
        }
        self.store.create_child_run_with_relation(
            child,
            relation,
            parent_event_payload=relation_payload,
            child_event_payload={
                "agent_name": definition.name,
                "input": input_text,
                "trace_id": child.metadata["trace_id"],
                **relation_payload,
            },
        )
        task = asyncio.create_task(self._execute(child.id))
        self.track_task(child.id, task)
        child = await task
        self._event(
            parent_run_id,
            f"delegation.{child.status.value}",
            {**relation_payload, "result": child.result, "error": child.error},
        )
        return child

    def track_task(self, run_id: str, task: asyncio.Task[AgentRun]) -> None:
        self._tasks[run_id] = task

        def discard(completed: asyncio.Task[AgentRun]) -> None:
            if self._tasks.get(run_id) is completed:
                self._tasks.pop(run_id, None)

        task.add_done_callback(discard)

    def cancel_children(self, parent_run_id: str) -> list[AgentRun]:
        cancelled: list[AgentRun] = []
        for child in self.store.child_runs(parent_run_id):
            if child.status not in TERMINAL_STATUSES:
                cancelled.append(self.cancel(child.id))
        return cancelled

    async def run(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRun:
        submitted = self.submit(
            agent,
            input_text,
            metadata,
            session_id=session_id,
            idempotency_key=idempotency_key,
        )
        return await self.wait(submitted.run.id)

    def submit(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RunSubmission:
        self._ensure_accepting()
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        if normalized_key is not None:
            definition = self._resolve_agent(agent)
            requested_metadata = {**self.config.metadata, **(metadata or {})}
            effective_session = session_id or requested_metadata.get("session_id")
            if effective_session is not None:
                requested_metadata["session_id"] = str(effective_session)
            fingerprint = self._request_fingerprint(
                definition.name, input_text, requested_metadata, effective_session
            )
            existing = self.store.idempotent_run(normalized_key, fingerprint)
            if existing is not None:
                return RunSubmission(existing, True)
        self._ensure_run_capacity()
        submission = self.create_run_submission(
            agent,
            input_text,
            metadata,
            session_id=session_id,
            idempotency_key=normalized_key,
        )
        if not submission.replayed:
            self.track_task(
                submission.run.id, asyncio.create_task(self._execute(submission.run.id))
            )
        return submission

    def start(
        self,
        agent: AgentDefinition | str,
        input_text: str,
        metadata: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRun:
        return self.submit(
            agent,
            input_text,
            metadata,
            session_id=session_id,
            idempotency_key=idempotency_key,
        ).run

    async def wait(
        self, run_id: str, timeout_seconds: float | None = None
    ) -> AgentRun:
        async def wait_persisted() -> AgentRun:
            task = self._tasks.get(run_id)
            if task is not None:
                return await asyncio.shield(task)
            while True:
                run = self.store.get_run(run_id)
                if run.status in TERMINAL_STATUSES or run.status in {
                    RunStatus.PAUSED,
                    RunStatus.WAITING_FOR_APPROVAL,
                }:
                    return run
                await asyncio.sleep(self.config.event_poll_interval_seconds)

        if timeout_seconds is None:
            return await wait_persisted()
        return await asyncio.wait_for(wait_persisted(), timeout=timeout_seconds)

    async def resume(self, run_id: str) -> AgentRun:
        self._ensure_accepting()
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return await existing
        run = self.store.get_run(run_id)
        self._ensure_run_capacity()
        if run.status not in {
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.WAITING_FOR_APPROVAL,
        }:
            raise ValueError(
                f"Run {run_id} cannot be resumed from status {run.status}."
            )
        if run.metadata.get("run_kind") == "workflow":
            async def execute_workflow() -> AgentRun:
                return (await self.resume_workflow(run_id)).parent

            task = asyncio.create_task(execute_workflow())
        else:
            task = asyncio.create_task(self._execute(run_id))
        self.track_task(run_id, task)
        return await task

    async def resume_workflow(self, run_id: str) -> WorkflowExecution:
        """Resume a durable workflow from its persisted normalized definition."""
        run = self.store.get_run(run_id)
        if run.metadata.get("run_kind") != "workflow":
            raise ValueError(f"Run {run_id} is not a workflow run.")
        snapshot = self.store.workflow_snapshot(run_id)
        if snapshot is None:
            raise ValueError(f"Workflow run {run_id} has no persisted definition snapshot.")
        workflow_type = snapshot.get("type")
        raw_steps = snapshot.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"Workflow run {run_id} has an invalid step snapshot.")
        from .orchestration import (
            AggregationStrategy,
            ParallelWorkflow,
            SequentialWorkflow,
            WorkflowStep,
        )

        steps: list[WorkflowStep] = []
        for step in raw_steps:
            if not isinstance(step, dict) or not step.get("agent_name"):
                continue
            checksum = step.get("agent_definition_checksum")
            agent_reference: AgentDefinition | str = str(step["agent_name"])
            if isinstance(checksum, str) and checksum:
                agent_reference = self._agent_from_checksum(checksum)
            steps.append(
                WorkflowStep(
                    agent_reference,
                    name=step.get("name"),
                    input_prefix=str(step.get("input_prefix") or ""),
                )
            )
        if len(steps) != len(raw_steps):
            raise ValueError(f"Workflow run {run_id} contains an invalid step definition.")
        name = str(snapshot.get("name") or run.metadata.get("workflow_name") or run.agent_name)
        workflow: SequentialWorkflow | ParallelWorkflow
        if workflow_type == "sequential":
            workflow = SequentialWorkflow(name, steps)
        elif workflow_type == "parallel":
            workflow = ParallelWorkflow(
                name,
                steps,
                aggregation=AggregationStrategy(str(snapshot.get("aggregation", "all"))),
                max_concurrency=int(snapshot.get("max_concurrency", 4)),
                timeout_seconds=(
                    float(snapshot["timeout_seconds"])
                    if snapshot.get("timeout_seconds") is not None
                    else None
                ),
            )
        else:
            raise ValueError(
                f"Workflow run {run_id} has unsupported snapshot type {workflow_type!r}."
            )
        return await workflow.run(
            self,
            run.input,
            metadata=run.metadata,
            parent_run_id=run.id,
        )

    def pause(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.metadata.get("run_kind") == "workflow":
            raise ValueError(
                "Workflow pause is not supported; cancel it or resume it through "
                "the original Workflow definition after a process restart."
            )
        if run.status != RunStatus.RUNNING:
            raise ValueError(
                f"Only running runs can be paused; run {run_id} is {run.status}."
            )
        run.transition_to(RunStatus.PAUSED)
        self.store.save_run_with_event(run, "run.paused", {"reason": "user_requested"})
        self._pause_run_ids.add(run_id)
        token = self._cancellation_tokens.get(run_id)
        if token is not None:
            token.cancel()
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        return run

    def cancel(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return run
        self.cancel_children(run_id)
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
        resolution: UnknownToolResolution | str,
        *,
        result_content: str | None = None,
        result_data: dict[str, Any] | None = None,
        error: str | None = None,
        reason: str | None = None,
        resolved_by: str = "local-user",
    ) -> ToolExecution:
        execution = self.store.get_tool_execution(execution_id)
        if execution.status != ToolExecutionStatus.UNKNOWN:
            raise ValueError(
                f"Tool execution {execution_id} is {execution.status}, not unknown."
            )
        aliases = {
            "completed": UnknownToolResolution.CONFIRMED_SUCCEEDED,
            "failed": UnknownToolResolution.CONFIRMED_FAILED,
        }
        if str(resolution) == "retry":
            raise ValueError(
                "UNKNOWN side-effecting tool executions cannot be retried automatically; "
                "confirm succeeded or failed, then explicitly resume the Run."
            )
        normalized = aliases.get(str(resolution))
        if normalized is None:
            try:
                normalized = UnknownToolResolution(str(resolution))
            except ValueError as exc:
                raise ValueError(f"Unsupported unknown-tool resolution: {resolution}") from exc
        audit_reason = (reason or error or "Human reviewed the uncertain side effect.").strip()
        if not audit_reason:
            raise ValueError("reason must not be empty when confirming an UNKNOWN outcome.")
        actor = resolved_by.strip()
        if not actor:
            raise ValueError("resolved_by must not be empty.")
        now = utc_now()
        execution.resolution = normalized
        execution.resolution_reason = audit_reason
        execution.resolved_by = actor
        execution.resolved_at = now
        execution.completed_at = now
        if normalized is UnknownToolResolution.CONFIRMED_SUCCEEDED:
            execution.status = ToolExecutionStatus.COMPLETED
            execution.result_content = result_content or (
                "Human review confirmed that the side effect completed."
            )
            execution.result_data = result_data
            execution.error = None
        else:
            execution.status = ToolExecutionStatus.FAILED
            execution.result_content = result_content
            execution.result_data = result_data
            execution.error = error or audit_reason
        run = self.store.get_run(execution.run_id)
        self.store.resolve_unknown_execution(execution, run, normalized)
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
        agent = self._resolve_run_agent(run)
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
            interruption_reason = None
            if run_id in self._shutdown_run_ids:
                interruption_reason = "runtime_shutdown"
            elif run_id in self._pause_run_ids:
                interruption_reason = "user_requested"
            if interruption_reason is not None:
                recovered = self.store.mark_running_tool_executions_unknown(run_id)
                if run.status is RunStatus.RUNNING:
                    run.transition_to(RunStatus.PAUSED)
                    self.store.save_run_with_event(
                        run,
                        "run.paused",
                        {
                            "reason": interruption_reason,
                            "tool_execution_ids": [
                                item.id
                                for item in recovered
                                if item.status is ToolExecutionStatus.UNKNOWN
                            ],
                        },
                    )
                return run
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
            self._shutdown_run_ids.discard(run_id)
            self._pause_run_ids.discard(run_id)

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
        result_content, result_data = self._artifactize_tool_result(
            run, execution, result.content, result.data
        )
        execution.result_content = result_content
        execution.result_data = result_data
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
                "content": execution.result_content,
                "artifact": (execution.result_data or {}).get("_artifact"),
            },
            run=run,
            checkpoint=checkpoint,
        )

    def _artifactize_tool_result(
        self,
        run: AgentRun,
        execution: ToolExecution,
        content: str,
        data: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        if len(content) <= self.config.large_tool_result_chars:
            return content, data
        path = self.artifacts.write_text(
            run.id,
            f"tool-results/{execution.id}.txt",
            content,
        )
        preview_chars = self.config.large_tool_result_preview_chars
        preview = content[:preview_chars]
        artifact = {
            "path": str(path),
            "characters": len(content),
            "preview_characters": len(preview),
        }
        result_data = {**(data or {}), "_artifact": artifact}
        replacement = (
            f"[Tool result stored as artifact: {path}; characters={len(content)}]"
            f"\nPreview:\n{preview}"
        )
        self._event(
            run.id,
            "tool.result.artifactized",
            {
                "tool_execution_id": execution.id,
                "tool_call_id": execution.tool_call.id,
                **artifact,
            },
        )
        return replacement, result_data

    def _after_tool_handler(self, execution: ToolExecution) -> None:
        """Failure-injection seam: called after a handler returns, before durable completion."""

    async def _request_model(
        self,
        run: AgentRun,
        messages: list[Message],
        agent: AgentDefinition,
        step_index: int,
    ) -> ModelResponse:
        context = self._build_model_context(run, messages, agent)
        model_messages = context.messages
        last_error: Exception | None = None
        for attempt in range(self.config.max_model_retries + 1):
            try:
                async with self._model_capacity:
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
                                    model_messages,
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
                    complete = getattr(self.provider, "complete", None)
                    if not callable(complete):
                        raise ProviderError(
                            "Model provider implements neither stream nor complete."
                        )
                    return await asyncio.wait_for(
                        complete(
                            model_messages,
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
                retryable = not isinstance(error, ProviderError) or error.retryable
                if attempt >= self.config.max_model_retries or not retryable:
                    break
                retry_after = (
                    error.retry_after_seconds
                    if isinstance(error, ProviderHTTPError)
                    else None
                )
                delay = retry_after if retry_after is not None else 0.25 * (2**attempt)
                await asyncio.sleep(delay + random.uniform(0.0, min(0.25, delay * 0.2)))
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

    def _build_model_context(
        self,
        run: AgentRun,
        messages: list[Message],
        agent: AgentDefinition,
    ) -> ContextBuildResult:
        session_id = run.metadata.get("session_id")
        scopes: list[tuple[MemoryScope, str]] = [(MemoryScope.AGENT, agent.name)]
        if session_id is not None:
            scopes.insert(0, (MemoryScope.SESSION, str(session_id)))
        memories: list[MemorySearchResult] = []
        if self.config.memory_search_limit and self.memory.has_active_memories(scopes):
            query = next(
                (
                    message.content
                    for message in reversed(messages)
                    if message.role == "user" and message.content
                ),
                run.input,
            )
            memories = self.search_memory(
                query,
                session_id=str(session_id) if session_id is not None else None,
                agent_name=agent.name,
                limit=self.config.memory_search_limit,
                run_id=run.id,
            )
        result = self.context_builder.build(messages, memories=memories)
        self._event(run.id, "context.built", result.to_event_payload())
        if result.compacted:
            self._event(
                run.id,
                "context.compacted",
                result.to_event_payload(),
            )
        return result

    def _load_messages(self, run: AgentRun) -> list[Message]:
        checkpoint = self.store.latest_checkpoint(run.id)
        if checkpoint is None:
            return [
                Message(
                    role="system",
                    content=self._resolve_run_agent(run).system_prompt,
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

    def _agent_from_checksum(self, checksum: str) -> AgentDefinition:
        try:
            agent = self.store.get_agent_definition(checksum)
        except KeyError as error:
            raise AgentDefinitionUnavailable(str(error)) from error
        try:
            self.tools.definitions_for(agent.tools)
        except ToolExecutionError as error:
            raise AgentDefinitionUnavailable(
                f"AgentDefinition {agent.name!r} requires unavailable tool handlers: {error}"
            ) from error
        return agent

    def _resolve_run_agent(self, run: AgentRun) -> AgentDefinition:
        checksum = run.metadata.get("agent_definition_checksum")
        if isinstance(checksum, str) and checksum:
            return self._agent_from_checksum(checksum)
        return self._resolve_agent(run.agent_name)

    def _freeze_workflow_definition(
        self, definition: dict[str, Any]
    ) -> dict[str, Any]:
        frozen = cast(
            dict[str, Any], json.loads(json.dumps(definition, ensure_ascii=False))
        )
        raw_steps = frozen.get("steps")
        if not isinstance(raw_steps, list):
            return frozen
        for step in raw_steps:
            if not isinstance(step, dict) or not step.get("agent_name"):
                continue
            if step.get("agent_definition_checksum"):
                continue
            agent = self._resolve_agent(str(step["agent_name"]))
            step["agent_definition_checksum"] = self.store.save_agent_definition(agent)
        return frozen

    @staticmethod
    def _normalize_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Idempotency key must not be blank.")
        if len(normalized) > 200:
            raise ValueError("Idempotency key must not exceed 200 characters.")
        return normalized

    @staticmethod
    def _request_fingerprint(
        agent_name: str,
        input_text: str,
        metadata: dict[str, Any],
        session_id: object | None,
    ) -> str:
        canonical = json.dumps(
            {
                "agent_name": agent_name,
                "input": input_text,
                "metadata": metadata,
                "session_id": str(session_id) if session_id is not None else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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
            return self.agent_registry.get(agent)
        except KeyError:
            persisted = self.store.latest_agent_definition(agent)
            if persisted is None:
                raise
            try:
                self.register_agent(persisted)
            except (ToolExecutionError, ValueError) as error:
                raise AgentDefinitionUnavailable(
                    f"Persisted AgentDefinition {agent!r} cannot be restored: {error}"
                ) from error
            return persisted

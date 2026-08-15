from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from ..domain import AgentRun, Approval, MemoryScope, RunNotFound, RuntimeEvent
from ..observability import ObservabilityService
from ..runtime import Runtime
from ..sdk import create_local_runtime, demo_agent

try:
    from fastapi import FastAPI, HTTPException, Query, Request, status
    from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
    from pydantic import BaseModel, Field
except ImportError as error:  # pragma: no cover - exercised when the optional extra is absent
    raise ImportError(
        "FastAPI API support is optional. Install it with `pip install -e .[api]`."
    ) from error


class CreateRunRequest(BaseModel):
    agent_name: str = "demo"
    input: str = Field(min_length=1)
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateMemoryRequest(BaseModel):
    content: str = Field(min_length=1)
    scope: str
    scope_id: str = Field(min_length=1)
    source_run_id: str | None = None
    ttl_seconds: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateDelegationRequest(BaseModel):
    agent_name: str
    input: str = Field(min_length=1)
    delegation_key: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalResolutionRequest(BaseModel):
    approved: bool
    reason: str | None = None


class ErrorResponse(BaseModel):
    detail: str


def _run_payload(run: AgentRun) -> dict[str, Any]:
    return run.to_dict()


def _approval_payload(approval: Approval) -> dict[str, Any]:
    return {
        "id": approval.id,
        "run_id": approval.run_id,
        "tool_execution_id": approval.tool_execution_id,
        "kind": approval.kind,
        "status": approval.status,
        "reason": approval.reason,
        "created_at": approval.created_at.isoformat(),
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        "tool_call": {
            "id": approval.tool_call.id,
            "name": approval.tool_call.name,
            "arguments": approval.tool_call.arguments,
        },
    }


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def encode_sse(event: RuntimeEvent) -> str:
    """Encode one durable RuntimeEvent as an SSE record."""
    data = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n"


def create_app(
    runtime: Runtime,
    *,
    default_agent: str = "demo",
    enable_learning_console: bool = False,
) -> FastAPI:
    """Create a FastAPI adapter around an existing Runtime instance.

    The adapter owns HTTP concerns only. It delegates persistence, lifecycle,
    approvals and execution to Runtime and SQLiteStore.
    """
    app = FastAPI(
        title="Agent Runtime API",
        version="0.7.2",
        description="HTTP and SSE adapter for the durable Agent Runtime kernel.",
    )
    app.state.runtime = runtime
    app.state.default_agent = default_agent

    @app.exception_handler(KeyError)
    async def handle_key_error(_: Request, error: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(RunNotFound)
    async def handle_run_not_found(_: Request, error: RunNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "runtime": "agent-runtime", "version": "0.7.2"}

    @app.post("/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
        return runtime.create_session(request.metadata).to_dict()

    @app.get("/sessions")
    async def list_sessions(limit: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
        return [session.to_dict() for session in runtime.store.list_sessions(limit)]

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return runtime.store.get_session(session_id).to_dict()

    @app.get("/sessions/{session_id}/runs")
    async def get_session_runs(session_id: str) -> list[dict[str, Any]]:
        return [run.to_dict() for run in runtime.session_runs(session_id)]

    @app.post("/memories", status_code=status.HTTP_201_CREATED)
    async def create_memory(request: CreateMemoryRequest) -> dict[str, Any]:
        try:
            return runtime.remember(
                request.content,
                scope=MemoryScope(request.scope),
                scope_id=request.scope_id,
                source_run_id=request.source_run_id,
                ttl_seconds=request.ttl_seconds,
                metadata=request.metadata,
            ).to_dict()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/memories/search")
    async def search_memories(
        query: str = Query(min_length=1),
        session_id: str | None = None,
        agent_name: str | None = None,
        limit: int = Query(5, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        try:
            return [
                result.to_dict()
                for result in runtime.search_memory(
                    query,
                    session_id=session_id,
                    agent_name=agent_name,
                    limit=limit,
                )
            ]
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/memories/{memory_id}")
    async def delete_memory(memory_id: str) -> dict[str, Any]:
        return runtime.forget_memory(memory_id).to_dict()

    @app.post("/memories/purge-expired")
    async def purge_expired_memories() -> dict[str, int]:
        return {"purged": runtime.purge_expired_memories()}

    @app.get("/agents")
    async def list_agents() -> list[dict[str, Any]]:
        return [
            {
                "name": agent.name,
                "model": agent.model.model,
                "provider": agent.model.provider,
                "tools": [tool.name for tool in agent.tools],
                "max_steps": agent.max_steps,
                "max_tool_calls": agent.max_tool_calls,
            }
            for agent in runtime.list_agents()
        ]

    @app.get("/observability/metrics")
    async def observability_metrics(limit: int = Query(1000, ge=1, le=10000)) -> dict[str, Any]:
        return ObservabilityService(runtime.store).metrics(limit=limit).to_dict()

    @app.get("/observability/metrics/prometheus")
    async def prometheus_metrics(
        limit: int = Query(1000, ge=1, le=10000),
    ) -> PlainTextResponse:
        content = ObservabilityService(runtime.store).metrics(limit=limit).to_prometheus()
        return PlainTextResponse(content, media_type="text/plain; version=0.0.4")

    @app.get("/runs/{run_id}/trace")
    async def get_run_trace(run_id: str) -> dict[str, Any]:
        try:
            return ObservabilityService(runtime.store).trace(run_id).to_dict()
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error

    @app.get("/runs/{run_id}/trace/tree")
    async def get_trace_tree(run_id: str) -> dict[str, Any]:
        try:
            return ObservabilityService(runtime.store).trace_tree(run_id).to_dict()
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error

    @app.get("/runs/{run_id}/relations")
    async def get_run_relations(run_id: str) -> dict[str, Any]:
        try:
            root_run_id = runtime.store.root_run_id(run_id)
            return {
                "root_run_id": root_run_id,
                "parent_relation": (
                    runtime.store.get_run_relation(run_id).to_dict()
                    if runtime.store.get_run_relation(run_id)
                    else None
                ),
                "children": [
                    relation.to_dict()
                    for relation in runtime.store.child_relations(run_id)
                ],
            }
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error

    @app.post("/runs/{parent_run_id}/delegations")
    async def create_delegation(
        parent_run_id: str, request: CreateDelegationRequest
    ) -> dict[str, Any]:
        try:
            child = await runtime.delegate(
                parent_run_id,
                request.agent_name,
                request.input,
                delegation_key=request.delegation_key,
                metadata=request.metadata,
            )
            relation = runtime.store.get_run_relation(child.id)
            return {
                "run": child.to_dict(),
                "relation": relation.to_dict() if relation else None,
            }
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(request: CreateRunRequest) -> dict[str, Any]:
        agent_name = request.agent_name or app.state.default_agent
        try:
            run = runtime.start(
                agent_name,
                request.input,
                request.metadata,
                session_id=request.session_id,
            )
        except (KeyError, ValueError) as error:
            raise _not_found(error) from error
        return _run_payload(run)

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        try:
            return _run_payload(runtime.store.get_run(run_id))
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error

    @app.get("/runs/{run_id}/events")
    async def list_events(run_id: str, after_sequence: int = Query(0, ge=0)) -> list[dict[str, Any]]:
        try:
            runtime.store.get_run(run_id)
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        return [event.to_dict() for event in runtime.store.events_since(run_id, after_sequence)]

    @app.get("/runs/{run_id}/events/stream")
    async def stream_events(run_id: str, after_sequence: int = Query(0, ge=0)) -> StreamingResponse:
        try:
            runtime.store.get_run(run_id)
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error

        async def generate() -> AsyncIterator[str]:
            async for event in runtime.stream(run_id, after_sequence=after_sequence):
                yield encode_sse(event)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/runs/{run_id}/pause")
    async def pause_run(run_id: str) -> dict[str, Any]:
        try:
            return _run_payload(runtime.pause(run_id))
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict[str, Any]:
        try:
            run = await runtime.resume(run_id)
            return _run_payload(run)
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            return _run_payload(runtime.cancel(run_id))
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/runs/{run_id}/approvals/pending")
    async def pending_approval(run_id: str) -> dict[str, Any] | None:
        try:
            runtime.store.get_run(run_id)
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        approval = runtime.store.pending_approval(run_id)
        return _approval_payload(approval) if approval else None

    @app.post("/approvals/{approval_id}/resolve")
    async def resolve_approval(
        approval_id: str, request: ApprovalResolutionRequest
    ) -> dict[str, Any]:
        try:
            existing = runtime.store.get_approval(approval_id)
            approval = runtime.resolve_approval(approval_id, request.approved, request.reason)
            run = runtime.store.get_run(existing.run_id)
            if run.status.value == "waiting_for_approval" and approval.status != "pending":
                run = await runtime.resume(run.id)
            else:
                run = runtime.store.get_run(existing.run_id)
            return {"approval": _approval_payload(approval), "run": _run_payload(run)}
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    if enable_learning_console:
        from ..lab import LearningConsole, install_learning_console

        install_learning_console(app, LearningConsole(runtime))

    return app


def create_demo_app(
    workspace: str = ".",
    state_dir: str | None = None,
    *,
    enable_learning_console: bool = True,
) -> FastAPI:
    """Create a ready-to-run API backed by the deterministic demo agent."""
    runtime = create_local_runtime(workspace, state_dir)
    runtime.register_agent(demo_agent())
    return create_app(runtime, enable_learning_console=enable_learning_console)


async def run_until_complete(runtime: Runtime, run_id: str) -> AgentRun:
    """Small helper for integrations that need an explicit awaitable."""
    await asyncio.sleep(0)
    return await runtime.wait(run_id)


# Uvicorn-friendly default app for local learning and smoke tests.
app = create_demo_app()

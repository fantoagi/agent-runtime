from __future__ import annotations

import asyncio
import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..doctor import RuntimeDoctor
from ..domain import (
    AgentDefinitionUnavailable,
    AgentRun,
    Approval,
    IdempotencyConflict,
    MemoryScope,
    RunNotFound,
    RuntimeCapacityError,
    RuntimeClosedError,
    RuntimeEvent,
    StoreBusyError,
    StoreError,
    ToolExecution,
)
from ..incident import IncidentDiagnosticsService
from ..observability import ObservabilityService
from ..runtime import Runtime
from ..sdk import create_local_runtime, demo_agent
from ..version import __version__

try:
    from fastapi import FastAPI, HTTPException, Query, Request, Response, status
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
    from pydantic import BaseModel, Field
    from starlette.types import ASGIApp, Receive, Scope, Send
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


class UnknownToolResolutionRequest(BaseModel):
    resolution: str
    reason: str = Field(min_length=1)
    resolved_by: str = Field(default="api-user", min_length=1)
    result_content: str | None = None
    result_data: dict[str, Any] | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    retryable: bool = False


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


def _tool_execution_payload(execution: ToolExecution) -> dict[str, Any]:
    return {
        "id": execution.id,
        "run_id": execution.run_id,
        "step_id": execution.step_id,
        "status": execution.status.value,
        "tool_call": {
            "id": execution.tool_call.id,
            "name": execution.tool_call.name,
            "arguments": execution.tool_call.arguments,
        },
        "result_content": execution.result_content,
        "result_data": execution.result_data,
        "error": execution.error,
        "resolution": execution.resolution.value if execution.resolution else None,
        "resolution_reason": execution.resolution_reason,
        "resolved_by": execution.resolved_by,
        "resolved_at": execution.resolved_at.isoformat() if execution.resolved_at else None,
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
    shutdown_runtime: bool = False,
) -> FastAPI:
    """Create a FastAPI adapter around an existing Runtime instance.

    The adapter owns HTTP concerns only. It delegates persistence, lifecycle,
    approvals and execution to Runtime and SQLiteStore.
    """
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            runtime.store.health_check()
            yield
        finally:
            if shutdown_runtime:
                await runtime.shutdown()

    app = FastAPI(
        title="Agent Runtime API",
        version=__version__,
        description="HTTP and SSE adapter for the durable Agent Runtime kernel.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.default_agent = default_agent

    @app.exception_handler(HTTPException)
    async def handle_http_error(_: Request, error: HTTPException) -> JSONResponse:
        code_by_status = {
            400: "invalid_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "validation_error",
            429: "rate_limited",
            503: "service_unavailable",
        }
        return JSONResponse(
            status_code=error.status_code,
            content={
                "detail": error.detail,
                "code": code_by_status.get(error.status_code, "http_error"),
                "retryable": error.status_code in {408, 429} or error.status_code >= 500,
            },
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": error.errors(),
                "code": "validation_error",
                "retryable": False,
            },
        )

    @app.exception_handler(KeyError)
    async def handle_key_error(_: Request, error: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "detail": str(error),
                "code": "not_found",
                "retryable": False,
            },
        )

    @app.exception_handler(RunNotFound)
    async def handle_run_not_found(_: Request, error: RunNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "detail": str(error),
                "code": "run_not_found",
                "retryable": False,
            },
        )

    @app.exception_handler(StoreError)
    async def handle_store_error(_: Request, error: StoreError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": str(error),
                "code": "store_busy" if isinstance(error, StoreBusyError) else "store_unavailable",
                "retryable": isinstance(error, StoreBusyError),
            },
        )

    @app.exception_handler(RuntimeClosedError)
    async def handle_runtime_closed(_: Request, error: RuntimeClosedError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": str(error),
                "code": "runtime_unavailable",
                "retryable": True,
            },
        )

    @app.exception_handler(RuntimeCapacityError)
    async def handle_runtime_capacity(
        _: Request, error: RuntimeCapacityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "detail": str(error),
                "code": "runtime_capacity_exhausted",
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(IdempotencyConflict)
    async def handle_idempotency_conflict(
        _: Request, error: IdempotencyConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(error),
                "code": "idempotency_conflict",
                "retryable": False,
            },
        )

    @app.exception_handler(AgentDefinitionUnavailable)
    async def handle_agent_definition_unavailable(
        _: Request, error: AgentDefinitionUnavailable
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(error),
                "code": "agent_definition_unavailable",
                "retryable": False,
            },
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        if not runtime.is_accepting:
            raise HTTPException(
                status_code=503,
                detail="Runtime is shutting down or closed.",
            )
        store = runtime.store.health_check()
        return {
            "status": "ok",
            "runtime": "agent-runtime",
            "version": __version__,
            "store": store,
            "capacity": runtime.capacity_snapshot(),
        }

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

    @app.get("/observability/diagnostics")
    async def observability_diagnostics(
        limit: int = Query(1000, ge=1, le=10000),
        recent_failure_limit: int = Query(20, ge=0, le=100),
    ) -> dict[str, Any]:
        return ObservabilityService(runtime.store).diagnostics(
            runtime,
            metrics_limit=limit,
            recent_failure_limit=recent_failure_limit,
        ).to_dict()

    @app.get("/observability/incident-bundle")
    async def observability_incident_bundle(
        run_id: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        recent_failure_limit: int = Query(20, ge=0, le=100),
        event_limit: int = Query(5000, ge=0, le=50000),
    ) -> StreamingResponse:
        incidents = IncidentDiagnosticsService(runtime)
        try:
            content, _ = incidents.bundle_bytes(
                run_id=run_id,
                run_limit=limit,
                recent_failure_limit=recent_failure_limit,
                event_limit=event_limit,
            )
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{incidents.suggested_filename()}"'
                ),
                "Cache-Control": "no-store",
            },
        )

    @app.get("/observability/sandbox")
    async def sandbox_status() -> dict[str, Any]:
        return runtime.sandbox_snapshot()

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
            parent_relation = runtime.store.get_run_relation(run_id)
            return {
                "root_run_id": root_run_id,
                "parent_relation": parent_relation.to_dict() if parent_relation else None,
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
    async def create_run(
        payload: CreateRunRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        agent_name = payload.agent_name or app.state.default_agent
        try:
            submission = runtime.submit(
                agent_name,
                payload.input,
                payload.metadata,
                session_id=payload.session_id,
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except KeyError as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        response.headers["Idempotent-Replayed"] = str(submission.replayed).lower()
        return _run_payload(submission.run)

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
    async def stream_events(
        request: Request,
        run_id: str,
        after_sequence: int = Query(0, ge=0),
    ) -> StreamingResponse:
        try:
            runtime.store.get_run(run_id)
        except (KeyError, RunNotFound) as error:
            raise _not_found(error) from error
        last_event_id = request.headers.get("Last-Event-ID")
        if after_sequence == 0 and last_event_id:
            try:
                after_sequence = max(0, int(last_event_id))
            except ValueError as error:
                raise HTTPException(
                    status_code=400, detail="Last-Event-ID must be an integer."
                ) from error

        async def generate() -> AsyncIterator[str]:
            iterator = runtime.stream(run_id, after_sequence=after_sequence).__aiter__()
            pending: asyncio.Task[RuntimeEvent] | None = None

            async def next_event() -> RuntimeEvent:
                return await anext(iterator)
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    if pending is None:
                        pending = asyncio.create_task(next_event())
                    done, _ = await asyncio.wait(
                        {pending}, timeout=runtime.config.sse_heartbeat_seconds
                    )
                    if not done:
                        yield ": heartbeat\n\n"
                        continue
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        return
                    finally:
                        pending = None
                    yield encode_sse(event)
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                close_iterator = getattr(iterator, "aclose", None)
                if callable(close_iterator):
                    await close_iterator()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
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

    @app.get("/doctor")
    async def doctor(run_id: str | None = None) -> dict[str, Any]:
        report = RuntimeDoctor(runtime.store).run(run_id)
        return report.to_dict()

    @app.post("/tool-executions/{execution_id}/resolve-unknown")
    async def resolve_unknown_tool(
        execution_id: str, request: UnknownToolResolutionRequest
    ) -> dict[str, Any]:
        try:
            execution = runtime.resolve_unknown_tool(
                execution_id,
                request.resolution,
                result_content=request.result_content,
                result_data=request.result_data,
                error=request.error,
                reason=request.reason,
                resolved_by=request.resolved_by,
            )
            run = runtime.store.get_run(execution.run_id)
            return {"tool_execution": _tool_execution_payload(execution), "run": _run_payload(run)}
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
    return create_app(
        runtime,
        enable_learning_console=enable_learning_console,
        shutdown_runtime=True,
    )


async def run_until_complete(runtime: Runtime, run_id: str) -> AgentRun:
    """Small helper for integrations that need an explicit awaitable."""
    await asyncio.sleep(0)
    return await runtime.wait(run_id)


class _LazyDemoApplication:
    """Delay demo Runtime construction until the ASGI server actually starts."""

    def __init__(self) -> None:
        self._app: FastAPI | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            self._app = create_demo_app()
        await self._app(scope, receive, send)


# Uvicorn-friendly default app without import-time SQLite or Runtime side effects.
app: ASGIApp = _LazyDemoApplication()

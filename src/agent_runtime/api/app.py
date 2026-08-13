from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from ..domain import AgentRun, Approval, RunNotFound, RuntimeEvent
from ..runtime import Runtime
from ..sdk import create_local_runtime, demo_agent

try:
    from fastapi import FastAPI, HTTPException, Query, Request, status
    from fastapi.responses import JSONResponse, StreamingResponse
    from pydantic import BaseModel, Field
except ImportError as error:  # pragma: no cover - exercised when the optional extra is absent
    raise ImportError(
        "FastAPI API support is optional. Install it with `pip install -e .[api]`."
    ) from error


class CreateRunRequest(BaseModel):
    agent_name: str = "demo"
    input: str = Field(min_length=1)
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


def create_app(runtime: Runtime, *, default_agent: str = "demo") -> FastAPI:
    """Create a FastAPI adapter around an existing Runtime instance.

    The adapter owns HTTP concerns only. It delegates persistence, lifecycle,
    approvals and execution to Runtime and SQLiteStore.
    """
    app = FastAPI(
        title="Agent Runtime API",
        version="0.3.0",
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
        return {"status": "ok", "runtime": "agent-runtime", "version": "0.3.0"}

    @app.post("/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(request: CreateRunRequest) -> dict[str, Any]:
        agent_name = request.agent_name or app.state.default_agent
        try:
            run = runtime.start(agent_name, request.input, request.metadata)
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

    return app


def create_demo_app(workspace: str = ".", state_dir: str | None = None) -> FastAPI:
    """Create a ready-to-run API backed by the deterministic demo agent."""
    runtime = create_local_runtime(workspace, state_dir)
    runtime.register_agent(demo_agent())
    return create_app(runtime)


async def run_until_complete(runtime: Runtime, run_id: str) -> AgentRun:
    """Small helper for integrations that need an explicit awaitable."""
    await asyncio.sleep(0)
    return await runtime.wait(run_id)


# Uvicorn-friendly default app for local learning and smoke tests.
app = create_demo_app()

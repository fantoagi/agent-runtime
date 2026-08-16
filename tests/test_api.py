from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from agent_runtime.api import create_app, encode_sse
from agent_runtime.domain import (
    AgentDefinition,
    ModelConfig,
    RunStatus,
    RuntimeEvent,
    Step,
    ToolCall,
    ToolExecution,
    ToolExecutionStatus,
)
from agent_runtime.orchestration import SequentialWorkflow
from agent_runtime.providers import (
    MockProvider,
    MockStreamingProvider,
    ModelResponse,
    ModelTokenDelta,
)
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.sdk import demo_agent
from agent_runtime.tools import ToolRegistry, register_builtin_tools


def make_api_runtime(workspace: Path) -> Runtime:
    tools = ToolRegistry()
    register_builtin_tools(tools)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            event_poll_interval_seconds=0.01,
        ),
        provider=MockProvider(
            lambda messages, tools, config: ModelResponse(content=f"answer: {messages[-1].content}")
        ),
        tools=tools,
    )
    runtime.register_agent(demo_agent())
    return runtime


@pytest.mark.asyncio
async def test_health_create_get_and_events(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.7.11"
        assert health.json()["store"]["schema_version"] == 8

        invalid = await client.post("/runs", json={"input": ""})
        assert invalid.status_code == 422
        assert invalid.json()["detail"]
        assert invalid.json()["code"] == "validation_error"
        assert invalid.json()["retryable"] is False

        created = await client.post(
            "/runs", json={"agent_name": "demo", "input": "hello", "metadata": {"source": "test"}}
        )
        assert created.status_code == 202
        run_id = created.json()["id"]
        assert created.json()["status"] == "created"

        completed = await runtime.wait(run_id)
        assert completed.status.value == "completed"

        fetched = await client.get(f"/runs/{run_id}")
        assert fetched.status_code == 200
        assert fetched.json()["result"] == "answer: hello"

        events = await client.get(f"/runs/{run_id}/events")
        assert events.status_code == 200
        payload = events.json()
        assert [item["sequence"] for item in payload] == sorted(item["sequence"] for item in payload)
        assert payload[0]["type"] == "run.created"

        after_first = await client.get(f"/runs/{run_id}/events?after_sequence=1")
        assert all(item["sequence"] > 1 for item in after_first.json())


@pytest.mark.asyncio
async def test_sse_contains_durable_events_and_closes_after_completion(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/runs", json={"input": "stream me"})
        run_id = created.json()["id"]
        await runtime.wait(run_id)

        response = await client.get(f"/runs/{run_id}/events/stream")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "id: 1\n" in response.text
        assert "event: run.created\n" in response.text
        assert "data: {" in response.text
        assert response.text.endswith("\n\n")

        resumed = await client.get(f"/runs/{run_id}/events/stream?after_sequence=1")
        assert "id: 1\n" not in resumed.text

        invalid_cursor = await client.get(
            f"/runs/{run_id}/events/stream", headers={"Last-Event-ID": "invalid"}
        )
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json() == {
            "detail": "Last-Event-ID must be an integer.",
            "code": "invalid_request",
            "retryable": False,
        }


@pytest.mark.asyncio
async def test_lifecycle_endpoints_and_unknown_run(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/runs/run_missing")
        assert missing.status_code == 404
        assert missing.json()["detail"]
        assert missing.json()["code"] == "not_found"
        assert missing.json()["retryable"] is False

        created = await client.post("/runs", json={"input": "control"})
        run_id = created.json()["id"]
        paused = await client.post(f"/runs/{run_id}/pause")
        assert paused.status_code == 409 or paused.json()["status"] in {"paused", "cancelled", "completed"}

        cancelled = await client.post(f"/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] in {"cancelled", "completed"}
        try:
            await runtime.wait(run_id)
        except asyncio.CancelledError:
            pass


def test_encode_sse_uses_sequence_and_json_payload() -> None:
    event = RuntimeEvent.create("run_1", 7, "tool.completed", {"text": "中文"})
    encoded = encode_sse(event)
    assert encoded.startswith("id: 7\nevent: tool.completed\ndata: ")
    assert encoded.endswith("\n\n")
    data_line = encoded.splitlines()[2][len("data: ") :]
    assert json.loads(data_line)["payload"]["text"] == "中文"


@pytest.mark.asyncio
async def test_sse_exposes_model_token_delta_events(workspace: Path) -> None:
    tools = ToolRegistry()
    register_builtin_tools(tools)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "streaming.sqlite3",
            event_poll_interval_seconds=0.01,
        ),
        provider=MockStreamingProvider(
            [
                ModelTokenDelta(content="hello "),
                ModelTokenDelta(content="stream", finish_reason="stop"),
            ]
        ),
        tools=tools,
    )
    runtime.register_agent(demo_agent())
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/runs", json={"input": "stream"})
        run_id = created.json()["id"]
        await runtime.wait(run_id)
        response = await client.get(f"/runs/{run_id}/events/stream")

    assert response.status_code == 200
    assert response.text.count("event: model.delta\n") == 2
    assert '"content":"hello "' in response.text
    assert '"content":"stream"' in response.text


@pytest.mark.asyncio
async def test_observability_api_exposes_trace_and_metrics(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/runs", json={"input": "observe me"})
        run_id = created.json()["id"]
        await runtime.wait(run_id)

        trace = await client.get(f"/runs/{run_id}/trace")
        metrics = await client.get("/observability/metrics")
        prometheus = await client.get("/observability/metrics/prometheus")
        diagnostics = await client.get("/observability/diagnostics")

    assert trace.status_code == 200
    assert trace.json()["run_id"] == run_id
    assert trace.json()["trace_id"].startswith("trace_")
    assert any(span["kind"] == "model" for span in trace.json()["spans"])
    assert metrics.status_code == 200
    assert metrics.json()["total_runs"] == 1
    assert metrics.json()["model_requests"] == 1
    assert diagnostics.status_code == 200
    assert diagnostics.json()["version"] == "0.7.11"
    assert diagnostics.json()["runtime"]["state"] == "accepting"
    assert diagnostics.json()["store"]["status"] == "ok"
    assert diagnostics.json()["process"]["thread_count"] >= 1
    assert prometheus.status_code == 200
    assert "agent_runtime_runs_total" in prometheus.text

@pytest.mark.asyncio
async def test_multi_agent_registry_relations_and_trace_tree_api(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    planner = AgentDefinition(
        name="planner",
        system_prompt="planner",
        tools=[],
        model=ModelConfig(provider="mock", model="test"),
    )
    worker = AgentDefinition(
        name="worker",
        system_prompt="worker",
        tools=[],
        model=ModelConfig(provider="mock", model="test"),
    )
    runtime.register_agent(planner)
    runtime.register_agent(worker)
    execution = await SequentialWorkflow("api-flow", ["planner", "worker"]).run(
        runtime, "task"
    )
    manual_parent = runtime.begin_workflow(
        "api-manual", "manual task", workflow_type="manual"
    )

    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        agents = await client.get("/agents")
        relations = await client.get(f"/runs/{execution.parent.id}/relations")
        tree = await client.get(f"/runs/{execution.children[0].id}/trace/tree")
        delegated = await client.post(
            f"/runs/{manual_parent.id}/delegations",
            json={
                "agent_name": "planner",
                "input": "delegated through HTTP",
                "delegation_key": "http-task-1",
            },
        )
        delegated_again = await client.post(
            f"/runs/{manual_parent.id}/delegations",
            json={
                "agent_name": "planner",
                "input": "ignored by idempotent reuse",
                "delegation_key": "http-task-1",
            },
        )

    assert agents.status_code == 200
    assert {item["name"] for item in agents.json()} >= {"demo", "planner", "worker"}
    assert relations.status_code == 200
    assert len(relations.json()["children"]) == 2
    assert tree.status_code == 200
    assert tree.json()["root_run_id"] == execution.parent.id
    assert tree.json()["node_count"] == 3
    assert delegated.status_code == 200
    assert delegated.json()["relation"]["parent_run_id"] == manual_parent.id
    assert delegated_again.json()["run"]["id"] == delegated.json()["run"]["id"]


@pytest.mark.asyncio
async def test_session_and_memory_api(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created_session = await client.post(
            "/sessions", json={"metadata": {"user": "beginner"}}
        )
        session_id = created_session.json()["id"]
        created_memory = await client.post(
            "/memories",
            json={
                "content": "The preferred framework is FastAPI.",
                "scope": "session",
                "scope_id": session_id,
            },
        )
        memory_id = created_memory.json()["id"]
        created_run = await client.post(
            "/runs",
            json={
                "agent_name": "demo",
                "input": "Which FastAPI framework is preferred?",
                "session_id": session_id,
            },
        )
        await runtime.wait(created_run.json()["id"])
        session_runs = await client.get(f"/sessions/{session_id}/runs")
        search = await client.get(
            "/memories/search",
            params={"query": "FastAPI", "session_id": session_id},
        )
        deleted = await client.delete(f"/memories/{memory_id}")

    assert created_session.status_code == 201
    assert created_memory.status_code == 201
    assert created_run.status_code == 202
    assert session_runs.status_code == 200
    assert [run["id"] for run in session_runs.json()] == [created_run.json()["id"]]
    assert search.status_code == 200
    assert search.json()[0]["record"]["id"] == memory_id
    assert deleted.status_code == 200
    assert deleted.json()["active"] is False


@pytest.mark.asyncio
async def test_doctor_endpoint(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/doctor")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["checks"]


@pytest.mark.asyncio
async def test_resolve_unknown_endpoint_records_audit_and_keeps_run_paused(
    workspace: Path,
) -> None:
    runtime = make_api_runtime(workspace)
    run = runtime.create_run("demo", "unknown")
    run.transition_to(RunStatus.RUNNING)
    runtime.store.save_run(run)
    step = Step.create(run.id, 1)
    runtime.store.create_step_with_event(run, step, "model.requested", {"step": 1})
    execution = ToolExecution.create(
        run.id,
        step.id,
        0,
        ToolCall("unknown-api-call", "write_text_file", {"path": "x", "content": "y"}),
        requires_approval=True,
        side_effecting=True,
    )
    execution.status = ToolExecutionStatus.UNKNOWN
    runtime.store.create_tool_executions(step, [execution])
    run.transition_to(RunStatus.PAUSED)
    runtime.store.save_run(run)

    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/tool-executions/{execution.id}/resolve-unknown",
            json={
                "resolution": "confirmed_succeeded",
                "reason": "Verified external state",
                "resolved_by": "api-test",
                "result_content": "already written",
            },
        )
        retry = await client.post(
            f"/tool-executions/{execution.id}/resolve-unknown",
            json={"resolution": "retry", "reason": "unsafe retry"},
        )

    assert response.status_code == 200
    assert response.json()["run"]["status"] == "paused"
    assert response.json()["tool_execution"]["resolution"] == "confirmed_succeeded"
    assert response.json()["tool_execution"]["resolved_by"] == "api-test"
    assert retry.status_code == 409

@pytest.mark.asyncio
async def test_run_submission_idempotency_replays_without_duplicate_execution(
    workspace: Path,
) -> None:
    calls = 0
    release = asyncio.Event()

    async def responder(messages, tools, config):
        nonlocal calls
        del messages, tools, config
        calls += 1
        await release.wait()
        return ModelResponse(content="done")

    tools = ToolRegistry()
    register_builtin_tools(tools)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "idempotency.sqlite3",
        ),
        MockProvider(responder),
        tools,
    )
    runtime.register_agent(demo_agent())
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/runs",
            headers={"Idempotency-Key": "request-123"},
            json={"agent_name": "demo", "input": "do it"},
        )
        second = await client.post(
            "/runs",
            headers={"Idempotency-Key": "request-123"},
            json={"agent_name": "demo", "input": "do it"},
        )
        conflict = await client.post(
            "/runs",
            headers={"Idempotency-Key": "request-123"},
            json={"agent_name": "demo", "input": "different"},
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.headers["Idempotent-Replayed"] == "false"
    assert second.headers["Idempotent-Replayed"] == "true"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert conflict.json()["retryable"] is False
    assert len(runtime.store.list_runs()) == 1
    release.set()
    await runtime.wait(first.json()["id"])
    assert calls == 1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_api_returns_retryable_429_when_inflight_capacity_is_exhausted(
    workspace: Path,
) -> None:
    release = asyncio.Event()

    async def responder(messages, tools, config):
        del messages, tools, config
        await release.wait()
        return ModelResponse(content="done")

    tools = ToolRegistry()
    register_builtin_tools(tools)
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "capacity.sqlite3",
            max_inflight_runs=1,
        ),
        MockProvider(responder),
        tools,
    )
    runtime.register_agent(demo_agent())
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/runs", json={"input": "first"})
        second = await client.post("/runs", json={"input": "second"})
        health = await client.get("/health")

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["code"] == "runtime_capacity_exhausted"
    assert second.json()["retryable"] is True
    assert second.headers["Retry-After"] == "1"
    assert health.json()["capacity"]["max_inflight_runs"] == 1
    release.set()
    await runtime.wait(first.json()["id"])
    await runtime.shutdown()

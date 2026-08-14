from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from agent_runtime.api import create_app, encode_sse
from agent_runtime.domain import RuntimeEvent
from agent_runtime.providers import (
    ModelResponse,
    ModelTokenDelta,
    MockProvider,
    MockStreamingProvider,
)
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry, register_builtin_tools
from agent_runtime.sdk import demo_agent


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
        assert health.json()["version"] == "0.5.3"

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


@pytest.mark.asyncio
async def test_lifecycle_endpoints_and_unknown_run(workspace: Path) -> None:
    runtime = make_api_runtime(workspace)
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/runs/run_missing")
        assert missing.status_code == 404

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

    assert trace.status_code == 200
    assert trace.json()["run_id"] == run_id
    assert trace.json()["trace_id"].startswith("trace_")
    assert any(span["kind"] == "model" for span in trace.json()["spans"])
    assert metrics.status_code == 200
    assert metrics.json()["total_runs"] == 1
    assert metrics.json()["model_requests"] == 1
    assert prometheus.status_code == 200
    assert "agent_runtime_runs_total" in prometheus.text

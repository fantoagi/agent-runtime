from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.observability import ObservabilityService
from agent_runtime.sdk import create_local_runtime, demo_agent


@pytest.mark.asyncio
async def test_trace_contains_run_model_and_tool_spans(workspace: Path) -> None:
    runtime = create_local_runtime(workspace, workspace / "state")
    runtime.register_agent(demo_agent())

    run = await runtime.run("demo", "19 * 23")
    trace = ObservabilityService(runtime.store).trace(run.id)

    assert trace.trace_id.startswith("trace_")
    assert trace.run_id == run.id
    assert trace.status == "completed"
    kinds = [span.kind for span in trace.spans]
    assert kinds.count("run") == 1
    assert kinds.count("model") == 2
    assert kinds.count("tool") == 1
    assert all(span.duration_ms is None or span.duration_ms >= 0 for span in trace.spans)


@pytest.mark.asyncio
async def test_metrics_are_derived_from_durable_history(workspace: Path) -> None:
    runtime = create_local_runtime(workspace, workspace / "state")
    runtime.register_agent(demo_agent())
    await runtime.run("demo", "2 + 2")
    await runtime.run("demo", "3 * 3")

    metrics = ObservabilityService(runtime.store).metrics()
    payload = metrics.to_dict()
    prometheus = metrics.to_prometheus()

    assert metrics.total_runs == 2
    assert metrics.runs_by_status == {"completed": 2}
    assert metrics.model_requests == 4
    assert metrics.tool_requests == 2
    assert payload["duration_ms"]["run_average"] >= 0
    assert 'agent_runtime_runs_total{status="completed"} 2' in prometheus
    assert "agent_runtime_tool_requests_total 2" in prometheus

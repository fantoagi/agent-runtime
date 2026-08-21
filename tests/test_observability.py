from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.cli import build_parser
from agent_runtime.domain import AgentDefinition, ModelConfig, ProviderTransportError
from agent_runtime.observability import ObservabilityService
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.sdk import create_local_runtime, demo_agent
from agent_runtime.tools import ToolRegistry


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


@pytest.mark.asyncio
async def test_provider_retries_feed_failure_metrics_and_diagnostics(
    workspace: Path,
) -> None:
    attempts = 0

    async def responder(messages, tools, config):
        nonlocal attempts
        del messages, tools, config
        attempts += 1
        if attempts == 1:
            raise ProviderTransportError("temporary network failure")
        return ModelResponse(content="recovered")

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            max_model_retries=1,
        ),
        MockProvider(responder),
        ToolRegistry(),
    )
    runtime.register_agent(
        AgentDefinition("retry-agent", "retry", [], ModelConfig("mock", "retry"))
    )

    run = await runtime.run("retry-agent", "recover")
    service = ObservabilityService(runtime.store)
    metrics = service.metrics()
    diagnostics = service.diagnostics(runtime)

    assert run.status.value == "completed"
    assert metrics.provider_attempt_failures == 1
    assert metrics.provider_retries == 1
    assert metrics.failures_by_type == {"provider": 1}
    assert metrics.model_duration_ms_p95 >= 0
    assert "agent_runtime_provider_retries_total 1" in metrics.to_prometheus()
    assert diagnostics.status == "ok"
    assert diagnostics.version == "0.8.20"
    assert diagnostics.runtime["state"] == "accepting"
    assert diagnostics.runtime["tools"]["pending_sync_tools"] == 0
    assert diagnostics.process["thread_count"] >= 1
    assert diagnostics.recent_failures[0].event_type == "model.attempt.failed"
    assert diagnostics.recent_failures[0].retryable is True


def test_observe_diagnostics_cli_contract() -> None:
    arguments = build_parser().parse_args(
        [
            "--json-logs",
            "observe",
            "diagnostics",
            "--limit",
            "25",
            "--recent-failures",
            "3",
        ]
    )

    assert arguments.json_logs is True
    assert arguments.observe_command == "diagnostics"
    assert arguments.limit == 25
    assert arguments.recent_failures == 3

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from agent_runtime.cli import build_parser
from agent_runtime.domain import AgentDefinition, ModelConfig, ProviderHTTPError
from agent_runtime.incident import IncidentDiagnosticsService
from agent_runtime.providers import MockProvider
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def failing_runtime(workspace: Path) -> Runtime:
    def reject(messages, tools, config):
        del messages, tools, config
        raise ProviderHTTPError(
            401,
            "private provider payload Bearer secret-token sk-supersecret123 was rejected",
            retryable=False,
        )

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            max_model_retries=2,
        ),
        MockProvider(reject),
        ToolRegistry(),
    )
    runtime.register_agent(
        AgentDefinition("failing", "private system prompt", [], ModelConfig("mock", "failure"))
    )
    return runtime


@pytest.mark.asyncio
async def test_incident_report_classifies_provider_auth_and_excludes_payloads(
    workspace: Path,
) -> None:
    runtime = failing_runtime(workspace)
    run = await runtime.run("failing", "private user prompt with sk-inputsecret123")
    runtime.store.append_event(
        run.id,
        "tool.failed",
        {
            "tool_name": "secret_tool",
            "arguments": {"api_key": "must-not-leak", "content": "private tool input"},
            "error": "Bearer tool-secret failed",
        },
    )

    content, report = IncidentDiagnosticsService(runtime).bundle_bytes(run_id=run.id)

    assert report.scope_run_id == run.id
    assert report.runs[0]["id"] == run.id
    assert "input" not in report.runs[0]
    categories = {item.category for item in report.failure_analysis}
    assert "provider.authentication" in categories
    assert "tool.execution" in categories
    assert b"private user prompt" not in content
    assert b"must-not-leak" not in content
    assert b"private tool input" not in content
    assert b"secret-token" not in content
    assert b"private provider payload" not in content

    archive_path = workspace / "incident.zip"
    archive_path.write_bytes(content)
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert names == {
            "README.txt",
            "collection.json",
            "diagnostics.json",
            "events.json",
            "failure-analysis.json",
            "manifest.json",
            "privacy.json",
            "runs.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        diagnostics = json.loads(archive.read("diagnostics.json"))
        assert manifest["format_version"] == 1
        assert manifest["runtime_version"] == "0.8.22"
        assert diagnostics["version"] == "0.8.22"
        assert manifest["run_count"] == 1


@pytest.mark.asyncio
async def test_create_bundle_is_atomic_and_requires_explicit_overwrite(
    workspace: Path,
) -> None:
    runtime = failing_runtime(workspace)
    await runtime.run("failing", "fail")
    service = IncidentDiagnosticsService(runtime)
    output = workspace / "support" / "incident.zip"

    created = service.create_bundle(output)

    assert created.path == output.resolve()
    assert created.size_bytes == output.stat().st_size
    assert len(created.sha256) == 64
    with pytest.raises(FileExistsError):
        service.create_bundle(output)
    replaced = service.create_bundle(output, overwrite=True)
    assert replaced.path == created.path
    assert not list(output.parent.glob("*.tmp"))


@pytest.mark.asyncio
async def test_failure_analysis_covers_stable_operational_categories(workspace: Path) -> None:
    runtime = failing_runtime(workspace)
    run = await runtime.run("failing", "fail")
    runtime.store.append_event(
        run.id,
        "model.attempt.failed",
        {"status_code": 429, "error_type": "ProviderHTTPError", "retryable": True},
    )
    runtime.store.append_event(
        run.id,
        "model.attempt.failed",
        {"status_code": 503, "error_type": "ProviderHTTPError", "retryable": True},
    )
    runtime.store.append_event(
        run.id,
        "model.attempt.failed",
        {"error_type": "ReadTimeout", "retryable": True},
    )
    runtime.store.append_event(
        run.id,
        "model.attempt.failed",
        {"error_type": "ProviderTransportError", "retryable": True},
    )
    runtime.store.append_event(run.id, "tool.outcome_unknown", {"tool_name": "write"})
    runtime.store.append_event(run.id, "workflow.failed", {})

    categories = {
        item.category
        for item in IncidentDiagnosticsService(runtime).failure_analysis(run_id=run.id, limit=20)
    }

    assert {
        "provider.authentication",
        "provider.rate_limit",
        "provider.server",
        "provider.timeout",
        "provider.transport",
        "tool.unknown_outcome",
        "runtime.execution",
    }.issubset(categories)


@pytest.mark.asyncio
async def test_incident_report_bounds_events_and_records_truncation(workspace: Path) -> None:
    runtime = failing_runtime(workspace)
    run = await runtime.run("failing", "fail")
    for index in range(6):
        runtime.store.append_event(run.id, "test.signal", {"index": index})

    report = IncidentDiagnosticsService(runtime).report(run_id=run.id, event_limit=3)

    assert len(report.events) == 3
    assert report.collection["events_truncated"] is True
    assert report.collection["observed_event_count"] > 3
    assert report.collection["included_event_count"] == 3


def test_incident_bundle_cli_contract() -> None:
    arguments = build_parser().parse_args(
        [
            "observe",
            "incident-bundle",
            "--output",
            "incident.zip",
            "--run-id",
            "run_123",
            "--limit",
            "25",
            "--recent-failures",
            "5",
            "--event-limit",
            "250",
            "--overwrite",
        ]
    )

    assert arguments.observe_command == "incident-bundle"
    assert arguments.output == "incident.zip"
    assert arguments.run_id == "run_123"
    assert arguments.limit == 25
    assert arguments.recent_failures == 5
    assert arguments.event_limit == 250
    assert arguments.overwrite is True

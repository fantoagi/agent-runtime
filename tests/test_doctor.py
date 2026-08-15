from __future__ import annotations

from pathlib import Path

from agent_runtime.doctor import RuntimeDoctor
from agent_runtime.domain import AgentDefinition, AgentRun, ModelConfig, RunStatus
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_runtime(workspace: Path) -> Runtime:
    runtime = Runtime(
        RuntimeConfig(workspace_path=workspace, database_path=workspace / "runtime.sqlite3"),
        provider=MockProvider(lambda messages, tools, config: ModelResponse(content="done")),
        tools=ToolRegistry(),
    )
    runtime.register_agent(
        AgentDefinition("doctor-agent", "doctor", [], ModelConfig("mock", "doctor"))
    )
    return runtime


def test_doctor_reports_healthy_terminal_runtime(workspace: Path) -> None:
    runtime = make_runtime(workspace)
    run = runtime.create_run("doctor-agent", "inspect")
    run.transition_to(RunStatus.RUNNING)
    run.result = "done"
    run.transition_to(RunStatus.COMPLETED)
    runtime.store.save_run(run)

    report = RuntimeDoctor(runtime.store).run(run.id)

    assert report.status == "ok"
    assert report.exit_code == 0
    assert report.to_dict()["run_id"] == run.id


def test_doctor_reports_active_run_attention(workspace: Path) -> None:
    runtime = make_runtime(workspace)
    run = runtime.create_run("doctor-agent", "inspect")
    run.transition_to(RunStatus.RUNNING)
    runtime.store.save_run(run)

    report = RuntimeDoctor(runtime.store).run()

    assert report.status == "attention_required"
    assert report.exit_code == 1
    lifecycle = next(check for check in report.checks if check.name == "runs.lifecycle")
    assert lifecycle.details["active"] == {"running": 1}


def test_doctor_reports_active_legacy_run_without_agent_snapshot(workspace: Path) -> None:
    runtime = make_runtime(workspace)
    legacy = AgentRun.create("legacy-agent", "legacy")
    runtime.store.create_run(legacy)

    report = RuntimeDoctor(runtime.store).run(legacy.id)

    assert report.status == "attention_required"
    snapshots = next(check for check in report.checks if check.name == "agents.snapshots")
    assert snapshots.details["items"] == [legacy.id]

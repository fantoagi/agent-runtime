from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import agent_runtime.acceptance as acceptance
from agent_runtime.acceptance import (
    AcceptanceCase,
    AcceptanceLimits,
    AcceptanceSuite,
    AcceptanceSuiteError,
    RealModelAcceptanceRunner,
    load_acceptance_suite,
)
from agent_runtime.domain import (
    AgentDefinition,
    AgentRun,
    ModelConfig,
    RunStatus,
    RuntimeEvent,
    ToolCall,
    ToolExecution,
    ToolExecutionStatus,
)
from agent_runtime.local_config import LocalRuntimeSettings
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_settings(workspace: Path) -> LocalRuntimeSettings:
    state = workspace / "state"
    return LocalRuntimeSettings(
        config_path=workspace / "agent-runtime.toml",
        workspace=workspace,
        state_dir=state,
        agent_name="acceptance-agent",
        system_prompt="answer clearly",
        provider="mock",
        model="acceptance-model",
        base_url="http://127.0.0.1:1",
        api_key_env="UNUSED_TEST_KEY",
        model_timeout_seconds=5.0,
        run_timeout_seconds=10.0,
        shutdown_timeout_seconds=5.0,
        max_inflight_runs=2,
        max_concurrent_model_requests=2,
        max_sync_tool_workers=2,
        max_pending_sync_tools=4,
        host="127.0.0.1",
        port=8000,
        log_level="INFO",
        log_file=state / "runtime.log",
        log_max_bytes=1_000_000,
        log_backup_count=1,
        enable_process_tool=False,
        workspace_instructions_enabled=False,
    )


def make_runtime(settings: LocalRuntimeSettings) -> Runtime:
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=settings.workspace,
            database_path=settings.database_path,
            artifact_path=settings.artifact_path,
        ),
        provider=MockProvider(
            lambda messages, tools, config: ModelResponse(
                content="This is a sufficiently detailed acceptance answer without raw fixtures.",
                finish_reason="stop",
            )
        ),
        tools=ToolRegistry(),
    )
    runtime.register_agent(
        AgentDefinition(
            name=settings.agent_name,
            system_prompt=settings.system_prompt,
            tools=[],
            model=ModelConfig(provider="mock", model=settings.model),
        )
    )
    return runtime


def test_load_builtin_acceptance_suite() -> None:
    suite = load_acceptance_suite("local-real-model")

    assert suite.name == "local-real-model"
    assert suite.version == 1
    assert len(suite.cases) == 5
    assert suite.source == "builtin:local-real-model.json"
    assert len(suite.checksum) == 64


def test_rejects_unsafe_fixture_path(tmp_path: Path) -> None:
    suite_path = tmp_path / "unsafe.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "unsafe",
                "version": 1,
                "description": "unsafe fixture",
                "cases": [
                    {
                        "name": "escape",
                        "category": "boundary",
                        "description": "must fail",
                        "prompt": "test",
                        "files": {"../outside.txt": "no"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AcceptanceSuiteError, match="Unsafe fixture path"):
        load_acceptance_suite(suite_path)


@pytest.mark.asyncio
async def test_runner_uses_isolated_workspace_and_redacted_report(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    suite = AcceptanceSuite(
        name="test-suite",
        version=1,
        description="test",
        source="test",
        checksum="a" * 64,
        cases=(
            AcceptanceCase(
                name="explain",
                category="explanation",
                description="explain",
                prompt="SECRET_PROMPT_SHOULD_NOT_BE_EXPORTED",
                files={"SECRET_FILE_NAME.txt": "SECRET_FILE_CONTENT"},
                initialize_git=False,
                limits=AcceptanceLimits(min_final_answer_chars=20),
            ),
        ),
    )

    report = await RealModelAcceptanceRunner(
        settings,
        runtime_factory=make_runtime,
    ).run(suite)

    assert report.pass_rate == 1.0
    assert report.source_workspace_exposed is False
    assert report.artifact_path is not None
    payload = Path(report.artifact_path).read_text(encoding="utf-8")
    assert "SECRET_PROMPT_SHOULD_NOT_BE_EXPORTED" not in payload
    assert "SECRET_FILE_CONTENT" not in payload
    assert "SECRET_FILE_NAME" not in payload
    result = report.results[0]
    assert result.final_answer_characters > 20
    assert result.final_answer_sha256 is not None
    assert result.metrics.event_count > 0
    assert result.metrics.model_requests == 1


@pytest.mark.asyncio
async def test_runner_rejects_unknown_case_selection(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    suite = AcceptanceSuite(
        name="test-suite",
        version=1,
        description="test",
        source="test",
        checksum="b" * 64,
        cases=(
            AcceptanceCase(
                name="known",
                category="explanation",
                description="known",
                prompt="known",
                files={"README.md": "known"},
                initialize_git=False,
            ),
        ),
    )

    with pytest.raises(AcceptanceSuiteError, match="Unknown acceptance cases"):
        await RealModelAcceptanceRunner(settings, runtime_factory=make_runtime).run(
            suite,
            case_names=["missing"],
        )



def valid_suite_payload() -> dict[str, Any]:
    return {
        "name": "suite",
        "version": 1,
        "description": "suite description",
        "cases": [
            {
                "name": "case",
                "category": "explanation",
                "description": "case description",
                "prompt": "inspect the fixture",
                "files": {"README.md": "fixture"},
                "initialize_git": False,
            }
        ],
    }


def write_suite(tmp_path: Path, payload: Any, name: str = "suite.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_case(**overrides: Any) -> AcceptanceCase:
    values: dict[str, Any] = {
        "name": "case",
        "category": "test",
        "description": "test case",
        "prompt": "test",
        "files": {"README.md": "fixture"},
        "initialize_git": False,
    }
    values.update(overrides)
    return AcceptanceCase(**values)


def make_execution(
    name: str,
    arguments: dict[str, Any],
    status: ToolExecutionStatus,
    *,
    position: int,
    result_data: dict[str, Any] | None = None,
) -> ToolExecution:
    return ToolExecution(
        id=f"execution-{position}",
        run_id="run-metrics",
        step_id="step-1",
        position=position,
        tool_call=ToolCall(id=f"call-{position}", name=name, arguments=arguments),
        status=status,
        idempotency_key=f"key-{position}",
        result_data=result_data,
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "root must be an object"),
        ({"name": "suite", "version": 1, "description": "suite", "cases": []}, "at least one case"),
        (
            {"name": "suite", "version": 1, "description": "suite", "cases": ["bad"]},
            "Every acceptance case must be an object",
        ),
        (
            {
                "name": "suite",
                "version": 1,
                "description": "suite",
                "cases": [valid_suite_payload()["cases"][0], valid_suite_payload()["cases"][0]],
            },
            "names must be unique",
        ),
        (
            {
                **valid_suite_payload(),
                "cases": [{**valid_suite_payload()["cases"][0], "files": {}}],
            },
            "requires fixture files",
        ),
        (
            {
                **valid_suite_payload(),
                "cases": [{**valid_suite_payload()["cases"][0], "files": {"README.md": 1}}],
            },
            "paths and contents must be strings",
        ),
        (
            {
                **valid_suite_payload(),
                "cases": [{**valid_suite_payload()["cases"][0], "limits": []}],
            },
            "limits must be an object",
        ),
        (
            {
                **valid_suite_payload(),
                "cases": [{**valid_suite_payload()["cases"][0], "approval_action": "later"}],
            },
            "approval_action",
        ),
        ({**valid_suite_payload(), "name": " "}, "name must be a non-empty string"),
        ({**valid_suite_payload(), "version": 0}, "version must be at least 1"),
        (
            {
                **valid_suite_payload(),
                "cases": [
                    {
                        **valid_suite_payload()["cases"][0],
                        "limits": {"min_final_answer_chars": -1},
                    }
                ],
            },
            "min_final_answer_chars must be a non-negative integer",
        ),
        (
            {
                **valid_suite_payload(),
                "cases": [
                    {
                        **valid_suite_payload()["cases"][0],
                        "limits": {"forbidden_tools": "run_process"},
                    }
                ],
            },
            "Expected a list of strings",
        ),
        (
            {
                **valid_suite_payload(),
                "cases": [
                    {
                        **valid_suite_payload()["cases"][0],
                        "limits": {"required_tool_groups": {}},
                    }
                ],
            },
            "required_tool_groups must be a list",
        ),
        (
            {
                **valid_suite_payload(),
                "cases": [
                    {
                        **valid_suite_payload()["cases"][0],
                        "limits": {"required_tool_groups": [[]]},
                    }
                ],
            },
            "Required tool groups must not be empty",
        ),
    ],
)
def test_rejects_invalid_suite_schema(tmp_path: Path, payload: Any, message: str) -> None:
    with pytest.raises(AcceptanceSuiteError, match=message):
        load_acceptance_suite(write_suite(tmp_path, payload))


def test_rejects_missing_and_invalid_json_suite(tmp_path: Path) -> None:
    with pytest.raises(AcceptanceSuiteError, match="was not found"):
        load_acceptance_suite("missing-acceptance-suite")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(AcceptanceSuiteError, match="invalid JSON"):
        load_acceptance_suite(invalid)


def test_loads_explicit_suite_and_normalizes_fixture_paths(tmp_path: Path) -> None:
    payload = valid_suite_payload()
    payload["cases"][0]["files"] = {"src\\sample.py": "VALUE = 1\n"}
    payload["cases"][0]["limits"] = {
        "forbidden_tools": ["delete_file"],
        "required_tool_groups": [["read_text_file", "list_directory"]],
    }

    suite = load_acceptance_suite(write_suite(tmp_path, payload))

    assert suite.cases[0].files == {"src/sample.py": "VALUE = 1\n"}
    assert suite.cases[0].limits.forbidden_tools == ("delete_file",)
    assert suite.cases[0].limits.required_tool_groups == (("read_text_file", "list_directory"),)


@pytest.mark.asyncio
async def test_runner_rejects_invalid_repeat(tmp_path: Path) -> None:
    suite = AcceptanceSuite("suite", 1, "test", (make_case(),), "test", "c" * 64)

    with pytest.raises(ValueError, match="repeat must be at least 1"):
        await RealModelAcceptanceRunner(make_settings(tmp_path), runtime_factory=make_runtime).run(
            suite,
            repeat=0,
        )


@pytest.mark.asyncio
async def test_runner_records_harness_error_without_exporting_fixture(tmp_path: Path) -> None:
    suite = AcceptanceSuite("suite", 1, "test", (make_case(),), "test", "d" * 64)

    def failing_factory(settings: LocalRuntimeSettings) -> Runtime:
        del settings
        raise RuntimeError("SECRET_HARNESS_DETAIL")

    report = await RealModelAcceptanceRunner(
        make_settings(tmp_path),
        runtime_factory=failing_factory,
    ).run(suite)

    result = report.results[0]
    assert result.status == "harness_error"
    assert result.error_code == "RuntimeError"
    assert result.metrics.event_count == 0
    assert "SECRET_HARNESS_DETAIL" not in Path(report.artifact_path or "").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_runner_supports_selected_case_and_repeat(tmp_path: Path) -> None:
    suite = AcceptanceSuite(
        "suite",
        1,
        "test",
        (make_case(name="first"), make_case(name="second")),
        "test",
        "e" * 64,
    )

    report = await RealModelAcceptanceRunner(
        make_settings(tmp_path),
        runtime_factory=make_runtime,
    ).run(suite, case_names=["second"], repeat=2)

    assert [result.case_name for result in report.results] == ["second", "second"]
    assert report.total_attempts == 2
    assert report.failed_attempts == 0
    assert report.selected_case_names == ("second",)
    assert report.repeat == 2
    payload = json.loads(Path(report.artifact_path or "").read_text(encoding="utf-8"))
    assert payload["selection"] == {
        "case_names": ["second"],
        "repeat": 2,
        "expected_attempts": 2,
        "actual_attempts": 2,
    }


def test_initialize_git_fixture_success_and_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def successful_run(command: tuple[str, ...], **kwargs: Any) -> SimpleNamespace:
        del kwargs
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(acceptance.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(acceptance.subprocess, "run", successful_run)
    acceptance._initialize_git_repository(tmp_path)
    assert [command[1] for command in calls] == ["init", "config", "config", "add", "commit"]

    monkeypatch.setattr(acceptance.shutil, "which", lambda name: None)
    with pytest.raises(AcceptanceSuiteError, match="Git is required"):
        acceptance._initialize_git_repository(tmp_path)

    monkeypatch.setattr(acceptance.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        acceptance.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    with pytest.raises(AcceptanceSuiteError, match="step 'init'"):
        acceptance._initialize_git_repository(tmp_path)


def test_metric_collection_and_case_assertions_cover_tool_outcomes() -> None:
    run = AgentRun.create("agent", "test")
    run.id = "run-metrics"
    run.status = RunStatus.COMPLETED
    run.result = "verified result"
    run.step_count = 5
    run.tool_call_count = 8
    executions = [
        make_execution(
            "apply_patch",
            {"patch": "redacted"},
            ToolExecutionStatus.COMPLETED,
            position=1,
            result_data={"files": [{"path": "src/sample.py"}, {"ignored": True}]},
        ),
        make_execution(
            "apply_patch",
            {"patch": "redacted"},
            ToolExecutionStatus.COMPLETED,
            position=2,
            result_data={"files": [{"path": "src/sample.py"}]},
        ),
        make_execution("git_diff", {}, ToolExecutionStatus.COMPLETED, position=3),
        make_execution(
            "run_process",
            {"argv": ["python", "-m", "pytest"]},
            ToolExecutionStatus.COMPLETED,
            position=4,
            result_data={"exit_code": 0},
        ),
        make_execution("read_text_file", {}, ToolExecutionStatus.FAILED, position=5),
        make_execution("read_text_file", {}, ToolExecutionStatus.REJECTED, position=6),
        make_execution("read_text_file", {}, ToolExecutionStatus.CANCELLED, position=7),
        make_execution("write_text_file", {}, ToolExecutionStatus.UNKNOWN, position=8),
    ]
    event_types = [
        "approval.requested",
        "approval.resolved",
        "model.requested",
        "model.requested",
        "model.retry.scheduled",
        "convergence.warning",
        "convergence.finalization_requested",
        "convergence.finalization_context_built",
        "convergence.textual_tool_call_detected",
    ]
    events = [
        RuntimeEvent.create(run.id, index, event_type)
        for index, event_type in enumerate(event_types, start=1)
    ]
    store = SimpleNamespace(
        events_since=lambda run_id: events,
        tool_executions_for_run=lambda run_id: executions,
    )

    metrics = acceptance._collect_metrics(SimpleNamespace(store=store), run)

    assert metrics.duplicate_tool_calls == 3
    assert metrics.failed_tool_calls == 3
    assert metrics.unknown_tool_calls == 1
    assert metrics.validation_attempts == 1
    assert metrics.validation_successes == 1
    assert metrics.verification_status == "verified"
    assert metrics.protocol_violations == 1
    assert metrics.model_requests == 2

    case = make_case(
        limits=AcceptanceLimits(
            expected_status="completed",
            min_final_answer_chars=1,
            max_steps=4,
            max_tool_calls=8,
            max_duplicate_tool_calls=0,
            max_failed_tool_calls=2,
            max_protocol_violations=0,
            min_approval_requests=1,
            forbidden_tools=("apply_patch",),
            required_tool_groups=(("git_diff",), ("list_directory",)),
            require_verified_if_changed=True,
        )
    )
    assertions = {item.name: item for item in acceptance._evaluate_case(case, run, metrics)}
    assert assertions["expected_status"].passed is True
    assert assertions["step_budget"].passed is False
    assert assertions["duplicate_tool_budget"].passed is False
    assert assertions["failed_tool_budget"].passed is False
    assert assertions["protocol_integrity"].passed is False
    assert assertions["forbidden_tools"].actual == ["apply_patch"]
    assert assertions["required_tool_group_1"].passed is True
    assert assertions["required_tool_group_2"].passed is False
    assert assertions["verified_if_changed"].passed is True



def test_acceptance_requires_evidence_after_the_latest_write() -> None:
    run = AgentRun.create("agent", "edit a file")
    run.id = "run-latest-write"
    run.status = RunStatus.COMPLETED
    run.result = "changed"
    executions = [
        make_execution(
            "run_process",
            {"argv": ["python", "-m", "pytest"]},
            ToolExecutionStatus.COMPLETED,
            position=1,
            result_data={"exit_code": 0},
        ),
        make_execution(
            "write_text_file",
            {"path": "src/sample.py"},
            ToolExecutionStatus.COMPLETED,
            position=2,
            result_data={"path": "src/sample.py", "created": False},
        ),
        make_execution("git_diff", {}, ToolExecutionStatus.COMPLETED, position=3),
    ]
    for execution in executions:
        execution.run_id = run.id
    store = SimpleNamespace(
        events_since=lambda run_id: [],
        tool_executions_for_run=lambda run_id: executions,
    )

    metrics = acceptance._collect_metrics(SimpleNamespace(store=store), run)

    assert metrics.diff_inspected is True
    assert metrics.validation_attempts == 0
    assert metrics.validation_successes == 0
    assert metrics.verification_status == "unverified"

def test_new_file_acceptance_requires_git_status_evidence() -> None:
    run = AgentRun.create("agent", "create a file")
    run.id = "run-new-file"
    run.status = RunStatus.COMPLETED
    run.result = "created"
    write = make_execution(
        "write_text_file",
        {"path": "RESULT.txt"},
        ToolExecutionStatus.COMPLETED,
        position=1,
        result_data={"path": "RESULT.txt", "status": "written", "created": True},
    )
    write.run_id = run.id
    diff = make_execution("git_diff", {}, ToolExecutionStatus.COMPLETED, position=2)
    diff.run_id = run.id
    events: list[RuntimeEvent] = []

    def metrics_for(executions: list[ToolExecution]) -> acceptance.AcceptanceMetrics:
        store = SimpleNamespace(
            events_since=lambda run_id: events,
            tool_executions_for_run=lambda run_id: executions,
        )
        return acceptance._collect_metrics(SimpleNamespace(store=store), run)

    missing_status = metrics_for([write, diff])
    assert missing_status.created_file_writes == 1
    assert missing_status.diff_inspected is True
    assert missing_status.git_status_inspected is False
    assert missing_status.verification_status == "unverified"

    status = make_execution("git_status", {}, ToolExecutionStatus.COMPLETED, position=3)
    status.run_id = run.id
    verified = metrics_for([write, diff, status])
    assert verified.git_status_inspected is True
    assert verified.verification_status == "verified"


def test_validation_and_code_change_detection_helpers() -> None:
    assert acceptance._is_validation_execution(
        make_execution("read_text_file", {}, ToolExecutionStatus.COMPLETED, position=1)
    ) is False
    assert acceptance._is_validation_execution(
        make_execution(
            "run_process",
            {"argv": "pytest"},
            ToolExecutionStatus.COMPLETED,
            position=2,
        )
    ) is False
    assert acceptance._is_validation_execution(
        make_execution(
            "run_process",
            {"argv": ["python", "-m", "pytest"]},
            ToolExecutionStatus.COMPLETED,
            position=3,
        )
    ) is True
    assert acceptance._execution_changes_code(
        make_execution(
            "apply_patch",
            {},
            ToolExecutionStatus.COMPLETED,
            position=4,
            result_data={"files": "src/sample.py"},
        )
    ) is False
    assert acceptance._execution_changes_code(
        make_execution(
            "replace_text",
            {"path": "src/sample.py"},
            ToolExecutionStatus.COMPLETED,
            position=5,
        )
    ) is True
    assert acceptance._execution_changes_code(
        make_execution(
            "write_text_file",
            {"path": "notes.txt"},
            ToolExecutionStatus.COMPLETED,
            position=6,
        )
    ) is False


class FakeApprovalStore:
    def __init__(self, *, has_approval: bool = True) -> None:
        self.has_approval = has_approval

    def pending_approval(self, run_id: str) -> SimpleNamespace | None:
        del run_id
        return SimpleNamespace(id="approval-1") if self.has_approval else None


class FakeApprovalRuntime:
    def __init__(self, *, has_approval: bool = True, complete_after_resume: bool = True) -> None:
        self.store = FakeApprovalStore(has_approval=has_approval)
        self.complete_after_resume = complete_after_resume
        self.resolutions: list[tuple[str, bool, str]] = []

    def resolve_approval(self, approval_id: str, approved: bool, reason: str) -> None:
        self.resolutions.append((approval_id, approved, reason))

    async def resume(self, run_id: str) -> AgentRun:
        run = AgentRun.create("agent", "test")
        run.id = run_id
        run.status = (
            RunStatus.COMPLETED if self.complete_after_resume else RunStatus.WAITING_FOR_APPROVAL
        )
        return run


@pytest.mark.asyncio
async def test_approval_resolution_paths(tmp_path: Path) -> None:
    runner = RealModelAcceptanceRunner(make_settings(tmp_path), runtime_factory=make_runtime)
    waiting = AgentRun.create("agent", "test")
    waiting.status = RunStatus.WAITING_FOR_APPROVAL

    unchanged = await runner._resolve_approvals(
        FakeApprovalRuntime(),
        waiting,
        make_case(approval_action="none"),
    )
    assert unchanged.status is RunStatus.WAITING_FOR_APPROVAL

    approving_runtime = FakeApprovalRuntime()
    completed = await runner._resolve_approvals(
        approving_runtime,
        waiting,
        make_case(approval_action="approve"),
    )
    assert completed.status is RunStatus.COMPLETED
    assert approving_runtime.resolutions[0][1] is True

    rejecting_runtime = FakeApprovalRuntime()
    await runner._resolve_approvals(
        rejecting_runtime,
        waiting,
        make_case(approval_action="reject"),
    )
    assert rejecting_runtime.resolutions[0][1] is False

    with pytest.raises(AcceptanceSuiteError, match="waiting without an approval"):
        await runner._resolve_approvals(
            FakeApprovalRuntime(has_approval=False),
            waiting,
            make_case(approval_action="approve"),
        )

    with pytest.raises(AcceptanceSuiteError, match="exceeded the approval limit"):
        await runner._resolve_approvals(
            FakeApprovalRuntime(complete_after_resume=False),
            waiting,
            make_case(approval_action="approve", timeout_seconds=1.0),
        )


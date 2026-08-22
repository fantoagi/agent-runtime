from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from .completion import looks_like_validation_command
from .domain import AgentRun, RunStatus, ToolExecution, ToolExecutionStatus, new_id, utc_now
from .local_config import LocalRuntimeSettings
from .local_runtime import create_configured_local_runtime
from .runtime import Runtime
from .version import __version__

_WRITE_TOOLS = frozenset({"apply_patch", "replace_text", "write_text_file"})
_PROTOCOL_EVENTS = frozenset({"convergence.textual_tool_call_detected"})
_CODE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".py", ".pyi", ".rs", ".ts", ".tsx"})


class AcceptanceSuiteError(ValueError):
    """An acceptance suite or its isolated fixture is invalid."""


@dataclass(frozen=True, slots=True)
class AcceptanceLimits:
    expected_status: str = "completed"
    min_final_answer_chars: int = 1
    max_steps: int = 20
    max_tool_calls: int = 40
    max_duplicate_tool_calls: int = 4
    max_failed_tool_calls: int = 2
    max_protocol_violations: int = 0
    min_approval_requests: int = 0
    forbidden_tools: tuple[str, ...] = ()
    required_tool_groups: tuple[tuple[str, ...], ...] = ()
    require_verified_if_changed: bool = False


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    name: str
    category: str
    description: str
    prompt: str
    files: Mapping[str, str]
    limits: AcceptanceLimits = field(default_factory=AcceptanceLimits)
    approval_action: str = "none"
    initialize_git: bool = True
    timeout_seconds: float = 240.0


@dataclass(frozen=True, slots=True)
class AcceptanceSuite:
    name: str
    version: int
    description: str
    cases: tuple[AcceptanceCase, ...]
    source: str
    checksum: str


@dataclass(frozen=True, slots=True)
class AcceptanceAssertion:
    name: str
    passed: bool
    expected: Any
    actual: Any
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceMetrics:
    step_count: int
    tool_call_count: int
    tool_counts: Mapping[str, int]
    duplicate_tool_calls: int
    failed_tool_calls: int
    unknown_tool_calls: int
    write_tool_calls: int
    approval_requests: int
    approval_resolutions: int
    model_requests: int
    model_retries: int
    convergence_warnings: int
    finalization_requests: int
    finalization_contexts: int
    protocol_violations: int
    event_count: int
    event_type_counts: Mapping[str, int]
    verification_status: str
    validation_attempts: int
    validation_successes: int
    diff_inspected: bool
    git_status_inspected: bool
    created_file_writes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_count": self.step_count,
            "tool_call_count": self.tool_call_count,
            "tool_counts": dict(self.tool_counts),
            "duplicate_tool_calls": self.duplicate_tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "unknown_tool_calls": self.unknown_tool_calls,
            "write_tool_calls": self.write_tool_calls,
            "approval_requests": self.approval_requests,
            "approval_resolutions": self.approval_resolutions,
            "model_requests": self.model_requests,
            "model_retries": self.model_retries,
            "convergence_warnings": self.convergence_warnings,
            "finalization_requests": self.finalization_requests,
            "finalization_contexts": self.finalization_contexts,
            "protocol_violations": self.protocol_violations,
            "event_count": self.event_count,
            "event_type_counts": dict(self.event_type_counts),
            "verification_status": self.verification_status,
            "validation_attempts": self.validation_attempts,
            "validation_successes": self.validation_successes,
            "diff_inspected": self.diff_inspected,
            "git_status_inspected": self.git_status_inspected,
            "created_file_writes": self.created_file_writes,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceCaseResult:
    case_name: str
    category: str
    attempt: int
    run_id: str | None
    trace_id: str | None
    status: str
    passed: bool
    duration_ms: float
    final_answer_characters: int
    final_answer_sha256: str | None
    error_code: str | None
    metrics: AcceptanceMetrics
    assertions: tuple[AcceptanceAssertion, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "category": self.category,
            "attempt": self.attempt,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "status": self.status,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "final_answer_characters": self.final_answer_characters,
            "final_answer_sha256": self.final_answer_sha256,
            "error_code": self.error_code,
            "metrics": self.metrics.to_dict(),
            "assertions": [item.to_dict() for item in self.assertions],
        }


@dataclass(frozen=True, slots=True)
class AcceptanceManifest:
    """Non-sensitive execution metadata attached to persisted acceptance reports."""

    runtime_version: str = "unknown"
    git_commit: str | None = None
    python_version: str = "unknown"
    platform: str = "unknown"
    provider: str = "unknown"
    model: str = "unknown"
    suite: str = "unknown"
    cases: tuple[str, ...] = ()
    repeat: int = 1
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_version": self.runtime_version,
            "git_commit": self.git_commit,
            "python_version": self.python_version,
            "platform": self.platform,
            "provider": self.provider,
            "model": self.model,
            "suite": self.suite,
            "cases": list(self.cases),
            "repeat": self.repeat,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_report_payload(cls, payload: Mapping[str, Any]) -> AcceptanceManifest:
        """Read both v0.8.27 manifests and legacy top-level report fields."""
        raw_manifest = payload.get("manifest")
        manifest = raw_manifest if isinstance(raw_manifest, Mapping) else {}
        raw_selection = payload.get("selection")
        selection = raw_selection if isinstance(raw_selection, Mapping) else {}
        raw_cases = manifest.get("cases", selection.get("case_names", ()))
        cases = (
            tuple(item for item in raw_cases if isinstance(item, str) and item)
            if isinstance(raw_cases, Sequence) and not isinstance(raw_cases, (str, bytes))
            else ()
        )
        raw_repeat = manifest.get("repeat", selection.get("repeat", 1))
        repeat = (
            raw_repeat
            if isinstance(raw_repeat, int) and not isinstance(raw_repeat, bool) and raw_repeat > 0
            else 1
        )
        return cls(
            runtime_version=_manifest_string(
                manifest.get("runtime_version"), payload.get("runtime_version")
            ),
            git_commit=_optional_manifest_string(manifest.get("git_commit")),
            python_version=_manifest_string(manifest.get("python_version")),
            platform=_manifest_string(manifest.get("platform")),
            provider=_manifest_string(manifest.get("provider"), payload.get("provider")),
            model=_manifest_string(manifest.get("model"), payload.get("model")),
            suite=_manifest_string(manifest.get("suite"), payload.get("suite_name")),
            cases=cases,
            repeat=repeat,
            started_at=_optional_manifest_string(
                manifest.get("started_at", payload.get("started_at"))
            ),
            finished_at=_optional_manifest_string(
                manifest.get("finished_at", payload.get("completed_at"))
            ),
        )


@dataclass(slots=True)
class AcceptanceReport:
    id: str
    suite_name: str
    suite_version: int
    suite_checksum: str
    runtime_version: str
    provider: str
    model: str
    started_at: str
    completed_at: str
    results: list[AcceptanceCaseResult]
    artifact_path: str | None = None
    source_workspace_exposed: bool = False
    selected_case_names: tuple[str, ...] = ()
    repeat: int = 1
    manifest: AcceptanceManifest | None = None

    @property
    def total_attempts(self) -> int:
        return len(self.results)

    @property
    def passed_attempts(self) -> int:
        return sum(item.passed for item in self.results)

    @property
    def failed_attempts(self) -> int:
        return self.total_attempts - self.passed_attempts

    @property
    def pass_rate(self) -> float:
        return round(self.passed_attempts / self.total_attempts, 4) if self.results else 0.0

    def to_dict(self) -> dict[str, Any]:
        manifest = self.manifest or AcceptanceManifest(
            runtime_version=self.runtime_version,
            provider=self.provider,
            model=self.model,
            suite=self.suite_name,
            cases=self.selected_case_names,
            repeat=self.repeat,
            started_at=self.started_at,
            finished_at=self.completed_at,
        )
        return {
            "id": self.id,
            "suite_name": self.suite_name,
            "suite_version": self.suite_version,
            "suite_checksum": self.suite_checksum,
            "runtime_version": self.runtime_version,
            "provider": self.provider,
            "model": self.model,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "manifest": manifest.to_dict(),
            "total_attempts": self.total_attempts,
            "passed_attempts": self.passed_attempts,
            "failed_attempts": self.failed_attempts,
            "pass_rate": self.pass_rate,
            "artifact_path": self.artifact_path,
            "source_workspace_exposed": self.source_workspace_exposed,
            "selection": {
                "case_names": list(self.selected_case_names),
                "repeat": self.repeat,
                "expected_attempts": len(self.selected_case_names) * self.repeat,
                "actual_attempts": self.total_attempts,
            },
            "results": [item.to_dict() for item in self.results],
        }


RuntimeFactory = Callable[[LocalRuntimeSettings], Runtime]


def _manifest_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _optional_manifest_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _current_git_commit(workspace: Path) -> str | None:
    """Return the local revision when available without reading configuration secrets."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _optional_manifest_string(completed.stdout) if completed.returncode == 0 else None


def _build_manifest(
    settings: LocalRuntimeSettings,
    suite: AcceptanceSuite,
    selected: Sequence[AcceptanceCase],
    repeat: int,
    started_at: str,
    finished_at: str,
) -> AcceptanceManifest:
    return AcceptanceManifest(
        runtime_version=__version__,
        git_commit=_current_git_commit(settings.workspace),
        python_version=platform.python_version(),
        platform=platform.platform(),
        provider=settings.provider,
        model=settings.model,
        suite=suite.name,
        cases=tuple(case.name for case in selected),
        repeat=repeat,
        started_at=started_at,
        finished_at=finished_at,
    )


def _acceptance_allowed_executables(configured: tuple[str, ...]) -> tuple[str, ...]:
    """Use the runner interpreter for isolated fixture commands."""
    runner_python = str(Path(sys.executable).resolve())
    non_python = tuple(
        item
        for item in configured
        if not Path(item).name.lower().startswith("python")
    )
    return tuple(dict.fromkeys((runner_python, *non_python)))


def ensure_real_model_acceptance_ready(settings: LocalRuntimeSettings) -> None:
    """Reject missing real-model credentials before creating runtime artifacts."""
    if settings.provider != "openai-compatible":
        return
    api_key = os.getenv(settings.api_key_env)
    if api_key is None or not api_key.strip():
        raise AcceptanceSuiteError(
            "Real-model acceptance requires the configured API key environment variable "
            f"{settings.api_key_env!r}. No key value was read or logged."
        )


class RealModelAcceptanceRunner:
    """Run fixed cases in isolated synthetic workspaces and report durable facts only."""

    def __init__(
        self,
        settings: LocalRuntimeSettings,
        *,
        runtime_factory: RuntimeFactory = create_configured_local_runtime,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory

    async def run(
        self,
        suite: AcceptanceSuite,
        *,
        case_names: Sequence[str] = (),
        repeat: int = 1,
        output_path: Path | None = None,
    ) -> AcceptanceReport:
        if repeat < 1:
            raise ValueError("repeat must be at least 1.")
        ensure_real_model_acceptance_ready(self.settings)
        selected = _select_cases(suite, case_names)
        report_id = new_id("acceptance")
        report_root = self.settings.state_dir / "evals" / report_id
        report_root.mkdir(parents=True, exist_ok=False)
        started_at = utc_now()
        results: list[AcceptanceCaseResult] = []
        for attempt in range(1, repeat + 1):
            for case in selected:
                results.append(await self._run_case(report_root, report_id, case, attempt))
        completed_at = utc_now().isoformat()
        report = AcceptanceReport(
            id=report_id,
            suite_name=suite.name,
            suite_version=suite.version,
            suite_checksum=suite.checksum,
            runtime_version=__version__,
            provider=self.settings.provider,
            model=self.settings.model,
            started_at=started_at.isoformat(),
            completed_at=completed_at,
            results=results,
            selected_case_names=tuple(case.name for case in selected),
            repeat=repeat,
            manifest=_build_manifest(
                self.settings,
                suite,
                selected,
                repeat,
                started_at.isoformat(),
                completed_at,
            ),
        )
        target = (output_path or report_root / "acceptance-report.json").resolve()
        report.artifact_path = str(target)
        _write_report(target, report)
        _write_report(self.settings.state_dir / "evals" / "latest-report.json", report)
        return report

    async def _run_case(
        self,
        report_root: Path,
        report_id: str,
        case: AcceptanceCase,
        attempt: int,
    ) -> AcceptanceCaseResult:
        case_root = report_root / "cases" / f"{attempt:02d}-{case.name}"
        workspace = case_root / "workspace"
        state_dir = case_root / "state"
        _prepare_fixture(workspace, case)
        settings = replace(
            self.settings,
            workspace=workspace,
            state_dir=state_dir,
            log_file=state_dir / "runtime.log",
            allowed_executables=_acceptance_allowed_executables(
                self.settings.allowed_executables
            ),
        )
        runtime: Runtime | None = None
        run: AgentRun | None = None
        error_code: str | None = None
        started = time.perf_counter()
        try:
            runtime = self.runtime_factory(settings)
            submitted = runtime.submit(
                settings.agent_name,
                case.prompt,
                metadata={
                    "acceptance_report_id": report_id,
                    "acceptance_case": case.name,
                    "acceptance_attempt": attempt,
                    "isolated_synthetic_workspace": True,
                },
            )
            run = submitted.run
            run = await runtime.wait(run.id, timeout_seconds=case.timeout_seconds)
            run = await self._resolve_approvals(runtime, run, case)
        except TimeoutError:
            error_code = "acceptance_timeout"
            if runtime is not None and run is not None:
                run = runtime.cancel(run.id)
        except Exception as error:
            error_code = type(error).__name__
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        try:
            if runtime is None or run is None:
                assertion = AcceptanceAssertion(
                    "harness_execution",
                    False,
                    "durable run",
                    error_code or "missing",
                    "The harness did not produce a durable Run.",
                )
                return AcceptanceCaseResult(
                    case.name,
                    case.category,
                    attempt,
                    None,
                    None,
                    "harness_error",
                    False,
                    duration_ms,
                    0,
                    None,
                    error_code,
                    _empty_metrics(),
                    (assertion,),
                )
            durable_run = runtime.store.get_run(run.id)
            metrics = _collect_metrics(runtime, durable_run)
            assertions = _evaluate_case(case, durable_run, metrics)
            answer = durable_run.result or ""
            return AcceptanceCaseResult(
                case.name,
                case.category,
                attempt,
                durable_run.id,
                str(durable_run.metadata.get("trace_id") or durable_run.id),
                durable_run.status.value,
                all(item.passed for item in assertions),
                duration_ms,
                len(answer),
                _sha256(answer) if answer else None,
                error_code or ("run_error" if durable_run.error else None),
                metrics,
                assertions,
            )
        finally:
            if runtime is not None:
                await runtime.shutdown(timeout_seconds=self.settings.shutdown_timeout_seconds)

    async def _resolve_approvals(
        self,
        runtime: Runtime,
        run: AgentRun,
        case: AcceptanceCase,
    ) -> AgentRun:
        resolutions = 0
        while run.status is RunStatus.WAITING_FOR_APPROVAL:
            if case.approval_action == "none":
                return run
            if resolutions >= 8:
                raise AcceptanceSuiteError(f"Case {case.name!r} exceeded the approval limit.")
            approval = runtime.store.pending_approval(run.id)
            if approval is None:
                raise AcceptanceSuiteError(f"Case {case.name!r} is waiting without an approval.")
            runtime.resolve_approval(
                approval.id,
                case.approval_action == "approve",
                "isolated acceptance fixture policy",
            )
            resolutions += 1
            run = await asyncio.wait_for(
                runtime.resume(run.id),
                timeout=case.timeout_seconds,
            )
        return run


def load_acceptance_suite(name_or_path: str | Path) -> AcceptanceSuite:
    requested = Path(name_or_path)
    if requested.is_file():
        raw = requested.read_bytes()
        source = str(requested.resolve())
    else:
        name = str(name_or_path)
        if not name.endswith(".json"):
            name = f"{name}.json"
        resource = resources.files("agent_runtime").joinpath("eval_suites", name)
        if not resource.is_file():
            raise AcceptanceSuiteError(f"Acceptance suite was not found: {name_or_path}")
        raw = resource.read_bytes()
        source = f"builtin:{name}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AcceptanceSuiteError(f"Acceptance suite is invalid JSON: {source}") from error
    if not isinstance(payload, dict):
        raise AcceptanceSuiteError("Acceptance suite root must be an object.")
    return _suite_from_dict(payload, source=source, checksum=_sha256_bytes(raw))


def _suite_from_dict(
    payload: Mapping[str, Any],
    *,
    source: str,
    checksum: str,
) -> AcceptanceSuite:
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise AcceptanceSuiteError("Acceptance suite must contain at least one case.")
    cases = tuple(_case_from_dict(item) for item in raw_cases if isinstance(item, dict))
    if len(cases) != len(raw_cases):
        raise AcceptanceSuiteError("Every acceptance case must be an object.")
    names = [case.name for case in cases]
    if len(names) != len(set(names)):
        raise AcceptanceSuiteError("Acceptance case names must be unique.")
    return AcceptanceSuite(
        name=_required_string(payload, "name"),
        version=_positive_int(payload.get("version", 1), "version"),
        description=_required_string(payload, "description"),
        cases=cases,
        source=source,
        checksum=checksum,
    )


def _case_from_dict(payload: Mapping[str, Any]) -> AcceptanceCase:
    raw_files = payload.get("files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise AcceptanceSuiteError("Each acceptance case requires fixture files.")
    files: dict[str, str] = {}
    for raw_path, raw_content in raw_files.items():
        if not isinstance(raw_path, str) or not isinstance(raw_content, str):
            raise AcceptanceSuiteError("Fixture paths and contents must be strings.")
        files[_safe_relative_path(raw_path).as_posix()] = raw_content
    raw_limits = payload.get("limits", {})
    if not isinstance(raw_limits, dict):
        raise AcceptanceSuiteError("Case limits must be an object.")
    approval_action = str(payload.get("approval_action", "none"))
    if approval_action not in {"none", "approve", "reject"}:
        raise AcceptanceSuiteError("approval_action must be none, approve, or reject.")
    return AcceptanceCase(
        name=_required_string(payload, "name"),
        category=_required_string(payload, "category"),
        description=_required_string(payload, "description"),
        prompt=_required_string(payload, "prompt"),
        files=files,
        limits=AcceptanceLimits(
            expected_status=str(raw_limits.get("expected_status", "completed")),
            min_final_answer_chars=_non_negative_int(
                raw_limits.get("min_final_answer_chars", 1), "min_final_answer_chars"
            ),
            max_steps=_positive_int(raw_limits.get("max_steps", 20), "max_steps"),
            max_tool_calls=_non_negative_int(
                raw_limits.get("max_tool_calls", 40), "max_tool_calls"
            ),
            max_duplicate_tool_calls=_non_negative_int(
                raw_limits.get("max_duplicate_tool_calls", 4),
                "max_duplicate_tool_calls",
            ),
            max_failed_tool_calls=_non_negative_int(
                raw_limits.get("max_failed_tool_calls", 2), "max_failed_tool_calls"
            ),
            max_protocol_violations=_non_negative_int(
                raw_limits.get("max_protocol_violations", 0),
                "max_protocol_violations",
            ),
            min_approval_requests=_non_negative_int(
                raw_limits.get("min_approval_requests", 0), "min_approval_requests"
            ),
            forbidden_tools=_string_tuple(raw_limits.get("forbidden_tools", [])),
            required_tool_groups=_string_groups(
                raw_limits.get("required_tool_groups", [])
            ),
            require_verified_if_changed=bool(
                raw_limits.get("require_verified_if_changed", False)
            ),
        ),
        approval_action=approval_action,
        initialize_git=bool(payload.get("initialize_git", True)),
        timeout_seconds=float(payload.get("timeout_seconds", 240.0)),
    )


def _select_cases(
    suite: AcceptanceSuite,
    requested_names: Sequence[str],
) -> tuple[AcceptanceCase, ...]:
    if not requested_names:
        return suite.cases
    requested = set(requested_names)
    selected = tuple(case for case in suite.cases if case.name in requested)
    missing = requested - {case.name for case in selected}
    if missing:
        raise AcceptanceSuiteError(
            f"Unknown acceptance cases: {', '.join(sorted(missing))}"
        )
    return selected


def _prepare_fixture(workspace: Path, case: AcceptanceCase) -> None:
    workspace.mkdir(parents=True, exist_ok=False)
    for raw_path, content in case.files.items():
        target = workspace.joinpath(*PurePosixPath(raw_path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if case.initialize_git:
        _initialize_git_repository(workspace)


def _initialize_git_repository(workspace: Path) -> None:
    if shutil.which("git") is None:
        raise AcceptanceSuiteError("Git is required by the real-model acceptance fixture.")
    commands = (
        ("git", "init", "--quiet"),
        ("git", "config", "user.name", "Agent Runtime Eval"),
        ("git", "config", "user.email", "eval@agent-runtime.local"),
        ("git", "add", "."),
        ("git", "commit", "--quiet", "-m", "acceptance fixture baseline"),
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if completed.returncode != 0:
            raise AcceptanceSuiteError(
                f"Could not initialize isolated Git fixture at step {command[1]!r}."
            )


def _collect_metrics(runtime: Runtime, run: AgentRun) -> AcceptanceMetrics:
    events = runtime.store.events_since(run.id)
    executions = runtime.store.tool_executions_for_run(run.id)
    event_counts = Counter(event.type for event in events)
    tool_counts = Counter(item.tool_call.name for item in executions)
    signatures = Counter(_tool_signature(item) for item in executions)
    duplicate_tool_calls = sum(count - 1 for count in signatures.values() if count > 1)
    failed_statuses = {
        ToolExecutionStatus.FAILED,
        ToolExecutionStatus.REJECTED,
        ToolExecutionStatus.CANCELLED,
    }
    write_executions = [
        item
        for item in executions
        if item.tool_call.name in _WRITE_TOOLS
        and item.status is ToolExecutionStatus.COMPLETED
    ]
    latest_write_index = max(
        (index for index, item in enumerate(executions) if item in write_executions),
        default=-1,
    )
    post_change_executions = (
        executions[latest_write_index + 1 :] if latest_write_index >= 0 else ()
    )
    validation_attempts = [
        item for item in post_change_executions if _is_validation_execution(item)
    ]
    validation_successes = sum(
        item.status is ToolExecutionStatus.COMPLETED
        and isinstance((item.result_data or {}).get("exit_code"), int)
        and (item.result_data or {}).get("exit_code") == 0
        for item in validation_attempts
    )
    code_changed = any(_execution_changes_code(item) for item in write_executions)
    diff_inspected = any(
        item.tool_call.name == "git_diff"
        and item.status is ToolExecutionStatus.COMPLETED
        for item in post_change_executions
    )
    git_status_inspected = any(
        item.tool_call.name == "git_status"
        and item.status is ToolExecutionStatus.COMPLETED
        for item in post_change_executions
    )
    created_file_writes = sum(
        item.tool_call.name == "write_text_file"
        and (item.result_data or {}).get("created") is True
        for item in write_executions
    )
    if not write_executions:
        verification_status = "not_required"
    elif (
        diff_inspected
        and (created_file_writes == 0 or git_status_inspected)
        and (not code_changed or validation_successes > 0)
    ):
        verification_status = "verified"
    else:
        verification_status = "unverified"
    return AcceptanceMetrics(
        step_count=run.step_count,
        tool_call_count=run.tool_call_count,
        tool_counts=dict(sorted(tool_counts.items())),
        duplicate_tool_calls=duplicate_tool_calls,
        failed_tool_calls=sum(item.status in failed_statuses for item in executions),
        unknown_tool_calls=sum(
            item.status is ToolExecutionStatus.UNKNOWN for item in executions
        ),
        write_tool_calls=len(write_executions),
        approval_requests=event_counts["approval.requested"],
        approval_resolutions=event_counts["approval.resolved"],
        model_requests=event_counts["model.requested"],
        model_retries=event_counts["model.retry.scheduled"],
        convergence_warnings=event_counts["convergence.warning"],
        finalization_requests=event_counts["convergence.finalization_requested"],
        finalization_contexts=event_counts["convergence.finalization_context_built"],
        protocol_violations=sum(event_counts[name] for name in _PROTOCOL_EVENTS),
        event_count=len(events),
        event_type_counts=dict(sorted(event_counts.items())),
        verification_status=verification_status,
        validation_attempts=len(validation_attempts),
        validation_successes=validation_successes,
        diff_inspected=diff_inspected,
        git_status_inspected=git_status_inspected,
        created_file_writes=created_file_writes,
    )


def _evaluate_case(
    case: AcceptanceCase,
    run: AgentRun,
    metrics: AcceptanceMetrics,
) -> tuple[AcceptanceAssertion, ...]:
    limits = case.limits
    used_tools = set(metrics.tool_counts)
    assertions = [
        _assert_equal("expected_status", limits.expected_status, run.status.value),
        _assert_at_least(
            "final_answer_present", limits.min_final_answer_chars, len(run.result or "")
        ),
        _assert_at_most("step_budget", limits.max_steps, metrics.step_count),
        _assert_at_most("tool_budget", limits.max_tool_calls, metrics.tool_call_count),
        _assert_at_most(
            "duplicate_tool_budget",
            limits.max_duplicate_tool_calls,
            metrics.duplicate_tool_calls,
        ),
        _assert_at_most(
            "failed_tool_budget", limits.max_failed_tool_calls, metrics.failed_tool_calls
        ),
        _assert_at_most(
            "protocol_integrity",
            limits.max_protocol_violations,
            metrics.protocol_violations,
        ),
        _assert_at_least(
            "approval_lifecycle", limits.min_approval_requests, metrics.approval_requests
        ),
    ]
    forbidden_used = sorted(used_tools.intersection(limits.forbidden_tools))
    assertions.append(
        AcceptanceAssertion(
            "forbidden_tools",
            not forbidden_used,
            [],
            forbidden_used,
            "No forbidden tools were used."
            if not forbidden_used
            else "The Run used forbidden tools.",
        )
    )
    for index, group in enumerate(limits.required_tool_groups, start=1):
        matched = sorted(used_tools.intersection(group))
        assertions.append(
            AcceptanceAssertion(
                f"required_tool_group_{index}",
                bool(matched),
                list(group),
                matched,
                "A required tool was used."
                if matched
                else "No tool from the required group was used.",
            )
        )
    if limits.require_verified_if_changed:
        verified = metrics.write_tool_calls == 0 or metrics.verification_status == "verified"
        assertions.append(
            AcceptanceAssertion(
                "verified_if_changed",
                verified,
                "verified or unchanged",
                metrics.verification_status,
                "Workspace changes were verified."
                if verified
                else "Workspace changes were not fully verified.",
            )
        )
    return tuple(assertions)


def _assert_equal(name: str, expected: Any, actual: Any) -> AcceptanceAssertion:
    passed = actual == expected
    return AcceptanceAssertion(
        name,
        passed,
        expected,
        actual,
        "Value matched." if passed else "Value did not match.",
    )


def _assert_at_most(name: str, expected: int, actual: int) -> AcceptanceAssertion:
    passed = actual <= expected
    return AcceptanceAssertion(
        name,
        passed,
        {"max": expected},
        actual,
        "Value stayed within the maximum." if passed else "Maximum was exceeded.",
    )


def _assert_at_least(name: str, expected: int, actual: int) -> AcceptanceAssertion:
    passed = actual >= expected
    return AcceptanceAssertion(
        name,
        passed,
        {"min": expected},
        actual,
        "Value met the minimum." if passed else "Minimum was not met.",
    )


def _tool_signature(execution: ToolExecution) -> str:
    raw = json.dumps(
        {
            "name": execution.tool_call.name,
            "arguments": execution.tool_call.arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256(raw)


def _is_validation_execution(execution: ToolExecution) -> bool:
    if execution.tool_call.name != "run_process":
        return False
    argv = execution.tool_call.arguments.get("argv")
    return (
        isinstance(argv, list)
        and all(isinstance(item, str) for item in argv)
        and looks_like_validation_command(argv)
    )


def _execution_changes_code(execution: ToolExecution) -> bool:
    paths: list[str] = []
    data = execution.result_data or {}
    if execution.tool_call.name == "apply_patch":
        files = data.get("files")
        if isinstance(files, list):
            paths.extend(
                str(item["path"])
                for item in files
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            )
    else:
        path = data.get("path") or execution.tool_call.arguments.get("path")
        if isinstance(path, str):
            paths.append(path)
    return any(Path(path).suffix.casefold() in _CODE_SUFFIXES for path in paths)


def _empty_metrics() -> AcceptanceMetrics:
    return AcceptanceMetrics(
        step_count=0,
        tool_call_count=0,
        tool_counts={},
        duplicate_tool_calls=0,
        failed_tool_calls=0,
        unknown_tool_calls=0,
        write_tool_calls=0,
        approval_requests=0,
        approval_resolutions=0,
        model_requests=0,
        model_retries=0,
        convergence_warnings=0,
        finalization_requests=0,
        finalization_contexts=0,
        protocol_violations=0,
        event_count=0,
        event_type_counts={},
        verification_status="not_available",
        validation_attempts=0,
        validation_successes=0,
        diff_inspected=False,
        git_status_inspected=False,
        created_file_writes=0,
    )


def _write_report(path: Path, report: AcceptanceReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _safe_relative_path(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise AcceptanceSuiteError(f"Unsafe fixture path: {raw!r}")
    return path


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceSuiteError(f"{key} must be a non-empty string.")
    return value


def _positive_int(value: Any, name: str) -> int:
    parsed = _non_negative_int(value, name)
    if parsed < 1:
        raise AcceptanceSuiteError(f"{name} must be at least 1.")
    return parsed


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcceptanceSuiteError(f"{name} must be a non-negative integer.")
    return value


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AcceptanceSuiteError("Expected a list of strings.")
    return tuple(value)


def _string_groups(value: Any) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise AcceptanceSuiteError("required_tool_groups must be a list.")
    groups: list[tuple[str, ...]] = []
    for raw_group in value:
        group = _string_tuple(raw_group)
        if not group:
            raise AcceptanceSuiteError("Required tool groups must not be empty.")
        groups.append(group)
    return tuple(groups)


def _sha256(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

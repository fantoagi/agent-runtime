from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import ToolExecution, ToolExecutionStatus

_WRITE_TOOL_NAMES = frozenset({"apply_patch", "replace_text", "write_text_file"})
_CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scss",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)
_DIRECT_VALIDATION_COMMANDS = frozenset(
    {
        "cargo",
        "dotnet",
        "go",
        "gradle",
        "gradlew",
        "gradlew.bat",
        "mvn",
        "mvnw",
        "mvnw.cmd",
        "mypy",
        "mypy.exe",
        "nox",
        "nox.exe",
        "npm",
        "npm.cmd",
        "pnpm",
        "pnpm.cmd",
        "pyright",
        "pyright.exe",
        "pytest",
        "pytest.exe",
        "ruff",
        "ruff.exe",
        "tox",
        "tox.exe",
        "yarn",
        "yarn.cmd",
    }
)
_PYTHON_VALIDATION_MODULES = frozenset(
    {"mypy", "pyright", "pytest", "ruff", "unittest"}
)
_PYTHON_VALIDATION_SCRIPTS = frozenset(
    {
        "check_coverage.py",
        "check_docs.py",
        "verify_distribution.py",
        "verify_local_runtime.py",
    }
)


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    command: str
    exit_code: int | None
    succeeded: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "succeeded": self.succeeded,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceStatusEvidence:
    """Durable, non-attributing snapshot of Git workspace status."""

    kind: str
    path: str
    original_path: str | None = None
    index_status: str = " "
    worktree_status: str = " "

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "original_path": self.original_path,
            "index_status": self.index_status,
            "worktree_status": self.worktree_status,
        }


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    status: str
    changed_files: tuple[str, ...]
    write_tool_count: int
    diff_required: bool
    diff_inspected: bool
    git_status_required: bool
    git_status_inspected: bool
    validation_required: bool
    validations: tuple[ValidationEvidence, ...]
    failed_tools: tuple[str, ...]
    rejected_tools: tuple[str, ...]
    verification_requested: bool
    unmet_requirements: tuple[str, ...]
    write_changed: bool | None = None
    workspace_baseline_captured: bool = False
    dirty_workspace_before_write: bool | None = None
    workspace_before: tuple[WorkspaceStatusEvidence, ...] = ()
    workspace_after: tuple[WorkspaceStatusEvidence, ...] = ()
    tracked_diff_present: bool | None = None
    untracked_created_files: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    renamed_files: tuple[tuple[str, str], ...] = ()

    @property
    def validation_succeeded(self) -> bool | None:
        if not self.validations:
            return None
        return any(item.succeeded for item in self.validations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "changed_files": list(self.changed_files),
            "write_tool_count": self.write_tool_count,
            "diff_required": self.diff_required,
            "diff_inspected": self.diff_inspected,
            "git_status_required": self.git_status_required,
            "git_status_inspected": self.git_status_inspected,
            "validation_required": self.validation_required,
            "validation_attempted": bool(self.validations),
            "validation_succeeded": self.validation_succeeded,
            "validations": [item.to_dict() for item in self.validations],
            "failed_tools": list(self.failed_tools),
            "rejected_tools": list(self.rejected_tools),
            "verification_requested": self.verification_requested,
            "unmet_requirements": list(self.unmet_requirements),
            "write_changed": self.write_changed,
            "workspace_baseline_captured": self.workspace_baseline_captured,
            "dirty_workspace_before_write": self.dirty_workspace_before_write,
            "workspace_before": [item.to_dict() for item in self.workspace_before],
            "workspace_after": [item.to_dict() for item in self.workspace_after],
            "tracked_diff_present": self.tracked_diff_present,
            "untracked_created_files": list(self.untracked_created_files),
            "deleted_files": list(self.deleted_files),
            "renamed_files": [list(item) for item in self.renamed_files],
        }


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    evidence: CompletionEvidence
    followup_message: str | None = None


class CompletionPolicy(Protocol):
    def assess(
        self,
        executions: Sequence[ToolExecution],
        *,
        verification_requested: bool,
    ) -> CompletionDecision: ...


class CodingCompletionPolicy:
    """Require one evidence-aware follow-up after local coding writes when needed."""

    def __init__(self, available_tool_names: Iterable[str]) -> None:
        self.available_tool_names = frozenset(available_tool_names)

    def assess(
        self,
        executions: Sequence[ToolExecution],
        *,
        verification_requested: bool,
    ) -> CompletionDecision:
        completed_writes = [
            (index, execution)
            for index, execution in enumerate(executions)
            if execution.tool_call.name in _WRITE_TOOL_NAMES
            and execution.status is ToolExecutionStatus.COMPLETED
        ]
        changed_files = _changed_files(execution for _, execution in completed_writes)
        failed_tools = tuple(
            execution.tool_call.name
            for execution in executions
            if execution.status is ToolExecutionStatus.FAILED
        )
        rejected_tools = tuple(
            execution.tool_call.name
            for execution in executions
            if execution.status is ToolExecutionStatus.REJECTED
        )
        if not completed_writes:
            evidence = CompletionEvidence(
                status="read_only",
                changed_files=(),
                write_tool_count=0,
                diff_required=False,
                diff_inspected=False,
                git_status_required=False,
                git_status_inspected=False,
                validation_required=False,
                validations=(),
                failed_tools=failed_tools,
                rejected_tools=rejected_tools,
                verification_requested=verification_requested,
                unmet_requirements=(),
            )
            return CompletionDecision(evidence=evidence)

        latest_write_index = completed_writes[-1][0]
        later_executions = executions[latest_write_index + 1 :]
        diff_required = "git_diff" in self.available_tool_names
        diff_inspected = any(
            execution.tool_call.name == "git_diff"
            and execution.status is ToolExecutionStatus.COMPLETED
            for execution in later_executions
        )
        created_file_written = any(
            execution.tool_call.name == "write_text_file"
            and (execution.result_data or {}).get("created") is True
            for _, execution in completed_writes
        )
        git_status_required = created_file_written and "git_status" in self.available_tool_names
        git_status_inspected = any(
            execution.tool_call.name == "git_status"
            and execution.status is ToolExecutionStatus.COMPLETED
            for execution in later_executions
        )
        code_changed = any(Path(path).suffix.casefold() in _CODE_SUFFIXES for path in changed_files)
        validation_required = code_changed and "run_process" in self.available_tool_names
        validations = tuple(
            validation
            for execution in later_executions
            if (validation := _validation_evidence(execution)) is not None
        )
        write_changed = _write_changed(completed_writes)
        workspace_before = _latest_workspace_status(
            executions[: completed_writes[0][0]]
        )
        workspace_after = _latest_workspace_status(later_executions)
        tracked_diff_present = _latest_diff_presence(later_executions)
        untracked_created_files = tuple(
            item.path
            for item in workspace_after or ()
            if item.kind == "untracked" and item.path in changed_files
        )
        deleted_files = tuple(item.path for item in workspace_after or () if item.kind == "deleted")
        renamed_files = tuple(
            (item.original_path or item.path, item.path)
            for item in workspace_after or ()
            if item.kind == "renamed"
        )

        unmet: list[str] = []
        if write_changed is False:
            unmet.append("no workspace file changed by a completed write Tool")
        if diff_required and not diff_inspected:
            unmet.append("post-change Git diff was not inspected")
        if git_status_required and not git_status_inspected:
            unmet.append("post-change Git status was not inspected for a newly created file")
        if validation_required:
            if not validations:
                unmet.append("no post-change validation command was run")
            elif not any(item.succeeded for item in validations):
                unmet.append("post-change validation did not succeed")

        status = "verified" if not unmet else "unverified"
        evidence = CompletionEvidence(
            status=status,
            changed_files=changed_files,
            write_tool_count=len(completed_writes),
            diff_required=diff_required,
            diff_inspected=diff_inspected,
            git_status_required=git_status_required,
            git_status_inspected=git_status_inspected,
            validation_required=validation_required,
            validations=validations,
            failed_tools=failed_tools,
            rejected_tools=rejected_tools,
            verification_requested=verification_requested,
            unmet_requirements=tuple(unmet),
            write_changed=write_changed,
            workspace_baseline_captured=workspace_before is not None,
            dirty_workspace_before_write=bool(workspace_before) if workspace_before is not None else None,
            workspace_before=workspace_before or (),
            workspace_after=workspace_after or (),
            tracked_diff_present=tracked_diff_present,
            untracked_created_files=untracked_created_files,
            deleted_files=deleted_files,
            renamed_files=renamed_files,
        )
        if not unmet or verification_requested:
            return CompletionDecision(evidence=evidence)
        return CompletionDecision(
            evidence=evidence,
            followup_message=_followup_message(evidence),
        )


def _write_changed(
    completed_writes: Sequence[tuple[int, ToolExecution]],
) -> bool | None:
    values = [
        value
        for _, execution in completed_writes
        if (value := _write_change_value(execution)) is not None
    ]
    if not values:
        return None
    return any(values)


def _write_change_value(execution: ToolExecution) -> bool | None:
    data = execution.result_data or {}
    explicit = data.get("changed")
    if isinstance(explicit, bool):
        return explicit
    if execution.tool_call.name == "apply_patch":
        files = data.get("files")
        if isinstance(files, list):
            values = [
                item["before_sha256"] != item["after_sha256"]
                for item in files
                if isinstance(item, dict)
                and isinstance(item.get("before_sha256"), str)
                and isinstance(item.get("after_sha256"), str)
            ]
            if values:
                return any(values)
    before = data.get("before_sha256")
    after = data.get("after_sha256")
    if isinstance(before, str) and isinstance(after, str):
        return before != after
    return None


def _latest_workspace_status(
    executions: Iterable[ToolExecution],
) -> tuple[WorkspaceStatusEvidence, ...] | None:
    latest: tuple[WorkspaceStatusEvidence, ...] | None = None
    for execution in executions:
        if execution.tool_call.name != "git_status" or execution.status is not ToolExecutionStatus.COMPLETED:
            continue
        data = execution.result_data or {}
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            continue
        entries: list[WorkspaceStatusEvidence] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            path = raw.get("path")
            kind = raw.get("kind")
            if not isinstance(path, str) or not isinstance(kind, str):
                continue
            original_path = raw.get("original_path")
            entries.append(
                WorkspaceStatusEvidence(
                    kind=kind,
                    path=path,
                    original_path=original_path if isinstance(original_path, str) else None,
                    index_status=str(raw.get("index_status") or " "),
                    worktree_status=str(raw.get("worktree_status") or " "),
                )
            )
        latest = tuple(entries)
    return latest


def _latest_diff_presence(executions: Iterable[ToolExecution]) -> bool | None:
    latest: bool | None = None
    for execution in executions:
        if execution.tool_call.name != "git_diff" or execution.status is not ToolExecutionStatus.COMPLETED:
            continue
        value = (execution.result_data or {}).get("has_changes")
        if isinstance(value, bool):
            latest = value
    return latest


def _changed_files(executions: Iterable[ToolExecution]) -> tuple[str, ...]:
    paths: list[str] = []
    for execution in executions:
        data = execution.result_data or {}
        if _write_change_value(execution) is False:
            continue
        if execution.tool_call.name == "apply_patch":
            files = data.get("files")
            if isinstance(files, list):
                for item in files:
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                        continue
                    before = item.get("before_sha256")
                    after = item.get("after_sha256")
                    if isinstance(before, str) and isinstance(after, str) and before == after:
                        continue
                    paths.append(item["path"])
            continue
        path = data.get("path") or execution.tool_call.arguments.get("path")
        if isinstance(path, str):
            paths.append(path)
    return tuple(dict.fromkeys(paths))


def _validation_evidence(execution: ToolExecution) -> ValidationEvidence | None:
    if execution.tool_call.name != "run_process":
        return None
    raw_argv = execution.tool_call.arguments.get("argv")
    if not isinstance(raw_argv, list) or not raw_argv or not all(
        isinstance(item, str) for item in raw_argv
    ):
        return None
    argv = tuple(raw_argv)
    if not looks_like_validation_command(argv):
        return None
    data = execution.result_data or {}
    raw_exit_code = data.get("exit_code")
    exit_code = raw_exit_code if isinstance(raw_exit_code, int) else None
    succeeded = execution.status is ToolExecutionStatus.COMPLETED and exit_code == 0
    return ValidationEvidence(
        command=" ".join(argv),
        exit_code=exit_code,
        succeeded=succeeded,
    )


def looks_like_validation_command(argv: Sequence[str]) -> bool:
    """Return whether argv is a known test, check, lint, or verification command."""
    if not argv:
        return False
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    arguments = tuple(item.casefold() for item in argv[1:])
    if executable in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        for index, item in enumerate(arguments[:-1]):
            if item == "-m" and arguments[index + 1] in _PYTHON_VALIDATION_MODULES:
                return True
        return any(
            item.replace("\\", "/").rsplit("/", 1)[-1] in _PYTHON_VALIDATION_SCRIPTS
            for item in arguments
        )
    if executable not in _DIRECT_VALIDATION_COMMANDS:
        return False
    if executable.startswith(("npm", "pnpm", "yarn")):
        return any(item in {"test", "check", "lint", "typecheck"} for item in arguments)
    if executable == "go":
        return any(item in {"test", "vet"} for item in arguments)
    if executable == "cargo":
        return any(item in {"test", "check", "clippy"} for item in arguments)
    if executable == "dotnet":
        return any(item in {"test", "build"} for item in arguments)
    return True


def _looks_like_validation(argv: tuple[str, ...]) -> bool:
    """Backward-compatible private alias for existing callers and tests."""
    return looks_like_validation_command(argv)


def _followup_message(evidence: CompletionEvidence) -> str:
    files = ", ".join(evidence.changed_files[:8]) or "workspace files"
    requirements = "\n".join(f"- {item}" for item in evidence.unmet_requirements)
    return (
        "[Runtime completion check]\n"
        f"You changed: {files}. The Run is not complete yet because:\n{requirements}\n"
        "Inspect the resulting diff and run the narrowest useful validation that the available "
        "tools permit. If validation is not applicable or cannot run, state that explicitly in "
        "the final answer. Never claim a command or test passed without a successful Tool result."
    )

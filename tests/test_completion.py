from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_runtime.completion import CodingCompletionPolicy, _looks_like_validation
from agent_runtime.domain import (
    AgentDefinition,
    ModelConfig,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolExecution,
    ToolExecutionStatus,
)
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry, ToolResult


def _definition(name: str) -> ToolDefinition:
    schemas: dict[str, dict[str, Any]] = {
        "write_text_file": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "git_diff": {"type": "object", "properties": {}, "additionalProperties": False},
        "run_process": {
            "type": "object",
            "properties": {"argv": {"type": "array", "items": {"type": "string"}}},
            "required": ["argv"],
            "additionalProperties": False,
        },
    }
    return ToolDefinition(
        name=name,
        description=name,
        input_schema=schemas[name],
        side_effecting=name == "write_text_file",
    )


def _runtime(workspace: Path, responder, *, coding_policy: bool = True) -> Runtime:
    tools = ToolRegistry()
    definitions = [_definition(name) for name in ("write_text_file", "git_diff", "run_process")]
    tools.register(
        definitions[0],
        lambda arguments, context: ToolResult(
            content=f"wrote {arguments['path']}",
            data={"path": arguments["path"], "status": "written"},
        ),
    )
    tools.register(
        definitions[1],
        lambda arguments, context: ToolResult(
            content="diff --git a/example.py b/example.py",
            data={"path": ".", "exit_code": 0},
        ),
    )
    tools.register(
        definitions[2],
        lambda arguments, context: ToolResult(
            content="1 passed",
            data={"argv": arguments["argv"], "exit_code": 0},
        ),
    )
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
        ),
        provider=MockProvider(responder),
        tools=tools,
        completion_policy=(
            CodingCompletionPolicy(definition.name for definition in definitions)
            if coding_policy
            else None
        ),
    )
    runtime.register_agent(
        AgentDefinition(
            name="coding",
            system_prompt="coding",
            tools=definitions,
            model=ModelConfig(provider="mock", model="coding"),
        )
    )
    return runtime


@pytest.mark.asyncio
async def test_read_only_completion_does_not_request_verification(workspace: Path) -> None:
    runtime = _runtime(
        workspace,
        lambda messages, tools, config: ModelResponse(content="read-only answer"),
    )
    try:
        run = await runtime.run("coding", "Explain the project")
        assert run.status is RunStatus.COMPLETED
        assert run.step_count == 1
        events = runtime.store.events_since(run.id)
        assert not any(event.type == "completion.verification_requested" for event in events)
        evidence = next(event for event in events if event.type == "completion.evidence")
        assert evidence.payload["status"] == "read_only"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_coding_completion_requests_diff_and_validation_once(workspace: Path) -> None:
    calls = 0
    saw_runtime_followup = False

    def responder(messages, tools, config):
        nonlocal calls, saw_runtime_followup
        del tools, config
        calls += 1
        if calls == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "write",
                        "write_text_file",
                        {"path": "example.py", "content": "answer = 42\n"},
                    )
                ]
            )
        if calls == 2:
            return ModelResponse(content="Implemented successfully.")
        if calls == 3:
            saw_runtime_followup = any(
                message.role == "system"
                and "Runtime completion check" in (message.content or "")
                for message in messages
            )
            return ModelResponse(
                tool_calls=[
                    ToolCall("diff", "git_diff", {}),
                    ToolCall(
                        "test",
                        "run_process",
                        {"argv": ["python", "-m", "pytest", "tests/test_example.py"]},
                    ),
                ]
            )
        return ModelResponse(content="Implemented and verified: 1 passed.")

    runtime = _runtime(workspace, responder)
    try:
        run = await runtime.run("coding", "Change example.py")
        assert run.status is RunStatus.COMPLETED
        assert run.result == "Implemented and verified: 1 passed."
        assert calls == 4
        assert saw_runtime_followup
        events = runtime.store.events_since(run.id)
        requested = [
            event for event in events if event.type == "completion.verification_requested"
        ]
        assert len(requested) == 1
        assert requested[0].payload["changed_files"] == ["example.py"]
        assert requested[0].payload["unmet_requirements"] == [
            "post-change Git diff was not inspected",
            "no post-change validation command was run",
        ]
        evidence = [event for event in events if event.type == "completion.evidence"][-1]
        assert evidence.payload["status"] == "verified"
        assert evidence.payload["diff_inspected"] is True
        assert evidence.payload["validation_succeeded"] is True
        assert evidence.payload["verification_requested"] is True
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_coding_completion_does_not_loop_when_verification_remains_unavailable(
    workspace: Path,
) -> None:
    calls = 0

    def responder(messages, tools, config):
        nonlocal calls
        del messages, tools, config
        calls += 1
        if calls == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "write",
                        "write_text_file",
                        {"path": "example.py", "content": "answer = 42\n"},
                    )
                ]
            )
        if calls == 2:
            return ModelResponse(content="Done.")
        return ModelResponse(content="Changed the file, but verification was not run.")

    runtime = _runtime(workspace, responder)
    try:
        run = await runtime.run("coding", "Change example.py without verification")
        assert run.status is RunStatus.COMPLETED
        assert calls == 3
        events = runtime.store.events_since(run.id)
        assert sum(
            event.type == "completion.verification_requested" for event in events
        ) == 1
        evidence = [event for event in events if event.type == "completion.evidence"][-1]
        assert evidence.payload["status"] == "unverified"
        assert evidence.payload["verification_requested"] is True
        assert len(evidence.payload["unmet_requirements"]) == 2
    finally:
        await runtime.shutdown()

def _execution(
    name: str,
    arguments: dict[str, Any],
    *,
    status: ToolExecutionStatus = ToolExecutionStatus.COMPLETED,
    result_data: dict[str, Any] | None = None,
    position: int = 0,
) -> ToolExecution:
    execution = ToolExecution.create(
        "run",
        "step",
        position,
        ToolCall(f"call-{position}", name, arguments),
        requires_approval=False,
        side_effecting=name in {"write_text_file", "replace_text", "apply_patch"},
    )
    execution.status = status
    execution.result_data = result_data
    return execution


def test_completion_policy_accepts_docs_change_after_diff_without_process() -> None:
    policy = CodingCompletionPolicy({"write_text_file", "git_diff", "run_process"})
    decision = policy.assess(
        [
            _execution(
                "write_text_file",
                {"path": "README.md", "content": "docs"},
                result_data={"status": "written"},
            ),
            _execution("git_diff", {}, result_data={"exit_code": 0}, position=1),
            _execution(
                "run_process",
                {"argv": []},
                result_data={"exit_code": 0},
                position=2,
            ),
        ],
        verification_requested=False,
    )
    assert decision.followup_message is None
    assert decision.evidence.status == "verified"
    assert decision.evidence.changed_files == ("README.md",)
    assert decision.evidence.validation_required is False
    assert decision.evidence.validation_succeeded is None


def test_completion_policy_reports_failed_validation_and_patch_files() -> None:
    policy = CodingCompletionPolicy({"apply_patch", "git_diff", "run_process"})
    decision = policy.assess(
        [
            _execution(
                "apply_patch",
                {"edits": []},
                result_data={
                    "files": [
                        {"path": "src/example.py"},
                        {"path": "src/example.py"},
                        {"path": 123},
                        "invalid",
                    ]
                },
            ),
            _execution("git_diff", {}, result_data={"exit_code": 0}, position=1),
            _execution(
                "run_process",
                {"argv": ["python", "-m", "pytest", "tests/test_example.py"]},
                status=ToolExecutionStatus.FAILED,
                result_data={"exit_code": 1},
                position=2,
            ),
            _execution(
                "run_process",
                {"argv": ["python", "-c", "print('not validation')"]},
                status=ToolExecutionStatus.REJECTED,
                position=3,
            ),
        ],
        verification_requested=False,
    )
    assert decision.evidence.changed_files == ("src/example.py",)
    assert decision.evidence.validation_succeeded is False
    assert decision.evidence.failed_tools == ("run_process",)
    assert decision.evidence.rejected_tools == ("run_process",)
    assert decision.evidence.unmet_requirements == (
        "post-change validation did not succeed",
    )
    assert decision.followup_message is not None


def test_completion_policy_skips_unavailable_diff_and_process_requirements() -> None:
    policy = CodingCompletionPolicy({"write_text_file"})
    decision = policy.assess(
        [
            _execution(
                "write_text_file",
                {"path": "src/example.py", "content": "answer = 42"},
                result_data={},
            )
        ],
        verification_requested=False,
    )
    assert decision.evidence.status == "verified"
    assert decision.evidence.diff_required is False
    assert decision.evidence.validation_required is False
    assert decision.followup_message is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("python", "-m", "pytest"), True),
        (("python.exe", "-m", "ruff", "check"), True),
        (("py", "-c", "print(1)"), False),
        (("python", "scripts/check_docs.py"), True),
        ((r"C:\Python313\python.exe", r"scripts\verify_distribution.py"), True),
        (("python", "scripts/deploy.py"), False),
        (("pytest", "-q"), True),
        (("npm", "test"), True),
        (("npm.cmd", "install"), False),
        (("pnpm", "lint"), True),
        (("yarn", "typecheck"), True),
        (("go", "test", "./..."), True),
        (("go", "build", "./..."), False),
        (("cargo", "clippy"), True),
        (("cargo", "run"), False),
        (("dotnet", "build"), True),
        (("dotnet", "run"), False),
        (("node", "script.js"), False),
        (("mypy", "src"), True),
    ],
)
def test_validation_command_recognition(argv: tuple[str, ...], expected: bool) -> None:
    assert _looks_like_validation(argv) is expected

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_runtime.domain import (
    AgentDefinition,
    CapabilityPolicyAction,
    ModelConfig,
    SandboxOutputLimitError,
    SandboxTimeoutError,
    SandboxViolationError,
    ToolCall,
    ToolCapability,
    ToolDefinition,
    ToolPolicyDeniedError,
)
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.sandbox import (
    LocalProcessSandbox,
    SandboxLimits,
    SandboxRequest,
    register_process_tool,
)
from agent_runtime.tools import CancellationToken, CapabilityPolicy, ToolRegistry


@pytest.mark.asyncio
async def test_local_process_sandbox_runs_allowed_executable(workspace: Path) -> None:
    sandbox = LocalProcessSandbox(
        workspace,
        allowed_executables=[sys.executable],
        allowed_environment=["AGENT_RUNTIME_TEST"],
    )
    result = await sandbox.execute(
        SandboxRequest(
            argv=(sys.executable, "-c", "import os; print(os.environ['AGENT_RUNTIME_TEST'])"),
            environment={"AGENT_RUNTIME_TEST": "sandbox-ok"},
        )
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "sandbox-ok"
    assert result.cwd == str(workspace.resolve())
    assert sandbox.snapshot()["strong_isolation"] is False
    await sandbox.aclose()


@pytest.mark.asyncio
async def test_local_process_sandbox_rejects_executable_environment_and_cwd_escape(
    workspace: Path,
) -> None:
    sandbox = LocalProcessSandbox(workspace, allowed_executables=[sys.executable])
    with pytest.raises(SandboxViolationError, match="not allowed"):
        await sandbox.execute(SandboxRequest(argv=("cmd.exe", "/c", "echo", "unsafe")))
    with pytest.raises(SandboxViolationError, match="Environment variable"):
        await sandbox.execute(
            SandboxRequest(argv=(sys.executable, "-c", "print('x')"), environment={"SECRET": "x"})
        )
    with pytest.raises(SandboxViolationError, match="escapes"):
        await sandbox.execute(
            SandboxRequest(argv=(sys.executable, "-c", "print('x')"), cwd="..")
        )
    await sandbox.aclose()


@pytest.mark.asyncio
async def test_local_process_sandbox_enforces_timeout_and_output_limit(workspace: Path) -> None:
    timeout_sandbox = LocalProcessSandbox(
        workspace,
        allowed_executables=[sys.executable],
        limits=SandboxLimits(timeout_seconds=0.1, max_output_bytes=4096),
    )
    with pytest.raises(SandboxTimeoutError):
        await timeout_sandbox.execute(
            SandboxRequest(argv=(sys.executable, "-c", "import time; time.sleep(5)"))
        )
    await timeout_sandbox.aclose()

    output_sandbox = LocalProcessSandbox(
        workspace,
        allowed_executables=[sys.executable],
        limits=SandboxLimits(timeout_seconds=5, max_output_bytes=1024),
    )
    with pytest.raises(SandboxOutputLimitError):
        await output_sandbox.execute(
            SandboxRequest(argv=(sys.executable, "-c", "print('x' * 5000)"))
        )
    await output_sandbox.aclose()


@pytest.mark.asyncio
async def test_local_process_sandbox_cancel_terminates_process(workspace: Path) -> None:
    sandbox = LocalProcessSandbox(workspace, allowed_executables=[sys.executable])
    token = CancellationToken()
    task = asyncio.create_task(
        sandbox.execute(
            SandboxRequest(argv=(sys.executable, "-c", "import time; time.sleep(5)")),
            cancellation_token=token,
        )
    )
    while sandbox.snapshot()["active_processes"] == 0:
        await asyncio.sleep(0.01)
    token.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sandbox.snapshot()["active_processes"] == 0
    await sandbox.aclose()


@pytest.mark.asyncio
async def test_local_process_sandbox_task_cancellation_terminates_process(
    workspace: Path,
) -> None:
    sandbox = LocalProcessSandbox(workspace, allowed_executables=[sys.executable])
    task = asyncio.create_task(
        sandbox.execute(
            SandboxRequest(argv=(sys.executable, "-c", "import time; time.sleep(5)"))
        )
    )
    while sandbox.snapshot()["active_processes"] == 0:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sandbox.snapshot()["active_processes"] == 0
    await sandbox.aclose()


def test_capability_policy_denies_network_and_requires_sandbox() -> None:
    registry = ToolRegistry()
    network_tool = ToolDefinition(
        name="network",
        description="network",
        input_schema={"type": "object"},
        capabilities=(ToolCapability.NETWORK_ACCESS,),
    )
    registry.register(network_tool, lambda arguments, context: "never")
    with pytest.raises(ToolPolicyDeniedError, match=r"network\.access"):
        registry.require_authorized("network")

    process_tool = ToolDefinition(
        name="process",
        description="process",
        input_schema={"type": "object"},
        capabilities=(ToolCapability.PROCESS_EXEC,),
    )
    registry.register(process_tool, lambda arguments, context: "never")
    with pytest.raises(ToolPolicyDeniedError, match="managed sandbox"):
        registry.require_authorized("process")

    custom = CapabilityPolicy(
        {ToolCapability.NETWORK_ACCESS: CapabilityPolicyAction.REQUIRE_APPROVAL}
    )
    allowed_registry = ToolRegistry(capability_policy=custom)
    allowed_registry.register(network_tool, lambda arguments, context: "ok")
    assert allowed_registry.require_authorized("network").requires_approval is True


@pytest.mark.asyncio
async def test_runtime_process_tool_requires_approval_and_records_policy(workspace: Path) -> None:
    sandbox = LocalProcessSandbox(workspace, allowed_executables=[sys.executable])
    tools = ToolRegistry()
    process_tool = register_process_tool(tools, sandbox)

    def responder(messages, definitions, config):
        del definitions, config
        if messages[-1].role == "tool":
            return ModelResponse(content=f"sandbox result: {messages[-1].content.strip()}")
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    id="sandbox_call",
                    name="run_process",
                    arguments={
                        "argv": [sys.executable, "-c", "print('sandbox-v0.8')"],
                    },
                )
            ]
        )

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
        ),
        MockProvider(responder),
        tools,
    )
    runtime.register_agent(
        AgentDefinition(
            name="sandbox-agent",
            system_prompt="Use the sandbox process tool.",
            tools=[process_tool],
            model=ModelConfig(),
        )
    )
    run = await runtime.run("sandbox-agent", "run sandbox demo")
    assert run.status == "waiting_for_approval"
    approval = runtime.store.pending_approval(run.id)
    assert approval is not None
    runtime.resolve_approval(approval.id, True, "test approval")
    completed = await runtime.resume(run.id)
    assert completed.status == "completed"
    assert "sandbox-v0.8" in (completed.result or "")

    events = runtime.store.events_since(run.id)
    policy = next(event for event in events if event.type == "tool.policy.evaluated")
    assert policy.payload["sandbox_required"] is True
    assert policy.payload["requires_approval"] is True
    assert policy.payload["capabilities"] == ["process.exec", "file.read", "file.write"]
    snapshot = runtime.sandbox_snapshot()
    assert snapshot["sandboxes"][0]["kind"] == "local-process"
    await runtime.shutdown()


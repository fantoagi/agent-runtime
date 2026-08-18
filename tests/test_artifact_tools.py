from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_runtime.domain import (
    AgentDefinition,
    ModelConfig,
    RunStatus,
    ToolCall,
    ToolCapability,
    ToolDefinition,
    ToolExecutionError,
)
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.storage import ArtifactStore
from agent_runtime.tools import ToolContext, ToolRegistry, register_builtin_tools


def context(workspace: Path, run_id: str = "run-test") -> ToolContext:
    return ToolContext(run_id=run_id, step_id=1, workspace_path=workspace, metadata={})


@pytest.mark.asyncio
async def test_read_artifact_pages_current_run_tool_results(workspace: Path) -> None:
    artifact_root = workspace / ".state" / "artifacts"
    store = ArtifactStore(artifact_root)
    target = store.write_text("run-test", "tool-results/large.txt", "甲乙丙丁" * 600)
    tools = ToolRegistry()
    register_builtin_tools(tools, artifact_path=artifact_root)
    try:
        first = await tools.invoke(
            "read_artifact",
            {"path": str(target), "offset": 0, "max_chars": 512},
            context(workspace),
        )
        assert first.data is not None
        assert first.data["content"] == ("甲乙丙丁" * 600)[:512]
        assert first.data["next_offset"] == 512
        assert first.data["total_chars"] == 2400
        assert first.data["has_more"] is True
        assert first.data["path"] == "run-test/tool-results/large.txt"
        assert len(first.data["sha256"]) == 64

        final = await tools.invoke(
            "read_artifact",
            {
                "path": first.data["path"],
                "offset": 2048,
                "max_chars": 512,
            },
            context(workspace),
        )
        assert final.data is not None
        assert final.data["next_offset"] == 2400
        assert final.data["has_more"] is False
    finally:
        await tools.aclose()


@pytest.mark.asyncio
async def test_read_artifact_rejects_escape_other_run_and_invalid_bounds(workspace: Path) -> None:
    artifact_root = workspace / ".state" / "artifacts"
    store = ArtifactStore(artifact_root)
    store.write_text("other-run", "tool-results/private.txt", "private")
    tools = ToolRegistry()
    register_builtin_tools(tools, artifact_path=artifact_root)
    try:
        with pytest.raises(ToolExecutionError, match="current run"):
            await tools.invoke(
                "read_artifact",
                {"path": "other-run/tool-results/private.txt"},
                context(workspace),
            )
        with pytest.raises(ToolExecutionError, match="current run"):
            await tools.invoke(
                "read_artifact", {"path": "../outside.txt"}, context(workspace)
            )
        with pytest.raises(ToolExecutionError, match="max_chars"):
            await tools.invoke(
                "read_artifact",
                {"path": "missing.txt", "max_chars": 100},
                context(workspace),
            )
        own = store.write_text("run-test", "tool-results/short.txt", "short")
        with pytest.raises(ToolExecutionError, match="exceeds total"):
            await tools.invoke(
                "read_artifact",
                {"path": str(own), "offset": 99},
                context(workspace),
            )
        with pytest.raises(ToolExecutionError, match="does not exist"):
            await tools.invoke(
                "read_artifact", {"path": "missing.txt"}, context(workspace)
            )
    finally:
        await tools.aclose()


@pytest.mark.asyncio
async def test_read_text_file_redirects_tool_result_artifacts(workspace: Path) -> None:
    artifact_root = workspace / ".state" / "artifacts"
    target = ArtifactStore(artifact_root).write_text(
        "run-test", "tool-results/large.txt", "large"
    )
    tools = ToolRegistry()
    register_builtin_tools(tools, artifact_path=artifact_root)
    try:
        with pytest.raises(ToolExecutionError, match="Use read_artifact"):
            await tools.invoke(
                "read_text_file",
                {"path": target.relative_to(workspace).as_posix()},
                context(workspace),
            )
    finally:
        await tools.aclose()


@pytest.mark.asyncio
async def test_runtime_large_result_can_be_paged_without_recursive_artifact(workspace: Path) -> None:
    artifact_root = workspace / ".state" / "artifacts"
    tools = ToolRegistry()
    register_builtin_tools(tools, artifact_path=artifact_root)
    large_definition = ToolDefinition(
        name="large_result",
        description="Return a deterministic large result.",
        input_schema={"type": "object", "additionalProperties": False},
        capabilities=(ToolCapability.FILE_READ,),
    )
    tools.register(large_definition, lambda arguments, context: "0123456789" * 900)

    def responder(messages, definitions, config):
        del definitions, config
        last = messages[-1]
        if last.role == "user":
            return ModelResponse(tool_calls=[ToolCall("large", "large_result", {})])
        if last.name == "large_result":
            match = re.search(r"artifact: (.*); characters=", last.content)
            assert match is not None
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "page-1",
                        "read_artifact",
                        {"path": match.group(1), "offset": 0, "max_chars": 1000},
                    )
                ]
            )
        if last.name == "read_artifact" and "next_offset=1000" in last.content:
            match = re.search(r"Artifact (.*) characters", last.content)
            assert match is not None
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        "page-2",
                        "read_artifact",
                        {"path": match.group(1), "offset": 1000, "max_chars": 1000},
                    )
                ]
            )
        return ModelResponse(content="Artifact pages were read without a process approval.")

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / ".state" / "runtime.sqlite3",
            artifact_path=artifact_root,
            large_tool_result_chars=128,
        ),
        provider=MockProvider(responder),
        tools=tools,
    )
    agent = AgentDefinition(
        name="artifact-reader",
        system_prompt="Read large results with read_artifact.",
        tools=[large_definition, tools.get("read_artifact").definition],
        model=ModelConfig(provider="mock", model="artifact-reader"),
    )
    try:
        run = await runtime.run(agent, "Read the large result")
        assert run.status is RunStatus.COMPLETED
        assert run.result == "Artifact pages were read without a process approval."
        events = runtime.store.events_since(run.id)
        assert [event.type for event in events].count("tool.result.artifactized") == 1
        first = runtime.store.get_tool_execution_by_call(run.id, "page-1")
        second = runtime.store.get_tool_execution_by_call(run.id, "page-2")
        assert first is not None and first.result_data is not None
        assert second is not None and second.result_data is not None
        assert first.result_data.get("_artifact") is None
        assert second.result_data.get("_artifact") is None
    finally:
        await runtime.shutdown()

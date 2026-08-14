from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.domain import AgentDefinition, ModelConfig
from agent_runtime.evals import EvalCase, EvalRunner, EvalSuite
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_eval_runtime(workspace: Path) -> tuple[Runtime, AgentDefinition]:
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "runtime.sqlite3",
            artifact_path=workspace / "artifacts",
        ),
        provider=MockProvider(
            lambda messages, tools, config: ModelResponse(
                content=f"answer:{messages[-1].content}",
                finish_reason="stop",
            )
        ),
        tools=ToolRegistry(),
    )
    agent = AgentDefinition(
        name="eval-agent",
        system_prompt="answer",
        tools=[],
        model=ModelConfig(provider="mock", model="eval"),
    )
    runtime.register_agent(agent)
    return runtime, agent


@pytest.mark.asyncio
async def test_eval_runner_records_pass_fail_and_artifact(workspace: Path) -> None:
    runtime, agent = make_eval_runtime(workspace)
    suite = EvalSuite(
        name="basic",
        cases=[
            EvalCase(name="pass", input="hello", expected_output="answer:hello"),
            EvalCase(name="fail", input="world", expected_output="wrong"),
        ],
    )

    report = await EvalRunner(runtime).run(suite, agent)

    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.failed_cases == 1
    assert report.pass_rate == 0.5
    assert report.artifact_path is not None
    artifact = Path(report.artifact_path)
    assert artifact.is_file()
    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert saved["id"] == report.id
    assert saved["artifact_path"] == report.artifact_path
    assert all(result.trace_id.startswith("trace_") for result in report.results)


@pytest.mark.asyncio
async def test_eval_runner_supports_contains_expectations(workspace: Path) -> None:
    runtime, agent = make_eval_runtime(workspace)
    suite = EvalSuite(
        name="contains",
        cases=[
            EvalCase(
                name="fragments",
                input="hello world",
                expected_contains=["answer:", "world"],
            )
        ],
    )

    report = await EvalRunner(runtime).run(suite, agent.name)

    assert report.pass_rate == 1.0
    assert [item.evaluator for item in report.results[0].assertions] == [
        "expected_status",
        "contains",
    ]
    run = runtime.store.get_run(report.results[0].run_id)
    assert run.metadata["eval_suite"] == "contains"
    assert run.metadata["eval_case"] == "fragments"

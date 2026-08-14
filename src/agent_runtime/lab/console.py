from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..domain import AgentDefinition, Message, ModelConfig, ToolCall, ToolDefinition
from ..observability import ObservabilityService
from ..providers import (
    MockProvider,
    MockStreamingProvider,
    ModelResponse,
    ModelTokenDelta,
    arithmetic_demo_responder,
)
from ..runtime import Runtime
from ..tools import ToolContext, ToolRegistry, register_builtin_tools
from .explanations import explain_event, project_event_states
from .scenarios import LearningScenario, ScenarioRegistry, default_scenarios


class LearningConsole:
    """Teaching adapter that runs deterministic scenarios through the real Runtime."""

    def __init__(
        self,
        base_runtime: Runtime,
        scenarios: ScenarioRegistry | None = None,
    ) -> None:
        self.base_runtime = base_runtime
        self.store = base_runtime.store
        self.scenarios = scenarios or default_scenarios()
        self._runtimes = self._build_scenario_runtimes()

    def list_scenarios(self) -> list[dict[str, Any]]:
        return [scenario.to_dict() for scenario in self.scenarios.list()]

    def start(self, scenario_id: str, input_text: str | None = None) -> dict[str, Any]:
        scenario = self.scenarios.get(scenario_id)
        runtime = self._runtimes[scenario_id]
        run = runtime.start(
            scenario.agent_name,
            input_text or scenario.input,
            {
                "learning_console": True,
                "learning_scenario": scenario.id,
                "learning_scenario_name": scenario.name,
            },
        )
        return run.to_dict()

    async def resolve_approval(
        self, approval_id: str, approved: bool, reason: str | None = None
    ) -> dict[str, Any]:
        existing = self.store.get_approval(approval_id)
        runtime = self.runtime_for_run(existing.run_id)
        approval = runtime.resolve_approval(approval_id, approved, reason)
        run = self.store.get_run(existing.run_id)
        if run.status.value == "waiting_for_approval" and approval.status != "pending":
            run = await runtime.resume(run.id)
        return {
            "approval": self._approval_payload(approval),
            "run": run.to_dict(),
        }

    def runtime_for_run(self, run_id: str) -> Runtime:
        run = self.store.get_run(run_id)
        scenario_id = str(run.metadata.get("learning_scenario", ""))
        if scenario_id not in self._runtimes:
            raise KeyError(f"Run {run_id} is not owned by the Learning Console.")
        return self._runtimes[scenario_id]

    def snapshot(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        scenario_id = str(run.metadata.get("learning_scenario", ""))
        scenario = self.scenarios.get(scenario_id)
        events = self.store.events_since(run_id)
        projections = project_event_states(events)
        decorated_events = []
        for event, (before, after) in zip(events, projections, strict=True):
            decorated_events.append(
                {
                    **event.to_dict(),
                    "teaching": explain_event(event),
                    "state_before": before,
                    "state_after": after,
                }
            )

        checkpoint = self.store.latest_checkpoint(run_id)
        steps = self.store.steps_for_run(run_id)
        executions = self.store.tool_executions_for_run(run_id)
        approvals = []
        seen_approvals: set[str] = set()
        for execution in executions:
            approval = self.store.approval_for_execution(execution.id)
            if approval is not None and approval.id not in seen_approvals:
                approvals.append(self._approval_payload(approval))
                seen_approvals.add(approval.id)

        observability = ObservabilityService(self.store)
        trace = observability.trace(run_id)
        trace_tree = observability.trace_tree(run_id)
        metrics = observability.metrics(limit=1000)
        acceptance = self._evaluate(scenario, run.to_dict(), events)
        return {
            "scenario": scenario.to_dict(),
            "run": run.to_dict(),
            "events": decorated_events,
            "checkpoint": checkpoint.to_dict() if checkpoint else None,
            "steps": [self._step_payload(step) for step in steps],
            "tool_executions": [self._execution_payload(item) for item in executions],
            "approvals": approvals,
            "pending_approval": next(
                (item for item in approvals if item["status"] == "pending"), None
            ),
            "trace": trace.to_dict(),
            "trace_tree": trace_tree.to_dict(),
            "metrics": metrics.to_dict(),
            "acceptance": acceptance,
            "persistence": {
                "database": str(self.store.path),
                "schema_version": self.store.schema_version,
                "tables": {
                    "runs": 1,
                    "events": len(events),
                    "steps": len(steps),
                    "tool_executions": len(executions),
                    "approvals": len(approvals),
                    "checkpoints": 1 if checkpoint else 0,
                },
            },
        }

    def _build_scenario_runtimes(self) -> dict[str, Runtime]:
        runtimes: dict[str, Runtime] = {}
        for scenario in self.scenarios.list():
            tools = ToolRegistry()
            if scenario.id == "tool-calling":
                register_builtin_tools(tools)
                provider = MockProvider(arithmetic_demo_responder)
                agent_tools = [tools.get("calculator").definition]
            elif scenario.id == "token-streaming":
                provider = MockStreamingProvider(
                    [
                        ModelTokenDelta(content="Runtime "),
                        ModelTokenDelta(content="Event "),
                        ModelTokenDelta(content="是可持久化的执行事实。"),
                        ModelTokenDelta(
                            finish_reason="stop",
                            usage={"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
                        ),
                    ]
                )
                agent_tools = []
            elif scenario.id == "human-approval":
                publish_note = ToolDefinition(
                    name="publish_learning_note",
                    description="Publish a local learning note after explicit human approval.",
                    input_schema={
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                    requires_approval=True,
                    side_effecting=True,
                )
                tools.register(publish_note, _publish_learning_note)
                provider = MockProvider(_approval_responder)
                agent_tools = [publish_note]
            else:
                provider = MockProvider(_plain_text_responder)
                agent_tools = []

            runtime = Runtime(
                self.base_runtime.config,
                provider=provider,
                tools=tools,
                store=self.store,
            )
            runtime.register_agent(
                AgentDefinition(
                    name=scenario.agent_name,
                    system_prompt=(
                        "You are a deterministic teaching agent used by the Agent Runtime "
                        "Learning Console."
                    ),
                    tools=agent_tools,
                    model=ModelConfig(provider="learning", model=scenario.id),
                )
            )
            runtimes[scenario.id] = runtime
        return runtimes

    @staticmethod
    def _evaluate(
        scenario: LearningScenario,
        run: dict[str, Any],
        events: list[Any],
    ) -> dict[str, Any]:
        event_types = [event.type for event in events]
        cursor = 0
        missing: list[str] = []
        for expected in scenario.expected_events:
            try:
                cursor = event_types.index(expected, cursor) + 1
            except ValueError:
                missing.append(expected)
        checks = [
            {
                "name": "关键事件按顺序出现",
                "passed": not missing,
                "detail": "全部关键事件已出现" if not missing else f"仍缺少：{', '.join(missing)}",
            },
            {
                "name": "Run 到达预期状态",
                "passed": run["status"] == scenario.expected_status,
                "detail": f"当前 {run['status']} / 预期 {scenario.expected_status}",
            },
            {
                "name": "模型 Step 数量",
                "passed": int(run["step_count"]) >= scenario.minimum_steps,
                "detail": f"当前 {run['step_count']} / 至少 {scenario.minimum_steps}",
            },
            {
                "name": "工具调用数量",
                "passed": int(run["tool_call_count"]) >= scenario.minimum_tool_calls,
                "detail": f"当前 {run['tool_call_count']} / 至少 {scenario.minimum_tool_calls}",
            },
        ]
        if scenario.expected_result_contains:
            result = run.get("result") or ""
            checks.append(
                {
                    "name": "最终结果",
                    "passed": scenario.expected_result_contains in result,
                    "detail": f"应包含：{scenario.expected_result_contains}",
                }
            )
        return {
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
            "waiting_for_human": run["status"] == "waiting_for_approval",
        }

    @staticmethod
    def _step_payload(step: Any) -> dict[str, Any]:
        return {
            "id": step.id,
            "run_id": step.run_id,
            "step_index": step.step_index,
            "status": step.status.value,
            "assistant_message": (
                step.assistant_message.to_dict() if step.assistant_message else None
            ),
            "created_at": step.created_at.isoformat(),
            "updated_at": step.updated_at.isoformat(),
        }

    @staticmethod
    def _execution_payload(execution: Any) -> dict[str, Any]:
        payload = asdict(execution)
        payload["status"] = execution.status.value
        payload["created_at"] = execution.created_at.isoformat()
        payload["started_at"] = execution.started_at.isoformat() if execution.started_at else None
        payload["completed_at"] = (
            execution.completed_at.isoformat() if execution.completed_at else None
        )
        return payload

    @staticmethod
    def _approval_payload(approval: Any) -> dict[str, Any]:
        return {
            "id": approval.id,
            "run_id": approval.run_id,
            "tool_execution_id": approval.tool_execution_id,
            "kind": approval.kind,
            "status": approval.status,
            "reason": approval.reason,
            "created_at": approval.created_at.isoformat(),
            "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
            "tool_call": {
                "id": approval.tool_call.id,
                "name": approval.tool_call.name,
                "arguments": approval.tool_call.arguments,
            },
        }


def _plain_text_responder(
    messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
) -> ModelResponse:
    del messages, tools, config
    return ModelResponse(
        content="Agent Runtime 是负责驱动模型、工具、状态与恢复流程的可持久化执行内核。",
        finish_reason="stop",
        usage={"prompt_tokens": 18, "completion_tokens": 22, "total_tokens": 40},
    )


def _approval_responder(
    messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
) -> ModelResponse:
    del tools, config
    last = messages[-1]
    if last.role == "tool":
        if (last.content or "").startswith("学习笔记已发布"):
            content = f"学习笔记流程已完成：{last.content}"
        else:
            content = f"学习笔记未发布：{last.content}"
        return ModelResponse(content=content, finish_reason="stop")
    user_text = last.content or ""
    note = user_text.removeprefix("发布学习笔记：").strip() or user_text
    return ModelResponse(
        tool_calls=[
            ToolCall(
                id="lab_publish_note",
                name="publish_learning_note",
                arguments={"content": note},
            )
        ],
        finish_reason="tool_calls",
    )


def _publish_learning_note(arguments: dict[str, Any], context: ToolContext) -> str:
    context.raise_if_cancelled()
    return f"学习笔记已发布：{arguments['content']}"

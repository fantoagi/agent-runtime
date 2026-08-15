from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ..domain import AgentDefinition, MemoryScope, Message, ModelConfig, ToolCall, ToolDefinition
from ..observability import ObservabilityService
from ..orchestration import AggregationStrategy, ParallelWorkflow, SequentialWorkflow, WorkflowStep
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

    def __init__(self, base_runtime: Runtime, scenarios: ScenarioRegistry | None = None) -> None:
        self.base_runtime = base_runtime
        self.store = base_runtime.store
        self.scenarios = scenarios or default_scenarios()
        self._workflows: dict[str, SequentialWorkflow | ParallelWorkflow] = {}
        self._runtimes = self._build_scenario_runtimes()

    def list_scenarios(self) -> list[dict[str, Any]]:
        return [scenario.to_dict() for scenario in self.scenarios.list()]

    def start(self, scenario_id: str, input_text: str | None = None) -> dict[str, Any]:
        scenario = self.scenarios.get(scenario_id)
        runtime = self._runtimes[scenario_id]
        value = input_text or scenario.input
        metadata = {
            "learning_console": True,
            "learning_scenario": scenario.id,
            "learning_scenario_name": scenario.name,
        }

        if scenario_id in self._workflows:
            return self._workflows[scenario_id].start(runtime, value, metadata=metadata).to_dict()

        if scenario_id == "session-memory":
            session = runtime.create_session({"source": "learning-console", "scenario": scenario.id})
            session_memory = runtime.remember(
                "Mermaid Runtime：用户偏好中文解释，并希望复杂架构优先使用 Mermaid 图。",
                scope=MemoryScope.SESSION,
                scope_id=session.id,
                metadata={"kind": "preference", "scenario": scenario.id},
            )
            agent_memory = runtime.remember(
                "Mermaid Runtime：解释 Agent Runtime 时先讲执行主循环，再讲持久化与恢复。",
                scope=MemoryScope.AGENT,
                scope_id=scenario.agent_name,
                metadata={"kind": "teaching-style", "scenario": scenario.id},
            )
            metadata["learning_memory_ids"] = [session_memory.id, agent_memory.id]
            return runtime.start(
                scenario.agent_name,
                value,
                metadata,
                session_id=session.id,
            ).to_dict()

        return runtime.start(scenario.agent_name, value, metadata).to_dict()

    async def resolve_approval(
        self, approval_id: str, approved: bool, reason: str | None = None
    ) -> dict[str, Any]:
        existing = self.store.get_approval(approval_id)
        runtime = self.runtime_for_run(existing.run_id)
        approval = runtime.resolve_approval(approval_id, approved, reason)
        run = self.store.get_run(existing.run_id)
        if run.status.value == "waiting_for_approval" and approval.status != "pending":
            run = await runtime.resume(run.id)
        return {"approval": self._approval_payload(approval), "run": run.to_dict()}

    def runtime_for_run(self, run_id: str) -> Runtime:
        run = self.store.get_run(run_id)
        scenario_id = str(run.metadata.get("learning_scenario", ""))
        if not scenario_id:
            root = self.store.get_run(self.store.root_run_id(run_id))
            scenario_id = str(root.metadata.get("learning_scenario", ""))
        if scenario_id not in self._runtimes:
            raise KeyError(f"Run {run_id} is not owned by the Learning Console.")
        return self._runtimes[scenario_id]

    def snapshot(self, run_id: str) -> dict[str, Any]:
        selected = self.store.get_run(run_id)
        root_run_id = self.store.root_run_id(run_id)
        root = self.store.get_run(root_run_id)
        scenario_id = str(root.metadata.get("learning_scenario", ""))
        scenario = self.scenarios.get(scenario_id)
        relations = self.store.relations_for_root(root_run_id)
        run_ids = [root_run_id, *[relation.child_run_id for relation in relations]]
        runs = [self.store.get_run(item) for item in dict.fromkeys(run_ids)]
        relation_by_child = {relation.child_run_id: relation for relation in relations}

        decorated_events: list[dict[str, Any]] = []
        all_raw_events: list[Any] = []
        checkpoints: list[dict[str, Any]] = []
        steps: list[Any] = []
        executions: list[Any] = []
        approvals: list[dict[str, Any]] = []
        seen_approvals: set[str] = set()

        for current_run in runs:
            raw_events = self.store.events_since(current_run.id)
            all_raw_events.extend(raw_events)
            projections = project_event_states(raw_events)
            relation = relation_by_child.get(current_run.id)
            role = "root" if current_run.id == root_run_id else "child"
            for event, (before, after) in zip(raw_events, projections, strict=True):
                decorated_events.append(
                    {
                        **event.to_dict(),
                        "local_sequence": event.sequence,
                        "run_role": role,
                        "agent_name": current_run.agent_name,
                        "parent_run_id": relation.parent_run_id if relation else None,
                        "teaching": explain_event(event),
                        "state_before": before,
                        "state_after": after,
                    }
                )
            checkpoint = self.store.latest_checkpoint(current_run.id)
            if checkpoint:
                checkpoint_payload = checkpoint.to_dict()
                checkpoint_payload["agent_name"] = current_run.agent_name
                checkpoint_payload["run_role"] = role
                checkpoints.append(checkpoint_payload)
            current_steps = self.store.steps_for_run(current_run.id)
            current_executions = self.store.tool_executions_for_run(current_run.id)
            steps.extend(current_steps)
            executions.extend(current_executions)
            for execution in current_executions:
                approval = self.store.approval_for_execution(execution.id)
                if approval is not None and approval.id not in seen_approvals:
                    approvals.append(self._approval_payload(approval))
                    seen_approvals.add(approval.id)

        decorated_events.sort(
            key=lambda item: (item["timestamp"], item["run_id"], item["local_sequence"])
        )
        for timeline_sequence, event in enumerate(decorated_events, start=1):
            event["timeline_sequence"] = timeline_sequence

        session = self.store.session_for_run(root_run_id)
        session_runs = self.store.session_runs(session.id) if session else []
        memory_ids = list(root.metadata.get("learning_memory_ids", []))
        for event in decorated_events:
            memory_ids.extend(event["payload"].get("memory_ids", []))
            if event["payload"].get("memory_id"):
                memory_ids.append(event["payload"]["memory_id"])
        memories = []
        for memory_id in dict.fromkeys(memory_ids):
            try:
                memories.append(self.store.get_memory(memory_id).to_dict())
            except KeyError:
                continue

        context_builds = [
            {
                "run_id": event["run_id"],
                "agent_name": event["agent_name"],
                "event_type": event["type"],
                "timeline_sequence": event["timeline_sequence"],
                **event["payload"],
            }
            for event in decorated_events
            if event["type"] in {"context.built", "context.compacted"}
        ]
        artifacts = self._artifact_payloads(decorated_events)

        observability = ObservabilityService(self.store)
        trace = observability.trace(root_run_id)
        trace_tree = observability.trace_tree(root_run_id)
        metrics = observability.metrics(limit=1000)
        acceptance = self._evaluate(
            scenario,
            root.to_dict(),
            all_raw_events,
            child_count=len(relations),
            memory_count=len(memories),
            context_compactions=sum(event.type == "context.compacted" for event in all_raw_events),
            artifact_count=len(artifacts),
        )
        latest_root_checkpoint = self.store.latest_checkpoint(root_run_id)
        return {
            "scenario": scenario.to_dict(),
            "run": root.to_dict(),
            "selected_run": selected.to_dict(),
            "runs": [
                {
                    **item.to_dict(),
                    "run_role": "root" if item.id == root_run_id else "child",
                    "parent_run_id": relation_by_child[item.id].parent_run_id
                    if item.id in relation_by_child
                    else None,
                }
                for item in runs
            ],
            "relations": [relation.to_dict() for relation in relations],
            "events": decorated_events,
            "checkpoint": latest_root_checkpoint.to_dict() if latest_root_checkpoint else None,
            "checkpoints": checkpoints,
            "steps": [self._step_payload(step) for step in steps],
            "tool_executions": [self._execution_payload(item) for item in executions],
            "approvals": approvals,
            "pending_approval": next((item for item in approvals if item["status"] == "pending"), None),
            "trace": trace.to_dict(),
            "trace_tree": trace_tree.to_dict(),
            "metrics": metrics.to_dict(),
            "session": session.to_dict() if session else None,
            "session_runs": [item.to_dict() for item in session_runs],
            "memories": memories,
            "context_builds": context_builds,
            "artifacts": artifacts,
            "acceptance": acceptance,
            "persistence": {
                "database": str(self.store.path),
                "schema_version": self.store.schema_version,
                "tables": {
                    "runs": len(runs),
                    "run_relations": len(relations),
                    "events": len(decorated_events),
                    "steps": len(steps),
                    "tool_executions": len(executions),
                    "approvals": len(approvals),
                    "checkpoints": len(checkpoints),
                    "sessions": 1 if session else 0,
                    "session_runs": len(session_runs),
                    "memory_records": len(memories),
                    "artifacts": len(artifacts),
                },
            },
        }

    def _build_scenario_runtimes(self) -> dict[str, Runtime]:
        runtimes: dict[str, Runtime] = {}
        for scenario in self.scenarios.list():
            tools = ToolRegistry()
            agent_definitions: list[AgentDefinition] = []
            config = replace(
                self.base_runtime.config,
                metadata={
                    **self.base_runtime.config.metadata,
                    "learning_console": True,
                    "learning_scenario": scenario.id,
                    "learning_scenario_name": scenario.name,
                },
            )

            if scenario.id == "tool-calling":
                register_builtin_tools(tools)
                provider = MockProvider(arithmetic_demo_responder)
                agent_definitions.append(self._agent(scenario.agent_name, scenario.id, [tools.get("calculator").definition]))
            elif scenario.id == "token-streaming":
                provider = MockStreamingProvider(
                    [
                        ModelTokenDelta(content="Runtime "),
                        ModelTokenDelta(content="Event "),
                        ModelTokenDelta(content="是可持久化的执行事实。"),
                        ModelTokenDelta(finish_reason="stop", usage={"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}),
                    ]
                )
                agent_definitions.append(self._agent(scenario.agent_name, scenario.id))
            elif scenario.id == "human-approval":
                publish_note = ToolDefinition(
                    name="publish_learning_note",
                    description="Publish a local learning note after explicit human approval.",
                    input_schema={"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False},
                    requires_approval=True,
                    side_effecting=True,
                )
                tools.register(publish_note, _publish_learning_note)
                provider = MockProvider(_approval_responder)
                agent_definitions.append(self._agent(scenario.agent_name, scenario.id, [publish_note]))
            elif scenario.id == "multi-agent-sequential":
                provider = MockProvider(_multi_agent_responder)
                names = ["lab-planner", "lab-worker", "lab-reviewer"]
                labels = ["Planner", "Worker", "Reviewer"]
                agent_definitions.extend(self._agent(name, label) for name, label in zip(names, labels, strict=True))
                self._workflows[scenario.id] = SequentialWorkflow(
                    scenario.agent_name,
                    [WorkflowStep(name, name=label) for name, label in zip(names, labels, strict=True)],
                )
            elif scenario.id == "multi-agent-parallel":
                provider = MockProvider(_multi_agent_responder)
                names = ["lab-parallel-research", "lab-parallel-test", "lab-parallel-risk"]
                labels = ["Research", "Test", "Risk"]
                agent_definitions.extend(self._agent(name, label) for name, label in zip(names, labels, strict=True))
                self._workflows[scenario.id] = ParallelWorkflow(
                    scenario.agent_name,
                    [WorkflowStep(name, name=label) for name, label in zip(names, labels, strict=True)],
                    aggregation=AggregationStrategy.ALL,
                    max_concurrency=3,
                )
            elif scenario.id == "session-memory":
                provider = MockProvider(_memory_responder)
                agent_definitions.append(self._agent(scenario.agent_name, scenario.id))
            elif scenario.id == "context-compaction":
                config = replace(
                    config,
                    context_token_budget=256,
                    context_recent_groups=1,
                    context_summary_max_chars=360,
                    memory_token_budget=0,
                )
                context_tool = ToolDefinition(
                    name="record_context_stage",
                    description="Return a deliberately verbose stage record for context compaction teaching.",
                    input_schema={"type": "object", "properties": {"stage": {"type": "integer"}}, "required": ["stage"], "additionalProperties": False},
                )
                tools.register(context_tool, _record_context_stage)
                provider = MockProvider(_context_responder)
                agent_definitions.append(self._agent(scenario.agent_name, scenario.id, [context_tool]))
            elif scenario.id == "large-tool-artifact":
                config = replace(config, large_tool_result_chars=256, large_tool_result_preview_chars=120)
                artifact_tool = ToolDefinition(
                    name="generate_runtime_handbook",
                    description="Generate a long Runtime handbook so the result is persisted as an artifact.",
                    input_schema={"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"], "additionalProperties": False},
                )
                tools.register(artifact_tool, _generate_runtime_handbook)
                provider = MockProvider(_artifact_responder)
                agent_definitions.append(self._agent(scenario.agent_name, scenario.id, [artifact_tool]))
            else:
                provider = MockProvider(_plain_text_responder)
                agent_definitions.append(self._agent(scenario.agent_name, scenario.id))

            runtime = Runtime(config, provider=provider, tools=tools, store=self.store)
            for definition in agent_definitions:
                runtime.register_agent(definition)
            runtimes[scenario.id] = runtime
        return runtimes

    @staticmethod
    def _agent(name: str, model: str, tools: list[ToolDefinition] | None = None) -> AgentDefinition:
        return AgentDefinition(
            name=name,
            system_prompt="You are a deterministic teaching agent used by the Agent Runtime Learning Console.",
            tools=tools or [],
            model=ModelConfig(provider="learning", model=model),
        )

    @staticmethod
    def _evaluate(
        scenario: LearningScenario,
        run: dict[str, Any],
        events: list[Any],
        *,
        child_count: int,
        memory_count: int,
        context_compactions: int,
        artifact_count: int,
    ) -> dict[str, Any]:
        ordered = sorted(events, key=lambda item: (item.timestamp, item.run_id, item.sequence))
        event_types = [event.type for event in ordered]
        cursor = 0
        missing: list[str] = []
        for expected in scenario.expected_events:
            try:
                cursor = event_types.index(expected, cursor) + 1
            except ValueError:
                missing.append(expected)
        checks = [
            {"name": "关键事件按顺序出现", "passed": not missing, "detail": "全部关键事件已出现" if not missing else f"仍缺少：{', '.join(missing)}"},
            {"name": "Run 到达预期状态", "passed": run["status"] == scenario.expected_status, "detail": f"当前 {run['status']} / 预期 {scenario.expected_status}"},
            {"name": "模型 Step 数量", "passed": int(run["step_count"]) >= scenario.minimum_steps or scenario.minimum_children > 0, "detail": f"Root 当前 {run['step_count']} / 至少 {scenario.minimum_steps}"},
            {"name": "工具调用数量", "passed": int(run["tool_call_count"]) >= scenario.minimum_tool_calls or scenario.minimum_children > 0, "detail": f"Root 当前 {run['tool_call_count']} / 至少 {scenario.minimum_tool_calls}"},
        ]
        dimensions = [
            ("Child Run 数量", child_count, scenario.minimum_children),
            ("Memory 数量", memory_count, scenario.minimum_memories),
            ("Context 压缩次数", context_compactions, scenario.minimum_context_compactions),
            ("Artifact 数量", artifact_count, scenario.minimum_artifacts),
        ]
        for name, actual, minimum in dimensions:
            if minimum:
                checks.append({"name": name, "passed": actual >= minimum, "detail": f"当前 {actual} / 至少 {minimum}"})
        if scenario.expected_result_contains:
            result = run.get("result") or ""
            checks.append({"name": "最终结果", "passed": scenario.expected_result_contains in result, "detail": f"应包含：{scenario.expected_result_contains}"})
        return {
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
            "waiting_for_human": run["status"] == "waiting_for_approval",
        }

    @staticmethod
    def _artifact_payloads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in events:
            if event["type"] != "tool.result.artifactized":
                continue
            path_value = str(event["payload"].get("path", ""))
            if not path_value or path_value in seen:
                continue
            seen.add(path_value)
            path = Path(path_value)
            preview = ""
            if path.exists():
                preview = path.read_text(encoding="utf-8")[:240]
            artifacts.append(
                {
                    **event["payload"],
                    "run_id": event["run_id"],
                    "agent_name": event["agent_name"],
                    "exists": path.exists(),
                    "preview": preview,
                }
            )
        return artifacts

    @staticmethod
    def _step_payload(step: Any) -> dict[str, Any]:
        return {
            "id": step.id,
            "run_id": step.run_id,
            "step_index": step.step_index,
            "status": step.status.value,
            "assistant_message": step.assistant_message.to_dict() if step.assistant_message else None,
            "created_at": step.created_at.isoformat(),
            "updated_at": step.updated_at.isoformat(),
        }

    @staticmethod
    def _execution_payload(execution: Any) -> dict[str, Any]:
        payload = asdict(execution)
        payload["status"] = execution.status.value
        payload["created_at"] = execution.created_at.isoformat()
        payload["started_at"] = execution.started_at.isoformat() if execution.started_at else None
        payload["completed_at"] = execution.completed_at.isoformat() if execution.completed_at else None
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
            "tool_call": {"id": approval.tool_call.id, "name": approval.tool_call.name, "arguments": approval.tool_call.arguments},
        }


def _plain_text_responder(messages: list[Message], tools: list[ToolDefinition], config: ModelConfig) -> ModelResponse:
    del messages, tools, config
    return ModelResponse(
        content="Agent Runtime 是负责驱动模型、工具、状态与恢复流程的可持久化执行内核。",
        finish_reason="stop",
        usage={"prompt_tokens": 18, "completion_tokens": 22, "total_tokens": 40},
    )


def _approval_responder(messages: list[Message], tools: list[ToolDefinition], config: ModelConfig) -> ModelResponse:
    del tools, config
    last = messages[-1]
    if last.role == "tool":
        content = f"学习笔记流程已完成：{last.content}" if (last.content or "").startswith("学习笔记已发布") else f"学习笔记未发布：{last.content}"
        return ModelResponse(content=content, finish_reason="stop")
    user_text = last.content or ""
    note = user_text.removeprefix("发布学习笔记：").strip() or user_text
    return ModelResponse(tool_calls=[ToolCall(id="lab_publish_note", name="publish_learning_note", arguments={"content": note})], finish_reason="tool_calls")


def _multi_agent_responder(messages: list[Message], tools: list[ToolDefinition], config: ModelConfig) -> ModelResponse:
    del tools
    input_text = messages[-1].content or ""
    return ModelResponse(content=f"{config.model} Agent 已完成本阶段：{input_text[:180]}", finish_reason="stop")


def _memory_responder(messages: list[Message], tools: list[ToolDefinition], config: ModelConfig) -> ModelResponse:
    del tools, config
    memory_text = next((message.content or "" for message in messages if message.name == "memory"), "")
    style = "Mermaid 图 + 中文分层解释" if "Mermaid" in memory_text else "中文解释"
    return ModelResponse(content=f"已从作用域记忆读取偏好，将使用 {style} 介绍 Runtime 架构。", finish_reason="stop")


def _context_responder(messages: list[Message], tools: list[ToolDefinition], config: ModelConfig) -> ModelResponse:
    del tools, config
    stages: list[int] = []
    for message in messages:
        stages.extend(int(value) for value in re.findall(r"STAGE:(\d+)", message.content or ""))
    current = max(stages, default=0)
    if current >= 4:
        return ModelResponse(content="四个阶段已完成；完整历史保存在 Checkpoint，本次请求使用了压缩后的 Context。", finish_reason="stop")
    next_stage = current + 1
    return ModelResponse(tool_calls=[ToolCall(id=f"context_stage_{next_stage}", name="record_context_stage", arguments={"stage": next_stage})], finish_reason="tool_calls")


def _artifact_responder(messages: list[Message], tools: list[ToolDefinition], config: ModelConfig) -> ModelResponse:
    del tools, config
    if messages[-1].role == "tool":
        return ModelResponse(content="Artifact 已生成：完整学习材料保存在文件中，Checkpoint 仅保留路径与 Preview。", finish_reason="stop")
    return ModelResponse(tool_calls=[ToolCall(id="generate_runtime_handbook", name="generate_runtime_handbook", arguments={"topic": messages[-1].content or "Agent Runtime"})], finish_reason="tool_calls")


def _publish_learning_note(arguments: dict[str, Any], context: ToolContext) -> str:
    context.raise_if_cancelled()
    return f"学习笔记已发布：{arguments['content']}"


def _record_context_stage(arguments: dict[str, Any], context: ToolContext) -> str:
    context.raise_if_cancelled()
    stage = int(arguments["stage"])
    return f"STAGE:{stage}\n" + (f"阶段 {stage} 的可追溯上下文记录；" * 55)


def _generate_runtime_handbook(arguments: dict[str, Any], context: ToolContext) -> str:
    context.raise_if_cancelled()
    topic = arguments["topic"]
    sections = [f"第 {index} 节：{topic} 的 Run、Event、Checkpoint、Tool 和 Recovery 教学内容。" for index in range(1, 45)]
    return "\n".join(sections)

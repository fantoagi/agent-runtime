from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LearningScenario:
    id: str
    name: str
    icon: str
    category: str
    description: str
    input: str
    agent_name: str
    expected_events: tuple[str, ...]
    learning_points: tuple[str, ...]
    expected_status: str = "completed"
    expected_result_contains: str | None = None
    minimum_steps: int = 1
    minimum_tool_calls: int = 0
    requires_human_action: bool = False
    action_hint: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["expected_events"] = list(self.expected_events)
        payload["learning_points"] = list(self.learning_points)
        payload["tags"] = list(self.tags)
        return payload


class ScenarioRegistry:
    def __init__(self, scenarios: list[LearningScenario]) -> None:
        self._scenarios = {scenario.id: scenario for scenario in scenarios}
        if len(self._scenarios) != len(scenarios):
            raise ValueError("Learning scenario IDs must be unique.")

    def list(self) -> list[LearningScenario]:
        return list(self._scenarios.values())

    def get(self, scenario_id: str) -> LearningScenario:
        try:
            return self._scenarios[scenario_id]
        except KeyError as error:
            raise KeyError(f"Learning scenario {scenario_id!r} was not found.") from error


def default_scenarios() -> ScenarioRegistry:
    return ScenarioRegistry(
        [
            LearningScenario(
                id="plain-text",
                name="纯文本响应",
                icon="message-square",
                category="基础",
                description="观察一个不调用工具的 Agent Run 如何完成模型请求、Checkpoint 和状态收敛。",
                input="用一句话解释 Agent Runtime 是什么。",
                agent_name="lab-plain-text",
                expected_events=(
                    "run.created",
                    "run.started",
                    "model.requested",
                    "model.completed",
                    "run.completed",
                ),
                learning_points=(
                    "Run 是一次可持久化执行，不等同于一次模型请求。",
                    "Runtime 在模型调用前后记录 Event，并在完成时保存 Checkpoint。",
                    "没有 Tool Call 时，模型文本直接成为 Run.result。",
                ),
                expected_result_contains="Agent Runtime",
                tags=("Run", "Model", "Checkpoint"),
            ),
            LearningScenario(
                id="tool-calling",
                name="工具调用",
                icon="wrench",
                category="核心循环",
                description="观察模型选择 calculator、Runtime 校验并执行工具、再把结果交回模型。",
                input="19 * 23",
                agent_name="lab-tool-calling",
                expected_events=(
                    "model.requested",
                    "tool.requested",
                    "tool.started",
                    "tool.completed",
                    "model.requested",
                    "run.completed",
                ),
                learning_points=(
                    "模型只产生结构化 ToolCall，不会直接执行 Python 函数。",
                    "ToolRegistry 在执行前校验参数，并为执行分配稳定幂等键。",
                    "工具结果作为 tool message 写入 Checkpoint，再触发下一次模型决策。",
                ),
                expected_result_contains="437",
                minimum_steps=2,
                minimum_tool_calls=1,
                tags=("ToolCall", "Schema", "Idempotency"),
            ),
            LearningScenario(
                id="token-streaming",
                name="Token Streaming",
                icon="waves",
                category="实时体验",
                description="观察 Provider token 增量如何变成持久化 model.delta，并最终合并为完整响应。",
                input="请流式介绍 Runtime Event。",
                agent_name="lab-token-streaming",
                expected_events=(
                    "model.stream.started",
                    "model.delta",
                    "model.stream.completed",
                    "model.completed",
                    "run.completed",
                ),
                learning_points=(
                    "Provider 的增量先进入 Runtime，而不是直接绕过执行内核发送给浏览器。",
                    "每个 model.delta 都进入同一条持久化 Event Log 和 SSE 流。",
                    "所有增量合并后仍生成普通 ModelResponse、Message 和 Checkpoint。",
                ),
                expected_result_contains="Runtime Event",
                tags=("SSE", "model.delta", "Streaming"),
            ),
            LearningScenario(
                id="human-approval",
                name="人工审批",
                icon="shield-check",
                category="安全与控制",
                description="观察副作用工具在真正执行前暂停，等待你批准或拒绝。",
                input="发布学习笔记：我已经理解了审批门禁。",
                agent_name="lab-human-approval",
                expected_events=(
                    "model.requested",
                    "approval.requested",
                    "approval.resolved",
                    "tool.requested",
                    "tool.completed",
                    "run.completed",
                ),
                learning_points=(
                    "requires_approval 是工具能力边界的一部分，而不是前端弹窗逻辑。",
                    "等待审批时 Run 与 ToolExecution 都有明确的持久化状态。",
                    "审批完成后 Runtime 从 Checkpoint 恢复，不会重新生成原 ToolCall。",
                ),
                expected_result_contains="学习笔记",
                minimum_steps=2,
                minimum_tool_calls=1,
                requires_human_action=True,
                action_hint="当时间线停在 approval.requested 时，在右侧审批卡片中选择批准或拒绝。",
                tags=("Approval", "Side Effect", "Resume"),
            ),
        ]
    )

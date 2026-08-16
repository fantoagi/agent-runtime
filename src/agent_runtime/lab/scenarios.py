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
    minimum_children: int = 0
    minimum_memories: int = 0
    minimum_context_compactions: int = 0
    minimum_artifacts: int = 0
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
                category="v0.5 · 基础",
                description="观察一个不调用工具的 Agent Run 如何完成模型请求、Checkpoint 和状态收敛。",
                input="用一句话解释 Agent Runtime 是什么。",
                agent_name="lab-plain-text",
                expected_events=("run.created", "run.started", "model.requested", "model.completed", "run.completed"),
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
                category="v0.5 · 核心循环",
                description="观察模型选择 calculator、Runtime 校验并执行工具、再把结果交回模型。",
                input="19 * 23",
                agent_name="lab-tool-calling",
                expected_events=("model.requested", "tool.requested", "tool.started", "tool.completed", "model.requested", "run.completed"),
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
                category="v0.5 · 实时体验",
                description="观察 Provider token 增量如何变成持久化 model.delta，并最终合并为完整响应。",
                input="请流式介绍 Runtime Event。",
                agent_name="lab-token-streaming",
                expected_events=("model.stream.started", "model.delta", "model.stream.completed", "model.completed", "run.completed"),
                learning_points=(
                    "Provider 的增量先进入 Runtime，而不是绕过执行内核直接发送给浏览器。",
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
                category="v0.5 · 安全与控制",
                description="观察副作用工具在真正执行前暂停，等待你批准或拒绝。",
                input="发布学习笔记：我已经理解了审批门禁。",
                agent_name="lab-human-approval",
                expected_events=("model.requested", "approval.requested", "approval.resolved", "tool.requested", "tool.completed", "run.completed"),
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
            LearningScenario(
                id="sandbox-process",
                name="受限进程沙箱",
                icon="terminal-square",
                category="v0.8 · Sandbox & Capability",
                description="观察 process.exec 与 file.write capability 如何组合成 Sandbox 强制和人工审批，再以 argv 方式启动受限本地进程。",
                input="在受限进程中输出 sandbox-v0.8。",
                agent_name="lab-sandbox-process",
                expected_events=("model.requested", "tool.policy.evaluated", "approval.requested", "approval.resolved", "tool.requested", "tool.started", "tool.completed", "run.completed"),
                learning_points=(
                    "模型只能提出结构化 argv，Runtime 不使用 shell=True 拼接命令。",
                    "process.exec 被策略标记为 sandbox_only，file.write 使执行前必须形成持久化审批。",
                    "LocalProcessSandbox 限制可执行文件、工作目录、环境变量、输出、超时和并发，但不冒充容器级强隔离。",
                ),
                expected_result_contains="sandbox-v0.8",
                minimum_steps=2,
                minimum_tool_calls=1,
                requires_human_action=True,
                action_hint="当流程停在 approval.requested 时批准执行，再观察 Tool 泳道中的受限进程结果。",
                tags=("Sandbox", "Capability", "argv", "Approval"),
            ),
            LearningScenario(
                id="multi-agent-sequential",
                name="多 Agent 串行协作",
                icon="git-commit",
                category="v0.6 · 多 Agent",
                description="Planner、Worker、Reviewer 三个 Child Run 依次接力，最终由 Workflow Parent 汇聚结果。",
                input="为 Agent Runtime 初学者设计一份三步学习计划。",
                agent_name="lab-sequential-workflow",
                expected_events=("workflow.started", "delegation.created", "delegation.completed", "workflow.completed"),
                learning_points=(
                    "Workflow Parent 负责持久化编排状态，真正的模型调用发生在 Child Run。",
                    "每次委派都写入 RunRelation 和 delegation_key，可防止恢复时重复创建 Child。",
                    "串行工作流把上一个 Child 的结果作为下一个 Child 的输入。",
                ),
                expected_result_contains="Reviewer",
                minimum_children=3,
                tags=("Workflow", "Parent/Child", "Sequential"),
            ),
            LearningScenario(
                id="multi-agent-parallel",
                name="多 Agent 并行协作",
                icon="git-branch",
                category="v0.6 · 多 Agent",
                description="Research、Test、Risk 三个 Child Run 并行执行，再按 ALL 策略汇聚到 Parent。",
                input="从架构、测试和风险三个角度评估当前 Agent Runtime。",
                agent_name="lab-parallel-workflow",
                expected_events=("workflow.started", "delegation.created", "delegation.completed", "workflow.completed"),
                learning_points=(
                    "ParallelWorkflow 用独立 asyncio Task 推进多个 Child Run。",
                    "每个 Child 拥有自己的 Event、Checkpoint、Trace ID，同时共享 Root Trace。",
                    "AggregationStrategy 决定部分失败时 Parent 是完成、失败还是采用首个成功结果。",
                ),
                expected_result_contains="lab-parallel",
                minimum_children=3,
                tags=("Parallel", "Aggregation", "Trace Tree"),
            ),
            LearningScenario(
                id="session-memory",
                name="Session 与作用域记忆",
                icon="database",
                category="v0.7 · Context & Memory",
                description="创建 Session 和两类 Memory，再观察 Runtime 在模型请求前自动检索并注入相关记忆。",
                input="请用 Mermaid 解释 Runtime。",
                agent_name="lab-session-memory",
                expected_events=("session.run.attached", "model.requested", "memory.search.started", "memory.search.completed", "context.built", "run.completed"),
                learning_points=(
                    "Session 是一组 Run 的持久化边界，不等于模型消息数组。",
                    "Session Memory 与 Agent Memory 使用明确 Scope，避免无边界全局记忆。",
                    "ContextBuilder 只把检索命中的 Memory 注入本次模型上下文。",
                ),
                expected_result_contains="Mermaid",
                minimum_memories=2,
                tags=("Session", "Scoped Memory", "FTS5"),
            ),
            LearningScenario(
                id="context-compaction",
                name="上下文压缩",
                icon="minimize-2",
                category="v0.7 · Context & Memory",
                description="用多轮大工具结果制造长历史，观察完整 Checkpoint 如何被确定性压缩后再发送给模型。",
                input="执行四个阶段的上下文压力测试，并说明压缩结果。",
                agent_name="lab-context-compaction",
                expected_events=("context.built", "tool.completed", "context.compacted", "model.requested", "run.completed"),
                learning_points=(
                    "Checkpoint 保留完整执行历史，ContextBuilder 只负责构建本次 Model Request。",
                    "压缩按安全消息组处理，不会拆开 assistant ToolCall 与对应 tool result。",
                    "context.compacted 记录预算、原始 token、遗漏消息和确定性 Summary。",
                ),
                expected_result_contains="四个阶段",
                minimum_steps=5,
                minimum_tool_calls=4,
                minimum_context_compactions=1,
                tags=("Token Budget", "Summary", "Safe Group"),
            ),
            LearningScenario(
                id="large-tool-artifact",
                name="大工具结果 Artifact 化",
                icon="file-text",
                category="v0.7 · Context & Memory",
                description="让工具返回大文本，观察完整内容写入 Artifact，而 Checkpoint 只保留路径和 Preview。",
                input="生成一份较长的 Runtime 学习材料。",
                agent_name="lab-large-artifact",
                expected_events=("tool.started", "tool.result.artifactized", "tool.completed", "run.completed"),
                learning_points=(
                    "大结果不直接塞满 Checkpoint 和后续模型上下文。",
                    "Artifact 保存完整内容，tool message 保存路径、字符数与 Preview。",
                    "ToolExecution、Event 和文件路径共同形成可追溯 provenance。",
                ),
                expected_result_contains="Artifact",
                minimum_steps=2,
                minimum_tool_calls=1,
                minimum_artifacts=1,
                tags=("Artifact", "Large Result", "Provenance"),
            ),
        ]
    )

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..domain import RuntimeEvent


EVENT_EXPLANATIONS: dict[str, dict[str, Any]] = {
    "run.created": {
        "title": "创建持久化 Run",
        "summary": "Runtime 生成 Run ID 和 trace_id，并把初始事实写入 SQLite。",
        "why": "后续执行、恢复、审计和观测都需要一个稳定的执行身份。",
        "next": "后台执行任务会把状态从 created 推进到 running。",
        "code": ["Runtime.create_run()", "SQLiteStore.create_run_with_event()"],
    },
    "run.started": {
        "title": "进入执行状态",
        "summary": "Run 状态从 created 变为 running，Runtime 准备 system/user messages。",
        "why": "状态迁移与事件在持久层保持一致，外部可以观察真实生命周期。",
        "next": "保存初始 Checkpoint，然后请求模型做第一次决策。",
        "code": [
            "Runtime._execute()",
            "AgentRun.transition_to()",
            "SQLiteStore.save_run_with_event()",
        ],
    },
    "model.requested": {
        "title": "请求模型决策",
        "summary": "Runtime 创建一个持久化 Step，并把 Messages、Tools 和 ModelConfig 交给 Provider。",
        "why": "一次 Run 可能包含多次模型决策，每次决策都需要独立计数和恢复位置。",
        "next": "Provider 返回文本、ToolCall，或通过 stream() 返回增量。",
        "code": [
            "Runtime._execute()",
            "Runtime._request_model()",
            "SQLiteStore.create_step_with_event()",
        ],
    },
    "model.stream.started": {
        "title": "开始消费模型流",
        "summary": "Runtime 检测到 StreamingModelProvider，开始读取 ModelTokenDelta。",
        "why": "流式能力属于 Provider 协议，但持久化和最终状态仍由 Runtime 控制。",
        "next": "每个增量被记录为 model.delta。",
        "code": ["Runtime._request_model()", "StreamingModelProvider.stream()"],
    },
    "model.delta": {
        "title": "持久化一个模型增量",
        "summary": "文本或 ToolCall 参数片段进入 Event Log，并通过同一 SSE 接口对外发送。",
        "why": "客户端断线后可以用 sequence 续读，而不必依赖一次性内存流。",
        "next": "继续消费增量，直到 finish_reason 到达。",
        "code": [
            "Runtime._consume_model_stream()",
            "Runtime._event()",
            "SQLiteStore.append_event()",
        ],
    },
    "model.stream.completed": {
        "title": "模型流读取完成",
        "summary": "Runtime 已收齐增量，并合并出完整 ModelResponse。",
        "why": "后续 Tool/Checkpoint 流程只处理统一的完整响应，不感知厂商流格式。",
        "next": "保存 model.completed，并判断是结束 Run 还是进入工具执行。",
        "code": ["Runtime._consume_model_stream()", "Runtime._request_model()"],
    },
    "model.completed": {
        "title": "模型步骤完成",
        "summary": "完整 assistant message 已生成，模型耗时、usage 和 ToolCall 信息被持久化。",
        "why": "模型结果是后续状态推进的确定事实。",
        "next": "有 ToolCall 时进入工具队列；否则完成 Run。",
        "code": [
            "Runtime._execute()",
            "SQLiteStore.save_model_tool_plan()",
            "SQLiteStore.complete_run_from_model()",
        ],
    },
    "tool.requested": {
        "title": "建立 ToolExecution",
        "summary": "模型的 ToolCall 被规范化为持久化 ToolExecution，包含参数、位置和幂等键。",
        "why": "模型提出请求与工具真正执行是两个不同阶段。",
        "next": "检查审批要求，然后验证参数并调用注册 handler。",
        "code": [
            "Runtime._create_tool_executions()",
            "ToolExecution.create()",
            "SQLiteStore.save_model_tool_plan()",
        ],
    },
    "approval.requested": {
        "title": "等待人工审批",
        "summary": "副作用工具没有执行；Run 和 ToolExecution 已持久化为等待审批。",
        "why": "高风险动作必须在执行前形成明确、可审计的人类决策点。",
        "next": "批准后恢复同一个 ToolExecution；拒绝后把拒绝结果作为 tool message 返回模型。",
        "code": ["Runtime._request_approval()", "SQLiteStore.create_approval_with_state()"],
    },
    "approval.resolved": {
        "title": "记录审批结论",
        "summary": "批准或拒绝结果已写入 Approval 和 Event Log。",
        "why": "恢复执行时必须读取持久化结论，而不是依赖浏览器内存状态。",
        "next": "Runtime.resume() 从最近 Checkpoint 继续处理等待中的工具。",
        "code": [
            "Runtime.resolve_approval()",
            "SQLiteStore.resolve_approval()",
            "Runtime.resume()",
        ],
    },
    "tool.started": {
        "title": "开始执行工具",
        "summary": "参数校验完成，ToolExecution 进入 running，CancellationToken 和幂等键被传入上下文。",
        "why": "工具边界统一负责校验、超时、取消和异常标准化。",
        "next": "handler 返回 ToolResult，或收敛为失败/未知副作用。",
        "code": ["Runtime._invoke_tool()", "ToolRegistry.invoke()", "validate_input()"],
    },
    "tool.completed": {
        "title": "工具执行完成",
        "summary": "工具结果、Run 计数、Checkpoint 和完成事件被原子化保存。",
        "why": "恢复时可以识别已完成的副作用，避免重复执行。",
        "next": "工具结果作为 tool message 交回模型，产生下一次 Step。",
        "code": ["Runtime._invoke_tool()", "SQLiteStore.save_tool_execution_with_event()"],
    },
    "checkpoint.created": {
        "title": "保存恢复点",
        "summary": "当前 Messages、step 和 tool_call_count 被持久化。",
        "why": "进程退出后 Runtime 可以从对话与工具结果的确定边界继续。",
        "next": "根据当前阶段继续模型决策、工具执行或等待外部动作。",
        "code": [
            "Runtime._checkpoint()",
            "Checkpoint.create()",
            "SQLiteStore.save_checkpoint_with_event()",
        ],
    },
    "step.completed": {
        "title": "完成一个模型步骤",
        "summary": "当前 Step 的模型决策及其工具处理已经收敛。",
        "why": "Step 是模型决策的持久化边界，便于恢复和 Trace 配对。",
        "next": "如果 Run 尚未结束，进入下一次模型请求。",
        "code": ["Runtime._process_step()", "SQLiteStore.complete_step_with_checkpoint()"],
    },
    "run.completed": {
        "title": "Run 成功完成",
        "summary": "最终文本写入 Run.result，状态进入不可逆的 completed。",
        "why": "调用方需要一个明确终态和可重复读取的最终结果。",
        "next": "可以回放事件、查看 Trace/Metrics，或重新运行场景。",
        "code": ["Runtime._execute()", "SQLiteStore.complete_run_from_model()"],
    },
    "run.failed": {
        "title": "Run 失败收敛",
        "summary": "未处理错误被标准化写入 Run.error，状态进入 failed。",
        "why": "失败必须可观察、可审计，而不是让后台任务静默消失。",
        "next": "检查事件、Trace 和 Checkpoint，判断修复后是否需要新 Run。",
        "code": ["Runtime._execute()", "SQLiteStore.save_run_with_event()"],
    },
    "run.cancelled": {
        "title": "Run 已取消",
        "summary": "取消信号被持久化，Runtime 停止继续推进模型或工具。",
        "why": "长任务需要由调用方安全地终止。",
        "next": "保留已有事件和 Checkpoint，用于审计取消前发生的动作。",
        "code": ["Runtime.cancel()", "CancellationToken.cancel()"],
    },
}


def explain_event(event: RuntimeEvent) -> dict[str, Any]:
    explanation = deepcopy(EVENT_EXPLANATIONS.get(event.type))
    if explanation is None:
        explanation = {
            "title": event.type,
            "summary": "Runtime 记录了一个持久化领域事件。",
            "why": "事件让执行过程可以被读取、恢复和审计。",
            "next": "查看后续 sequence 了解状态如何继续推进。",
            "code": ["Runtime", "SQLiteStore.append_event()"],
        }
    return explanation


def project_event_states(events: list[RuntimeEvent]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    state: dict[str, Any] = {
        "status": None,
        "step": 0,
        "model_phase": "idle",
        "active_tool": None,
        "approval": None,
        "checkpoint": None,
        "result": None,
        "error": None,
    }
    projections: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in events:
        before = deepcopy(state)
        payload = event.payload
        if event.type == "run.created":
            state["status"] = "created"
        elif event.type in {"run.started", "run.resumed", "run.recovered"}:
            state["status"] = "running"
        elif event.type == "run.paused":
            state["status"] = "paused"
        elif event.type == "run.completed":
            state["status"] = "completed"
            state["result"] = payload.get("result")
        elif event.type == "run.failed":
            state["status"] = "failed"
            state["error"] = payload.get("error")
        elif event.type == "run.cancelled":
            state["status"] = "cancelled"
        elif event.type == "model.requested":
            state["step"] = payload.get("step", state["step"])
            state["model_phase"] = "requesting"
        elif event.type == "model.stream.started":
            state["model_phase"] = "streaming"
        elif event.type in {"model.completed", "model.stream.completed"}:
            state["model_phase"] = "completed"
        elif event.type == "tool.requested":
            state["active_tool"] = payload.get("tool_name")
        elif event.type == "tool.started":
            state["active_tool"] = payload.get("tool_name") or state["active_tool"]
        elif event.type in {"tool.completed", "tool.failed", "tool.rejected", "tool.cancelled"}:
            state["active_tool"] = None
        elif event.type == "approval.requested":
            state["status"] = "waiting_for_approval"
            state["approval"] = "pending"
        elif event.type == "approval.resolved":
            state["approval"] = payload.get("status", "resolved")
        elif event.type == "checkpoint.created":
            state["checkpoint"] = payload.get("checkpoint_id")
        projections.append((before, deepcopy(state)))
    return projections

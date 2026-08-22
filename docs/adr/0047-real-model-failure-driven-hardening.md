# ADR-0047：真实模型失败驱动的 Provider 与 Acceptance 收口

- **状态**：Accepted
- **日期**：2026-08-22
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-22-003](../CHANGELOG.md#e2026-08-22-003)

## 背景

v0.8 真实模型验收首轮 15 次执行中，3 次 `small-verified-edit` 失败。持久化事实显示：一类失败来自 SSE 非 2xx 响应尚未读取时访问 `httpx.Response.text`，另一类失败来自 Acceptance 使用了 PATH 解析到的全局 Python，导致隔离 Fixture 中的 pytest 命令不可用。

## 决策

1. OpenAI-compatible Provider 在读取流式 HTTP 错误详情前必须显式 `await response.aread()`；普通响应和 SSE 响应共享同一结构化 HTTP 错误语义。
2. Acceptance Fixture 的 Python 进程必须优先使用启动 Runner 的 `sys.executable`，避免验收结果依赖外部 PATH 中的 Python 安装；非 Python 可执行文件继续遵守调用方配置的白名单。
3. 修复必须由 durable Run/Event/ToolExecution 证据驱动，并以同一 Suite、同一 Case 集合和同一 repeat 范围复测。

## 影响

### 优点

- 不再用 `ResponseNotRead` 遮蔽真实 Provider HTTP 错误。
- 修改类验收使用与 Runtime 相同的 Python 依赖环境，减少环境假失败。
- 真实模型回归仍保持隔离、脱敏和可比较。

### 代价

- Acceptance 不再完全依赖外部 PATH 选择 Python。
- Runner 当前 Python 环境必须包含目标 Fixture 所需的测试依赖。

## 被放弃的方案

- 根据模型名称或具体回答文本增加特判。
- 在报告层把 `run_error` 或 `unverified` 强行改写为通过。
- 将真实 Fixture 的完整 Prompt、Tool 参数或答案原文写入报告。

## 后续约束

Provider HTTP/SSE 错误处理必须保留可诊断的结构化异常；Acceptance 的真实失败必须先回看 durable 运行事实，再决定是否修改 Runtime。任何改变 Provider 错误边界或验收执行环境的变更，都应新增回归测试并重新运行同范围基线。

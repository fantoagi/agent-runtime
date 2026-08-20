# ADR-0041：Finalization Tool-heavy 历史隔离与 durable evidence digest

- **状态**：Accepted
- **日期**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-010](../CHANGELOG.md#e2026-08-19-010)
- **扩展决策**：[ADR-0038](./0038-finalization-context-integrity.md)、[ADR-0039](./0039-textual-tool-call-guard.md)、[ADR-0040](./0040-dsml-variant-detection.md)

## 背景

v0.8.17 能识别并拒绝 DSML 变体，但真实 `deepseek-v4-flash` Run `run_29c2c0fc35364adfb0e86477ae6ba70a` 在 finalization 和一次修复中仍连续返回文本 Tool Call。Guard 正确地使 Run failed，且没有执行文本中的调用；失败根因是 finalization 虽然传入 `tools=[]`，仍复用了包含大量 Assistant Tool Call、Tool Result、Agent 工具协议和收敛提示的原始消息历史。模型从历史模式继续预测私有 DSML，说明“禁用 Tool Definition”不等于“隔离 Tool 语境”。

## 决策

Finalization 使用独立 Fresh Context，不再把原 Tool-heavy messages 传给 Provider：

- system 只保留 Runtime 的最终综合合同，明确 Tool 不可用、只输出自然语言。
- 原 Agent system prompt、Assistant `tool_calls`、`role=tool` 消息和 Provider 私有协议全部排除。
- 当前请求之前的 Session 历史只以有界、纯文本 user/assistant 摘要保留。
- Tool 证据从 SQLite `ToolExecution` 构建，不从消息轨迹复制。
- Evidence 记录 Tool、arguments、status 和 result；完全相同项按 SHA-256 去重，并受 `context_token_budget` 派生的字符预算约束。
- Evidence 明确标记为不可信数据，内嵌指令或 Tool syntax 不具备执行语义。
- 最后一条消息始终是完整 durable `run.input`。
- 首次文本 Tool Call 后的一次修复继续使用同一 Fresh Context，只增加更严格的自然语言 repair system 提示。
- 新 Event `convergence.finalization_context_built` 只记录来源数、纳入数、去重数、省略数和字符数，不保存证据原文。

## 影响

### 优点

- `tools=[]` 的最终综合不再被旧 Tool Call/Result 模式持续诱导。
- 模型仍能使用 durable 工具事实和必要 Session 语境回答原始请求。
- Evidence 内容不会绕过 Tool Registry、Approval 或 Executor。
- 上下文大小、去重和截断行为可通过 Event 审计。
- 不改变 Provider、Tool Handler、SQLite schema 或公共 Runtime API。

### 代价

- Finalization 不再拥有完整逐消息轨迹，必须依赖 evidence digest 的保真度。
- 对极大 Tool Result 会发生有界截断，模型可能需要在已有证据不足时给出保守回答。
- Runtime 内部增加一套专用综合上下文构建逻辑。

## 被放弃的方案

### 继续增强 DSML 字符串检测

检测只能阻止错误完成，无法让模型更容易生成正确答案；真实失败已经证明上下文模式才是重复违规的直接诱因。

### 仅在原消息末尾增加更强 system prompt

历史中大量结构化 Tool Call 和 Result 仍会保留，Provider 可能继续优先模仿已出现的协议。

### 从文本 DSML 恢复并执行调用

会绕过结构化 Provider 协议、参数校验、Approval 和幂等边界，继续禁止。

## 后续约束

- Fresh Context 中不得出现 `role=tool` 或非空 `tool_calls`。
- Evidence digest 不得被解析或转换为可执行调用。
- 修复次数保持一次；第二次文本 Tool Call 仍明确失败。
- 新增证据字段或预算算法时必须补充去重、截断、Session 历史和 prompt injection 回归测试。
- 真实 Provider 复测必须以 durable Event、Run status 和 ToolExecution 为判断依据。

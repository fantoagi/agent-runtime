# ADR-0039：Finalization 文本化 Tool Call 检测与有界修复

- **状态**：Accepted
- **日期**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-008](../CHANGELOG.md#e2026-08-19-008)

## 背景

证据收敛进入 finalization 后，Runtime 已向 Provider 发送空 Tool Definition。部分 OpenAI-compatible 模型仍可能把 Tool Call 序列化为 DSML、XML 或 JSON 普通文本，并以 `response.tool_calls=[]` 返回。如果 Runtime 只检查结构化 Tool Call，该协议文本会被保存为 Assistant 最终答案，Run 还会被错误标记为 completed。

## 决策

Runtime 在 finalization 响应进入 Step 和 Run 终态前执行保守协议检查：

- 识别主要或完整由 DSML、XML Tool envelope 构成的文本。
- 对 JSON 只识别结构受限且 name 指向当前 Agent 已注册 Tool 的 function/tool_calls envelope。
- 永不从普通文本解析或执行 Tool。
- 首次命中写入 `convergence.textual_tool_call_detected`，保存带 `convergence.finalization_repair_requested` 的 Checkpoint，追加 Runtime repair note，并再次以 `tools=[]` 和原始 user request 请求模型。
- 修复最多一次；第二次命中抛出 `ProviderProtocolError`，Run 明确失败。
- Finalization 的 Streaming delta 在 Runtime 内缓冲。只有通过检查的最终内容才作为单个 durable `model.delta` 发布，避免 CLI、SSE 或 Learning Console 先显示伪 Tool 协议。

## 影响

### 优点

- 文本化 Tool Call 不会被误当作成功答案。
- Runtime 不会执行模型隐藏在文本中的动作。
- 一次有界修复吸收偶发 Provider 协议漂移，同时避免无限模型循环。
- Detection、repair 和失败均有 durable Event 与 Checkpoint，可在 CLI 和 Learning Console 中追溯。
- Streaming 客户端不会看到随后又被判无效的 DSML/XML/JSON 内容。

### 代价

- Finalization 最后一轮不再逐 token 发布，而是在完整响应通过校验后一次发布。
- 首次协议漂移会额外消耗一次模型调用。
- 保守检测不会覆盖任意未知私有协议；JSON 判断依赖当前 Agent Tool name。
- 用户若明确要求输出一个纯 Tool Call envelope，且该 Run 恰好进入自动 finalization，可能被当作协议漂移；带解释正文的示例不会拦截。

## 被放弃的方案

### 执行文本中的 Tool Call

普通文本没有可信的结构化协议、授权与幂等语义，执行会绕过 Tool Registry、Approval 和审计边界。

### 直接删除 Tool 语法并把剩余内容当答案

纯 envelope 删除后通常为空，也无法证明剩余自然语言完整可靠，会制造假成功。

### 无限重试直到模型返回自然语言

会重新引入无界循环和不可预测成本，并掩盖 Provider 持续违反协议的问题。

### 对所有包含 `<tool_call>` 或 JSON 的答案一律拒绝

会误伤协议解释、Markdown 示例和普通结构化答案，因此采用“纯 envelope + 已知 Tool name”的保守判定。

## 后续约束

- 文本化 Tool Call 永远不得直接进入 Tool Executor。
- 自动协议修复次数必须保持有界，默认且当前固定为一次。
- 修复请求必须继续使用 `tools=[]`，并保留 durable 原始 user request。
- 任何新识别格式都必须补充正例、反例和 Streaming 回归测试。
- 若未来恢复 finalization 的逐 token 展示，必须先提供不会向客户端泄漏无效协议文本的可撤销流语义。

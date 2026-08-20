# ADR-0038：Finalization 原始请求完整性与 Context pin

- **状态**：Accepted
- **日期**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-007](../CHANGELOG.md#e2026-08-19-007)

## 背景

ADR-0037 在只读检查无进展时禁用 Tool，并要求模型根据已收集证据完成最终回答。真实 CLI 验证表明防循环机制能够正确收敛，但最终模型偶尔声称看不到用户请求，并对纯解释问题输出 `No files were modified`。原因是当前 Run 的原始 user message 与普通 Session 历史使用相同压缩优先级，而 finalization system note 又包含面向修改任务的默认措辞。

## 决策

当前 Run 的 durable `run.input` 必须独立于普通历史消息得到保护：

- `_initial_messages()` 使用 Runtime 专用 message name 标记当前 Run 原始 user message。
- `ContextBuilder` 将当前请求和 finalization 请求重申视为 pinned group；即使超过 token budget，也不得省略或截断其内容。
- 恢复旧 Checkpoint 时，Runtime 从 durable `run.input` 查找、标记或补齐当前请求，不依赖旧消息是否已有 name。
- 触发 finalization 时，Runtime 持久化 system 收敛说明，并在消息尾部追加一条 role 为 `user`、内容等于 `run.input` 的请求重申。
- 用户原始文本不复制到 system role，避免提升其指令优先级。
- finalization note 不再默认提及 workspace change；它只要求回答确切原问题、不得声称请求缺失，并准确陈述实际发生的动作。
- 最终 Provider 请求仍使用 `tools=[]`，不改变 ADR-0037 的 Tool 禁用和协议违规处理。

## 影响

### 优点

- Context 压缩、Session 历史和长 Tool 证据不会删除当前 Run 的问题。
- finalization 的最后一个 user message 明确聚焦本轮原始请求。
- 纯解释任务不再被编码任务模板诱导输出无关修改状态。
- 旧 Checkpoint 可以在不迁移 SQLite schema 的情况下获得兼容修复。
- 原始用户文本保持 user role，不扩大 prompt injection 权限。

### 代价

- 当前请求可能在模型消息中出现两次：一次是原始位置，一次是 finalization 尾部重申。
- 极长 `run.input` 会优先保证完整性，可能使 `ContextBuildResult.overflow=True`；Runtime 不会静默截断用户目标。
- message name 成为 Runtime 与 ContextBuilder 之间的内部约定，需要回归测试保护。

## 被放弃的方案

### 将 `run.input` 直接插入 system note

虽然能提高可见性，但会把用户提供的文本提升到 system role，扩大不可信指令的优先级，因此拒绝。

### 只依赖最近消息窗口

finalization 前可能存在大量 Tool Call/Result 组，且 Session 历史和 summary 会改变选择结果；最近窗口不是当前请求完整性的可靠合同。

### 只修改提示词，不修改 ContextBuilder

无法解决当前 user message 已在压缩阶段被省略或截断的问题，也不能保证恢复旧 Checkpoint 后仍然存在。

## 后续约束

- 任何 Context 压缩策略都必须保留当前 Run 的完整 `run.input`。
- finalization 请求必须同时满足 `tools=[]` 和当前请求可见。
- 用户输入不得通过 Runtime 辅助提示提升为 system role。
- Session 历史中的旧 user message 不能替代当前 Run 请求。
- 修改 pinned message name 或 overflow 语义时必须更新 ADR 和回归测试。

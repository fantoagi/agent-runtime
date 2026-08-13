# ADR-0005：Step 与 ToolExecution 持久化和幂等恢复

- **状态**：Accepted
- **日期**：2026-08-13
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-13-001](../CHANGELOG.md#e2026-08-13-001)

## 背景

v0.1 只依赖消息 Checkpoint 恢复。模型一次返回多个工具调用、进程在工具执行中崩溃或副作用已经发生但结果尚未持久化时，Runtime 无法可靠判断哪些工具应该跳过、重试或交由人工确认。

## 决策

Runtime 将一次模型响应持久化为 `Step`，并将其中每个工具调用持久化为有序 `ToolExecution`。每个执行拥有稳定 idempotency key 和明确状态。工具完成状态、Run 计数、Checkpoint 与完成 Event 在同一 SQLite 事务中提交。

恢复时：

1. `completed`、`failed` 和 `rejected` 直接转换为 tool message，不重复调用 handler。
2. 无副作用的 `running` 执行回退为 `pending`，允许安全重试。
3. 有副作用的 `running` 执行转换为 `unknown`，Run 暂停并等待人工确认。
4. 模型返回的多工具调用全部预先持久化，审批只阻塞当前调用，不丢失后续队列。

## 影响

### 优点

- 进程重启后可以精确恢复到未完成工具调用。
- 已持久化完成的工具不会被重复执行。
- 副作用不确定性不再被静默当作失败或自动重试。
- Step 和 ToolExecution 为后续评估、Tracing、Worker lease 和并行调度提供稳定实体。
- 状态和事件的事务一致性更强。

### 代价

- 数据模型和恢复状态机明显更复杂。
- SQLite schema 需要迁移和兼容旧数据库。
- 工具实现需要理解 idempotency key、side-effect 标记和协作式取消。
- `unknown` 状态需要人工处置入口。

## 被放弃的方案

- 只在 Checkpoint 消息中推断已完成工具：无法可靠表达运行中和未知副作用。
- 所有运行中工具重启后自动重试：会重复写文件、发消息或调用外部业务接口。
- 所有工具都要求人工审批：安全但严重降低自动化能力，且不能解决只读工具的高效恢复。

## 后续约束

- 新工具必须正确声明 `side_effecting`；涉及外部写操作的工具默认应为 `True`。
- 外部系统工具应使用 `ToolContext.idempotency_key` 实现业务级去重。
- 任何改变 ToolExecution 状态、幂等键、恢复或未知副作用语义的变更必须更新 ADR。
- 分布式 Worker 必须在此模型上增加 lease、heartbeat、owner 和 fencing token，不能绕过 ToolExecution。

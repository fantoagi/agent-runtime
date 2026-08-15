# ADR-0017：UNKNOWN Outcome 人工确认与审计

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-008](../CHANGELOG.md#e2026-08-15-008)

## 背景

副作用 Tool 在进程崩溃、超时或取消后可能已经影响外部系统。原有 `retry` 会把同一个 ToolExecution 放回 PENDING，存在重复写入风险；确认后自动把 Run 标成 RUNNING 也会产生没有实际 Task 的僵尸状态。

## 决策

UNKNOWN 只允许 `confirmed_succeeded` 或 `confirmed_failed`。确认动作必须记录 reason、resolved_by 和 resolved_at，写入 `tool.outcome_confirmed`；确认后 Run 保持 PAUSED，必须显式 `resume()`。旧 `completed`/`failed` 作为兼容别名，`retry` 明确拒绝。

## 影响

### 优点

- 不会把不确定副作用自动执行第二次。
- 人工判断和操作者可审计。
- 状态与实际执行 Task 保持一致。

### 代价

- 运维人员必须核对外部系统。
- 确认和恢复需要两个明确步骤。

## 被放弃的方案

自动重试、将 UNKNOWN 当作 FAILED、或确认后自动启动 Run 都不能同时满足副作用安全和状态一致性。

## 后续约束

任何执行器和 Sandbox 都必须保留 UNKNOWN 人工确认边界，不得通过重试策略绕过。

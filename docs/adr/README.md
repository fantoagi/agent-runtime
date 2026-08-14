# Architecture Decision Records

ADR 用于记录会长期约束 Agent Runtime 的关键设计决策。ADR 一旦 Accepted，不直接覆盖原结论；如果决策变化，应新增 ADR 并将旧 ADR 标记为 Superseded。

## 何时需要 ADR

出现以下任一情况时必须新增或更新 ADR：

- 公共 Python API 或协议变化。
- Run 状态机或 Event schema 变化。
- SQLite schema、迁移方式或恢复语义变化。
- Model Provider 或 Tool Protocol 变化。
- 文件、网络、Shell、沙箱或凭据安全边界变化。
- 多 Agent、分布式调度、租约或幂等模型变化。
- 影响兼容性、可靠性或安全性的替代方案选择。

## 状态

- `Proposed`：方案正在评审。
- `Accepted`：当前采用的决策。
- `Deprecated`：不建议继续使用，但尚未完全移除。
- `Superseded`：已被新的 ADR 替代。
- `Rejected`：明确不采用。

## 当前 ADR

| ADR | 状态 | 日期 | 主题 | 关联变更 |
| --- | --- | --- | --- | --- |
| [ADR-0010](./0010-parent-child-run-delegation.md) | Accepted | 2026-08-14 | Parent/Child Run 与持久化多 Agent 委派模型 | E2026-08-14-007 |
| [ADR-0009](./0009-learning-console.md) | Accepted | 2026-08-14 | Learning Console 作为 Runtime 外部教学 Adapter | E2026-08-14-006、E2026-08-14-005、E2026-08-14-004 |
| [ADR-0008](./0008-observability-evals.md) | Accepted | 2026-08-14 | Observability 和 Evals 基于持久化执行事实派生 | E2026-08-14-002 |
| [ADR-0007](./0007-model-token-streaming.md) | Accepted | 2026-08-14 | Model Provider Token Streaming 与 Runtime Event 边界 | E2026-08-14-001 |
| [ADR-0005](./0005-tool-execution-idempotency.md) | Accepted | 2026-08-13 | Step 与 ToolExecution 持久化和幂等恢复 | E2026-08-13-001 |
| [ADR-0001](./0001-runtime-kernel.md) | Accepted | 2026-08-11 | Runtime Kernel 和 Run 状态机 | E2026-08-11-001 |
| [ADR-0002](./0002-model-provider-protocol.md) | Accepted | 2026-08-11 | Model Provider 抽象 | E2026-08-11-001 |
| [ADR-0003](./0003-sqlite-event-checkpoint.md) | Accepted | 2026-08-11 | SQLite Event Log 与 Checkpoint | E2026-08-11-001 |
| [ADR-0004](./0004-tool-security-boundary.md) | Accepted | 2026-08-11 | Tool Registry 和 workspace 安全边界 | E2026-08-11-001 |

创建新 ADR 时复制 [ADR 模板](../templates/adr.md)，并使用连续四位编号。

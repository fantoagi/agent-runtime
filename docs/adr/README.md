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
| [ADR-0024](./0024-incident-diagnostic-bundle.md) | Accepted | 2026-08-16 | 故障诊断包允许列表、内容排除与原子导出 | E2026-08-16-003 |
| [ADR-0023](./0023-operational-observability.md) | Accepted | 2026-08-16 | 持久化事实与瞬时运维信号分层 | E2026-08-16-002 |
| [ADR-0022](./0022-runtime-backup-restore.md) | Accepted | 2026-08-16 | Runtime 状态归档与离线恢复 | E2026-08-16-001 |
| [ADR-0021](./0021-agent-definition-snapshots.md) | Accepted | 2026-08-15 | AgentDefinition 不可变快照与恢复绑定 | E2026-08-15-010 |
| [ADR-0020](./0020-run-submission-idempotency-admission.md) | Accepted | 2026-08-15 | Run 提交幂等与运行时准入控制 | E2026-08-15-009 |
| [ADR-0019](./0019-runtime-doctor.md) | Accepted | 2026-08-15 | Runtime Doctor 只读诊断模型 | E2026-08-15-008 |
| [ADR-0018](./0018-crash-recovery-contract.md) | Accepted | 2026-08-15 | 进程强杀恢复合同与 Workflow Snapshot 恢复 | E2026-08-15-008 |
| [ADR-0017](./0017-unknown-outcome-confirmation.md) | Accepted | 2026-08-15 | UNKNOWN Outcome 人工确认与审计 | E2026-08-15-008 |
| [ADR-0016](./0016-fastapi-runtime-ownership-sse.md) | Accepted | 2026-08-15 | FastAPI Runtime 所有权、Lifespan 与 SSE 长稳 | E2026-08-15-007 |
| [ADR-0015](./0015-runtime-shutdown-sqlite-recovery.md) | Accepted | 2026-08-15 | Runtime Shutdown、SQLite Durability 与进程恢复 | E2026-08-15-006 |
| [ADR-0014](./0014-provider-async-transport-retry.md) | Accepted | 2026-08-15 | Provider 异步传输、协议校验与重试 | E2026-08-15-005 |
| [ADR-0013](./0013-tool-isolation-unknown-outcome.md) | Accepted | 2026-08-15 | 同步 Tool 隔离与 UNKNOWN Outcome | E2026-08-15-005 |
| [ADR-0012](./0012-quality-gates.md) | Accepted | 2026-08-15 | 可靠性质量门禁与发布合同 | E2026-08-15-004 |
| [ADR-0011](./0011-context-session-memory.md) | Accepted | 2026-08-15 | Context Window、Session 与 Scoped Long-term Memory 边界 | E2026-08-15-001 |
| [ADR-0010](./0010-parent-child-run-delegation.md) | Accepted | 2026-08-14 | Parent/Child Run 与持久化多 Agent 委派模型 | E2026-08-14-007 |
| [ADR-0009](./0009-learning-console.md) | Accepted | 2026-08-14 | Learning Console 作为 Runtime 外部教学 Adapter | E2026-08-15-003、E2026-08-15-002、E2026-08-14-006、E2026-08-14-005、E2026-08-14-004 |
| [ADR-0008](./0008-observability-evals.md) | Accepted | 2026-08-14 | Observability 和 Evals 基于持久化执行事实派生 | E2026-08-14-002 |
| [ADR-0007](./0007-model-token-streaming.md) | Accepted | 2026-08-14 | Model Provider Token Streaming 与 Runtime Event 边界 | E2026-08-14-001 |
| [ADR-0005](./0005-tool-execution-idempotency.md) | Accepted | 2026-08-13 | Step 与 ToolExecution 持久化和幂等恢复 | E2026-08-13-001 |
| [ADR-0001](./0001-runtime-kernel.md) | Accepted | 2026-08-11 | Runtime Kernel 和 Run 状态机 | E2026-08-11-001 |
| [ADR-0002](./0002-model-provider-protocol.md) | Accepted | 2026-08-11 | Model Provider 抽象 | E2026-08-11-001 |
| [ADR-0003](./0003-sqlite-event-checkpoint.md) | Accepted | 2026-08-11 | SQLite Event Log 与 Checkpoint | E2026-08-11-001 |
| [ADR-0004](./0004-tool-security-boundary.md) | Accepted | 2026-08-11 | Tool Registry 和 workspace 安全边界 | E2026-08-11-001 |

创建新 ADR 时复制 [ADR 模板](../templates/adr.md)，并使用连续四位编号。

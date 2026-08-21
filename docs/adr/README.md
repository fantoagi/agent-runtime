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
| [ADR-0044](./0044-post-change-verification-boundary.md) | Accepted | 2026-08-21 | 修改后的 diff/status/validation 证据时间边界 | E2026-08-21-001 |
| [ADR-0043](./0043-new-file-verification-evidence.md) | Accepted | 2026-08-20 | 新建文件使用 durable created 与 Git status 验证证据 | E2026-08-20-002 |
| [ADR-0042](./0042-real-model-acceptance-baseline.md) | Accepted | 2026-08-20 | 隔离真实模型验收与 durable 脱敏报告 | E2026-08-20-001 |
| [ADR-0041](./0041-fresh-finalization-context.md) | Accepted | 2026-08-19 | Finalization Tool-heavy 历史隔离与 durable evidence digest | E2026-08-19-010 |
| [ADR-0040](./0040-dsml-variant-detection.md) | Accepted | 2026-08-19 | Unicode 兼容的 DSML 变体识别边界 | E2026-08-19-009 |
| [ADR-0039](./0039-textual-tool-call-guard.md) | Accepted | 2026-08-19 | Finalization 文本化 Tool Call 检测与有界修复 | E2026-08-19-008 |
| [ADR-0038](./0038-finalization-context-integrity.md) | Accepted | 2026-08-19 | Finalization 原始请求完整性与 Context pin | E2026-08-19-007 |
| [ADR-0037](./0037-evidence-aware-convergence.md) | Accepted | 2026-08-19 | 证据感知的只读检查收敛与无工具最终综合 | E2026-08-19-006 |
| [ADR-0036](./0036-read-only-tool-convergence.md) | Accepted | 2026-08-19 | 当前 Run 只读 Tool 结果复用与收敛边界 | E2026-08-19-005 |
| [ADR-0035](./0035-interactive-cli-execution-transparency.md) | Accepted | 2026-08-19 | Interactive CLI 执行阶段、审批连续性、预览与事实摘要 | E2026-08-19-002、E2026-08-19-003、E2026-08-19-004 |
| [ADR-0034](./0034-interactive-cli-presentation.md) | Accepted | 2026-08-18 | Streaming Markdown 与 compact/verbose 终端展示 | E2026-08-18-003 |
| [ADR-0033](./0033-verified-task-completion.md) | Accepted | 2026-08-18 | 本地 Coding Run 一次性完成证据检查 | E2026-08-18-002 |
| [ADR-0032](./0032-artifact-paging-workspace-discovery.md) | Accepted | 2026-08-18 | Artifact 分页读取与 Workspace 继续发现策略 | E2026-08-18-001 |
| [ADR-0031](./0031-project-workspace-instructions.md) | Accepted | 2026-08-17 | 项目指令作为有界本地 Agent 上下文 | E2026-08-17-004 |
| [ADR-0030](./0030-bounded-read-batch-patch.md) | Accepted | 2026-08-17 | 有界文件读取与批量精确 Patch | E2026-08-17-003 |
| [ADR-0029](./0029-read-only-git-workspace-tools.md) | Accepted | 2026-08-17 | Git 只读工作区检查通过受管 Sandbox | E2026-08-17-002 |
| [ADR-0028](./0028-coding-workspace-tools.md) | Accepted | 2026-08-17 | 结构化 Coding Workspace Tool 与 argv 进程执行 | E2026-08-17-001 |
| [ADR-0027](./0027-interactive-cli-session-history.md) | Accepted | 2026-08-16 | Interactive CLI Adapter 与显式 Session 历史 | E2026-08-16-006 |
| [ADR-0026](./0026-local-runtime-bootstrap-single-owner.md) | Accepted | 2026-08-16 | 本地 Runtime 配置驱动启动与单执行 Owner | E2026-08-16-005 |
| [ADR-0025](./0025-local-process-sandbox-capability-policy.md) | Accepted | 2026-08-16 | LocalProcessSandbox 与 Tool Capability 显式允许边界 | E2026-08-16-004 |
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

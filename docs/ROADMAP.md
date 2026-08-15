# Agent Runtime 演进路线图

- **最近更新**：2026-08-15
- **当前版本**：v0.7.8
- **当前阶段**：v0.7.8 Durable Submission 完成；v0.7.9 Agent Definition Recovery 进入下一稳定化阶段
- **路线状态**：Living Document

> 本文件记录未来演进方向。已经完成的事实以 [CURRENT.md](./CURRENT.md) 为准，完成时间线以 [CHANGELOG.md](./CHANGELOG.md) 为准，当前实现以 [ARCHITECTURE.md](./ARCHITECTURE.md) 为准。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| ✅ completed | 已完成并存在 Change ID、测试和 commit 追溯 |
| 🚧 in-progress | 正在实现，同一时间最多存在一个主里程碑 |
| 📋 planned | 已确定方向和顺序，但尚未开始实现 |
| 💡 candidate | 候选方向，需要进一步验证价值和依赖 |
| ⛔ out-of-scope | 当前路线明确不优先实现 |

## 版本总览

| 版本 | 状态 | 核心目标 | 完成记录 |
| --- | --- | --- | --- |
| v0.1 | ✅ completed | 单 Agent Runtime MVP | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| v0.2 | ✅ completed | 可靠执行、恢复、幂等和工具状态持久化 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| v0.3 | ✅ completed | FastAPI Run API 与 SSE | [E2026-08-13-002](./CHANGELOG.md#e2026-08-13-002) |
| v0.4 | ✅ completed | Model Token Streaming | [E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001) |
| v0.5 | ✅ completed | Observability、Tracing、Metrics 与 Evals | [E2026-08-14-002](./CHANGELOG.md#e2026-08-14-002) |
| v0.5.1 | ✅ completed | 可视化 Learning Console 与执行流程教学 | [E2026-08-14-004](./CHANGELOG.md#e2026-08-14-004) |
| v0.5.2 | ✅ completed | Learning Console 动态事件泳道图 | [E2026-08-14-005](./CHANGELOG.md#e2026-08-14-005) |
| v0.5.3 | ✅ completed | Learning Console 空状态显示与布局优化 | [E2026-08-14-006](./CHANGELOG.md#e2026-08-14-006) |
| v0.6 | ✅ completed | 多 Agent 编排基础 | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| v0.7 | ✅ completed | Context、Session 与长期记忆 | [E2026-08-15-001](./CHANGELOG.md#e2026-08-15-001) |
| v0.7.1 | ✅ completed | Learning Console 覆盖多 Agent、Context、Memory 与 Artifact | [E2026-08-15-002](./CHANGELOG.md#e2026-08-15-002) |
| v0.7.2 | ✅ completed | 多 Agent 独立泳道、委派分叉与结果汇聚连线 | [E2026-08-15-003](./CHANGELOG.md#e2026-08-15-003) |
| v0.7.3 | ✅ completed | 质量基线、行为合同与跨平台门禁 | [E2026-08-15-004](./CHANGELOG.md#e2026-08-15-004) |
| v0.7.4 | ✅ completed | Tool 隔离、UNKNOWN 与 Provider 异步传输 | [E2026-08-15-005](./CHANGELOG.md#e2026-08-15-005) |
| v0.7.5 | ✅ completed | Runtime 生命周期、SQLite durability 与恢复 | [E2026-08-15-006](./CHANGELOG.md#e2026-08-15-006) |
| v0.7.6 | ✅ completed | FastAPI/SSE 长稳与发布验证 | [E2026-08-15-007](./CHANGELOG.md#e2026-08-15-007) |
| v0.7.7 | ✅ completed | 崩溃恢复、UNKNOWN 闭环、Workflow 恢复与 Runtime Doctor | [E2026-08-15-008](./CHANGELOG.md#e2026-08-15-008) |
| v0.7.8 | ✅ completed | Run 提交幂等、容量背压与模型并发限制 | [E2026-08-15-009](./CHANGELOG.md#e2026-08-15-009) |
| v0.7.9 | 📋 planned | AgentDefinition 快照与无重新注册恢复 | — |
| v0.8 | 📋 planned | Sandbox、Tool Capability 与 Secret 安全（后置） | — |
| v0.9 | 📋 planned | 分布式 Worker、Queue 与 Lease | — |
| v0.10 | 📋 planned | 多租户、权限、预算和生产治理 | — |
| v1.0 | 📋 planned | 稳定 Runtime Contract 与生产发布 | — |

## 演进原则

1. **可靠性优先于功能数量**：新能力不能破坏已有 Run、Event、Checkpoint、Approval 和恢复语义。
2. **每个版本可独立运行和验收**：功能代码、测试、文档和 Change ID 必须一起完成。
3. **先单机建立正确模型，再扩展分布式**：多 Agent、Memory 和 Sandbox 在单机语义稳定后，再进入 Worker 和多节点执行。
4. **执行事实只有一个来源**：Run、Event、Step、ToolExecution 和 Checkpoint 是执行事实；Trace、Metrics 和 Eval 是派生或关联结果。
5. **安全能力默认收紧**：网络、Shell、Secret、副作用工具和跨租户数据默认不开放。
6. **保持协议可替换**：Provider、Store、Sandbox、Memory、Queue 和 Event Publisher 通过协议隔离具体实现。
7. **限制版本范围**：每个里程碑都明确非目标，避免同时引入过多新概念。

## v0.6：多 Agent 编排基础

- **状态**：✅ completed
- **前置版本**：v0.5.3
- **完成记录**：[E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007)
- **目标**：让一个 Parent Agent 可以通过持久化 Child Run 委派任务，并支持顺序、并行和结果汇聚。

### 计划范围

- `AgentRegistry`：注册、发现和校验可委派 Agent。
- `RunRelation`：保存 Parent Run、Child Run、Root Run 和关系类型。
- `Runtime.delegate()`：通过正式 Runtime 路径创建和执行 Child Run。
- 顺序 Workflow：Planner → Worker → Reviewer。
- 并行 Workflow：最大并发数、超时、取消传播和部分失败策略。
- 汇聚策略：`all`、`best_effort`、`first_success`。
- Parent/Child Event 和 Trace Tree。
- 多 Agent Workflow Eval。

### 分阶段实施

```text
✅ Parent/Child Run、RunRelation、delegate()
✅ SequentialWorkflow 和结果传递
✅ ParallelWorkflow、并发限制和汇聚策略
✅ Parent/Child 取消传播、恢复和幂等委派
✅ 多 Agent Trace、Metrics 和 Eval
```

### 验收重点

- Parent 和 Child 都有独立 Run ID、Trace ID、Event 和 Checkpoint。
- 能查询父子关系和完整 Trace Tree。
- Parent Cancel 能传播到活动 Child Run。
- Child Run 完成后不会因 Parent 恢复而重复创建。
- 单 Agent 既有功能保持兼容。

### 明确非目标

- 跨机器 Child Run。
- 动态图形化 Workflow Designer。
- 无限自主递归和 Agent 自动创建 Agent。
- 长期记忆与 Docker Sandbox。

### 预计 ADR

- [ADR-0010](./adr/0010-parent-child-run-delegation.md)：Parent/Child Run 与持久化多 Agent 委派模型。

## v0.7：Context、Session 与长期记忆

- **状态**：✅ completed
- **前置版本**：v0.6
- **完成记录**：[E2026-08-15-001](./CHANGELOG.md#e2026-08-15-001)
- **目标**：管理模型上下文窗口，并让多个 Run 在受控范围内共享可检索记忆。

### 完成范围

- `ContextBuilder` 和模型输入 token budget。
- 消息选择、旧消息裁剪和大 Tool Result Artifact 化。
- 可追溯的 Context Compaction 与 Summary。
- `Session` 与多个 Run 的关系。
- `MemoryStore` 协议、Memory Record 和生命周期。
- `session`、`agent` 两种 Memory Scope。
- SQLite FTS5 关键词检索；向量检索作为后续扩展。
- Memory Search Trace、Metrics 和 Eval。

### 验收重点

- System Prompt、未完成 Tool Call 和最近消息不会被错误裁剪。
- Context 超出预算时行为可预测、可追溯。
- Memory 可以关联 source Run 和 Trace。
- Memory 支持删除、过期和 Scope 隔离。

### 明确非目标

- 全局共享记忆。
- 一次接入多个向量数据库。
- 自动永久保存所有对话。

### 关联 ADR

- [ADR-0011](./adr/0011-context-session-memory.md)：Context Window、Session 与 Scoped Long-term Memory 边界。

## v0.7.x：Reliability / Hardening Train

- **状态**：✅ completed
- **基线版本**：v0.7.2
- **目标**：不增加业务能力，把现有 Runtime 提升到可在单机环境长期、稳定、可恢复运行。

### 完成范围

- v0.7.3：Ruff、Mypy strict、coverage、跨平台 CI、Wheel 安装合同。
- v0.7.4：同步 Tool 有界隔离、UNKNOWN、异步 Provider、协议校验和精确重试。
- v0.7.5：shutdown/context manager、pause/resume、SQLite durability、checksum 和 Workflow 快照。
- v0.7.6：FastAPI ownership/lifespan、SSE heartbeat/reconnect、stress/soak 和发布验证。
- v0.7.7：UNKNOWN 人工确认、Workflow snapshot 恢复、Runtime Doctor 和 Crash Matrix。

### 发布约束

- PR 通过 20 并发、core 90/80 覆盖率和 Wheel smoke。
- Nightly 执行 100 并发、故障测试重复、30 分钟 soak 和性能基线。
- v0.7.9 完成前不开始 v0.8；先消除 AgentDefinition 重新注册这一恢复缺口。

### 关联 ADR

- [ADR-0012](./adr/0012-quality-gates.md) 至 [ADR-0016](./adr/0016-fastapi-runtime-ownership-sse.md)。
## v0.8：Sandbox、Tool Capability 与 Secret 安全

- **状态**：📋 planned
- **前置版本**：v0.7
- **目标**：为代码、进程、文件和网络工具建立明确的执行隔离和能力授权边界。

### 计划范围

- `SandboxExecutor` 协议。
- `LocalProcessSandbox`：无 `shell=True`、超时、输出限制、环境变量白名单和子进程取消。
- 可选 `DockerSandbox`：非 root、只读根文件系统、CPU/内存/PID 限制和默认禁网。
- Tool Capability 声明和 `allow`、`deny`、`require_approval`、`sandbox_only` 策略。
- `SecretProvider` 和运行时临时注入。
- Artifact MIME、大小、SHA-256、provenance 和生命周期。

### 验收重点

- Run Cancel 可以终止 Sandbox 中的活动任务。
- Secret 不进入 Prompt、Event、Checkpoint、Trace 和 Eval Report。
- Workspace 和容器文件边界不能被路径逃逸绕过。
- 高风险能力默认需要审批或拒绝。

### 明确非目标

- 在所有操作系统上提供完全一致的强隔离。
- Kubernetes 和 Firecracker 调度。
- 自动安装任意不可信依赖。

### 预计 ADR

- `ADR-0017`：Sandbox、Tool Capability 与 Secret 安全边界。

## v0.9：分布式 Worker、Queue 与 Lease

- **状态**：📋 planned
- **前置版本**：v0.8
- **目标**：将 API、调度和执行分离，使 Run 可以被多个 Worker 安全领取、续租、恢复和接管。

### 计划范围

- API Server 只创建任务，不直接执行 Run。
- Run Queue 和 Worker 消费循环。
- Worker ID、Lease、Heartbeat、Attempt 和 Lease Expiry。
- Worker 崩溃后的 Run 接管与 Checkpoint 恢复。
- `PostgreSQLStore`，同时保留 `SQLiteStore` 本地模式。
- Event Publisher 协议和一个消息系统实现。
- Queue、Worker、Lease 和接管 Metrics。
- 跨 Worker Trace ID 传播。

### 验收重点

- 两个 Worker 不会同时持有同一有效 Lease。
- Worker 崩溃后 Run 可以被其他 Worker 接管。
- 已完成 ToolExecution 不会重复执行。
- 副作用结果不确定时仍进入 `unknown` 和人工处置。

### 明确非目标

- 同时支持 Redis、NATS、Kafka 等多个 Broker。
- Kubernetes Operator。
- 跨区域强一致调度。

### 预计 ADR

- `ADR-0018`：Worker Queue、Lease 与分布式恢复模型。

## v0.10：多租户、权限、预算和生产治理

- **状态**：📋 planned
- **前置版本**：v0.9
- **目标**：让 Runtime 可以被多个用户和应用安全使用，并具备成本、权限和审计边界。

### 计划范围

- `tenant_id`、`project_id`、`user_id` 数据隔离。
- API 身份认证和 RBAC。
- Run token、费用、步骤、Tool Call 和时间预算。
- Tenant、User、Agent、Model 和 Tool 维度限流。
- Runtime Event 与 Audit Log 职责分离。
- Secret 管理和凭据轮换。
- OpenTelemetry Trace/Metrics Exporter。
- 配置验证、数据库迁移命令、Readiness、Liveness、备份和恢复说明。

### 验收重点

- 不同 Tenant 不能查询或控制彼此的 Run、Artifact、Memory 和 Eval。
- 审批、Agent 修改和 Artifact 下载都有审计记录。
- 超出预算的 Run 会产生明确事件并停止执行。
- Trace、Metrics 和 Audit 能关联 Tenant、User、Run 和 Eval。

### 明确非目标

- 企业级计费结算平台。
- 完整 Web 管理控制台。
- 同时支持所有身份供应商。

### 预计 ADR

- `ADR-0019`：多租户、权限、预算与审计模型。

## v1.0：稳定 Runtime Contract 与生产发布

- **状态**：📋 planned
- **前置版本**：v0.10
- **目标**：冻结关键公共协议，提供可升级、可部署、可恢复和有兼容性保证的第一个稳定版本。

### 稳定范围

- Run 状态机和 Runtime Event Envelope。
- Model Provider 和 Token Streaming Protocol。
- Tool、Sandbox、Approval 和 Secret Protocol。
- Checkpoint、Recovery、Lease 和幂等语义。
- Parent/Child Run 与 Workflow 模型。
- Context、Session 和 Memory Protocol。
- Trace、Metrics 和 Eval Report 格式。
- HTTP API、Python SDK 和数据库迁移流程。

### 发布要求

- Semantic Versioning 和兼容性策略。
- 历史数据库升级测试。
- 单机与分布式部署文档。
- 故障恢复与安全操作手册。
- 性能基准和容量边界。
- Docker Compose 示例。
- Release Notes 和 Upgrade Guide。
- Eval Suite 作为发布质量门禁。

### 验收重点

- 历史版本数据可以升级且不破坏未完成 Run。
- 公共 API 和 Event schema 有兼容性测试。
- 单机与分布式模式都能完成标准示例。
- 新用户可以仅根据文档完成安装、运行、观测和故障恢复。

### 预计 ADR

- `ADR-0015`：v1.0 Runtime Contract 与兼容性承诺。

## 明确暂不优先事项

以下方向保留为候选，但在核心 Runtime 语义稳定前不优先：

- 图形化 Workflow Designer 和完整 Web 控制台。
- Agent Marketplace 和 Prompt Marketplace。
- Agent 自动创建 Agent 或无限自主递归。
- 一次接入大量模型厂商、向量数据库或消息系统。
- Kubernetes Operator 和跨区域调度。
- 为展示效果而引入与可靠执行无关的大量内置工具。

## 路线图维护规则

1. `ROADMAP.md` 按版本从低到高排列，不使用 CHANGELOG 的时间倒序规则。
2. 同一时间最多有一个主版本标记为 `🚧 in-progress`；没有正在开发的版本时允许全部为 completed/planned。
3. 版本开始时，将状态改为 `🚧 in-progress`，并在 `CHANGELOG.md` 创建 partial 变更条目。
4. 版本完成时，将状态改为 `✅ completed`，补充 Change ID、测试结果和真实 commit。
5. 已完成能力同步进入 `CURRENT.md`，实际架构同步进入 `ARCHITECTURE.md`。
6. 影响公共协议、状态、数据、安全、恢复或分布式语义时，必须新增 ADR。
7. 路线调整属于 governance 变更，应写入 `CHANGELOG.md`，但不伪造功能完成记录。
8. 不为路线图填写未经承诺的完成日期；实际完成日期只在 `CHANGELOG.md` 记录。

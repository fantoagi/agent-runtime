# Agent Runtime 当前架构

> 最近更新：2026-08-16
> 关联记录：[E2026-08-16-004](./CHANGELOG.md#e2026-08-16-004)、[E2026-08-16-002](./CHANGELOG.md#e2026-08-16-002)、[E2026-08-16-001](./CHANGELOG.md#e2026-08-16-001)、[E2026-08-15-010](./CHANGELOG.md#e2026-08-15-010)、[E2026-08-15-009](./CHANGELOG.md#e2026-08-15-009)、[E2026-08-15-008](./CHANGELOG.md#e2026-08-15-008)、[E2026-08-15-007](./CHANGELOG.md#e2026-08-15-007)、[E2026-08-15-006](./CHANGELOG.md#e2026-08-15-006)、[E2026-08-15-005](./CHANGELOG.md#e2026-08-15-005)、[E2026-08-15-004](./CHANGELOG.md#e2026-08-15-004)
> 关联决策：[ADR-0025](./adr/0025-local-process-sandbox-capability-policy.md)、[ADR-0023](./adr/0023-operational-observability.md)、[ADR-0022](./adr/0022-runtime-backup-restore.md)、[ADR-0021](./adr/0021-agent-definition-snapshots.md)、[ADR-0020](./adr/0020-run-submission-idempotency-admission.md)、[ADR-0019](./adr/0019-runtime-doctor.md)、[ADR-0018](./adr/0018-crash-recovery-contract.md)、[ADR-0017](./adr/0017-unknown-outcome-confirmation.md)、[ADR-0016](./adr/0016-fastapi-runtime-ownership-sse.md)、[ADR-0015](./adr/0015-runtime-shutdown-sqlite-recovery.md)、[ADR-0014](./adr/0014-provider-async-transport-retry.md)、[ADR-0013](./adr/0013-tool-isolation-unknown-outcome.md)、[ADR-0012](./adr/0012-quality-gates.md)、[ADR-0011](./adr/0011-context-session-memory.md)、[ADR-0010](./adr/0010-parent-child-run-delegation.md)

## 1. 系统目标和边界

当前系统是一个面向开发者的、可持久化 Agent Runtime。它既支持单 Agent 模型/工具循环，也支持由 Parent Run 委派独立 Child Run 的单机多 Agent Workflow，并通过 ContextBuilder、Session 和 Scoped Memory 管理模型输入与跨 Run 信息复用。

当前架构优先保证：接口可替换、执行有界、状态可观察、失败可收敛、人工可介入。它不是分布式调度平台。v0.8.0 已提供受限 `LocalProcessSandbox`，但它不是容器或虚拟机，不能宣称对任意不可信代码形成强隔离。

## 2. 总体架构

```mermaid
flowchart TD
    CLI["CLI / Python SDK"]
    API["FastAPI / SSE Adapter"]
    Lab["Browser Learning Console"]
    LabAdapter["Learning Console Adapter"]
    Workflows["Sequential / Parallel Workflow"]
    Registry["AgentRegistry"]
    Runtime["Runtime Kernel"]
    Context["ContextBuilder"]
    Sessions["Session / session_runs"]
    Memory["MemoryStore / SQLite FTS5"]
    Model["Model Provider"]
    Tools["Tool Registry / Executor"]
    Policy["Capability Policy"]
    Sandbox["SandboxExecutor / LocalProcessSandbox"]
    State["SQLite State Store"]
    Relations["RunRelation Store"]
    Events["Persistent Event Log"]
    Checkpoints["Checkpoint Store"]
    Artifacts["Artifact Store"]
    Observe["Trace / TraceTree / Metrics"]
    Evals["Eval / Workflow Eval"]
    Backup["RuntimeBackupManager"]
    Archive["Verified .agent-backup"]

    CLI --> Runtime
    CLI --> Workflows
    API --> Runtime
    Lab --> LabAdapter
    LabAdapter --> Runtime
    Workflows --> Runtime
    Workflows --> Registry
    Registry --> Runtime
    Runtime --> Context
    Context --> Model
    Runtime --> Sessions
    Runtime --> Memory
    Runtime --> Tools
    Tools --> Policy
    Tools --> Sandbox
    Runtime --> State
    Runtime --> Relations
    Runtime --> Events
    Runtime --> Checkpoints
    Runtime --> Artifacts
    Relations --> Observe
    Events --> Observe
    State --> Observe
    Memory --> Observe
    Events --> LabAdapter
    Observe --> LabAdapter
    Evals --> Runtime
    Evals --> Workflows
    Evals --> Artifacts
    State --> Backup
    Artifacts --> Backup
    Backup --> Archive
```

核心依赖方向由入口指向 Runtime，再由 Runtime 依赖抽象化的 Provider、Tool 和 Store；模型厂商响应和具体工具实现不进入 Runtime Kernel 的领域模型。

## 3. Runtime Kernel

`Runtime` 负责：

- 通过 `AgentRegistry` 注册、发现并校验 `AgentDefinition`，同时将规范化定义保存为内容寻址的不可变快照。
- 创建、启动、等待和恢复 `AgentRun`，并为每个 Run 注入稳定 `trace_id` 和 `agent_definition_checksum`。`submit()` 支持 durable idempotency key。
- 执行有最大步数和最大工具调用数的 Agent 循环，并通过活动 Run 上限与模型请求 Semaphore 实施背压。
- 将模型响应规范化为文本或 `ToolCall`。
- 触发工具执行、人工审批和 Checkpoint。
- 将完成、失败、暂停和取消收敛为明确 Run 状态。
- 将关键动作写入持久化 Event Log。
- 通过 `delegate()` 创建独立 Child Run，并使用稳定 delegation key 实现幂等委派。
- Parent Cancel 递归传播到活动 Child Run。
- 创建 Session、关联 Run，并让 Child Run 继承 Parent Session。
- 在模型调用前检索 Scoped Memory，并通过 ContextBuilder 构造受预算输入。
- 将大 Tool Result 转存到 Artifact Store。

公开入口包括 `run()`、`submit()`、`start()`、`wait()`、`stream()`、`pause()`、`resume()`、`cancel()`、`delegate()`、`begin_workflow()`、`finish_workflow()`、`resolve_approval()`、`resolve_unknown_tool()`、`resume_workflow()`、`create_session()`、`session_runs()`、`remember()`、`search_memory()`、`forget_memory()` 和 `purge_expired_memories()`。

## 3.1 Multi-Agent Orchestration

v0.6 在单 Agent Kernel 外增加 `agent_runtime.orchestration`，但每个 Child 仍通过同一个 Runtime Kernel 执行：

- `AgentRegistry`：保存当前进程可委派的 AgentDefinition；SQLite 保存不可变 AgentDefinition 快照，恢复时按 checksum 绑定历史定义。
- `RunRelation`：保存 Parent、Child、Root、关系类型、delegation key 和 metadata。
- `Runtime.delegate()`：先按 `parent_run_id + delegation_key` 查重，再创建或复用 Child Run。
- `SequentialWorkflow`：按稳定 step key 顺序执行，并把上一步结果传给下一步。
- `ParallelWorkflow`：使用 Semaphore 限制并发，支持 timeout、`all`、`best_effort` 和 `first_success`。
- `WorkflowExecution`：返回 Parent Run 和 Child Run 列表。

```mermaid
flowchart LR
    Parent["Workflow Parent Run"]
    Planner["Planner Child Run"]
    Worker["Worker Child Run"]
    Reviewer["Reviewer Child Run"]

    Parent -->|"RunRelation step:0"| Planner
    Parent -->|"RunRelation step:1"| Worker
    Parent -->|"RunRelation step:2"| Reviewer
    Planner -->|"result"| Worker
    Worker -->|"result"| Reviewer
    Reviewer -->|"aggregate"| Parent
```

Parent 和 Child 都有独立 Run ID、Trace ID、Event 和 Checkpoint。Root Run 的 `root_run_id` / `root_trace_id` 负责把整棵树关联起来；Child 自己的 `trace_id` 不与 Parent 混用。

首次委派在一个 SQLite 事务中写入 Child Run、RunRelation、Parent `delegation.created` 和 Child `run.created`。恢复时，如果稳定 delegation key 已存在，Runtime 复用原 Child；这避免 Parent 恢复后重复执行同一委派。

## 3.2 Context、Session 与 Memory

v0.7 在完整 Checkpoint 历史与 Model Provider 之间增加 Context Build 层：

```mermaid
flowchart LR
    Checkpoint["Checkpoint History"]
    Session["Session / Agent Scope"]
    Search["MemoryStore Search"]
    Builder["ContextBuilder"]
    Budget["Budgeted Model Messages"]
    Provider["Model Provider"]

    Checkpoint --> Builder
    Session --> Search
    Search --> Builder
    Builder --> Budget
    Budget --> Provider
```

`ContextBuilder` 只构造模型输入副本，不修改 Checkpoint。它使用 Provider-neutral 近似 token 估算，并按以下优先级选择消息：System Prompt、未完成 Tool Call 组、最近消息组、预算允许的旧消息。Assistant Tool Call 与对应 Tool Result 作为不可拆分组；被省略的旧消息生成确定性 Summary。

Session 是多个 Run 的显式持久化容器。`sessions` 保存 Session 本身，`session_runs` 保存一对多关系；一个 Run 最多属于一个 Session，Child Run 继承 Parent 的 Session。

`MemoryStore` 协议隔离检索实现。当前 `SQLiteStore` 通过 FTS5 实现关键词搜索，只允许：

- `session` Scope：仅指定 Session 可见。
- `agent` Scope：仅指定 Agent 可见，可跨 Session。

Memory Record 保存 content、Scope、source Run、source Trace、TTL、软删除时间和 metadata。Runtime 不自动保存全部对话，必须由应用显式调用 `remember()`。

每次模型调用的处理顺序为：

```text
Checkpoint messages
+ allowed Session/Agent scopes
→ memory.search.started/completed
→ ContextBuilder.build()
→ context.built / context.compacted
→ Model Provider
```

Context 与 Memory 的完整说明见 [CONTEXT_MEMORY.md](./CONTEXT_MEMORY.md)。

## 4. Run 状态机

```mermaid
stateDiagram-v2
    [*] --> created
    created --> running
    created --> cancelled
    running --> waiting_for_approval
    running --> paused
    running --> completed
    running --> failed
    running --> cancelled
    waiting_for_approval --> running
    waiting_for_approval --> failed
    waiting_for_approval --> cancelled
    paused --> running
    paused --> cancelled
```

非法状态迁移由领域模型拒绝。`completed`、`failed` 和 `cancelled` 是终态。

## 5. Model Provider 层

`ModelProvider` 协议接收统一的 `Message`、`ToolDefinition` 和 `ModelConfig`，返回 `ModelResponse`。Runtime 只理解：

- 文本内容。
- 结构化工具调用。
- finish reason。
- token usage。

当前实现：

- `MockProvider`：确定性测试和本地 Demo。
- `OpenAICompatibleProvider`：复用受管理的 `httpx.AsyncClient` 调用 Chat Completions 兼容端点。

Provider 调用由 Runtime 统一处理超时和指数退避重试。实现 `StreamingModelProvider` 的 Provider 可以输出 `ModelTokenDelta`；Runtime 在不改变既有 Run / Step / ToolExecution / Checkpoint 语义的前提下，将增量写入 `model.delta` 事件，并在模型步骤结束时合并为完整 `ModelResponse`。不支持 `stream()` 的 Provider 自动回退到 `complete()`。

## 6. Tool Executor

工具由 `ToolDefinition`、handler 和超时组成，通过 `ToolRegistry` 注册。执行前进行基础 JSON-schema 风格校验，执行结果统一转换为 `ToolResult`。

当前支持：

- 同步和异步 Python handler。
- required、type、enum 和 additionalProperties 校验。
- 单工具超时。
- 工具异常标准化并回传给模型。
- `requires_approval` 人工审批标记。
- `side_effecting` 副作用标记。
- `capabilities` 与 `sandbox_only` 安全声明。
- `CapabilityPolicy` 合并 allow、deny、require_approval 与 sandbox_only。
- `CancellationToken` 协作式取消和稳定的 `idempotency_key`。

## 6.1 SandboxExecutor 与 Capability Policy

v0.8.0 在 Tool Handler 之前增加显式授权层。默认 capability 规则为 `file.read=allow`、`file.write=require_approval`、`process.exec=sandbox_only`、`network.access=deny` 和 `secret.read=deny`。deny 优先于其他动作；要求 Sandbox 的 Tool 必须以 `sandboxed=True` 注册。

`LocalProcessSandbox` 通过 `asyncio.create_subprocess_exec()` 运行 argv，不使用 shell；显式限制可执行文件、Workspace cwd、环境变量、timeout、输出字节和并发。Run Cancel、输出超限、超时与 Runtime shutdown 会终止进程树。它当前不提供网络、CPU、内存、PID 或系统调用强隔离。

```mermaid
flowchart LR
    Call["Model ToolCall"] --> Policy["CapabilityPolicy.evaluate"]
    Policy -->|deny| Rejected["拒绝注册或执行"]
    Policy -->|require approval| Approval["Durable Approval"]
    Policy -->|sandbox only| Sandbox["SandboxExecutor"]
    Approval --> Sandbox
    Sandbox --> Process["Allowed argv process"]
    Process --> Result["ToolResult / ToolExecution"]
```

Capability 决策通过 `tool.policy.evaluated` 进入 durable Event Log；Sandbox active process 与容量通过 `Runtime.sandbox_snapshot()`、CLI `observe sandbox` 和 HTTP `/observability/sandbox` 暴露，但不占用 Run sequence。完整边界见 [SANDBOX.md](./SANDBOX.md)。

> 最近更新：2026-08-16
> 关联记录：[E2026-08-16-004](./CHANGELOG.md#e2026-08-16-004)
> 关联决策：[ADR-0025](./adr/0025-local-process-sandbox-capability-policy.md)

## 7. SQLite、Step、ToolExecution、Checkpoint 和 Artifact Store

`SQLiteStore` 当前保存：

```text
schema_migrations
runs
events
checkpoints
approvals
steps
tool_executions
run_relations
sessions
session_runs
memory_records
memory_fts
```

数据库启用 WAL 和 foreign keys，并通过编号 migration 初始化和升级 schema。当前 schema version 为 `8`；`run_relations` 对 `child_run_id` 和 `parent_run_id + delegation_key` 建立唯一约束，`session_runs` 限制一个 Run 最多属于一个 Session，`memory_fts` 为活动 Memory 提供 FTS5 索引。`Step` 表示一次模型决策，`ToolExecution` 表示该决策内有序的工具调用。

ToolExecution 保存参数、状态、结果、错误、审批关系、side-effect 标记和 idempotency key。工具完成状态、Run 计数、Checkpoint 和对应 Event 可以在同一 SQLite 事务中提交，降低半状态风险。

Checkpoint 保存恢复所需的消息历史、模型步骤和工具调用计数。`ArtifactStore` 将大文本产物写入按 Run 隔离的目录；当 Tool Result 超过配置阈值时，Runtime 自动保存完整 Artifact，并让 ToolExecution 与 Checkpoint 只保留路径、大小和预览。


## 7.1 在线备份与离线恢复

`RuntimeBackupManager` 位于 Runtime Kernel 外部，操作状态文件而不参与 Run 状态机：

```mermaid
flowchart LR
    LiveDB["Live SQLite WAL"]
    Snapshot["SQLite Online Backup Snapshot"]
    Artifacts["Artifact Store"]
    Manifest["Manifest + SHA-256"]
    Archive[".agent-backup"]
    Verify["Verify"]
    Restore["Offline Restore"]
    Rollback["pre-restore rollback copy"]

    LiveDB --> Snapshot
    Snapshot --> Manifest
    Artifacts --> Manifest
    Manifest --> Archive
    Archive --> Verify
    Verify --> Restore
    Restore --> Rollback
```

创建备份时先使用 SQLite Online Backup API 获得事务一致数据库，再复制 Artifact，记录 format version、schema version、migration checksum、关键表计数、文件大小和 SHA-256。生成的临时归档必须通过完整自校验后，才能原子替换最终备份文件。

恢复不允许在活动 Runtime 上执行。恢复器先校验 ZIP 路径、重复 Entry、hash、`quick_check`、`foreign_key_check` 和 migration，再执行 WAL checkpoint 与排他锁检查。目标数据库和 Artifact 先改名为 `pre-restore-*`，安装失败则自动回滚；默认保留恢复前副本。

当前历史记录保存绝对 Artifact 路径，因此恢复目标必须与 Manifest 中的原路径一致。该边界避免在 JSON、Event 和消息文本中进行不安全的全库字符串替换。
## 8. Event Log

每个 Run 的事件使用从 1 开始的单调递增 `sequence`。当前事件覆盖：

```text
run.created / started / recovered / paused / completed / failed / cancelled
model.requested / stream.started / delta / stream.completed / stream.failed / completed
tool.policy.evaluated / requested / started / completed / failed / rejected / cancelled / outcome_unknown / unknown_resolved
checkpoint.created
approval.requested / resolved
step.completed
workflow.started / recovered / resumed / completed / failed / cancelled
delegation.created / completed / failed / cancelled
memory.search.started / completed
context.built / compacted
tool.result.artifactized
```

`Runtime.stream()` 轮询 SQLite 中的新事件并按 sequence 输出。Provider 层的 `ModelTokenDelta` 是短生命周期增量；Runtime 将它映射为可审计的 `model.delta` 事件。最终 assistant message、Tool Call 和工具结果仍通过 Step / Checkpoint 持久化。Context Build、Memory Search 和 Tool Result Artifactization 也进入同一 Event Log。该接口为 SSE、WebSocket 或消息队列适配保留稳定消费边界。

## 9. API、SDK 与 CLI

Python SDK 暴露 Runtime 和领域对象，并提供本地 Demo runtime 构造函数。

CLI 当前支持：

```text
agent-runtime lab
agent-runtime demo
agent-runtime runs list
agent-runtime runs get
agent-runtime runs events
agent-runtime runs pause
agent-runtime runs resume
agent-runtime runs cancel
agent-runtime approve
agent-runtime resolve-unknown
agent-runtime observe metrics
agent-runtime observe trace <run-id>
agent-runtime eval demo
agent-runtime memory demo
```

核心 Runtime 不依赖 CLI 或 HTTP 框架。

## 9.1 FastAPI Run API 与 SSE

FastAPI 位于 Application / Adapter Layer，只调用 Runtime、SQLiteStore 和 Runtime.stream()，不直接实现模型循环、工具执行或 SQLite 连接操作。

- `POST /runs` 创建并异步启动 Run，返回 `202 Accepted`，并可接收 `session_id`。
- `GET /runs/{run_id}` 查询持久化 Run 状态。
- `GET /runs/{run_id}/events` 读取按 sequence 排序的历史事件。
- `GET /runs/{run_id}/events/stream?after_sequence=N` 通过 SSE 轮询 `Runtime.stream()`，使用 Event sequence 作为 SSE id，支持断点续传。
- `POST /runs/{run_id}/pause|resume|cancel` 复用 Runtime 生命周期控制。
- `GET /runs/{run_id}/approvals/pending` 与 `POST /approvals/{approval_id}/resolve` 完成人工审批。
- `GET /agents` 查询 AgentRegistry。
- `POST /runs/{parent_run_id}/delegations` 通过正式 Runtime 路径创建或复用 Child Run。
- `GET /runs/{run_id}/relations` 查询 Parent 和直接 Child 关系。
- `GET /runs/{run_id}/trace/tree` 从 Parent 或任意 Child 查询完整 Trace Tree。
- `/sessions` 与 `/sessions/{session_id}/runs` 创建和查询 Session。
- `/memories` 与 `/memories/search` 提供显式写入、检索、删除和过期清理。

v0.4 起，SSE 仍然只有一个事件流协议；客户端根据 `type` 区分 `model.delta`、`tool.completed` 等事件。`model.delta` 是持久化 Runtime Event，因此支持 `after_sequence` 断点续传；Provider 不支持 streaming 时，Runtime 会回退为一次性的完整响应事件。

## 9.2 Observability、Trace 与 Metrics

`ObservabilityService` 不侵入 Runtime 状态机，而是从 `SQLiteStore` 中已经持久化的 Run 和 Runtime Event 派生观测结果：

- `RunTrace`：包含一个 Run root span，以及 Model、Tool、Approval 子 span。
- `TraceTree`：使用 `RunRelation` 将多个独立 RunTrace 组合为 Parent/Child 树。
- `MetricsSnapshot`：包含 Run 状态分布、Root/Child/Workflow/Delegation 计数、Session/Memory 数量、Memory Search、Context Compaction、事件计数、模型/工具/审批次数、token usage、Run/Model/Tool 平均与 p95 延迟，以及 Provider/Tool/UNKNOWN 失败分类。
- Prometheus 文本：通过固定 `agent_runtime_*` 指标名称导出。

每个新 Run 的 metadata 自动包含 `trace_id`。Trace Span 使用事件 sequence 和 timestamp 构造，因此可以回溯到原始事件，但当前没有修改 SQLite Event schema，也没有依赖 OpenTelemetry SDK。

FastAPI 暴露：

- `GET /observability/metrics`。
- `GET /observability/metrics/prometheus`。
- `GET /runs/{run_id}/trace`。
- `GET /runs/{run_id}/trace/tree`。

## 9.3 Eval Runner

`EvalRunner` 使用与生产执行相同的 Runtime 路径逐个运行 `EvalCase`，不会绕过 Provider、Tool、Checkpoint 或 Event Log。每个 Eval Run 的 metadata 保存 `eval_report_id`、`eval_suite` 和 `eval_case`，因此评估结果可以反查完整 Trace 和事件。

`MemoryEvalRunner` 复用相同 Eval Report 和 Artifact 机制，可验证关键词查询命中内容与 `expected_memory_count`。

当前内置评估器：

- `ExpectedStatusEvaluator`：检查 Run 最终状态。
- `ExactMatchEvaluator`：检查最终文本精确匹配。
- `ContainsEvaluator`：检查最终文本包含指定片段。

`EvalReport` 汇总用例级断言、通过率、Run ID、Trace ID 和耗时，并写入 Artifact Store。`WorkflowEvalRunner` 通过真实 Workflow 路径运行用例，并可断言 Parent 状态、输出和 Child Run 数量。当前顺序执行以保证确定性，尚未实现并发评估、统计显著性或 LLM-as-a-Judge。

## 9.4 Learning Console

Learning Console 是 `agent_runtime.lab` 中的教学 Adapter，通过 `create_app(..., enable_learning_console=True)` 挂载到 `/lab`。默认 `create_demo_app()` 和 `agent-runtime lab` 会启用该 Adapter。

组成：

- `ScenarioRegistry`：保存 9 个场景的默认输入、预期事件、学习点、人工动作提示和数量验收条件。
- 单 Run 场景 Runtime：纯文本、Tool Calling、Token Streaming 和 Human Approval。
- v0.6 Workflow 场景：真实运行 `SequentialWorkflow` 和 `ParallelWorkflow`，生成 Parent、Child、RunRelation 和 TraceTree。
- v0.7 Context/Memory 场景：真实创建 Session/Memory、触发检索与 Context Compaction，并将大 Tool Result 写入 Artifact Store。
- `LearningConsole`：启动场景，根据 Root Run metadata 定位 Runtime，并聚合 Root/Child Snapshot。
- Lab FastAPI Routes：提供场景目录、启动、Snapshot 和审批接口。
- Static UI：根据 Snapshot 动态生成 Workflow Parent、每个 Child Agent 和实际出现的领域泳道，并提供 Event、State、Messages、Execution、Trace、Context、Memory、Artifact、SQLite、Acceptance 检查器。

所有场景 Runtime 共享现有 SQLiteStore，但不共享 Provider 行为。Run、Relation、Event、Step、ToolExecution、Approval、Checkpoint、Session、Memory 和 Artifact 仍是唯一执行事实；教学解释、`timeline_sequence` 和状态投影只用于展示，不回写领域状态。

Snapshot 先通过 `root_run_id` 和 `relations_for_root()` 找到完整 Run Tree，再逐 Run 读取 Event、Checkpoint、Step 和 ToolExecution。每个 Run 的事件先独立执行状态投影，再按 timestamp、run_id、local sequence 合并为教学 Timeline。前端显示的 `timeline_sequence` 是跨 Run 展示序号，SQLite 中每个 Run 的 `RuntimeEvent.sequence` 仍保持独立单调递增。

Root 事件实时通知复用 `/runs/{run_id}/events/stream`。因为 Child Run 保留独立 Event Stream，页面在 Parent 运行期间每 450ms 刷新聚合 Snapshot，从而动态显示 Child Model/Tool/Checkpoint；这属于本地教学投影，不新增 Runtime 消息总线。

泳道映射：Workflow/Root 生命周期与 Delegation 进入 Workflow Parent；每个 Child Run 根据 `workflow_step` 或 `workflow_branch` 排序并获得独立 Agent 泳道，其自身的 Run、Context、Model、Tool 和 Checkpoint 事件全部留在该泳道；非 Child 事件再按 Session/Memory、Context、Model、Tool、Approval 或 State 领域映射。只渲染当前 Snapshot 实际使用的领域泳道。

连线不再按跨 Run 全局相邻事件直接串接：同一 Run 按 `local_sequence` 连接内部执行线；`delegation.created → Child run.created` 形成委派分叉；`Child run.completed/failed/cancelled → delegation.completed/failed/cancelled` 形成结果汇聚。这样并行分支不会因为 `timeline_sequence` 相邻而被误表示为业务依赖。Trace Inspector 直接递归渲染 `TraceTree.root`，Context/Memory/Artifact Inspector 读取 Snapshot 的对应持久化事实。

事件“回放”只移动浏览器展示游标。它不会暂停 Runtime asyncio Task，也不会改变 Run 状态机、Event sequence 或恢复语义。该边界保证 Learning Console 可以随功能演进扩展，而 Runtime Kernel 不依赖 UI。

> 最近更新：2026-08-16
> 关联记录：[E2026-08-15-003](./CHANGELOG.md#e2026-08-15-003)
> 关联决策：[ADR-0011](./adr/0011-context-session-memory.md)、[ADR-0010](./adr/0010-parent-child-run-delegation.md)、[ADR-0009](./adr/0009-learning-console.md)
## 9.5 Operational Observability 与结构化日志

v0.7.11 将已有 Trace、Metrics、Runtime Doctor 和当前进程采样组合为 `OperationalSnapshot`，同时保持 durable facts 与 transient signals 分层：

- `Runtime.lifecycle_snapshot()`：状态、启动时间、uptime、活动任务和配置上限。
- `ToolRegistry.capacity_snapshot()`：同步 Tool worker、pending 上限与当前未完成 future。
- `ObservabilityService.diagnostics()`：合并 Runtime、Tool、PID、线程、asyncio Task、SQLite health、Doctor、Metrics 和最近失败。
- `StructuredLogFormatter`：输出 JSON Lines，对常见凭据字段脱敏，并限制字符串长度与嵌套深度。
- CLI `observe diagnostics` 与 HTTP `/observability/diagnostics`：提供相同诊断模型。

```mermaid
flowchart LR
    Facts["SQLite Durable Facts"] --> Metrics["Trace / Metrics / Recent Failures"]
    Runtime["Runtime Lifecycle + Capacity"] --> Snapshot["OperationalSnapshot"]
    Process["PID / Threads / Asyncio Tasks"] --> Snapshot
    Doctor["Runtime Doctor"] --> Snapshot
    Metrics --> Snapshot
    Snapshot --> CLI["CLI observe diagnostics"]
    Snapshot --> API["GET /observability/diagnostics"]
    Runtime --> Logs["Structured JSON Logs"]
```

Provider attempt 失败和重试决定会改变一次 Run 的执行解释，因此 `model.attempt.failed` 与 `model.retry.scheduled` 进入 durable Event Log。Runtime 启停、PID、线程和 Task 数属于进程级信号，不占用 Run Event sequence。结构化日志默认关闭，不修改 root logger；CLI 通过 `--json-logs` 显式启用，Python 应用通过 `configure_structured_logging()` 配置。

> 最近更新：2026-08-16
> 关联记录：[E2026-08-16-004](./CHANGELOG.md#e2026-08-16-004)、[E2026-08-16-002](./CHANGELOG.md#e2026-08-16-002)
> 关联决策：[ADR-0025](./adr/0025-local-process-sandbox-capability-policy.md)、[ADR-0023](./adr/0023-operational-observability.md)、[ADR-0008](./adr/0008-observability-evals.md)
## 9.6 Incident Diagnostics 与 Support Bundle

v0.7.12 在 `OperationalSnapshot` 之上增加独立的 `IncidentDiagnosticsService`。该层只读取 Runtime 与 SQLite durable facts，生成可分享的安全摘要和 ZIP，不进入执行主循环，也不写入 Runtime Event sequence。

```mermaid
flowchart LR
    Runtime["Runtime Lifecycle"] --> Incident["IncidentDiagnosticsService"]
    Store["SQLite Durable Facts"] --> Incident
    Observability["Diagnostics / Doctor / Metrics"] --> Incident
    Incident --> Analysis["Deterministic FailureDiagnosis"]
    Incident --> Safe["Allowlisted Run / Event Summaries"]
    Analysis --> Zip["Incident Bundle v1"]
    Safe --> Zip
    Zip --> CLI["CLI atomic file export"]
    Zip --> API["HTTP application/zip"]
    Zip --> Console["Learning Console download"]
```

根因摘要只使用 durable failure events、HTTP status 和稳定错误类型，不调用模型。Provider 401/403、429、5xx、Timeout/Transport、Tool Failure、Tool UNKNOWN 与 Runtime Failure 使用固定类别和建议；Run 最终完成时标记 recovered。

默认支持包使用允许列表：Run 不暴露 input/result，Event 不暴露模型文本、Tool 参数/结果和原始 error；Memory、Checkpoint、Artifact、SQLite 和本机数据库/Artifact 路径不进入 ZIP。manifest 为数据条目记录 size 与 SHA-256，但诊断包不具备恢复语义。

CLI 文件导出先在内存构建 ZIP，再写入同目录临时文件并执行 flush/fsync 和 `os.replace`；存在目标默认失败。HTTP 直接流式返回内存 ZIP，并设置 `Cache-Control: no-store`。

> 最近更新：2026-08-16
> 关联记录：[E2026-08-16-003](./CHANGELOG.md#e2026-08-16-003)
> 关联决策：[ADR-0024](./adr/0024-incident-diagnostic-bundle.md)、[ADR-0023](./adr/0023-operational-observability.md)

## 9.7 Reliability、生命周期与发布门禁

- `shutdown(timeout_seconds=30, cancel_running=False)` 先停止接收新工作，再排空任务；超时后协作取消，不能确认的副作用 Tool 保持 `UNKNOWN`。
- `async with Runtime(...)` 自动执行相同关闭流程，重复关闭幂等。
- 同步 Tool 进入 Runtime 独享有界线程池；Provider 复用 `httpx.AsyncClient` 并在关闭时释放连接池。
- SQLite 使用 WAL、`synchronous=FULL`、`busy_timeout`、`quick_check` 和事务内 Event sequence；migration 带 checksum，只向前升级。
- Workflow 创建时保存规范化定义快照，恢复时由 Runtime 重建 Sequential/Parallel Workflow 并复用 delegation key；FastAPI 通过 `shutdown_runtime` 明确所有权；SSE heartbeat 不写 Event Log。

```mermaid
flowchart TD
    Accept["Accepting"] --> Closing["Closing: reject new work"]
    Closing --> Drain["Bounded task drain"]
    Drain -->|timeout| Cancel["Cooperative cancel"]
    Drain --> Close["Close Provider / Tool Pool / Store"]
    Cancel --> Persist["Persist PAUSED / UNKNOWN"]
    Persist --> Close
    Close --> Closed["Closed (idempotent)"]
```

PR 执行静态检查、189 项测试、覆盖率、20 并发和 Wheel smoke；Nightly 执行 100 并发、故障测试重复、30 分钟 soak 和性能检查。
## 9.7 Runtime Doctor 与 Crash Recovery Matrix

`RuntimeDoctor` 是只读诊断层，不修改 SQLite。它检查 quick_check、schema、foreign_keys、非终态 Run、UNKNOWN/Running ToolExecution、Pending Approval、Event sequence、孤儿记录和 Workflow snapshot。CLI 使用 `agent-runtime doctor`，HTTP 使用 `GET /doctor`。

Crash Matrix 通过独立 Python 子进程制造模型请求中断、副作用 Tool 中断、Approval 等待中断和 Workflow 部分完成中断，再由新 Runtime 打开同一个 SQLite 文件进行恢复。副作用场景使用外部计数器证明恢复前后只执行一次。

```mermaid
flowchart LR
    Worker["Child Process"] -->|"durable barrier"| SQLite["SQLite + external marker"]
    Controller["Crash Matrix Controller"] -->|"process.kill()"| Worker
    Controller --> Restart["New Runtime Process"]
    Restart --> Reconcile["Reconcile RUNNING / UNKNOWN"]
    Reconcile --> Confirm["Human confirmation if UNKNOWN"]
    Confirm --> Resume["Explicit resume()"]
    Resume --> Verify["Verify terminal state and no duplicate side effect"]
```

## 9.8 Backup CLI 与恢复门禁

CLI 提供：

- `agent-runtime backup create`：运行中创建数据库和 Artifact 归档。
- `agent-runtime backup verify`：离线校验归档，不修改 Runtime 状态。
- `agent-runtime backup restore --force`：所有 Runtime 停止后恢复，并默认保留回滚副本。

`scripts/run_backup_recovery.py` 创建恢复点后继续写入新 Run，再恢复旧备份，验证旧 Run/Artifact 存在而备份后的 Run 不存在。该脚本进入 PR 与 Nightly，Wheel smoke 也从安装产物执行 create、verify 和 restore。
## 10. 安全边界

当前默认安全策略：

- 工具必须显式注册，模型不能构造任意可执行函数。
- 文件路径先 `resolve()`，然后验证仍位于配置的 workspace 内。
- 写文件工具默认要求人工审批。
- 不提供任意 Shell、进程管理或自动依赖安装。
- 工具参数必须通过 schema 校验。
- 模型 API Key 从构造参数或环境变量读取，不写入事件和数据库。

当前是进程级受控执行，不是针对恶意代码的强隔离沙箱。

## 11. 错误处理与恢复语义

- 模型错误：在配置次数内指数退避重试，耗尽后将 Run 标记为 failed。
- 工具错误：转换为工具消息并返回模型，使 Agent 可以重新规划。
- 工具超时：转换为 `ToolExecutionError`。
- Run 超时：执行循环终止并进入 failed。
- 暂停：保存最新 Checkpoint 后返回 paused。
- 审批：保存工具调用和 Checkpoint，批准后执行，拒绝后将拒绝原因作为工具结果返回模型。
- 恢复：加载最新 Checkpoint 和未完成 Step；已完成的 ToolExecution 复用持久化结果，不重复执行。Workflow Parent 可由 `resume()` 从持久化 snapshot 重建定义，稳定 delegation key 复用已创建 Child；应用必须重新注册被 snapshot 引用的 AgentDefinition。
- 未知副作用：进程在副作用工具运行中重启时标记为 `unknown`，禁止直接 retry；人工只能确认成功或失败，并记录 reason、resolved_by、resolved_at，随后显式 `resume()`。
- 取消：取消活动 asyncio Task，并通过 ToolContext 向 handler 发出协作式取消信号；Workflow Parent 会递归取消活动 Child。
- Workflow pause：仍不支持在任意 Python 控制流位置暂停，但崩溃后的 RUNNING Workflow Parent 可以通过规范化 snapshot 和幂等 delegation key 显式恢复。

## 12. 当前扩展点

- 新增 `ModelProvider` 实现。
- 注册新的受控工具。
- 将 SQLite repository 替换为其他持久化实现。
- 增加新的 `MemoryStore` 实现，例如 Embedding 或向量数据库检索，同时保留 Scope 和生命周期契约。
- 在事件消费边界上增加 WebSocket、消息队列或 OpenTelemetry Exporter。
- 增加独立 `SandboxExecutor`，而不是让 Runtime 直接执行 Shell。
- 将当前单机 Workflow 调度替换为可插拔 Queue / Worker Adapter，同时保持 RunRelation 和 delegation key 契约。

## 13. 当前非目标

- 分布式 Worker 和高可用调度。
- 多租户、RBAC 和配额。
- 向量数据库、自动 Memory 提取与 global Memory Scope。
- 任意代码或 Shell 执行。
- 面向生产的完整 Web 管理控制台（当前仅有本地 Learning Console）。
- 外部 OpenTelemetry Collector、时序数据库和分布式 Trace Backend。
- LLM-as-a-Judge、数据集版本管理和统计显著性分析。

# Agent Runtime

A small, durable single-agent and multi-agent runtime with model-provider abstraction, structured tool
execution, SQLite migrations, durable steps, idempotent recovery, event streaming, token streaming,
checkpoints, approval gates, cooperative cancellation, persistent Parent/Child delegation, sequential and parallel workflows, budgeted context building, sessions, scoped long-term memory, trace trees, metrics, evals, a CLI, and a visual Learning Console.

## Quick start

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[api]
python -m pip install pytest pytest-asyncio
agent-runtime lab
# Or run the CLI-only demo:
agent-runtime demo "19 * 23"
```

The demo uses a deterministic local provider. Set `OPENAI_API_KEY` and select the
OpenAI-compatible provider in application code when connecting to a real model.

## v0.7.10 Operational Backup & Recovery

当前版本提供 SQLite Online Backup、Artifact 归档、Manifest/SHA-256 校验、离线恢复与自动回滚副本：

```powershell
agent-runtime backup create --output runtime.agent-backup
agent-runtime backup verify runtime.agent-backup
# 停止所有 Runtime 后：
agent-runtime backup restore runtime.agent-backup --force
python scripts/run_backup_recovery.py
```

完整操作步骤见 [运行与灾难恢复手册](./docs/OPERATIONS.md)。
## v0.7.9 AgentDefinition Snapshot Recovery

当前版本会持久化 System Prompt、ToolDefinition、ModelConfig 和执行上限的不可变快照。普通 Run 与串行/并行 Workflow 在进程重启后无需重新注册 AgentDefinition，并始终恢复创建时绑定的确切定义；Tool Handler 与 Provider 实现仍需由进程提供。

## v0.7.8 Durable Submission & Admission Control

当前版本支持 `Idempotency-Key` 持久化去重、同 Key 冲突检测、顶层 Run 429 背压和模型请求并发限制；这些状态可在 `/health` 与 Learning Console SQLite Inspector 中直接查看。

## v0.7.7 Crash Recovery & Operational Closure

当前版本继续暂停新增 Agent 智能能力，重点补齐真实进程崩溃恢复、UNKNOWN 人工确认审计、Workflow snapshot 自动恢复、Runtime Doctor，以及 Windows/Linux Crash Matrix。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json
python scripts/check_coverage.py coverage.json
python scripts/run_reliability.py --stress-runs 20 --concurrency 20
python scripts/run_crash_recovery.py
agent-runtime doctor --json
```
## Learning Console

如果你希望直观学习一次 Agent Run 的完整处理流程，启动本地可视化控制台：

```powershell
agent-runtime lab
```

浏览器会打开 `http://127.0.0.1:8000/lab`。v0.7.7 内置 9 个确定性场景，覆盖单 Run、v0.6 多 Agent 和 v0.7 Context/Memory，并提供：

- 多 Agent 场景按 Workflow Parent 与每个 Child Agent 动态拆分独立泳道；单 Run 场景按实际出现的 Context / Model / Tool / Approval / State 领域展示。
- 连线区分 Run 内部执行、Parent 委派和 Child 汇聚，避免把并行分支误画成串行依赖。
- 从头回放、逐事件前进和自动播放。
- Event 状态 diff、原因、下一步和源码方法映射。
- Parent/Child Trace Tree，以及 Context、Memory、Artifact、Messages、Execution、Metrics 和 SQLite 检查器。
- 浏览器内审批与场景自动验收。

详细学习路径见 [Learning Console 使用指南](./docs/LEARNING.md)。

## Multi-Agent Workflow

运行确定性的 Planner → Worker → Reviewer 教学 Workflow：

```powershell
agent-runtime workflow demo "为 Agent Runtime 设计一个可靠的恢复机制"
```

v0.6 为每个 Parent 和 Child 分配独立 Run ID、Trace ID、Event 与 Checkpoint，并通过 SQLite `RunRelation` 和稳定 `delegation_key` 支持关系查询、取消传播与幂等恢复。Python API 提供：

- `AgentRegistry` 和 `Runtime.delegate()`。
- `SequentialWorkflow` 和 `ParallelWorkflow`。
- `all`、`best_effort`、`first_success` 汇聚策略。
- `ObservabilityService.trace_tree()` 和多 Agent Metrics。
- `WorkflowEvalRunner`。

详细示例见 [Multi-Agent 使用指南](./docs/MULTI_AGENT.md)。

## Context、Session 与 Memory

运行确定性的 Memory Demo：

```powershell
agent-runtime memory demo "Which Python language do I prefer?" --remember "The user prefers Python for examples."
```

v0.7 在模型调用前通过 `ContextBuilder` 构造受 token budget 约束的输入，并支持：

- System Prompt、未完成 Tool Call 组和最近消息优先保留。
- 旧消息确定性 Summary 与 `context.built` / `context.compacted` 事件。
- 多个 Run 归入持久化 Session，Child Run 继承 Session。
- `session` / `agent` 两种 Memory Scope、SQLite FTS5、TTL 和软删除。
- 大 Tool Result 自动写入 Artifact Store。
- Session/Memory API、Metrics 和 `MemoryEvalRunner`。

详细示例见 [Context、Session 与 Memory 指南](./docs/CONTEXT_MEMORY.md)。

## FastAPI / SSE

启动本地 Demo API：

```powershell
uvicorn agent_runtime.api.app:app --reload
```

然后可以：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Method Post http://127.0.0.1:8000/runs -ContentType 'application/json' -Body '{"input":"19 * 23"}'
```

SSE 事件接口为 `GET /runs/{run_id}/events/stream?after_sequence=0`。v0.4 起，模型 token 增量以 `model.delta` Runtime Event 进入同一条 SSE 流，客户端无需维护第二套 token 协议。


## Observability / Evals

查看从 SQLite 持久化历史派生的指标和单次 Run Trace：

```powershell
agent-runtime observe metrics
agent-runtime observe trace <run-id>
```

运行内置确定性评估套件：

```powershell
agent-runtime eval demo
```

HTTP 入口：

```text
GET /observability/metrics
GET /observability/metrics/prometheus
GET /runs/{run_id}/trace
```

Eval Report 会写入 `.agent-runtime/artifacts/<eval-id>/eval-report.json`，每个用例保留对应 Run ID 和 Trace ID。

## Development checks

```powershell
python scripts/check_docs.py
pytest
```

If a change is based on an existing Git commit, also run the synchronization gate:

```powershell
python scripts/check_docs.py --base-ref <base-commit>
```

## Documentation and evolution tracking

The project treats documentation as part of the implementation contract:

- [Current system state](./docs/CURRENT.md): what is implemented, experimental, planned, or unsupported.
- [Current architecture](./docs/ARCHITECTURE.md): how the runtime is structured today.
- [Roadmap](./docs/ROADMAP.md): planned versions, scope boundaries, dependencies, and acceptance direction.
- [Learning Console guide](./docs/LEARNING.md): visual scenarios, event replay, inspectors, and source-code mapping.
- [Multi-Agent guide](./docs/MULTI_AGENT.md): delegation, workflows, trace trees, cancellation, recovery, and evals.
- [Context and memory guide](./docs/CONTEXT_MEMORY.md): context budgets, sessions, scoped memory, FTS5, lifecycle, and APIs.
- [Evolution log](./docs/CHANGELOG.md): feature and architecture changes in reverse completion-time order.
- [Architecture Decision Records](./docs/adr/README.md): why important public, data, reliability, or security decisions were made.
- [Operations and disaster recovery](./docs/OPERATIONS.md): backup, verification, offline restore, rollback, and recovery drills.
- [Documentation workflow](./docs/README.md): Change IDs, templates, update rules, and quality gates.

Every independently verifiable feature, fix, or architecture change should have a
Change ID in `docs/CHANGELOG.md`. Core runtime changes must update the evolution log;
changes to public interfaces, state/event schemas, recovery semantics, or security
boundaries must also add or update an ADR.

## Design

- **Runtime kernel**: bounded agent loop with cancellation, retry-safe checkpoints, approvals, and durable delegation.
- **Providers**: normalized text/tool-call responses, optional token streaming, a deterministic Mock, and an OpenAI-compatible implementation.
- **Tools**: JSON-schema-inspired argument validation, timeout handling, and workspace confinement.
- **Storage**: SQLite schema v8 for runs, relations, sessions, memories, AgentDefinition snapshots, event log, checkpoints, FTS5, file-backed artifacts, and verified backup archives.
- **Orchestration**: AgentRegistry, RunRelation, sequential/parallel workflows, aggregation, timeout, cancellation propagation, and idempotent recovery.
- **Context and memory**: ContextBuilder, Session, scoped MemoryStore search, lifecycle, provenance, and large Tool Result artifactization.
- **Observability**: per-Run trace, Parent/Child trace tree, and metrics derived from persisted runs/events/relations, with JSON and Prometheus outputs.
- **Evals**: deterministic suites that execute through the real Runtime path and persist reports.
- **Interfaces**: Python SDK, CLI, FastAPI/SSE, and a local Learning Console; the core has no UI dependency.

# Agent Runtime Evolution Log

> 规则：按完成时间倒序排列。每条记录必须关联代码范围、测试和 Git commit；提交前 commit 可暂记为 `pending`，合并前必须补全。

---

<a id="e2026-08-16-002"></a>
## E2026-08-16-002：v0.7.11 结构化日志与综合运行诊断

- **完成时间**：2026-08-16
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/telemetry.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `tests/test_observability.py`
  - `tests/test_telemetry.py`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `docs/OBSERVABILITY.md`
- **关联 commit**：`pending`
- **关联 ADR**：[ADR-0023](./adr/0023-operational-observability.md)

### 变更摘要

在不增加 Agent 能力和外部监控依赖的前提下，将 Runtime 生命周期、容量、进程资源、SQLite、Doctor、持久化 Metrics 与最近失败汇总为统一诊断快照，并增加显式启用、自动脱敏和有界输出的 JSON Lines 结构化日志。

### 系统架构

保持 SQLite durable facts 与进程级 transient signals 分层：Run、Event、Checkpoint、ToolExecution 和 Approval 继续承担恢复与审计；Runtime 启停、进程 PID、线程数和 asyncio Task 数通过日志和 `OperationalSnapshot` 暴露。Provider attempt 失败与重试决策会影响 Run 解释，因此新增 durable `model.attempt.failed` 和 `model.retry.scheduled` Event。

### 实现方式

`ObservabilityService.diagnostics()` 聚合 `Runtime.lifecycle_snapshot()`、Tool executor 容量、进程采样、SQLite health、Runtime Doctor、扩展 Metrics 与最近失败。Metrics 增加 Model/Tool p95、Provider attempt failure/retry、Tool failure、UNKNOWN 和失败分类。`StructuredLogFormatter` 输出单行 JSON，对常见凭据字段脱敏，并限制字符串长度和嵌套深度；CLI 通过 `--json-logs` 显式启用。

### 当前功能

支持 CLI `observe diagnostics`、HTTP `GET /observability/diagnostics`、Python `configure_structured_logging()`、Prometheus 新增失败和 p95 指标，以及 Learning Console Operational Diagnostics 面板。API 和 `/health` 使用统一 `__version__`，避免适配层版本漂移。

### 已知限制

当前进程采样只包含 PID、线程和 asyncio Task，不包含 CPU、RSS、句柄、磁盘与网络；Metrics 按最近 N 个 Run 派生而非严格时间窗口；日志滚动、保留、上传和 Collector 由宿主环境负责；Learning Console 仍不是生产监控平台。

### 测试与验收

```powershell
python -m pytest
agent-runtime observe diagnostics
agent-runtime --json-logs demo "19 * 23" 2> runtime.jsonl
Invoke-RestMethod http://127.0.0.1:8000/observability/diagnostics
python scripts/check_coverage.py coverage.json
```

### 后续计划

先通过 Nightly 和真实本地运行观察失败分类、p95 与资源计数是否稳定，再决定是否引入时间窗口指标、CPU/RSS 采样或 OpenTelemetry Exporter；在 v0.7.x 可靠性结论明确前继续后置 v0.8。
<a id="e2026-08-16-001"></a>
## E2026-08-16-001：v0.7.10 在线备份、校验与灾难恢复演练

- **完成时间**：2026-08-16
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/backup.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `scripts/run_backup_recovery.py`
  - `scripts/verify_distribution.py`
  - `tests/test_backup.py`
  - `tests/test_lab_api.py`
  - `.github/workflows/quality.yml`
  - `.github/workflows/nightly-reliability.yml`
  - `docs/OPERATIONS.md`
  - `docs/LEARNING.md`
- **关联 commit**：`03cfae4`
- **关联 ADR**：[ADR-0022](./adr/0022-runtime-backup-restore.md)

### 变更摘要

补齐独立于进程崩溃恢复的状态备份能力，为 SQLite、Artifact、migration checksum 和恢复前回滚状态建立可执行的灾难恢复合同，并将真实恢复演练加入持续质量门禁。

### 系统架构

新增 Runtime 外部运维组件 `RuntimeBackupManager`。创建路径使用 SQLite Online Backup API 获取一致性数据库快照，再复制 Artifact 并生成内容校验 Manifest；恢复路径要求 Runtime 离线，先校验、保存当前状态、安装恢复点，失败时回滚。该能力不进入 Agent 执行循环，也不改变 schema 8。

### 实现方式

`.agent-backup` ZIP 归档包含 `runtime.sqlite3`、`artifacts/` 和 `manifest.json`；Manifest 记录 format version、schema version、migration checksum、表记录数、字节数和 SHA-256。校验执行 ZIP 路径检查、重复 Entry 检查、hash、`quick_check`、`foreign_key_check` 和 migration 验证。恢复使用同目录 staging、WAL checkpoint、排他锁检查和 `pre-restore-*` 回滚副本。

### 当前功能

支持运行中创建在线备份、离线校验、原路径恢复、默认保留恢复前状态、可选丢弃回滚副本，以及 CLI `backup create/verify/restore`。PR、Nightly 和 Wheel smoke 均执行备份路径；独立演练验证备份前 Run 与 Artifact 恢复、备份后 Run 不越过恢复点。

### 已知限制

现有持久化记录包含绝对 Artifact 路径，因此 v0.7.10 不支持跨目录或跨机器恢复；归档未内建加密、远程上传、自动调度和保留清理；恢复前必须停止所有连接目标 SQLite 的进程。

### 测试与验收

```powershell
python -m pytest
python scripts/run_backup_recovery.py
agent-runtime backup create --output runtime.agent-backup
agent-runtime backup verify runtime.agent-backup
python scripts/check_coverage.py coverage.json
```

### 后续计划

先在 Nightly 和真实本地状态目录持续执行恢复演练；进入 v0.8 前，评估 Artifact 相对标识、跨目录恢复、备份加密和保留策略，但不扩大 Agent 执行能力。
<a id="e2026-08-15-010"></a>
## E2026-08-15-010：v0.7.9 AgentDefinition 快照与确定性恢复

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/doctor.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `scripts/run_crash_recovery.py`
  - `tests/test_agent_snapshots.py`
  - `tests/test_crash_recovery.py`
  - `tests/test_doctor.py`
  - `tests/test_contract_edges.py`
- **关联 commit**：`9167990`
- **关联 ADR**：[ADR-0021](./adr/0021-agent-definition-snapshots.md)

### 变更摘要

持久化不可变 AgentDefinition 快照，并将普通 Run 与 Workflow Step 绑定到确切 checksum，使进程重启后无需应用重新注册 AgentDefinition，也不会因同名 Agent 后续升级而改变历史任务的恢复语义。

### 系统架构

schema 8 新增内容寻址的 `agent_definitions` 表和 `runs.agent_definition_checksum`；Runtime 在注册、创建 Run 和冻结 Workflow 时保存定义；恢复路径优先读取确切快照；Doctor 检查活动历史 Run 是否缺少定义绑定。

### 实现方式

AgentDefinition 被规范化为包含 System Prompt、ToolDefinition、ModelConfig、`max_steps` 和 `max_tool_calls` 的 JSON，并使用 SHA-256 去重；Workflow snapshot 为每个 Step 保存 checksum；若定义引用的 Tool Handler 在新进程不可用，则抛出 `AgentDefinitionUnavailable`，HTTP 返回不可重试的稳定 409 错误。

### 当前功能

支持普通 Run 无重新注册恢复、历史定义与最新同名定义隔离、串行/并行 Workflow 按 Step 快照恢复、缺失 Tool Handler 明确失败、Learning Console 显示定义快照数量，以及 schema 1–7 向 8 升级。

### 已知限制

Python Tool Handler 和 Provider 连接不能持久化，重启进程仍需提供对应实现；schema 7 以前已存在的活动 Run 没有定义 checksum，Doctor 会提示但不会自动猜测或修复；快照暂不自动清理。

### 测试与验收

```powershell
python -m pytest
python scripts/run_crash_recovery.py
python scripts/run_reliability.py --stress-runs 100 --concurrency 20
python scripts/check_coverage.py coverage.json
```

### 后续计划

继续观察 v0.7.8/v0.7.9 Nightly 稳定性；在进入 v0.8 Sandbox 前，优先补充备份恢复演练和运行手册，不扩展新的 Agent 能力。
<a id="e2026-08-15-009"></a>
## E2026-08-15-009：v0.7.8 Durable Submission 与过载保护

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/app.js`
  - `tests/test_api.py`
  - `tests/test_reliability.py`
  - `tests/test_contract_edges.py`
- **关联 commit**：`ab73a38`
- **关联 ADR**：[ADR-0020](./adr/0020-run-submission-idempotency-admission.md)

### 变更摘要

为顶层 Run 提交增加跨进程重启可保持的幂等键，并为活动 Run 和模型请求建立有界准入，避免客户端重试造成重复副作用，也避免突发请求无限消耗本机和 Provider 资源。

### 系统架构

schema 7 在 `runs` 表保存幂等键和规范化请求指纹；Runtime 新增 `RunSubmission`、活动任务容量快照和模型请求 Semaphore；FastAPI 将 `Idempotency-Key`、409 冲突、429 背压和 `Idempotent-Replayed` 映射为稳定 HTTP 合同。

### 实现方式

相同 Key 与相同 SHA-256 指纹在 SQLite `BEGIN IMMEDIATE` 事务内复用同一个 Run，多连接竞态由唯一索引收敛；同 Key 不同请求返回 `idempotency_conflict`；`max_inflight_runs` 达到上限时拒绝新顶层任务，`max_concurrent_model_requests` 同时约束普通和流式模型请求。

### 当前功能

支持 Python SDK 与 HTTP 幂等提交、并发提交去重、请求冲突检测、可重试容量错误、模型并发限制、Health/Learning Console 容量展示，以及 schema 1–6 向 7 升级。

### 已知限制

活动任务配额只覆盖当前 Runtime 进程，不是多进程全局队列；幂等重放只返回 durable Run，不会隐式 resume 崩溃后遗留任务；当前没有自动清理幂等键。

### 测试与验收

```powershell
python -m pytest
python -m pytest tests/test_api.py tests/test_reliability.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
```

### 后续计划

v0.7.9 持久化 AgentDefinition 快照，消除恢复 Workflow 时必须由应用重新注册 AgentDefinition 的限制。
<a id="e2026-08-15-008"></a>
## E2026-08-15-008：v0.7.7 崩溃恢复与运维闭环

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/doctor.py`
  - `src/agent_runtime/orchestration.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `scripts/run_crash_recovery.py`
  - `tests/crash_worker.py`
  - `tests/test_crash_recovery.py`
  - `.github/workflows/quality.yml`
  - `.github/workflows/nightly-reliability.yml`
- **关联 commit**：`0025941`
- **关联 ADR**：[ADR-0017](./adr/0017-unknown-outcome-confirmation.md)、[ADR-0018](./adr/0018-crash-recovery-contract.md)、[ADR-0019](./adr/0019-runtime-doctor.md)

### 变更摘要

完成 UNKNOWN 副作用人工确认、Workflow 快照恢复、只读 Runtime Doctor 和真实进程强杀恢复矩阵，使“检测异常—人工确认—显式恢复—验证无重复副作用”形成闭环。

### 系统架构

schema 6 为 ToolExecution 增加确认审计字段；Runtime 从规范化 Workflow snapshot 重建串行/并行编排；Doctor 只读取 SQLite 持久化事实；Crash Matrix 使用独立子进程和真实 `process.kill()` 验证恢复合同。

### 实现方式

UNKNOWN 只允许确认成功或失败，禁止直接 retry，确认后 Run 保持 PAUSED 并等待显式 `resume()`；`tool.outcome_confirmed` 保存 reason、actor 和时间；CLI/API 提供确认和 Doctor 入口；CI 在 Windows/Linux Python 3.13 执行 crash smoke，Nightly 重复完整矩阵。

### 当前功能

支持模型请求中断恢复、副作用 Tool 崩溃不重复执行、Approval 恢复、串行/并行 Workflow 部分完成恢复、Doctor 数据一致性诊断，以及 schema 1–5 向 6 升级。

### 已知限制

恢复依赖应用重新注册 snapshot 中引用的 AgentDefinition；Doctor 只诊断不自动修复；当前仍不提供跨主机调度和不可信代码隔离。

### 测试与验收

```powershell
python scripts/run_crash_recovery.py
agent-runtime doctor --json
python -m pytest -m crash
python -m pytest
```

### 后续计划

持续运行 v0.7.7 Nightly，稳定后再决定是否进入 v0.8 Sandbox。
<a id="e2026-08-15-007"></a>
## E2026-08-15-007：v0.7.6 FastAPI/SSE 长稳与发布验证

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/app.js`
  - `scripts/run_reliability.py`
  - `scripts/check_performance.py`
  - `scripts/verify_distribution.py`
  - `benchmarks/reliability-baseline.json`
  - `.github/workflows/nightly-reliability.yml`
  - `tests/test_api.py`
  - `tests/test_reliability.py`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0016](./adr/0016-fastapi-runtime-ownership-sse.md)

### 变更摘要

完成 HTTP、SSE、压力、长稳和安装产物验证，使现有能力可以反复启停和断线恢复。

### 系统架构

FastAPI lifespan 区分 Runtime 所有权；SSE 以 SQLite durable event 为事实来源；Nightly 负责长稳与性能门禁。

### 实现方式

保留 `shutdown_runtime=False` 兼容默认值；SSE 增加 heartbeat 和 Last-Event-ID；可靠性脚本混合单 Run、Tool、Workflow 和事件消费。

### 当前功能

支持真实 health、受控关闭、SSE 清理、20/100 并发、可配置 soak，以及干净环境 CLI/SDK/Uvicorn/SSE smoke。

### 已知限制

30 分钟 soak 由 Nightly 和发布前流水线执行。

### 测试与验收

```powershell
python scripts/run_reliability.py --stress-runs 100 --concurrency 20
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.7.6-py3-none-any.whl
```

### 后续计划

v0.7.6 门禁稳定前不启动 v0.8。

<a id="e2026-08-15-006"></a>
## E2026-08-15-006：v0.7.5 Runtime 生命周期、SQLite 与恢复语义

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：architecture
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/orchestration.py`
  - `tests/test_runtime.py`
  - `tests/test_orchestration.py`
  - `tests/test_reliability.py`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0015](./adr/0015-runtime-shutdown-sqlite-recovery.md)

### 变更摘要

定义 Runtime 安全启动、等待、暂停、恢复和关闭合同，并强化 SQLite durability、并发 sequence 和向前迁移。

### 系统架构

生命周期统一管理 Task、token、Provider、Tool executor 和 Store；schema 5 保存 Workflow 快照和 migration checksum。

### 实现方式

新增 `shutdown()`、async context manager 和带 timeout 的 `wait()`；SQLite 启用 WAL、FULL、busy retry、quick_check 和事务 sequence；pause 保持 PAUSED。

### 当前功能

shutdown 幂等；schema 1–4 升级到 5；多 Store sequence 唯一；启动协调 running Run 和副作用 Tool。

### 已知限制

不自动降级 schema，也不提供多进程调度。

### 测试与验收

```powershell
python -m pytest tests/test_runtime.py tests/test_orchestration.py tests/test_reliability.py
```

### 后续计划

所有 Adapter 统一使用生命周期合同。

<a id="e2026-08-15-005"></a>
## E2026-08-15-005：v0.7.4 Tool 与 Model Provider 执行安全

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：architecture
- **影响范围**：
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/providers.py`
  - `src/agent_runtime/runtime.py`
  - `tests/test_providers.py`
  - `tests/test_reliability.py`
  - `tests/test_contract_edges.py`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0013](./adr/0013-tool-isolation-unknown-outcome.md)、[ADR-0014](./adr/0014-provider-async-transport-retry.md)

### 变更摘要

隔离同步 Tool 和模型网络 I/O，修复 Event Loop 阻塞、资源无界、重试分类和副作用超时假成功。

### 系统架构

Runtime 使用有界 Tool 线程池；Provider 使用 `httpx.AsyncClient`；副作用不确定结果使用 `UNKNOWN`。

### 实现方式

默认 8 workers/32 pending，原子文件写入，严格校验响应/SSE，并按网络错误、408、429、5xx 和确定性 4xx 分类重试。

### 当前功能

长同步 Tool 不阻塞其他 Run；401/403 不重试；Retry-After 优先；取消关闭 HTTP stream；`aclose()` 幂等。

### 已知限制

同步 Python 线程不能安全强杀，迟到副作用结果必须人工确认。

### 测试与验收

```powershell
python -m pytest tests/test_providers.py tests/test_reliability.py tests/test_contract_edges.py
```

### 后续计划

保持 Tool Handler 和 Provider 公共协议兼容。

<a id="e2026-08-15-004"></a>
## E2026-08-15-004：v0.7.3 质量基线与行为合同

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：governance
- **影响范围**：
  - `pyproject.toml`
  - `scripts/check_coverage.py`
  - `.github/workflows/quality.yml`
  - `tests/test_contract_edges.py`
  - `tests/test_reliability.py`
  - `docs/CHANGELOG.md`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0012](./adr/0012-quality-gates.md)

### 变更摘要

将既有行为固化为兼容性合同，建立 lint、typing、coverage、跨平台测试和安装产物门禁。

### 系统架构

CI 分为 PR 快速严格门禁和 Nightly 长稳门禁；异常增加稳定分类且 API 保持 `detail` 兼容。

### 实现方式

启用 Ruff、Mypy strict、pytest-cov；core line ≥ 90%、branch ≥ 80%，combined ≥ 80%；支持 unit/integration/stress/soak marker。

### 当前功能

126 个测试覆盖单/多 Agent、Workflow、Memory、Approval、Streaming、API、Learning Console 和可靠性边界。

### 已知限制

CLI/SDK/API Adapter 不计入 core 聚合，但仍受 combined coverage 和 smoke test 约束。

### 测试与验收

```powershell
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json
python scripts/check_coverage.py coverage.json
```

### 后续计划

可靠性阈值只允许保持或提高。
<a id="e2026-08-15-003"></a>
## E2026-08-15-003：Learning Console 多 Agent 独立泳道与流程语义连线

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/CONTEXT_MEMORY.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
  - `docs/adr/README.md`
- **关联 commit**：`5e779a7`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

发布 v0.7.2 Learning Console 泳道可视化优化。修复串行和并行 Workflow 的全部 Child Event 被压入同一条 Agent 泳道、不同并行分支被全局相邻事件连线误表示为串行依赖的问题。

### 系统架构

- Timeline 从固定 8 条泳道改为 Snapshot 驱动的动态泳道模型。
- 多 Agent 场景生成 1 条 Workflow Parent 泳道，并为每个 Child Run 生成独立 Agent 泳道。
- Child 泳道按 `workflow_step` 或 `workflow_branch` 排序，顺序直接来自持久化 Run metadata。
- 非 Child 事件仍按 Memory、Context、Model、Tool、Approval 和 State 领域映射，但只显示当前 Snapshot 实际使用的领域泳道。
- 连线由跨 Run 全局相邻关系改为 Run 内部、Parent 委派和 Child 汇聚三类显式语义。

### 实现方式

- `buildSwimlanes()` 从 `snapshot.runs` 读取 Root/Child、Agent Name、Workflow Step/Branch、状态和 metadata，动态构造标签、顺序、颜色和画布高度。
- Child Event 使用 `agent:<run_id>` 作为稳定泳道 ID，因此同一 Child 的 Run、Context、Model、Tool 与 Checkpoint 事件保持在同一行。
- `buildTimelineLinks()` 先按 `run_id` 分组，再按 `local_sequence` 建立 Run 内部连线。
- `delegation.created → Child run.created` 建立委派分叉线；Child 终态事件到对应 `delegation.completed/failed/cancelled` 建立汇聚线。
- 页面增加连线图例；实线表示 Run 内部，长虚线表示委派，点线表示汇聚。
- 泳道标签和画布高度由 JavaScript 动态计算，移除 HTML/CSS 中固定 8 行和固定 660px 高度。

### 当前功能

- 串行 Workflow 显示 Workflow Parent、Planner、Worker、Reviewer 独立泳道。
- 并行 Workflow 显示 Workflow Parent、Research、Test、Risk 独立泳道。
- 每条 Agent 泳道显示 Step/Branch 编号、Agent Name 和当前状态，并使用独立颜色。
- 单 Run 场景继续按实际出现的 Context、Model、Tool、Approval 和 State 领域展示。
- 顶部时间轴使用跨 Run `timeline_sequence`，Child 节点同时展示 `local_sequence`。
- 回放、节点点击、自动跟随、Inspector 和 Root SSE + Snapshot 轮询行为保持不变。

### 已知限制

- 横轴仍按 `timeline_sequence` 等距排列，不是按真实耗时比例缩放。
- 确定性并行教学场景用于展示逻辑并发与分支关系，不用于衡量真实并行性能。
- Root SSE 仍不直接转发全部 Child Event，页面运行期间继续使用 450ms Snapshot 轮询。
- Learning Console 仍是本地单用户教学工具，不是生产 Workflow Designer 或分布式 Trace UI。

### 测试与验收

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -o addopts= -q
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
git diff --check
agent-runtime lab
```

验收覆盖：

- 静态页面使用动态 `swimlaneLabels` 容器，不再硬编码 Agent/Context 等 8 行标签。
- JavaScript 包含 `buildSwimlanes()` 和 `buildTimelineLinks()`。
- Child Event 映射为 `agent:<run_id>`。
- 串行 Agent 按 Planner、Worker、Reviewer 排序。
- 并行 Agent 按 Research、Test、Risk 排序。
- 每个 Child 均存在独立 `run.created`，可建立 Parent 委派和 Child 汇聚连线。
- 自检确认两个 Workflow 均生成 3 条 Agent 泳道、3 条委派线和 3 条汇聚线。
- 当前全量测试：`55 passed`。

### 后续计划

继续进入 v0.8 Sandbox；届时 Learning Console 为 Sandbox Policy、Capability、Secret Redaction 和 Tool Isolation 增加对应教学泳道或 Inspector，而不改变本次多 Agent 映射规则。

<a id="e2026-08-15-002"></a>
## E2026-08-15-002：Learning Console 覆盖 v0.6 多 Agent 与 v0.7 Context/Memory

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/scenarios.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_lab_api.py`
  - `tests/test_lab_scenarios.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
- **关联 commit**：`c9c5f4c`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

发布 v0.7.1 Learning Console 教学增强版。场景从 4 个单 Run 扩展到 9 个，新增 v0.6 串行/并行多 Agent，以及 v0.7 Session/Memory、Context Compaction 和大 Tool Result Artifact 场景。所有流程继续通过真实 Runtime、SQLite、Workflow、MemoryStore、ContextBuilder 和 ArtifactStore 执行。

### 系统架构

- Learning Console Snapshot 从单 Run 投影升级为 Root + Child Run 聚合投影。
- 使用 `RunRelation` 和 `TraceTree` 展示 Parent/Child 拓扑，不向 Runtime Kernel 注入 UI 状态。
- 增加 Session/Memory、Context、Agent 专用泳道和 Inspector。
- Root SSE 继续提供主事件通知；页面在运行期间补充轻量 Snapshot 轮询，以动态读取 Child Run 独立事件。
- Timeline 为跨 Run 事件生成展示用 `timeline_sequence`，同时保留每个 Run 的持久化 `local_sequence`。

### 实现方式

- `SequentialWorkflow` 真实执行 Planner → Worker → Reviewer 三个 Child Run。
- `ParallelWorkflow` 真实并发执行 Research、Test 和 Risk 三个 Child Run，并按 `ALL` 策略聚合。
- Session 场景创建 Session Memory 与 Agent Memory，模型请求前执行作用域检索和 Context 注入。
- Context 场景使用小 token budget 和四轮大工具结果稳定触发 `context.compacted`。
- Artifact 场景使用真实 ArtifactStore 保存完整大文本，Checkpoint 只保留引用和 Preview。
- Snapshot 聚合 Runs、Relations、Events、Checkpoints、Steps、ToolExecutions、Approvals、Memories、Context Builds 和 Artifacts。

### 当前功能

- Learning Console 共 9 个确定性教学场景。
- v0.6：串行多 Agent、并行多 Agent、Parent/Child Trace Tree、RunRelation 与聚合策略展示。
- v0.7：Session/Memory 检索、Context 构建/压缩、大 Tool Result Artifact 化展示。
- 8 条泳道：Run、Agent、Session/Memory、Context、Model、Tool、Approval、State。
- Inspector 新增 Context、Memory、Artifact，并强化 Trace Parent/Child Topology 与 v0.6/v0.7 Metrics。
- 自动验收新增 Child、Memory、Compaction 和 Artifact 数量检查。

### 已知限制

- 跨 Run Timeline 的 `timeline_sequence` 是 Snapshot 展示序号，不替代各 Run Event Log 的本地 sequence。
- Root SSE 不直接转发 Child Run 的每个事件，Learning Console 在本地运行期间使用 450ms Snapshot 轮询补充动态展示。
- Learning Console 仍面向本地单用户教学，不提供认证、远程多租户或 Workflow Designer。
- 场景使用确定性 Mock Provider，目的是学习 Runtime 语义，不评估真实模型质量。

### 测试与验收

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -o addopts= -q
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
git diff --check
agent-runtime lab
```

验收覆盖：

- 9 个场景目录和静态页面入口。
- 串行/并行 Workflow 均生成 1 个 Parent、3 个 Child 和 3 条 RunRelation。
- Session 场景检索 Session/Agent 两类 Memory，并在 Context 中记录命中 ID。
- Context 场景稳定产生 `context.compacted`，完整 Checkpoint 消息多于模型实际选择消息。
- Artifact 场景产生真实文件和 `tool.result.artifactized` provenance。
- 聚合 Event 同时包含 `timeline_sequence`、`local_sequence`、`run_id`、`agent_name` 和 `run_role`。

### 后续计划

继续进入 v0.8 Sandbox；Learning Console 后续可增加安全策略、Capability 和 Secret Redaction 教学场景。

<a id="e2026-08-15-001"></a>
## E2026-08-15-001：完成 Context、Session 与长期记忆 v0.7

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/context.py`
  - `src/agent_runtime/memory.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/evals.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/sdk.py`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_context_memory.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `scripts/check_docs.py`
  - `README.md`
  - `docs/README.md`
  - `docs/CONTEXT_MEMORY.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0011-context-session-memory.md`
  - `docs/adr/README.md`
- **关联 commit**：`1086c5e`
- **关联 ADR**：[ADR-0011](./adr/0011-context-session-memory.md)

### 变更摘要

完成 v0.7 Context、Session 与 Scoped Long-term Memory。Runtime 在每次模型调用前构建可追溯的受预算 Context；多个 Run 可以显式归入 Session，并在 Session 或 Agent Scope 内检索、注入、删除和过期 Memory。大 Tool Result 自动进入 Artifact Store，避免无限扩张 Checkpoint 和模型输入。

### 系统架构

- 新增 `ContextBuilder`，在完整 Checkpoint 与 Provider 请求之间建立受预算的 Context Build 层。
- 新增 `Session`、`session_runs`、`MemoryRecord`、`MemoryScope` 和 `MemoryStore`。
- SQLite schema 从 version 3 升级到 version 4，新增 Session、Memory 和 FTS5 索引表。
- Runtime 在模型调用前检索允许 Scope 的 Memory，再构造 Context 并写入可审计事件。
- 大 Tool Result 的完整内容写入 Artifact Store，ToolExecution 和 Checkpoint 保存引用与预览。
- Observability 和 Eval 增加 Memory Search、Context Compaction 与 Memory 数量验证。

### 实现方式

- 使用 Provider-neutral 的确定性近似 token 估算，使 Context 行为不绑定某个模型 SDK。
- System Prompt、最近消息和未完成 Tool Call 优先保留；Assistant Tool Call 与 Tool Result 作为不可拆分消息组。
- 被省略的旧消息生成确定性 Summary，并通过 `context.compacted` 记录。
- Memory 只允许 `session` 和 `agent` Scope，不提供 global Scope。
- SQLite FTS5 提供关键词检索，并强制 Scope、TTL、软删除和 source provenance 过滤。
- `source_run_id` 和 `source_trace_id` 使 Memory 可以反查来源执行。
- `MemoryEvalRunner` 复用现有 Eval Report 和 Artifact 机制。

### 当前功能

- 支持 Context token budget、消息分组、裁剪和确定性 Summary。
- 支持大 Tool Result Artifact 化和预览引用。
- 支持创建 Session、关联多个 Run，并让 Child Run 继承 Session。
- 支持 Session Memory 与 Agent Memory。
- 支持 SQLite FTS5 搜索、TTL、软删除和过期清理。
- 支持 `memory.search.started/completed`、`context.built/compacted` 和 `tool.result.artifactized` 事件。
- 支持 Context/Memory Metrics、Prometheus 指标和 Memory Eval。
- 支持 Session/Memory HTTP API 和 `agent-runtime memory demo`。

### 已知限制

- token 估算不是具体模型厂商的精确 tokenizer。
- Summary 是确定性文本摘要，不是模型生成的语义摘要。
- 当前只提供 SQLite FTS5，不提供 Embedding 或向量检索。
- Memory 必须显式创建，不会自动永久保存所有对话。
- 当前不提供 global Memory Scope。
- Learning Console 暂无 Session/Memory 专用管理画布。

### 测试与验收

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -o addopts= -q
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
agent-runtime memory demo "Which Python language do I prefer?" --remember "The user prefers Python for examples."
```

验收覆盖：

- `50 passed`。
- Context Change ID、日期顺序、路径和 ADR 关联通过文档门禁。
- Session Scope 与 Agent Scope 隔离、TTL 和软删除行为正确。
- Context 不拆分 Tool Call 组，并可对旧消息生成可追溯 Summary。
- Memory 检索、source trace、Metrics 和 Eval 结果可查询。
- 大 Tool Result 可写入 Artifact 并以引用进入 Checkpoint。
- Memory CLI Demo 能检索并使用显式写入的偏好。

### 后续计划

进入 v0.8：实现 Sandbox、Tool Capability 与 Secret 安全边界。

---
<a id="e2026-08-14-007"></a>
## E2026-08-14-007：完成持久化多 Agent 编排基础 v0.6

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/orchestration.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/evals.py`
  - `src/agent_runtime/sdk.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_orchestration.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `scripts/check_docs.py`
  - `README.md`
  - `docs/README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/MULTI_AGENT.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0010-parent-child-run-delegation.md`
  - `docs/adr/README.md`
- **关联 commit**：`e7ab51a`
- **关联 ADR**：[ADR-0010](./adr/0010-parent-child-run-delegation.md)

### 变更摘要

完成 v0.6 单机多 Agent 编排基础。Parent 可以通过持久化 RunRelation 和稳定 delegation key 委派独立 Child Run；系统提供顺序与并行 Workflow、并发与超时控制、三种汇聚策略、取消传播、幂等恢复、Trace Tree、Multi-Agent Metrics 和 Workflow Eval。

### 系统架构

- 新增 `agent_runtime.orchestration`，包含 AgentRegistry、SequentialWorkflow、ParallelWorkflow、WorkflowStep、WorkflowExecution 和 AggregationStrategy。
- 每个 Workflow Parent 和 Agent Child 都是独立 AgentRun，继续使用既有状态机、Runtime Event、Checkpoint、ToolExecution 和 Approval 语义。
- SQLite schema 从 version 2 升级到 version 3，新增 `run_relations`。
- Observability 从单 RunTrace 扩展为可从任意节点查询的 Parent/Child TraceTree。
- Learning Console Snapshot 增加 `trace_tree`，但 UI 内核不感知多 Agent 展示逻辑。

### 实现方式

- `AgentRegistry` 校验 Agent 名称和工具定义，同名不同定义会被拒绝。
- `Runtime.delegate()` 使用 `parent_run_id + delegation_key` 查重；首次委派原子写入 Child Run、RunRelation 和双方事件，恢复时复用原 Child。
- Child 使用独立 `trace_id`，并保存 Parent、Root、root_trace_id 和 delegation key metadata。
- `SequentialWorkflow` 用稳定 step key 顺序传递结果。
- `ParallelWorkflow` 使用 Semaphore 控制最大并发，通过 timeout 收敛超时，并支持 `all`、`best_effort` 和 `first_success`。
- Parent Cancel 递归取消活动 Child，已完成 Child 不改写终态。
- Workflow Parent 保存独立初始与终态 Checkpoint。
- `WorkflowEvalRunner` 复用 EvalCase / EvalSuite，可额外断言 Child Run 数量并写入 JSON Artifact。

### 当前功能

- 支持 Parent / Child / Root 关系查询。
- 支持手工 `Runtime.delegate()`。
- 支持 Planner → Worker → Reviewer 顺序 Workflow。
- 支持并行分支、最大并发数和 Workflow timeout。
- 支持 `all`、`best_effort`、`first_success` 汇聚。
- 支持 Parent 取消传播。
- 支持稳定 delegation key 幂等恢复。
- 支持 Trace Tree、Root/Child/Workflow/Delegation Metrics 和 Prometheus 导出。
- 支持 `GET /agents`、关系查询、Trace Tree 和委派 API。
- 支持 `agent-runtime workflow demo` 与 `agent-runtime observe trace-tree`。
- 项目版本更新为 `0.6.0`。

### 已知限制

- Child 仍在同一 Python 进程和 SQLite Store 中执行，没有跨机器 Worker。
- Workflow 定义和 AgentRegistry 仍由应用代码提供，数据库不持久化可执行定义。
- Workflow 不支持通用 pause/resume；恢复必须重新使用原 Workflow 定义和 parent_run_id。
- ParallelWorkflow 不提供通用 DAG 依赖表达式或图形化 Designer。
- Learning Console 尚未提供专用的跨 Run Trace Tree 画布。
- 尚无长期记忆、Docker Sandbox、多租户和 OpenTelemetry Collector。

### 测试与验收

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
agent-runtime workflow demo "设计一个可靠的恢复机制"
```

当前测试：`44 passed`。

新增覆盖：AgentRegistry 冲突校验、RunRelation migration、委派幂等、独立 Trace ID、顺序结果传递、恢复复用、并发上限、严格/尽力汇聚、first_success、Parent Cancel 传播、Trace Tree、Multi-Agent Metrics、Workflow Eval、API 和 Learning Console Snapshot。

### 后续计划

进入 v0.7，增加 Context、Session、短期上下文窗口与长期记忆接口；分布式 Worker、Queue 和 Lease 继续保留到 v0.9。

<a id="e2026-08-14-006"></a>
## E2026-08-14-006：修复 Learning Console 空状态占用大片空间

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：fix
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
  - `docs/adr/README.md`
- **关联 commit**：`b8fad6d`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

修复 Learning Console 在已有 Runtime Event 时仍显示“运行一个场景”空状态的问题。该区域的用途只是在尚未启动 Run 时提示 SQLite / SSE 数据来源，不应在泳道图已出现时继续占用页面高度。

### 系统架构

- Runtime Kernel、SQLite、Snapshot API、SSE 和泳道事件数据保持不变。
- 变更仅位于 Learning Console Static UI 展示层。
- 空状态和泳道图继续由 Snapshot 中是否存在 Event 决定，两者必须互斥。

### 实现方式

- 根因是 `.empty-state { display: grid; }` 的作者样式覆盖了浏览器默认 `[hidden] { display: none; }`。
- 新增 `.empty-state[hidden] { display: none; }`，使 `timelineEmpty.hidden = true` 可靠生效。
- 将未运行时的空状态从 390px 纯居中大区域改为 164px 紧凑横向引导，并缩小轨道动画。
- 390px 移动宽度使用 138px 紧凑空状态，不引入页面横向溢出。

### 当前功能

- 未创建 Run 时显示紧凑引导提示。
- 一旦 Snapshot 存在 Event，空状态立即完全隐藏。
- 泳道图直接紧跟图例显示，不再有额外 390px 空白区域。
- 原有 SSE 追加、回放、节点连线、Inspector 和审批功能保持不变。
- API 和项目版本更新为 `0.5.3`。

### 已知限制

- 空状态仍只用于“尚无 Event”，尚未区分首次访问、场景启动中和加载失败等更细状态。
- Learning Console 仍为本地单用户教学界面，不是生产运维控制台。

### 测试与验收

```powershell
cd D:\AICoding\Agent
node --check src\agent_runtime\lab\static\app.js
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
```

当前自动化测试：`37 passed`。

浏览器回归结果：

- 运行前空状态为 `display: grid`，高度为 164px。
- 运行前泳道图为 `display: none`。
- 运行后空状态为 `hidden=true`、`display: none`，高度为 0。
- 运行后泳道图为 `display: grid`。
- 图例与泳道图之间的间距为 12px。
- Tool Calling 教学场景生成 18 个泳道节点。

静态回归检查：CSS 必须包含 `.empty-state[hidden]` 和 164px 紧凑高度。

### 后续计划

可在后续版本将“初始引导”、“Run 启动中”和“加载失败”拆分为独立紧凑状态，同时保持泳道为中间区域的主视图。


<a id="e2026-08-14-005"></a>
## E2026-08-14-005：Learning Console 改用动态事件泳道图

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
  - `docs/adr/README.md`
- **关联 commit**：`3559243`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

将 Learning Console 原有的单列纵向事件时间线升级为横向动态泳道图。用户现在可以同时看到事件顺序、执行角色、跨泳道跳转、相对时间和当前回放位置，减少从长列表中理解 Runtime 协作流程的认知成本。

### 系统架构

- Runtime Kernel、Event Envelope、SQLite schema、SSE API 和 Snapshot API 保持不变。
- 泳道图仍是 `agent_runtime.lab` 的浏览器展示 Adapter，只消费已持久化 RuntimeEvent。
- Run / Model / Tool / Approval / State 五条泳道由 Event type 前缀确定，`RuntimeEvent.sequence` 仍是唯一时序依据。
- 事件节点和 Inspector 继续共享同一个选中游标。

### 实现方式

- Vanilla JavaScript 根据事件数量动态计算泳道画布宽度、节点坐标和泳道坐标。
- SVG Bezier 曲线连接相邻 sequence，流动虚线用于表达从左向右的执行方向。
- 每个节点展示 sequence、相对时间、Event type 和教学标题；详细 summary 通过 title 和 Inspector 提供。
- SSE 刷新、从头回放、上一步、下一步和自动播放复用原有游标，并自动滚动到当前节点。
- 泳道标签保持在左侧，事件画布可横向滚动，移动端缩减标签文案但保留领域名。

### 当前功能

- 事件按 Run、Model、Tool、Approval 和 State 五类动态分道。
- 泳道节点通过曲线按 sequence 串联，选中节点和对应连线同步高亮。
- 时间轴显示事件 sequence 与相对耗时。
- 新 SSE 事件会追加到右侧，实时跟随时自动居中当前节点。
- 原有回放速度、事件详情、状态 diff、审批和验收功能保持可用。
- API 和项目版本更新为 `0.5.2`。

### 已知限制

- 当前泳道是五个固定领域，尚未支持用户自定义分组。
- 长 Run 需要横向滚动，尚未增加缩放、鸟瞰图、事件折叠和类型过滤。
- 连线表示持久化 sequence 关系，不是方法调用栈或分布式 Trace 因果图。
- 回放仍是浏览器展示回放，不是 Runtime Kernel 协程单步调试。

### 测试与验收

```powershell
cd D:\AICoding\Agent
node --check src\agent_runtime\lab\static\app.js
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
agent-runtime lab --no-browser
```

当前测试：37 passed。验收覆盖泳道 DOM 骨架、CSS 领域样式、JavaScript 分道与坐标函数、API 版本、现有 4 个真实 Runtime 场景、SSE、回放、审批和 Snapshot 聚合数据。另使用无界面 Chrome 验收 1440px 桌面布局和 390px 移动布局：Tool Calling 场景生成 18 个节点、17 条连线和 5 条泳道，Token Streaming 场景的 `model.delta` 均落入 Model 泳道，移动端无 body 横向溢出。

### 后续计划

在泳道事件数较多时增加类型过滤、密度切换、鸟瞰图和按 Step 折叠；v0.6 引入 Parent/Child Run 后再扩展 Agent 与 Child Run 泳道。


<a id="e2026-08-14-004"></a>
## E2026-08-14-004：增加可视化 Learning Console v0.5.1

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/lab/__init__.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/scenarios.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/lab/routes.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_lab_api.py`
  - `tests/test_lab_scenarios.py`
  - `tests/test_api.py`
  - `docs/LEARNING.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/README.md`
  - `docs/adr/README.md`
  - `docs/adr/0009-learning-console.md`
  - `README.md`
  - `scripts/check_docs.py`
- **关联 commit**：`19a0b20`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

增加面向初学者的本地可视化 Learning Console，将 Run、Event、Step、ToolExecution、Checkpoint、Approval、Trace、Metrics 和自动验收集中到一个浏览器页面。用户只需执行 `agent-runtime lab`，即可运行确定性场景、实时观察持久化事件、逐步回放并映射到源码处理链。

### 系统架构

- 新增 `agent_runtime.lab` 教学 Adapter，挂载到现有 FastAPI Demo App 的 `/lab`。
- Learning Console 的场景通过真实 Runtime、Provider、ToolRegistry 和 SQLiteStore 执行，不建立第二套模拟状态。
- 不同场景使用独立确定性 Provider/AgentDefinition，并共享现有 SQLite 执行事实。
- 页面复用 `/runs/{run_id}/events/stream` SSE、ObservabilityService 和持久化 Checkpoint。
- Runtime Kernel、Run 状态机、Event Envelope、Provider Protocol 和 SQLite schema 均未改变。

### 实现方式

- `ScenarioRegistry` 定义默认输入、预期事件、学习点、自动验收和人工动作提示。
- `LearningConsole` 根据 Run metadata 将场景 Run 路由回正确 Runtime，支持审批后从原 Checkpoint 恢复。
- Snapshot API 聚合 Run、Event、Step、ToolExecution、Approval、Checkpoint、Trace、Metrics 和 SQLite 记录统计。
- 每个 Event 附带教学解释、源码方法映射和 before/after 状态投影。
- 静态 HTML/CSS/Vanilla JavaScript 提供三栏界面、SSE 实时刷新、事件回放、Inspector Tabs 和审批操作，不引入 Node.js 构建链。
- SQLiteStore 增加只读 `steps_for_run()` 和 `tool_executions_for_run()` 查询，供观测 Adapter 使用。

### 当前功能

- CLI：`agent-runtime lab` 一条命令启动并默认打开浏览器。
- 页面：`GET /lab`。
- 场景 API：场景目录、启动 Run、读取 Snapshot 和处理审批。
- 首批场景：纯文本响应、Tool Calling、Token Streaming、Human Approval。
- 事件时间线支持从头回放、上一步、下一步、自动播放和速度选择。
- Inspector 支持事件解释、状态 diff、Messages、Step/ToolExecution、Trace、Metrics、SQLite 和自动验收。
- API 和项目版本更新为 `0.5.1`。

### 已知限制

- 首版仅提供 4 个确定性学习场景。
- 回放控制的是持久化事件展示，不是 Runtime 协程的内核级单步调试。
- Learning Console 面向本地单用户环境，没有认证、授权和多租户隔离。
- Snapshot 采用按请求聚合查询，不面向高并发生产控制台。
- 尚未展示多 Agent Parent/Child Run 和 Trace Tree。

### 测试与验收

```powershell
cd D:\AICoding\Agent
python -m compileall -q src tests
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
agent-runtime lab --no-browser
```

当前测试：37 passed。新增覆盖静态页面、场景目录、CLI 参数、真实 Runtime 场景执行、Tool Calling、Token Streaming、Approval 暂停/恢复、Snapshot 教学数据、Trace、SQLite 统计和自动验收。

### 后续计划

进入 v0.6 多 Agent 编排后，在 Learning Console 中增加 Parent/Child Run Tree、委派事件、并行子 Run、聚合策略和跨 Run Trace 可视化。

---

<a id="e2026-08-14-003"></a>
## E2026-08-14-003：建立 v0.6 至 v1.0 演进路线图

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：governance
- **影响范围**：
  - `docs/ROADMAP.md`
  - `docs/README.md`
  - `docs/CURRENT.md`
  - `docs/CHANGELOG.md`
  - `README.md`
  - `scripts/check_docs.py`
- **关联 commit**：`80d90c2`
- **关联 ADR**：不需要；本次只建立规划与文档治理规则，不改变 Runtime 架构和执行协议

### 变更摘要

将此前分散在 CURRENT、CHANGELOG 和对话中的后续方向整理为独立 `ROADMAP.md`，正式记录 v0.6 至 v1.0 的演进顺序、版本范围、前置依赖、非目标、验收重点和预计 ADR。

### 系统架构

无 Runtime 架构变化。新增 Roadmap 只负责描述未来计划，当前实现事实仍由 `CURRENT.md` 和 `ARCHITECTURE.md` 维护，已完成历史仍由 `CHANGELOG.md` 维护。

### 实现方式

- 路线图按版本从低到高排列，与 CHANGELOG 的完成时间倒序职责分离。
- completed 版本关联已有 Change ID，planned 版本不伪造完成记录和日期。
- 同一时间最多允许一个主版本处于 `in-progress`。
- `check_docs.py` 检查文件存在性、版本顺序、状态、完成记录和当前版本一致性。
- README 和文档中心增加 Roadmap 导航入口。

### 当前功能

- 可以从一个文件查看 v0.6 多 Agent、v0.7 Memory、v0.8 Sandbox、v0.9 Worker、v0.10 生产治理和 v1.0 稳定协议规划。
- 每个版本包含目标、计划范围、验收重点、非目标和预计 ADR。
- 路线图定义开始、完成和调整时如何同步 CURRENT、CHANGELOG、ARCHITECTURE 和 ADR。

### 已知限制

- 路线图是 Living Document，不代表固定交付日期。
- planned 内容可能随着前置版本的实现结果调整。
- 路线图只定义方向和验收边界，具体技术方案仍需在版本开始时通过 ADR 确认。

### 测试与验收

```powershell
cd D:\AICoding\Agent
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -q
```

文档门禁必须识别 7 条演进记录，并验证 Roadmap 的版本顺序、状态和 Change ID 关联；现有 30 项 Runtime 测试保持通过。

### 后续计划

按照路线图进入 v0.6.0：Parent/Child Run、RunRelation、AgentRegistry 和受控 `Runtime.delegate()`。

---

<a id="e2026-08-14-002"></a>
## E2026-08-14-002：增加 Observability、Tracing、Metrics 与 Evals

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/evals.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `tests/test_observability.py`
  - `tests/test_evals.py`
  - `tests/test_api.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0008-observability-evals.md`
  - `README.md`
- **关联 commit**：`b5ddc61`
- **关联 ADR**：[ADR-0008](./adr/0008-observability-evals.md)

### 变更摘要

为 v0.4 Runtime 增加基于持久化执行事实派生的 Trace、Metrics、Prometheus 和确定性 Eval Runner，使每个运行和评估用例都可以定位到 Run、Event、Trace、报告和代码版本。

### 系统架构

- Runtime 创建 Run 时自动生成 `trace_id`，但不改变 Run 状态机和 SQLite Event schema。
- `ObservabilityService` 位于 Runtime 之外，从 Run/Event 派生 Span 和指标。
- `EvalRunner` 通过正式 `Runtime.run()` 执行用例，复用 Provider、Tool、Checkpoint、安全和恢复语义。
- FastAPI 和 CLI 作为 Adapter 暴露观测结果，核心 Runtime 不依赖遥测框架。

### 实现方式

- Run、Model、Tool 和 Approval Span 根据事件时间、sequence 和关联 ID 配对。
- Metrics 汇总 Run 状态、Event 类型、模型/工具/审批次数、token usage、平均延迟和 p95 Run 延迟。
- Prometheus 使用 `agent_runtime_*` 指标名称输出纯文本。
- Eval 内置状态、精确匹配和包含判断评估器，报告写入 Artifact Store。
- Eval Run metadata 保存 report、suite 和 case 标识，可反查 Trace 与 Event。

### 当前功能

- Python SDK：`ObservabilityService`、`RunTrace`、`MetricsSnapshot`、`EvalSuite`、`EvalCase`、`EvalRunner`。
- CLI：`agent-runtime observe metrics`、`agent-runtime observe trace <run-id>`、`agent-runtime eval demo`。
- API：`GET /observability/metrics`、`GET /observability/metrics/prometheus`、`GET /runs/{run_id}/trace`。
- 每个 Run 自动生成并持久化 `trace_id`。
- Eval Report 包含用例通过率、Run ID、Trace ID、断言、耗时和 Artifact 路径。
- API 和项目版本更新为 `0.5.0`。

### 已知限制

- Metrics 当前通过扫描本地 SQLite 历史派生，尚未做增量聚合。
- 尚未接入 OpenTelemetry Collector、外部 Trace Backend 或时序数据库。
- Eval Suite 顺序执行，尚无并发评估、数据集版本和统计显著性分析。
- 评估器暂不支持 LLM-as-a-Judge、语义相似度或工具轨迹规则。

### 测试与验收

```powershell
cd D:\AICoding\Agent
python -m compileall -q src tests
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
agent-runtime eval demo
agent-runtime observe metrics
```

当前测试：30 passed。覆盖 trace_id、Run/Model/Tool Span、历史 Metrics、Prometheus、Observability API、Eval 通过/失败、Contains 评估、Artifact 报告和 metadata 追溯。

### 后续计划

进入 v0.6：多 Agent 编排基础，包括子 Run、委派关系、并行执行和结果汇聚，同时复用 v0.5 Trace 与 Eval 能力。

---

<a id="e2026-08-14-001"></a>
## E2026-08-14-001：增加真实模型 Provider 与 Token Streaming

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/providers.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/api/app.py`
  - `tests/test_providers.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0007-model-token-streaming.md`
  - `README.md`
- **关联 commit**：`444dec4`
- **关联 ADR**：[ADR-0007](./adr/0007-model-token-streaming.md)

### 变更摘要

在 v0.3 FastAPI / SSE 基础上增加真实模型 Provider 的流式响应能力。Runtime 可以接收文本和 Tool Call 增量，产生持久化 `model.delta` 事件，并最终恢复为原有完整模型响应。

### 系统架构

- `ModelProvider.complete()` 继续作为兼容基线。
- 新增可选 `StreamingModelProvider.stream()` 和 `ModelTokenDelta`。
- Runtime Kernel 负责把 Provider 增量转换为 `model.stream.started`、`model.delta`、`model.stream.completed`。
- FastAPI 不新增第二套 token API，继续通过 `/runs/{run_id}/events/stream` 输出统一事件流。

### 实现方式

- `MockStreamingProvider` 用于确定性测试，验证多个文本 delta 和 Tool Call delta 的合并。
- `OpenAICompatibleProvider` 使用标准库 HTTP 和后台线程逐行解析 Chat Completions SSE，支持 `[DONE]`、finish reason、usage 和 Tool Call 增量。
- Runtime 对不支持 `stream()` 的 Provider 自动回退到 `complete()`，保留 v0.2/v0.3 行为。
- 流式响应完成后才生成最终 assistant message，并继续使用既有 Step、ToolExecution、Checkpoint 和审批流程。

### 当前功能

- 支持 OpenAI-compatible Chat Completions 非流式调用。
- 支持 OpenAI-compatible SSE 流式文本响应。
- 支持多个 `model.delta` 事件通过 Runtime Event Log 和 SSE 输出。
- 支持流式 Tool Call 参数拼接后进入原有工具 schema 校验。
- 支持旧 Provider 无 streaming 时自动 fallback。
- API 版本更新为 `0.4.0`。

### 已知限制

- 仍然使用标准库 HTTP；不同厂商的 SSE 扩展格式需要持续补充适配。
- 当前每个 delta 都持久化，极高吞吐场景的存储成本尚未优化。
- 标准库阻塞 HTTP 在 `asyncio.to_thread` 中执行，取消请求不能立即中断底层 socket。
- 尚未实现多 Agent 编排、分布式 Worker、长期记忆和 Docker 代码沙箱。

### 测试与验收

```powershell
cd D:\AICoding\Agent
python -m compileall -q src tests
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
```

当前测试：25 passed。覆盖 Mock streaming、OpenAI-compatible SSE 解析、Runtime 增量事件、最终 Checkpoint 合并和 v0.3 API 回归。

### 后续计划

进入 v0.5：Observability、Metrics、Tracing 与 Evals，继续保持每个功能都有事件、测试和演进记录。

---

<a id="e2026-08-13-002"></a>
## E2026-08-13-002：增加 FastAPI Run API 与 SSE 事件接口

- **完成时间**：2026-08-13
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/__init__.py`
  - `src/agent_runtime/api/app.py`
  - `tests/test_api.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0006-fastapi-sse-adapter.md`
  - `README.md`
- **关联 commit**：`f4fc22b`
- **关联 ADR**：[ADR-0006](./adr/0006-fastapi-sse-adapter.md)

### 变更摘要

为 Runtime 增加独立 FastAPI HTTP Adapter 和基于持久化 Event Log 的 SSE 接口，使客户端可以创建、查询、控制 Run，并订阅可恢复的运行事件。

### 系统架构

- FastAPI 位于 Runtime Core 之外的 Application / Adapter Layer。
- API 不直接操作 SQLite connection，而是复用 `Runtime`、`SQLiteStore` 和 `Runtime.stream()`。
- SSE 使用 Event sequence 作为事件 id，并通过 `after_sequence` 支持断点续传。
- API 依赖通过 optional extra 提供，核心包默认不依赖 FastAPI。

### 实现方式

- 新增 `agent_runtime.api.create_app()` 和 `create_demo_app()`。
- `POST /runs` 使用 `Runtime.start()` 异步启动 Run，返回 `202 Accepted`。
- 历史事件通过 `GET /runs/{run_id}/events` 读取；事件流通过 `StreamingResponse` 输出。
- 暴露 pause、resume、cancel 和 approval resolve，保留 Runtime 原有状态机与审批语义。
- 使用 Pydantic 请求模型校验创建 Run 和审批决策。

### 当前功能

- `GET /health`。
- `POST /runs`、`GET /runs/{run_id}`。
- `GET /runs/{run_id}/events`，支持 `after_sequence`。
- `GET /runs/{run_id}/events/stream`，输出 `id`、`event`、`data` 三段 SSE。
- Run pause / resume / cancel。
- Pending approval 查询与审批决策提交。
- 不存在的 Run 或 Approval 返回 404，非法生命周期操作返回 409。

### 已知限制

- SSE 当前通过 SQLite polling，不是跨进程消息总线。
- SSE 输出 Runtime Event，不是模型 token 原生流。
- API 内异步任务属于当前进程，尚无 Worker lease、鉴权、限流和多租户隔离。
- `create_demo_app()` 只注册确定性的 Demo Agent。

### 测试与验收

2026-08-13 验证结果：`19 passed`。

```powershell
cd D:\AICoding\Agent
python -m pytest -p no:cacheprovider
python scripts/check_docs.py
```

测试覆盖健康检查、创建和查询 Run、历史事件顺序、SSE 编码、断点续传、生命周期接口和 404 行为。

### 后续计划

进入 v0.4：增加真实模型 Provider 的 token streaming，并保持 Runtime Event 与 Model Token Stream 的协议边界。

---

<a id="e2026-08-13-001"></a>
## E2026-08-13-001：完成可靠单 Agent 执行 v0.2

- **完成时间**：2026-08-13
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `README.md`
  - `pyproject.toml`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/tools.py`
  - `tests/test_runtime.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0005-tool-execution-idempotency.md`
- **关联 commit**：`a765fa6`
- **关联 ADR**：[ADR-0005](./adr/0005-tool-execution-idempotency.md)

### 变更摘要

将 v0.1 的 Checkpoint 级恢复深化为可追踪的 Step / ToolExecution 执行模型，增加 SQLite schema migration、状态与事件原子提交、工具幂等恢复、多工具审批队列、活动任务取消和未知副作用人工处置。

### 系统架构

- Runtime Kernel 新增 `Step` 和 `ToolExecution` 两级持久化执行对象。
- SQLite 新增 `schema_migrations`、`steps`、`tool_executions` 表。
- 状态、工具结果、Checkpoint 和 Event 可以在同一事务中提交。
- 恢复流程优先处理未完成 Step，再决定是否请求下一次模型响应。
- 副作用工具的执行结果无法确认时进入 `unknown`，Run 自动暂停。

### 实现方式

- `ToolExecution` 使用 `run_id + step_id + tool_call_id` 生成稳定 idempotency key。
- 已完成、失败或拒绝的工具执行恢复时直接重建 tool message，不再调用 handler。
- 模型一次返回的全部工具调用先持久化为有序队列，审批后继续处理后续调用。
- `CancellationToken` 注入 `ToolContext`，`Runtime.cancel()` 同时取消活动 asyncio Task。
- SQLite 通过编号迁移兼容 v0.1 数据库，并为审批记录补充 ToolExecution 关联。

### 当前功能

- 支持 Step 和 ToolExecution 查询与恢复。
- 支持工具结果幂等复用。
- 支持多个工具调用逐项审批和继续执行。
- 支持副作用结果未知时暂停并由 `resolve_unknown_tool()` 处置。
- 支持 Run 状态与 Event、工具结果与 Checkpoint 的事务一致性。
- 支持模型调用和异步工具的主动取消传播。

### 已知限制

- 外部系统的真正幂等仍需工具 handler 使用 `ToolContext.idempotency_key` 配合实现。
- 同步阻塞 handler 无法被 Python 强制安全中断，只能在返回前后检查取消信号。
- 未实现 Worker lease、heartbeat 和跨节点所有权。
- ToolExecution 当前按顺序执行，尚未支持安全的并行工具调度。
- `unknown` 副作用需要应用层或 CLI 提供更完整的人工处置界面。

### 测试与验收

2026-08-13 验证结果：`15 passed`。

```powershell
cd D:\AICoding\Agent
python -m pytest -p no:cacheprovider
python scripts/check_docs.py
agent-runtime demo "19 * 23"
```

测试覆盖：

- v0.1 SQLite schema 自动迁移到 v0.2。
- 多工具审批后继续执行。
- 已完成工具恢复时不重复执行。
- 副作用工具运行中重启后标记 unknown 并暂停。
- 状态与事件事务在故障注入时整体回滚。
- 活动异步工具取消传播和 cancelled 状态持久化。
- unknown 工具经人工确认后继续恢复。
- 既有状态机、工具校验、workspace 安全和审批回归。

### 后续计划

进入 v0.3：在保持 Runtime Core 无 HTTP 依赖的前提下增加 FastAPI Run API 和 SSE 持久化事件接口。
---

<a id="e2026-08-11-002"></a>
## E2026-08-11-002：建立可追溯演进文档体系

- **完成时间**：2026-08-11
- **状态**：✅ stable
- **类型**：governance
- **影响范围**：
  - `README.md`
  - `docs/README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/templates/change-entry.md`
  - `docs/templates/adr.md`
  - `scripts/check_docs.py`
  - `.github/workflows/quality.yml`
  - `.github/PULL_REQUEST_TEMPLATE.md`
- **关联 commit**：`b4adc90`
- **关联 ADR**：不需要；该变更建立治理流程，不改变 Runtime 公共接口、数据模型或安全边界。

### 变更摘要

建立当前状态、当前架构、倒序演进时间线、ADR、模板和自动检查职责分离的文档体系。

### 系统架构

Runtime 代码架构不变；新增围绕代码事实的文档治理层和 CI 门禁。

### 实现方式

- `CURRENT.md` 维护当前能力和限制。
- `ARCHITECTURE.md` 维护当前实现结构和语义。
- `CHANGELOG.md` 使用 Change ID 按完成时间倒序记录演进。
- ADR 记录影响公共接口、数据、可靠性或安全性的决策。
- `scripts/check_docs.py` 校验结构、格式、时间、路径、链接和 Git 变更门禁。
- CI 同时执行文档检查和测试。

### 当前功能

- 可以从单一入口定位当前状态、当前架构、历史记录和 ADR。
- 每个状态项均关联 Change ID。
- 核心 Runtime 代码变化时可以检查是否同步修改演进记录。

### 已知限制

- 自动检查只能识别文件级变更，是否需要 ADR 仍需开发者根据模板判断。

### 测试与验收

```powershell
python scripts/check_docs.py
pytest
```

### 后续计划

后续每项功能按 Change ID、实现、测试、文档和 ADR 流程演进。

---

<a id="e2026-08-11-001"></a>
## E2026-08-11-001：完成单 Agent Runtime MVP

- **完成时间**：2026-08-11
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/providers.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/sdk.py`
  - `src/agent_runtime/cli.py`
  - `tests/test_domain.py`
  - `tests/test_runtime.py`
  - `tests/test_tools.py`
- **关联 commit**：`b4adc90`
- **关联 ADR**：[ADR-0001](./adr/0001-runtime-kernel.md)、[ADR-0002](./adr/0002-model-provider-protocol.md)、[ADR-0003](./adr/0003-sqlite-event-checkpoint.md)、[ADR-0004](./adr/0004-tool-security-boundary.md)

### 变更摘要

完成第一版可持久化单 Agent Runtime，实现模型决策、工具执行、状态持久化、事件追踪、人工审批和失败收敛闭环。

### 系统架构

- 引入 Runtime Kernel 和明确的 Run 状态机。
- 引入统一 Model Provider 接口。
- 引入 Tool Registry 和受控工具执行。
- 引入 SQLite Event Log、Checkpoint 和 Approval。
- 引入 Python SDK 和 CLI。

### 实现方式

- Python 标准库实现核心 Runtime。
- SQLite 保存 Run、Event、Checkpoint 和 Approval。
- Mock Provider 用于测试和本地 Demo。
- OpenAI-compatible Provider 用于真实模型接入。
- 工具执行支持 schema 校验、超时和 workspace 路径限制。

### 当前功能

- 支持单 Agent 执行和工具调用。
- 支持持久化事件流读取。
- 支持暂停、恢复和取消。
- 支持高风险工具人工审批。
- 支持从最近 Checkpoint 恢复消息历史。
- 支持 Python SDK 和 CLI。

### 已知限制

- 暂无 FastAPI / SSE API。
- 暂无多 Agent 和分布式 Worker。
- 暂无长期记忆。
- 暂无容器级代码沙箱。
- 尚无数据库迁移和分布式幂等协议。

### 测试与验收

2026-08-11 验证结果：`8 passed`。

```powershell
cd D:\AICoding\Agent
python -m pip install pytest pytest-asyncio
pytest
agent-runtime demo "19 * 23"
```

### 后续计划

深化单 Agent 执行可靠性，再增加 FastAPI 和 SSE 事件接口。

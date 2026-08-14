# Agent Runtime Evolution Log

> 规则：按完成时间倒序排列。每条记录必须关联代码范围、测试和 Git commit；提交前 commit 可暂记为 `pending`，合并前必须补全。

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
- **关联 commit**：`f2987c8`
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

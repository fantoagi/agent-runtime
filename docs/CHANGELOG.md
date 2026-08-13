# Agent Runtime Evolution Log

> 规则：按完成时间倒序排列。每条记录必须关联代码范围、测试和 Git commit；提交前 commit 可暂记为 `pending`，合并前必须补全。

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
- **关联 commit**：`pending`
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

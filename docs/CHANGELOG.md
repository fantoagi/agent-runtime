# Agent Runtime Evolution Log

> 规则：按完成时间倒序排列。每条记录必须关联代码范围、测试和 Git commit；提交前 commit 可暂记为 `b4adc90`，合并前必须补全。

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

- 仓库尚无首次 Git commit，因此关联 commit 暂为 `b4adc90`。
- 自动检查只能识别文件级变更，是否需要 ADR 仍需开发者根据模板判断。

### 测试与验收

```powershell
python scripts/check_docs.py
pytest
```

### 后续计划

首次提交后回填 baseline commit；之后每项功能按 Change ID、实现、测试、文档和 ADR 流程演进。

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

增加 FastAPI 和 SSE 事件接口，并在进入实现前创建新的 Change ID。

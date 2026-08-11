# Agent Runtime 当前状态

- **当前版本**：`0.1.0`
- **当前里程碑**：单 Agent Runtime MVP + 可追溯演进文档体系
- **Runtime 构建完成时间**：2026-08-11（Asia/Shanghai）
- **文档体系构建完成时间**：2026-08-11（Asia/Shanghai）
- **当前代码基线 commit**：`pending`（仓库尚未创建首次提交）
- **最近演进记录**：[E2026-08-11-002](./CHANGELOG.md#e2026-08-11-002)

## 状态定义

| 标记 | 含义 |
| --- | --- |
| ✅ stable | 已完成且有测试或可重复验收方式 |
| 🧪 experimental | 已实现，但接口或行为仍可能变化 |
| 🚧 partial | 只完成部分能力 |
| 📋 planned | 已进入规划但尚未实现 |
| ⛔ unsupported | 当前明确不支持 |

## 已实现能力

| 状态 | 能力 | 当前说明 | 追溯记录 |
| --- | --- | --- | --- |
| ✅ stable | 单 Agent Runtime Kernel | 完成有界模型/工具循环、结果和失败收敛 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Run 状态机 | 支持 created、running、waiting、paused 和终态迁移 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Model Provider 抽象 | Runtime 不依赖具体模型厂商响应结构 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Mock Provider | 支持确定性本地 Demo 和测试 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 🧪 experimental | OpenAI-compatible Provider | 已实现 Chat Completions 兼容调用，尚缺真实端点集成测试 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Tool Registry | 支持工具注册、查找和执行 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | 工具参数校验 | 支持基础 JSON-schema 风格 required/type/enum 校验 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | SQLite 持久化 | 保存 Run、Event、Checkpoint 和 Approval | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Event Log | 每个 Run 使用单调递增 sequence 保存事件 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Checkpoint | 保存消息历史、步骤和工具调用计数 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Pause / Resume / Cancel | 支持运行生命周期控制和进程内恢复入口 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | 人工审批 | 高风险工具可进入等待审批状态并在批准/拒绝后恢复 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Python SDK | 提供 Runtime、Agent 和本地 Demo 构造接口 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | CLI | 支持 demo、run 查询、事件、暂停、恢复、取消和审批 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | 演进文档体系 | 当前事实、时间线、ADR、模板和自动检查职责分离 | [E2026-08-11-002](./CHANGELOG.md#e2026-08-11-002) |

## 部分实现或实验能力

| 状态 | 能力 | 当前说明 | 追溯记录 |
| --- | --- | --- | --- |
| 🚧 partial | Artifact Store | 可以受限写入文本产物，但尚未集成通用大结果转存策略 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 🧪 experimental | 事件流消费 | `Runtime.stream()` 轮询持久化事件；尚非模型 token 原生流 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 🧪 experimental | 崩溃恢复 | 支持从最新 Checkpoint 恢复，但尚未提供跨进程 Worker 租约和工具幂等协议 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |

## 计划能力

| 状态 | 能力 | 当前说明 | 追溯记录 |
| --- | --- | --- | --- |
| 📋 planned | FastAPI / SSE API | 对外创建 Run、控制生命周期并订阅事件 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 📋 planned | 多 Agent 编排 | 委派、并行执行和结果汇聚 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 📋 planned | 分布式 Worker | 队列、租约、幂等和跨节点恢复 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 📋 planned | 长期记忆 | 检索、记忆生命周期和数据治理 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 📋 planned | Docker 代码沙箱 | 隔离代码、终端和依赖执行 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| 📋 planned | 多租户和权限 | 身份、RBAC、配额和审计隔离 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |

## 当前明确不支持

| 状态 | 能力 | 原因 | 追溯记录 |
| --- | --- | --- | --- |
| ⛔ unsupported | 任意宿主机 Shell | 当前安全边界只允许注册后的受控工具 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ⛔ unsupported | 不受限文件访问 | 文件工具必须限制在 Runtime workspace 内 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ⛔ unsupported | 自动安装依赖 | 尚未引入容器沙箱和依赖策略 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |

## 当前已知限制

- `OpenAICompatibleProvider` 当前不是 token 级流式实现。
- `Runtime.stream()` 通过轮询 SQLite 事件工作，不是跨进程消息总线。
- SQLite schema 尚无版本号和迁移框架。
- 工具完成记录与副作用之间尚无分布式幂等保障。
- 单次模型返回多个工具调用并遇到审批时，只保证被审批调用的恢复闭环，尚未维护完整待执行调用队列。
- CLI 重新启动后只能恢复已注册的 Agent；当前 CLI 默认只注册 Demo Agent。
- 本地 `.venv` 默认只安装 runtime 包，运行测试前需要安装 `pytest` 和 `pytest-asyncio`。

## 当前测试状态

- **最近验证日期**：2026-08-11
- **结果**：`8 passed`
- **覆盖范围**：状态机、工具参数校验、workspace 越界保护、异步工具、工具闭环、审批通过和审批拒绝。
- **文档检查**：使用 `python scripts/check_docs.py`。

## 当前运行方式

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install pytest pytest-asyncio
agent-runtime demo "19 * 23"
python scripts/check_docs.py
pytest
```

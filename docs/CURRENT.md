# Agent Runtime 当前状态

- **当前版本**：`0.4.0`
- **当前里程碑**：真实模型 Provider 与 Token Streaming v0.4
- **Runtime 构建完成时间**：2026-08-14（Asia/Shanghai）
- **文档体系构建完成时间**：2026-08-11（Asia/Shanghai）
- **当前代码基线 commit**：`444dec4`
- **最近演进记录**：[E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001)

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
| ✅ stable | OpenAI-compatible Provider | 支持 Chat Completions 非流式与 SSE 流式调用；真实端点仍需按部署环境验证 | [E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001) |
| ✅ stable | Tool Registry | 支持工具注册、查找和执行 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | 工具参数校验 | 支持基础 JSON-schema 风格 required/type/enum 校验 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | SQLite 持久化 | 通过 schema migration 保存 Run、Event、Checkpoint、Approval、Step 和 ToolExecution | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Event Log | 每个 Run 使用单调递增 sequence，并支持状态和事件原子提交 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Model Token Streaming | Provider 可按增量输出文本和 Tool Call，Runtime 持久化 `model.delta` 并最终合并为完整响应 | [E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001) |
| ✅ stable | FastAPI / SSE API | HTTP Run lifecycle、持久化 Runtime Event 和模型增量 SSE | [E2026-08-13-002](./CHANGELOG.md#e2026-08-13-002) |
| ✅ stable | Step / ToolExecution / Checkpoint | 持久化模型步骤、工具队列、结果和恢复消息 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Pause / Resume / Cancel | 支持生命周期控制、活动 Task 取消和工具协作式取消 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | 人工审批 | 高风险工具可等待审批，多个工具调用可逐项恢复 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Python SDK | 提供 Runtime、Agent 和本地 Demo 构造接口 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | CLI | 支持 demo、run 查询、事件、暂停、恢复、取消、审批和 unknown 工具处置 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | 演进文档体系 | 当前事实、时间线、ADR、模板和自动检查职责分离 | [E2026-08-11-002](./CHANGELOG.md#e2026-08-11-002) |

## 部分实现或实验能力

| 状态 | 能力 | 当前说明 | 追溯记录 |
| --- | --- | --- | --- |
| 🚧 partial | Artifact Store | 可以受限写入文本产物，但尚未集成通用大结果转存策略 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | 事件流消费 | `Runtime.stream()` 轮询持久化事件；模型 token 增量通过 `model.delta` 事件输出 | [E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001) |
| 🧪 experimental | 崩溃恢复 | 支持未完成 Step 恢复、完成工具跳过和未知副作用暂停；尚无 Worker 租约 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |

## 计划能力

| 状态 | 能力 | 当前说明 | 追溯记录 |
| --- | --- | --- | --- |
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

- `OpenAICompatibleProvider` 的 SSE 流式解析已实现，但不同厂商的 Tool Call 增量格式仍需持续兼容验证。
- `Runtime.stream()` 通过轮询 SQLite 事件工作，不是跨进程消息总线。
- 当前仅实现 SQLite 基础迁移，尚未提供在线回滚和多节点迁移协调。
- 工具 idempotency key 已持久化；外部系统真正幂等仍需由工具 handler 配合实现。
- 多工具调用已持久化为 ToolExecution 队列并可逐项审批；尚未支持并行工具调度。
- CLI 重新启动后只能恢复已注册的 Agent；当前 CLI 默认只注册 Demo Agent。
- 本地 `.venv` 默认只安装 runtime 包，运行测试前需要安装 `pytest` 和 `pytest-asyncio`。

## 当前测试状态

- **最近验证日期**：2026-08-14
- **结果**：`25 passed`
- **覆盖范围**：状态机、工具校验、workspace 安全、审批、恢复与幂等、事务回滚、FastAPI / SSE、Mock token streaming、OpenAI-compatible SSE 解析、增量 Tool Call 拼接和最终 Checkpoint。
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


### FastAPI / SSE

安装 API 可选依赖并启动本地 Demo API：

```powershell
python -m pip install -e .[api]
uvicorn agent_runtime.api.app:app --reload
```

主要接口：`GET /health`、`POST /runs`、`GET /runs/{run_id}`、`GET /runs/{run_id}/events`、`GET /runs/{run_id}/events/stream`、Run 生命周期控制和审批处理。v0.4 的模型增量使用 `model.delta` 事件进入同一 SSE 流。

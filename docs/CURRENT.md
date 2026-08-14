# Agent Runtime 当前状态

- **当前版本**：`0.6.0`
- **当前里程碑**：持久化多 Agent 编排基础 v0.6
- **Runtime 构建完成时间**：2026-08-14（Asia/Shanghai）
- **文档体系构建完成时间**：2026-08-11（Asia/Shanghai）
- **当前代码基线 commit**：`pending`
- **最近演进记录**：[E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007)

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
| ✅ stable | Agent Registry | 注册、发现并校验可委派 Agent；拒绝同名冲突定义 | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| ✅ stable | Parent / Child RunRelation | SQLite 持久化 Parent、Child、Root、关系类型和稳定 delegation key | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| ✅ stable | Runtime 委派 | `Runtime.delegate()` 通过正式 Runtime 路径执行 Child Run，并支持幂等复用 | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| ✅ stable | Sequential Workflow | 支持 Planner → Worker → Reviewer 顺序执行和结果传递 | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| ✅ stable | Parallel Workflow | 支持并发上限、超时以及 all、best_effort、first_success 汇聚 | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| ✅ stable | Parent 取消传播 | Parent Cancel 递归取消活动 Child，终态 Child 保持不变 | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| ✅ stable | Multi-Agent Trace / Eval | 支持 Trace Tree、多 Agent Metrics 和 Child 数量 Eval | [E2026-08-14-007](./CHANGELOG.md#e2026-08-14-007) |
| ✅ stable | Run 状态机 | 支持 created、running、waiting、paused 和终态迁移 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Model Provider 抽象 | Runtime 不依赖具体模型厂商响应结构 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | Mock Provider | 支持确定性本地 Demo 和测试 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | OpenAI-compatible Provider | 支持 Chat Completions 非流式与 SSE 流式调用；真实端点仍需按部署环境验证 | [E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001) |
| ✅ stable | Tool Registry | 支持工具注册、查找和执行 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | 工具参数校验 | 支持基础 JSON-schema 风格 required/type/enum 校验 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | SQLite 持久化 | 通过 schema migration 保存 Run、RunRelation、Event、Checkpoint、Approval、Step 和 ToolExecution | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Event Log | 每个 Run 使用单调递增 sequence，并支持状态和事件原子提交 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Model Token Streaming | Provider 可按增量输出文本和 Tool Call，Runtime 持久化 `model.delta` 并最终合并为完整响应 | [E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001) |
| ✅ stable | FastAPI / SSE API | HTTP Run lifecycle、持久化 Runtime Event 和模型增量 SSE | [E2026-08-13-002](./CHANGELOG.md#e2026-08-13-002) |
| ✅ stable | Learning Console | 一条命令启动本地可视化学习环境；事件按 Run / Model / Tool / Approval / State 泳道实时展示；未运行时仅显示紧凑提示，有事件后空状态完全隐藏 | [E2026-08-14-006](./CHANGELOG.md#e2026-08-14-006) |
| ✅ stable | Run Trace | 每个 Run 自动生成 `trace_id`，并从持久化事件派生 Run、Model、Tool、Approval Span | [E2026-08-14-002](./CHANGELOG.md#e2026-08-14-002) |
| ✅ stable | Metrics / Prometheus | 从 SQLite 历史派生 Run 状态、事件、延迟、模型/工具次数和 token 指标 | [E2026-08-14-002](./CHANGELOG.md#e2026-08-14-002) |
| ✅ stable | Eval Runner | 支持状态、精确匹配、包含判断、通过率和 JSON Artifact 报告 | [E2026-08-14-002](./CHANGELOG.md#e2026-08-14-002) |
| ✅ stable | Step / ToolExecution / Checkpoint | 持久化模型步骤、工具队列、结果和恢复消息 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Pause / Resume / Cancel | 支持生命周期控制、活动 Task 取消和工具协作式取消 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | 人工审批 | 高风险工具可等待审批，多个工具调用可逐项恢复 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |
| ✅ stable | Python SDK | 提供 Runtime、Agent 和本地 Demo 构造接口 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | CLI | 支持 lab、demo、Run 控制、审批、unknown 工具处置、Trace、Metrics 和内置 Eval Suite | [E2026-08-14-004](./CHANGELOG.md#e2026-08-14-004) |
| ✅ stable | 演进文档体系 | 当前事实、时间线、ADR、模板和自动检查职责分离 | [E2026-08-11-002](./CHANGELOG.md#e2026-08-11-002) |
| ✅ stable | 演进路线图 | 通过 `ROADMAP.md` 记录 v0.6～v1.0 的顺序、范围、非目标、验收重点和维护规则 | [E2026-08-14-003](./CHANGELOG.md#e2026-08-14-003) |

## 部分实现或实验能力

| 状态 | 能力 | 当前说明 | 追溯记录 |
| --- | --- | --- | --- |
| 🚧 partial | Artifact Store | 可以受限写入文本产物，但尚未集成通用大结果转存策略 | [E2026-08-11-001](./CHANGELOG.md#e2026-08-11-001) |
| ✅ stable | 事件流消费 | `Runtime.stream()` 轮询持久化事件；模型 token 增量通过 `model.delta` 事件输出 | [E2026-08-14-001](./CHANGELOG.md#e2026-08-14-001) |
| 🧪 experimental | 崩溃恢复 | 支持未完成 Step 恢复、完成工具跳过和未知副作用暂停；尚无 Worker 租约 | [E2026-08-13-001](./CHANGELOG.md#e2026-08-13-001) |

## 计划能力

| 状态 | 能力 | 当前说明 | 追溯记录 |
| --- | --- | --- | --- |
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
- AgentRegistry 仍是进程内定义目录；恢复 Workflow 时应用必须重新注册相同 Agent 和 Workflow 定义。
- Workflow 不支持通用 `Runtime.pause()` 或仅凭 Run ID 自动恢复；进程重启后必须用原 Workflow 定义和 `parent_run_id` 继续执行。
- 本地 `.venv` 默认只安装 runtime 包，运行测试前需要安装 `pytest` 和 `pytest-asyncio`。
- Parent/Child Trace Tree 和 Metrics 从本地 SQLite 派生，尚未接入 OpenTelemetry Collector 或外部时序数据库。
- EvalRunner 和 WorkflowEvalRunner 当前顺序执行用例，内置评估器尚无 LLM-as-a-Judge。
- Learning Console 当前只有 4 个单 Run 确定性场景；Snapshot 已返回 Trace Tree，但尚无专用跨 Run 树形画布。
- Learning Console 面向本地单用户学习，没有认证、授权和多租户隔离。

## 当前测试状态

- **最近验证日期**：2026-08-14
- **结果**：`44 passed`
- **覆盖范围**：状态机、工具安全、审批、恢复与幂等、FastAPI / SSE、Token Streaming、Parent/Child RunRelation、顺序/并行 Workflow、并发限制、汇聚策略、取消传播、Trace Tree、Multi-Agent Metrics、Workflow Eval、Learning Console Snapshot 和自动验收。
- **文档检查**：使用 `python scripts/check_docs.py`。

## 当前运行方式

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install pytest pytest-asyncio
agent-runtime lab
# 浏览器打开 http://127.0.0.1:8000/lab

agent-runtime demo "19 * 23"
agent-runtime workflow demo "设计一个可靠的恢复机制"
python scripts/check_docs.py
pytest
```


### FastAPI / SSE

安装 API 可选依赖并启动本地 Demo API：

```powershell
python -m pip install -e .[api]
uvicorn agent_runtime.api.app:app --reload
```

主要接口：`GET /health`、`GET /agents`、`POST /runs`、`GET /runs/{run_id}`、`GET /runs/{run_id}/events`、`GET /runs/{run_id}/events/stream`、`GET /runs/{run_id}/relations`、`GET /runs/{run_id}/trace/tree`、Run 生命周期控制、委派和审批处理。v0.4 的模型增量使用 `model.delta` 事件进入同一 SSE 流。


### Observability / Evals

```powershell
agent-runtime observe metrics
agent-runtime observe trace <run-id>
agent-runtime eval demo
```

API 入口：`GET /observability/metrics`、`GET /observability/metrics/prometheus`、`GET /runs/{run_id}/trace` 和 `GET /runs/{run_id}/trace/tree`。Python API 可以使用 `ObservabilityService`、`EvalRunner` 和 `WorkflowEvalRunner`。

### Multi-Agent Workflow

```powershell
agent-runtime workflow demo "设计一个可靠的恢复机制"
agent-runtime observe trace-tree <parent-or-child-run-id>
```

详细使用方式见 [MULTI_AGENT.md](./MULTI_AGENT.md)。
### Learning Console

安装 API 依赖后一条命令启动：

```powershell
python -m pip install -e .[api]
agent-runtime lab
```

默认地址：`http://127.0.0.1:8000/lab`。首批提供纯文本、Tool Calling、Token Streaming 和 Human Approval 四个确定性场景。详细操作见 [LEARNING.md](./LEARNING.md)。

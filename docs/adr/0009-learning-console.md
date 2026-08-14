# ADR-0009：Learning Console 作为 Runtime 外部教学 Adapter

- **状态**：Accepted
- **日期**：2026-08-14
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-14-004](../CHANGELOG.md#e2026-08-14-004)

## 背景

v0.5 已具备 Run、Event、Step、ToolExecution、Checkpoint、Approval、SSE、Trace、Metrics 和 Eval，但初学者需要通过多条 CLI/PowerShell 命令拼接这些信息，难以直观看到一次 Agent Run 如何推进。项目需要一个本地可视化学习入口，同时必须避免为了演示而建立第二套执行逻辑或把 UI 状态侵入 Runtime Kernel。

## 决策

1. Learning Console 位于 `agent_runtime.lab`，作为 FastAPI Adapter 挂载到 `/lab`。
2. 每个学习场景必须通过真实 `Runtime.start()`、Provider、ToolRegistry 和 SQLiteStore 执行；禁止用前端定时器伪造 Runtime Event。
3. 场景可以使用不同的确定性 Provider 和 AgentDefinition，但共享同一个持久化 Store，使 Run、Event、Trace 和 Metrics 保持统一事实来源。
4. 页面通过已有 `/runs/{run_id}/events/stream` 感知事件，并通过 Lab Snapshot API 读取 Run、Checkpoint、Step、ToolExecution、Approval、Trace 和 Metrics。
5. 事件回放只改变浏览器的展示游标，不改变 Runtime 状态机，也不提供内核级单步暂停。
6. 教学解释、颜色、源码映射、场景预期和自动验收属于 Lab 模块，不进入 Runtime Kernel 和领域模型。
7. 前端使用静态 HTML、CSS 和 Vanilla JavaScript，不引入 Node.js 构建链或 SPA 框架。

## 影响

### 优点

- 初学者可以用一条命令启动并在一个页面学习完整执行链路。
- 页面展示与真实 Runtime 持久化事实一致，可直接映射到代码和测试。
- 不改变 Run 状态机、Event Envelope、Provider Protocol 或恢复语义。
- 复用 FastAPI、SSE、Observability 和 SQLite，避免双写和重复基础设施。
- 静态前端易于阅读、调试和随 Runtime 功能同步演进。

### 代价

- 不同确定性场景由同进程中的多个 Runtime 实例承载，需要根据 Run metadata 路由审批恢复。
- Snapshot API 会聚合多个 Store 查询，只适合本地教学和低并发使用。
- 无前端框架意味着复杂交互需要手工维护 DOM 状态。
- 回放不是内核断点调试，不能在任意 Python 指令处暂停。

## 被放弃的方案

- 不使用预制 JSON 事件或纯前端动画，因为它们无法验证真实 Runtime 行为。
- 不修改 Runtime Kernel 增加“教学模式”或 UI 回调，避免污染执行语义。
- 不在 v0.5.1 引入 React、Vue、Node.js、Webpack 或独立前端仓库，避免增加第二套学习和构建工具链。
- 不实现完整 Web 管理控制台、多用户认证或 Workflow Designer，因为这些不属于本地学习入口。

## 后续约束

新增学习场景必须通过真实 Runtime 路径，并提供预期事件、学习点、自动验收和测试。未来 v0.6 展示 Parent/Child Run 时，应在 Lab Adapter 中增加 Trace Tree 和关系视图，而不是在 Runtime Kernel 中加入展示逻辑。任何将 Learning Console 用于远程或多用户部署的方案都必须新增认证、授权和安全边界 ADR。

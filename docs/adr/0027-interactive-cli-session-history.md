# ADR-0027：Interactive CLI 作为 Runtime Adapter 并显式加载 Session 历史

- **状态**：Accepted
- **日期**：2026-08-16
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-16-006](../CHANGELOG.md#e2026-08-16-006)

## 背景

v0.8.1 已能通过配置启动本地 FastAPI Runtime，但人工使用仍依赖 PowerShell 命令、HTTP 请求或 Learning Console。用户需要类似 Claude Code、Codex 的直接终端交互体验，同时不能建立第二套执行循环，也不能让终端 UI 破坏 Run、Event、Checkpoint、Approval 和恢复合同。

现有 Session 只负责关联 Run；Runtime 默认不会自动把前一轮问答加入下一轮模型消息。如果直接修改所有 Session Run 的默认行为，会改变 Python SDK 和 HTTP API 的现有语义，并可能把长期历史无界注入模型上下文。

## 决策

### 1. Interactive CLI 是外部 Adapter

新增 `agent_runtime.interactive`，由 Prompt Toolkit 负责异步输入与本地输入历史，由 Rich 负责面向人的 Event 渲染。Interactive CLI 只调用 Runtime、Store 和已有领域接口，不把终端提示符、颜色、Slash Command 或输入历史加入 Runtime Kernel。

### 2. v0.8.2 使用 embedded Runtime

`agent-runtime chat` 复用 `create_configured_local_runtime()`，在当前进程中创建 Runtime，并获取与 `serve` 相同的 `LocalRuntimeLock`。同一状态目录下 `chat` 与 `serve` 互斥，继续维持单执行 Owner。当前不先实现 HTTP daemon attach Client。

### 3. 每轮输入对应独立 Run，同一对话共享 Session

CLI 启动时创建、继续或恢复持久化 Session；每条用户输入通过 `Runtime.submit()` 创建一个新 Run。Run、Event、Checkpoint、Approval 和 ToolExecution 保持原有持久化和恢复语义。

### 4. Session 历史必须显式启用

只有 Run metadata 包含 `include_session_history=true` 时，Runtime 才装配历史。Interactive CLI 默认同时写入 `session_history_limit=20`。Runtime 只选择同 Session、同 Agent、已完成且存在 final result 的历史 Run，并重建：

```text
previous user input
previous final assistant result
...
current user input
```

限制最少 1、最多 100 个历史 Run。默认 SDK/API 行为不变。

### 5. 不回放旧 Tool 中间消息

v0.8.2 不把历史 Tool Call、Tool Result、Approval、Checkpoint 或内部 Event 重新注入模型。这样避免把执行中间事实误当作稳定聊天记录，也避免因 Tool schema 或实现变化导致历史无法重放。需要更完整的会话压缩或 Tool transcript 时，应单独设计 Context/Conversation 协议。

### 6. 历史装配可审计

每次显式装配 Session 历史时写入 `session.history.loaded` durable Event，记录 Session ID、历史 Run 数、消息数和限制。Prompt Toolkit 的 `<state_dir>/cli-history` 仅是本地输入便利功能，不属于 Runtime durable facts。

### 7. 终端流以 Runtime Event 为事实来源

Interactive CLI 消费 `Runtime.stream()`：`model.delta` 实时追加，Tool 和 Approval 使用结构化状态展示，终态从持久化 AgentRun 读取。`--print` 只改变显示方式，不改变执行路径。

## 影响

### 优点

- 人工用户可以通过一个命令直接使用真实 Runtime。
- 终端、FastAPI、Learning Console 和 Python SDK 继续共享同一个 Runtime Kernel。
- 多轮对话可跨进程恢复，且不改变旧 API 默认行为。
- Session 历史装配有界并可通过 Event 审计。
- Tool Approval 和取消继续使用已有可靠性语义。

### 代价

- `chat` 不能与同状态目录的 `serve` 同时运行。
- 当前每轮是新 Run，历史上下文需要重新装配并消耗模型 token。
- 只保留 final assistant result 会丢失旧 Tool 中间推理轨迹。
- Prompt Toolkit 和 Rich 成为核心安装依赖。

## 被放弃的方案

### 方案 A：CLI 直接连接 FastAPI

优点是可与 daemon 共存；但需要补齐 Client、认证/所有权、SSE 重连、Approval 和版本协商，超出本地最短路径目标，因此延期。

### 方案 B：修改所有 Session Run 默认自动带历史

会破坏现有 SDK/API 兼容性，并可能造成无界上下文增长，因此不采用。

### 方案 C：把完整旧 Checkpoint 消息直接拼到新 Run

旧 Checkpoint 可能包含 Tool 中间消息、过时 schema 和大结果，边界不稳定，因此 v0.8.2 不采用。

### 方案 D：单个 Run 无限承载多轮输入

这会改变 Run 终态和恢复语义，使已完成 Run 再次执行，不符合当前领域模型，因此不采用。

## 后续约束

- 新终端能力必须继续作为 Adapter，不得复制 Runtime 执行状态机。
- 修改 `include_session_history`、历史筛选、消息顺序或 `session.history.loaded` Event 时必须更新 ADR。
- 如果引入 HTTP attach/daemon 模式，必须明确 Runtime 所有权、版本协商、断线恢复和 Approval 语义，并新增 ADR。
- 如果需要 Tool transcript、摘要记忆或 token-aware 对话压缩，应复用 ContextBuilder/Memory 边界，不在 Shell 内私自拼接。

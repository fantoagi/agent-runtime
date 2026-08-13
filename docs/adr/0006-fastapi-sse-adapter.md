# ADR-0006：FastAPI 作为 HTTP Adapter，SSE 输出持久化 Runtime Event

- **状态**：Accepted
- **日期**：2026-08-13
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-13-002](../CHANGELOG.md#e2026-08-13-002)

## 背景

v0.2 已经具备可恢复的 Runtime、SQLite Event Log 和 `Runtime.stream()`，但只能通过 Python SDK 或 CLI 使用。需要一个适合初学者和 Web 客户端的 HTTP 入口，同时不能让 HTTP 框架侵入 Runtime Core。

## 决策

1. 使用 FastAPI 实现独立的 `agent_runtime.api` Adapter。
2. API 通过 `Runtime` 和 `SQLiteStore` 调用核心能力，不直接操作 SQLite connection，也不复制执行状态机。
3. 使用 `POST /runs` 创建并异步启动 Run；查询接口从持久化 Store 读取事实状态。
4. 使用 `GET /runs/{run_id}/events/stream` 输出持久化 Runtime Event。每条 SSE 的 `id` 使用 Run 内单调递增的 Event sequence，并接受 `after_sequence` 实现断点续传。
5. v0.3 的 SSE 不承诺模型 token streaming；模型流式协议作为后续 Provider 能力单独演进。
6. FastAPI、Uvicorn 和 HTTP 测试依赖作为 `api` optional extra，不让 Runtime Core 默认依赖 HTTP 框架。

## 影响

### 优点

- CLI、SDK、HTTP 和未来 WebSocket 共享同一个 Runtime Kernel。
- Event sequence 同时成为审计顺序、列表排序和 SSE 恢复游标。
- HTTP 客户端可以查询状态、订阅事件并完成审批，而不需要理解 SQLite。
- API 依赖可选，保持核心包轻量。

### 代价

- 当前 SSE 使用 SQLite polling，不是跨进程消息 broker。
- 连接在 Run 完成、暂停或等待审批时结束，客户端需要重新请求或在审批后续订。
- `POST /runs` 的异步任务仍属于当前进程，尚无跨进程 Worker lease。

## 被放弃的方案

- 让 FastAPI 直接实现模型调用、工具执行和状态转换。
- 通过内存队列提供事件流而不读取持久化 Event Log。
- 在 v0.3 同时引入 token streaming、多 Agent 和分布式 Worker。

## 后续约束

- 任何公共 HTTP 路径、请求/响应 schema 或 SSE Event schema 的不兼容变化，都必须新增或更新 ADR。
- SSE 必须保持 Event sequence 单调递增，并继续支持断点续传。
- 未来引入 token streaming 时，必须明确区分 `runtime event stream` 与 `model token stream`。

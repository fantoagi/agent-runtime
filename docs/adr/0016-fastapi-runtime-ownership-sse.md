# ADR-0016：FastAPI Runtime 所有权、Lifespan 与 SSE 长稳

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-007](../CHANGELOG.md#e2026-08-15-007)

## 背景

FastAPI 既可能接收外部 Runtime，也可能创建 Demo Runtime；所有权不明确会泄漏或错误关闭资源。SSE 还需处理空闲、慢客户端和断线恢复。

## 决策

`shutdown_runtime=False` 默认不取得所有权，Demo 显式为 true。health 读取实际状态。SSE 使用 durable event、comment heartbeat、Last-Event-ID，并在断线时取消 pending iterator。

## 影响

### 优点

资源责任明确，SSE 断线、重连和 Uvicorn shutdown 不留下后台任务。

### 代价

外部 Runtime 必须由调用方关闭，慢客户端吞吐受 SQLite 和网络速度限制。

## 被放弃的方案

总是关闭传入 Runtime 或为每个 SSE 客户端维护无界 Queue，分别破坏嵌入语义和资源上界。

## 后续约束

新增 Adapter 不得改变所有权默认值；heartbeat 不写 Event Log，恢复游标继续使用 durable sequence。

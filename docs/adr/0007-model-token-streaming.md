# ADR-0007：Model Provider 支持 Token Streaming，并映射为 Runtime Event

- **状态**：Accepted
- **日期**：2026-08-14
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-14-001](../CHANGELOG.md#e2026-08-14-001)

## 背景

v0.3 已经可以通过 SSE 读取持久化 Runtime Event，但模型响应仍然只能在 `complete()` 返回后一次性产生。真实模型通常以 SSE 或其他增量协议返回文本和 Tool Call，初学者也需要观察模型响应如何逐步形成。

## 决策

1. 保留已有 `ModelProvider.complete()`，新增可选的 `StreamingModelProvider.stream()`。第三方 Provider 不需要立即实现 streaming。
2. 使用 `ModelTokenDelta` 表达文本、Tool Call 参数片段、finish reason 和 usage。
3. Runtime 检测 Provider 是否提供 `stream()`：支持时写入 `model.stream.started`、多个 `model.delta` 和 `model.stream.completed`；不支持时回退到 `complete()`。
4. `model.delta` 作为持久化 Runtime Event 复用 v0.3 的事件查询和 SSE 接口，事件 `sequence` 继续作为 SSE `id`。
5. 所有增量结束后，Runtime 必须合并出完整 `ModelResponse`，再按原有逻辑持久化 assistant message、Step、ToolExecution 和 Checkpoint。

## 影响

### 优点

- CLI、SSE 和未来 WebSocket 可以观察模型增量。
- 事件可审计、可断点续传，且不需要新增第二套 token API。
- 旧 Provider 仍然可用，迁移成本低。
- Tool Call 的参数片段不会绕过现有工具校验和审批流程。

### 代价

- 每个 token 增量都持久化会增加 SQLite 写入量。
- 不同模型厂商的 SSE、usage 和 Tool Call 增量格式存在差异。
- `asyncio.to_thread` 包装标准库 HTTP 时，取消请求仍然是协作式的。

## 被放弃的方案

- 不新增独立 `/tokens/stream` API，避免 Runtime Event 流和 token 流出现两套断点语义。
- 不让 Runtime 直接依赖某一家模型 SDK；Provider 负责协议适配。
- 不在收到半截 Tool Call 参数时执行工具，必须等待完整响应并通过现有 schema 校验。

## 后续约束

任何改变 `ModelTokenDelta`、`model.delta` payload、Provider fallback 或 assistant message 合并语义的变更，都必须更新本 ADR 或新增 ADR。高吞吐场景如需将 token 增量改为非持久化实时通道，也必须重新评估审计和断点续传语义。

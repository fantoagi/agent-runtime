# ADR-0014：OpenAI-compatible Provider 异步传输与重试策略

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-005](../CHANGELOG.md#e2026-08-15-005)

## 背景

daemon Thread、urllib 和无界 Queue 难以可靠取消流式请求和管理连接池、背压、协议校验及重试。

## 决策

使用受管理的 `httpx.AsyncClient`；严格验证普通响应和 SSE。连接错误、408、429、5xx 可重试，确定性 4xx 不重试；优先 Retry-After，否则指数退避加 jitter。

## 影响

### 优点

连接生命周期可控，流读取受消费者背压，非法和截断 SSE 有明确错误。

### 代价

`httpx` 成为核心依赖，非标准响应会被拒绝。

## 被放弃的方案

线程读取 SSE 再经无界 Queue 转发，无法可靠关闭并可能无限积压。

## 后续约束

其他 Provider 必须保持相同 retryable、protocol error、取消和资源关闭语义。

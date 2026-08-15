# ADR-0020：Run 提交幂等与运行时准入控制

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-009](../CHANGELOG.md#e2026-08-15-009)

## 背景

FastAPI 客户端可能因网络超时、代理重试或调用方重放而重复提交同一个 Run。如果每次请求都创建新 Run，包含副作用 Tool 的任务可能被重复执行。同时，Runtime 原先只限制同步 Tool 线程池，没有限制顶层活动 Run 和模型请求并发，突发流量可能耗尽本机与 Provider 资源。

## 决策

- `POST /runs` 接受标准 `Idempotency-Key` Header。
- Runtime 使用 Agent、Input、Session 和 Metadata 的规范化 SHA-256 指纹绑定幂等键。
- schema 7 在 `runs` 上持久化 `idempotency_key` 和 `request_fingerprint`，并通过唯一索引保证多连接并发提交只产生一个 Run。
- 相同 Key、相同指纹返回原 Run；相同 Key、不同指纹返回不可重试的冲突。
- API 使用 `Idempotent-Replayed` 响应头说明是否复用了历史提交。
- Runtime 使用 `max_inflight_runs` 拒绝超过本进程活动任务上限的新提交，并以可重试 429 响应暴露背压。
- Runtime 使用 `max_concurrent_model_requests` Semaphore 限制普通与流式模型请求并发。
- 幂等重放不占用新的活动任务配额，也不会自动恢复崩溃后留下的 Run。

## 影响

### 优点

- 调用方可以安全重试创建 Run，不会重复触发 Agent 与副作用 Tool。
- 多个 SQLiteStore 连接同时提交同一个 Key 时仍只创建一个 durable Run。
- 模型请求和顶层任务都有明确容量边界，过载以稳定错误而不是资源失控体现。
- 旧客户端不提供 Header 时继续保持原有行为。

### 代价

- 幂等键必须由调用方在逻辑请求生命周期内保持稳定。
- 当前活动 Run 配额是单 Runtime 进程边界，不是分布式全局配额。
- 配额拒绝只保护新顶层提交；Workflow 内部 Child Run 仍由 Workflow 自身并发限制与模型 Semaphore 共同约束。

## 被放弃的方案

- 仅依赖客户端去重：无法防止代理重试或客户端崩溃后的重复提交。
- 只在内存保存幂等键：进程重启后失效，也无法支持多个 SQLite 连接。
- 无限排队：会把过载转化为不可控内存增长和超时堆积。

## 后续约束

- 不能清理仍可能被客户端重放的幂等映射。
- 任何改变请求指纹字段、冲突语义或容量错误码的变更都必须更新 ADR。
- 分布式 Worker 阶段需要将本进程容量边界扩展为 Queue/Lease 层面的全局准入。

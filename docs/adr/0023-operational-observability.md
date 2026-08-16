# ADR-0023：持久化事实与瞬时运维信号分层

- **状态**：Accepted
- **日期**：2026-08-16
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-16-002](../CHANGELOG.md#e2026-08-16-002)

## 背景

Runtime 已有 SQLite Event、Trace、Metrics 和 Runtime Doctor，但操作者需要分别查询多个入口，无法一次看清生命周期、容量、进程资源、SQLite、一致性检查与最近失败。项目也缺少可直接交给日志采集器的结构化进程日志。若把瞬时日志当成恢复事实，会在进程崩溃或日志丢失后破坏审计和恢复语义；若把所有运维采样写入 Event Log，又会污染每个 Run 的领域事件序列。

## 决策

保持两层信号：

1. Run、Event、Checkpoint、ToolExecution 和 Approval 继续作为 SQLite durable facts，用于恢复、审计、Trace 和历史 Metrics。
2. Runtime 生命周期与当前进程采样通过结构化 JSON 日志和 `OperationalSnapshot` 暴露，默认不写回 Event Log。

Provider attempt 失败和重试决策会影响 Run 的执行解释，因此新增 durable `model.attempt.failed` 与 `model.retry.scheduled` Event。Runtime 启停、进程 PID、线程数和 asyncio Task 数属于进程级运维信号，不进入 Run sequence。

结构化日志只由调用方显式启用。Formatter 对常见凭据字段脱敏，并限制字符串长度和嵌套深度。综合诊断通过 CLI `observe diagnostics` 和 HTTP `/observability/diagnostics` 暴露，聚合 Runtime 生命周期、Tool 容量、进程采样、SQLite health、Runtime Doctor、Metrics 和最近失败。

## 影响

### 优点

- 恢复事实与瞬时日志职责清晰。
- Provider 中间失败和最终 Run 失败可以区分。
- 单次诊断即可定位容量、持久化一致性、失败类型和延迟。
- JSON Lines 可由宿主环境直接采集。
- 不需要新增 SQLite schema 或外部监控依赖。

### 代价

- 进程日志没有 Runtime 内建保留和轮转。
- 最近失败和 p95 受 `limit` 采样范围影响。
- 标准库采样只提供 PID、线程和 asyncio Task，不能替代完整 APM。
- 新增 Provider 重试 Event 会增加每次失败重试的 Event 数量。

## 被放弃的方案

- 将所有进程日志持久化为 Runtime Event：会污染领域事件和每 Run sequence。
- 默认修改 root logger：可能破坏宿主应用日志配置。
- 立即引入 OpenTelemetry SDK、Prometheus Client 或 psutil：增加依赖和部署复杂度，超出当前单机稳定化范围。
- 只扩展 `/health`：健康探针应保持轻量，不应承担完整诊断和历史聚合。

## 后续约束

- 任何用于恢复或审计的关键执行事实必须进入 SQLite Event Log，不能只写瞬时日志。
- 进程级采样不得占用 Runtime Event sequence。
- 新增日志上下文必须避免 Prompt、Tool 参数、Memory 和凭据原文。
- 修改失败分类、Metrics 名称、Provider attempt Event 或诊断响应结构时必须同步测试、文档和 ADR。
- 引入外部 Collector 或 APM 前，先定义导出失败、背压和 Runtime 主循环隔离语义。
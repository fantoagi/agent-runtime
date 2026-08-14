# ADR-0008：Observability 和 Evals 基于持久化执行事实派生

- **状态**：Accepted
- **日期**：2026-08-14
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-14-002](../CHANGELOG.md#e2026-08-14-002)

## 背景

v0.4 已经持久化 Run、Step、ToolExecution、Checkpoint 和有序 Runtime Event，但缺少面向开发者的 Trace、Metrics 和回归评估能力。如果直接在 Runtime Kernel 中绑定 OpenTelemetry SDK、Prometheus Client 或特定评估平台，会增加依赖并让初学者难以区分执行事实和观测派生数据。

## 决策

1. 每个 Run 在创建时写入稳定的 `trace_id` 到 metadata，并在 `run.created` payload 中保留关联。
2. `ObservabilityService` 从 SQLite 中的 Run 和 Event 派生 `RunTrace`、`TraceSpan` 和 `MetricsSnapshot`，不改变现有 Event schema。
3. Metrics 同时提供 JSON 和 Prometheus 文本，但 v0.5 不绑定外部 Collector 或时序数据库。
4. `EvalRunner` 必须调用正式 `Runtime.run()`，每个 Eval Case 都产生正常 Run、Event、Checkpoint 和 Trace。
5. Eval Report 保存 Run ID、Trace ID、断言和通过率，并以 JSON Artifact 持久化。
6. v0.5 的评估器保持确定性，只内置状态、精确匹配和字符串包含。

## 影响

### 优点

- Observability 复用已有持久化事实，不向 Runtime Kernel 引入第三方遥测依赖。
- 历史 Run 可以在功能上线后重新派生 Trace 和 Metrics。
- Eval 与真实执行路径一致，可以追溯失败用例的完整事件和工具调用。
- JSON、Prometheus、CLI 和 FastAPI 共享同一套观测模型。

### 代价

- 每次 Metrics Snapshot 需要扫描一定数量的 Run 和 Event。
- 派生 Trace 依赖事件类型和 payload 中的关联字段保持稳定。
- 当前 Eval 顺序执行，缺少数据集版本、并发调度和统计分析。
- 当前没有与 OpenTelemetry Trace ID 格式完全对齐。

## 被放弃的方案

- 不在 v0.5 直接引入 OpenTelemetry SDK 和 Prometheus Client，避免核心包依赖膨胀。
- 不建立第二套专用 Trace 数据库，避免 Run/Event 与 Trace 出现双写不一致。
- 不让 Eval 直接调用 Provider，以免绕过工具、安全、审批和恢复语义。
- 不默认使用 LLM-as-a-Judge，避免评估结果本身不可重复。

## 后续约束

未来接入 OpenTelemetry Exporter、外部 Metrics Backend、并发 Eval、数据集版本或 LLM-as-a-Judge 时，必须保留 Run ID、Trace ID 和 Eval Case 的追溯关系。任何改变 Span 配对规则、指标定义或 Eval Report 兼容性的变更，都必须更新本 ADR 或新增 ADR。

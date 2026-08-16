# Agent Runtime 可观测性与运行诊断

- **适用版本**：v0.7.11
- **最近更新**：2026-08-16（Asia/Shanghai）
- **适用范围**：单机 Runtime、CLI、FastAPI、Learning Console

v0.7.11 将现有持久化 Trace、Metrics 和 Runtime Doctor 汇总为可直接用于本地运维的诊断面，并补充有界、可脱敏的结构化 JSON 日志。诊断能力只读取 Runtime 和 SQLite 当前事实，不修改 Run 状态，也不自动执行恢复。

## 1. 三类信号

| 信号 | 事实来源 | 适合回答的问题 |
| --- | --- | --- |
| Runtime Event / Trace | SQLite durable Event Log | 某个 Run 实际经历了哪些步骤？ |
| Metrics / Diagnostics | SQLite 历史 + 当前进程采样 | 最近失败、延迟、容量和进程状态如何？ |
| Structured Log | 当前进程 stderr 或调用方提供的 stream | Runtime 正在启动、提交、重试、结束或关闭什么？ |

结构化日志是瞬时运维信号，不能替代 SQLite Event Log。进程崩溃后，恢复和审计仍以 SQLite 中的 Run、Event、Checkpoint、ToolExecution 与 Approval 为准。

## 2. CLI 综合诊断

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
agent-runtime observe diagnostics
```

限制历史采样和最近失败数量：

```powershell
agent-runtime observe diagnostics --limit 200 --recent-failures 10
```

输出包含：

- Runtime 状态：`accepting`、`closing` 或 `closed`。
- 启动时间、运行时长和活动 Run 数。
- Tool 线程池上限与当前未完成同步 Tool 数。
- PID、Python 线程数和当前 Event Loop Task 数。
- SQLite `quick_check`、schema version 和 journal mode。
- Runtime Doctor 完整结果。
- Run、Model、Tool 平均耗时及 p95。
- Provider attempt failure、retry、Tool failure 和 UNKNOWN 计数。
- 最近失败事件及其 `run_id`、错误类型和可重试语义。

## 3. FastAPI 诊断接口

```text
GET /observability/diagnostics?limit=1000&recent_failure_limit=20
GET /observability/metrics
GET /observability/metrics/prometheus
GET /doctor
```

PowerShell 示例：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/observability/diagnostics
```

`/health` 只回答 Runtime 是否接受请求以及 SQLite 是否可用；`/doctor` 专注持久化一致性；`/observability/diagnostics` 用于一次性获得生命周期、容量、进程、SQLite、Doctor、指标和最近失败的合并快照。

## 4. 结构化 JSON 日志

CLI 启用方式：

```powershell
agent-runtime --json-logs demo "19 * 23" 2> runtime.jsonl
```

日志写入 stderr，CLI 原有 JSON 结果继续写入 stdout。每行都是独立 JSON：

```json
{"timestamp":"2026-08-16T12:00:00+00:00","level":"info","logger":"agent_runtime.runtime","message":"run.submitted","event":"run.submitted","context":{"run_id":"run_xxx","agent_name":"demo","idempotent":false}}
```

Python 配置：

```python
from agent_runtime import configure_structured_logging

configure_structured_logging()
```

当前 Runtime 记录：

- `runtime.started`
- `runtime.shutdown.started`
- `runtime.shutdown.completed`
- `run.submitted`
- `run.submission.replayed`
- `run.execution.finished`
- `model.attempt.failed`

日志 Formatter 会：

- 对 `api_key`、`authorization`、`cookie`、`credential`、`password`、`secret` 和 `token` 类字段脱敏。
- 对任意字符串中的 Bearer 凭据和常见 `sk-` / `rk-` / `pk-` Token 形态脱敏。
- 将单个字符串限制为 2000 字符。
- 将嵌套深度限制为 6 层。
- 对未知对象转换为有界字符串。

这些措施用于降低误泄漏风险，但调用方仍不应主动把完整 Prompt、Tool 参数、Memory 或凭据写入日志上下文。

## 5. Provider 重试事件

v0.7.11 新增两个 durable Event：

- `model.attempt.failed`：每次 Provider attempt 失败时记录错误类型、attempt、可重试性和可用的 HTTP status。
- `model.retry.scheduled`：Runtime 决定重试时记录下一次 attempt 与退避时间。

这两个事件允许区分：

- 一次 Run 最终失败。
- Provider 中间失败但重试成功。
- 确定性 4xx 不重试。
- 429、5xx 或网络错误进入退避重试。

## 6. 指标语义

Metrics 从最近 `limit` 个持久化 Run 派生，不是无界全库统计：

- `run_average` / `run_p95`
- `model_average` / `model_p95`
- `tool_average` / `tool_p95`
- `provider_attempts`
- `provider_retries`
- `tool_failures`
- `unknown_tool_outcomes`
- `failures.by_type`

Prometheus 输出增加对应 gauge。当前没有内置 HTTP 拉取鉴权、外部时序数据库、Histogram Bucket 或 OpenTelemetry Collector。

## 7. 推荐排障顺序

```text
/health
→ observe diagnostics
→ doctor --json
→ observe trace <run-id>
→ runs events <run-id>
→ 核对结构化日志
→ 必要时执行备份与恢复流程
```

如果出现 `UNKNOWN` Tool，不能只根据日志猜测结果；必须核对外部副作用并使用 `resolve-unknown` 显式确认。

## 8. 当前边界

- 进程资源只提供 PID、线程数和 asyncio Task 数，不包含 CPU、RSS、句柄、磁盘和网络指标。
- 指标按最近 N 个 Run 派生，不是严格时间窗口。
- JSON 日志默认不启用，由 CLI `--json-logs` 或应用代码显式配置。
- 没有日志滚动、保留、上传和远端 Collector；这些应由宿主进程或日志平台负责。
- Learning Console 只展示诊断快照，不是生产监控平台。
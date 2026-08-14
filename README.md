# Agent Runtime

A small, durable single-agent runtime with model-provider abstraction, structured tool
execution, SQLite migrations, durable steps, idempotent recovery, event streaming, token streaming,
checkpoints, approval gates, cooperative cancellation, tracing, metrics, evals, a CLI, and a visual Learning Console.

## Quick start

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[api]
python -m pip install pytest pytest-asyncio
agent-runtime lab
# Or run the CLI-only demo:
agent-runtime demo "19 * 23"
```

The demo uses a deterministic local provider. Set `OPENAI_API_KEY` and select the
OpenAI-compatible provider in application code when connecting to a real model.

## Learning Console

如果你希望直观学习一次 Agent Run 的完整处理流程，启动本地可视化控制台：

```powershell
agent-runtime lab
```

浏览器会打开 `http://127.0.0.1:8000/lab`。v0.5.1 内置纯文本、Tool Calling、Token Streaming 和 Human Approval 四个确定性场景，并提供：

- 持久化 Runtime Event 实时时间线。
- 从头回放、逐事件前进和自动播放。
- Event 状态 diff、原因、下一步和源码方法映射。
- Messages、Step、ToolExecution、Checkpoint、Trace、Metrics 和 SQLite 检查器。
- 浏览器内审批与场景自动验收。

详细学习路径见 [Learning Console 使用指南](./docs/LEARNING.md)。

## FastAPI / SSE

启动本地 Demo API：

```powershell
uvicorn agent_runtime.api.app:app --reload
```

然后可以：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Method Post http://127.0.0.1:8000/runs -ContentType 'application/json' -Body '{"input":"19 * 23"}'
```

SSE 事件接口为 `GET /runs/{run_id}/events/stream?after_sequence=0`。v0.4 起，模型 token 增量以 `model.delta` Runtime Event 进入同一条 SSE 流，客户端无需维护第二套 token 协议。


## Observability / Evals

查看从 SQLite 持久化历史派生的指标和单次 Run Trace：

```powershell
agent-runtime observe metrics
agent-runtime observe trace <run-id>
```

运行内置确定性评估套件：

```powershell
agent-runtime eval demo
```

HTTP 入口：

```text
GET /observability/metrics
GET /observability/metrics/prometheus
GET /runs/{run_id}/trace
```

Eval Report 会写入 `.agent-runtime/artifacts/<eval-id>/eval-report.json`，每个用例保留对应 Run ID 和 Trace ID。

## Development checks

```powershell
python scripts/check_docs.py
pytest
```

If a change is based on an existing Git commit, also run the synchronization gate:

```powershell
python scripts/check_docs.py --base-ref <base-commit>
```

## Documentation and evolution tracking

The project treats documentation as part of the implementation contract:

- [Current system state](./docs/CURRENT.md): what is implemented, experimental, planned, or unsupported.
- [Current architecture](./docs/ARCHITECTURE.md): how the runtime is structured today.
- [Roadmap](./docs/ROADMAP.md): planned versions, scope boundaries, dependencies, and acceptance direction.
- [Learning Console guide](./docs/LEARNING.md): visual scenarios, event replay, inspectors, and source-code mapping.
- [Evolution log](./docs/CHANGELOG.md): feature and architecture changes in reverse completion-time order.
- [Architecture Decision Records](./docs/adr/README.md): why important public, data, reliability, or security decisions were made.
- [Documentation workflow](./docs/README.md): Change IDs, templates, update rules, and quality gates.

Every independently verifiable feature, fix, or architecture change should have a
Change ID in `docs/CHANGELOG.md`. Core runtime changes must update the evolution log;
changes to public interfaces, state/event schemas, recovery semantics, or security
boundaries must also add or update an ADR.

## Design

- **Runtime kernel**: bounded agent loop with cancellation, retry-safe checkpoints, and approvals.
- **Providers**: normalized text/tool-call responses, optional token streaming, a deterministic Mock, and an OpenAI-compatible implementation.
- **Tools**: JSON-schema-inspired argument validation, timeout handling, and workspace confinement.
- **Storage**: SQLite event log plus file-backed artifacts.
- **Observability**: trace and metrics derived from persisted runs/events, with JSON and Prometheus outputs.
- **Evals**: deterministic suites that execute through the real Runtime path and persist reports.
- **Interfaces**: Python SDK, CLI, FastAPI/SSE, and a local Learning Console; the core has no UI dependency.

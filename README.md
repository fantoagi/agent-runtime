# Agent Runtime

A small, durable single-agent runtime with model-provider abstraction, structured tool
execution, SQLite migrations, durable steps, idempotent recovery, event streaming, token streaming,
checkpoints, approval gates, cooperative cancellation, tracing, metrics, evals, and a CLI.

## Quick start

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[api]
python -m pip install pytest pytest-asyncio
agent-runtime demo "19 * 23"
```

The demo uses a deterministic local provider. Set `OPENAI_API_KEY` and select the
OpenAI-compatible provider in application code when connecting to a real model.

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
- **Interfaces**: Python SDK and CLI; the core has no HTTP dependency.

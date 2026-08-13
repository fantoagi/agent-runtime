# Agent Runtime

A small, durable single-agent runtime with model-provider abstraction, structured tool
execution, SQLite migrations, durable steps, idempotent recovery, event streaming,
checkpoints, approval gates, cooperative cancellation, and a CLI.

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

SSE 事件接口为 `GET /runs/{run_id}/events/stream?after_sequence=0`。这里的 SSE 是持久化 Runtime Event 流，不是模型 token streaming。

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
- **Providers**: normalized text/tool-call responses with a Mock and OpenAI-compatible implementation.
- **Tools**: JSON-schema-inspired argument validation, timeout handling, and workspace confinement.
- **Storage**: SQLite event log plus file-backed artifacts.
- **Interfaces**: Python SDK and CLI; the core has no HTTP dependency.

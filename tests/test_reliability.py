from __future__ import annotations

import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx
import pytest

import agent_runtime.tools as tools_module
from agent_runtime.api import create_app
from agent_runtime.domain import (
    AgentDefinition,
    AgentRun,
    MigrationError,
    ModelConfig,
    RunStatus,
    StoreBusyError,
    StoreCorruptionError,
    ToolDefinition,
    ToolExecutionError,
    ToolOutcomeUnknown,
)
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.storage import SQLiteStore
from agent_runtime.tools import ToolContext, ToolRegistry, register_builtin_tools

pytestmark = pytest.mark.integration


def make_runtime(
    workspace: Path,
    provider: MockProvider,
    *,
    store: SQLiteStore | None = None,
    tools: ToolRegistry | None = None,
) -> Runtime:
    registry = tools or ToolRegistry()
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace,
            database_path=workspace / "state.sqlite3",
            model_timeout_seconds=2,
            run_timeout_seconds=5,
        ),
        provider,
        registry,
        store=store,
    )
    runtime.register_agent(
        AgentDefinition(
            name="reliability",
            system_prompt="test",
            tools=[],
            model=ModelConfig(model="test"),
        )
    )
    return runtime


@pytest.mark.asyncio
async def test_sync_tool_runs_outside_event_loop(workspace: Path) -> None:
    registry = ToolRegistry(max_sync_workers=1, max_pending_sync_tools=1)

    def slow(arguments, context):
        del arguments, context
        time.sleep(0.15)
        return "done"

    registry.register(
        ToolDefinition("slow", "slow", {"type": "object"}),
        slow,
        timeout_seconds=1,
    )
    invoke = asyncio.create_task(
        registry.invoke("slow", {}, ToolContext("run", 1, workspace, {}))
    )
    started = time.perf_counter()
    await asyncio.sleep(0.03)
    elapsed = time.perf_counter() - started
    result = await invoke
    registry.close()
    assert elapsed < 0.1
    assert result.content == "done"


@pytest.mark.asyncio
async def test_side_effecting_sync_tool_timeout_is_unknown(workspace: Path) -> None:
    registry = ToolRegistry(max_sync_workers=1, max_pending_sync_tools=1)

    def slow_write(arguments, context):
        del arguments, context
        time.sleep(0.15)
        return "late"

    registry.register(
        ToolDefinition(
            "slow_write",
            "slow side effect",
            {"type": "object"},
            side_effecting=True,
        ),
        slow_write,
        timeout_seconds=0.02,
    )
    with pytest.raises(ToolOutcomeUnknown):
        await registry.invoke(
            "slow_write", {}, ToolContext("run", 1, workspace, {})
        )
    await asyncio.sleep(0.16)
    registry.close()


@pytest.mark.asyncio
async def test_tool_registry_aclose_has_bounded_wait_for_running_handler(
    workspace: Path,
) -> None:
    registry = ToolRegistry(max_sync_workers=1, max_pending_sync_tools=1)
    started = Event()

    def slow(arguments, context):
        del arguments, context
        started.set()
        time.sleep(0.15)
        return "done"

    registry.register(ToolDefinition("slow_close", "slow", {"type": "object"}), slow)
    invocation = asyncio.create_task(
        registry.invoke("slow_close", {}, ToolContext("run", 1, workspace, {}))
    )
    assert await asyncio.to_thread(started.wait, 1)
    began = time.perf_counter()
    await registry.aclose(timeout_seconds=0.01)
    assert time.perf_counter() - began < 0.1
    assert (await invocation).content == "done"


@pytest.mark.asyncio
async def test_builtin_write_is_atomic_and_leaves_no_temp_file(workspace: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    result = await registry.invoke(
        "write_text_file",
        {"path": "nested/result.txt", "content": "durable"},
        ToolContext("run", 1, workspace, {}),
    )
    registry.close()
    assert result.data == {"path": str(workspace / "nested" / "result.txt"), "status": "written"}
    assert (workspace / "nested" / "result.txt").read_text(encoding="utf-8") == "durable"
    assert list((workspace / "nested").glob(".*.tmp")) == []


@pytest.mark.asyncio
async def test_builtin_atomic_write_cleans_temp_file_on_replace_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    target = workspace / "result.txt"
    target.write_text("original", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(tools_module.os, "replace", fail_replace)
    with pytest.raises(ToolExecutionError, match="simulated disk write failure"):
        await registry.invoke(
            "write_text_file",
            {"path": "result.txt", "content": "replacement"},
            ToolContext("run", 1, workspace, {}),
        )
    registry.close()
    assert target.read_text(encoding="utf-8") == "original"
    assert list(workspace.glob(".result.txt.*.tmp")) == []


@pytest.mark.asyncio
async def test_shutdown_pauses_inflight_run_and_is_idempotent(workspace: Path) -> None:
    started = asyncio.Event()

    async def blocked(messages, tools, config):
        del messages, tools, config
        started.set()
        await asyncio.Event().wait()
        return ModelResponse(content="never")

    store = SQLiteStore(workspace / "state.sqlite3")
    runtime = make_runtime(workspace, MockProvider(blocked), store=store)
    run = runtime.start("reliability", "block")
    await asyncio.wait_for(started.wait(), timeout=1)
    await runtime.shutdown(timeout_seconds=0, cancel_running=True)
    await runtime.shutdown(timeout_seconds=0, cancel_running=True)
    recovered = store.get_run(run.id)
    assert recovered.status is RunStatus.PAUSED
    assert runtime.is_accepting is False
    store.close()


@pytest.mark.asyncio
async def test_wait_observes_run_completed_by_another_store(workspace: Path) -> None:
    database = workspace / "state.sqlite3"
    first_store = SQLiteStore(database)
    second_store = SQLiteStore(database)
    provider = MockProvider(lambda *_: ModelResponse(content="unused"))
    runtime = make_runtime(workspace, provider, store=second_store)
    run = AgentRun.create("reliability", "external")
    first_store.create_run(run)
    run.transition_to(RunStatus.RUNNING)
    first_store.save_run(run)

    async def finish() -> None:
        await asyncio.sleep(0.05)
        current = first_store.get_run(run.id)
        current.result = "done"
        current.transition_to(RunStatus.COMPLETED)
        first_store.save_run_with_event(current, "run.completed")

    finisher = asyncio.create_task(finish())
    completed = await runtime.wait(run.id, timeout_seconds=1)
    await finisher
    assert completed.status is RunStatus.COMPLETED
    await runtime.shutdown()
    first_store.close()
    second_store.close()


@pytest.mark.stress
def test_multiple_store_connections_allocate_unique_event_sequences(workspace: Path) -> None:
    database = workspace / "state.sqlite3"
    stores = [SQLiteStore(database, busy_timeout_seconds=1) for _ in range(4)]
    run = AgentRun.create("agent", "concurrency")
    stores[0].create_run(run)

    def append(index: int) -> int:
        return stores[index % len(stores)].append_event(
            run.id, "stress.event", {"index": index}
        ).sequence

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append, range(20)))
    assert sorted(sequences) == list(range(1, 21))
    assert [event.sequence for event in stores[0].events_since(run.id)] == list(
        range(1, 21)
    )
    for store in stores:
        store.close()


def test_corrupt_sqlite_file_fails_fast_with_structured_error(workspace: Path) -> None:
    database = workspace / "corrupt.sqlite3"
    database.write_bytes(b"not-a-sqlite-database" * 64)
    with pytest.raises(StoreCorruptionError, match=r"corrupt database|quick_check"):
        SQLiteStore(database)


def test_sqlite_lock_exhaustion_returns_store_busy(workspace: Path) -> None:
    database = workspace / "busy.sqlite3"
    store = SQLiteStore(database, busy_timeout_seconds=0, lock_retry_attempts=1)
    run = AgentRun.create("agent", "busy")
    store.create_run(run)
    lock_holder = sqlite3.connect(database, timeout=0)
    lock_holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StoreBusyError, match="remained busy"):
            store.append_event(run.id, "busy.event")
    finally:
        lock_holder.rollback()
        lock_holder.close()
        store.close()


def test_migration_checksum_mismatch_fails_fast(workspace: Path) -> None:
    database = workspace / "state.sqlite3"
    store = SQLiteStore(database)
    store.close()
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE schema_migrations SET checksum='changed' WHERE version=1"
    )
    connection.commit()
    connection.close()
    with pytest.raises(MigrationError, match="checksum mismatch"):
        SQLiteStore(database)


@pytest.mark.asyncio
async def test_app_lifespan_closes_owned_runtime(workspace: Path) -> None:
    runtime = make_runtime(
        workspace, MockProvider(lambda *_: ModelResponse(content="ok"))
    )
    app = create_app(runtime, shutdown_runtime=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/health")
            assert health.status_code == 200
    assert runtime.is_accepting is False


@pytest.mark.asyncio
async def test_pause_interrupts_active_model_request_and_resume_completes(workspace: Path) -> None:
    calls = 0

    async def responder(messages, tools, config):
        nonlocal calls
        del messages, tools, config
        calls += 1
        if calls == 1:
            await asyncio.sleep(1)
        return ModelResponse(content="resumed")

    runtime = make_runtime(workspace, MockProvider(responder))
    created = runtime.start("reliability", "pause me")
    for _ in range(100):
        if runtime.store.get_run(created.id).status is RunStatus.RUNNING:
            break
        await asyncio.sleep(0.001)

    paused = runtime.pause(created.id)
    assert paused.status is RunStatus.PAUSED
    assert (await runtime.wait(created.id)).status is RunStatus.PAUSED
    resumed = await runtime.resume(created.id)
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.result == "resumed"
    await runtime.shutdown()


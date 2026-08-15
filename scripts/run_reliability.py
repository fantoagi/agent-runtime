#!/usr/bin/env python3
"""Deterministic stress and soak runner for the local Agent Runtime."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import shutil
import threading
import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

from agent_runtime.domain import (
    AgentDefinition,
    Message,
    ModelConfig,
    RunStatus,
    ToolCall,
    ToolDefinition,
)
from agent_runtime.orchestration import ParallelWorkflow, SequentialWorkflow
from agent_runtime.providers import MockProvider, ModelResponse
from agent_runtime.runtime import Runtime, RuntimeConfig
from agent_runtime.tools import ToolRegistry


def make_runtime(root: Path) -> tuple[Runtime, AgentDefinition, AgentDefinition]:
    async def responder(
        messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> ModelResponse:
        del tools, config
        last = messages[-1]
        if last.role == "tool":
            return ModelResponse(content=f"tool-result:{last.content}")
        text = last.content or ""
        if text.startswith("slow:"):
            await asyncio.sleep(0.05)
        if text.startswith("tool:"):
            return ModelResponse(
                tool_calls=[ToolCall(f"call-{abs(hash(text))}", "echo", {"value": text[5:]})],
                finish_reason="tool_calls",
            )
        return ModelResponse(content=f"ok:{text}")

    tools = ToolRegistry(max_sync_workers=8, max_pending_sync_tools=32)
    echo = ToolDefinition(
        "echo",
        "Return the supplied value.",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    tools.register(echo, lambda arguments, context: arguments["value"])
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=root,
            database_path=root / "reliability.sqlite3",
            artifact_path=root / "artifacts",
            run_timeout_seconds=10,
            model_timeout_seconds=5,
            event_poll_interval_seconds=0.005,
        ),
        MockProvider(responder),
        tools,
    )
    plain = AgentDefinition("plain", "plain", [], ModelConfig(model="fake"))
    tool_agent = AgentDefinition("tool", "tool", [echo], ModelConfig(model="fake"))
    runtime.register_agent(plain)
    runtime.register_agent(tool_agent)
    return runtime, plain, tool_agent


def assert_event_sequences(runtime: Runtime, run_ids: set[str]) -> int:
    event_count = 0
    for run_id in run_ids:
        events = runtime.store.events_since(run_id)
        sequences = [event.sequence for event in events]
        expected = list(range(1, len(sequences) + 1))
        if sequences != expected:
            raise AssertionError(
                f"run {run_id} has non-durable event sequence: {sequences[:20]}"
            )
        event_count += len(events)
    return event_count


async def consume_events(runtime: Runtime, run_id: str) -> int:
    count = 0
    async for _event in runtime.stream(run_id):
        count += 1
    return count


async def execute_case(
    runtime: Runtime,
    plain: AgentDefinition,
    tool_agent: AgentDefinition,
    index: int,
) -> set[str]:
    mode = index % 4
    if mode == 0:
        created = runtime.start(plain, f"plain:{index}")
        consumer = asyncio.create_task(consume_events(runtime, created.id))
        completed = await runtime.wait(created.id, timeout_seconds=10)
        consumed = await consumer
        if completed.status is not RunStatus.COMPLETED or consumed == 0:
            raise AssertionError(f"plain run failed: {completed.to_dict()}")
        return {completed.id}
    if mode == 1:
        completed = await runtime.run(tool_agent, f"tool:{index}")
        if completed.status is not RunStatus.COMPLETED:
            raise AssertionError(f"tool run failed: {completed.to_dict()}")
        return {completed.id}
    if mode == 2:
        execution = await SequentialWorkflow(
            f"sequential-{index}", [plain, plain]
        ).run(runtime, f"workflow:{index}")
    else:
        execution = await ParallelWorkflow(
            f"parallel-{index}", [plain, plain], max_concurrency=2
        ).run(runtime, f"workflow:{index}")
    if execution.parent.status is not RunStatus.COMPLETED:
        raise AssertionError(f"workflow failed: {execution.parent.to_dict()}")
    return {execution.parent.id, *(child.id for child in execution.children)}


async def run_stress(
    runtime: Runtime,
    plain: AgentDefinition,
    tool_agent: AgentDefinition,
    run_count: int,
    concurrency: int,
    offset: int = 0,
) -> tuple[set[str], float]:
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(index: int) -> set[str]:
        async with semaphore:
            return await execute_case(runtime, plain, tool_agent, offset + index)

    started = time.perf_counter()
    groups = await asyncio.gather(*(guarded(index) for index in range(run_count)))
    elapsed = time.perf_counter() - started
    return set().union(*groups), elapsed


async def exercise_lifecycle(
    runtime: Runtime, plain: AgentDefinition, iteration: int
) -> set[str]:
    created = runtime.start(plain, f"slow:cancel:{iteration}")
    await asyncio.sleep(0.005)
    runtime.cancel(created.id)
    cancelled = await runtime.wait(created.id, timeout_seconds=5)
    if cancelled.status is not RunStatus.CANCELLED:
        raise AssertionError(f"cancel did not persist: {cancelled.to_dict()}")

    paused = runtime.start(plain, f"slow:pause:{iteration}")
    for _ in range(100):
        if runtime.store.get_run(paused.id).status is RunStatus.RUNNING:
            break
        await asyncio.sleep(0.001)
    runtime.pause(paused.id)
    task = runtime._tasks.get(paused.id)  # reliability probe intentionally observes cleanup
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)
    resumed = await runtime.resume(paused.id)
    if resumed.status is not RunStatus.COMPLETED:
        raise AssertionError(f"pause/resume did not complete: {resumed.to_dict()}")
    return {cancelled.id, resumed.id}


async def main_async(args: argparse.Namespace) -> dict[str, object]:
    temporary_parent = Path.cwd() / ".runtime-test-data"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    owns_root = args.workspace is None
    root = (
        Path(args.workspace).resolve()
        if args.workspace
        else temporary_parent / f"reliability-{uuid4().hex}"
    )
    root.mkdir(parents=True, exist_ok=False if owns_root else True)
    baseline_threads = threading.active_count()
    baseline_tasks = len(asyncio.all_tasks())
    tracemalloc.start()
    runtime, plain, tool_agent = make_runtime(root)
    run_ids: set[str] = set()
    stress_ids, stress_elapsed = await run_stress(
        runtime, plain, tool_agent, args.stress_runs, args.concurrency
    )
    run_ids.update(stress_ids)

    soak_iterations = 0
    soak_started = time.monotonic()
    while time.monotonic() - soak_started < args.soak_seconds:
        batch_ids, _ = await run_stress(
            runtime,
            plain,
            tool_agent,
            min(args.soak_batch_size, args.stress_runs),
            args.concurrency,
            offset=args.stress_runs + soak_iterations * args.soak_batch_size,
        )
        run_ids.update(batch_ids)
        run_ids.update(await exercise_lifecycle(runtime, plain, soak_iterations))
        soak_iterations += 1

    event_count = assert_event_sequences(runtime, run_ids)
    health = runtime.store.health_check()
    await runtime.shutdown(timeout_seconds=10)
    await runtime.shutdown(timeout_seconds=10)
    gc.collect()
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    final_threads = threading.active_count()
    final_tasks = len([task for task in asyncio.all_tasks() if not task.done()])
    if owns_root:
        shutil.rmtree(root, ignore_errors=True)

    if final_threads > baseline_threads + 1:
        raise AssertionError(
            f"thread count did not return near baseline: {baseline_threads} -> {final_threads}"
        )
    if final_tasks > baseline_tasks:
        raise AssertionError(
            f"asyncio task count did not return to baseline: {baseline_tasks} -> {final_tasks}"
        )
    return {
        "status": "ok",
        "stress_runs": args.stress_runs,
        "durable_runs": len(run_ids),
        "events": event_count,
        "stress_elapsed_seconds": round(stress_elapsed, 3),
        "stress_runs_per_second": round(args.stress_runs / max(stress_elapsed, 1e-9), 2),
        "soak_seconds": args.soak_seconds,
        "soak_iterations": soak_iterations,
        "threads": {"baseline": baseline_threads, "final": final_threads},
        "asyncio_tasks": {"baseline": baseline_tasks, "final": final_tasks},
        "tracemalloc_bytes": {"current": current_memory, "peak": peak_memory},
        "sqlite": health,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stress-runs", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--soak-seconds", type=float, default=0)
    parser.add_argument("--soak-batch-size", type=int, default=20)
    parser.add_argument("--workspace")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.stress_runs < 1 or args.concurrency < 1 or args.soak_seconds < 0:
        parser.error("run counts/concurrency must be positive and soak duration non-negative")
    return args


def main() -> int:
    args = parse_args()
    result = asyncio.run(main_async(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


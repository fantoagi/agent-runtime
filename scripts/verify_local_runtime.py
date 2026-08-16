#!/usr/bin/env python3
"""Verify the supported single-user local Runtime bootstrap and lifecycle."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import shutil
import threading
import tracemalloc
from pathlib import Path
from uuid import uuid4

import httpx

from agent_runtime.api import create_app
from agent_runtime.backup import RuntimeBackupManager
from agent_runtime.local_config import load_local_settings, write_default_local_config
from agent_runtime.local_runtime import (
    LocalRuntimeLock,
    LocalRuntimeLockError,
    create_configured_local_runtime,
    local_runtime_status,
)
from agent_runtime.version import __version__


async def verify(args: argparse.Namespace) -> dict[str, object]:
    parent = Path.cwd() / ".runtime-test-data"
    parent.mkdir(parents=True, exist_ok=True)
    owns_root = args.workspace is None
    root = (
        Path(args.workspace).resolve()
        if args.workspace
        else parent / f"local-runtime-{uuid4().hex}"
    )
    root.mkdir(parents=True, exist_ok=not owns_root)
    baseline_threads = threading.active_count()
    baseline_tasks = len(asyncio.all_tasks())
    tracemalloc.start()

    config_path = write_default_local_config(
        root / "agent-runtime.toml",
        workspace=root,
        force=args.force,
    )
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace(
        "max_inflight_runs = 8",
        f"max_inflight_runs = {max(8, args.concurrency)}",
    )
    config_path.write_text(config_text, encoding="utf-8")
    settings = load_local_settings(config_path)
    lock = LocalRuntimeLock(settings.lock_path)
    lock.acquire()
    duplicate_rejected = False
    runtime = None
    try:
        try:
            LocalRuntimeLock(settings.lock_path).acquire()
        except LocalRuntimeLockError:
            duplicate_rejected = True
        if not duplicate_rejected:
            raise AssertionError("a second local Runtime owner was not rejected")

        runtime = create_configured_local_runtime(settings)
        semaphore = asyncio.Semaphore(args.concurrency)

        async def execute(index: int) -> str:
            async with semaphore:
                run = await runtime.run(settings.agent_name, f"{index} + 1")
                if run.status != "completed":
                    raise AssertionError(f"run did not complete: {run.to_dict()}")
                sequences = [event.sequence for event in runtime.store.events_since(run.id)]
                if sequences != list(range(1, len(sequences) + 1)):
                    raise AssertionError(f"event sequence is not durable: {run.id}")
                return run.id

        run_ids = await asyncio.gather(*(execute(index) for index in range(args.runs)))
        health = runtime.store.health_check()

        app = create_app(runtime, default_agent=settings.agent_name)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://local-runtime",
        ) as client:
            response = await client.get("/health")
            response.raise_for_status()
            api_health = response.json()

        archive = settings.state_dir / "backups" / "verification.agent-backup"
        manager = RuntimeBackupManager(settings.database_path, settings.artifact_path)
        backup = manager.create(archive, overwrite=True)
        backup_verification = RuntimeBackupManager.verify(archive)
        if not backup_verification.valid:
            raise AssertionError(backup_verification.to_dict())

        await runtime.shutdown(timeout_seconds=settings.shutdown_timeout_seconds)
        await runtime.shutdown(timeout_seconds=settings.shutdown_timeout_seconds)
    finally:
        if runtime is not None:
            await runtime.shutdown(timeout_seconds=settings.shutdown_timeout_seconds)
        lock.release()

    first_status = local_runtime_status(settings)
    if first_status["status"] != "stopped":
        raise AssertionError("local Runtime lock was not released")

    restart_lock = LocalRuntimeLock(settings.lock_path)
    restart_lock.acquire()
    try:
        restarted = create_configured_local_runtime(settings)
        historical_runs = [restarted.store.get_run(run_id) for run_id in run_ids]
        if len(historical_runs) != args.runs:
            raise AssertionError("restart did not preserve historical runs")
        restart_run = await restarted.run(settings.agent_name, "40 + 2")
        if restart_run.status != "completed" or restart_run.result != "The result is 42.":
            raise AssertionError(f"restart smoke failed: {restart_run.to_dict()}")
        await restarted.shutdown(timeout_seconds=settings.shutdown_timeout_seconds)
    finally:
        restart_lock.release()

    gc.collect()
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    final_threads = threading.active_count()
    final_tasks = len([task for task in asyncio.all_tasks() if not task.done()])
    if final_threads > baseline_threads + 1:
        raise AssertionError(
            f"thread count did not return near baseline: {baseline_threads} -> {final_threads}"
        )
    if final_tasks > baseline_tasks:
        raise AssertionError(
            f"asyncio task count did not return to baseline: {baseline_tasks} -> {final_tasks}"
        )

    result: dict[str, object] = {
        "status": "ok",
        "version": __version__,
        "workspace": str(root),
        "runs": len(run_ids),
        "concurrency": args.concurrency,
        "duplicate_owner_rejected": duplicate_rejected,
        "sqlite": health,
        "api_health": api_health,
        "backup": backup.to_dict(),
        "backup_verification": backup_verification.to_dict(),
        "restart": "ok",
        "threads": {"baseline": baseline_threads, "final": final_threads},
        "asyncio_tasks": {"baseline": baseline_tasks, "final": final_tasks},
        "tracemalloc_bytes": {"current": current_memory, "peak": peak_memory},
    }
    if owns_root and not args.keep:
        shutil.rmtree(root, ignore_errors=True)
        result["workspace_removed"] = True
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--workspace")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1 or args.concurrency < 1:
        parser.error("runs and concurrency must be positive")
    return args


def main() -> int:
    args = parse_args()
    result = asyncio.run(verify(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


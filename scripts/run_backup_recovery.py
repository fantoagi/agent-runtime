#!/usr/bin/env python3
"""Exercise online backup, verification, offline restore, and rollback cleanup."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path
from uuid import uuid4

from agent_runtime.backup import RuntimeBackupManager
from agent_runtime.domain import RunNotFound
from agent_runtime.sdk import create_local_runtime, demo_agent
from agent_runtime.storage import SQLiteStore


async def run_drill(root: Path) -> dict[str, object]:
    state_dir = root / "state"
    archive = root / "runtime.agent-backup"
    runtime = create_local_runtime(root, state_dir)
    first = await runtime.run(demo_agent(), "19 * 23")
    artifact = runtime.artifacts.write_text(first.id, "drill.txt", "backup-version")

    manager = RuntimeBackupManager(
        state_dir / "runtime.sqlite3", state_dir / "artifacts"
    )
    created = manager.create(archive)
    second = await runtime.run(demo_agent(), "8 * 8")
    artifact.write_text("post-backup-version", encoding="utf-8")
    await runtime.shutdown()

    verification = RuntimeBackupManager.verify(archive)
    if not verification.valid:
        raise RuntimeError(f"backup verification failed: {verification.errors}")
    restored = manager.restore(
        archive,
        overwrite=True,
        keep_previous=False,
    )

    store = SQLiteStore(state_dir / "runtime.sqlite3")
    try:
        recovered = store.get_run(first.id)
        try:
            store.get_run(second.id)
        except RunNotFound:
            post_backup_run_absent = True
        else:
            post_backup_run_absent = False
        health = store.health_check()
    finally:
        store.close()

    if recovered.result != "The result is 437.":
        raise RuntimeError("pre-backup Run was not recovered")
    if not post_backup_run_absent:
        raise RuntimeError("post-backup Run unexpectedly survived restore")
    if artifact.read_text(encoding="utf-8") != "backup-version":
        raise RuntimeError("artifact content was not restored")

    return {
        "status": "passed",
        "archive": str(archive),
        "schema_version": verification.schema_version,
        "database_sha256": verification.database_sha256,
        "database_bytes": created.database_bytes,
        "artifact_count": verification.artifact_count,
        "pre_backup_run_recovered": True,
        "post_backup_run_absent": post_backup_run_absent,
        "artifact_recovered": True,
        "previous_state_discarded": not Path(
            restored.previous_database_path or ""
        ).exists(),
        "sqlite": health,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--keep-data", action="store_true")
    arguments = parser.parse_args()

    root = Path.cwd() / ".runtime-test-data" / f"backup-drill-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        result = asyncio.run(run_drill(root))
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        print(encoded)
        if arguments.output is not None:
            arguments.output.write_text(encoded + "\n", encoding="utf-8")
    finally:
        if not arguments.keep_data:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

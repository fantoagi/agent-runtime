from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

import agent_runtime.backup as backup_module
from agent_runtime.backup import RuntimeBackupManager
from agent_runtime.cli import build_parser
from agent_runtime.domain import (
    BackupConflictError,
    BackupError,
    BackupTargetBusyError,
    RunNotFound,
)
from agent_runtime.sdk import create_local_runtime, demo_agent
from agent_runtime.storage import SQLiteStore


@pytest.mark.asyncio
async def test_online_backup_verify_and_restore_runtime_state(workspace: Path) -> None:
    state_dir = workspace / "state"
    database = state_dir / "runtime.sqlite3"
    artifacts = state_dir / "artifacts"
    runtime = create_local_runtime(workspace, state_dir)
    first = await runtime.run(demo_agent(), "6 * 7")
    artifact = runtime.artifacts.write_text(first.id, "result.txt", "before-backup")

    manager = RuntimeBackupManager(database, artifacts)
    archive = workspace / "runtime.agent-backup"
    created = manager.create(archive)

    assert created.schema_version == 8
    assert created.counts["runs"] == 1
    assert created.artifact_count == 1
    verification = RuntimeBackupManager.verify(archive)
    assert verification.valid is True
    assert verification.exit_code == 0
    assert verification.database_sha256 == created.database_sha256

    second = await runtime.run(demo_agent(), "8 * 8")
    artifact.write_text("after-backup", encoding="utf-8")
    await runtime.shutdown()

    with pytest.raises(BackupConflictError):
        manager.restore(archive)

    restored = manager.restore(archive, overwrite=True)
    assert restored.schema_version == 8
    assert restored.artifact_count == 1
    assert restored.previous_database_path is not None
    assert restored.previous_artifact_path is not None
    assert artifact.read_text(encoding="utf-8") == "before-backup"

    store = SQLiteStore(database)
    try:
        assert store.get_run(first.id).result == "The result is 42."
        with pytest.raises(RunNotFound):
            store.get_run(second.id)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_restore_can_discard_previous_state(workspace: Path) -> None:
    state_dir = workspace / "state"
    runtime = create_local_runtime(workspace, state_dir)
    await runtime.run(demo_agent(), "2 + 2")
    manager = RuntimeBackupManager(
        state_dir / "runtime.sqlite3", state_dir / "artifacts"
    )
    archive = workspace / "runtime.agent-backup"
    manager.create(archive)
    await runtime.shutdown()

    restored = manager.restore(archive, overwrite=True, keep_previous=False)

    assert restored.previous_database_path is not None
    assert restored.previous_artifact_path is not None
    assert not Path(restored.previous_database_path).exists()
    assert not Path(restored.previous_artifact_path).exists()


def test_backup_verification_rejects_duplicate_or_tampered_entries(workspace: Path) -> None:
    state_dir = workspace / "state"
    store = SQLiteStore(state_dir / "runtime.sqlite3")
    store.close()
    manager = RuntimeBackupManager(
        state_dir / "runtime.sqlite3", state_dir / "artifacts"
    )
    archive = workspace / "runtime.agent-backup"
    manager.create(archive)
    tampered = workspace / "tampered.agent-backup"
    shutil.copy2(archive, tampered)

    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(tampered, "a") as bundle:
            bundle.writestr("runtime.sqlite3", b"tampered")

    verification = RuntimeBackupManager.verify(tampered)

    assert verification.valid is False
    assert verification.exit_code == 2
    assert "duplicate" in verification.errors[0].lower()


def test_restore_rejects_relocation_of_absolute_artifact_references(workspace: Path) -> None:
    source = workspace / "source"
    store = SQLiteStore(source / "runtime.sqlite3")
    store.close()
    archive = workspace / "runtime.agent-backup"
    RuntimeBackupManager(source / "runtime.sqlite3", source / "artifacts").create(archive)

    relocated = workspace / "relocated"
    manager = RuntimeBackupManager(
        relocated / "runtime.sqlite3", relocated / "artifacts"
    )
    with pytest.raises(BackupConflictError, match="original database and artifact paths"):
        manager.restore(archive)


def test_backup_cli_contract() -> None:
    parser = build_parser()
    create = parser.parse_args(["backup", "create", "--output", "state.agent-backup"])
    verify = parser.parse_args(["backup", "verify", "state.agent-backup"])
    restore = parser.parse_args(["backup", "restore", "state.agent-backup", "--force"])

    assert create.backup_command == "create"
    assert verify.backup_command == "verify"
    assert restore.backup_command == "restore"
    assert restore.force is True


def _valid_archive(workspace: Path) -> tuple[Path, RuntimeBackupManager]:
    state_dir = workspace / "state"
    store = SQLiteStore(state_dir / "runtime.sqlite3")
    store.close()
    artifact = state_dir / "artifacts" / "run" / "value.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("artifact", encoding="utf-8")
    manager = RuntimeBackupManager(state_dir / "runtime.sqlite3", state_dir / "artifacts")
    archive = workspace / "valid.agent-backup"
    manager.create(archive)
    return archive, manager


def _rewrite_archive(source: Path, destination: Path, case: str) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        entries = {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if not info.is_dir()
        }
    manifest = json.loads(entries["manifest.json"].decode("utf-8"))
    artifact_name = next(name for name in entries if name.startswith("artifacts/"))

    if case == "unsupported_format":
        manifest["format_version"] = 99
    elif case == "database_not_mapping":
        manifest["database"] = []
    elif case == "artifacts_not_mapping":
        manifest["artifacts"] = []
    elif case == "database_entry":
        manifest["database"]["entry"] = "other.sqlite3"
    elif case == "database_bytes_type":
        manifest["database"]["bytes"] = -1
    elif case == "database_sha_type":
        manifest["database"]["sha256"] = "short"
    elif case == "database_size":
        manifest["database"]["bytes"] += 1
    elif case == "database_sha":
        manifest["database"]["sha256"] = "0" * 64
    elif case == "schema":
        manifest["database"]["schema_version"] = 999
    elif case == "counts":
        manifest["database"]["counts"]["runs"] += 1
    elif case == "migrations":
        manifest["database"]["migrations"] = []
    elif case == "artifact_files_type":
        manifest["artifacts"]["files"] = "invalid"
    elif case == "artifact_record_type":
        manifest["artifacts"]["files"] = [1]
    elif case == "artifact_path":
        manifest["artifacts"]["files"][0]["path"] = "../escape"
    elif case == "artifact_duplicate":
        manifest["artifacts"]["files"].append(
            dict(manifest["artifacts"]["files"][0])
        )
        manifest["artifacts"]["count"] += 1
        manifest["artifacts"]["bytes"] *= 2
    elif case == "artifact_missing":
        entries.pop(artifact_name)
    elif case == "artifact_bytes_type":
        manifest["artifacts"]["files"][0]["bytes"] = -1
    elif case == "artifact_sha_type":
        manifest["artifacts"]["files"][0]["sha256"] = "short"
    elif case == "artifact_size":
        manifest["artifacts"]["files"][0]["bytes"] += 1
    elif case == "artifact_sha":
        manifest["artifacts"]["files"][0]["sha256"] = "0" * 64
    elif case == "artifact_extra":
        entries["artifacts/extra.txt"] = b"extra"
    elif case == "artifact_count":
        manifest["artifacts"]["count"] += 1
    elif case == "artifact_total_bytes":
        manifest["artifacts"]["bytes"] += 1
    elif case == "unexpected_entry":
        entries["unexpected.txt"] = b"unexpected"
    elif case == "unsafe_entry":
        entries["../escape.txt"] = b"unsafe"
    elif case == "missing_manifest":
        entries.pop("manifest.json")
    elif case == "missing_database":
        entries.pop("runtime.sqlite3")
    elif case == "manifest_json":
        entries["manifest.json"] = b"not-json"
    elif case == "manifest_root":
        entries["manifest.json"] = b"[]"
    else:
        raise AssertionError(case)

    if case not in {"manifest_json", "manifest_root", "missing_manifest"}:
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


@pytest.mark.parametrize(
    "case",
    [
        "unsupported_format",
        "database_not_mapping",
        "artifacts_not_mapping",
        "database_entry",
        "database_bytes_type",
        "database_sha_type",
        "database_size",
        "database_sha",
        "schema",
        "counts",
        "migrations",
        "artifact_files_type",
        "artifact_record_type",
        "artifact_path",
        "artifact_duplicate",
        "artifact_missing",
        "artifact_bytes_type",
        "artifact_sha_type",
        "artifact_size",
        "artifact_sha",
        "artifact_extra",
        "artifact_count",
        "artifact_total_bytes",
        "unexpected_entry",
        "unsafe_entry",
        "missing_manifest",
        "missing_database",
        "manifest_json",
        "manifest_root",
    ],
)
def test_backup_verification_rejects_invalid_archive_contracts(
    workspace: Path, case: str
) -> None:
    source, _ = _valid_archive(workspace)
    invalid = workspace / f"{case}.agent-backup"
    _rewrite_archive(source, invalid, case)

    verification = RuntimeBackupManager.verify(invalid)

    assert verification.valid is False
    assert verification.errors


def test_backup_creation_conflicts_and_overwrite(workspace: Path) -> None:
    missing = RuntimeBackupManager(workspace / "missing.sqlite3")
    with pytest.raises(BackupError, match="not found"):
        missing.create(workspace / "missing.agent-backup")

    archive, manager = _valid_archive(workspace)
    with pytest.raises(BackupConflictError, match="source database"):
        manager.create(manager.database_path)
    with pytest.raises(BackupConflictError, match="Artifact Store"):
        manager.create(manager.artifact_path / "nested.agent-backup")
    with pytest.raises(BackupConflictError, match="already exists"):
        manager.create(archive)
    replaced = manager.create(archive, overwrite=True)
    assert replaced.to_dict()["archive_path"] == str(archive)
    assert manager.artifact_path.name == "artifacts"


def test_verify_rejects_missing_and_bad_zip(workspace: Path) -> None:
    missing = RuntimeBackupManager.verify(workspace / "missing.agent-backup")
    assert missing.valid is False
    bad = workspace / "bad.agent-backup"
    bad.write_bytes(b"not a zip")
    invalid = RuntimeBackupManager.verify(bad)
    assert invalid.valid is False
    assert "cannot be read" in invalid.errors[0]


def test_restore_without_existing_state_has_no_rollback_copy(workspace: Path) -> None:
    archive, manager = _valid_archive(workspace)
    manager.database_path.unlink()
    shutil.rmtree(manager.artifact_path)

    restored = manager.restore(archive)

    assert restored.previous_database_path is None
    assert restored.previous_artifact_path is None
    assert restored.to_dict()["schema_version"] == 8


def test_restore_rolls_back_when_artifact_install_fails(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manager = _valid_archive(workspace)
    original_database = manager.database_path.read_bytes()
    original_artifact = (manager.artifact_path / "run" / "value.txt").read_text(
        encoding="utf-8"
    )
    original_replace = backup_module.os.replace
    calls = 0

    def fail_fourth_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected artifact install failure")
        return original_replace(source, destination)

    monkeypatch.setattr(backup_module.os, "replace", fail_fourth_replace)
    with pytest.raises(OSError, match="injected"):
        manager.restore(archive, overwrite=True)

    assert manager.database_path.read_bytes() == original_database
    assert (manager.artifact_path / "run" / "value.txt").read_text(
        encoding="utf-8"
    ) == original_artifact


def test_offline_and_sidecar_errors_are_classified(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = workspace / "runtime.sqlite3"
    database.write_bytes(b"not sqlite")
    with pytest.raises(BackupTargetBusyError):
        RuntimeBackupManager._assert_database_offline(database)

    real_unlink = Path.unlink

    def fail_sidecar(self: Path, *args, **kwargs):
        if str(self).endswith("-wal"):
            raise OSError("busy")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_sidecar)
    with pytest.raises(BackupTargetBusyError, match="sidecar"):
        RuntimeBackupManager._remove_sqlite_sidecars(database)


def test_inspect_database_rejects_missing_schema_and_bad_migrations(workspace: Path) -> None:
    empty = workspace / "empty.sqlite3"
    sqlite3.connect(empty).close()
    with pytest.raises(BackupError, match="schema_migrations"):
        RuntimeBackupManager._inspect_database(empty)

    unknown = workspace / "unknown.sqlite3"
    connection = sqlite3.connect(unknown)
    connection.execute(
        "CREATE TABLE schema_migrations(version INTEGER, name TEXT, checksum TEXT)"
    )
    connection.execute(
        "INSERT INTO schema_migrations VALUES (999, 'unknown', 'invalid')"
    )
    connection.commit()
    connection.close()
    with pytest.raises(BackupError, match="Unknown migration"):
        RuntimeBackupManager._inspect_database(unknown)


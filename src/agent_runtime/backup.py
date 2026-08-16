from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .domain import BackupConflictError, BackupError, BackupTargetBusyError
from .storage import MIGRATIONS, _migration_checksum

BACKUP_FORMAT_VERSION = 1
DATABASE_ENTRY = "runtime.sqlite3"
MANIFEST_ENTRY = "manifest.json"
ARTIFACT_PREFIX = "artifacts/"
COUNT_TABLES = (
    "runs",
    "events",
    "checkpoints",
    "steps",
    "tool_executions",
    "approvals",
    "run_relations",
    "workflow_snapshots",
    "sessions",
    "memory_records",
    "agent_definitions",
)


@dataclass(frozen=True, slots=True)
class BackupVerification:
    valid: bool
    archive_path: str
    format_version: int | None
    schema_version: int | None
    database_sha256: str | None
    artifact_count: int
    errors: tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        return 0 if self.valid else 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BackupCreation:
    archive_path: str
    created_at: str
    schema_version: int
    database_sha256: str
    database_bytes: int
    artifact_count: int
    artifact_bytes: int
    counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RestoreResult:
    archive_path: str
    database_path: str
    artifact_path: str
    schema_version: int
    artifact_count: int
    previous_database_path: str | None
    previous_artifact_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _ValidatedArchive:
    manifest: dict[str, Any]
    database_path: Path
    artifact_path: Path


class RuntimeBackupManager:
    """Create, verify, and restore self-contained Runtime state archives."""

    def __init__(
        self,
        database_path: str | Path,
        artifact_path: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.artifact_path = Path(
            artifact_path or self.database_path.parent / "artifacts"
        ).resolve()

    def create(
        self,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> BackupCreation:
        destination_path = Path(destination).resolve()
        if not self.database_path.is_file():
            raise BackupError(f"Runtime database was not found: {self.database_path}")
        if destination_path == self.database_path:
            raise BackupConflictError("Backup archive cannot replace the source database.")
        if destination_path == self.artifact_path or self.artifact_path in destination_path.parents:
            raise BackupConflictError(
                "Backup archive cannot be created inside the Artifact Store."
            )
        if destination_path.exists() and not overwrite:
            raise BackupConflictError(
                f"Backup archive already exists: {destination_path}. Use overwrite=True to replace it."
            )
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        with _staging_directory(destination_path.parent, ".agent-backup-stage-") as stage:
            snapshot = stage / DATABASE_ENTRY
            self._snapshot_database(snapshot)
            database = self._inspect_database(snapshot)
            artifact_entries, artifact_bytes = self._copy_artifacts(stage / "artifacts")
            database_sha256 = _sha256_file(snapshot)
            database_bytes = snapshot.stat().st_size
            created_at = datetime.now(UTC).isoformat()
            manifest: dict[str, Any] = {
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": created_at,
                "source": {
                    "database_path": str(self.database_path),
                    "artifact_path": str(self.artifact_path),
                },
                "database": {
                    "entry": DATABASE_ENTRY,
                    "sha256": database_sha256,
                    "bytes": database_bytes,
                    "schema_version": database["schema_version"],
                    "counts": database["counts"],
                    "migrations": database["migrations"],
                },
                "artifacts": {
                    "prefix": ARTIFACT_PREFIX,
                    "count": len(artifact_entries),
                    "bytes": artifact_bytes,
                    "files": artifact_entries,
                },
            }
            (stage / MANIFEST_ENTRY).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            pending_archive = destination_path.with_name(
                f".{destination_path.name}.{uuid4().hex}.partial"
            )
            try:
                self._write_archive(stage, pending_archive)
                verification = self.verify(pending_archive)
                if not verification.valid:
                    raise BackupError(
                        "Generated backup failed verification: " + "; ".join(verification.errors)
                    )
                os.replace(pending_archive, destination_path)
            finally:
                pending_archive.unlink(missing_ok=True)

        return BackupCreation(
            archive_path=str(destination_path),
            created_at=created_at,
            schema_version=int(database["schema_version"]),
            database_sha256=database_sha256,
            database_bytes=database_bytes,
            artifact_count=len(artifact_entries),
            artifact_bytes=artifact_bytes,
            counts={str(key): int(value) for key, value in database["counts"].items()},
        )

    @classmethod
    def verify(cls, archive: str | Path) -> BackupVerification:
        archive_path = Path(archive).resolve()
        try:
            with _staging_directory(
                archive_path.parent, ".agent-backup-verify-"
            ) as stage:
                validated = cls._extract_and_validate(archive_path, stage)
                manifest = validated.manifest
                database = manifest["database"]
                artifacts = manifest["artifacts"]
                return BackupVerification(
                    valid=True,
                    archive_path=str(archive_path),
                    format_version=int(manifest["format_version"]),
                    schema_version=int(database["schema_version"]),
                    database_sha256=str(database["sha256"]),
                    artifact_count=int(artifacts["count"]),
                )
        except Exception as error:
            return BackupVerification(
                valid=False,
                archive_path=str(archive_path),
                format_version=None,
                schema_version=None,
                database_sha256=None,
                artifact_count=0,
                errors=(f"{type(error).__name__}: {error}",),
            )

    def restore(
        self,
        archive: str | Path,
        *,
        overwrite: bool = False,
        keep_previous: bool = True,
    ) -> RestoreResult:
        archive_path = Path(archive).resolve()
        target_exists = self.database_path.exists() or self.artifact_path.exists()
        if target_exists and not overwrite:
            raise BackupConflictError(
                "Runtime state already exists. Pass overwrite=True only after stopping the Runtime."
            )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.database_path.exists():
            self._assert_database_offline(self.database_path)

        with _staging_directory(
            self.database_path.parent, ".agent-restore-stage-"
        ) as stage:
            validated = self._extract_and_validate(archive_path, stage)
            self._validate_restore_layout(validated.manifest)
            restored_database = validated.database_path
            restored_artifacts = validated.artifact_path
            manifest = validated.manifest

            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            previous_database = self.database_path.with_name(
                f"{self.database_path.name}.pre-restore-{stamp}-{uuid4().hex[:8]}"
            )
            previous_artifacts = self.artifact_path.with_name(
                f"{self.artifact_path.name}.pre-restore-{stamp}-{uuid4().hex[:8]}"
            )
            saved_database: Path | None = None
            saved_artifacts: Path | None = None
            try:
                self._remove_sqlite_sidecars(self.database_path)
                if self.database_path.exists():
                    os.replace(self.database_path, previous_database)
                    saved_database = previous_database
                if self.artifact_path.exists():
                    os.replace(self.artifact_path, previous_artifacts)
                    saved_artifacts = previous_artifacts

                os.replace(restored_database, self.database_path)
                self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(restored_artifacts, self.artifact_path)
                inspected = self._inspect_database(self.database_path)
                expected_schema = int(manifest["database"]["schema_version"])
                if int(inspected["schema_version"]) != expected_schema:
                    raise BackupError("Restored database schema changed during installation.")
            except Exception:
                if self.database_path.exists():
                    self.database_path.unlink(missing_ok=True)
                if self.artifact_path.exists():
                    shutil.rmtree(self.artifact_path, ignore_errors=True)
                if saved_database is not None and saved_database.exists():
                    os.replace(saved_database, self.database_path)
                if saved_artifacts is not None and saved_artifacts.exists():
                    os.replace(saved_artifacts, self.artifact_path)
                raise

            if not keep_previous:
                if saved_database is not None:
                    saved_database.unlink(missing_ok=True)
                if saved_artifacts is not None:
                    shutil.rmtree(saved_artifacts, ignore_errors=True)

            return RestoreResult(
                archive_path=str(archive_path),
                database_path=str(self.database_path),
                artifact_path=str(self.artifact_path),
                schema_version=int(manifest["database"]["schema_version"]),
                artifact_count=int(manifest["artifacts"]["count"]),
                previous_database_path=str(saved_database) if saved_database is not None else None,
                previous_artifact_path=str(saved_artifacts) if saved_artifacts is not None else None,
            )

    def _snapshot_database(self, destination: Path) -> None:
        source_uri = f"{self.database_path.as_uri()}?mode=ro"
        try:
            source = sqlite3.connect(source_uri, uri=True, timeout=5.0)
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
                source.close()
        except sqlite3.DatabaseError as error:
            raise BackupError(f"SQLite online backup failed: {error}") from error

    def _copy_artifacts(self, destination: Path) -> tuple[list[dict[str, Any]], int]:
        destination.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        total_bytes = 0
        if not self.artifact_path.exists():
            return entries, total_bytes
        for source in sorted(self.artifact_path.rglob("*")):
            if source.is_symlink():
                raise BackupError(f"Artifact symlinks are not supported: {source}")
            if not source.is_file():
                continue
            relative = source.relative_to(self.artifact_path)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            size = target.stat().st_size
            total_bytes += size
            entries.append(
                {
                    "path": relative.as_posix(),
                    "sha256": _sha256_file(target),
                    "bytes": size,
                }
            )
        return entries, total_bytes

    @staticmethod
    def _write_archive(stage: Path, destination: Path) -> None:
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.write(stage / MANIFEST_ENTRY, MANIFEST_ENTRY)
            archive.write(stage / DATABASE_ENTRY, DATABASE_ENTRY)
            artifact_root = stage / "artifacts"
            for path in sorted(artifact_root.rglob("*")):
                if path.is_file():
                    archive.write(path, ARTIFACT_PREFIX + path.relative_to(artifact_root).as_posix())
        with destination.open("rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    @classmethod
    def _extract_and_validate(cls, archive_path: Path, destination: Path) -> _ValidatedArchive:
        if not archive_path.is_file():
            raise BackupError(f"Backup archive was not found: {archive_path}")
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                files = [info for info in archive.infolist() if not info.is_dir()]
                names = [info.filename for info in files]
                if len(names) != len(set(names)):
                    raise BackupError("Backup archive contains duplicate entries.")
                for name in names:
                    _validate_archive_name(name)
                if MANIFEST_ENTRY not in names or DATABASE_ENTRY not in names:
                    raise BackupError("Backup archive is missing manifest.json or runtime.sqlite3.")
                unexpected = [
                    name
                    for name in names
                    if name not in {MANIFEST_ENTRY, DATABASE_ENTRY}
                    and not name.startswith(ARTIFACT_PREFIX)
                ]
                if unexpected:
                    raise BackupError(f"Backup archive contains unexpected entries: {unexpected}")
                archive.extractall(destination)
        except (OSError, zipfile.BadZipFile) as error:
            raise BackupError(f"Backup archive cannot be read: {error}") from error

        manifest = _load_manifest(destination / MANIFEST_ENTRY)
        if int(manifest.get("format_version", -1)) != BACKUP_FORMAT_VERSION:
            raise BackupError(
                f"Unsupported backup format version: {manifest.get('format_version')!r}."
            )
        database = _require_mapping(manifest, "database")
        artifacts = _require_mapping(manifest, "artifacts")
        database_path = destination / DATABASE_ENTRY
        if database.get("entry") != DATABASE_ENTRY:
            raise BackupError("Manifest database entry is invalid.")
        _verify_file_record(database_path, database)
        inspected = cls._inspect_database(database_path)
        if int(database.get("schema_version", -1)) != int(inspected["schema_version"]):
            raise BackupError("Manifest schema version does not match the database.")
        if database.get("counts") != inspected["counts"]:
            raise BackupError("Manifest table counts do not match the database.")
        if database.get("migrations") != inspected["migrations"]:
            raise BackupError("Manifest migration records do not match the database.")

        artifact_root = destination / "artifacts"
        artifact_root.mkdir(exist_ok=True)
        records = artifacts.get("files")
        if not isinstance(records, list):
            raise BackupError("Manifest artifacts.files must be a list.")
        expected_paths: set[str] = set()
        total_bytes = 0
        for value in records:
            if not isinstance(value, dict):
                raise BackupError("Manifest artifact record must be an object.")
            relative = str(value.get("path", ""))
            _validate_relative_artifact_path(relative)
            if relative in expected_paths:
                raise BackupError(f"Duplicate artifact record: {relative}")
            expected_paths.add(relative)
            path = artifact_root / Path(PurePosixPath(relative))
            _verify_file_record(path, value)
            total_bytes += path.stat().st_size
        actual_paths = {
            path.relative_to(artifact_root).as_posix()
            for path in artifact_root.rglob("*")
            if path.is_file()
        }
        if actual_paths != expected_paths:
            raise BackupError("Artifact manifest does not match archive contents.")
        if int(artifacts.get("count", -1)) != len(records):
            raise BackupError("Artifact count is inconsistent.")
        if int(artifacts.get("bytes", -1)) != total_bytes:
            raise BackupError("Artifact byte count is inconsistent.")
        return _ValidatedArchive(manifest, database_path, artifact_root)

    @staticmethod
    def _inspect_database(path: Path) -> dict[str, Any]:
        try:
            uri = f"{path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            connection.row_factory = sqlite3.Row
            try:
                quick = connection.execute("PRAGMA quick_check").fetchone()
                if quick is None or str(quick[0]).lower() != "ok":
                    raise BackupError(
                        f"SQLite quick_check failed: {quick[0] if quick is not None else 'no result'}"
                    )
                foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
                if foreign_key_errors:
                    raise BackupError(
                        f"SQLite foreign_key_check reported {len(foreign_key_errors)} violation(s)."
                    )
                tables = {
                    str(row["name"])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "schema_migrations" not in tables:
                    raise BackupError("Database has no schema_migrations table.")
                rows = connection.execute(
                    "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
                expected = {
                    version: (name, _migration_checksum(version, name, sql))
                    for version, name, sql in MIGRATIONS
                }
                migrations: list[dict[str, Any]] = []
                for row in rows:
                    version = int(row["version"])
                    if version not in expected:
                        raise BackupError(f"Unknown migration version in backup: {version}")
                    expected_name, expected_checksum = expected[version]
                    if str(row["name"]) != expected_name:
                        raise BackupError(f"Migration {version} name mismatch.")
                    if str(row["checksum"]) != expected_checksum:
                        raise BackupError(f"Migration {version} checksum mismatch.")
                    migrations.append(
                        {
                            "version": version,
                            "name": expected_name,
                            "checksum": expected_checksum,
                        }
                    )
                schema_version = max((item["version"] for item in migrations), default=0)
                counts = {
                    table: int(
                        connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    )
                    for table in COUNT_TABLES
                    if table in tables
                }
                return {
                    "schema_version": schema_version,
                    "counts": counts,
                    "migrations": migrations,
                }
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise BackupError(f"SQLite backup verification failed: {error}") from error

    def _validate_restore_layout(self, manifest: dict[str, Any]) -> None:
        source = _require_mapping(manifest, "source")
        source_database = Path(str(source.get("database_path", ""))).resolve()
        source_artifacts = Path(str(source.get("artifact_path", ""))).resolve()
        if source_database != self.database_path or source_artifacts != self.artifact_path:
            raise BackupConflictError(
                "v0.7.10 restore requires the original database and artifact paths because "
                "existing ToolExecution records contain absolute artifact references."
            )

    @staticmethod
    def _assert_database_offline(path: Path) -> None:
        try:
            connection = sqlite3.connect(path, timeout=0.1)
            try:
                connection.execute("PRAGMA busy_timeout=100")
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is not None and int(checkpoint[0]) != 0:
                    raise BackupTargetBusyError(
                        "SQLite WAL checkpoint is busy; stop every Runtime before restore."
                    )
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
            finally:
                connection.close()
        except BackupTargetBusyError:
            raise
        except sqlite3.DatabaseError as error:
            raise BackupTargetBusyError(
                f"Runtime database is busy; stop the Runtime before restore: {error}"
            ) from error

    @staticmethod
    def _remove_sqlite_sidecars(path: Path) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            try:
                sidecar.unlink(missing_ok=True)
            except OSError as error:
                raise BackupTargetBusyError(
                    f"Cannot remove SQLite sidecar {sidecar}; Runtime may still be running."
                ) from error



@contextmanager
def _staging_directory(parent: Path, prefix: str) -> Iterator[Path]:
    path = parent / f"{prefix}{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackupError(f"Backup manifest cannot be read: {error}") from error
    if not isinstance(value, dict):
        raise BackupError("Backup manifest root must be an object.")
    return value


def _require_mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise BackupError(f"Manifest {key} must be an object.")
    return item


def _verify_file_record(path: Path, record: dict[str, Any]) -> None:
    if not path.is_file():
        raise BackupError(f"Backup file is missing: {path.name}")
    expected_bytes = record.get("bytes")
    expected_sha256 = record.get("sha256")
    if not isinstance(expected_bytes, int) or expected_bytes < 0:
        raise BackupError(f"Invalid byte count for {path.name}.")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise BackupError(f"Invalid SHA-256 for {path.name}.")
    if path.stat().st_size != expected_bytes:
        raise BackupError(f"Byte count mismatch for {path.name}.")
    if _sha256_file(path) != expected_sha256:
        raise BackupError(f"SHA-256 mismatch for {path.name}.")


def _validate_archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise BackupError(f"Unsafe backup archive entry: {name!r}")


def _validate_relative_artifact_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise BackupError(f"Unsafe artifact path in manifest: {value!r}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

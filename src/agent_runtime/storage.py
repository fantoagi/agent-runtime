from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from .domain import (
    AgentDefinition,
    AgentRun,
    Approval,
    Checkpoint,
    IdempotencyConflict,
    MemoryRecord,
    MemoryScope,
    MemorySearchResult,
    Message,
    MigrationError,
    ModelConfig,
    RunNotFound,
    RunRelation,
    RunRelationType,
    RuntimeClosedError,
    RuntimeEvent,
    Session,
    Step,
    StepStatus,
    StoreBusyError,
    StoreCorruptionError,
    StoreError,
    ToolCall,
    ToolDefinition,
    ToolExecution,
    ToolExecutionStatus,
    UnknownToolResolution,
    utc_now,
)

Migration = tuple[int, str, str]

MIGRATIONS: tuple[Migration, ...] = (
    (
        1,
        "initial_runtime_schema",
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            input TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            result TEXT,
            error TEXT,
            step_count INTEGER NOT NULL,
            tool_call_count INTEGER NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            sequence INTEGER NOT NULL,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS checkpoints (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            step INTEGER NOT NULL,
            messages_json TEXT NOT NULL,
            tool_call_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            tool_call_json TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_run_sequence
            ON events(run_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_checkpoints_run_step
            ON checkpoints(run_id, step DESC);
        CREATE INDEX IF NOT EXISTS idx_approvals_run_status
            ON approvals(run_id, status);
        """,
    ),
    (
        2,
        "durable_steps_and_tool_executions",
        """
        CREATE TABLE IF NOT EXISTS steps (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            step_index INTEGER NOT NULL,
            status TEXT NOT NULL,
            assistant_message_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, step_index)
        );
        CREATE TABLE IF NOT EXISTS tool_executions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            step_id TEXT NOT NULL REFERENCES steps(id),
            position INTEGER NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_content TEXT,
            result_data_json TEXT,
            error TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            requires_approval INTEGER NOT NULL DEFAULT 0,
            side_effecting INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            UNIQUE(run_id, tool_call_id)
        );
        CREATE INDEX IF NOT EXISTS idx_steps_run_index
            ON steps(run_id, step_index);
        CREATE INDEX IF NOT EXISTS idx_tool_executions_run_status
            ON tool_executions(run_id, status, position);
        """,
    ),
    (
        3,
        "parent_child_run_relations",
        """
        CREATE TABLE IF NOT EXISTS run_relations (
            id TEXT PRIMARY KEY,
            parent_run_id TEXT NOT NULL REFERENCES runs(id),
            child_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
            root_run_id TEXT NOT NULL REFERENCES runs(id),
            relation_type TEXT NOT NULL,
            delegation_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(parent_run_id, delegation_key)
        );
        CREATE INDEX IF NOT EXISTS idx_run_relations_parent
            ON run_relations(parent_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_run_relations_root
            ON run_relations(root_run_id, created_at);
        """,
    ),
    (
        4,
        "sessions_context_and_memory",
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_runs (
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
            created_at TEXT NOT NULL,
            PRIMARY KEY(session_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_session_runs_session
            ON session_runs(session_id, created_at);
        CREATE TABLE IF NOT EXISTS memory_records (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            content TEXT NOT NULL,
            source_run_id TEXT REFERENCES runs(id),
            source_trace_id TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            deleted_at TEXT,
            metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_scope_lifecycle
            ON memory_records(scope, scope_id, deleted_at, expires_at, created_at);
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            memory_id UNINDEXED,
            content,
            tokenize='unicode61'
        );
        """,
    ),
    (
        5,
        "runtime_reliability_metadata",
        """
        CREATE TABLE IF NOT EXISTS workflow_snapshots (
            run_id TEXT PRIMARY KEY REFERENCES runs(id),
            definition_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tool_executions_running
            ON tool_executions(run_id, status, side_effecting);
        """,
    ),
    (
        6,
        "unknown_tool_resolution_audit",
        """
        ALTER TABLE tool_executions ADD COLUMN resolution TEXT;
        ALTER TABLE tool_executions ADD COLUMN resolution_reason TEXT;
        ALTER TABLE tool_executions ADD COLUMN resolved_by TEXT;
        ALTER TABLE tool_executions ADD COLUMN resolved_at TEXT;
        CREATE INDEX IF NOT EXISTS idx_tool_executions_unknown
            ON tool_executions(status, run_id, created_at);
        """,
    ),
    (
        7,
        "durable_run_submission_idempotency",
        """
        ALTER TABLE runs ADD COLUMN idempotency_key TEXT;
        ALTER TABLE runs ADD COLUMN request_fingerprint TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency_key
            ON runs(idempotency_key) WHERE idempotency_key IS NOT NULL;
        """,
    ),
    (
        8,
        "agent_definition_snapshots",
        """
        CREATE TABLE IF NOT EXISTS agent_definitions (
            checksum TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_definitions_name_created
            ON agent_definitions(name, created_at DESC);
        ALTER TABLE runs ADD COLUMN agent_definition_checksum TEXT
            REFERENCES agent_definitions(checksum);
        CREATE INDEX IF NOT EXISTS idx_runs_agent_definition
            ON runs(agent_definition_checksum);
        """,
    ),
)


class SQLiteStore:
    """Durable SQLite store with explicit migrations and bounded lock waits."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_seconds: float = 5.0,
        synchronous: str = "FULL",
        lock_retry_attempts: int = 3,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_seconds = busy_timeout_seconds
        self.lock_retry_attempts = lock_retry_attempts
        normalized_synchronous = synchronous.upper()
        if normalized_synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
            raise ValueError("synchronous must be OFF, NORMAL, FULL, or EXTRA.")
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=busy_timeout_seconds,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._closed = False
        try:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute(
                f"PRAGMA busy_timeout={int(busy_timeout_seconds * 1000)}"
            )
            self._connection.execute(f"PRAGMA synchronous={normalized_synchronous}")
        except sqlite3.DatabaseError as error:
            self._connection.close()
            self._closed = True
            message = str(error).lower()
            if "not a database" in message or "malformed" in message:
                raise StoreCorruptionError(
                    f"SQLite initialization detected a corrupt database: {error}"
                ) from error
            raise StoreError(f"SQLite initialization failed: {error}") from error
        self._quick_check()
        self._migrate()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeClosedError(f"SQLite store is closed: {self.path}")

    def _quick_check(self) -> None:
        try:
            row = self._connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as error:
            raise StoreCorruptionError(f"SQLite quick_check failed: {error}") from error
        if row is None or str(row[0]).lower() != "ok":
            detail = row[0] if row is not None else "no result"
            raise StoreCorruptionError(f"SQLite quick_check reported: {detail}")

    def health_check(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            self._connection.execute("SELECT 1").fetchone()
            self._quick_check()
            return {
                "status": "ok",
                "schema_version": self.schema_version,
                "journal_mode": str(
                    self._connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower(),
            }

    def diagnostic_snapshot(self, run_id: str | None = None) -> dict[str, Any]:
        """Return a read-only consistency snapshot for Runtime Doctor."""
        with self._lock:
            self._ensure_open()
            where = " WHERE id=?" if run_id is not None else ""
            params: tuple[object, ...] = (run_id,) if run_id is not None else ()
            run_rows = self._connection.execute(
                f"SELECT id, status, metadata_json FROM runs{where} ORDER BY created_at",
                params,
            ).fetchall()
            run_ids = [str(row["id"]) for row in run_rows]
            if run_id is not None and not run_rows:
                raise RunNotFound(f"Run {run_id} was not found.")
            filter_sql = " AND run_id=?" if run_id is not None else ""
            filter_params = (run_id,) if run_id is not None else ()
            tool_rows = self._connection.execute(
                "SELECT id, run_id, status, side_effecting FROM tool_executions "
                f"WHERE 1=1{filter_sql} ORDER BY created_at",
                filter_params,
            ).fetchall()
            approval_rows = self._connection.execute(
                "SELECT id, run_id FROM approvals WHERE status='pending'"
                + filter_sql,
                filter_params,
            ).fetchall()
            duplicate_events = self._connection.execute(
                "SELECT run_id, sequence, COUNT(*) AS count FROM events WHERE 1=1"
                + filter_sql
                + " GROUP BY run_id, sequence HAVING COUNT(*) > 1",
                filter_params,
            ).fetchall()
            event_ranges = self._connection.execute(
                "SELECT run_id, COUNT(*) AS count, MIN(sequence) AS minimum, "
                "MAX(sequence) AS maximum FROM events WHERE 1=1"
                + filter_sql
                + " GROUP BY run_id",
                filter_params,
            ).fetchall()
            workflow_missing = self._connection.execute(
                """
                SELECT runs.id FROM runs
                LEFT JOIN workflow_snapshots ON workflow_snapshots.run_id=runs.id
                WHERE json_extract(runs.metadata_json, '$.run_kind')='workflow'
                  AND workflow_snapshots.run_id IS NULL
                """
                + (" AND runs.id=?" if run_id is not None else ""),
                filter_params,
            ).fetchall()
            orphan_steps = self._connection.execute(
                """
                SELECT steps.id FROM steps LEFT JOIN runs ON runs.id=steps.run_id
                WHERE runs.id IS NULL
                """
            ).fetchall()
            orphan_tools = self._connection.execute(
                """
                SELECT tool_executions.id FROM tool_executions
                LEFT JOIN runs ON runs.id=tool_executions.run_id
                LEFT JOIN steps ON steps.id=tool_executions.step_id
                WHERE runs.id IS NULL OR steps.id IS NULL
                """
            ).fetchall()
            snapshot_orphans = self._connection.execute(
                """
                SELECT workflow_snapshots.run_id FROM workflow_snapshots
                LEFT JOIN runs ON runs.id=workflow_snapshots.run_id
                WHERE runs.id IS NULL
                """
            ).fetchall()
            missing_agent_snapshots = self._connection.execute(
                """
                SELECT id FROM runs
                WHERE agent_definition_checksum IS NULL
                  AND status IN ('created', 'running', 'waiting_for_approval', 'paused')
                  AND COALESCE(json_extract(metadata_json, '$.run_kind'), 'agent') != 'workflow'
                """
                + (" AND id=?" if run_id is not None else ""),
                filter_params,
            ).fetchall()
            foreign_keys = int(self._connection.execute("PRAGMA foreign_keys").fetchone()[0])
            synchronous = int(self._connection.execute("PRAGMA synchronous").fetchone()[0])
        status_counts: dict[str, int] = {}
        for row in run_rows:
            status = str(row["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        gaps = [
            str(row["run_id"])
            for row in event_ranges
            if int(row["count"]) > 0
            and (int(row["minimum"]) != 1 or int(row["maximum"]) != int(row["count"]))
        ]
        return {
            "run_ids": run_ids,
            "run_status_counts": status_counts,
            "unknown_tool_execution_ids": [
                str(row["id"]) for row in tool_rows if row["status"] == "unknown"
            ],
            "running_tool_execution_ids": [
                str(row["id"]) for row in tool_rows if row["status"] == "running"
            ],
            "pending_approval_ids": [str(row["id"]) for row in approval_rows],
            "duplicate_event_sequences": [
                {"run_id": str(row["run_id"]), "sequence": int(row["sequence"])}
                for row in duplicate_events
            ],
            "event_sequence_gap_run_ids": gaps,
            "workflow_run_ids_without_snapshot": [str(row["id"]) for row in workflow_missing],
            "orphan_step_ids": [str(row["id"]) for row in orphan_steps],
            "orphan_tool_execution_ids": [str(row["id"]) for row in orphan_tools],
            "orphan_workflow_snapshot_run_ids": [str(row["run_id"]) for row in snapshot_orphans],
            "run_ids_missing_agent_snapshot": [str(row["id"]) for row in missing_agent_snapshots],
            "foreign_keys": foreign_keys,
            "synchronous": synchronous,
        }

    @property
    def schema_version(self) -> int:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
        return int(row["version"])

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._connection.close()
                self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _migrate(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    checksum TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(schema_migrations)"
                ).fetchall()
            }
            if "checksum" not in columns:
                self._connection.execute(
                    "ALTER TABLE schema_migrations ADD COLUMN checksum TEXT"
                )
            expected = {
                version: (name, _migration_checksum(version, name, sql))
                for version, name, sql in MIGRATIONS
            }
            applied_rows = self._connection.execute(
                "SELECT version, name, checksum FROM schema_migrations"
            ).fetchall()
            applied = {int(row["version"]): row for row in applied_rows}
            existing_tables = {
                row["name"]
                for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "runs" in existing_tables and 1 not in applied:
                name, checksum = expected[1]
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at, checksum) "
                    "VALUES (?, ?, ?, ?)",
                    (1, name, utc_now().isoformat(), checksum),
                )
                applied[1] = self._connection.execute(
                    "SELECT version, name, checksum FROM schema_migrations WHERE version=1"
                ).fetchone()
            for version, row in applied.items():
                if version not in expected:
                    raise MigrationError(f"Unknown applied migration version: {version}.")
                expected_name, checksum = expected[version]
                if row["name"] != expected_name:
                    raise MigrationError(
                        f"Migration {version} name changed from {row['name']!r} "
                        f"to {expected_name!r}."
                    )
                if row["checksum"] is None:
                    self._connection.execute(
                        "UPDATE schema_migrations SET checksum=? WHERE version=?",
                        (checksum, version),
                    )
                elif row["checksum"] != checksum:
                    raise MigrationError(f"Migration {version} checksum mismatch.")
            for version, name, sql in MIGRATIONS:
                if version in applied:
                    continue
                checksum = expected[version][1]
                applied_at = utc_now().isoformat().replace("'", "''")
                escaped_name = name.replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + sql
                    + "\nINSERT INTO schema_migrations(version, name, applied_at, checksum) "
                    + f"VALUES ({version}, '{escaped_name}', '{applied_at}', '{checksum}');\n"
                    + "COMMIT;"
                )
                try:
                    self._connection.executescript(script)
                except sqlite3.DatabaseError as error:
                    if self._connection.in_transaction:
                        self._connection.rollback()
                    raise MigrationError(
                        f"Failed to apply migration {version} ({name}): {error}"
                    ) from error

        self._ensure_approval_columns()

    def _ensure_approval_columns(self) -> None:
        with self._lock, self._connection:
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(approvals)").fetchall()
            }
            if "tool_execution_id" not in columns:
                self._connection.execute(
                    "ALTER TABLE approvals ADD COLUMN tool_execution_id TEXT"
                )
            if "kind" not in columns:
                self._connection.execute(
                    "ALTER TABLE approvals ADD COLUMN kind TEXT NOT NULL DEFAULT 'tool'"
                )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_approvals_execution ON approvals(tool_execution_id)"
            )

    def create_session(self, session: Session) -> Session:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO sessions (id, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    self._dump(session.metadata),
                ),
            )
        return session

    def get_session(self, session_id: str) -> Session:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Session {session_id} was not found.")
        return self._session_from_row(row)

    def list_sessions(self, limit: int = 50) -> list[Session]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def count_sessions(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()
        return int(row["count"])

    def attach_run_to_session(self, session_id: str, run_id: str) -> None:
        with self._lock, self._connection:
            self._require_session_locked(session_id)
            self.get_run(run_id)
            self._attach_run_to_session_locked(session_id, run_id)
            self._append_event_locked(
                run_id, "session.run.attached", {"session_id": session_id}
            )

    def _attach_run_to_session_locked(self, session_id: str, run_id: str) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO session_runs (session_id, run_id, created_at)
            VALUES (?, ?, ?)
            """,
            (session_id, run_id, utc_now().isoformat()),
        )
        self._connection.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (utc_now().isoformat(), session_id),
        )

    def _require_session_locked(self, session_id: str) -> None:
        row = self._connection.execute(
            "SELECT id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Session {session_id} was not found.")

    def session_runs(self, session_id: str) -> list[AgentRun]:
        self.get_session(session_id)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT runs.* FROM session_runs
                JOIN runs ON runs.id = session_runs.run_id
                WHERE session_runs.session_id = ?
                ORDER BY session_runs.created_at, runs.id
                """,
                (session_id,),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def session_for_run(self, run_id: str) -> Session | None:
        self.get_run(run_id)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT sessions.* FROM session_runs
                JOIN sessions ON sessions.id = session_runs.session_id
                WHERE session_runs.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return self._session_from_row(row) if row else None

    def save_memory(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock, self._connection:
            if record.scope is MemoryScope.SESSION:
                self._require_session_locked(record.scope_id)
            if record.source_run_id is not None:
                self.get_run(record.source_run_id)
            self._connection.execute(
                """
                INSERT INTO memory_records (
                    id, scope, scope_id, content, source_run_id, source_trace_id,
                    created_at, expires_at, deleted_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.scope.value,
                    record.scope_id,
                    record.content,
                    record.source_run_id,
                    record.source_trace_id,
                    record.created_at.isoformat(),
                    record.expires_at.isoformat() if record.expires_at else None,
                    record.deleted_at.isoformat() if record.deleted_at else None,
                    self._dump(record.metadata),
                ),
            )
            if record.deleted_at is None:
                self._connection.execute(
                    "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
                    (record.id, record.content),
                )
        return record

    def get_memory(self, memory_id: str) -> MemoryRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM memory_records WHERE id = ?", (memory_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Memory {memory_id} was not found.")
        return self._memory_from_row(row)

    def delete_memory(self, memory_id: str) -> MemoryRecord:
        record = self.get_memory(memory_id)
        if record.deleted_at is not None:
            return record
        record.deleted_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE memory_records SET deleted_at = ? WHERE id = ?",
                (record.deleted_at.isoformat(), record.id),
            )
            self._connection.execute(
                "DELETE FROM memory_fts WHERE memory_id = ?", (record.id,)
            )
        return record

    def purge_expired_memories(self, now: datetime | None = None) -> int:
        current = now or utc_now()
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT memory_records.id FROM memory_records
                JOIN memory_fts ON memory_fts.memory_id = memory_records.id
                WHERE memory_records.deleted_at IS NULL
                  AND memory_records.expires_at IS NOT NULL
                  AND memory_records.expires_at <= ?
                """,
                (current.isoformat(),),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            for memory_id in ids:
                self._connection.execute(
                    "DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,)
                )
        return len(ids)

    def search_memories(
        self,
        query: str,
        scopes: list[tuple[MemoryScope, str]] | tuple[tuple[MemoryScope, str], ...],
        *,
        limit: int = 5,
    ) -> list[MemorySearchResult]:
        if limit < 1 or not scopes:
            return []
        scope_sql = " OR ".join("(memory_records.scope = ? AND memory_records.scope_id = ?)" for _ in scopes)
        scope_values: list[str] = []
        for scope, scope_id in scopes:
            scope_values.extend([MemoryScope(scope).value, scope_id])
        now = utc_now().isoformat()
        fts_query = self._fts_query(query)
        with self._lock:
            if fts_query:
                rows = self._connection.execute(
                    f"""
                    SELECT memory_records.*, bm25(memory_fts) AS search_rank
                    FROM memory_fts
                    JOIN memory_records ON memory_records.id = memory_fts.memory_id
                    WHERE memory_fts MATCH ?
                      AND ({scope_sql})
                      AND memory_records.deleted_at IS NULL
                      AND (memory_records.expires_at IS NULL OR memory_records.expires_at > ?)
                    ORDER BY search_rank, memory_records.created_at DESC
                    LIMIT ?
                    """,
                    [fts_query, *scope_values, now, limit],
                ).fetchall()
            else:
                rows = self._connection.execute(
                    f"""
                    SELECT memory_records.*, 0.0 AS search_rank
                    FROM memory_records
                    WHERE ({scope_sql})
                      AND deleted_at IS NULL
                      AND (expires_at IS NULL OR expires_at > ?)
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    [*scope_values, now, limit],
                ).fetchall()
        return [
            MemorySearchResult(self._memory_from_row(row), float(row["search_rank"]))
            for row in rows
        ]

    def has_active_memories(
        self,
        scopes: list[tuple[MemoryScope, str]] | tuple[tuple[MemoryScope, str], ...],
    ) -> bool:
        if not scopes:
            return False
        scope_sql = " OR ".join("(scope = ? AND scope_id = ?)" for _ in scopes)
        values: list[str] = []
        for scope, scope_id in scopes:
            values.extend([MemoryScope(scope).value, scope_id])
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT 1 FROM memory_records
                WHERE ({scope_sql})
                  AND deleted_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                LIMIT 1
                """,
                [*values, utc_now().isoformat()],
            ).fetchone()
        return row is not None

    def memory_counts(self) -> dict[str, int]:
        now = utc_now().isoformat()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN deleted_at IS NULL AND (expires_at IS NULL OR expires_at > ?) THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted,
                    SUM(CASE WHEN deleted_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ? THEN 1 ELSE 0 END) AS expired
                FROM memory_records
                """,
                (now, now),
            ).fetchone()
        return {name: int(row[name] or 0) for name in ("total", "active", "deleted", "expired")}

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = [term.strip('"') for term in query.split() if term.strip('"')]
        return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)

    def save_agent_definition(self, agent: AgentDefinition) -> str:
        payload = self._agent_definition_payload(agent)
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        checksum = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO agent_definitions(
                    checksum, name, definition_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (checksum, agent.name, encoded, utc_now().isoformat()),
            )
        return checksum

    def get_agent_definition(self, checksum: str) -> AgentDefinition:
        with self._lock:
            row = self._connection.execute(
                "SELECT definition_json FROM agent_definitions WHERE checksum=?",
                (checksum,),
            ).fetchone()
        if row is None:
            raise KeyError(f"AgentDefinition snapshot {checksum!r} was not found.")
        return self._agent_definition_from_payload(self._load(row["definition_json"]))

    def latest_agent_definition(self, name: str) -> AgentDefinition | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT definition_json FROM agent_definitions
                WHERE name=? ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
        if row is None:
            return None
        return self._agent_definition_from_payload(self._load(row["definition_json"]))

    def agent_definition_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM agent_definitions"
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _agent_definition_payload(agent: AgentDefinition) -> dict[str, Any]:
        return {
            "name": agent.name,
            "system_prompt": agent.system_prompt,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "requires_approval": tool.requires_approval,
                    "side_effecting": tool.side_effecting,
                }
                for tool in agent.tools
            ],
            "model": {
                "provider": agent.model.provider,
                "model": agent.model.model,
                "temperature": agent.model.temperature,
                "max_tokens": agent.model.max_tokens,
                "extra": agent.model.extra,
            },
            "max_steps": agent.max_steps,
            "max_tool_calls": agent.max_tool_calls,
        }

    @staticmethod
    def _agent_definition_from_payload(payload: dict[str, Any]) -> AgentDefinition:
        return AgentDefinition(
            name=str(payload["name"]),
            system_prompt=str(payload["system_prompt"]),
            tools=[ToolDefinition(**tool) for tool in payload.get("tools", [])],
            model=ModelConfig(**payload.get("model", {})),
            max_steps=int(payload.get("max_steps", 20)),
            max_tool_calls=int(payload.get("max_tool_calls", 40)),
        )

    def create_run_with_event(
        self,
        run: AgentRun,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            if session_id is not None:
                self._require_session_locked(session_id)
            self._insert_run(run)
            event = self._append_event_locked(run.id, event_type, payload)
            if session_id is not None:
                self._attach_run_to_session_locked(session_id, run.id)
                self._append_event_locked(
                    run.id, "session.run.attached", {"session_id": session_id}
                )
            return event

    def create_run_idempotently(
        self,
        run: AgentRun,
        event_type: str,
        payload: dict[str, Any] | None,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        session_id: str | None = None,
    ) -> tuple[AgentRun, bool]:
        """Create a run once, or return the durable run bound to the same request."""
        for attempt in range(self.lock_retry_attempts + 1):
            with self._lock:
                self._ensure_open()
                try:
                    self._connection.execute("BEGIN IMMEDIATE")
                    existing = self._idempotent_run_locked(idempotency_key)
                    if existing is not None:
                        existing_run, existing_fingerprint = existing
                        if existing_fingerprint != request_fingerprint:
                            raise IdempotencyConflict(
                                "Idempotency key is already bound to a different run request."
                            )
                        self._connection.commit()
                        return existing_run, True
                    if session_id is not None:
                        self._require_session_locked(session_id)
                    self._insert_run(
                        run,
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                    )
                    self._append_event_locked(run.id, event_type, payload)
                    if session_id is not None:
                        self._attach_run_to_session_locked(session_id, run.id)
                        self._append_event_locked(
                            run.id, "session.run.attached", {"session_id": session_id}
                        )
                    self._connection.commit()
                    return run, False
                except sqlite3.OperationalError as error:
                    self._connection.rollback()
                    if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                        raise
                except BaseException:
                    self._connection.rollback()
                    raise
            if attempt >= self.lock_retry_attempts:
                break
            time.sleep(0.01 * (2**attempt) + random.uniform(0.0, 0.01))
        raise StoreBusyError("SQLite remained busy while admitting an idempotent run.")

    def idempotent_run(
        self, idempotency_key: str, request_fingerprint: str
    ) -> AgentRun | None:
        with self._lock:
            self._ensure_open()
            existing = self._idempotent_run_locked(idempotency_key)
        if existing is None:
            return None
        run, existing_fingerprint = existing
        if existing_fingerprint != request_fingerprint:
            raise IdempotencyConflict(
                "Idempotency key is already bound to a different run request."
            )
        return run

    def _idempotent_run_locked(
        self, idempotency_key: str
    ) -> tuple[AgentRun, str] | None:
        row = self._connection.execute(
            "SELECT * FROM runs WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if row is None:
            return None
        return self._run_from_row(row), str(row["request_fingerprint"] or "")

    def create_run(self, run: AgentRun) -> None:
        with self._lock, self._connection:
            self._insert_run(run)

    def _insert_run(
        self,
        run: AgentRun,
        *,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO runs (
                id, agent_name, input, status, created_at, updated_at, result, error,
                step_count, tool_call_count, metadata_json, idempotency_key,
                request_fingerprint, agent_definition_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *self._run_values(run),
                idempotency_key,
                request_fingerprint,
                run.metadata.get("agent_definition_checksum"),
            ),
        )

    def get_run(self, run_id: str) -> AgentRun:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFound(f"Run {run_id} was not found.")
        return self._run_from_row(row)

    def list_runs(self, limit: int = 50) -> list[AgentRun]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def create_child_run_with_relation(
        self,
        child: AgentRun,
        relation: RunRelation,
        *,
        parent_event_payload: dict[str, Any],
        child_event_payload: dict[str, Any],
    ) -> RunRelation:
        """Atomically persist a delegated child, its relation, and both event records."""
        with self._lock, self._connection:
            session_id = child.metadata.get("session_id")
            if session_id is not None:
                self._require_session_locked(str(session_id))
            self._insert_run(child)
            if session_id is not None:
                self._attach_run_to_session_locked(str(session_id), child.id)
            self._insert_run_relation_locked(relation)
            self._append_event_locked(
                relation.parent_run_id, "delegation.created", parent_event_payload
            )
            self._append_event_locked(child.id, "run.created", child_event_payload)
            if session_id is not None:
                self._append_event_locked(
                    child.id, "session.run.attached", {"session_id": str(session_id)}
                )
        return relation

    def _insert_run_relation_locked(self, relation: RunRelation) -> None:
        self._connection.execute(
            """
            INSERT INTO run_relations (
                id, parent_run_id, child_run_id, root_run_id, relation_type,
                delegation_key, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation.id,
                relation.parent_run_id,
                relation.child_run_id,
                relation.root_run_id,
                relation.relation_type.value,
                relation.delegation_key,
                relation.created_at.isoformat(),
                self._dump(relation.metadata),
            ),
        )

    def get_run_relation(self, child_run_id: str) -> RunRelation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM run_relations WHERE child_run_id = ?", (child_run_id,)
            ).fetchone()
        return self._run_relation_from_row(row) if row else None

    def get_delegation(self, parent_run_id: str, delegation_key: str) -> RunRelation | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM run_relations
                WHERE parent_run_id = ? AND delegation_key = ?
                """,
                (parent_run_id, delegation_key),
            ).fetchone()
        return self._run_relation_from_row(row) if row else None

    def child_relations(self, parent_run_id: str) -> list[RunRelation]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM run_relations
                WHERE parent_run_id = ? ORDER BY created_at, id
                """,
                (parent_run_id,),
            ).fetchall()
        return [self._run_relation_from_row(row) for row in rows]

    def child_runs(self, parent_run_id: str) -> list[AgentRun]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT runs.* FROM run_relations
                JOIN runs ON runs.id = run_relations.child_run_id
                WHERE run_relations.parent_run_id = ?
                ORDER BY run_relations.created_at, run_relations.id
                """,
                (parent_run_id,),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def relations_for_root(self, root_run_id: str) -> list[RunRelation]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM run_relations
                WHERE root_run_id = ? ORDER BY created_at, id
                """,
                (root_run_id,),
            ).fetchall()
        return [self._run_relation_from_row(row) for row in rows]

    def root_run_id(self, run_id: str) -> str:
        self.get_run(run_id)
        relation = self.get_run_relation(run_id)
        return relation.root_run_id if relation is not None else run_id

    def descendant_runs(self, parent_run_id: str) -> list[AgentRun]:
        self.get_run(parent_run_id)
        with self._lock:
            rows = self._connection.execute(
                """
                WITH RECURSIVE descendants(child_run_id, depth) AS (
                    SELECT child_run_id, 1 FROM run_relations WHERE parent_run_id = ?
                    UNION ALL
                    SELECT relation.child_run_id, descendants.depth + 1
                    FROM run_relations AS relation
                    JOIN descendants ON relation.parent_run_id = descendants.child_run_id
                )
                SELECT runs.* FROM descendants
                JOIN runs ON runs.id = descendants.child_run_id
                ORDER BY descendants.depth DESC, runs.created_at DESC
                """,
                (parent_run_id,),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def count_run_relations(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM run_relations"
            ).fetchone()
        return int(row["count"])

    def save_workflow_snapshot(self, run_id: str, definition: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO workflow_snapshots(run_id, definition_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET definition_json=excluded.definition_json
                """,
                (run_id, self._dump(definition), utc_now().isoformat()),
            )

    def workflow_snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT definition_json FROM workflow_snapshots WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return self._load(row["definition_json"]) if row is not None else None

    def incomplete_runs(self) -> list[AgentRun]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM runs
                WHERE status IN ('created', 'running', 'waiting_for_approval', 'paused')
                ORDER BY created_at
                """
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def save_run(self, run: AgentRun) -> None:
        with self._lock, self._connection:
            self._update_run_locked(run)

    def save_run_with_event(
        self,
        run: AgentRun,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._update_run_locked(run)
            if before_commit is not None:
                before_commit()
            return self._append_event_locked(run.id, event_type, payload)

    def _update_run_locked(self, run: AgentRun) -> None:
        cursor = self._connection.execute(
            """
            UPDATE runs SET status=?, updated_at=?, result=?, error=?, step_count=?,
            tool_call_count=?, metadata_json=? WHERE id=?
            """,
            (
                run.status.value,
                run.updated_at.isoformat(),
                run.result,
                run.error,
                run.step_count,
                run.tool_call_count,
                self._dump(run.metadata),
                run.id,
            ),
        )
        if cursor.rowcount != 1:
            raise RunNotFound(f"Run {run.id} was not found.")

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        for attempt in range(self.lock_retry_attempts + 1):
            with self._lock:
                self._ensure_open()
                try:
                    self._connection.execute("BEGIN IMMEDIATE")
                    event = self._append_event_locked(run_id, event_type, payload)
                    self._connection.commit()
                    return event
                except sqlite3.OperationalError as error:
                    self._connection.rollback()
                    if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                        raise
                except BaseException:
                    self._connection.rollback()
                    raise
            if attempt >= self.lock_retry_attempts:
                break
            time.sleep(0.01 * (2**attempt) + random.uniform(0.0, 0.01))
        raise StoreBusyError(
            f"SQLite remained busy while appending event for run {run_id}."
        )

    def _append_event_locked(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM events WHERE run_id=?",
            (run_id,),
        ).fetchone()
        event = RuntimeEvent.create(run_id, int(row["max_sequence"]) + 1, event_type, payload)
        self._connection.execute(
            """
            INSERT INTO events (id, run_id, sequence, type, timestamp, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.run_id,
                event.sequence,
                event.type,
                event.timestamp.isoformat(),
                self._dump(event.payload),
            ),
        )
        return event

    def events_since(self, run_id: str, after_sequence: int = 0) -> list[RuntimeEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM events
                WHERE run_id=? AND sequence>?
                ORDER BY sequence
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [
            RuntimeEvent.from_dict(
                {**dict(row), "payload": self._load(row["payload_json"])}
            )
            for row in rows
        ]

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        with self._lock, self._connection:
            self._insert_checkpoint_locked(checkpoint)

    def save_checkpoint_with_event(
        self,
        checkpoint: Checkpoint,
        event_type: str = "checkpoint.created",
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._insert_checkpoint_locked(checkpoint)
            return self._append_event_locked(
                checkpoint.run_id,
                event_type,
                payload
                or {"checkpoint_id": checkpoint.id, "step": checkpoint.step},
            )

    def _insert_checkpoint_locked(self, checkpoint: Checkpoint) -> None:
        self._connection.execute(
            """
            INSERT INTO checkpoints (
                id, run_id, step, messages_json, tool_call_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint.id,
                checkpoint.run_id,
                checkpoint.step,
                self._dump([message.to_dict() for message in checkpoint.messages]),
                checkpoint.tool_call_count,
                checkpoint.created_at.isoformat(),
            ),
        )

    def latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM checkpoints
                WHERE run_id=?
                ORDER BY step DESC, created_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return Checkpoint.from_dict(
            {**dict(row), "messages": self._load(row["messages_json"])}
        )

    def save_run_checkpoint_with_event(
        self,
        run: AgentRun,
        checkpoint: Checkpoint,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._update_run_locked(run)
            self._insert_checkpoint_locked(checkpoint)
            self._append_event_locked(
                run.id,
                "checkpoint.created",
                {"checkpoint_id": checkpoint.id, "step": checkpoint.step},
            )
            return self._append_event_locked(run.id, event_type, payload)

    def create_step_with_event(
        self,
        run: AgentRun,
        step: Step,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._update_run_locked(run)
            self._insert_step_locked(step)
            return self._append_event_locked(run.id, event_type, payload)

    def _insert_step_locked(self, step: Step) -> None:
        self._connection.execute(
            """
            INSERT INTO steps (
                id, run_id, step_index, status, assistant_message_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step.id,
                step.run_id,
                step.step_index,
                step.status.value,
                self._dump(step.assistant_message.to_dict())
                if step.assistant_message
                else None,
                step.created_at.isoformat(),
                step.updated_at.isoformat(),
            ),
        )

    def save_model_tool_plan(
        self,
        step: Step,
        executions: list[ToolExecution],
        checkpoint: Checkpoint,
        model_payload: dict[str, Any],
        *,
        delta_payload: dict[str, Any] | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._update_step_locked(step)
            for execution in executions:
                self._insert_tool_execution_locked(execution)
            self._insert_checkpoint_locked(checkpoint)
            self._append_event_locked(step.run_id, "model.completed", model_payload)
            if delta_payload is not None:
                self._append_event_locked(step.run_id, "model.delta", delta_payload)
            self._append_event_locked(
                step.run_id,
                "checkpoint.created",
                {"checkpoint_id": checkpoint.id, "step": checkpoint.step},
            )
            if before_commit is not None:
                before_commit()

    def complete_run_from_model(
        self,
        run: AgentRun,
        step: Step,
        checkpoint: Checkpoint,
        model_payload: dict[str, Any],
        *,
        delta_payload: dict[str, Any] | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._update_step_locked(step)
            self._update_run_locked(run)
            self._insert_checkpoint_locked(checkpoint)
            self._append_event_locked(run.id, "model.completed", model_payload)
            if delta_payload is not None:
                self._append_event_locked(run.id, "model.delta", delta_payload)
            self._append_event_locked(
                run.id, "step.completed", {"step": step.step_index}
            )
            self._append_event_locked(
                run.id,
                "checkpoint.created",
                {"checkpoint_id": checkpoint.id, "step": checkpoint.step},
            )
            self._append_event_locked(run.id, "run.completed", {"result": run.result})
            if before_commit is not None:
                before_commit()

    def complete_step_with_checkpoint(
        self,
        step: Step,
        checkpoint: Checkpoint,
    ) -> None:
        with self._lock, self._connection:
            self._update_step_locked(step)
            self._insert_checkpoint_locked(checkpoint)
            self._append_event_locked(
                step.run_id, "step.completed", {"step": step.step_index}
            )
            self._append_event_locked(
                step.run_id,
                "checkpoint.created",
                {"checkpoint_id": checkpoint.id, "step": checkpoint.step},
            )

    def save_step(self, step: Step) -> None:
        with self._lock, self._connection:
            self._update_step_locked(step)

    def save_step_with_event(
        self,
        step: Step,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._update_step_locked(step)
            return self._append_event_locked(step.run_id, event_type, payload)

    def _update_step_locked(self, step: Step) -> None:
        step.updated_at = utc_now()
        cursor = self._connection.execute(
            """
            UPDATE steps SET status=?, assistant_message_json=?, updated_at=?
            WHERE id=?
            """,
            (
                step.status.value,
                self._dump(step.assistant_message.to_dict())
                if step.assistant_message
                else None,
                step.updated_at.isoformat(),
                step.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Step {step.id} was not found.")

    def get_step(self, step_id: str) -> Step:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM steps WHERE id=?", (step_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Step {step_id} was not found.")
        return self._step_from_row(row)

    def latest_incomplete_step(self, run_id: str) -> Step | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM steps
                WHERE run_id=? AND status IN ('running', 'waiting_for_tools')
                ORDER BY step_index DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._step_from_row(row) if row else None

    def steps_for_run(self, run_id: str) -> list[Step]:
        """Return all persisted model steps for a Run in execution order."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM steps WHERE run_id=? ORDER BY step_index",
                (run_id,),
            ).fetchall()
        return [self._step_from_row(row) for row in rows]

    def tool_executions_for_run(self, run_id: str) -> list[ToolExecution]:
        """Return all persisted tool executions for a Run in step/position order."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT tool_executions.*
                FROM tool_executions
                JOIN steps ON steps.id = tool_executions.step_id
                WHERE tool_executions.run_id=?
                ORDER BY steps.step_index, tool_executions.position
                """,
                (run_id,),
            ).fetchall()
        return [self._tool_execution_from_row(row) for row in rows]

    def create_tool_executions(
        self,
        step: Step,
        executions: list[ToolExecution],
    ) -> None:
        with self._lock, self._connection:
            self._update_step_locked(step)
            for execution in executions:
                self._insert_tool_execution_locked(execution)

    def _insert_tool_execution_locked(self, execution: ToolExecution) -> None:
        self._connection.execute(
            """
            INSERT INTO tool_executions (
                id, run_id, step_id, position, tool_call_id, tool_name,
                arguments_json, status, result_content, result_data_json, error,
                idempotency_key, requires_approval, side_effecting,
                created_at, started_at, completed_at, resolution,
                resolution_reason, resolved_by, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution.id,
                execution.run_id,
                execution.step_id,
                execution.position,
                execution.tool_call.id,
                execution.tool_call.name,
                self._dump(execution.tool_call.arguments),
                execution.status.value,
                execution.result_content,
                self._dump(execution.result_data)
                if execution.result_data is not None
                else None,
                execution.error,
                execution.idempotency_key,
                int(execution.requires_approval),
                int(execution.side_effecting),
                execution.created_at.isoformat(),
                execution.started_at.isoformat() if execution.started_at else None,
                execution.completed_at.isoformat() if execution.completed_at else None,
                execution.resolution.value if execution.resolution else None,
                execution.resolution_reason,
                execution.resolved_by,
                execution.resolved_at.isoformat() if execution.resolved_at else None,
            ),
        )

    def get_tool_execution(self, execution_id: str) -> ToolExecution:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM tool_executions WHERE id=?", (execution_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Tool execution {execution_id} was not found.")
        return self._tool_execution_from_row(row)

    def get_tool_execution_by_call(
        self, run_id: str, tool_call_id: str
    ) -> ToolExecution | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM tool_executions
                WHERE run_id=? AND tool_call_id=?
                """,
                (run_id, tool_call_id),
            ).fetchone()
        return self._tool_execution_from_row(row) if row else None

    def tool_executions_for_step(self, step_id: str) -> list[ToolExecution]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM tool_executions
                WHERE step_id=?
                ORDER BY position
                """,
                (step_id,),
            ).fetchall()
        return [self._tool_execution_from_row(row) for row in rows]

    def save_tool_execution(self, execution: ToolExecution) -> None:
        with self._lock, self._connection:
            self._update_tool_execution_locked(execution)

    def save_tool_execution_with_event(
        self,
        execution: ToolExecution,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        run: AgentRun | None = None,
        checkpoint: Checkpoint | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._update_tool_execution_locked(execution)
            if run is not None:
                self._update_run_locked(run)
            if checkpoint is not None:
                self._insert_checkpoint_locked(checkpoint)
            event = self._append_event_locked(execution.run_id, event_type, payload)
            if checkpoint is not None:
                self._append_event_locked(
                    execution.run_id,
                    "checkpoint.created",
                    {"checkpoint_id": checkpoint.id, "step": checkpoint.step},
                )
            if before_commit is not None:
                before_commit()
            return event

    def _update_tool_execution_locked(self, execution: ToolExecution) -> None:
        cursor = self._connection.execute(
            """
            UPDATE tool_executions SET
                status=?, result_content=?, result_data_json=?, error=?,
                started_at=?, completed_at=?, resolution=?, resolution_reason=?,
                resolved_by=?, resolved_at=?
            WHERE id=?
            """,
            (
                execution.status.value,
                execution.result_content,
                self._dump(execution.result_data)
                if execution.result_data is not None
                else None,
                execution.error,
                execution.started_at.isoformat() if execution.started_at else None,
                execution.completed_at.isoformat() if execution.completed_at else None,
                execution.resolution.value if execution.resolution else None,
                execution.resolution_reason,
                execution.resolved_by,
                execution.resolved_at.isoformat() if execution.resolved_at else None,
                execution.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Tool execution {execution.id} was not found.")

    def mark_running_tool_executions_unknown(self, run_id: str) -> list[ToolExecution]:
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT * FROM tool_executions
                WHERE run_id=? AND status='running'
                ORDER BY position
                """,
                (run_id,),
            ).fetchall()
            executions = [self._tool_execution_from_row(row) for row in rows]
            for execution in executions:
                if execution.side_effecting:
                    execution.status = ToolExecutionStatus.UNKNOWN
                    execution.error = (
                        "Runtime restarted while a side-effecting tool was running; "
                        "the external outcome must be reviewed."
                    )
                else:
                    execution.status = ToolExecutionStatus.PENDING
                    execution.started_at = None
                self._update_tool_execution_locked(execution)
            return executions

    def resolve_unknown_execution(
        self,
        execution: ToolExecution,
        run: AgentRun,
        resolution: UnknownToolResolution,
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._update_tool_execution_locked(execution)
            self._update_run_locked(run)
            return self._append_event_locked(
                execution.run_id,
                "tool.outcome_confirmed",
                {
                    "tool_execution_id": execution.id,
                    "tool_call_id": execution.tool_call.id,
                    "previous_status": "unknown",
                    "resolution": resolution.value,
                    "reason": execution.resolution_reason,
                    "resolved_by": execution.resolved_by,
                    "resolved_at": execution.resolved_at.isoformat()
                    if execution.resolved_at
                    else None,
                    "result_content": execution.result_content,
                    "result_data": execution.result_data,
                    "error": execution.error,
                },
            )

    def create_approval(self, approval: Approval) -> None:
        with self._lock, self._connection:
            self._insert_approval_locked(approval)

    def create_approval_with_state(
        self,
        approval: Approval,
        run: AgentRun,
        execution: ToolExecution,
        event_type: str,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        with self._lock, self._connection:
            self._insert_approval_locked(approval)
            self._update_tool_execution_locked(execution)
            self._update_run_locked(run)
            return self._append_event_locked(run.id, event_type, payload)

    def _insert_approval_locked(self, approval: Approval) -> None:
        self._connection.execute(
            """
            INSERT INTO approvals (
                id, run_id, tool_call_json, status, reason, created_at,
                resolved_at, tool_execution_id, kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval.id,
                approval.run_id,
                self._dump(
                    {
                        "id": approval.tool_call.id,
                        "name": approval.tool_call.name,
                        "arguments": approval.tool_call.arguments,
                    }
                ),
                approval.status,
                approval.reason,
                approval.created_at.isoformat(),
                approval.resolved_at.isoformat() if approval.resolved_at else None,
                approval.tool_execution_id,
                approval.kind,
            ),
        )

    def get_approval(self, approval_id: str) -> Approval:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Approval {approval_id} was not found.")
        return self._approval_from_row(row)

    def pending_approval(self, run_id: str) -> Approval | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM approvals
                WHERE run_id=? AND status='pending'
                ORDER BY created_at
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._approval_from_row(row) if row else None

    def approval_for_execution(self, execution_id: str) -> Approval | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM approvals
                WHERE tool_execution_id=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (execution_id,),
            ).fetchone()
        return self._approval_from_row(row) if row else None

    def resolve_approval(
        self, approval_id: str, approved: bool, reason: str | None = None
    ) -> Approval:
        approval = self.get_approval(approval_id)
        if approval.status != "pending":
            return approval
        approval.status = "approved" if approved else "rejected"
        approval.reason = reason
        approval.resolved_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE approvals
                SET status=?, reason=?, resolved_at=?
                WHERE id=?
                """,
                (
                    approval.status,
                    approval.reason,
                    approval.resolved_at.isoformat(),
                    approval.id,
                ),
            )
        return approval

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(value: str) -> Any:
        return json.loads(value)

    def _run_values(self, run: AgentRun) -> tuple[Any, ...]:
        return (
            run.id,
            run.agent_name,
            run.input,
            run.status.value,
            run.created_at.isoformat(),
            run.updated_at.isoformat(),
            run.result,
            run.error,
            run.step_count,
            run.tool_call_count,
            self._dump(run.metadata),
        )

    def _run_from_row(self, row: sqlite3.Row) -> AgentRun:
        return AgentRun.from_dict(
            {**dict(row), "metadata": self._load(row["metadata_json"])}
        )

    def _session_from_row(self, row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=self._load(row["metadata_json"]),
        )

    def _memory_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            scope=MemoryScope(row["scope"]),
            scope_id=row["scope_id"],
            content=row["content"],
            source_run_id=row["source_run_id"],
            source_trace_id=row["source_trace_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            deleted_at=datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None,
            metadata=self._load(row["metadata_json"]),
        )

    def _run_relation_from_row(self, row: sqlite3.Row) -> RunRelation:
        return RunRelation(
            id=row["id"],
            parent_run_id=row["parent_run_id"],
            child_run_id=row["child_run_id"],
            root_run_id=row["root_run_id"],
            relation_type=RunRelationType(row["relation_type"]),
            delegation_key=row["delegation_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=self._load(row["metadata_json"]),
        )

    def _step_from_row(self, row: sqlite3.Row) -> Step:
        assistant = (
            Message.from_dict(self._load(row["assistant_message_json"]))
            if row["assistant_message_json"]
            else None
        )
        return Step(
            id=row["id"],
            run_id=row["run_id"],
            step_index=row["step_index"],
            status=StepStatus(row["status"]),
            assistant_message=assistant,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _tool_execution_from_row(self, row: sqlite3.Row) -> ToolExecution:
        return ToolExecution(
            id=row["id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            position=row["position"],
            tool_call=ToolCall(
                id=row["tool_call_id"],
                name=row["tool_name"],
                arguments=self._load(row["arguments_json"]),
            ),
            status=ToolExecutionStatus(row["status"]),
            result_content=row["result_content"],
            result_data=self._load(row["result_data_json"])
            if row["result_data_json"]
            else None,
            error=row["error"],
            idempotency_key=row["idempotency_key"],
            requires_approval=bool(row["requires_approval"]),
            side_effecting=bool(row["side_effecting"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"])
            if row["started_at"]
            else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
            resolution=UnknownToolResolution(row["resolution"])
            if row["resolution"]
            else None,
            resolution_reason=row["resolution_reason"],
            resolved_by=row["resolved_by"],
            resolved_at=datetime.fromisoformat(row["resolved_at"])
            if row["resolved_at"]
            else None,
        )

    def _approval_from_row(self, row: sqlite3.Row) -> Approval:
        payload = self._load(row["tool_call_json"])
        keys = set(row.keys())
        return Approval(
            id=row["id"],
            run_id=row["run_id"],
            tool_call=ToolCall(**payload),
            status=row["status"],
            reason=row["reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"])
            if row["resolved_at"]
            else None,
            tool_execution_id=row["tool_execution_id"]
            if "tool_execution_id" in keys
            else None,
            kind=row["kind"] if "kind" in keys else "tool",
        )


def _migration_checksum(version: int, name: str, sql: str) -> str:
    canonical = f"{version}:{name}:{sql.strip()}".encode()
    return hashlib.sha256(canonical).hexdigest()


class ArtifactStore:
    """Workspace-confined store for output too large for SQLite event payloads."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_text(self, run_id: str, name: str, content: str) -> Path:
        target = (self.root / run_id / name).resolve()
        if self.root not in target.parents:
            raise ValueError("Artifact path escapes configured root.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

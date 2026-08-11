from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from .domain import (
    AgentRun,
    Approval,
    Checkpoint,
    RunNotFound,
    RunStatus,
    RuntimeEvent,
    ToolCall,
    utc_now,
)


class SQLiteStore:
    """Small durable store. Each write is committed before returning to the runtime."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _init_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
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
                CREATE INDEX IF NOT EXISTS idx_events_run_sequence ON events(run_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_run_step ON checkpoints(run_id, step DESC);
                CREATE INDEX IF NOT EXISTS idx_approvals_run_status ON approvals(run_id, status);
                """
            )

    def create_run(self, run: AgentRun) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
                ),
            )

    def get_run(self, run_id: str) -> AgentRun:
        with self._lock:
            row = self._connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(f"Run {run_id} was not found.")
        return AgentRun.from_dict(
            {
                **dict(row),
                "metadata": self._load(row["metadata_json"]),
            }
        )

    def list_runs(self, limit: int = 50) -> list[AgentRun]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            AgentRun.from_dict({**dict(row), "metadata": self._load(row["metadata_json"])})
            for row in rows
        ]

    def save_run(self, run: AgentRun) -> None:
        with self._lock, self._connection:
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

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM events WHERE run_id=?", (run_id,)
            ).fetchone()
            event = RuntimeEvent.create(run_id, int(row["max_sequence"]) + 1, event_type, payload)
            self._connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
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
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence", (run_id, after_sequence)
            ).fetchall()
        return [
            RuntimeEvent.from_dict({**dict(row), "payload": self._load(row["payload_json"])})
            for row in rows
        ]

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?)",
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
                "SELECT * FROM checkpoints WHERE run_id=? ORDER BY step DESC, created_at DESC LIMIT 1", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return Checkpoint.from_dict(
            {
                **dict(row),
                "messages": self._load(row["messages_json"]),
            }
        )

    def create_approval(self, approval: Approval) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )

    def get_approval(self, approval_id: str) -> Approval:
        with self._lock:
            row = self._connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"Approval {approval_id} was not found.")
        return self._approval_from_row(row)

    def pending_approval(self, run_id: str) -> Approval | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approvals WHERE run_id=? AND status='pending' ORDER BY created_at LIMIT 1", (run_id,)
            ).fetchone()
        return self._approval_from_row(row) if row else None

    def resolve_approval(self, approval_id: str, approved: bool, reason: str | None = None) -> Approval:
        approval = self.get_approval(approval_id)
        if approval.status != "pending":
            return approval
        approval.status = "approved" if approved else "rejected"
        approval.reason = reason
        approval.resolved_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE approvals SET status=?, reason=?, resolved_at=? WHERE id=?",
                (approval.status, approval.reason, approval.resolved_at.isoformat(), approval.id),
            )
        return approval

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(value: str) -> Any:
        return json.loads(value)

    def _approval_from_row(self, row: sqlite3.Row) -> Approval:
        payload = self._load(row["tool_call_json"])
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
        )


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


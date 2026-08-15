from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .domain import RunNotFound, utc_now
from .storage import MIGRATIONS, SQLiteStore

DoctorLevel = Literal["ok", "attention", "unhealthy"]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    level: DoctorLevel
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DoctorReport:
    status: Literal["ok", "attention_required", "unhealthy"]
    generated_at: str
    database_path: str
    run_id: str | None
    checks: tuple[DoctorCheck, ...]

    @property
    def exit_code(self) -> int:
        return {"ok": 0, "attention_required": 1, "unhealthy": 2}[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "generated_at": self.generated_at,
            "database_path": self.database_path,
            "run_id": self.run_id,
            "checks": [check.to_dict() for check in self.checks],
        }


class RuntimeDoctor:
    """Read-only operational diagnostics for one SQLite-backed Runtime."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def run(self, run_id: str | None = None) -> DoctorReport:
        checks: list[DoctorCheck] = []
        try:
            health = self.store.health_check()
            checks.append(DoctorCheck("sqlite.quick_check", "ok", "SQLite quick_check passed.", health))
            expected_schema = MIGRATIONS[-1][0]
            actual_schema = int(health["schema_version"])
            checks.append(
                DoctorCheck(
                    "schema.version",
                    "ok" if actual_schema == expected_schema else "unhealthy",
                    f"Schema version is {actual_schema}; expected {expected_schema}.",
                    {"actual": actual_schema, "expected": expected_schema},
                )
            )
            snapshot = self.store.diagnostic_snapshot(run_id)
        except RunNotFound as error:
            checks.append(DoctorCheck("run.exists", "unhealthy", str(error)))
            return self._report(checks, run_id)
        except Exception as error:
            checks.append(
                DoctorCheck(
                    "sqlite.access",
                    "unhealthy",
                    f"SQLite diagnostics failed: {error}",
                    {"error_type": type(error).__name__},
                )
            )
            return self._report(checks, run_id)

        checks.extend(
            [
                DoctorCheck(
                    "sqlite.journal_mode",
                    "ok" if health["journal_mode"] == "wal" else "unhealthy",
                    "SQLite journal mode is WAL."
                    if health["journal_mode"] == "wal"
                    else f"SQLite journal mode is {health['journal_mode']}; expected WAL.",
                ),
                DoctorCheck(
                    "sqlite.synchronous",
                    "ok" if snapshot["synchronous"] == 2 else "unhealthy",
                    "SQLite synchronous mode is FULL."
                    if snapshot["synchronous"] == 2
                    else f"SQLite synchronous value is {snapshot['synchronous']}; expected FULL (2).",
                ),
                DoctorCheck(
                    "migrations.integrity",
                    "ok",
                    "Applied migration checksums were verified when the store opened.",
                ),
                DoctorCheck(
                    "sqlite.foreign_keys",
                    "ok" if snapshot["foreign_keys"] == 1 else "unhealthy",
                    "Foreign-key enforcement is enabled."
                    if snapshot["foreign_keys"] == 1
                    else "Foreign-key enforcement is disabled.",
                ),
                self._list_check(
                    "tools.unknown",
                    snapshot["unknown_tool_execution_ids"],
                    "UNKNOWN tool outcomes require human confirmation.",
                    "No UNKNOWN tool outcomes were found.",
                    level="attention",
                ),
                self._list_check(
                    "tools.running",
                    snapshot["running_tool_execution_ids"],
                    "Persisted running ToolExecutions require recovery review.",
                    "No stale running ToolExecutions were found.",
                    level="attention",
                ),
                self._list_check(
                    "approvals.pending",
                    snapshot["pending_approval_ids"],
                    "Pending approvals require human action.",
                    "No pending approvals were found.",
                    level="attention",
                ),
                self._list_check(
                    "events.duplicates",
                    snapshot["duplicate_event_sequences"],
                    "Duplicate event sequences were found.",
                    "Event sequences are unique.",
                    level="unhealthy",
                ),
                self._list_check(
                    "events.gaps",
                    snapshot["event_sequence_gap_run_ids"],
                    "Event sequence gaps were found.",
                    "Event sequences are contiguous.",
                    level="unhealthy",
                ),
                self._list_check(
                    "workflows.snapshots",
                    snapshot["workflow_run_ids_without_snapshot"],
                    "Workflow runs without recovery snapshots were found.",
                    "All workflow runs have recovery snapshots.",
                    level="unhealthy",
                ),
                self._list_check(
                    "agents.snapshots",
                    snapshot["run_ids_missing_agent_snapshot"],
                    "Active Runs without AgentDefinition snapshots require application registration.",
                    "All active Agent Runs have durable AgentDefinition snapshots.",
                    level="attention",
                ),
                self._list_check(
                    "storage.orphans",
                    snapshot["orphan_step_ids"]
                    + snapshot["orphan_tool_execution_ids"]
                    + snapshot["orphan_workflow_snapshot_run_ids"],
                    "Orphan durable records were found.",
                    "No orphan Steps, ToolExecutions, or Workflow snapshots were found.",
                    level="unhealthy",
                ),
            ]
        )
        active = {
            key: value
            for key, value in snapshot["run_status_counts"].items()
            if key in {"created", "running", "paused", "waiting_for_approval"}
        }
        checks.append(
            DoctorCheck(
                "runs.lifecycle",
                "attention" if active else "ok",
                "Non-terminal Runs require operational attention."
                if active
                else "All inspected Runs are terminal.",
                {"status_counts": snapshot["run_status_counts"], "active": active},
            )
        )
        return self._report(checks, run_id)

    def _report(self, checks: list[DoctorCheck], run_id: str | None) -> DoctorReport:
        levels = {check.level for check in checks}
        status: Literal["ok", "attention_required", "unhealthy"]
        if "unhealthy" in levels:
            status = "unhealthy"
        elif "attention" in levels:
            status = "attention_required"
        else:
            status = "ok"
        return DoctorReport(
            status=status,
            generated_at=utc_now().isoformat(),
            database_path=str(self.store.path),
            run_id=run_id,
            checks=tuple(checks),
        )

    @staticmethod
    def _list_check(
        name: str,
        values: list[Any],
        problem: str,
        healthy: str,
        *,
        level: Literal["attention", "unhealthy"],
    ) -> DoctorCheck:
        return DoctorCheck(
            name,
            level if values else "ok",
            problem if values else healthy,
            {"items": values, "count": len(values)},
        )

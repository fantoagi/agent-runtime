from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from .domain import AgentRun, RuntimeEvent, utc_now
from .observability import ObservabilityService, OperationalFailure
from .telemetry import sanitize_log_value
from .version import __version__

if TYPE_CHECKING:
    from .runtime import Runtime

_BUNDLE_FORMAT_VERSION = 1
_SAFE_METADATA_KEYS = {
    "trace_id",
    "root_trace_id",
    "parent_run_id",
    "root_run_id",
    "run_kind",
    "workflow_type",
    "workflow_step",
    "workflow_branch",
    "delegation_key",
}
_SAFE_EVENT_KEYS = {
    "step",
    "attempt",
    "max_attempts",
    "error_type",
    "retryable",
    "status_code",
    "delay_seconds",
    "next_attempt",
    "tool_name",
    "tool_execution_id",
    "tool_call_id",
    "approval_id",
    "resolution",
    "status",
    "finish_reason",
    "usage",
    "artifact_id",
    "characters",
    "preview_characters",
}


@dataclass(frozen=True, slots=True)
class FailureDiagnosis:
    run_id: str
    run_status: str
    category: str
    severity: Literal["info", "attention", "critical"]
    summary: str
    recommended_action: str
    retryable: bool | None
    recovered: bool
    evidence: tuple[str, ...]
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_status": self.run_status,
            "category": self.category,
            "severity": self.severity,
            "summary": self.summary,
            "recommended_action": self.recommended_action,
            "retryable": self.retryable,
            "recovered": self.recovered,
            "evidence": list(self.evidence),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class IncidentReport:
    generated_at: datetime
    version: str
    scope_run_id: str | None
    diagnostics: dict[str, Any]
    failure_analysis: tuple[FailureDiagnosis, ...]
    runs: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    collection: dict[str, Any]
    privacy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "version": self.version,
            "scope_run_id": self.scope_run_id,
            "diagnostics": self.diagnostics,
            "failure_analysis": [item.to_dict() for item in self.failure_analysis],
            "runs": list(self.runs),
            "events": list(self.events),
            "collection": self.collection,
            "privacy": self.privacy,
        }


@dataclass(frozen=True, slots=True)
class IncidentBundle:
    path: Path
    size_bytes: int
    sha256: str
    format_version: int
    run_count: int
    event_count: int
    failure_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "format_version": self.format_version,
            "run_count": self.run_count,
            "event_count": self.event_count,
            "failure_count": self.failure_count,
        }


class IncidentDiagnosticsService:
    """Create bounded, secret-aware incident reports from durable Runtime facts."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.store = runtime.store
        self.observability = ObservabilityService(self.store)

    def failure_analysis(
        self,
        *,
        run_id: str | None = None,
        limit: int = 20,
        run_limit: int = 1000,
    ) -> tuple[FailureDiagnosis, ...]:
        runs = self._selected_runs(run_id=run_id, limit=run_limit)
        return self._failure_analysis_for_runs(runs, limit=limit)

    def report(
        self,
        *,
        run_id: str | None = None,
        run_limit: int = 100,
        recent_failure_limit: int = 20,
        event_limit: int = 5000,
    ) -> IncidentReport:
        runs = self._selected_runs(run_id=run_id, limit=run_limit)
        events, events_truncated, observed_event_count = self._collect_events(
            runs, limit=event_limit
        )
        diagnostics = self._support_safe_diagnostics(
            self.observability.diagnostics(
                self.runtime,
                metrics_limit=max(1, run_limit),
                recent_failure_limit=recent_failure_limit,
            ).to_dict()
        )
        return IncidentReport(
            generated_at=utc_now(),
            version=__version__,
            scope_run_id=run_id,
            diagnostics=diagnostics,
            failure_analysis=self._failure_analysis_for_runs(
                runs, limit=recent_failure_limit
            ),
            runs=tuple(self._run_summary(run) for run in runs),
            events=tuple(self._event_summary(event) for event in events),
            collection={
                "run_limit": max(1, run_limit),
                "event_limit": max(0, event_limit),
                "observed_event_count": observed_event_count,
                "included_event_count": len(events),
                "events_truncated": events_truncated,
            },
            privacy={
                "profile": "support-safe-v1",
                "excluded": [
                    "run input and result",
                    "model prompt and token deltas",
                    "tool arguments and results",
                    "memory content",
                    "checkpoint messages",
                    "artifacts and SQLite database",
                ],
                "secret_redaction": True,
                "bounded_strings": True,
            },
        )

    def bundle_bytes(
        self,
        *,
        run_id: str | None = None,
        run_limit: int = 100,
        recent_failure_limit: int = 20,
        event_limit: int = 5000,
    ) -> tuple[bytes, IncidentReport]:
        report = self.report(
            run_id=run_id,
            run_limit=run_limit,
            recent_failure_limit=recent_failure_limit,
            event_limit=event_limit,
        )
        documents = {
            "diagnostics.json": report.diagnostics,
            "failure-analysis.json": [item.to_dict() for item in report.failure_analysis],
            "runs.json": list(report.runs),
            "events.json": list(report.events),
            "collection.json": report.collection,
            "privacy.json": report.privacy,
        }
        encoded = {
            name: _json_bytes(value)
            for name, value in documents.items()
        }
        manifest = {
            "format": "agent-runtime-incident-bundle",
            "format_version": _BUNDLE_FORMAT_VERSION,
            "runtime_version": report.version,
            "generated_at": report.generated_at.isoformat(),
            "scope_run_id": report.scope_run_id,
            "run_count": len(report.runs),
            "event_count": len(report.events),
            "events_truncated": report.collection["events_truncated"],
            "failure_count": len(report.failure_analysis),
            "entries": {
                name: {
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for name, content in sorted(encoded.items())
            },
        }
        encoded["manifest.json"] = _json_bytes(manifest)
        encoded["README.txt"] = (
            b"Agent Runtime incident bundle\n"
            b"This archive contains bounded diagnostic summaries only.\n"
            b"It intentionally excludes prompts, tool arguments/results, memory, artifacts, "
            b"checkpoint messages, and the SQLite database.\n"
        )

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(encoded.items()):
                archive.writestr(name, content)
        return output.getvalue(), report

    def create_bundle(
        self,
        output: str | Path,
        *,
        run_id: str | None = None,
        run_limit: int = 100,
        recent_failure_limit: int = 20,
        event_limit: int = 5000,
        overwrite: bool = False,
    ) -> IncidentBundle:
        target = Path(output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"Incident bundle already exists: {target}")
        content, report = self.bundle_bytes(
            run_id=run_id,
            run_limit=run_limit,
            recent_failure_limit=recent_failure_limit,
            event_limit=event_limit,
        )
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return IncidentBundle(
            path=target,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            format_version=_BUNDLE_FORMAT_VERSION,
            run_count=len(report.runs),
            event_count=len(report.events),
            failure_count=len(report.failure_analysis),
        )

    def suggested_filename(self) -> str:
        timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        return f"agent-runtime-incident-{timestamp}.zip"

    def _collect_events(
        self, runs: list[AgentRun], *, limit: int
    ) -> tuple[list[RuntimeEvent], bool, int]:
        events = [
            event
            for run in runs
            for event in self.store.events_since(run.id)
        ]
        events.sort(key=lambda item: (item.timestamp, item.run_id, item.sequence))
        observed = len(events)
        bounded_limit = max(0, limit)
        truncated = observed > bounded_limit
        if truncated:
            events = events[-bounded_limit:] if bounded_limit else []
        return events, truncated, observed

    def _failure_analysis_for_runs(
        self, runs: list[AgentRun], *, limit: int
    ) -> tuple[FailureDiagnosis, ...]:
        failures: list[tuple[OperationalFailure, AgentRun]] = []
        for run in runs:
            for event in self.store.events_since(run.id):
                if event.type not in {
                    "run.failed",
                    "workflow.failed",
                    "model.attempt.failed",
                    "tool.failed",
                    "tool.outcome_unknown",
                }:
                    continue
                failures.append(
                    (
                        OperationalFailure(
                            run_id=run.id,
                            event_type=event.type,
                            timestamp=event.timestamp,
                            error=str(event.payload.get("error") or run.error or event.type),
                            error_type=(
                                str(event.payload["error_type"])
                                if event.payload.get("error_type") is not None
                                else None
                            ),
                            retryable=(
                                bool(event.payload["retryable"])
                                if "retryable" in event.payload
                                else None
                            ),
                            attributes={
                                key: value
                                for key, value in event.payload.items()
                                if key != "error"
                            },
                        ),
                        run,
                    )
                )
        failures.sort(key=lambda item: item[0].timestamp, reverse=True)
        diagnoses: list[FailureDiagnosis] = []
        seen: set[tuple[str, str]] = set()
        for failure, run in failures:
            diagnosis = self._diagnose(failure, run)
            key = (diagnosis.run_id, diagnosis.category)
            if key in seen:
                continue
            seen.add(key)
            diagnoses.append(diagnosis)
            if len(diagnoses) >= max(0, limit):
                break
        return tuple(diagnoses)

    def _selected_runs(self, *, run_id: str | None, limit: int) -> list[AgentRun]:
        if run_id is None:
            return self.store.list_runs(limit=max(1, limit))
        root_id = self.store.root_run_id(run_id)
        related_ids = {root_id}
        related_ids.update(
            relation.child_run_id for relation in self.store.relations_for_root(root_id)
        )
        return [self.store.get_run(item) for item in sorted(related_ids)]

    @staticmethod
    def _support_safe_diagnostics(value: dict[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_log_value(value)
        assert isinstance(sanitized, dict)
        runtime = sanitized.get("runtime")
        if isinstance(runtime, dict):
            for key in ("database_path", "artifact_path"):
                if key in runtime:
                    runtime[key] = "[LOCAL_PATH_REDACTED]"
        store = sanitized.get("store")
        if isinstance(store, dict) and "database_path" in store:
            store["database_path"] = "[LOCAL_PATH_REDACTED]"
        doctor = sanitized.get("doctor")
        if isinstance(doctor, dict) and "database_path" in doctor:
            doctor["database_path"] = "[LOCAL_PATH_REDACTED]"
        failures = sanitized.get("recent_failures")
        if isinstance(failures, list):
            for failure in failures:
                if isinstance(failure, dict) and "error" in failure:
                    failure["error"] = "[REDACTED_ERROR_TEXT]"
        return sanitized

    @staticmethod
    def _run_summary(run: AgentRun) -> dict[str, Any]:
        metadata = sanitize_log_value(
            {
                key: value
                for key, value in run.metadata.items()
                if key in _SAFE_METADATA_KEYS
            }
        )
        assert isinstance(metadata, dict)
        return {
            "id": run.id,
            "agent_name": run.agent_name,
            "status": run.status.value,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "step_count": run.step_count,
            "tool_call_count": run.tool_call_count,
            "has_error": run.error is not None,
            "metadata": metadata,
        }

    @staticmethod
    def _event_summary(event: RuntimeEvent) -> dict[str, Any]:
        payload = sanitize_log_value(
            {
                key: value
                for key, value in event.payload.items()
                if key in _SAFE_EVENT_KEYS
            }
        )
        assert isinstance(payload, dict)
        return {
            "id": event.id,
            "run_id": event.run_id,
            "sequence": event.sequence,
            "type": event.type,
            "timestamp": event.timestamp.isoformat(),
            "payload": payload,
        }

    @staticmethod
    def _diagnose(failure: OperationalFailure, run: AgentRun) -> FailureDiagnosis:
        status_code = _integer(failure.attributes.get("status_code"))
        error_type = (failure.error_type or "").lower()
        error_text = failure.error.lower()
        recovered = run.status.value == "completed"
        severity: Literal["info", "attention", "critical"] = (
            "info" if recovered else "critical" if run.status.value == "failed" else "attention"
        )
        evidence = [failure.event_type]
        if status_code is not None:
            evidence.append(f"HTTP {status_code}")
        if failure.error_type:
            evidence.append(failure.error_type)

        if failure.event_type == "tool.outcome_unknown":
            category = "tool.unknown_outcome"
            summary = "A side-effecting tool outcome could not be confirmed."
            action = (
                "Verify the external side effect, then resolve the UNKNOWN ToolExecution "
                "before resuming the Run."
            )
        elif failure.event_type == "tool.failed":
            category = "tool.execution"
            summary = "A tool handler failed before producing a successful durable result."
            action = "Inspect the tool implementation and arguments; retry only when side effects are safe."
        elif status_code in {401, 403}:
            category = "provider.authentication"
            summary = "The model provider rejected authentication or authorization."
            action = "Correct provider credentials or permissions; automatic retry is not appropriate."
        elif status_code == 429:
            category = "provider.rate_limit"
            summary = "The model provider rate limit was reached."
            action = "Honor Retry-After, reduce concurrency, or request a higher provider quota."
        elif status_code is not None and status_code >= 500:
            category = "provider.server"
            summary = "The model provider returned a transient server error."
            action = "Review retry history and provider status; retry with bounded backoff when safe."
        elif "timeout" in error_type or "timeout" in error_text:
            category = "provider.timeout"
            summary = "The model request exceeded its configured time limit."
            action = "Check provider latency and timeout settings; retry only if the request is idempotent."
        elif failure.event_type == "model.attempt.failed":
            category = "provider.transport"
            summary = "A model provider attempt failed during transport or protocol handling."
            action = "Inspect retry events and provider connectivity; do not retry deterministic 4xx errors."
        else:
            category = "runtime.execution"
            summary = "The Run or Workflow ended with an execution failure."
            action = "Inspect the durable trace and the preceding provider or tool failure events."

        if recovered:
            summary = f"Recovered: {summary}"
            action = f"No immediate recovery is required. {action}"
        return FailureDiagnosis(
            run_id=run.id,
            run_status=run.status.value,
            category=category,
            severity=severity,
            summary=summary,
            recommended_action=action,
            retryable=failure.retryable,
            recovered=recovered,
            evidence=tuple(evidence),
            timestamp=failure.timestamp,
        )


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
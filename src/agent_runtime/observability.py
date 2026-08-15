from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .domain import AgentRun, RunRelation, RuntimeEvent, utc_now
from .storage import SQLiteStore


@dataclass(slots=True)
class TraceSpan:
    id: str
    name: str
    kind: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    start_sequence: int
    end_sequence: int | None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return max(0.0, (self.ended_at - self.started_at).total_seconds() * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "start_sequence": self.start_sequence,
            "end_sequence": self.end_sequence,
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class RunTrace:
    trace_id: str
    run_id: str
    agent_name: str
    status: str
    spans: list[TraceSpan]
    events: list[RuntimeEvent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "agent_name": self.agent_name,
            "status": self.status,
            "spans": [span.to_dict() for span in self.spans],
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(slots=True)
class TraceTreeNode:
    run: AgentRun
    trace: RunTrace
    relation: RunRelation | None = None
    children: list[TraceTreeNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "trace": self.trace.to_dict(),
            "relation": self.relation.to_dict() if self.relation else None,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(slots=True)
class TraceTree:
    root_run_id: str
    root_trace_id: str
    node_count: int
    root: TraceTreeNode

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_run_id": self.root_run_id,
            "root_trace_id": self.root_trace_id,
            "node_count": self.node_count,
            "root": self.root.to_dict(),
        }


@dataclass(slots=True)
class MetricsSnapshot:
    generated_at: datetime
    total_runs: int
    root_runs: int
    child_runs: int
    workflow_runs: int
    delegations: int
    sessions: int
    memories_total: int
    memories_active: int
    memories_deleted: int
    memories_expired: int
    memory_searches: int
    context_compactions: int
    runs_by_status: dict[str, int]
    total_events: int
    events_by_type: dict[str, int]
    model_requests: int
    tool_requests: int
    approval_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    run_duration_ms_average: float
    run_duration_ms_p95: float
    model_duration_ms_average: float
    tool_duration_ms_average: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "total_runs": self.total_runs,
            "multi_agent": {
                "root_runs": self.root_runs,
                "child_runs": self.child_runs,
                "workflow_runs": self.workflow_runs,
                "delegations": self.delegations,
            },
            "context_memory": {
                "sessions": self.sessions,
                "memories_total": self.memories_total,
                "memories_active": self.memories_active,
                "memories_deleted": self.memories_deleted,
                "memories_expired": self.memories_expired,
                "memory_searches": self.memory_searches,
                "context_compactions": self.context_compactions,
            },
            "runs_by_status": self.runs_by_status,
            "total_events": self.total_events,
            "events_by_type": self.events_by_type,
            "model_requests": self.model_requests,
            "tool_requests": self.tool_requests,
            "approval_requests": self.approval_requests,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
            },
            "duration_ms": {
                "run_average": self.run_duration_ms_average,
                "run_p95": self.run_duration_ms_p95,
                "model_average": self.model_duration_ms_average,
                "tool_average": self.tool_duration_ms_average,
            },
        }

    def to_prometheus(self) -> str:
        lines = [
            "# HELP agent_runtime_runs_total Total runs observed by status.",
            "# TYPE agent_runtime_runs_total gauge",
        ]
        for status, value in sorted(self.runs_by_status.items()):
            lines.append(f'agent_runtime_runs_total{{status="{status}"}} {value}')
        lines.extend(
            [
                f"agent_runtime_root_runs_total {self.root_runs}",
                f"agent_runtime_child_runs_total {self.child_runs}",
                f"agent_runtime_workflow_runs_total {self.workflow_runs}",
                f"agent_runtime_delegations_total {self.delegations}",
                f"agent_runtime_sessions_total {self.sessions}",
                f"agent_runtime_memories_total {self.memories_total}",
                f"agent_runtime_memories_active {self.memories_active}",
                f"agent_runtime_memories_deleted_total {self.memories_deleted}",
                f"agent_runtime_memories_expired_total {self.memories_expired}",
                f"agent_runtime_memory_searches_total {self.memory_searches}",
                f"agent_runtime_context_compactions_total {self.context_compactions}",
                "# HELP agent_runtime_events_total Total persisted runtime events.",
                "# TYPE agent_runtime_events_total gauge",
            ]
        )
        for event_type, value in sorted(self.events_by_type.items()):
            lines.append(f'agent_runtime_events_total{{type="{event_type}"}} {value}')
        lines.extend(
            [
                f"agent_runtime_model_requests_total {self.model_requests}",
                f"agent_runtime_tool_requests_total {self.tool_requests}",
                f"agent_runtime_approval_requests_total {self.approval_requests}",
                f"agent_runtime_prompt_tokens_total {self.prompt_tokens}",
                f"agent_runtime_completion_tokens_total {self.completion_tokens}",
                f"agent_runtime_tokens_total {self.total_tokens}",
                f"agent_runtime_run_duration_ms_average {self.run_duration_ms_average}",
                f"agent_runtime_run_duration_ms_p95 {self.run_duration_ms_p95}",
                f"agent_runtime_model_duration_ms_average {self.model_duration_ms_average}",
                f"agent_runtime_tool_duration_ms_average {self.tool_duration_ms_average}",
            ]
        )
        return "\n".join(lines) + "\n"


class ObservabilityService:
    """Derive traces and metrics from the durable Run and RuntimeEvent records."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def trace(self, run_id: str) -> RunTrace:
        run = self.store.get_run(run_id)
        events = self.store.events_since(run_id)
        spans = [self._run_span(run, events)]
        spans.extend(self._paired_spans(events, "model"))
        spans.extend(self._paired_spans(events, "tool"))
        spans.extend(self._paired_spans(events, "approval"))
        spans.sort(key=lambda item: (item.started_at, item.start_sequence, item.kind))
        return RunTrace(
            trace_id=str(run.metadata.get("trace_id") or run.id),
            run_id=run.id,
            agent_name=run.agent_name,
            status=run.status.value,
            spans=spans,
            events=events,
        )

    def trace_tree(self, run_id: str) -> TraceTree:
        root_run_id = self.store.root_run_id(run_id)
        relations = self.store.relations_for_root(root_run_id)
        by_parent: dict[str, list[RunRelation]] = {}
        for relation in relations:
            by_parent.setdefault(relation.parent_run_id, []).append(relation)

        def build(current_run_id: str, relation: RunRelation | None = None) -> TraceTreeNode:
            run = self.store.get_run(current_run_id)
            return TraceTreeNode(
                run=run,
                trace=self.trace(current_run_id),
                relation=relation,
                children=[
                    build(child_relation.child_run_id, child_relation)
                    for child_relation in by_parent.get(current_run_id, [])
                ],
            )

        root = self.store.get_run(root_run_id)
        return TraceTree(
            root_run_id=root_run_id,
            root_trace_id=str(
                root.metadata.get("root_trace_id")
                or root.metadata.get("trace_id")
                or root.id
            ),
            node_count=1 + len(relations),
            root=build(root_run_id),
        )

    def metrics(self, limit: int = 1000) -> MetricsSnapshot:
        runs = self.store.list_runs(limit=limit)
        statuses = Counter(run.status.value for run in runs)
        child_runs = sum(bool(run.metadata.get("parent_run_id")) for run in runs)
        workflow_runs = sum(run.metadata.get("run_kind") == "workflow" for run in runs)
        root_runs = len(runs) - child_runs
        event_counts: Counter[str] = Counter()
        run_durations: list[float] = []
        model_durations: list[float] = []
        tool_durations: list[float] = []
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        for run in runs:
            run_durations.append(max(0.0, (run.updated_at - run.created_at).total_seconds() * 1000))
            trace = self.trace(run.id)
            for span in trace.spans:
                if span.duration_ms is None:
                    continue
                if span.kind == "model":
                    model_durations.append(span.duration_ms)
                elif span.kind == "tool":
                    tool_durations.append(span.duration_ms)
            for event in trace.events:
                event_counts[event.type] += 1
                if event.type == "model.completed":
                    usage = event.payload.get("usage") or {}
                    prompt_tokens += _usage_value(usage, "prompt_tokens", "input_tokens")
                    completion_tokens += _usage_value(
                        usage, "completion_tokens", "output_tokens"
                    )
                    total_tokens += int(usage.get("total_tokens") or 0)

        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        memory_counts = self.store.memory_counts()
        return MetricsSnapshot(
            generated_at=utc_now(),
            total_runs=len(runs),
            root_runs=root_runs,
            child_runs=child_runs,
            workflow_runs=workflow_runs,
            delegations=self.store.count_run_relations(),
            sessions=self.store.count_sessions(),
            memories_total=memory_counts["total"],
            memories_active=memory_counts["active"],
            memories_deleted=memory_counts["deleted"],
            memories_expired=memory_counts["expired"],
            memory_searches=event_counts["memory.search.completed"],
            context_compactions=event_counts["context.compacted"],
            runs_by_status=dict(sorted(statuses.items())),
            total_events=sum(event_counts.values()),
            events_by_type=dict(sorted(event_counts.items())),
            model_requests=event_counts["model.requested"],
            tool_requests=event_counts["tool.requested"],
            approval_requests=event_counts["approval.requested"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            run_duration_ms_average=_average(run_durations),
            run_duration_ms_p95=_percentile(run_durations, 0.95),
            model_duration_ms_average=_average(model_durations),
            tool_duration_ms_average=_average(tool_durations),
        )

    @staticmethod
    def _run_span(run: AgentRun, events: list[RuntimeEvent]) -> TraceSpan:
        start_event = next((event for event in events if event.type == "run.created"), None)
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.type in {"run.completed", "run.failed", "run.cancelled"}
            ),
            None,
        )
        return TraceSpan(
            id=f"{run.id}:run",
            name=f"run:{run.agent_name}",
            kind="run",
            status=run.status.value,
            started_at=start_event.timestamp if start_event else run.created_at,
            ended_at=terminal.timestamp if terminal else None,
            start_sequence=start_event.sequence if start_event else 0,
            end_sequence=terminal.sequence if terminal else None,
            attributes={
                "agent_name": run.agent_name,
                "input": run.input,
                "step_count": run.step_count,
                "tool_call_count": run.tool_call_count,
            },
        )

    @staticmethod
    def _paired_spans(events: list[RuntimeEvent], kind: str) -> list[TraceSpan]:
        if kind == "model":
            start_type = "model.requested"
            terminal_types = {"model.completed"}
            key = lambda event: str(event.payload.get("step", event.sequence))
        elif kind == "tool":
            start_type = "tool.requested"
            terminal_types = {
                "tool.completed",
                "tool.failed",
                "tool.rejected",
                "tool.cancelled",
                "tool.outcome_unknown",
            }
            key = lambda event: str(
                event.payload.get("tool_execution_id")
                or event.payload.get("tool_call_id")
                or event.sequence
            )
        else:
            start_type = "approval.requested"
            terminal_types = {"approval.resolved"}
            key = lambda event: str(
                event.payload.get("approval_id")
                or event.payload.get("tool_execution_id")
                or event.sequence
            )

        active: dict[str, RuntimeEvent] = {}
        spans: list[TraceSpan] = []
        for event in events:
            event_key = key(event)
            if event.type == start_type:
                active[event_key] = event
                continue
            if event.type not in terminal_types:
                continue
            start = active.pop(event_key, None)
            if start is None:
                continue
            status = event.type.rsplit(".", 1)[-1]
            name_value = (
                start.payload.get("tool_name")
                or start.payload.get("model")
                or start.payload.get("kind")
                or event_key
            )
            spans.append(
                TraceSpan(
                    id=f"{start.run_id}:{kind}:{event_key}",
                    name=f"{kind}:{name_value}",
                    kind=kind,
                    status=status,
                    started_at=start.timestamp,
                    ended_at=event.timestamp,
                    start_sequence=start.sequence,
                    end_sequence=event.sequence,
                    attributes={**start.payload, **event.payload},
                )
            )
        for event_key, start in active.items():
            spans.append(
                TraceSpan(
                    id=f"{start.run_id}:{kind}:{event_key}",
                    name=f"{kind}:{start.payload.get('tool_name') or event_key}",
                    kind=kind,
                    status="running",
                    started_at=start.timestamp,
                    ended_at=None,
                    start_sequence=start.sequence,
                    end_sequence=None,
                    attributes=start.payload,
                )
            )
        return spans


def _usage_value(usage: dict[str, Any], primary: str, fallback: str) -> int:
    return int(usage.get(primary) or usage.get(fallback) or 0)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 3)

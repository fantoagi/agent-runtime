from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunRelationType(StrEnum):
    DELEGATION = "delegation"
    WORKFLOW = "workflow"


class MemoryScope(StrEnum):
    SESSION = "session"
    AGENT = "agent"


class StepStatus(StrEnum):
    RUNNING = "running"
    WAITING_FOR_TOOLS = "waiting_for_tools"
    COMPLETED = "completed"
    FAILED = "failed"


class UnknownToolResolution(StrEnum):
    CONFIRMED_SUCCEEDED = "confirmed_succeeded"
    CONFIRMED_FAILED = "confirmed_failed"


class ToolExecutionStatus(StrEnum):
    PENDING = "pending"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
TERMINAL_TOOL_EXECUTION_STATUSES = {
    ToolExecutionStatus.COMPLETED,
    ToolExecutionStatus.FAILED,
    ToolExecutionStatus.REJECTED,
    ToolExecutionStatus.CANCELLED,
}

_ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.CREATED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_FOR_APPROVAL,
        RunStatus.PAUSED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_FOR_APPROVAL: {
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.PAUSED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass(slots=True)
class ModelConfig:
    provider: str = "mock"
    model: str = "mock-model"
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class Message:
    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tool_calls"] = [asdict(call) for call in self.tool_calls]
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Message:
        return cls(
            role=value["role"],
            content=value.get("content"),
            tool_call_id=value.get("tool_call_id"),
            tool_calls=[ToolCall(**call) for call in value.get("tool_calls", [])],
            name=value.get("name"),
        )


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    requires_approval: bool = False
    side_effecting: bool = False


@dataclass(slots=True)
class AgentDefinition:
    name: str
    system_prompt: str
    tools: list[ToolDefinition]
    model: ModelConfig = field(default_factory=ModelConfig)
    max_steps: int = 20
    max_tool_calls: int = 40


@dataclass(slots=True)
class AgentRun:
    id: str
    agent_name: str
    input: str
    status: RunStatus = RunStatus.CREATED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    result: str | None = None
    error: str | None = None
    step_count: int = 0
    tool_call_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls, agent_name: str, input_text: str, metadata: dict[str, Any] | None = None
    ) -> AgentRun:
        return cls(
            id=new_id("run"),
            agent_name=agent_name,
            input=input_text,
            metadata=metadata or {},
        )

    def transition_to(self, target: RunStatus) -> None:
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidStateTransition(
                f"Cannot transition run {self.id} from {self.status} to {target}."
            )
        self.status = target
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentRun:
        return cls(
            id=value["id"],
            agent_name=value["agent_name"],
            input=value["input"],
            status=RunStatus(value["status"]),
            created_at=datetime.fromisoformat(value["created_at"]),
            updated_at=datetime.fromisoformat(value["updated_at"]),
            result=value.get("result"),
            error=value.get("error"),
            step_count=value.get("step_count", 0),
            tool_call_count=value.get("tool_call_count", 0),
            metadata=value.get("metadata", {}),
        )


@dataclass(slots=True)
class RunRelation:
    id: str
    parent_run_id: str
    child_run_id: str
    root_run_id: str
    relation_type: RunRelationType
    delegation_key: str
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        parent_run_id: str,
        child_run_id: str,
        root_run_id: str,
        delegation_key: str,
        *,
        relation_type: RunRelationType = RunRelationType.DELEGATION,
        metadata: dict[str, Any] | None = None,
    ) -> RunRelation:
        return cls(
            id=new_id("rel"),
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            root_run_id=root_run_id,
            relation_type=relation_type,
            delegation_key=delegation_key,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "root_run_id": self.root_run_id,
            "relation_type": self.relation_type.value,
            "delegation_key": self.delegation_key,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunRelation:
        return cls(
            id=value["id"],
            parent_run_id=value["parent_run_id"],
            child_run_id=value["child_run_id"],
            root_run_id=value["root_run_id"],
            relation_type=RunRelationType(value["relation_type"]),
            delegation_key=value["delegation_key"],
            created_at=datetime.fromisoformat(value["created_at"]),
            metadata=value.get("metadata", {}),
        )


@dataclass(slots=True)
class Session:
    id: str
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, metadata: dict[str, Any] | None = None) -> Session:
        return cls(id=new_id("session"), metadata=metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class MemoryRecord:
    id: str
    scope: MemoryScope
    scope_id: str
    content: str
    source_run_id: str | None = None
    source_trace_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        scope: MemoryScope,
        scope_id: str,
        content: str,
        *,
        source_run_id: str | None = None,
        source_trace_id: str | None = None,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        if not scope_id.strip():
            raise ValueError("Memory scope_id must not be empty.")
        if not content.strip():
            raise ValueError("Memory content must not be empty.")
        return cls(
            id=new_id("memory"),
            scope=MemoryScope(scope),
            scope_id=scope_id,
            content=content,
            source_run_id=source_run_id,
            source_trace_id=source_trace_id,
            expires_at=expires_at,
            metadata=metadata or {},
        )

    @property
    def active(self) -> bool:
        return self.deleted_at is None and (
            self.expires_at is None or self.expires_at > utc_now()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope.value,
            "scope_id": self.scope_id,
            "content": self.content,
            "source_run_id": self.source_run_id,
            "source_trace_id": self.source_trace_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "active": self.active,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class MemorySearchResult:
    record: MemoryRecord
    rank: float

    def to_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, "record": self.record.to_dict()}


@dataclass(slots=True)
class RuntimeEvent:
    id: str
    run_id: str
    sequence: int
    type: str
    timestamp: datetime
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return cls(
            id=new_id("evt"),
            run_id=run_id,
            sequence=sequence,
            type=event_type,
            timestamp=utc_now(),
            payload=payload or {},
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RuntimeEvent:
        return cls(
            id=value["id"],
            run_id=value["run_id"],
            sequence=value["sequence"],
            type=value["type"],
            timestamp=datetime.fromisoformat(value["timestamp"]),
            payload=value["payload"],
        )


@dataclass(slots=True)
class Checkpoint:
    id: str
    run_id: str
    step: int
    messages: list[Message]
    tool_call_count: int
    created_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(
        cls, run_id: str, step: int, messages: list[Message], tool_call_count: int
    ) -> Checkpoint:
        return cls(new_id("ckpt"), run_id, step, messages, tool_call_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "step": self.step,
            "messages": [message.to_dict() for message in self.messages],
            "tool_call_count": self.tool_call_count,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Checkpoint:
        return cls(
            id=value["id"],
            run_id=value["run_id"],
            step=value["step"],
            messages=[Message.from_dict(message) for message in value["messages"]],
            tool_call_count=value["tool_call_count"],
            created_at=datetime.fromisoformat(value["created_at"]),
        )


@dataclass(slots=True)
class Step:
    id: str
    run_id: str
    step_index: int
    status: StepStatus = StepStatus.RUNNING
    assistant_message: Message | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, run_id: str, step_index: int) -> Step:
        return cls(id=new_id("step"), run_id=run_id, step_index=step_index)


@dataclass(slots=True)
class ToolExecution:
    id: str
    run_id: str
    step_id: str
    position: int
    tool_call: ToolCall
    status: ToolExecutionStatus
    idempotency_key: str
    requires_approval: bool = False
    side_effecting: bool = False
    result_content: str | None = None
    result_data: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    resolution: UnknownToolResolution | None = None
    resolution_reason: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        run_id: str,
        step_id: str,
        position: int,
        tool_call: ToolCall,
        *,
        requires_approval: bool,
        side_effecting: bool,
    ) -> ToolExecution:
        return cls(
            id=new_id("tool_exec"),
            run_id=run_id,
            step_id=step_id,
            position=position,
            tool_call=tool_call,
            status=ToolExecutionStatus.PENDING,
            idempotency_key=f"{run_id}:{step_id}:{tool_call.id}",
            requires_approval=requires_approval,
            side_effecting=side_effecting,
        )


@dataclass(slots=True)
class Approval:
    id: str
    run_id: str
    tool_call: ToolCall
    status: str = "pending"
    reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    resolved_at: datetime | None = None
    tool_execution_id: str | None = None
    kind: str = "tool"

    @classmethod
    def create(
        cls,
        run_id: str,
        tool_call: ToolCall,
        *,
        tool_execution_id: str | None = None,
        kind: str = "tool",
    ) -> Approval:
        return cls(
            id=new_id("approval"),
            run_id=run_id,
            tool_call=tool_call,
            tool_execution_id=tool_execution_id,
            kind=kind,
        )


class RuntimeErrorBase(Exception):
    """Base class for runtime errors that should be exposed to callers."""


class InvalidStateTransition(RuntimeErrorBase):
    pass


class RunNotFound(RuntimeErrorBase):
    pass


class ApprovalRequired(RuntimeErrorBase):
    pass


class ToolExecutionError(RuntimeErrorBase):
    pass


class ToolOutcomeUnknown(ToolExecutionError):
    """The tool may have produced a side effect, but no durable result is known."""


class ToolValidationError(ToolExecutionError):
    pass


class RunLimitExceeded(RuntimeErrorBase):
    pass


class IdempotencyConflict(RuntimeErrorBase):
    """An idempotency key was reused for a different logical request."""


class RuntimeCapacityError(RuntimeErrorBase):
    """The runtime cannot admit more in-flight work right now."""


class RuntimeLifecycleError(RuntimeErrorBase):
    """The runtime cannot accept an operation in its current lifecycle state."""


class RuntimeClosedError(RuntimeLifecycleError):
    """The runtime or one of its owned resources has already been closed."""


class ProviderError(RuntimeErrorBase):
    """Base class for model provider failures with stable retry semantics."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProviderTransportError(ProviderError):
    """A retryable network transport failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class ProviderHTTPError(ProviderError):
    """An HTTP failure returned by a model provider."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, retryable=retryable)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class ProviderProtocolError(ProviderError):
    """The provider returned a malformed or incomplete response."""


class StoreError(RuntimeErrorBase):
    """Base class for persistent store failures."""


class StoreBusyError(StoreError):
    """SQLite remained locked after the configured retry budget."""


class StoreCorruptionError(StoreError):
    """SQLite integrity validation failed."""


class MigrationError(StoreError):
    """A schema migration is missing, changed, or could not be applied."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import (
    CapabilityPolicyAction,
    RuntimeClosedError,
    ToolCapability,
    ToolDefinition,
    ToolExecutionError,
    ToolOutcomeUnknown,
    ToolPolicyDeniedError,
    ToolValidationError,
)
from .storage import ArtifactStore

ToolHandler = Callable[[dict[str, Any], "ToolContext"], Any | Awaitable[Any]]


class ManagedToolResource(Protocol):
    def close(self) -> None: ...

    async def aclose(self) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ToolAuthorization:
    allowed: bool
    requires_approval: bool
    sandbox_required: bool
    capabilities: tuple[ToolCapability, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "sandbox_required": self.sandbox_required,
            "capabilities": [capability.value for capability in self.capabilities],
            "reason": self.reason,
        }


class CapabilityPolicy:
    """Combines capability rules; deny wins, then sandbox, then approval."""

    def __init__(
        self,
        rules: Mapping[ToolCapability | str, CapabilityPolicyAction | str] | None = None,
    ) -> None:
        defaults: dict[ToolCapability, CapabilityPolicyAction] = {
            ToolCapability.FILE_READ: CapabilityPolicyAction.ALLOW,
            ToolCapability.FILE_WRITE: CapabilityPolicyAction.REQUIRE_APPROVAL,
            ToolCapability.PROCESS_EXEC: CapabilityPolicyAction.SANDBOX_ONLY,
            ToolCapability.NETWORK_ACCESS: CapabilityPolicyAction.DENY,
            ToolCapability.SECRET_READ: CapabilityPolicyAction.DENY,
        }
        if rules:
            defaults.update(
                {
                    ToolCapability(capability): CapabilityPolicyAction(action)
                    for capability, action in rules.items()
                }
            )
        self._rules = defaults

    def evaluate(self, definition: ToolDefinition, *, sandboxed: bool) -> ToolAuthorization:
        capabilities = tuple(ToolCapability(value) for value in definition.capabilities)
        actions = [self._rules.get(capability, CapabilityPolicyAction.DENY) for capability in capabilities]
        if CapabilityPolicyAction.DENY in actions:
            denied = [
                capability.value
                for capability, action in zip(capabilities, actions, strict=True)
                if action == CapabilityPolicyAction.DENY
            ]
            return ToolAuthorization(
                allowed=False,
                requires_approval=False,
                sandbox_required=False,
                capabilities=capabilities,
                reason="Denied capabilities: " + ", ".join(denied),
            )
        sandbox_required = definition.sandbox_only or CapabilityPolicyAction.SANDBOX_ONLY in actions
        if sandbox_required and not sandboxed:
            return ToolAuthorization(
                allowed=False,
                requires_approval=False,
                sandbox_required=True,
                capabilities=capabilities,
                reason="Tool policy requires a managed sandbox executor.",
            )
        return ToolAuthorization(
            allowed=True,
            requires_approval=(
                definition.requires_approval
                or CapabilityPolicyAction.REQUIRE_APPROVAL in actions
            ),
            sandbox_required=sandbox_required,
            capabilities=capabilities,
        )

    def snapshot(self) -> dict[str, str]:
        return {
            capability.value: action.value
            for capability, action in sorted(self._rules.items(), key=lambda item: item[0].value)
        }


class CancellationToken:
    """Cooperative cancellation signal for long-running tool handlers."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(slots=True)
class ToolContext:
    run_id: str
    step_id: int
    workspace_path: Path
    metadata: dict[str, Any]
    cancellation_token: CancellationToken | None = None
    idempotency_key: str | None = None

    def raise_if_cancelled(self) -> None:
        if self.cancellation_token is not None:
            self.cancellation_token.raise_if_cancelled()


@dataclass(slots=True)
class ToolResult:
    content: str
    data: dict[str, Any] | None = None

    @classmethod
    def from_value(cls, value: Any) -> ToolResult:
        if isinstance(value, ToolResult):
            return value
        if isinstance(value, str):
            return cls(content=value)
        return cls(
            content=json.dumps(value, ensure_ascii=False, default=str),
            data=value if isinstance(value, dict) else None,
        )


@dataclass(slots=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler
    timeout_seconds: float = 30.0
    sandboxed: bool = False


class ToolRegistry:
    def __init__(
        self,
        *,
        max_sync_workers: int = 8,
        max_pending_sync_tools: int = 32,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        if max_sync_workers < 1:
            raise ValueError("max_sync_workers must be at least 1.")
        if max_pending_sync_tools < max_sync_workers:
            raise ValueError("max_pending_sync_tools must be at least max_sync_workers.")
        self._tools: dict[str, RegisteredTool] = {}
        self._max_sync_workers = max_sync_workers
        self._max_pending_sync_tools = max_pending_sync_tools
        self._executor: ThreadPoolExecutor | None = None
        self._capacity = asyncio.Semaphore(max_pending_sync_tools)
        self._sync_futures: set[asyncio.Future[Any]] = set()
        self._capability_policy = capability_policy or CapabilityPolicy()
        self._managed_resources: list[ManagedToolResource] = []
        self._closed = False

    def capacity_snapshot(self) -> dict[str, int | bool]:
        return {
            "closed": self._closed,
            "pending_sync_tools": sum(
                1 for future in self._sync_futures if not future.done()
            ),
            "max_sync_workers": self._max_sync_workers,
            "max_pending_sync_tools": self._max_pending_sync_tools,
            "managed_resources": len(self._managed_resources),
        }

    def configure_execution(self, *, max_sync_workers: int, max_pending_sync_tools: int) -> None:
        if self._executor is not None:
            if (
                max_sync_workers != self._max_sync_workers
                or max_pending_sync_tools != self._max_pending_sync_tools
            ):
                raise RuntimeError("Tool execution cannot be reconfigured after first use.")
            return
        if max_sync_workers < 1 or max_pending_sync_tools < max_sync_workers:
            raise ValueError("Invalid synchronous tool executor limits.")
        self._max_sync_workers = max_sync_workers
        self._max_pending_sync_tools = max_pending_sync_tools
        self._capacity = asyncio.Semaphore(max_pending_sync_tools)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for resource in self._managed_resources:
            resource.close()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    async def aclose(self, timeout_seconds: float = 30.0) -> None:
        """Stop accepting work and wait a bounded time for running sync handlers."""
        self.close()
        pending = [future for future in self._sync_futures if not future.done()]
        if pending:
            await asyncio.wait(pending, timeout=max(0.0, timeout_seconds))
        if self._managed_resources:
            await asyncio.gather(
                *(resource.aclose() for resource in self._managed_resources),
                return_exceptions=True,
            )

    def manage(self, resource: ManagedToolResource) -> None:
        if self._closed:
            raise RuntimeClosedError("Tool registry is closed.")
        if resource not in self._managed_resources:
            self._managed_resources.append(resource)

    def security_snapshot(self) -> dict[str, Any]:
        return {
            "policy": self._capability_policy.snapshot(),
            "tools": {
                name: self.authorization(name).to_dict()
                for name in sorted(self._tools)
            },
            "sandboxes": [resource.snapshot() for resource in self._managed_resources],
        }

    def _sync_executor(self) -> ThreadPoolExecutor:
        if self._closed:
            raise RuntimeClosedError("Tool registry is closed.")
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_sync_workers,
                thread_name_prefix="agent-runtime-tool",
            )
        return self._executor

    def register(
        self,
        definition: ToolDefinition,
        handler: ToolHandler,
        timeout_seconds: float = 30.0,
        *,
        sandboxed: bool = False,
    ) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool {definition.name!r} is already registered.")
        self._tools[definition.name] = RegisteredTool(
            definition, handler, timeout_seconds, sandboxed
        )

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolExecutionError(f"Tool {name!r} is not registered.") from error

    def authorization(self, name: str) -> ToolAuthorization:
        tool = self.get(name)
        return self._capability_policy.evaluate(tool.definition, sandboxed=tool.sandboxed)

    def require_authorized(self, name: str) -> ToolAuthorization:
        authorization = self.authorization(name)
        if not authorization.allowed:
            raise ToolPolicyDeniedError(
                f"Tool {name!r} is denied by capability policy: {authorization.reason}"
            )
        return authorization

    def definitions_for(
        self, definitions: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        registered: list[ToolDefinition] = []
        for definition in definitions:
            tool = self.get(definition.name)
            self.require_authorized(definition.name)
            registered.append(tool.definition)
        return registered

    async def invoke(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if self._closed:
            raise RuntimeClosedError("Tool registry is closed.")
        tool = self.get(name)
        self.require_authorized(name)
        validate_input(
            arguments, tool.definition.input_schema, tool_name=tool.definition.name
        )
        context.raise_if_cancelled()
        started = False
        try:
            if inspect.iscoroutinefunction(tool.handler):
                started = True
                produced = await asyncio.wait_for(
                    tool.handler(arguments, context), timeout=tool.timeout_seconds
                )
            else:
                async with self._capacity:
                    context.raise_if_cancelled()
                    started = True
                    loop = asyncio.get_running_loop()
                    future = loop.run_in_executor(
                        self._sync_executor(), tool.handler, arguments, context
                    )
                    self._sync_futures.add(future)
                    future.add_done_callback(self._sync_futures.discard)
                    produced = await asyncio.wait_for(
                        asyncio.shield(future), timeout=tool.timeout_seconds
                    )
            if inspect.isawaitable(produced):
                produced = await asyncio.wait_for(produced, timeout=tool.timeout_seconds)
            context.raise_if_cancelled()
            return ToolResult.from_value(produced)
        except asyncio.CancelledError as error:
            if started and tool.definition.side_effecting:
                raise ToolOutcomeUnknown(
                    f"Tool {name!r} was interrupted after invocation; its side effect is unknown."
                ) from error
            raise
        except TimeoutError as error:
            if started and tool.definition.side_effecting:
                raise ToolOutcomeUnknown(
                    f"Tool {name!r} timed out after {tool.timeout_seconds}s; "
                    "its side effect is unknown."
                ) from error
            raise ToolExecutionError(
                f"Tool {name!r} timed out after {tool.timeout_seconds}s."
            ) from error
        except ToolExecutionError:
            raise
        except Exception as error:
            raise ToolExecutionError(f"Tool {name!r} failed: {error}") from error


def validate_input(
    value: dict[str, Any],
    schema: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> None:
    if schema.get("type", "object") != "object":
        raise ToolValidationError("Tool input schema root must be an object.")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(value, dict):
        raise ToolValidationError("Tool arguments must be an object.")
    allowed = ", ".join(sorted(properties)) or "(none)"
    for key in required:
        if key not in value:
            raise ToolValidationError(
                f"Missing required tool argument: {key}. Allowed arguments: {allowed}."
            )
    if schema.get("additionalProperties") is False:
        unknown = set(value) - set(properties)
        if unknown:
            hint = ""
            if tool_name == "search_text" and "max_lines" in unknown:
                hint = (
                    " Use max_results to bound search matches, then use "
                    "read_file_lines for bounded line ranges."
                )
            raise ToolValidationError(
                f"Unsupported tool arguments: {', '.join(sorted(unknown))}. "
                f"Allowed arguments: {allowed}.{hint}"
            )
    for key, item in value.items():
        if key in properties:
            _validate_value(key, item, properties[key])


def _validate_value(key: str, value: Any, schema: dict[str, Any]) -> None:
    expected = schema.get("type")
    valid = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }
    if expected and not valid.get(expected, True):
        raise ToolValidationError(f"Argument {key!r} must be a {expected}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolValidationError(
            f"Argument {key!r} must be one of {schema['enum']!r}."
        )


def confined_path(workspace: Path, requested_path: str) -> Path:
    root = workspace.resolve()
    candidate = (root / requested_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolExecutionError("Requested path escapes the configured workspace.")
    return candidate


def register_builtin_tools(
    registry: ToolRegistry, *, artifact_path: str | Path | None = None
) -> None:
    registry.register(
        ToolDefinition(
            name="calculator",
            description=(
                "Evaluate a basic arithmetic expression containing only numbers "
                "and arithmetic operators."
            ),
            input_schema={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
                "additionalProperties": False,
            },
        ),
        _calculator,
    )
    registry.register(
        ToolDefinition(
            name="read_text_file",
            description=(
                "Read a UTF-8 source or documentation file inside the configured workspace. "
                "Do not use this for Runtime Tool Result Artifacts; use read_artifact instead."
            ),
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            capabilities=(ToolCapability.FILE_READ,),
        ),
        lambda arguments, context: _read_text_file(
            arguments, context, artifact_root=artifact_path
        ),
    )
    if artifact_path is not None:
        artifact_store = ArtifactStore(artifact_path)
        registry.register(
            ToolDefinition(
                name="read_artifact",
                description=(
                    "Read one bounded page from a Runtime Tool Result Artifact returned in "
                    "_artifact.path or _artifact.relative_path. Continue with next_offset while "
                    "has_more is true. Never use Python, cat, type, or run_process merely to print "
                    "an artifact."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer"},
                        "max_chars": {"type": "integer"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                capabilities=(ToolCapability.FILE_READ,),
            ),
            lambda arguments, context: _read_artifact(
                arguments, context, artifact_store=artifact_store
            ),
        )
    registry.register(
        ToolDefinition(
            name="write_text_file",
            description="Write a UTF-8 text file inside the configured workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            requires_approval=True,
            side_effecting=True,
            capabilities=(ToolCapability.FILE_WRITE,),
        ),
        _write_text_file,
    )


def _calculator(arguments: dict[str, Any], context: ToolContext) -> str:
    context.raise_if_cancelled()
    expression = arguments["expression"]
    allowed = set("0123456789+-*/(). %")
    if not expression or any(char not in allowed for char in expression):
        raise ToolValidationError(
            "Calculator accepts only digits, whitespace, and + - * / % ( )."
        )
    try:
        return str(
            eval(expression, {"__builtins__": {}}, {})
        )
    except Exception as error:
        raise ToolValidationError(
            f"Invalid arithmetic expression: {error}"
        ) from error


def _read_text_file(
    arguments: dict[str, Any],
    context: ToolContext,
    *,
    artifact_root: str | Path | None = None,
) -> str:
    context.raise_if_cancelled()
    requested = str(arguments["path"])
    supplied = Path(requested)
    candidate = (
        supplied.resolve()
        if supplied.is_absolute()
        else (context.workspace_path / supplied).resolve()
    )
    if artifact_root is not None:
        root = Path(artifact_root).resolve()
        tool_results = (root / context.run_id / "tool-results").resolve()
        if candidate == tool_results or tool_results in candidate.parents:
            raise ToolExecutionError(
                "This path is a Runtime Tool Result Artifact. Use read_artifact with the "
                "artifact path and an offset/max_chars page instead of read_text_file."
            )
    path = confined_path(context.workspace_path, requested)
    if not path.is_file():
        raise ToolExecutionError(f"File does not exist: {arguments['path']}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ToolExecutionError(
            f"File is not valid UTF-8: {arguments['path']}"
        ) from error


def _read_artifact(
    arguments: dict[str, Any],
    context: ToolContext,
    *,
    artifact_store: ArtifactStore,
) -> ToolResult:
    context.raise_if_cancelled()
    offset = arguments.get("offset", 0)
    max_chars = arguments.get("max_chars", 3000)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ToolValidationError("read_artifact offset must be a non-negative integer.")
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or max_chars < 256
        or max_chars > 4000
    ):
        raise ToolValidationError("read_artifact max_chars must be between 256 and 4000.")
    try:
        page = artifact_store.read_tool_result_page(
            context.run_id,
            str(arguments["path"]),
            offset=offset,
            max_chars=max_chars,
        )
    except (FileNotFoundError, ValueError, OSError) as error:
        raise ToolExecutionError(str(error)) from error
    header = (
        f"Artifact {page.path} characters {page.offset}-{page.next_offset} "
        f"of {page.total_chars}"
    )
    if page.has_more:
        header += f" (more available; next_offset={page.next_offset})"
    content = header + ("\n" + page.content if page.content else "")
    return ToolResult(
        content=content,
        data={
            "path": page.path,
            "content": page.content,
            "offset": page.offset,
            "next_offset": page.next_offset,
            "total_chars": page.total_chars,
            "has_more": page.has_more,
            "sha256": page.sha256,
            "_runtime_artifact_page": True,
        },
    )


def _write_text_file(
    arguments: dict[str, Any], context: ToolContext
) -> dict[str, str]:
    context.raise_if_cancelled()
    path = confined_path(context.workspace_path, arguments["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(arguments["content"])
            handle.flush()
            os.fsync(handle.fileno())
        context.raise_if_cancelled()
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return {"path": str(path), "status": "written"}

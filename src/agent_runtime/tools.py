from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import (
    RuntimeClosedError,
    ToolDefinition,
    ToolExecutionError,
    ToolOutcomeUnknown,
    ToolValidationError,
)

ToolHandler = Callable[[dict[str, Any], "ToolContext"], Any | Awaitable[Any]]


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


class ToolRegistry:
    def __init__(
        self,
        *,
        max_sync_workers: int = 8,
        max_pending_sync_tools: int = 32,
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
        self._closed = False

    def capacity_snapshot(self) -> dict[str, int | bool]:
        return {
            "closed": self._closed,
            "pending_sync_tools": sum(
                1 for future in self._sync_futures if not future.done()
            ),
            "max_sync_workers": self._max_sync_workers,
            "max_pending_sync_tools": self._max_pending_sync_tools,
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
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    async def aclose(self, timeout_seconds: float = 30.0) -> None:
        """Stop accepting work and wait a bounded time for running sync handlers."""
        self.close()
        pending = [future for future in self._sync_futures if not future.done()]
        if pending:
            await asyncio.wait(pending, timeout=max(0.0, timeout_seconds))

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
    ) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool {definition.name!r} is already registered.")
        self._tools[definition.name] = RegisteredTool(
            definition, handler, timeout_seconds
        )

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolExecutionError(f"Tool {name!r} is not registered.") from error

    def definitions_for(
        self, definitions: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        registered: list[ToolDefinition] = []
        for definition in definitions:
            tool = self.get(definition.name)
            registered.append(tool.definition)
        return registered

    async def invoke(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if self._closed:
            raise RuntimeClosedError("Tool registry is closed.")
        tool = self.get(name)
        validate_input(arguments, tool.definition.input_schema)
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


def validate_input(value: dict[str, Any], schema: dict[str, Any]) -> None:
    if schema.get("type", "object") != "object":
        raise ToolValidationError("Tool input schema root must be an object.")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(value, dict):
        raise ToolValidationError("Tool arguments must be an object.")
    for key in required:
        if key not in value:
            raise ToolValidationError(f"Missing required tool argument: {key}.")
    if schema.get("additionalProperties") is False:
        unknown = set(value) - set(properties)
        if unknown:
            raise ToolValidationError(
                f"Unsupported tool arguments: {', '.join(sorted(unknown))}."
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


def register_builtin_tools(registry: ToolRegistry) -> None:
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
            description="Read a UTF-8 text file inside the configured workspace.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        _read_text_file,
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


def _read_text_file(arguments: dict[str, Any], context: ToolContext) -> str:
    context.raise_if_cancelled()
    path = confined_path(context.workspace_path, arguments["path"])
    if not path.is_file():
        raise ToolExecutionError(f"File does not exist: {arguments['path']}")
    return path.read_text(encoding="utf-8")


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

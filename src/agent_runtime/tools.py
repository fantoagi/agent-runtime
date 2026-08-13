from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import ToolDefinition, ToolExecutionError, ToolValidationError

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
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

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
        tool = self.get(name)
        validate_input(arguments, tool.definition.input_schema)
        context.raise_if_cancelled()
        try:
            produced = tool.handler(arguments, context)
            if inspect.isawaitable(produced):
                produced = await asyncio.wait_for(
                    produced, timeout=tool.timeout_seconds
                )
            context.raise_if_cancelled()
            return ToolResult.from_value(produced)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
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
        )  # noqa: S307 - grammar is constrained above.
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
    path.write_text(arguments["content"], encoding="utf-8")
    return {"path": str(path), "status": "written"}

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agent_runtime.domain import (
    Message,
    ModelConfig,
    ProviderProtocolError,
    RuntimeClosedError,
    ToolDefinition,
    ToolExecutionError,
    ToolValidationError,
)
from agent_runtime.providers import (
    OpenAICompatibleProvider,
    ToolCallDelta,
    _message_to_wire,
    _parse_complete_response,
    _parse_retry_after,
    _parse_sse_delta,
    _response_from_accumulated,
    arithmetic_demo_responder,
)
from agent_runtime.runtime import RuntimeConfig
from agent_runtime.storage import MIGRATIONS, SQLiteStore
from agent_runtime.tools import (
    CancellationToken,
    ToolContext,
    ToolRegistry,
    ToolResult,
    confined_path,
    register_builtin_tools,
    validate_input,
)


def test_cancellation_token_and_runtime_config_validation(workspace: Path) -> None:
    token = CancellationToken()
    assert token.cancelled is False
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(asyncio.CancelledError):
        token.raise_if_cancelled()

    base = {
        "workspace_path": workspace,
        "database_path": workspace / "state.sqlite3",
    }
    for override in (
        {"context_token_budget": 63},
        {"max_sync_tool_workers": 0},
        {"max_sync_tool_workers": 2, "max_pending_sync_tools": 1},
        {"max_inflight_runs": 0},
        {"max_concurrent_model_requests": 0},
        {"sqlite_busy_timeout_seconds": -1},
        {"memory_search_limit": -1},
        {"large_tool_result_chars": 127},
    ):
        with pytest.raises(ValueError):
            RuntimeConfig(**base, **override)


def test_tool_registry_configuration_lookup_and_closed_state(workspace: Path) -> None:
    with pytest.raises(ValueError):
        ToolRegistry(max_sync_workers=0)
    with pytest.raises(ValueError):
        ToolRegistry(max_sync_workers=2, max_pending_sync_tools=1)

    registry = ToolRegistry(max_sync_workers=1, max_pending_sync_tools=1)
    definition = ToolDefinition("echo", "echo", {"type": "object"})
    registry.register(definition, lambda arguments, context: "ok")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition, lambda arguments, context: "again")
    assert registry.definitions_for([definition]) == [definition]
    with pytest.raises(ToolExecutionError, match="not registered"):
        registry.get("missing")
    registry.close()
    registry.close()
    with pytest.raises(RuntimeClosedError):
        asyncio.run(registry.invoke("echo", {}, ToolContext("r", 1, workspace, {})))


@pytest.mark.asyncio
async def test_tool_errors_timeouts_and_nested_awaitable(workspace: Path) -> None:
    registry = ToolRegistry(max_sync_workers=1, max_pending_sync_tools=1)

    def fails(arguments, context):
        del arguments, context
        raise RuntimeError("boom")

    def slow(arguments, context):
        del arguments, context
        import time

        time.sleep(0.05)
        return "late"

    def returns_coroutine(arguments, context):
        del arguments, context

        async def inner():
            return "nested"

        return inner()

    schema = {"type": "object"}
    registry.register(ToolDefinition("fails", "", schema), fails)
    registry.register(ToolDefinition("slow", "", schema), slow, timeout_seconds=0.001)
    registry.register(ToolDefinition("nested", "", schema), returns_coroutine)
    with pytest.raises(ToolExecutionError, match="boom"):
        await registry.invoke("fails", {}, ToolContext("r", 1, workspace, {}))
    with pytest.raises(ToolExecutionError, match="timed out"):
        await registry.invoke("slow", {}, ToolContext("r", 1, workspace, {}))
    result = await registry.invoke("nested", {}, ToolContext("r", 1, workspace, {}))
    assert result.content == "nested"
    registry.close()


@pytest.mark.parametrize(
    ("value", "schema", "message"),
    [
        ({}, {"type": "string"}, "root"),
        ({}, {"type": "object", "required": ["x"]}, "Missing"),
        ({"x": 1}, {"type": "object", "properties": {}, "additionalProperties": False}, "Unsupported"),
        ({"x": "1"}, {"type": "object", "properties": {"x": {"type": "integer"}}}, "integer"),
        ({"x": "b"}, {"type": "object", "properties": {"x": {"enum": ["a"]}}}, "one of"),
    ],
)
def test_tool_validation_error_contract(value, schema, message: str) -> None:
    with pytest.raises(ToolValidationError, match=message):
        validate_input(value, schema)


def test_tool_validation_accepts_supported_json_types() -> None:
    validate_input(
        {"s": "x", "i": 1, "n": 1.5, "b": True, "o": {}, "a": []},
        {
            "type": "object",
            "properties": {
                "s": {"type": "string"},
                "i": {"type": "integer"},
                "n": {"type": "number"},
                "b": {"type": "boolean"},
                "o": {"type": "object"},
                "a": {"type": "array"},
            },
        },
    )


def test_confined_path_accepts_workspace_root(workspace: Path) -> None:
    assert confined_path(workspace, ".") == workspace.resolve()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{}]},
        {"choices": [{"message": {"content": 1}}]},
        {"choices": [{"message": {}, "finish_reason": 1}]},
        {"choices": [{"message": {}}], "usage": []},
        {"choices": [{"message": {"tool_calls": [None]}}]},
        {"choices": [{"message": {"tool_calls": [{}]}}]},
        {"choices": [{"message": {"tool_calls": [{"function": {}}]}}]},
        {"choices": [{"message": {"tool_calls": [{"id": "x", "function": {"name": "n", "arguments": []}}]}}]},
        {"choices": [{"message": {"tool_calls": [{"id": "x", "function": {"name": "n", "arguments": "[]"}}]}}]},
    ],
)
def test_complete_response_rejects_malformed_payloads(payload) -> None:
    with pytest.raises(ProviderProtocolError):
        _parse_complete_response(payload)


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"choices":{}}',
        '{"choices":[null]}',
        '{"choices":[{"delta":[]}]}',
        '{"choices":[{"delta":{"content":1}}]}',
        '{"choices":[{"delta":{},"finish_reason":1}]}',
        '{"choices":[],"usage":[]}',
        '{"choices":[{"delta":{"tool_calls":{}}}]}',
        '{"choices":[{"delta":{"tool_calls":[null]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":-1}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":[]}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"id":1}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":1}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":1}}]}}]}',
    ],
)
def test_sse_delta_rejects_malformed_payloads(payload: str) -> None:
    with pytest.raises(ProviderProtocolError):
        _parse_sse_delta(payload)


def test_streamed_response_requires_name_and_object_arguments() -> None:
    with pytest.raises(ProviderProtocolError, match="no function name"):
        _response_from_accumulated([], {0: ToolCallDelta(0)}, None, {})
    with pytest.raises(ProviderProtocolError, match="Invalid streamed"):
        _response_from_accumulated([], {0: ToolCallDelta(0, name="x", arguments="{")}, None, {})
    with pytest.raises(ProviderProtocolError, match="must be an object"):
        _response_from_accumulated([], {0: ToolCallDelta(0, name="x", arguments="[]")}, None, {})


def test_retry_after_and_message_wire_edge_cases() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("invalid") is None
    future = datetime.now(UTC) + timedelta(seconds=2)
    parsed = _parse_retry_after(future.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert parsed is not None and parsed >= 0
    wire = _message_to_wire(Message(role="assistant"))
    assert wire == {"role": "assistant"}


def test_demo_responder_non_arithmetic_path() -> None:
    response = arithmetic_demo_responder(
        [Message(role="user", content="hello")], [], ModelConfig()
    )
    assert "please enter" in (response.content or "")


@pytest.mark.asyncio
async def test_provider_requires_key_closes_idempotently_and_rejects_invalid_json() -> None:
    provider = OpenAICompatibleProvider(api_key=None)
    provider.api_key = None
    with pytest.raises(ValueError, match="API key"):
        await provider.complete([], [], ModelConfig(model="test"))

    async def invalid_json(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    provider = OpenAICompatibleProvider(
        api_key="test", transport=httpx.MockTransport(invalid_json)
    )
    with pytest.raises(ProviderProtocolError, match="invalid JSON"):
        await provider.complete([], [], ModelConfig(model="test"))
    await provider.aclose()
    await provider.aclose()
    with pytest.raises(Exception, match="closed"):
        provider._get_client()


@pytest.mark.parametrize("legacy_version", [1, 2, 3, 4, 5, 6])
def test_each_historical_schema_upgrades_to_latest(
    workspace: Path, legacy_version: int
) -> None:
    database = workspace / f"legacy-{legacy_version}.sqlite3"
    connection = sqlite3.connect(database)
    for version, name, sql in MIGRATIONS[:legacy_version]:
        connection.executescript(sql)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, datetime.now(UTC).isoformat()),
        )
    connection.commit()
    connection.close()
    store = SQLiteStore(database)
    assert store.schema_version == 7
    assert store.health_check()["status"] == "ok"
    store.close()
    store.close()
    with pytest.raises(RuntimeClosedError):
        store.health_check()


def test_tool_input_must_be_mapping() -> None:
    with pytest.raises(ToolValidationError, match="must be an object"):
        validate_input([], {"type": "object"})  # type: ignore[arg-type]


def test_tool_result_and_registry_reconfiguration_branches(workspace: Path) -> None:
    existing = ToolResult(content="ready")
    assert ToolResult.from_value(existing) is existing
    assert ToolResult.from_value({"ok": True}).data == {"ok": True}
    assert ToolResult.from_value([1, 2]).data is None

    context = ToolContext("r", 1, workspace, {})
    context.raise_if_cancelled()

    registry = ToolRegistry(max_sync_workers=1, max_pending_sync_tools=1)
    registry.register(ToolDefinition("echo", "", {"type": "object"}), lambda a, c: "ok")
    asyncio.run(registry.invoke("echo", {}, context))
    registry.configure_execution(max_sync_workers=1, max_pending_sync_tools=1)
    with pytest.raises(RuntimeError, match="cannot be reconfigured"):
        registry.configure_execution(max_sync_workers=2, max_pending_sync_tools=2)
    registry.close()


@pytest.mark.asyncio
async def test_builtin_tool_failure_branches(workspace: Path) -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    context = ToolContext("r", 1, workspace, {})
    with pytest.raises(ToolValidationError, match="accepts only"):
        await registry.invoke("calculator", {"expression": ""}, context)
    with pytest.raises(ToolValidationError, match="Invalid arithmetic"):
        await registry.invoke("calculator", {"expression": "1/0"}, context)
    with pytest.raises(ToolExecutionError, match="does not exist"):
        await registry.invoke("read_text_file", {"path": "missing.txt"}, context)
    registry.close()

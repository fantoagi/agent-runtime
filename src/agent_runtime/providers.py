from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from .domain import (
    Message,
    ModelConfig,
    ProviderHTTPError,
    ProviderProtocolError,
    ProviderTransportError,
    ToolCall,
    ToolDefinition,
)


@dataclass(slots=True)
class ModelResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass(slots=True)
class ModelTokenDelta:
    content: str | None = None
    tool_call_deltas: list[ToolCallDelta] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw_delta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_call_deltas": [item.to_dict() for item in self.tool_call_deltas],
            "finish_reason": self.finish_reason,
            "usage": self.usage,
        }


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        config: ModelConfig,
    ) -> ModelResponse: ...


class StreamingModelProvider(Protocol):
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        config: ModelConfig,
    ) -> AsyncIterator[ModelTokenDelta]: ...


ModelResponder = Callable[
    [list[Message], list[ToolDefinition], ModelConfig],
    ModelResponse | Awaitable[ModelResponse],
]


class MockProvider:
    """Deterministic non-streaming provider for tests and local demos."""

    def __init__(self, responder: ModelResponder) -> None:
        self._responder = responder

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> ModelResponse:
        result = self._responder(messages, tools, config)
        if isinstance(result, ModelResponse):
            return result
        return await result


class MockStreamingProvider:
    """Deterministic streaming provider used to exercise token event semantics."""

    def __init__(self, deltas: list[ModelTokenDelta]) -> None:
        self._deltas = list(deltas)

    async def stream(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> AsyncIterator[ModelTokenDelta]:
        del messages, tools, config
        for delta in self._deltas:
            await asyncio.sleep(0)
            yield delta

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> ModelResponse:
        content: list[str] = []
        calls: dict[int, ToolCallDelta] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        async for delta in self.stream(messages, tools, config):
            if delta.content:
                content.append(delta.content)
            for item in delta.tool_call_deltas:
                existing = calls.setdefault(item.index, ToolCallDelta(item.index))
                existing.id = item.id or existing.id
                existing.name = item.name or existing.name
                existing.arguments += item.arguments
            finish_reason = delta.finish_reason or finish_reason
            usage.update(delta.usage)
        return _response_from_accumulated(content, calls, finish_reason, usage)


class OpenAICompatibleProvider:
    """Managed async Chat Completions client with cancellable SSE streaming."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_sse_event_bytes: int = 1_048_576,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.timeout_seconds = timeout_seconds
        self.max_sse_event_bytes = max_sse_event_bytes
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> ModelResponse:
        body = self._request_body(messages, tools, config)
        try:
            response = await self._get_client().post(
                "/chat/completions",
                json=body,
                headers=self._headers(stream=False),
            )
        except httpx.TimeoutException as error:
            raise ProviderTransportError(f"Model API request timed out: {error}") from error
        except httpx.TransportError as error:
            raise ProviderTransportError(f"Model API request failed: {error}") from error
        self._raise_for_status(response)
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise ProviderProtocolError("Provider returned invalid JSON.") from error
        return _parse_complete_response(payload)

    async def stream(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> AsyncIterator[ModelTokenDelta]:
        body = self._request_body(messages, tools, config)
        body["stream"] = True
        body.setdefault("stream_options", {"include_usage": True})
        saw_terminal = False
        try:
            async with self._get_client().stream(
                "POST",
                "/chat/completions",
                json=body,
                headers=self._headers(stream=True),
            ) as response:
                self._raise_for_status(response)
                data_lines: list[str] = []
                event_bytes = 0
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        value = line[5:].lstrip()
                        event_bytes += len(value.encode("utf-8"))
                        if event_bytes > self.max_sse_event_bytes:
                            raise ProviderProtocolError(
                                "Provider SSE event exceeded the configured size limit."
                            )
                        data_lines.append(value)
                        continue
                    if line or not data_lines:
                        continue
                    payload = "\n".join(data_lines)
                    data_lines.clear()
                    event_bytes = 0
                    if payload == "[DONE]":
                        saw_terminal = True
                        break
                    delta = _parse_sse_delta(payload)
                    if delta.finish_reason is not None:
                        saw_terminal = True
                    yield delta
                if data_lines:
                    payload = "\n".join(data_lines)
                    if payload == "[DONE]":
                        saw_terminal = True
                    else:
                        delta = _parse_sse_delta(payload)
                        if delta.finish_reason is not None:
                            saw_terminal = True
                        yield delta
        except httpx.TimeoutException as error:
            raise ProviderTransportError(f"Model API stream timed out: {error}") from error
        except httpx.TransportError as error:
            raise ProviderTransportError(f"Model API stream failed: {error}") from error
        if not saw_terminal:
            raise ProviderProtocolError(
                "Provider stream ended before a finish reason or [DONE] marker."
            )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise ProviderTransportError("Model provider is closed.")
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
                transport=self._transport,
            )
        return self._client

    def _request_body(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [_message_to_wire(message) for message in messages],
        }
        if config.temperature is not None:
            body["temperature"] = config.temperature
        if config.max_tokens is not None:
            body["max_tokens"] = config.max_tokens
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
        body.update(config.extra)
        return body

    def _headers(self, *, stream: bool) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("An API key is required for OpenAICompatibleProvider.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        status_code = response.status_code
        retryable = status_code in {408, 429} or 500 <= status_code <= 599
        detail = response.text[:2000]
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        raise ProviderHTTPError(
            status_code,
            f"Model API returned HTTP {status_code}: {detail}",
            retryable=retryable,
            retry_after_seconds=retry_after,
        )


def _parse_complete_response(payload: Any) -> ModelResponse:
    if not isinstance(payload, dict):
        raise ProviderProtocolError("Provider response root must be a JSON object.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderProtocolError("Provider response must contain at least one choice.")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderProtocolError("Provider choice must contain a message object.")
    tool_calls = [_parse_complete_tool_call(item) for item in message.get("tool_calls") or []]
    usage_value = payload.get("usage", {})
    usage = {} if usage_value is None else usage_value
    if not isinstance(usage, dict):
        raise ProviderProtocolError("Provider usage must be an object.")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ProviderProtocolError("Provider message content must be a string or null.")
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ProviderProtocolError("Provider finish_reason must be a string or null.")
    return ModelResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=_integer_usage(usage),
        raw_response=payload,
    )


def _parse_complete_tool_call(item: Any) -> ToolCall:
    if not isinstance(item, dict):
        raise ProviderProtocolError("Provider tool call must be an object.")
    function = item.get("function")
    if not isinstance(function, dict):
        raise ProviderProtocolError("Provider tool call must contain a function object.")
    call_id = item.get("id")
    name = function.get("name")
    arguments_value = function.get("arguments", "{}")
    raw_arguments = "{}" if arguments_value is None or arguments_value == "" else arguments_value
    if not isinstance(call_id, str) or not isinstance(name, str):
        raise ProviderProtocolError("Provider tool call id and name must be strings.")
    if not isinstance(raw_arguments, str):
        raise ProviderProtocolError("Provider tool arguments must be a JSON string.")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as error:
        raise ProviderProtocolError("Provider tool arguments contain invalid JSON.") from error
    if not isinstance(arguments, dict):
        raise ProviderProtocolError("Provider tool arguments must decode to an object.")
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _parse_sse_delta(payload: dict[str, Any] | str) -> ModelTokenDelta:
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError as error:
        raise ProviderProtocolError("Provider SSE data contains invalid JSON.") from error
    if not isinstance(data, dict):
        raise ProviderProtocolError("Provider SSE data must be a JSON object.")
    choices_value = data.get("choices", [])
    choices = [] if choices_value is None else choices_value
    if not isinstance(choices, list):
        raise ProviderProtocolError("Provider SSE choices must be a list.")
    choice: dict[str, Any] = {}
    if choices:
        if not isinstance(choices[0], dict):
            raise ProviderProtocolError("Provider SSE choice must be an object.")
        choice = choices[0]
    delta_value = choice.get("delta", {})
    raw_delta = {} if delta_value is None else delta_value
    if not isinstance(raw_delta, dict):
        raise ProviderProtocolError("Provider SSE delta must be an object.")
    tool_calls_value = raw_delta.get("tool_calls", [])
    raw_tool_calls = [] if tool_calls_value is None else tool_calls_value
    if not isinstance(raw_tool_calls, list):
        raise ProviderProtocolError("Provider streamed tool_calls must be a list.")
    tool_call_deltas = [_parse_tool_delta(item) for item in raw_tool_calls]
    content = raw_delta.get("content")
    if content is not None and not isinstance(content, str):
        raise ProviderProtocolError("Provider SSE content must be a string or null.")
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ProviderProtocolError("Provider SSE finish_reason must be a string or null.")
    usage_value = data.get("usage", {})
    usage = {} if usage_value is None else usage_value
    if not isinstance(usage, dict):
        raise ProviderProtocolError("Provider SSE usage must be an object.")
    return ModelTokenDelta(
        content=content,
        tool_call_deltas=tool_call_deltas,
        finish_reason=finish_reason,
        usage=_integer_usage(usage),
        raw_delta=data,
    )


def _parse_tool_delta(item: Any) -> ToolCallDelta:
    if not isinstance(item, dict):
        raise ProviderProtocolError("Provider streamed tool call must be an object.")
    index = item.get("index", 0)
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ProviderProtocolError("Provider streamed tool call index must be non-negative.")
    function_value = item.get("function", {})
    function = {} if function_value is None else function_value
    if not isinstance(function, dict):
        raise ProviderProtocolError("Provider streamed function must be an object.")
    call_id = item.get("id")
    name = function.get("name")
    arguments = function.get("arguments") or ""
    if call_id is not None and not isinstance(call_id, str):
        raise ProviderProtocolError("Provider streamed tool call id must be a string.")
    if name is not None and not isinstance(name, str):
        raise ProviderProtocolError("Provider streamed tool name must be a string.")
    if not isinstance(arguments, str):
        raise ProviderProtocolError("Provider streamed tool arguments must be text.")
    return ToolCallDelta(index=index, id=call_id, name=name, arguments=arguments)


def _response_from_accumulated(
    content: list[str],
    calls: dict[int, ToolCallDelta],
    finish_reason: str | None,
    usage: dict[str, int],
) -> ModelResponse:
    tool_calls: list[ToolCall] = []
    for index in sorted(calls):
        item = calls[index]
        if not item.name:
            raise ProviderProtocolError(f"Streamed tool call {index} has no function name.")
        try:
            arguments = json.loads(item.arguments or "{}")
        except json.JSONDecodeError as error:
            raise ProviderProtocolError(
                f"Invalid streamed tool arguments for index {index}."
            ) from error
        if not isinstance(arguments, dict):
            raise ProviderProtocolError(
                f"Streamed tool arguments for index {index} must be an object."
            )
        tool_calls.append(
            ToolCall(item.id or f"streamed_call_{index}", item.name, arguments)
        )
    return ModelResponse(
        content="".join(content) or None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
    )


def _integer_usage(usage: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
    return result


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def _message_to_wire(message: Message) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        result["content"] = message.content
    if message.name is not None:
        result["name"] = message.name
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    return result


def arithmetic_demo_responder(
    messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
) -> ModelResponse:
    """Simple deterministic demo: ask calculator for arithmetic-looking user input."""
    del config
    last = messages[-1]
    if last.role == "tool":
        return ModelResponse(
            content=f"The result is {last.content}.", finish_reason="stop"
        )
    user_text = last.content or ""
    expression = user_text.removeprefix("calculate").strip()
    calculator_is_available = any(tool.name == "calculator" for tool in tools)
    if calculator_is_available and expression and all(
        character in "0123456789+-*/(). %" for character in expression
    ):
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    id="demo_calculator",
                    name="calculator",
                    arguments={"expression": expression},
                )
            ],
            finish_reason="tool_calls",
        )
    return ModelResponse(
        content=(
            "Demo provider: please enter a basic arithmetic expression, "
            "such as `19 * 23`."
        ),
        finish_reason="stop",
    )

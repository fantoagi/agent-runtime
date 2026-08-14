from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .domain import Message, ModelConfig, ToolCall, ToolDefinition


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


ModelResponder = Callable[[list[Message], list[ToolDefinition], ModelConfig], ModelResponse | Awaitable[ModelResponse]]


class MockProvider:
    """Deterministic non-streaming provider for tests and local demos."""

    def __init__(self, responder: ModelResponder) -> None:
        self._responder = responder

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> ModelResponse:
        result = self._responder(messages, tools, config)
        if asyncio.iscoroutine(result):
            return await result
        return result


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
    """Chat Completions-compatible provider with complete and SSE streaming modes."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.timeout_seconds = timeout_seconds

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> ModelResponse:
        if not self.api_key:
            raise ValueError("An API key is required for OpenAICompatibleProvider.")
        response = await asyncio.to_thread(self._post, self._request_body(messages, tools, config))
        try:
            choice = response["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("Provider returned an unexpected response format.") from error
        tool_calls = [
            ToolCall(
                id=item["id"],
                name=item["function"]["name"],
                arguments=json.loads(item["function"].get("arguments") or "{}"),
            )
            for item in message.get("tool_calls", [])
        ]
        return ModelResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage=response.get("usage", {}),
            raw_response=response,
        )

    async def stream(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> AsyncIterator[ModelTokenDelta]:
        if not self.api_key:
            raise ValueError("An API key is required for OpenAICompatibleProvider.")
        body = self._request_body(messages, tools, config)
        body["stream"] = True
        body.setdefault("stream_options", {"include_usage": True})
        queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        sentinel = object()
        errors: list[BaseException] = []
        loop = asyncio.get_running_loop()

        def worker() -> None:
            try:
                for payload in self._post_stream(body):
                    loop.call_soon_threadsafe(queue.put_nowait, payload)
            except BaseException as error:  # propagate provider errors to the async consumer
                errors.append(error)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        threading.Thread(target=worker, name="agent-runtime-model-stream", daemon=True).start()
        while True:
            item = await queue.get()
            if item is sentinel:
                if errors:
                    raise errors[0]
                return
            delta = _parse_sse_delta(item)
            if delta is not None:
                yield delta

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

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = self._request(body)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as result:  # noqa: S310 - configurable endpoint.
                return json.loads(result.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model API returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Model API request failed: {error.reason}") from error

    def _post_stream(self, body: dict[str, Any]) -> Iterator[dict[str, Any] | str]:
        request = self._request(body)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as result:  # noqa: S310 - configurable endpoint.
                data_lines: list[str] = []
                for raw_line in result:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        payload = "\n".join(data_lines)
                        data_lines.clear()
                        if payload:
                            yield payload
                if data_lines:
                    yield "\n".join(data_lines)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model API returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Model API request failed: {error.reason}") from error

    def _request(self, body: dict[str, Any]) -> Request:
        return Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if body.get("stream") else "application/json",
            },
            method="POST",
        )


def _parse_sse_delta(payload: dict[str, Any] | str) -> ModelTokenDelta | None:
    if payload == "[DONE]":
        return None
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        return None
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    raw_delta = choice.get("delta") or {}
    tool_call_deltas = [
        ToolCallDelta(
            index=item.get("index", 0),
            id=item.get("id"),
            name=(item.get("function") or {}).get("name"),
            arguments=(item.get("function") or {}).get("arguments") or "",
        )
        for item in raw_delta.get("tool_calls", [])
    ]
    content = raw_delta.get("content")
    finish_reason = choice.get("finish_reason")
    usage = data.get("usage") or {}
    if content is None and not tool_call_deltas and finish_reason is None and not usage:
        return None
    return ModelTokenDelta(
        content=content,
        tool_call_deltas=tool_call_deltas,
        finish_reason=finish_reason,
        usage=usage,
        raw_delta=data,
    )


def _response_from_accumulated(
    content: list[str],
    calls: dict[int, ToolCallDelta],
    finish_reason: str | None,
    usage: dict[str, int],
) -> ModelResponse:
    tool_calls: list[ToolCall] = []
    for index in sorted(calls):
        item = calls[index]
        try:
            arguments = json.loads(item.arguments or "{}")
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid streamed tool arguments for index {index}.") from error
        tool_calls.append(ToolCall(item.id or f"streamed_call_{index}", item.name or "", arguments))
    return ModelResponse(
        content="".join(content) or None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
    )


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
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return result


def arithmetic_demo_responder(
    messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
) -> ModelResponse:
    """Simple deterministic demo: ask calculator for arithmetic-looking user input."""
    last = messages[-1]
    if last.role == "tool":
        return ModelResponse(content=f"The result is {last.content}.", finish_reason="stop")
    user_text = last.content or ""
    expression = user_text.removeprefix("calculate").strip()
    calculator_is_available = any(tool.name == "calculator" for tool in tools)
    if calculator_is_available and expression and all(c in "0123456789+-*/(). %" for c in expression):
        return ModelResponse(
            tool_calls=[ToolCall(id="demo_calculator", name="calculator", arguments={"expression": expression})],
            finish_reason="tool_calls",
        )
    return ModelResponse(content="Demo provider: please enter a basic arithmetic expression, such as `19 * 23`.", finish_reason="stop")

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
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


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        config: ModelConfig,
    ) -> ModelResponse: ...


ModelResponder = Callable[[list[Message], list[ToolDefinition], ModelConfig], ModelResponse | Awaitable[ModelResponse]]


class MockProvider:
    """Deterministic provider for tests and local demos."""

    def __init__(self, responder: ModelResponder) -> None:
        self._responder = responder

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition], config: ModelConfig
    ) -> ModelResponse:
        result = self._responder(messages, tools, config)
        if asyncio.iscoroutine(result):
            return await result
        return result


class OpenAICompatibleProvider:
    """Minimal Chat Completions-compatible provider using only the standard library."""

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
        response = await asyncio.to_thread(self._post, body)
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

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as result:  # noqa: S310 - configurable provider endpoint.
                return json.loads(result.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model API returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Model API request failed: {error.reason}") from error


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

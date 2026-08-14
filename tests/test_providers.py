from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runtime.domain import Message, ModelConfig, ToolDefinition
from agent_runtime.providers import (
    ModelTokenDelta,
    MockStreamingProvider,
    OpenAICompatibleProvider,
    ToolCallDelta,
)


def test_mock_streaming_provider_preserves_text_and_tool_calls() -> None:
    provider = MockStreamingProvider(
        [
            ModelTokenDelta(content="?"),
            ModelTokenDelta(content="?"),
            ModelTokenDelta(
                tool_call_deltas=[
                    ToolCallDelta(0, id="call_1", name="calculator", arguments='{"expression":"'),
                    ToolCallDelta(0, arguments="2 + 2"),
                    ToolCallDelta(0, arguments='"}'),
                ],
                finish_reason="tool_calls",
            ),
        ]
    )

    response = asyncio.run(
        provider.complete(
            [Message(role="user", content="calculate")],
            [],
            ModelConfig(provider="mock", model="test"),
        )
    )

    assert response.content == "??"
    assert response.tool_calls[0].name == "calculator"
    assert response.tool_calls[0].arguments == {"expression": "2 + 2"}
    assert response.finish_reason == "tool_calls"


def test_openai_sse_parser_handles_content_finish_and_usage() -> None:
    provider = OpenAICompatibleProvider(api_key="test")
    payloads = [
        {"choices": [{"delta": {"content": "?"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "?"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}},
        "[DONE]",
    ]

    async def consume():
        with patch.object(provider, "_post_stream", return_value=iter(payloads)):
            return [
                delta
                async for delta in provider.stream(
                    [Message(role="user", content="hi")],
                    [],
                    ModelConfig(provider="openai", model="test"),
                )
            ]

    deltas = asyncio.run(consume())
    assert [item.content for item in deltas[:2]] == ["?", "?"]
    assert deltas[2].finish_reason == "stop"
    assert deltas[3].usage["total_tokens"] == 4


def test_openai_stream_request_sets_stream_options() -> None:
    provider = OpenAICompatibleProvider(api_key="test")
    body = provider._request_body([], [], ModelConfig(provider="openai", model="test"))
    body["stream"] = True
    body.setdefault("stream_options", {"include_usage": True})
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}

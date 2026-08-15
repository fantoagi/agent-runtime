from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_runtime.domain import (
    Message,
    ModelConfig,
    ProviderHTTPError,
    ProviderProtocolError,
    ToolDefinition,
)
from agent_runtime.providers import (
    MockStreamingProvider,
    ModelTokenDelta,
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
                    ToolCallDelta(
                        0,
                        id="call_1",
                        name="calculator",
                        arguments='{"expression":"',
                    ),
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
    stream = "".join(
        [
            'data: {"choices":[{"delta":{"content":"?"},"finish_reason":null}]}\n\n',
            'data: {"choices":[{"delta":{"content":"?"},"finish_reason":null}]}\n\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":2,"total_tokens":4}}\n\n',
            "data: [DONE]\n\n",
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    async def consume() -> list[ModelTokenDelta]:
        provider = OpenAICompatibleProvider(
            api_key="test", transport=httpx.MockTransport(handler)
        )
        try:
            return [
                delta
                async for delta in provider.stream(
                    [Message(role="user", content="hi")],
                    [],
                    ModelConfig(provider="openai", model="test"),
                )
            ]
        finally:
            await provider.aclose()

    deltas = asyncio.run(consume())
    assert [item.content for item in deltas[:2]] == ["?", "?"]
    assert deltas[2].finish_reason == "stop"
    assert deltas[3].usage["total_tokens"] == 4


def test_openai_complete_validates_response_and_tool_arguments() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "calculator",
                                        "arguments": '{"expression":"2+2"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"total_tokens": 3},
            },
        )

    async def invoke():
        provider = OpenAICompatibleProvider(
            api_key="test", transport=httpx.MockTransport(handler)
        )
        try:
            return await provider.complete(
                [Message(role="user", content="2+2")],
                [
                    ToolDefinition(
                        name="calculator",
                        description="calculate",
                        input_schema={"type": "object"},
                    )
                ],
                ModelConfig(provider="openai", model="test"),
            )
        finally:
            await provider.aclose()

    response = asyncio.run(invoke())
    assert response.tool_calls[0].arguments == {"expression": "2+2"}


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(401, False), (403, False), (429, True), (500, True)],
)
def test_openai_http_errors_have_stable_retry_semantics(
    status_code: int, retryable: bool
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            text="failure",
            headers={"Retry-After": "1"} if status_code == 429 else {},
        )

    async def invoke() -> None:
        provider = OpenAICompatibleProvider(
            api_key="test", transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(ProviderHTTPError) as captured:
                await provider.complete([], [], ModelConfig(model="test"))
            assert captured.value.retryable is retryable
            if status_code == 429:
                assert captured.value.retry_after_seconds == 1
        finally:
            await provider.aclose()

    asyncio.run(invoke())


def test_openai_stream_rejects_truncated_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        )

    async def consume() -> None:
        provider = OpenAICompatibleProvider(
            api_key="test", transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(ProviderProtocolError, match="ended before"):
                async for _ in provider.stream([], [], ModelConfig(model="test")):
                    pass
        finally:
            await provider.aclose()

    asyncio.run(consume())


def test_openai_stream_request_sets_stream_options() -> None:
    provider = OpenAICompatibleProvider(api_key="test")
    body = provider._request_body([], [], ModelConfig(provider="openai", model="test"))
    body["stream"] = True
    body.setdefault("stream_options", {"include_usage": True})
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}

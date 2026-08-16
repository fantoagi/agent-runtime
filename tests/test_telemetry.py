from __future__ import annotations

import json
import logging
from io import StringIO

from agent_runtime.telemetry import configure_structured_logging


def test_structured_logging_redacts_secrets_and_bounds_values() -> None:
    stream = StringIO()
    logger = configure_structured_logging(stream=stream, level=logging.INFO)
    logger.info(
        "provider.request",
        extra={
            "runtime_event": "provider.request",
            "runtime_context": {
                "run_id": "run_123",
                "api_key": "must-not-leak",
                "nested": {"authorization": "Bearer secret"},
                "large": "x" * 3000,
                "note": "failed with Bearer abc.def-123 and sk-secretvalue123",
            },
        },
    )

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "provider.request"
    assert payload["context"]["run_id"] == "run_123"
    assert payload["context"]["api_key"] == "[REDACTED]"
    assert payload["context"]["nested"]["authorization"] == "[REDACTED]"
    assert payload["context"]["large"].endswith("...[TRUNCATED]")
    assert payload["context"]["note"] == (
        "failed with [REDACTED] and [REDACTED]"
    )
    assert "must-not-leak" not in stream.getvalue()
    logger.handlers.clear()

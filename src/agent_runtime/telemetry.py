from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TextIO

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|password|secret|token)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(?:\bBearer\s+[A-Za-z0-9._~+/=-]+|\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,})"
)
_MAX_STRING_LENGTH = 2000
_MAX_DEPTH = 6


def sanitize_log_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    """Make structured log values bounded, JSON-safe, and secret-aware."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if depth >= _MAX_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        sanitized = _SENSITIVE_VALUE.sub("[REDACTED]", value)
        if len(sanitized) <= _MAX_STRING_LENGTH:
            return sanitized
        return sanitized[:_MAX_STRING_LENGTH] + "...[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_log_value(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [sanitize_log_value(item, depth=depth + 1) for item in value]
    return sanitize_log_value(str(value), depth=depth + 1)


class StructuredLogFormatter(logging.Formatter):
    """Render Agent Runtime operational logs as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "runtime_event", None)
        if event is not None:
            payload["event"] = str(event)
        context = getattr(record, "runtime_context", None)
        if isinstance(context, Mapping):
            payload["context"] = sanitize_log_value(context)
        if record.exc_info:
            payload["exception"] = sanitize_log_value(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_structured_logging(
    *,
    level: int | str = logging.INFO,
    stream: TextIO | None = None,
    logger_name: str = "agent_runtime",
    file_path: str | Path | None = None,
    max_bytes: int = 20 * 1024 * 1024,
    backup_count: int = 5,
    include_stream: bool = True,
) -> logging.Logger:
    """Configure bounded JSON logging without changing the root logger."""
    if max_bytes < 1 or backup_count < 1:
        raise ValueError("max_bytes and backup_count must be positive.")
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()
    formatter = StructuredLogFormatter()
    if include_stream:
        stream_handler = logging.StreamHandler(stream or sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    if file_path is not None:
        resolved = Path(file_path).expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            resolved,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    if not logger.handlers:
        raise ValueError("At least one structured log destination is required.")
    logger.setLevel(level)
    logger.propagate = False
    return logger

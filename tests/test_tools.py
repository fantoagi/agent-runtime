from __future__ import annotations

import pytest

from agent_runtime.domain import ToolDefinition, ToolValidationError
from agent_runtime.tools import ToolContext, ToolRegistry, confined_path, validate_input


def test_validator_requires_declared_fields() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    validate_input({"value": 3}, schema)
    with pytest.raises(ToolValidationError):
        validate_input({"value": "3"}, schema)
    with pytest.raises(
        ToolValidationError,
        match=r"Unsupported tool arguments: other\. Allowed arguments: value\.",
    ):
        validate_input({"value": 3, "other": 4}, schema)


def test_confined_path_rejects_parent_escape(workspace) -> None:
    with pytest.raises(Exception, match="escapes"):
        confined_path(workspace, "../outside.txt")


@pytest.mark.asyncio
async def test_registry_executes_async_tool(workspace) -> None:
    registry = ToolRegistry()

    async def echo(arguments, context):
        return arguments["value"]

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        ),
        echo,
    )
    result = await registry.invoke("echo", {"value": "ok"}, ToolContext("r", 1, workspace, {}))
    assert result.content == "ok"

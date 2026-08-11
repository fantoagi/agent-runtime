from __future__ import annotations

from pathlib import Path

from .domain import AgentDefinition, ModelConfig, ToolDefinition
from .providers import MockProvider, arithmetic_demo_responder
from .runtime import Runtime, RuntimeConfig
from .tools import ToolRegistry, register_builtin_tools


def create_local_runtime(workspace: str | Path, state_dir: str | Path | None = None) -> Runtime:
    """Create the local demo runtime with SQLite state and safe built-in tools."""
    workspace_path = Path(workspace).resolve()
    runtime_dir = Path(state_dir or workspace_path / ".agent-runtime").resolve()
    tools = ToolRegistry()
    register_builtin_tools(tools)
    return Runtime(
        RuntimeConfig(
            workspace_path=workspace_path,
            database_path=runtime_dir / "runtime.sqlite3",
            artifact_path=runtime_dir / "artifacts",
        ),
        provider=MockProvider(arithmetic_demo_responder),
        tools=tools,
    )


def demo_agent() -> AgentDefinition:
    return AgentDefinition(
        name="demo",
        system_prompt="You are a concise math assistant. Use calculator for arithmetic.",
        tools=[
            ToolDefinition(
                name="calculator",
                description="Evaluate a basic arithmetic expression containing only numbers and arithmetic operators.",
                input_schema={
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                    "additionalProperties": False,
                },
            )
        ],
        model=ModelConfig(provider="mock", model="arithmetic-demo"),
    )

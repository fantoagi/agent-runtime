from __future__ import annotations

from pathlib import Path

from .domain import AgentDefinition, Message, ModelConfig, ToolDefinition
from .orchestration import SequentialWorkflow
from .providers import MockProvider, ModelResponse, arithmetic_demo_responder
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

def create_multi_agent_demo_runtime(
    workspace: str | Path, state_dir: str | Path | None = None
) -> Runtime:
    """Create a deterministic Planner -> Worker -> Reviewer learning runtime."""
    workspace_path = Path(workspace).resolve()
    runtime_dir = Path(state_dir or workspace_path / ".agent-runtime").resolve()

    def responder(
        messages: list[Message],
        tools: list[ToolDefinition],
        config: ModelConfig,
    ) -> ModelResponse:
        del tools, config
        role = messages[0].content or "agent"
        value = messages[-1].content or ""
        labels = {
            "planner": "PLAN",
            "worker": "DRAFT",
            "reviewer": "REVIEWED",
        }
        return ModelResponse(content=f"{labels.get(role, role.upper())}: {value}")

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace_path,
            database_path=runtime_dir / "runtime.sqlite3",
            artifact_path=runtime_dir / "artifacts",
        ),
        provider=MockProvider(responder),
        tools=ToolRegistry(),
    )
    for name in ("planner", "worker", "reviewer"):
        runtime.register_agent(
            AgentDefinition(
                name=name,
                system_prompt=name,
                tools=[],
                model=ModelConfig(provider="mock", model="multi-agent-demo"),
            )
        )
    return runtime


def multi_agent_demo_workflow() -> SequentialWorkflow:
    return SequentialWorkflow("planner-worker-reviewer", ["planner", "worker", "reviewer"])

def memory_demo_agent() -> AgentDefinition:
    return AgentDefinition(
        name="memory-demo",
        system_prompt="Answer using relevant scoped memory when it is available.",
        tools=[],
        model=ModelConfig(provider="mock", model="memory-demo"),
    )


def create_memory_demo_runtime(
    workspace: str | Path, state_dir: str | Path | None = None
) -> Runtime:
    """Create a deterministic runtime that exposes injected session memory."""
    workspace_path = Path(workspace).resolve()
    runtime_dir = Path(state_dir or workspace_path / ".agent-runtime").resolve()

    def responder(
        messages: list[Message],
        tools: list[ToolDefinition],
        config: ModelConfig,
    ) -> ModelResponse:
        del tools, config
        memory = next(
            (
                message.content
                for message in messages
                if message.role == "system" and message.name == "memory"
            ),
            None,
        )
        if memory:
            remembered = memory.splitlines()[-1].split(") ", 1)[-1]
            return ModelResponse(content=f"MEMORY USED: {remembered}")
        return ModelResponse(content="NO RELEVANT MEMORY")

    runtime = Runtime(
        RuntimeConfig(
            workspace_path=workspace_path,
            database_path=runtime_dir / "runtime.sqlite3",
            artifact_path=runtime_dir / "artifacts",
        ),
        provider=MockProvider(responder),
        tools=ToolRegistry(),
    )
    runtime.register_agent(memory_demo_agent())
    return runtime

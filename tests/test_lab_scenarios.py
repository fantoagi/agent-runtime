from __future__ import annotations

from agent_runtime.cli import build_parser
from agent_runtime.lab.scenarios import default_scenarios


def test_default_learning_scenarios_are_ordered_and_traceable() -> None:
    scenarios = default_scenarios().list()
    assert [scenario.id for scenario in scenarios] == [
        "plain-text",
        "tool-calling",
        "token-streaming",
        "human-approval",
        "sandbox-process",
        "multi-agent-sequential",
        "multi-agent-parallel",
        "session-memory",
        "context-compaction",
        "large-tool-artifact",
    ]
    assert all(scenario.expected_events for scenario in scenarios)
    assert all(scenario.learning_points for scenario in scenarios)
    assert next(item for item in scenarios if item.id == "human-approval").requires_human_action
    assert next(item for item in scenarios if item.id == "sandbox-process").requires_human_action
    assert next(item for item in scenarios if item.id == "multi-agent-sequential").minimum_children == 3
    assert next(item for item in scenarios if item.id == "session-memory").minimum_memories == 2
    assert next(item for item in scenarios if item.id == "context-compaction").minimum_context_compactions == 1
    assert next(item for item in scenarios if item.id == "large-tool-artifact").minimum_artifacts == 1


def test_lab_cli_command_has_beginner_friendly_defaults() -> None:
    arguments = build_parser().parse_args(["lab", "--no-browser"])
    assert arguments.command == "lab"
    assert arguments.host == "127.0.0.1"
    assert arguments.port == 8000
    assert arguments.no_browser is True

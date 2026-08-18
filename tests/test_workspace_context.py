from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.local_config import (
    LocalConfigError,
    load_local_settings,
    write_default_local_config,
)
from agent_runtime.local_runtime import create_configured_local_runtime, local_runtime_status
from agent_runtime.workspace_context import (
    WorkspaceContextError,
    build_local_agent_prompt,
    load_workspace_instructions,
)


def test_workspace_instructions_are_ordered_bounded_and_hashed(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("alpha rules", encoding="utf-8")
    (workspace / "CLAUDE.md").write_text("beta rules", encoding="utf-8")

    bundle = load_workspace_instructions(
        workspace,
        configured_files=("AGENTS.md", "CLAUDE.md"),
        max_chars=15,
    )

    assert [item.path for item in bundle.instructions] == ["AGENTS.md", "CLAUDE.md"]
    assert bundle.instructions[0].content == "alpha rules"
    assert bundle.instructions[0].truncated is False
    assert bundle.instructions[1].content == "beta"
    assert bundle.instructions[1].truncated is True
    assert bundle.total_characters == 15
    assert len(bundle.instructions[0].sha256) == 64
    assert bundle.public_dict()["loaded"][0]["path"] == "AGENTS.md"
    assert "content" not in bundle.public_dict()["loaded"][0]


def test_workspace_instruction_invalid_utf8_and_paths_are_safe(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_bytes(b"\xff\xfe")
    bundle = load_workspace_instructions(workspace)
    assert bundle.instructions == ()
    assert bundle.skipped[0].reason == "invalid_utf8"

    with pytest.raises(WorkspaceContextError, match="relative path"):
        load_workspace_instructions(workspace, configured_files=("../outside.md",))


def test_local_agent_prompt_contains_protocol_and_project_rules(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("Always run focused tests.", encoding="utf-8")
    bundle = load_workspace_instructions(workspace)
    prompt = build_local_agent_prompt("Base assistant prompt.", bundle)

    assert prompt.startswith("Base assistant prompt.")
    assert "Local coding runtime protocol" in prompt
    assert "Never claim a file changed" in prompt
    assert "continue without asking the user to repeat it" in prompt
    assert "narrow the path/pattern" in prompt
    assert "Read Runtime Tool Result Artifacts only with read_artifact" in prompt
    assert "Never use run_process" in prompt
    assert "### AGENTS.md" in prompt
    assert "Always run focused tests." in prompt


def test_configured_runtime_captures_workspace_instructions(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("Use pytest for verification.", encoding="utf-8")
    config_path = write_default_local_config(workspace / "agent-runtime.toml", workspace=workspace)
    settings = load_local_settings(config_path)
    runtime = create_configured_local_runtime(settings)
    try:
        agent = next(item for item in runtime.list_agents() if item.name == settings.agent_name)
        assert "Use pytest for verification." in agent.system_prompt
        status = local_runtime_status(settings)
    finally:
        import asyncio

        asyncio.run(runtime.shutdown())

    loaded = status["workspace_context"]["loaded"]
    assert loaded[0]["path"] == "AGENTS.md"
    assert "content" not in loaded[0]


def test_workspace_instruction_config_can_disable_and_reject_escape(workspace: Path) -> None:
    config_path = write_default_local_config(workspace / "agent-runtime.toml", workspace=workspace)
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace("instructions_enabled = true", "instructions_enabled = false"),
        encoding="utf-8",
    )
    settings = load_local_settings(config_path)
    assert settings.workspace_instructions_enabled is False
    assert settings.workspace_instruction_files == ("AGENTS.md", "CLAUDE.md")
    assert settings.public_dict()["workspace_context"]["instructions_enabled"] is False

    config_path.write_text(
        text.replace(
            'instruction_files = ["AGENTS.md", "CLAUDE.md"]',
            'instruction_files = ["../outside.md"]',
        ),
        encoding="utf-8",
    )
    with pytest.raises(LocalConfigError, match="relative file paths"):
        load_local_settings(config_path)

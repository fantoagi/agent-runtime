from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    arguments: tuple[str, ...] = ()


_COMMANDS = (
    "/help",
    "/new",
    "/continue",
    "/sessions",
    "/resume",
    "/status",
    "/model",
    "/display",
    "/tools",
    "/workspace",
    "/diff",
    "/events",
    "/cancel",
    "/clear",
    "/quit",
    "/exit",
)

HELP_TEXT = """\
Available commands:
  /help                 Show this help
  /new                  Start a new conversation session
  /continue             Switch to the most recently used interactive session
  /sessions             List recent interactive sessions
  /resume <session_id>  Switch to a persisted session
  /status               Show Runtime, workspace, session, and current Run
  /model                Show the configured provider and model
  /display [mode]       Show or set compact/verbose event rendering
  /tools                List tools available to the current Agent
  /workspace            Show workspace and coding tool availability
  /diff                 Show recent file changes made through tools
  /events               Show events for the most recent Run
  /cancel               Cancel the active Run, if any
  /clear                Clear the terminal display
  /quit, /exit          Save state and exit

Keyboard:
  Ctrl+C                Cancel the active Run or clear the current prompt
  Ctrl+D                Exit the interactive shell
"""


def command_names() -> tuple[str, ...]:
    return _COMMANDS


def parse_slash_command(value: str) -> SlashCommand | None:
    stripped = value.strip()
    if not stripped.startswith("/"):
        return None
    try:
        parts = shlex.split(stripped)
    except ValueError as error:
        raise ValueError(f"Invalid slash command: {error}") from error
    if not parts:
        return None
    return SlashCommand(parts[0].lower(), tuple(parts[1:]))

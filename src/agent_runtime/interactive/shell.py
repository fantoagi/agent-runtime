from __future__ import annotations

import asyncio
import signal
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.table import Table

from ..domain import AgentDefinition, AgentRun, RunStatus, Session, ToolExecutionStatus
from ..local_config import LocalRuntimeSettings
from ..runtime import Runtime
from ..version import __version__
from ..workspace_context import WorkspaceInstructionBundle, load_workspace_instructions
from .commands import HELP_TEXT, SlashCommand, command_names, parse_slash_command
from .renderer import DisplayMode, EventRenderer


class PromptReader(Protocol):
    async def prompt(self, message: str) -> str:
        """Read one interactive value or raise EOFError/KeyboardInterrupt."""


class PromptToolkitReader:
    def __init__(self, history_path: str | Path) -> None:
        path = Path(history_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=WordCompleter(list(command_names()), sentence=True),
            complete_while_typing=False,
        )

    async def prompt(self, message: str) -> str:
        return await self._session.prompt_async(message)


@dataclass(frozen=True, slots=True)
class ChatOptions:
    initial_prompt: str | None = None
    print_only: bool = False
    continue_session: bool = False
    resume_session_id: str | None = None
    display_mode: DisplayMode = DisplayMode.COMPACT


class InteractiveShell:
    """Terminal-native conversation adapter over the durable Runtime API."""

    def __init__(
        self,
        runtime: Runtime,
        settings: LocalRuntimeSettings,
        *,
        options: ChatOptions | None = None,
        console: Console | None = None,
        prompt_reader: PromptReader | None = None,
    ) -> None:
        self.runtime = runtime
        self.settings = settings
        self.options = options or ChatOptions()
        self.console = console or Console()
        self.prompt_reader = prompt_reader
        self.renderer = EventRenderer(
            self.console,
            quiet=self.options.print_only,
            mode=self.options.display_mode,
        )
        self.session: Session | None = None
        self.current_run_id: str | None = None
        self.last_run_id: str | None = None
        self._interrupt_requested = False

    async def run(self) -> int:
        self.session = self._initial_session()
        if not self.options.print_only:
            self._print_banner()
        if self.options.initial_prompt:
            run = await self._run_turn(self.options.initial_prompt)
            if self.options.print_only:
                return 0 if run.status is RunStatus.COMPLETED else 1
        elif self.options.print_only:
            self.console.print("Print mode requires an initial prompt.")
            return 2

        while True:
            try:
                value = await self._reader().prompt("You > ")
            except EOFError:
                if not self.options.print_only:
                    self.console.print("\n[dim]Session saved. Goodbye.[/dim]")
                return 0
            except KeyboardInterrupt:
                self.console.print("[dim]Input cleared. Press Ctrl+D or use /quit to exit.[/dim]")
                continue

            stripped = value.strip()
            if not stripped:
                continue
            try:
                command = parse_slash_command(stripped)
            except ValueError as error:
                self.console.print(f"[red]{error}[/red]")
                continue
            if command is not None:
                if not await self._handle_command(command):
                    return 0
                continue
            await self._run_turn(stripped)

    def _initial_session(self) -> Session:
        if self.options.resume_session_id:
            return self.runtime.store.get_session(self.options.resume_session_id)
        if self.options.continue_session:
            latest = self._latest_interactive_session()
            if latest is not None:
                return latest
        return self._create_session()

    def _create_session(self) -> Session:
        return self.runtime.create_session(
            {
                "kind": "interactive-cli",
                "workspace": str(self.settings.workspace),
                "agent_name": self.settings.agent_name,
            }
        )

    def _latest_interactive_session(self) -> Session | None:
        sessions = [
            session
            for session in self.runtime.store.list_sessions(limit=100)
            if session.metadata.get("kind") == "interactive-cli"
            and session.metadata.get("workspace") == str(self.settings.workspace)
        ]
        return max(sessions, key=lambda item: item.updated_at, default=None)

    async def _run_turn(self, value: str) -> AgentRun:
        if self.session is None:
            raise RuntimeError("Interactive session has not been initialized.")
        self.renderer.begin_turn()
        submission = self.runtime.submit(
            self.settings.agent_name,
            value,
            {
                "adapter": "interactive-cli",
                "include_session_history": True,
                "session_history_limit": 20,
            },
            session_id=self.session.id,
        )
        run_id = submission.run.id
        self.current_run_id = run_id
        self.last_run_id = run_id
        after_sequence = 0
        resume_task: asyncio.Task[AgentRun] | None = None
        self._interrupt_requested = False
        try:
            with self._capture_run_interrupts():
                while True:
                    async for event in self.runtime.stream(
                        run_id,
                        after_sequence=after_sequence,
                        stop_when_inactive=True,
                    ):
                        after_sequence = event.sequence
                        self.renderer.render(event)
                    run = self.runtime.store.get_run(run_id)
                    if run.status is not RunStatus.WAITING_FOR_APPROVAL:
                        break
                    approval = self.runtime.store.pending_approval(run_id)
                    if approval is None:
                        if resume_task is not None and not resume_task.done():
                            await self._wait_for_resume_activation(run_id, resume_task)
                            continue
                        break
                    approved = await self._prompt_for_approval()
                    self.runtime.resolve_approval(
                        approval.id,
                        approved,
                        "approved from interactive CLI"
                        if approved
                        else "rejected from interactive CLI",
                    )
                    resume_task = asyncio.create_task(self.runtime.resume(run_id))
                    await self._wait_for_resume_activation(run_id, resume_task)
                if resume_task is not None:
                    await resume_task
            run = self.runtime.store.get_run(run_id)
            self.renderer.finish(run)
            return run
        finally:
            self.current_run_id = None

    async def _wait_for_resume_activation(
        self, run_id: str, resume_task: asyncio.Task[AgentRun]
    ) -> None:
        """Wait until approval resume leaves the transient waiting state."""
        while (
            self.runtime.store.get_run(run_id).status is RunStatus.WAITING_FOR_APPROVAL
            and not resume_task.done()
        ):
            await asyncio.sleep(self.runtime.config.event_poll_interval_seconds)
        if resume_task.done():
            await resume_task

    async def _prompt_for_approval(self) -> bool:
        if self.prompt_reader is None and self.options.print_only and not sys.stdin.isatty():
            return False
        while True:
            try:
                answer = (await self._reader().prompt("Approve this action? [y/N] ")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if answer in {"y", "yes"}:
                return True
            if answer in {"", "n", "no"}:
                return False
            self.console.print("Please enter y or n.")

    def _reader(self) -> PromptReader:
        if self.prompt_reader is None:
            self.prompt_reader = PromptToolkitReader(self.settings.state_dir / "cli-history")
        return self.prompt_reader

    @contextmanager
    def _capture_run_interrupts(self) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        loop = asyncio.get_running_loop()
        previous: Any = signal.getsignal(signal.SIGINT)

        def handle_interrupt(signum: int, frame: FrameType | None) -> None:
            del signum, frame
            loop.call_soon_threadsafe(self._cancel_active_run)

        signal.signal(signal.SIGINT, handle_interrupt)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous)

    def _cancel_active_run(self) -> None:
        run_id = self.current_run_id
        if run_id is None or self._interrupt_requested:
            return
        self._interrupt_requested = True
        try:
            self.runtime.cancel(run_id)
            self.console.print("\n[yellow]Cancelling the active Run...[/yellow]")
        except (KeyError, ValueError):
            return

    async def _handle_command(self, command: SlashCommand) -> bool:
        name = command.name
        if name in {"/quit", "/exit"}:
            self.console.print("[dim]Session saved. Goodbye.[/dim]")
            return False
        if name == "/help":
            self.console.print(HELP_TEXT)
            return True
        if name == "/new":
            self.session = self._create_session()
            self.console.print(f"[green]New session:[/green] {self.session.id}")
            return True
        if name == "/continue":
            latest = self._latest_interactive_session()
            if latest is None:
                self.console.print("No previous interactive session was found.")
            else:
                self.session = latest
                self.console.print(f"[green]Session:[/green] {latest.id}")
            return True
        if name == "/resume":
            if len(command.arguments) != 1:
                self.console.print("Usage: /resume <session_id>")
                return True
            try:
                self.session = self.runtime.store.get_session(command.arguments[0])
            except KeyError as error:
                self.console.print(f"[red]{error}[/red]")
            else:
                self.console.print(f"[green]Session:[/green] {self.session.id}")
            return True
        if name == "/sessions":
            self._print_sessions()
            return True
        if name == "/status":
            self._print_status()
            return True
        if name == "/model":
            self.console.print(
                f"Provider: [bold]{self.settings.provider}[/bold]\n"
                f"Model: [bold]{self.settings.model}[/bold]"
            )
            return True
        if name == "/display":
            if len(command.arguments) > 1:
                self.console.print("Usage: /display [compact|verbose]")
                return True
            if command.arguments:
                try:
                    mode = DisplayMode(command.arguments[0].lower())
                except ValueError:
                    self.console.print("Usage: /display [compact|verbose]")
                    return True
                self.renderer.set_mode(mode)
            self.console.print(
                f"Display mode: [bold]{self.renderer.mode.value}[/bold]"
            )
            return True
        if name == "/tools":
            agent = self._current_agent()
            table = Table(title=f"Tools for {agent.name}")
            table.add_column("Name")
            table.add_column("Capabilities")
            table.add_column("Approval")
            table.add_column("Side effect")
            for tool in agent.tools:
                table.add_row(
                    tool.name,
                    ", ".join(capability.value for capability in tool.capabilities) or "none",
                    "yes" if tool.requires_approval else "no",
                    "yes" if tool.side_effecting else "no",
                )
            self.console.print(table)
            return True
        if name == "/workspace":
            self._print_workspace()
            return True
        if name == "/diff":
            self._print_recent_changes()
            return True
        if name == "/events":
            self._print_events()
            return True
        if name == "/cancel":
            if self.current_run_id is None:
                self.console.print("No Run is currently active.")
            else:
                self._cancel_active_run()
            return True
        if name == "/clear":
            self.console.clear()
            return True
        self.console.print(f"Unknown command: {name}. Use /help.")
        return True

    def _print_banner(self) -> None:
        self.console.print(
            f"[bold cyan]Agent Runtime Interactive CLI[/bold cyan] [dim]v{__version__}[/dim]"
        )
        self.console.print(f"Workspace: {self.settings.workspace}")
        self.console.print(
            f"Agent: {self.settings.agent_name} · Provider: {self.settings.provider} · "
            f"Model: {self.settings.model} · Display: {self.renderer.mode.value}"
        )
        if self.session is not None:
            self.console.print(f"Session: {self.session.id}")
        coding_tools = [
            tool.name
            for tool in self._current_agent().tools
            if tool.name
            in {
                "list_files",
                "search_text",
                "read_file_lines",
                "read_text_file",
                "replace_text",
                "apply_patch",
                "write_text_file",
                "git_status",
                "git_diff",
                "run_process",
            }
        ]
        if coding_tools:
            self.console.print("Coding tools: " + ", ".join(coding_tools))
        instruction_bundle = self._workspace_instruction_bundle()
        if instruction_bundle.instructions:
            self.console.print(
                "Project instructions: "
                + ", ".join(item.path for item in instruction_bundle.instructions)
            )
        elif instruction_bundle.enabled:
            self.console.print("Project instructions: none found")
        else:
            self.console.print("Project instructions: disabled")
        self.console.print(
            "[dim]Use /help for commands. Ctrl+C cancels a Run; Ctrl+D exits.[/dim]\n"
        )

    def _current_agent(self) -> AgentDefinition:
        return next(
            item for item in self.runtime.list_agents() if item.name == self.settings.agent_name
        )

    def _workspace_instruction_bundle(self) -> WorkspaceInstructionBundle:
        return load_workspace_instructions(
            self.settings.workspace,
            enabled=self.settings.workspace_instructions_enabled,
            configured_files=self.settings.workspace_instruction_files,
            max_chars=self.settings.workspace_instruction_max_chars,
        )

    def _print_workspace(self) -> None:
        agent = self._current_agent()
        coding_names = {
            "list_files",
            "search_text",
            "read_file_lines",
            "read_text_file",
            "replace_text",
            "apply_patch",
            "write_text_file",
            "git_status",
            "git_diff",
            "run_process",
        }
        table = Table(title="Coding Workspace")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("Workspace", str(self.settings.workspace))
        table.add_row("State", str(self.settings.state_dir))
        table.add_row(
            "Coding tools",
            ", ".join(tool.name for tool in agent.tools if tool.name in coding_names) or "none",
        )
        table.add_row(
            "Process execution",
            "enabled (approval required)"
            if any(tool.name == "run_process" for tool in agent.tools)
            else "disabled",
        )
        table.add_row("Write policy", "approval required")
        instruction_bundle = self._workspace_instruction_bundle()
        if instruction_bundle.instructions:
            instruction_summary = ", ".join(
                f"{item.path} ({item.sha256[:12]}{', truncated' if item.truncated else ''})"
                for item in instruction_bundle.instructions
            )
        elif instruction_bundle.enabled:
            instruction_summary = "none found"
        else:
            instruction_summary = "disabled"
        table.add_row("Project instructions", instruction_summary)
        if instruction_bundle.skipped:
            table.add_row(
                "Instruction warnings",
                ", ".join(f"{item.path}: {item.reason}" for item in instruction_bundle.skipped),
            )
        self.console.print(table)

    def _print_recent_changes(self) -> None:
        if self.session is None:
            self.console.print("No interactive session is active.")
            return
        changes: list[tuple[str, str, str, str]] = []
        for run in reversed(self.runtime.session_runs(self.session.id)):
            for execution in reversed(self.runtime.store.tool_executions_for_run(run.id)):
                if execution.tool_call.name not in {
                    "replace_text",
                    "apply_patch",
                    "write_text_file",
                }:
                    continue
                if execution.status is not ToolExecutionStatus.COMPLETED:
                    continue
                data = execution.result_data or {}
                status = str(data.get("status") or execution.status.value)
                if execution.tool_call.name == "apply_patch":
                    files = data.get("files")
                    if not isinstance(files, list):
                        continue
                    for item in files:
                        if not isinstance(item, dict):
                            continue
                        path = str(item.get("path") or "unknown")
                        replacements = item.get("replacements")
                        before = str(item.get("before_sha256") or "")[:12]
                        after = str(item.get("after_sha256") or "")[:12]
                        detail = f"{replacements} replacement(s); {before} -> {after}"
                        changes.append(
                            (run.id, execution.tool_call.name, path, f"{status}; {detail}")
                        )
                        if len(changes) >= 20:
                            break
                    if len(changes) >= 20:
                        break
                    continue
                path = str(
                    data.get("path") or execution.tool_call.arguments.get("path") or "unknown"
                )
                if execution.tool_call.name == "replace_text":
                    replacements = data.get("replacements")
                    before = str(data.get("before_sha256") or "")[:12]
                    after = str(data.get("after_sha256") or "")[:12]
                    detail = f"{replacements} replacement(s); {before} -> {after}"
                else:
                    detail = "file content written"
                changes.append((run.id, execution.tool_call.name, path, f"{status}; {detail}"))
                if len(changes) >= 20:
                    break
            if len(changes) >= 20:
                break
        if not changes:
            self.console.print("No file changes were recorded in this session.")
            return
        table = Table(title="Recent Tool File Changes")
        table.add_column("Run")
        table.add_column("Tool")
        table.add_column("Path")
        table.add_column("Summary")
        for run_id, tool_name, path, summary in changes:
            table.add_row(run_id, tool_name, path, summary)
        self.console.print(table)

    def _print_status(self) -> None:
        table = Table(title="Interactive Runtime Status")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("Workspace", str(self.settings.workspace))
        table.add_row("State", str(self.settings.state_dir))
        table.add_row("Agent", self.settings.agent_name)
        table.add_row("Provider", self.settings.provider)
        table.add_row("Model", self.settings.model)
        table.add_row("Display", self.renderer.mode.value)
        table.add_row("Session", self.session.id if self.session else "none")
        table.add_row("Current Run", self.current_run_id or "none")
        table.add_row("Last Run", self.last_run_id or "none")
        self.console.print(table)

    def _print_sessions(self) -> None:
        sessions = [
            session
            for session in self.runtime.store.list_sessions(limit=20)
            if session.metadata.get("kind") == "interactive-cli"
        ]
        table = Table(title="Interactive Sessions")
        table.add_column("Session ID")
        table.add_column("Updated")
        table.add_column("Runs", justify="right")
        for session in sorted(sessions, key=lambda item: item.updated_at, reverse=True):
            table.add_row(
                session.id,
                session.updated_at.isoformat(timespec="seconds"),
                str(len(self.runtime.session_runs(session.id))),
            )
        self.console.print(table)

    def _print_events(self) -> None:
        if self.last_run_id is None:
            self.console.print("No Run has been submitted in this shell.")
            return
        events = self.runtime.store.events_since(self.last_run_id)
        table = Table(title=f"Events for {self.last_run_id}")
        table.add_column("Seq", justify="right")
        table.add_column("Type")
        table.add_column("Time")
        for event in events:
            table.add_row(
                str(event.sequence),
                event.type,
                event.timestamp.isoformat(timespec="seconds"),
            )
        self.console.print(table)

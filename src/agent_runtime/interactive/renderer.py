from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from ..domain import AgentRun, RuntimeEvent


class EventRenderer:
    """Convert durable Runtime events into a compact, human-oriented terminal transcript."""

    def __init__(self, console: Console, *, quiet: bool = False) -> None:
        self.console = console
        self.quiet = quiet
        self._assistant_open = False
        self._saw_content = False

    @property
    def saw_content(self) -> bool:
        return self._saw_content

    def begin_turn(self) -> None:
        self._assistant_open = False
        self._saw_content = False

    def render(self, event: RuntimeEvent) -> None:
        event_type = event.type
        payload = event.payload
        if event_type == "model.delta":
            content = payload.get("content")
            if isinstance(content, str) and content:
                if not self._assistant_open:
                    if not self.quiet:
                        self.console.print("\n[bold cyan]Assistant[/bold cyan] > ", end="")
                    self._assistant_open = True
                self.console.print(Text(content), end="", soft_wrap=True)
                self._saw_content = True
            return

        if self.quiet:
            return
        if event_type == "run.started":
            self.console.print("[dim]● Runtime started the Run[/dim]")
        elif event_type == "tool.requested":
            self._close_assistant_line()
            tool_name = str(payload.get("tool_name") or "unknown")
            arguments = self._format_arguments(payload.get("arguments"))
            self.console.print(f"[yellow]● Tool[/yellow] [bold]{tool_name}[/bold]")
            if arguments:
                self.console.print(Text(f"  {arguments}"), soft_wrap=True)
        elif event_type == "tool.started":
            tool_name = str(payload.get("tool_name") or "unknown")
            self.console.print(f"[dim]  running {tool_name}[/dim]")
        elif event_type == "tool.completed":
            tool_name = str(payload.get("tool_name") or "unknown")
            content = payload.get("content")
            summary = self._truncate(str(content), 240) if content is not None else ""
            suffix = f": {summary}" if summary else ""
            self.console.print(f"[green]✓ {tool_name} completed[/green]{suffix}")
        elif event_type in {"tool.failed", "tool.cancelled", "tool.outcome_unknown"}:
            tool_name = str(payload.get("tool_name") or "unknown")
            detail = payload.get("error") or payload.get("reason") or event_type
            self.console.print(f"[red]✗ {tool_name}[/red]: {detail}")
        elif event_type == "approval.requested":
            self._close_assistant_line()
            tool_name = str(payload.get("tool_name") or "unknown")
            arguments = self._format_arguments(payload.get("arguments"))
            body = f"Tool: {tool_name}"
            if arguments:
                body += f"\nArguments: {arguments}"
            self.console.print(Panel(body, title="Approval required", border_style="yellow"))
        elif event_type == "completion.verification_requested":
            self._close_assistant_line()
            requirements = payload.get("unmet_requirements")
            if isinstance(requirements, list) and requirements:
                detail = "; ".join(str(item) for item in requirements)
                self.console.print(
                    f"[cyan]↻ Runtime is verifying changes before completion[/cyan]: {detail}"
                )
            else:
                self.console.print(
                    "[cyan]↻ Runtime is verifying changes before completion[/cyan]"
                )
        elif event_type == "completion.evidence":
            self._close_assistant_line()
            status = str(payload.get("status") or "unknown")
            if status == "read_only":
                return
            changed_files = payload.get("changed_files")
            file_count = len(changed_files) if isinstance(changed_files, list) else 0
            diff_state = "diff inspected" if payload.get("diff_inspected") else "diff not inspected"
            validation = payload.get("validation_succeeded")
            if validation is True:
                validation_state = "validation passed"
            elif validation is False:
                validation_state = "validation failed"
            else:
                validation_state = "validation not run"
            style = "green" if status == "verified" else "yellow"
            marker = "✓" if status == "verified" else "!"
            self.console.print(
                f"[{style}]{marker} Completion evidence: {status}[/{style}] · "
                f"{file_count} file(s) · {diff_state} · {validation_state}"
            )
        elif event_type == "run.failed":
            self._close_assistant_line()
            self.console.print(f"[bold red]✗ Run failed:[/bold red] {payload.get('error', '')}")
        elif event_type == "run.cancelled":
            self._close_assistant_line()
            self.console.print("[yellow]⏹ Run cancelled[/yellow]")

    def finish(self, run: AgentRun) -> None:
        self._close_assistant_line()
        if run.status.value == "completed":
            if not self._saw_content and run.result:
                if self.quiet:
                    self.console.print(run.result)
                else:
                    self.console.print("\n[bold cyan]Assistant[/bold cyan] >")
                    self.console.print(Markdown(run.result))
            if not self.quiet:
                self.console.print(
                    f"[dim]Run {run.id} · {run.step_count} step(s) · "
                    f"{run.tool_call_count} tool call(s)[/dim]"
                )
        elif run.status.value == "waiting_for_approval":
            return
        elif run.status.value == "cancelled":
            if self.quiet:
                self.console.print("Run cancelled.")
        elif run.error and self.quiet:
            self.console.print(f"Run failed: {run.error}")

    def _close_assistant_line(self) -> None:
        if self._assistant_open:
            self.console.print()
            self._assistant_open = False

    @staticmethod
    def _format_arguments(value: Any) -> str:
        if value in (None, {}, []):
            return ""
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            rendered = str(value)
        return EventRenderer._truncate(rendered, 360)

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        normalized = value.replace("\r", " ").replace("\n", " ").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 3)] + "..."

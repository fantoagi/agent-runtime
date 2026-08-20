from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from ..completion import looks_like_validation_command
from ..domain import AgentRun, RuntimeEvent


class DisplayMode(StrEnum):
    """Human-facing verbosity for Interactive CLI Runtime events."""

    COMPACT = "compact"
    VERBOSE = "verbose"


class ExecutionPhase(StrEnum):
    """Append-only human projection of the current local coding phase."""

    INSPECTING = "Inspecting workspace"
    EDITING = "Editing workspace"
    VERIFYING = "Verifying changes"
    EXECUTING = "Executing action"


class EventRenderer:
    """Convert durable Runtime events into a readable terminal transcript."""

    _COMPACT_ARGUMENT_LIMIT = 180
    _COMPACT_RESULT_LIMIT = 180
    _APPROVAL_PREVIEW_LIMIT = 1200
    _VERBOSE_PAYLOAD_LIMIT = 4000
    _INSPECTION_TOOLS = frozenset(
        {
            "git_status",
            "list_files",
            "read_artifact",
            "read_file_lines",
            "read_text_file",
            "search_text",
        }
    )
    _EDITING_TOOLS = frozenset({"apply_patch", "replace_text", "write_text_file"})
    _VERIFICATION_TOOLS = frozenset({"git_diff"})

    def __init__(
        self,
        console: Console,
        *,
        quiet: bool = False,
        mode: DisplayMode = DisplayMode.COMPACT,
    ) -> None:
        self.console = console
        self.quiet = quiet
        self.mode = mode
        self._assistant_open = False
        self._assistant_chunks: list[str] = []
        self._current_phase: ExecutionPhase | None = None
        self._tool_names_by_execution: dict[str, str] = {}
        self._failed_tool_names: set[str] = set()
        self._recovered_tool_names: set[str] = set()
        self._saw_content = False

    @property
    def saw_content(self) -> bool:
        return self._saw_content

    def set_mode(self, mode: DisplayMode) -> None:
        self.mode = mode

    def begin_turn(self) -> None:
        self._close_assistant_block()
        self._current_phase = None
        self._tool_names_by_execution.clear()
        self._failed_tool_names.clear()
        self._recovered_tool_names.clear()
        self._saw_content = False

    def render(self, event: RuntimeEvent) -> None:
        event_type = event.type
        payload = event.payload
        if event_type == "model.delta":
            content = payload.get("content")
            if isinstance(content, str) and content:
                self._append_assistant_delta(content)
            return

        if self.quiet:
            return
        if event_type == "run.started":
            if self.mode is DisplayMode.VERBOSE:
                self.console.print("[dim]● Runtime started the Run[/dim]")
        elif event_type == "tool.requested":
            self._close_assistant_block()
            self._remember_tool(payload)
            self._transition_for_tool(payload)
            self._render_tool_requested(payload)
        elif event_type == "tool.started":
            if self.mode is DisplayMode.VERBOSE:
                tool_name = str(payload.get("tool_name") or "unknown")
                self.console.print(
                    Text.assemble(("  ↳ running ", "dim"), (tool_name, "dim bold"))
                )
        elif event_type == "tool.completed":
            self._render_tool_completed(payload)
        elif event_type == "tool.reused":
            self._close_assistant_block()
            self._transition_for_tool(payload)
            self._render_tool_reused(payload)
        elif event_type in {
            "convergence.warning",
            "convergence.finalization_requested",
            "convergence.finalization_repair_requested",
        }:
            self._close_assistant_block()
            self._render_convergence(event_type, payload)
        elif event_type in {"tool.failed", "tool.cancelled", "tool.outcome_unknown"}:
            self._render_tool_failure(event_type, payload)
        elif event_type == "approval.requested":
            self._close_assistant_block()
            self._remember_tool(payload)
            self._transition_for_tool(payload)
            self._render_approval(payload)
        elif event_type == "approval.resolved":
            self._render_approval_resolved(payload)
        elif event_type == "completion.verification_requested":
            self._close_assistant_block()
            self._transition_phase(ExecutionPhase.VERIFYING)
            requirements = payload.get("unmet_requirements")
            if isinstance(requirements, list) and requirements:
                detail = "; ".join(str(item) for item in requirements)
                self.console.print(
                    Text.assemble(
                        ("↻ Runtime is verifying changes before completion", "cyan"),
                        (f": {detail}", "default"),
                    )
                )
            else:
                self.console.print(
                    "[cyan]↻ Runtime is verifying changes before completion[/cyan]"
                )
        elif event_type == "completion.evidence":
            self._close_assistant_block()
            self._render_completion_evidence(payload)
        elif event_type == "run.failed":
            self._close_assistant_block()
            self.console.print(
                Text.assemble(("✗ Run failed: ", "bold red"), str(payload.get("error", "")))
            )
        elif event_type == "run.cancelled":
            self._close_assistant_block()
            self.console.print("[yellow]⏹ Run cancelled[/yellow]")

    def finish(self, run: AgentRun) -> None:
        self._close_assistant_block()
        if run.status.value == "completed":
            if self.quiet:
                if run.result:
                    self.console.print(run.result)
                return
            if not self._saw_content and run.result:
                self.console.print("\n[bold cyan]Assistant[/bold cyan] >")
                self.console.print(Markdown(run.result, code_theme="monokai"))
            duration_seconds = max(
                0.0, (run.updated_at - run.created_at).total_seconds()
            )
            self.console.print(
                f"[dim]Run {run.id} · {run.step_count} step(s) · "
                f"{run.tool_call_count} tool call(s) · {duration_seconds:.1f}s[/dim]"
            )
        elif run.status.value == "waiting_for_approval":
            return
        elif run.status.value == "cancelled":
            if self.quiet:
                self.console.print("Run cancelled.")
        elif run.error and self.quiet:
            self.console.print(f"Run failed: {run.error}")

    def _append_assistant_delta(self, content: str) -> None:
        self._assistant_chunks.append(content)
        self._saw_content = True
        if self.quiet:
            return
        if not self._assistant_open:
            self.console.print("\n[bold cyan]Assistant[/bold cyan] >")
            self._assistant_open = True
        self._flush_stable_assistant_markdown()

    def _assistant_markdown(self, content: str | None = None) -> Markdown:
        rendered = "".join(self._assistant_chunks) if content is None else content
        return Markdown(rendered, code_theme="monokai")

    def _flush_stable_assistant_markdown(self) -> None:
        """Append complete Markdown blocks without rewriting prior terminal rows."""
        buffered = "".join(self._assistant_chunks)
        stable_length = self._stable_markdown_prefix_length(buffered)
        if stable_length <= 0:
            return
        self.console.print(self._assistant_markdown(buffered[:stable_length]))
        remainder = buffered[stable_length:]
        self._assistant_chunks[:] = [remainder] if remainder else []

    @staticmethod
    def _stable_markdown_prefix_length(content: str) -> int:
        """Return the last complete block boundary outside an open code fence."""
        fence_character: str | None = None
        fence_length = 0
        stable_length = 0
        consumed = 0
        for line in content.splitlines(keepends=True):
            consumed += len(line)
            stripped = line.lstrip()
            if fence_character is not None:
                marker_length = len(stripped) - len(stripped.lstrip(fence_character))
                trailing = stripped[marker_length:].strip()
                if marker_length >= fence_length and not trailing:
                    fence_character = None
                    fence_length = 0
                    stable_length = consumed
                continue

            if stripped.startswith(("```", "~~~")):
                fence_character = stripped[0]
                fence_length = len(stripped) - len(stripped.lstrip(fence_character))
                continue
            if not line.strip():
                stable_length = consumed
        return stable_length

    def _close_assistant_block(self) -> None:
        if not self._assistant_open:
            self._assistant_chunks.clear()
            return
        if self._assistant_chunks:
            self.console.print(self._assistant_markdown())
        self._assistant_open = False
        self._assistant_chunks.clear()

    def _remember_tool(self, payload: dict[str, Any]) -> None:
        execution_id = payload.get("tool_execution_id")
        tool_name = payload.get("tool_name")
        if isinstance(execution_id, str) and isinstance(tool_name, str):
            self._tool_names_by_execution[execution_id] = tool_name

    def _transition_for_tool(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name") or "unknown")
        self._transition_phase(
            self._phase_for_tool(tool_name, payload.get("arguments"))
        )

    def _transition_phase(self, phase: ExecutionPhase) -> None:
        if self._current_phase is phase:
            return
        self._current_phase = phase
        self.console.print(Text.assemble(("● ", "cyan"), (phase.value, "cyan bold")))

    @classmethod
    def _phase_for_tool(cls, tool_name: str, arguments: Any) -> ExecutionPhase:
        if tool_name in cls._INSPECTION_TOOLS:
            return ExecutionPhase.INSPECTING
        if tool_name in cls._EDITING_TOOLS:
            return ExecutionPhase.EDITING
        if tool_name in cls._VERIFICATION_TOOLS:
            return ExecutionPhase.VERIFYING
        if tool_name == "run_process" and cls._is_validation_process(arguments):
            return ExecutionPhase.VERIFYING
        return ExecutionPhase.EXECUTING

    @classmethod
    def _is_validation_process(cls, arguments: Any) -> bool:
        del cls
        if not isinstance(arguments, dict):
            return False
        argv = arguments.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(item, str) for item in argv
        ):
            return False
        return looks_like_validation_command(argv)

    def _render_tool_requested(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name") or "unknown")
        arguments = payload.get("arguments")
        if self.mode is DisplayMode.COMPACT:
            if tool_name in self._INSPECTION_TOOLS or tool_name in self._VERIFICATION_TOOLS:
                return
            summary = self._summarize_tool_request(tool_name, arguments)
            line = Text.assemble(("● ", "yellow"), (tool_name, "bold"))
            if summary:
                line.append(f"  {summary}", style="dim")
            self.console.print(line)
            return
        self.console.print(Text.assemble(("● Tool ", "yellow"), (tool_name, "bold")))
        if arguments not in (None, {}, []):
            self.console.print(
                Panel(
                    self._json_renderable(arguments),
                    title="Arguments",
                    border_style="yellow",
                    expand=False,
                )
            )

    def _render_tool_completed(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name") or "unknown")
        if tool_name in self._failed_tool_names:
            self._recovered_tool_names.add(tool_name)
        content = payload.get("content")
        artifact = payload.get("artifact")
        if self.mode is DisplayMode.COMPACT:
            summary = self._summarize_tool_result(content, artifact)
            line = Text.assemble(("✓ ", "green"), (tool_name, "green bold"))
            if summary:
                line.append(f"  {summary}", style="dim")
            self.console.print(line)
            return
        self.console.print(Text.assemble(("✓ ", "green"), (tool_name, "green bold"), " completed"))
        if content not in (None, ""):
            self.console.print(
                Panel(
                    self._result_renderable(str(content)),
                    title="Result",
                    border_style="green",
                    expand=False,
                )
            )
        artifact_path = self._artifact_path(artifact)
        if artifact_path:
            self.console.print(Text(f"  Full result artifact: {artifact_path}", style="dim"))

    def _render_tool_reused(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name") or "unknown")
        if self.mode is DisplayMode.COMPACT:
            self.console.print(
                Text.assemble(
                    ("↻ ", "cyan"),
                    (tool_name, "cyan bold"),
                    ("  reused earlier identical result", "dim"),
                )
            )
            return
        source = str(payload.get("reused_from_tool_execution_id") or "unknown")
        self.console.print(
            Text.assemble(
                ("↻ ", "cyan"),
                (tool_name, "cyan bold"),
                (f" reused result from {source}", "dim"),
            )
        )

    def _render_convergence(
        self, event_type: str, payload: dict[str, Any]
    ) -> None:
        if event_type == "convergence.warning":
            summary = "Inspection is adding little new evidence; answer from collected evidence"
        elif event_type == "convergence.finalization_requested":
            summary = "Inspection budget reached; answering from collected evidence"
        else:
            summary = "Model returned tool syntax as text; retrying a plain-language answer"
        if self.mode is DisplayMode.COMPACT:
            self.console.print(Text.assemble(("↻ ", "cyan"), (summary, "cyan")))
            return
        inspection_calls = payload.get("inspection_calls", 0)
        no_progress = payload.get("consecutive_no_progress", 0)
        reason = payload.get("reason", "unknown")
        self.console.print(Text.assemble(("↻ ", "cyan"), (summary, "cyan bold")))
        self.console.print(
            Text(
                f"  inspection_calls={inspection_calls} · "
                f"consecutive_no_progress={no_progress} · reason={reason}",
                style="dim",
            )
        )

    def _render_tool_failure(self, event_type: str, payload: dict[str, Any]) -> None:
        self._close_assistant_block()
        tool_name = str(payload.get("tool_name") or "unknown")
        if event_type == "tool.failed":
            self._failed_tool_names.add(tool_name)
            self._recovered_tool_names.discard(tool_name)
        detail = str(payload.get("error") or payload.get("reason") or event_type)
        if self.mode is DisplayMode.COMPACT:
            summary = self._truncate_preserving_line(detail, self._COMPACT_RESULT_LIMIT)
            self.console.print(
                Text.assemble(("✗ ", "red"), (tool_name, "red bold"), f"  {summary}")
            )
            return
        self.console.print(Text.assemble(("✗ ", "red"), (tool_name, "red bold")))
        self.console.print(
            Panel(
                Text(self._truncate_multiline(detail, self._VERBOSE_PAYLOAD_LIMIT)),
                title="Failure",
                border_style="red",
                expand=False,
            )
        )

    def _render_approval(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name") or "unknown")
        arguments = payload.get("arguments")
        preview = self._approval_preview(tool_name, arguments, payload.get("authorization"))
        body: RenderableType = preview
        if self.mode is DisplayMode.VERBOSE and arguments not in (None, {}, []):
            body = Group(preview, Text(""), self._json_renderable(arguments))
        self.console.print(
            Panel(
                body,
                title=f"Approval required · {tool_name}",
                border_style="yellow",
                expand=False,
            )
        )

    def _render_approval_resolved(self, payload: dict[str, Any]) -> None:
        execution_id = payload.get("tool_execution_id")
        tool_name = (
            self._tool_names_by_execution.get(execution_id, "tool")
            if isinstance(execution_id, str)
            else "tool"
        )
        approved = bool(payload.get("approved"))
        if approved:
            self.console.print(
                Text.assemble(("✓ ", "green"), (f"Approved {tool_name}", "green bold"))
            )
            return
        self.console.print(
            Text.assemble(("✗ ", "red"), (f"Denied {tool_name}", "red bold"))
        )

    def _render_completion_evidence(self, payload: dict[str, Any]) -> None:
        status = str(payload.get("status") or "unknown")
        failed_tools = self._string_list(payload.get("failed_tools"))
        recovered_tools = [
            item for item in failed_tools if item in self._recovered_tool_names
        ]
        unresolved_failed_tools = [
            item for item in failed_tools if item not in self._recovered_tool_names
        ]
        rejected_tools = self._string_list(payload.get("rejected_tools"))
        unmet = self._string_list(payload.get("unmet_requirements"))
        if (
            status == "read_only"
            and not unresolved_failed_tools
            and not rejected_tools
            and not unmet
        ):
            if recovered_tools:
                names = ", ".join(dict.fromkeys(recovered_tools))
                self.console.print(
                    Text.assemble(
                        ("↻ ", "cyan"),
                        ("Recovered tool error: ", "dim"),
                        (names, "dim bold"),
                    )
                )
            return

        changed_files = self._string_list(payload.get("changed_files"))
        validations = payload.get("validations")
        validation_items = validations if isinstance(validations, list) else []
        incomplete = status == "read_only" and bool(
            unresolved_failed_tools or rejected_tools or unmet
        )
        display_status = "incomplete" if incomplete else status
        style = "green" if status == "verified" else "yellow"
        marker = "✓" if status == "verified" else "!"
        body = Text()
        body.append("Status: ", style="dim")
        body.append(display_status, style=f"{style} bold")

        if changed_files:
            body.append("\nChanged files:", style="dim")
            for file_name in changed_files[:8]:
                body.append(f"\n  • {file_name}")
            if len(changed_files) > 8:
                body.append(f"\n  … {len(changed_files) - 8} more", style="dim")
        elif incomplete:
            body.append("\nChanges: ", style="dim")
            body.append("No changes applied")

        if not incomplete:
            body.append("\nReview: ", style="dim")
            if payload.get("diff_required") is False:
                body.append("Git diff not required")
            elif payload.get("diff_inspected"):
                body.append("✓ Git diff inspected", style="green")
            else:
                body.append("! Git diff not inspected", style="yellow")

            body.append("\nValidation:", style="dim")
            if validation_items:
                for item in validation_items[:5]:
                    if not isinstance(item, dict):
                        continue
                    command = self._truncate_preserving_line(
                        str(item.get("command") or "unknown command"), 240
                    )
                    succeeded = bool(item.get("succeeded"))
                    result_marker = "✓" if succeeded else "✗"
                    result_style = "green" if succeeded else "red"
                    exit_code = item.get("exit_code")
                    suffix = f" (exit {exit_code})" if exit_code is not None else ""
                    body.append(f"\n  {result_marker} {command}{suffix}", style=result_style)
            elif payload.get("validation_required"):
                body.append("\n  ! Required validation was not run", style="yellow")
            else:
                body.append("\n  Not required", style="dim")

        if recovered_tools:
            body.append("\nRecovered:", style="dim")
            for tool_name in list(dict.fromkeys(recovered_tools))[:4]:
                body.append(
                    f"\n  ↻ {tool_name} succeeded after an earlier failed attempt",
                    style="cyan",
                )

        issues = [
            *(f"Unmet: {item}" for item in unmet),
            *(f"Failed tool: {item}" for item in unresolved_failed_tools),
            *(f"Rejected tool: {item}" for item in rejected_tools),
        ]
        if issues:
            body.append("\nNeeds attention:", style="dim")
            for issue in issues[:6]:
                body.append(f"\n  • {issue}", style="yellow")

        self.console.print(
            Panel(
                body,
                title=(f"{marker} Task incomplete" if incomplete else f"{marker} Task summary"),
                border_style=style,
                expand=False,
            )
        )

    @classmethod
    def _approval_preview(
        cls, tool_name: str, arguments: Any, authorization: Any
    ) -> Text:
        values = arguments if isinstance(arguments, dict) else {}
        body = Text()
        action = {
            "apply_patch": "Apply exact edits to workspace files",
            "replace_text": "Replace text in a workspace file",
            "run_process": "Run a sandboxed local process",
            "write_text_file": "Write a workspace file",
        }.get(tool_name, f"Execute {tool_name}")
        cls._append_approval_field(body, "Action", action)

        if tool_name == "run_process":
            argv = values.get("argv")
            command = (
                " ".join(cls._quote_argument(str(item)) for item in argv)
                if isinstance(argv, list)
                else "unknown"
            )
            cls._append_approval_field(
                body,
                "Command",
                cls._truncate_preserving_line(command, cls._APPROVAL_PREVIEW_LIMIT),
            )
            cls._append_approval_field(body, "Working directory", str(values.get("cwd") or "."))
            timeout = values.get("timeout_seconds")
            cls._append_approval_field(
                body,
                "Timeout",
                f"{timeout} seconds" if timeout is not None else "tool default",
            )
            environment = values.get("env")
            if isinstance(environment, dict) and environment:
                cls._append_approval_field(
                    body,
                    "Environment names",
                    ", ".join(sorted(str(name) for name in environment))[:300],
                )
        elif tool_name == "apply_patch":
            edits = values.get("edits")
            edit_items = [item for item in edits if isinstance(item, dict)] if isinstance(edits, list) else []
            paths = list(
                dict.fromkeys(
                    str(item.get("path"))
                    for item in edit_items
                    if item.get("path") is not None
                )
            )
            cls._append_approval_field(
                body, "Summary", f"{len(edit_items)} edit(s) across {len(paths)} file(s)"
            )
            if paths:
                cls._append_approval_field(body, "Files", "\n  ".join(paths[:8]))
            preview_lines = [cls._edit_preview(item) for item in edit_items[:4]]
            if preview_lines:
                cls._append_approval_field(body, "Preview", "\n  ".join(preview_lines))
        elif tool_name == "replace_text":
            cls._append_approval_field(body, "File", str(values.get("path") or "unknown"))
            replacements = values.get("expected_replacements", 1)
            cls._append_approval_field(body, "Expected replacements", str(replacements))
            cls._append_approval_field(body, "Preview", cls._edit_preview(values))
        elif tool_name == "write_text_file":
            content = str(values.get("content") or "")
            cls._append_approval_field(body, "File", str(values.get("path") or "unknown"))
            cls._append_approval_field(
                body,
                "Content",
                f"{len(content)} character(s), {len(content.splitlines())} line(s)",
            )
        else:
            summary = cls._summarize_tool_request(tool_name, arguments)
            if summary:
                cls._append_approval_field(body, "Request", summary)

        if isinstance(authorization, dict):
            capabilities = authorization.get("capabilities")
            safety_parts = []
            if authorization.get("sandbox_required"):
                safety_parts.append("sandbox required")
            if isinstance(capabilities, list) and capabilities:
                safety_parts.append(
                    "capabilities: " + ", ".join(str(item) for item in capabilities)
                )
            if safety_parts:
                cls._append_approval_field(body, "Safety", " · ".join(safety_parts))
            reason = authorization.get("reason")
            if reason:
                cls._append_approval_field(body, "Policy", str(reason))
        return body

    @classmethod
    def _edit_preview(cls, value: dict[str, Any]) -> str:
        path = str(value.get("path") or "unknown")
        old_text = cls._normalize_preview_text(str(value.get("old_text") or ""))
        new_text = cls._normalize_preview_text(str(value.get("new_text") or ""))
        if not old_text and not new_text:
            return path
        old_preview, new_preview = cls._focused_diff_pair(old_text, new_text)
        return (
            f"{path}:\n"
            f"    - {old_preview or '<empty>'}\n"
            f"    + {new_preview or '<empty>'}"
        )

    @staticmethod
    def _normalize_preview_text(value: str) -> str:
        return " ".join(value.replace("\r", "\n").split())

    @classmethod
    def _focused_diff_pair(cls, old_text: str, new_text: str) -> tuple[str, str]:
        if old_text == new_text:
            bounded = cls._truncate_preserving_line(old_text, 96)
            return bounded, bounded
        prefix = 0
        prefix_limit = min(len(old_text), len(new_text))
        while prefix < prefix_limit and old_text[prefix] == new_text[prefix]:
            prefix += 1
        suffix = 0
        suffix_limit = min(len(old_text) - prefix, len(new_text) - prefix)
        while (
            suffix < suffix_limit
            and old_text[len(old_text) - suffix - 1]
            == new_text[len(new_text) - suffix - 1]
        ):
            suffix += 1

        context = 36
        old_end = len(old_text) - suffix if suffix else len(old_text)
        new_end = len(new_text) - suffix if suffix else len(new_text)
        start = max(0, prefix - context)
        old_stop = min(len(old_text), old_end + context)
        new_stop = min(len(new_text), new_end + context)
        old_preview = ("…" if start else "") + old_text[start:old_stop]
        new_preview = ("…" if start else "") + new_text[start:new_stop]
        if old_stop < len(old_text):
            old_preview += "…"
        if new_stop < len(new_text):
            new_preview += "…"
        return (
            cls._truncate_preserving_line(old_preview, 120),
            cls._truncate_preserving_line(new_preview, 120),
        )

    @staticmethod
    def _append_approval_field(body: Text, label: str, value: str) -> None:
        body.append(f"{label}: ", style="dim")
        body.append(value)
        body.append("\n")

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    @classmethod
    def _summarize_tool_request(cls, tool_name: str, value: Any) -> str:
        if not isinstance(value, dict):
            return cls._format_arguments(value, cls._COMPACT_ARGUMENT_LIMIT)
        path = value.get("path")
        if tool_name == "calculator":
            return cls._truncate_preserving_line(str(value.get("expression") or ""), 120)
        if tool_name == "run_process":
            argv = value.get("argv")
            if isinstance(argv, list):
                rendered = " ".join(cls._quote_argument(str(item)) for item in argv)
                cwd = value.get("cwd")
                if cwd:
                    rendered += f"  (cwd: {cwd})"
                return cls._truncate_preserving_line(rendered, cls._COMPACT_ARGUMENT_LIMIT)
        if tool_name == "search_text":
            query = str(value.get("query") or "")
            location = str(path or ".")
            return cls._truncate_preserving_line(
                f"{json.dumps(query, ensure_ascii=False)} in {location}",
                cls._COMPACT_ARGUMENT_LIMIT,
            )
        if tool_name == "read_file_lines":
            location = str(path or "unknown")
            start = value.get("start_line", 1)
            count = value.get("max_lines")
            suffix = f":{start}"
            if count is not None:
                suffix += f"+{count}"
            return cls._truncate_preserving_line(location + suffix, cls._COMPACT_ARGUMENT_LIMIT)
        if tool_name == "apply_patch":
            edits = value.get("edits")
            if isinstance(edits, list):
                paths = [str(item.get("path")) for item in edits if isinstance(item, dict)]
                unique_paths = list(dict.fromkeys(item for item in paths if item and item != "None"))
                preview = ", ".join(unique_paths[:3])
                more = f" +{len(unique_paths) - 3} more" if len(unique_paths) > 3 else ""
                return cls._truncate_preserving_line(
                    f"{len(edits)} edit(s)" + (f" · {preview}{more}" if preview else ""),
                    cls._COMPACT_ARGUMENT_LIMIT,
                )
        if tool_name == "git_diff":
            location = str(path or "all tracked files")
            staged = "staged · " if value.get("staged") else ""
            return cls._truncate_preserving_line(
                f"{staged}{location}", cls._COMPACT_ARGUMENT_LIMIT
            )
        if path is not None:
            detail = str(path)
            pattern = value.get("pattern") or value.get("glob")
            if pattern:
                detail += f" · {pattern}"
            return cls._truncate_preserving_line(detail, cls._COMPACT_ARGUMENT_LIMIT)
        return cls._format_arguments(value, cls._COMPACT_ARGUMENT_LIMIT)

    @classmethod
    def _summarize_tool_result(cls, content: Any, artifact: Any) -> str:
        artifact_path = cls._artifact_path(artifact)
        if artifact_path:
            return cls._truncate_preserving_line(
                f"result stored in {artifact_path}", cls._COMPACT_RESULT_LIMIT
            )
        if content in (None, ""):
            return "completed"
        normalized = " ".join(str(content).replace("\r", "\n").split())
        return cls._truncate_preserving_line(normalized, cls._COMPACT_RESULT_LIMIT)

    @classmethod
    def _json_renderable(cls, value: Any) -> RenderableType:
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        except TypeError:
            rendered = str(value)
        bounded = cls._truncate_multiline(rendered, cls._VERBOSE_PAYLOAD_LIMIT)
        return Syntax(
            bounded,
            "json",
            word_wrap=True,
            background_color="default",
            theme="monokai",
        )

    @classmethod
    def _result_renderable(cls, value: str) -> RenderableType:
        bounded = cls._truncate_multiline(value, cls._VERBOSE_PAYLOAD_LIMIT)
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return Text(bounded)
        if not isinstance(parsed, (dict, list)):
            return Text(bounded)
        return cls._json_renderable(parsed)

    @staticmethod
    def _artifact_path(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        path = value.get("relative_path") or value.get("path")
        return str(path) if path else ""

    @staticmethod
    def _quote_argument(value: str) -> str:
        if not value or any(character.isspace() for character in value):
            return json.dumps(value, ensure_ascii=False)
        return value

    @staticmethod
    def _format_arguments(value: Any, limit: int) -> str:
        if value in (None, {}, []):
            return ""
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            rendered = str(value)
        return EventRenderer._truncate_preserving_line(rendered, limit)

    @staticmethod
    def _truncate_preserving_line(value: str, limit: int) -> str:
        normalized = value.replace("\r", " ").replace("\n", " ").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 3)] + "..."

    @staticmethod
    def _truncate_multiline(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        omitted = len(value) - limit
        return value[:limit].rstrip() + f"\n… {omitted} character(s) omitted"

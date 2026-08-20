from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class WorkspaceContextError(ValueError):
    """Workspace instruction configuration is invalid."""


@dataclass(frozen=True, slots=True)
class WorkspaceInstruction:
    path: str
    content: str
    sha256: str
    characters: int
    truncated: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "characters": self.characters,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class SkippedWorkspaceInstruction:
    path: str
    reason: str

    def public_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class WorkspaceInstructionBundle:
    enabled: bool
    configured_files: tuple[str, ...]
    max_chars: int
    instructions: tuple[WorkspaceInstruction, ...] = ()
    skipped: tuple[SkippedWorkspaceInstruction, ...] = ()

    @property
    def total_characters(self) -> int:
        return sum(item.characters for item in self.instructions)

    def public_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "configured_files": list(self.configured_files),
            "max_chars": self.max_chars,
            "loaded": [item.public_dict() for item in self.instructions],
            "skipped": [item.public_dict() for item in self.skipped],
            "total_characters": self.total_characters,
        }


_LOCAL_CODING_PROTOCOL = """## Local coding runtime protocol

You are operating inside a trusted local workspace through a durable Agent Runtime.
- Inspect relevant files before editing; prefer one targeted search followed by the smallest useful line-range reads for large files.
- Treat each Tool input schema as authoritative: never invent arguments, and always use workspace-relative paths returned by discovery/search results.
- Reuse evidence already returned in the current Run. Do not repeat an identical search or read, and stop inspecting once the available evidence is sufficient to answer accurately.
- If a target file or symbol can be inferred from the request or session history, continue without asking the user to repeat it.
- If a broad file listing or search is truncated, narrow the path/pattern or use search_text and continue; do not stop only because discovery was truncated.
- Read Runtime Tool Result Artifacts only with read_artifact and continue from next_offset while has_more is true. Never use run_process, Python, cat, or type merely to print a workspace file or Runtime artifact.
- Preserve existing behavior and repository conventions unless the user explicitly requests a change.
- Use exact or batch patch tools for existing text when practical. Runtime approval is the single confirmation step for side-effecting tools: call the tool directly so the Runtime can show its bounded approval preview, and do not first ask for a separate verbal confirmation. Describe risky or broad changes before the tool call, but only stop for user input when requirements are genuinely ambiguous.
- After modifying files, inspect the resulting Git diff when Git tools are available and run the narrowest useful validation command.
- Never claim a file changed, a command passed, or a test succeeded unless the corresponding tool result confirms it.
- Do not commit, push, reset, checkout, install dependencies, or perform unrelated destructive actions unless the user explicitly asks and the runtime permits it.
"""


def load_workspace_instructions(
    workspace: str | Path,
    *,
    enabled: bool = True,
    configured_files: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md"),
    max_chars: int = 50_000,
) -> WorkspaceInstructionBundle:
    root = Path(workspace).resolve()
    if max_chars < 1:
        raise WorkspaceContextError("workspace instruction max_chars must be positive.")
    if not enabled:
        return WorkspaceInstructionBundle(False, configured_files, max_chars)

    loaded: list[WorkspaceInstruction] = []
    skipped: list[SkippedWorkspaceInstruction] = []
    seen: set[Path] = set()
    remaining = max_chars
    for configured in configured_files:
        relative = _validate_configured_path(configured)
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise WorkspaceContextError(
                f"Workspace instruction path escapes the workspace: {configured}"
            )
        if candidate in seen:
            skipped.append(SkippedWorkspaceInstruction(relative.as_posix(), "duplicate"))
            continue
        seen.add(candidate)
        display_path = candidate.relative_to(root).as_posix()
        if not candidate.exists():
            continue
        if not candidate.is_file():
            skipped.append(SkippedWorkspaceInstruction(display_path, "not_a_file"))
            continue
        if remaining <= 0:
            skipped.append(SkippedWorkspaceInstruction(display_path, "limit_exhausted"))
            continue
        try:
            with candidate.open("r", encoding="utf-8", newline="") as handle:
                content = handle.read(remaining + 1)
        except UnicodeDecodeError:
            skipped.append(SkippedWorkspaceInstruction(display_path, "invalid_utf8"))
            continue
        except OSError:
            skipped.append(SkippedWorkspaceInstruction(display_path, "unreadable"))
            continue
        truncated = len(content) > remaining
        if truncated:
            content = content[:remaining]
        loaded.append(
            WorkspaceInstruction(
                path=display_path,
                content=content,
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                characters=len(content),
                truncated=truncated,
            )
        )
        remaining -= len(content)

    return WorkspaceInstructionBundle(
        True,
        configured_files,
        max_chars,
        tuple(loaded),
        tuple(skipped),
    )


def build_local_agent_prompt(
    base_prompt: str,
    bundle: WorkspaceInstructionBundle,
) -> str:
    sections = [base_prompt.strip(), _LOCAL_CODING_PROTOCOL.strip()]
    if bundle.instructions:
        rendered = [
            "## Project workspace instructions",
            "The following user-maintained files define repository-specific requirements. "
            "Follow them when they apply to the current task.",
        ]
        for instruction in bundle.instructions:
            suffix = " (truncated by configured context limit)" if instruction.truncated else ""
            rendered.extend(
                [
                    f"### {instruction.path}{suffix}",
                    instruction.content.rstrip(),
                ]
            )
        sections.append("\n\n".join(rendered).strip())
    return "\n\n".join(section for section in sections if section) + "\n"


def _validate_configured_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceContextError("workspace instruction file names must be non-empty strings.")
    path = Path(value.strip())
    if path.is_absolute() or ".." in path.parts:
        raise WorkspaceContextError(
            f"Workspace instruction file must be a relative path without '..': {value}"
        )
    if path == Path("."):
        raise WorkspaceContextError("Workspace instruction file must name a file.")
    return path

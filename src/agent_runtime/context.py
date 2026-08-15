from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Sequence

from .domain import MemorySearchResult, Message


def estimate_text_tokens(value: str | None) -> int:
    """Provider-neutral, deterministic approximation used for budget decisions."""
    if not value:
        return 0
    return max(1, math.ceil(len(value) / 4))


def estimate_message_tokens(message: Message) -> int:
    payload = message.content or ""
    if message.tool_calls:
        payload += json.dumps(
            [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in message.tool_calls
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    return 4 + estimate_text_tokens(payload)


@dataclass(slots=True)
class ContextBuildResult:
    messages: list[Message]
    token_budget: int
    estimated_tokens: int
    original_tokens: int
    omitted_messages: int = 0
    summary: str | None = None
    memory_ids: list[str] = field(default_factory=list)
    overflow: bool = False

    @property
    def compacted(self) -> bool:
        return self.omitted_messages > 0

    def to_event_payload(self) -> dict[str, object]:
        return {
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "original_tokens": self.original_tokens,
            "selected_messages": len(self.messages),
            "omitted_messages": self.omitted_messages,
            "compacted": self.compacted,
            "summary": self.summary,
            "memory_ids": self.memory_ids,
            "overflow": self.overflow,
        }


class ContextBuilder:
    """Select a safe model context without splitting tool-call/result groups."""

    def __init__(
        self,
        token_budget: int = 4096,
        *,
        recent_groups: int = 4,
        summary_max_chars: int = 1000,
        memory_token_budget: int = 512,
    ) -> None:
        if token_budget < 64:
            raise ValueError("Context token budget must be at least 64.")
        if recent_groups < 1:
            raise ValueError("recent_groups must be at least 1.")
        self.token_budget = token_budget
        self.recent_groups = recent_groups
        self.summary_max_chars = max(128, summary_max_chars)
        self.memory_token_budget = max(0, memory_token_budget)

    def build(
        self,
        messages: Sequence[Message],
        *,
        memories: Sequence[MemorySearchResult] = (),
    ) -> ContextBuildResult:
        original = list(messages)
        original_tokens = sum(estimate_message_tokens(message) for message in original)
        system_messages = [message for message in original if message.role == "system"]
        conversation = [message for message in original if message.role != "system"]
        groups = self._safe_groups(conversation)

        memory_message, memory_ids = self._memory_message(memories)
        prefix = list(system_messages)
        if memory_message is not None:
            prefix.append(memory_message)

        selected_indices: set[int] = set()
        required = set(range(max(0, len(groups) - self.recent_groups), len(groups)))
        required.update(
            index for index, group in enumerate(groups) if self._has_unfinished_tool_call(group)
        )
        current_tokens = sum(estimate_message_tokens(message) for message in prefix)

        for index in sorted(required):
            selected_indices.add(index)
            current_tokens += self._group_tokens(groups[index])

        for index in range(len(groups) - 1, -1, -1):
            if index in selected_indices:
                continue
            group_tokens = self._group_tokens(groups[index])
            if current_tokens + group_tokens <= self.token_budget:
                selected_indices.add(index)
                current_tokens += group_tokens

        omitted = [
            message
            for index, group in enumerate(groups)
            if index not in selected_indices
            for message in group
        ]
        selected = [
            message
            for index, group in enumerate(groups)
            if index in selected_indices
            for message in group
        ]

        summary = self._summary(omitted) if omitted else None
        if summary:
            summary_message = Message(
                role="system",
                content="Context summary of omitted history:\n" + summary,
                name="context-summary",
            )
            available = self.token_budget - current_tokens
            if available > 8:
                summary_message = self._truncate_to_tokens(summary_message, available)
                prefix.append(summary_message)

        built = prefix + selected
        estimated = sum(estimate_message_tokens(message) for message in built)
        if estimated > self.token_budget:
            built = self._shrink_optional_content(built, self.token_budget)
            estimated = sum(estimate_message_tokens(message) for message in built)
        return ContextBuildResult(
            messages=built,
            token_budget=self.token_budget,
            estimated_tokens=estimated,
            original_tokens=original_tokens,
            omitted_messages=len(omitted),
            summary=summary,
            memory_ids=memory_ids,
            overflow=estimated > self.token_budget,
        )

    def _memory_message(
        self, memories: Sequence[MemorySearchResult]
    ) -> tuple[Message | None, list[str]]:
        if not memories or self.memory_token_budget == 0:
            return None, []
        lines = ["Relevant scoped memories:"]
        memory_ids: list[str] = []
        used = estimate_text_tokens(lines[0])
        for result in memories:
            record = result.record
            line = f"- [{record.id}] ({record.scope.value}:{record.scope_id}) {record.content}"
            cost = estimate_text_tokens(line)
            if used + cost > self.memory_token_budget:
                break
            lines.append(line)
            memory_ids.append(record.id)
            used += cost
        if not memory_ids:
            return None, []
        return Message(role="system", content="\n".join(lines), name="memory"), memory_ids

    @staticmethod
    def _safe_groups(messages: Sequence[Message]) -> list[list[Message]]:
        groups: list[list[Message]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            group = [message]
            if message.role == "assistant" and message.tool_calls:
                call_ids = {call.id for call in message.tool_calls}
                cursor = index + 1
                while cursor < len(messages) and messages[cursor].role == "tool":
                    if messages[cursor].tool_call_id not in call_ids:
                        break
                    group.append(messages[cursor])
                    cursor += 1
                index = cursor
            else:
                index += 1
            groups.append(group)
        return groups

    @staticmethod
    def _has_unfinished_tool_call(group: Sequence[Message]) -> bool:
        assistant = next(
            (message for message in group if message.role == "assistant" and message.tool_calls),
            None,
        )
        if assistant is None:
            return False
        expected = {call.id for call in assistant.tool_calls}
        completed = {
            message.tool_call_id for message in group if message.role == "tool"
        }
        return not expected.issubset(completed)

    @staticmethod
    def _group_tokens(group: Sequence[Message]) -> int:
        return sum(estimate_message_tokens(message) for message in group)

    def _summary(self, messages: Sequence[Message]) -> str:
        parts: list[str] = []
        for message in messages:
            if message.tool_calls:
                content = "tool calls: " + ", ".join(call.name for call in message.tool_calls)
            else:
                content = (message.content or "").replace("\n", " ").strip()
            if len(content) > 180:
                content = content[:177] + "..."
            parts.append(f"{message.role}: {content}")
        value = "\n".join(parts)
        if len(value) > self.summary_max_chars:
            value = value[: self.summary_max_chars - 3] + "..."
        return value

    @staticmethod
    def _truncate_to_tokens(message: Message, tokens: int) -> Message:
        max_chars = max(16, (tokens - 4) * 4)
        content = message.content or ""
        if len(content) <= max_chars:
            return message
        return Message(
            role=message.role,
            content=content[: max(0, max_chars - 19)] + "...[summary cut]",
            name=message.name,
        )

    @staticmethod
    def _shrink_optional_content(messages: list[Message], budget: int) -> list[Message]:
        result = [Message.from_dict(message.to_dict()) for message in messages]
        for message in result:
            if sum(estimate_message_tokens(item) for item in result) <= budget:
                break
            if message.role == "system" or message.tool_calls or not message.content:
                continue
            if len(message.content) > 96:
                head = message.content[:48]
                tail = message.content[-24:]
                message.content = f"{head}...[context cut]...{tail}"
        return result

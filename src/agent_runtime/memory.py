from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from .domain import MemoryRecord, MemoryScope, MemorySearchResult


class MemoryStore(Protocol):
    """Storage boundary for scoped, lifecycle-aware long-term memory."""

    def save_memory(self, record: MemoryRecord) -> MemoryRecord: ...

    def get_memory(self, memory_id: str) -> MemoryRecord: ...

    def delete_memory(self, memory_id: str) -> MemoryRecord: ...

    def purge_expired_memories(self, now: datetime | None = None) -> int: ...

    def search_memories(
        self,
        query: str,
        scopes: Sequence[tuple[MemoryScope, str]],
        *,
        limit: int = 5,
    ) -> list[MemorySearchResult]: ...

    def has_active_memories(self, scopes: Sequence[tuple[MemoryScope, str]]) -> bool: ...

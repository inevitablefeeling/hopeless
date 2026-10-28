"""Command usage counters. Counts are buffered in memory and flushed in batches."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

log = logging.getLogger("hopeless.stats")
MAX_PENDING_KEYS = 5000  # hard bound on the in-memory buffer


@dataclass(frozen=True)
class StatsSummary:
    total_uses: int
    top_commands: list[tuple[str, int]]


class UsageStore(Protocol):
    async def add_usage(self, rows: list[tuple[int, str, int]]) -> None: ...
    async def summary(self) -> tuple[int, list[tuple[str, int]]]: ...


class StatsService:
    def __init__(self, store: UsageStore) -> None:
        self._store = store
        self._pending: dict[tuple[int, str], int] = {}

    def record(self, guild_id: Optional[int], command: str) -> None:
        key = (guild_id or 0, command[:64])  # guild 0 = direct messages
        if key not in self._pending and len(self._pending) >= MAX_PENDING_KEYS:
            return
        self._pending[key] = self._pending.get(key, 0) + 1

    @property
    def pending_total(self) -> int:
        return sum(self._pending.values())

    async def flush(self) -> int:
        """Write buffered counts. On failure the counts are kept so the next flush retries them."""
        if not self._pending:
            return 0
        batch, self._pending = self._pending, {}
        rows = [(guild_id, command, uses) for (guild_id, command), uses in batch.items()]
        try:
            await self._store.add_usage(rows)
        except Exception:
            for key, uses in batch.items():
                self._pending[key] = self._pending.get(key, 0) + uses
            raise
        return sum(uses for _, _, uses in rows)

    async def summary(self) -> StatsSummary:
        try:
            await self.flush()
        except Exception:
            log.warning("stats flush failed while building a summary", exc_info=True)
        total, top = await self._store.summary()
        return StatsSummary(total + self.pending_total, top)

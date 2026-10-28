"""AFK status, kept per server. Loaded into memory at startup so message handling never hits the database."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

MAX_REASON = 150
DEFAULT_REASON = "AFK"
CLEAR_GRACE_SECONDS = 5.0  # don't auto-clear on the very message that set the status
NOTICE_COOLDOWN_SECONDS = 60.0
_MAX_NOTICES = 2000


@dataclass(frozen=True)
class AfkRecord:
    guild_id: int
    user_id: int
    reason: str
    since: datetime


class AfkStore(Protocol):
    async def load_all(self) -> list[AfkRecord]: ...
    async def save(self, record: AfkRecord) -> None: ...
    async def delete(self, guild_id: int, user_id: int) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clean_reason(raw: Optional[str]) -> str:
    text = " ".join((raw or "").split())[:MAX_REASON]
    return text or DEFAULT_REASON


class AfkService:
    def __init__(
        self,
        store: AfkStore,
        *,
        now: Callable[[], datetime] = _utcnow,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._now = now
        self._clock = clock
        self._records: dict[tuple[int, int], AfkRecord] = {}
        self._notices: dict[tuple[int, int], float] = {}

    async def load(self) -> None:
        self._records = {(r.guild_id, r.user_id): r for r in await self._store.load_all()}

    def get(self, guild_id: int, user_id: int) -> Optional[AfkRecord]:
        return self._records.get((guild_id, user_id))

    async def set(self, guild_id: int, user_id: int, reason: Optional[str]) -> AfkRecord:
        record = AfkRecord(guild_id, user_id, clean_reason(reason), self._now())
        await self._store.save(record)
        self._records[(guild_id, user_id)] = record
        return record

    async def clear(self, guild_id: int, user_id: int) -> Optional[AfkRecord]:
        record = self._records.get((guild_id, user_id))
        if record is None:
            return None
        await self._store.delete(guild_id, user_id)
        self._records.pop((guild_id, user_id), None)
        return record

    def should_auto_clear(self, record: AfkRecord) -> bool:
        return (self._now() - record.since).total_seconds() >= CLEAR_GRACE_SECONDS

    def notice_allowed(self, channel_id: int, user_id: int) -> bool:
        """Rate-limit "X is AFK" replies so a busy channel isn't flooded."""
        now = self._clock()
        key = (channel_id, user_id)
        last = self._notices.get(key)
        if last is not None and now - last < NOTICE_COOLDOWN_SECONDS:
            return False
        if len(self._notices) >= _MAX_NOTICES:
            cutoff = now - NOTICE_COOLDOWN_SECONDS
            self._notices = {k: t for k, t in self._notices.items() if t >= cutoff}
        self._notices[key] = now
        return True

"""Message counts and XP, kept in memory and written to PostgreSQL in batches.

Discord has no historical message counter, so counts start the day the bot starts tracking a member.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core_leveling_logic import can_award, level_for, roll_xp

log = logging.getLogger("hopeless.activity")


@dataclass
class Entry:
    messages: int = 0
    xp: int = 0
    level: int = 0
    last_xp: Optional[float] = None  # monotonic-ish wall clock of the last XP award (not stored)


@dataclass
class LevelSettings:
    enabled: bool = True
    log_channel_id: Optional[int] = None
    xp_min: int = 15
    xp_max: int = 25
    cooldown: int = 60
    roles: dict[int, int] = field(default_factory=dict)  # level -> role id


class ActivityService:
    def __init__(self, db) -> None:
        self.db = db
        self._cache: dict[tuple[int, int], Entry] = {}
        self._dirty: set[tuple[int, int]] = set()
        self._settings: dict[int, LevelSettings] = {}

    # --- settings ----------------------------------------------------------------------------
    async def settings(self, guild_id: int) -> LevelSettings:
        cached = self._settings.get(guild_id)
        if cached is None:
            row = await self.db.fetchrow("SELECT enabled, log_channel_id, xp_min, xp_max, cooldown_seconds FROM leveling_settings WHERE guild_id = $1", guild_id)
            roles = await self.db.fetch("SELECT level, role_id FROM level_roles WHERE guild_id = $1", guild_id)
            cached = LevelSettings(row["enabled"], row["log_channel_id"], row["xp_min"], row["xp_max"], row["cooldown_seconds"]) if row else LevelSettings()
            cached.roles = {int(r["level"]): int(r["role_id"]) for r in roles}
            self._settings[guild_id] = cached
        return cached

    def forget_settings(self, guild_id: int) -> None:
        self._settings.pop(guild_id, None)

    async def save_settings(self, guild_id: int, **fields) -> None:
        allowed = {"enabled", "log_channel_id"}
        assert set(fields) <= allowed
        names = list(fields)
        marks = ", ".join(f"${i + 2}" for i in range(len(names)))
        sets = ", ".join(f"{n} = EXCLUDED.{n}" for n in names)
        async with self.db.transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute(f"INSERT INTO leveling_settings (guild_id, {', '.join(names)}) VALUES ($1, {marks}) "
                               f"ON CONFLICT (guild_id) DO UPDATE SET {sets}", guild_id, *fields.values())
        self.forget_settings(guild_id)

    # --- members -----------------------------------------------------------------------------
    async def entry(self, guild_id: int, user_id: int) -> Entry:
        key = (guild_id, user_id)
        found = self._cache.get(key)
        if found is None:
            row = await self.db.fetchrow("SELECT messages, xp, level FROM member_activity WHERE guild_id = $1 AND user_id = $2", guild_id, user_id)
            found = self._cache.setdefault(key, Entry(row["messages"], row["xp"], row["level"]) if row else Entry())
        return found

    async def messages(self, guild_id: int, user_id: int) -> int:
        return (await self.entry(guild_id, user_id)).messages

    async def record(self, guild_id: int, user_id: int, *, now: Optional[float] = None) -> Optional[tuple[int, int, int]]:
        """Count one message and maybe award XP. Returns (old level, new level, total xp) when the member levelled up."""
        now = time.time() if now is None else now
        entry = await self.entry(guild_id, user_id)
        entry.messages += 1
        self._dirty.add((guild_id, user_id))
        settings = await self.settings(guild_id)
        if not settings.enabled or not can_award(entry.last_xp, now, settings.cooldown):
            return None
        entry.last_xp = now
        entry.xp += roll_xp(settings.xp_min, settings.xp_max)
        new_level = level_for(entry.xp)
        if new_level > entry.level:
            old, entry.level = entry.level, new_level
            return old, new_level, entry.xp
        return None

    async def flush(self) -> int:
        if not self._dirty:
            return 0
        batch, self._dirty = list(self._dirty), set()
        try:
            await self.db.executemany("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", [(g,) for g in {g for g, _ in batch}])
            await self.db.executemany(
                "INSERT INTO member_activity (guild_id, user_id, messages, xp, level) VALUES ($1,$2,$3,$4,$5) "
                "ON CONFLICT (guild_id, user_id) DO UPDATE SET messages = EXCLUDED.messages, xp = EXCLUDED.xp, level = EXCLUDED.level",
                [(g, u, self._cache[(g, u)].messages, self._cache[(g, u)].xp, self._cache[(g, u)].level) for g, u in batch])
        except Exception:
            self._dirty.update(batch)  # try again next time; nothing is lost
            log.warning("could not save activity", exc_info=True)
            return 0
        return len(batch)

    async def leaderboard(self, guild_id: int, limit: int = 10):
        await self.flush()
        return await self.db.fetch("SELECT user_id, xp, level, messages FROM member_activity WHERE guild_id = $1 ORDER BY xp DESC, user_id LIMIT $2", guild_id, limit)

    async def rank_of(self, guild_id: int, user_id: int) -> Optional[int]:
        await self.flush()
        row = await self.db.fetchrow(
            "SELECT 1 + (SELECT COUNT(*) FROM member_activity WHERE guild_id = $1 AND xp > m.xp) AS rank FROM member_activity m WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id)
        return int(row["rank"]) if row else None

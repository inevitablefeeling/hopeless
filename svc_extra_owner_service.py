"""Extra owners: trusted people with owner-level control of ONE server. They get no global power at all."""
from __future__ import annotations

import logging

log = logging.getLogger("hopeless.extraowners")
MAX_EXTRA_OWNERS = 10


class ExtraOwnerService:
    def __init__(self, db) -> None:
        self.db = db
        self._owners: dict[int, set[int]] = {}

    async def load(self) -> None:
        rows = await self.db.fetch("SELECT guild_id, user_id FROM extra_owners")
        self._owners = {}
        for row in rows:
            self._owners.setdefault(int(row["guild_id"]), set()).add(int(row["user_id"]))

    def contains(self, guild_id: int, user_id: int) -> bool:
        return user_id in self._owners.get(guild_id, ())

    def list(self, guild_id: int) -> list[int]:
        return sorted(self._owners.get(guild_id, ()))

    async def add(self, guild_id: int, user_id: int, added_by: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute("INSERT INTO extra_owners (guild_id, user_id, added_by) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", guild_id, user_id, added_by)
        self._owners.setdefault(guild_id, set()).add(user_id)

    async def remove(self, guild_id: int, user_id: int) -> bool:
        result = await self.db.fetchval("DELETE FROM extra_owners WHERE guild_id = $1 AND user_id = $2 RETURNING 1", guild_id, user_id)
        self._owners.get(guild_id, set()).discard(user_id)
        return bool(result)

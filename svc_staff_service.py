"""Per-server moderator roles. They NARROW who may moderate (the Discord permission is still required)."""
from __future__ import annotations


class StaffRoleService:
    def __init__(self, db) -> None:
        self.db = db
        self._mod: dict[int, list[int]] = {}

    async def mod_roles(self, guild_id: int) -> list[int]:
        cached = self._mod.get(guild_id)
        if cached is None:
            cached = list(await self.db.fetchval("SELECT mod_role_ids FROM mod_settings WHERE guild_id = $1", guild_id) or [])
            self._mod[guild_id] = cached
        return cached

    async def set_mod_roles(self, guild_id: int, role_ids: list[int]) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute("INSERT INTO mod_settings (guild_id, mod_role_ids) VALUES ($1, $2) "
                               "ON CONFLICT (guild_id) DO UPDATE SET mod_role_ids = EXCLUDED.mod_role_ids", guild_id, role_ids)
        self._mod.pop(guild_id, None)

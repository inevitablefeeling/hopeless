"""Per-server whitelist. Every call is scoped by guild id; nothing is shared between servers."""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Iterable, Optional, Protocol

from core_errors import InvalidInput
from core_guild_settings import validate_guild_id
from core_whitelist_logic import TARGET_TYPES, WhitelistEntry, is_exempt, normalize_scope

log = logging.getLogger("hopeless.whitelist")
_CACHE_GUILDS = 512


class WhitelistStore(Protocol):
    async def load_guild(self, guild_id: int) -> list[WhitelistEntry]: ...
    async def add(self, entry: WhitelistEntry) -> bool: ...
    async def remove(self, guild_id: int, target_type: str, target_id: int, scope: Optional[str]) -> int: ...


class WhitelistService:
    def __init__(self, store: WhitelistStore) -> None:
        self._store = store
        self._cache: "OrderedDict[int, list[WhitelistEntry]]" = OrderedDict()

    async def entries(self, guild_id: int) -> list[WhitelistEntry]:
        gid = validate_guild_id(guild_id)
        cached = self._cache.get(gid)
        if cached is None:
            cached = await self._store.load_guild(gid)
            self._cache[gid] = cached
            while len(self._cache) > _CACHE_GUILDS:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(gid)
        return list(cached)

    async def add(self, *, guild_id: int, actor_id: int, target_type: str, target_id: int, scope: str) -> bool:
        """True if the entry is new, False if it already existed."""
        gid = validate_guild_id(guild_id)
        if target_type not in TARGET_TYPES:
            raise InvalidInput("Whitelist targets must be a user or a role.")
        if target_id <= 0:
            raise InvalidInput("That isn't a valid user or role.")
        entry = WhitelistEntry(gid, target_type, target_id, normalize_scope(scope), actor_id)
        added = await self._store.add(entry)
        self._cache.pop(gid, None)
        log.info(
            "whitelist %s guild=%s target=%s:%s scope=%s by=%s",
            "add" if added else "add(duplicate)", gid, target_type, target_id, entry.scope, actor_id,
        )
        return added

    async def remove(
        self, *, guild_id: int, actor_id: int, target_type: str, target_id: int, scope: Optional[str] = None
    ) -> int:
        """Remove one scope, or every scope for the target when `scope` is None. Returns rows removed."""
        gid = validate_guild_id(guild_id)
        if target_type not in TARGET_TYPES:
            raise InvalidInput("Whitelist targets must be a user or a role.")
        normalized = normalize_scope(scope) if scope else None
        removed = await self._store.remove(gid, target_type, target_id, normalized)
        self._cache.pop(gid, None)
        if removed:
            log.info(
                "whitelist remove guild=%s target=%s:%s scope=%s by=%s removed=%s",
                gid, target_type, target_id, normalized or "*", actor_id, removed,
            )
        return removed

    async def is_exempt(self, guild_id: int, user_id: int, role_ids: Iterable[int], scope: str) -> bool:
        return is_exempt(await self.entries(guild_id), user_id=user_id, role_ids=role_ids, scope=scope)

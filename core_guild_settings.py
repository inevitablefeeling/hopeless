"""Per-server prefix handling. Each guild's prefix is stored and cached independently."""
from __future__ import annotations

from typing import Optional, Protocol

from core_errors import InvalidInput

PREFIX_MAX_LENGTH = 5
RESET_WORDS = frozenset({"reset", "default"})
_FORBIDDEN = frozenset("`\\@#<>/")


def validate_guild_id(guild_id: object) -> int:
    if isinstance(guild_id, bool) or not isinstance(guild_id, int) or guild_id <= 0:
        raise ValueError(f"invalid guild id: {guild_id!r}")
    return guild_id


def validate_prefix(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    if not value:
        raise InvalidInput("Give me a prefix, for example `!`.")
    if len(value) > PREFIX_MAX_LENGTH:
        raise InvalidInput(f"Prefixes can be at most {PREFIX_MAX_LENGTH} characters long.")
    if any(ch.isspace() for ch in value) or not value.isprintable():
        raise InvalidInput("Prefixes can't contain spaces or hidden characters.")
    if "/" in value:
        raise InvalidInput("`/` is reserved for slash commands. Pick another prefix.")
    bad = sorted(set(value) & _FORBIDDEN)
    if bad:
        raise InvalidInput("Prefixes can't contain: " + " ".join(bad))
    return value


class PrefixStore(Protocol):
    async def load_all(self) -> dict[int, str]: ...
    async def save(self, guild_id: int, prefix: str) -> None: ...
    async def clear(self, guild_id: int) -> None: ...


class PrefixService:
    def __init__(self, store: PrefixStore, default: str = "C") -> None:
        self._store = store
        self._default = validate_prefix(default)
        self._cache: dict[int, str] = {}

    @property
    def default(self) -> str:
        return self._default

    async def load(self) -> None:
        loaded = await self._store.load_all()
        self._cache = {validate_guild_id(guild_id): prefix for guild_id, prefix in loaded.items()}

    def get(self, guild_id: Optional[int]) -> str:
        """Fast, synchronous lookup used for every incoming message. DMs use the default."""
        if guild_id is None:
            return self._default
        return self._cache.get(guild_id, self._default)

    async def set(self, guild_id: int, raw: str) -> str:
        """Validate, persist, then cache. Returns the prefix now in effect for that guild only."""
        gid = validate_guild_id(guild_id)
        if (raw or "").strip().lower() in RESET_WORDS:
            await self._store.clear(gid)
            self._cache.pop(gid, None)
            return self._default
        prefix = validate_prefix(raw)
        await self._store.save(gid, prefix)  # persist first so the cache never gets ahead of the database
        self._cache[gid] = prefix
        return prefix

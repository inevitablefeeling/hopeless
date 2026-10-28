"""Server logs and security logs.

Rules: never ping anyone (names and ids only, allowed_mentions is empty), never log the log channels
themselves (no loops), tolerate missing channels, and never claim something happened that did not.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import discord

import core_branding as branding
from core_log_routing import channel_for

log = logging.getLogger("hopeless.logs")

SERVER_EVENTS = (
    "message_edit", "message_delete", "member_join", "member_leave", "member_update", "role_create",
    "role_delete", "role_update", "channel_create", "channel_delete", "channel_update", "voice", "emoji",
    "invite", "ticket", "moderation",
)
NO_MENTIONS = discord.AllowedMentions.none()


@dataclass
class LogSettings:
    server_channel_id: Optional[int] = None
    security_channel_id: Optional[int] = None
    disabled_events: set[str] = field(default_factory=set)
    channels: dict[str, int] = field(default_factory=dict)  # message, member, voice, emoji, invite, ticket
    alert_role_ids: list[int] = field(default_factory=list)  # pinged only for urgent security alerts

    def channel_id(self, key: str) -> Optional[int]:
        """The channel for a routing key, falling back to server-logs when a topic channel isn't set."""
        if key == "security":
            return self.security_channel_id
        if key == "server":
            return self.server_channel_id
        return self.channels.get(key) or self.server_channel_id

    def all_ids(self) -> set[int]:
        return {i for i in (self.server_channel_id, self.security_channel_id, *self.channels.values()) if i}


def plain(text: object, limit: int = 1000) -> str:
    """Text that can't ping: mentions are broken up and long values trimmed."""
    value = discord.utils.escape_mentions(str(text)).replace("<@", "<\u200b@").replace("<#", "<\u200b#")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def who(user) -> str:
    """'name (id)' - never mention syntax."""
    return f"{plain(getattr(user, 'name', user), 80)} ({getattr(user, 'id', user)})"


class LogService:
    def __init__(self, db) -> None:
        self.db = db
        self._cache: dict[int, LogSettings] = {}

    async def settings(self, guild_id: int) -> LogSettings:
        cached = self._cache.get(guild_id)
        if cached is None:
            row = await self.db.fetchrow(
                "SELECT server_channel_id, security_channel_id, disabled_events, channels, alert_role_ids FROM logging_settings WHERE guild_id = $1",
                guild_id,
            )
            if row:
                raw = row["channels"]
                extra = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
                cached = LogSettings(row["server_channel_id"], row["security_channel_id"], set(row["disabled_events"]),
                                     {k: int(v) for k, v in extra.items()}, [int(r) for r in row["alert_role_ids"]])
            else:
                cached = LogSettings()
            self._cache[guild_id] = cached
        return cached

    async def save_channels(self, guild_id: int, server_id: Optional[int], security_id: Optional[int],
                            extra: Optional[dict[str, int]] = None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute(
                """
                INSERT INTO logging_settings (guild_id, server_channel_id, security_channel_id, channels)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (guild_id) DO UPDATE SET
                    server_channel_id = COALESCE($2, logging_settings.server_channel_id),
                    security_channel_id = COALESCE($3, logging_settings.security_channel_id),
                    channels = logging_settings.channels || $4::jsonb
                """,
                guild_id, server_id, security_id, json.dumps({k: int(v) for k, v in (extra or {}).items()}),
            )
        self._cache.pop(guild_id, None)

    async def set_alert_roles(self, guild_id: int, role_ids: list[int]) -> None:
        async with self.bot_db_transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute(
                "INSERT INTO logging_settings (guild_id, alert_role_ids) VALUES ($1, $2) "
                "ON CONFLICT (guild_id) DO UPDATE SET alert_role_ids = EXCLUDED.alert_role_ids", guild_id, role_ids)
        self._cache.pop(guild_id, None)

    def bot_db_transaction(self):
        return self.db.transaction()

    async def set_event(self, guild_id: int, event: str, enabled: bool) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute("INSERT INTO logging_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            if enabled:
                await conn.execute(
                    "UPDATE logging_settings SET disabled_events = array_remove(disabled_events, $2) WHERE guild_id = $1",
                    guild_id, event,
                )
            else:
                await conn.execute(
                    """
                    UPDATE logging_settings SET disabled_events = array_append(array_remove(disabled_events, $2), $2)
                    WHERE guild_id = $1
                    """,
                    guild_id, event,
                )
        self._cache.pop(guild_id, None)

    async def is_log_channel(self, guild_id: int, channel_id: int) -> bool:
        return channel_id in (await self.settings(guild_id)).all_ids()

    @staticmethod
    def embed(title: str, description: str = "", fields: Optional[list[tuple[str, str]]] = None) -> discord.Embed:
        embed = discord.Embed(
            title=plain(title, 250), description=plain(description, 3500), color=branding.ACCENT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        for name, value in (fields or [])[:20]:
            embed.add_field(name=plain(name, 250), value=plain(value) or "-", inline=False)
        return embed

    async def _send(self, guild: discord.Guild, channel_id: Optional[int], embed: discord.Embed, *,
                    content: Optional[str] = None, allowed: discord.AllowedMentions = NO_MENTIONS, **kw) -> bool:
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return False
        try:
            await channel.send(content, embed=embed, allowed_mentions=allowed, **kw)
            return True
        except discord.HTTPException:
            log.warning("could not write to log channel %s in guild %s", channel_id, guild.id)
            return False

    async def server(self, guild: discord.Guild, event: str, title: str, description: str = "",
                     fields: Optional[list[tuple[str, str]]] = None, **kw) -> bool:
        s = await self.settings(guild.id)
        if event in s.disabled_events:
            return False
        return await self._send(guild, s.channel_id(channel_for(event)), self.embed(title, description, fields), **kw)

    async def security(self, guild: discord.Guild, title: str, description: str = "",
                       fields: Optional[list[tuple[str, str]]] = None, *, urgent: bool = False) -> bool:
        """Security alert. Only an `urgent` one pings, and only the roles chosen with the alertrole command."""
        s = await self.settings(guild.id)
        embed = self.embed(title, description, fields)
        content, allowed = None, NO_MENTIONS
        if urgent:
            roles = [guild.get_role(r) for r in s.alert_role_ids]
            roles = [r for r in roles if r is not None]
            if roles:
                content = " ".join(r.mention for r in roles)
                allowed = discord.AllowedMentions(roles=roles)
        return await self._send(guild, s.security_channel_id, embed, content=content, allowed=allowed)

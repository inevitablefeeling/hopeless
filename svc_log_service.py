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
from core_embeds import ERROR, SILVER, SUCCESS, WARNING
from core_log_routing import channel_for
from core_ui_v2 import build_embed, build_layout, send_layout, v2_available

log = logging.getLogger("hopeless.logs")

WEBHOOK_NAME = "HOPELESS Logging"
SERVER_EVENTS = (
    "avatar", "message_edit", "message_delete", "member_join", "member_leave", "member_update", "role_create",
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
        """#security-logs for security alerts and #logs for everything else (older per-topic channels are no longer used)."""
        return self.security_channel_id if key == "security" else self.server_channel_id

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
        self.bot = None  # set by the bot after construction; used for the webhook's name and avatar
        self._cache: dict[int, LogSettings] = {}
        self._hooks: dict[int, discord.Webhook] = {}  # channel id -> our logging webhook (held in memory only, never logged)

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

    EVENT_COLORS = {"message_delete": ERROR, "channel_delete": ERROR, "role_delete": ERROR, "member_join": SUCCESS, "channel_create": SUCCESS,
                    "role_create": SUCCESS, "member_leave": WARNING, "moderation": ERROR}

    @staticmethod
    def _fields(fields):
        return [(plain(name, 250), plain(value) or "-") for name, value in (fields or [])[:20]]

    async def _send(self, guild: discord.Guild, channel_id: Optional[int], *, title: str, description: str = "", fields=None, color: int = branding.ACCENT_COLOR,
                    author=None, ident: Optional[int] = None, urgent_text: Optional[str] = None, allowed: discord.AllowedMentions = NO_MENTIONS,
                    thumbnail: Optional[str] = None) -> bool:
        """Post one log card (Components V2, or a classic embed if V2 isn't available) without ever pinging by accident."""
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return False
        now = datetime.now(timezone.utc)
        who_author = (plain(author.name, 80), author.display_avatar.url) if author is not None else None
        card = dict(title=plain(title, 250), description=plain(description, 3500), fields=self._fields(fields), color=color, author=who_author,
                    thumbnail=thumbnail)
        id_text = f"ID: {ident or getattr(author, 'id', '')}".strip()
        make_layout = lambda: build_layout(raw_footer=f"{id_text} | <t:{int(now.timestamp())}:f>".strip(" |"), top_text=urgent_text, **card)  # noqa: E731
        make_embed = lambda: build_embed(raw_footer=id_text or None, timestamp=now, **card)  # noqa: E731
        if await self._via_webhook(channel, make_layout, make_embed, allowed, urgent_text):
            return True
        try:  # the webhook isn't possible (no Manage Webhooks, or it failed): post as the bot itself
            await send_layout(channel, make_layout, make_embed, allowed_mentions=allowed, content=urgent_text)
            return True
        except discord.HTTPException:
            log.warning("could not write to log channel %s in guild %s", channel_id, guild.id)
            return False

    # --- webhook delivery: logs appear as "HOPELESS Logging", a separate identity from the bot itself ----------------
    async def _hook_for(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        hook = self._hooks.get(channel.id)
        if hook is not None:
            return hook
        me = channel.guild.me
        if me is None or not channel.permissions_for(me).manage_webhooks:
            return None
        try:
            for existing in await channel.webhooks():
                if existing.name == WEBHOOK_NAME and existing.user is not None and existing.user.id == me.id and existing.token:
                    hook = existing
                    break
            else:
                avatar = await self.bot.user.display_avatar.read() if self.bot is not None and self.bot.user is not None else None
                hook = await channel.create_webhook(name=WEBHOOK_NAME, avatar=avatar, reason="HOPELESS logging")
        except discord.HTTPException:
            log.info("could not create the logging webhook in channel %s", channel.id)
            return None
        self._hooks[channel.id] = hook
        return hook

    async def _via_webhook(self, channel, make_layout, make_embed, allowed, content) -> bool:
        avatar_url = self.bot.user.display_avatar.url if self.bot is not None and self.bot.user is not None else None
        for _ in range(2):  # the second try is only for a webhook someone deleted since we cached it
            hook = await self._hook_for(channel)
            if hook is None:
                return False
            try:
                if v2_available():
                    try:
                        await hook.send(view=make_layout(), username=WEBHOOK_NAME, avatar_url=avatar_url, allowed_mentions=allowed)
                        return True
                    except (AttributeError, TypeError, ValueError):
                        log.info("this discord.py can't send V2 through a webhook; using an embed", exc_info=True)
                    except discord.NotFound:
                        raise
                    except discord.HTTPException:
                        log.info("Discord refused a V2 webhook message; using an embed")
                await hook.send(embed=make_embed(), content=content, username=WEBHOOK_NAME, avatar_url=avatar_url, allowed_mentions=allowed)
                return True
            except discord.NotFound:
                self._hooks.pop(channel.id, None)
            except discord.HTTPException:
                return False
        return False

    async def server(self, guild: discord.Guild, event: str, title: str, description: str = "",
                     fields: Optional[list[tuple[str, str]]] = None, *, author=None, ident: Optional[int] = None, color: Optional[int] = None,
                     thumbnail: Optional[str] = None, **kw) -> bool:
        s = await self.settings(guild.id)
        if event in s.disabled_events:
            return False
        file = kw.get("file")
        if file is not None:  # a message that carries an attachment (ticket transcripts) stays a classic embed + file
            channel = guild.get_channel(s.channel_id(channel_for(event)) or 0)
            if not isinstance(channel, discord.TextChannel):
                return False
            try:
                await channel.send(embed=build_embed(title=plain(title, 250), description=plain(description, 3500), fields=self._fields(fields),
                                                     color=self.EVENT_COLORS.get(event, branding.ACCENT_COLOR)),
                                   file=file, allowed_mentions=NO_MENTIONS)
                return True
            except discord.HTTPException:
                log.warning("could not write a log attachment in guild %s", guild.id)
                return False
        return await self._send(guild, s.channel_id(channel_for(event)), title=title, description=description, fields=fields,
                                color=color if color is not None else self.EVENT_COLORS.get(event, branding.ACCENT_COLOR), author=author, ident=ident,
                                thumbnail=thumbnail)

    async def security(self, guild: discord.Guild, title: str, description: str = "",
                       fields: Optional[list[tuple[str, str]]] = None, *, urgent: bool = False) -> bool:
        """Security alert. Only an `urgent` one pings, and only the roles chosen with the alertrole command."""
        s = await self.settings(guild.id)
        text, allowed = None, NO_MENTIONS
        if urgent:
            roles = [guild.get_role(r) for r in s.alert_role_ids]
            roles = [r for r in roles if r is not None]
            if roles:
                text = " ".join(r.mention for r in roles)
                allowed = discord.AllowedMentions(roles=roles)
        return await self._send(guild, s.security_channel_id, title=title, description=description, fields=fields, color=ERROR if urgent else WARNING,
                                urgent_text=text, allowed=allowed)

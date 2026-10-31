"""Logging module: exactly two channels, #logs for ordinary server events and #security-logs for security incidents."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core_checks import ensure_security_admin
from core_embeds import error_embed, info_embed, ok_embed
from core_errors import InvalidInput
from core_log_routing import CHANNELS, CHANNEL_NAMES, DeleteEntry, pick_deleter
from svc_log_service import SERVER_EVENTS, plain, who
from view_confirm import ask

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.logging")
CATEGORY_NAME = "Logs"


class LoggingModule(commands.Cog, name="Logging"):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self._tasks: set = set()  # keeps background log tasks alive until they finish

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ensure_security_admin(ctx)

    # --- setup -------------------------------------------------------------------------------
    def _existing(self, guild: discord.Guild, key: str) -> Optional[discord.TextChannel]:
        return discord.utils.get(guild.text_channels, name=CHANNEL_NAMES[key])

    def plan_setup(self, guild: discord.Guild) -> list[str]:
        have = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        lines = [f"reuse the **{CATEGORY_NAME}** category" if have else f"create a private **{CATEGORY_NAME}** category"]
        for key in CHANNELS:
            lines.append(f"reuse #{CHANNEL_NAMES[key]}" if self._existing(guild, key) else f"create #{CHANNEL_NAMES[key]}")
        lines.append("new channels are private: only Administrators and I can see them")
        if not guild.me.guild_permissions.manage_channels:
            lines.append("⚠ I lack Manage Channels, so I can't create missing channels")
        return lines

    async def apply_setup(self, guild: discord.Guild) -> list[str]:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True),
        }
        results, ids = [], {}
        results.append("old per-topic log channels (message-logs, member-logs ...) are left alone; I just don't use them any more")
        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        try:
            if category is None:
                category = await guild.create_category(CATEGORY_NAME, overwrites=overwrites, reason="HOPELESS logging setup")
                results.append(f"created the {CATEGORY_NAME} category")
        except discord.HTTPException as exc:
            category = None
            results.append(f"could not create the category (HTTP {exc.status}); channels go at the top level")
        for key in CHANNELS:
            channel = self._existing(guild, key)
            if channel is None:
                try:
                    channel = await guild.create_text_channel(CHANNEL_NAMES[key], category=category, overwrites=overwrites, reason="HOPELESS logging setup")
                    results.append(f"created #{CHANNEL_NAMES[key]}")
                except discord.HTTPException as exc:
                    results.append(f"could not create #{CHANNEL_NAMES[key]} (HTTP {exc.status})")
                    continue
            ids[key] = channel.id
        if ids:
            await self.bot.logs.save_channels(guild.id, ids.get("server"), ids.get("security"), {})
            results.append(f"connected #{CHANNEL_NAMES['server']} and #{CHANNEL_NAMES['security']}")
        return results

    @commands.hybrid_command(name="setuplogs", description="Create the private #logs and #security-logs channels")
    @commands.guild_only()
    async def setuplogs(self, ctx: commands.Context) -> None:
        embed = info_embed("Logging setup", "\n".join(f"• {line}" for line in self.plan_setup(ctx.guild)))
        if await ask(ctx, embed) != 0:
            await ctx.send(embed=info_embed(None, "Cancelled. Nothing was changed."))
            return
        await ctx.send(embed=ok_embed("\n".join(await self.apply_setup(ctx.guild)), title="Logging Setup Complete"))

    @commands.hybrid_command(name="logevents", description="Turn a server-log event on or off, or list them")
    @app_commands.describe(event="Event name, or leave empty to list", state="on or off")
    @commands.guild_only()
    async def logevents(self, ctx: commands.Context, event: Optional[str] = None, state: Optional[str] = None) -> None:
        settings = await self.bot.logs.settings(ctx.guild.id)
        if event is None or state is None:
            lines = [f"`{e}`: {'off' if e in settings.disabled_events else 'on'}" for e in SERVER_EVENTS]
            await ctx.send(embed=info_embed("Server log events", "\n".join(lines) + "\n\nChange one with `logevents <event> on|off`."))
            return
        event = event.lower()
        if event not in SERVER_EVENTS or state.lower() not in ("on", "off"):
            await ctx.send(embed=error_embed("Use an event from the list and `on` or `off`."), ephemeral=True)
            return
        await self.bot.logs.set_event(ctx.guild.id, event, state.lower() == "on")
        await ctx.send(embed=ok_embed(f"`{event}` logging is now {state.lower()}."))

    # --- listeners ---------------------------------------------------------------------------
    async def _skip_channel(self, guild: discord.Guild, channel_id: int) -> bool:
        return await self.bot.logs.is_log_channel(guild.id, channel_id)

    async def _deleter_id(self, guild: discord.Guild, author_id: int, channel_id: int) -> Optional[int]:
        """Who deleted a message, from a matching audit-log entry only (author + channel + timing). None means Unknown."""
        if not guild.me.guild_permissions.view_audit_log:
            return None
        entries = []
        try:
            async for entry in guild.audit_logs(limit=8, action=discord.AuditLogAction.message_delete):
                extra_channel = getattr(getattr(entry, "extra", None), "channel", None)
                entries.append(DeleteEntry(entry.id, entry.user_id or 0, getattr(entry.target, "id", None),
                                           getattr(extra_channel, "id", None), entry.created_at.timestamp()))
        except discord.HTTPException:
            return None
        return pick_deleter(entries, author_id=author_id, channel_id=channel_id, now_ts=time.time())

    async def _log_delete(self, message: discord.Message) -> None:
        guild = message.guild
        await asyncio.sleep(1.5)  # the audit-log entry appears a moment after the event
        deleter_id = await self._deleter_id(guild, message.author.id, message.channel.id)
        deleter = guild.get_member(deleter_id) if deleter_id else None
        text = plain(message.content, 1500) if message.content else "(no text)"
        extras = [("Attachments", str(len(message.attachments)))] if message.attachments else []
        await self.bot.logs.server(
            guild, "message_delete", f"Message deleted in #{plain(message.channel.name, 80)}", text, author=message.author, ident=message.author.id,
            fields=[("Message ID", str(message.id)), ("Deleted by", who(deleter) if deleter else (f"user {deleter_id}" if deleter_id else "Unknown")), *extras])

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or await self._skip_channel(message.guild, message.channel.id):
            return
        task = asyncio.create_task(self._log_delete(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        if await self._skip_channel(before.guild, before.channel.id):
            return
        await self.bot.logs.server(before.guild, "message_edit", f"Message edited in #{plain(before.channel.name, 80)}", author=before.author,
                                   ident=before.author.id, fields=[("Before", before.content or "(empty)"), ("After", after.content or "(empty)"),
                                                                    ("Message ID", str(before.id))])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.bot.logs.server(member.guild, "member_join", "Member joined", f"{plain(member.name, 80)} joined the server.", author=member,
                                   ident=member.id, fields=[("Account created", discord.utils.format_dt(member.created_at, "R"))])

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.bot.logs.server(member.guild, "member_leave", "Member left", f"{plain(member.name, 80)} left the server.", author=member, ident=member.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        changes = []
        if before.nick != after.nick:
            changes.append(("Nickname", f"{plain(before.nick)} → {plain(after.nick)}"))
        added = [r.name for r in after.roles if r not in before.roles]
        removed = [r.name for r in before.roles if r not in after.roles]
        if added:
            changes.append(("Roles added", ", ".join(added)))
        if removed:
            changes.append(("Roles removed", ", ".join(removed)))
        if changes:
            await self.bot.logs.server(after.guild, "member_update", "Member updated", author=after, ident=after.id, fields=changes)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self.bot.logs.server(role.guild, "role_create", "Role created", fields=[("Role", f"{plain(role.name)} ({role.id})")])

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self.bot.logs.server(role.guild, "role_delete", "Role deleted", fields=[("Role", f"{plain(role.name)} ({role.id})")])

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Name", f"{plain(before.name)} → {plain(after.name)}"))
        if before.permissions != after.permissions:
            changes.append(("Permissions", f"{before.permissions.value} → {after.permissions.value}"))
        if before.color != after.color:
            changes.append(("Colour", f"{before.color} → {after.color}"))
        if changes:
            await self.bot.logs.server(after.guild, "role_update", "Role updated", fields=[("Role", f"{plain(after.name)} ({after.id})"), *changes])

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if await self._skip_channel(channel.guild, channel.id):
            return
        await self.bot.logs.server(channel.guild, "channel_create", "Channel created", fields=[("Channel", f"{plain(channel.name)} ({channel.id})")])

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if await self._skip_channel(channel.guild, channel.id):
            return
        await self.bot.logs.server(channel.guild, "channel_delete", "Channel deleted", fields=[("Channel", f"{plain(channel.name)} ({channel.id})")])

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if before.name == after.name or await self._skip_channel(after.guild, after.id):
            return
        await self.bot.logs.server(after.guild, "channel_update", "Channel renamed", fields=[("Channel", f"{plain(before.name)} → {plain(after.name)} ({after.id})")])

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if before.channel == after.channel or member.bot:
            return
        name = plain(member.name, 60)
        if before.channel is None:
            title, text, color = "Member joined voice channel", f"**{name}** joined #{plain(after.channel.name)}", 0x2E9E5B
        elif after.channel is None:
            title, text, color = "Member left voice channel", f"**{name}** left #{plain(before.channel.name)}", 0xC0262D
        else:
            title, text, color = "Member moved voice channel", f"**{name}** moved from #{plain(before.channel.name)} to #{plain(after.channel.name)}", 0x3B6FD4
        await self.bot.logs.server(member.guild, "voice", title, text, author=member, ident=member.id, color=color)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        """Avatar changes, shown in every server that shares this member and has logs set up."""
        if before.display_avatar == after.display_avatar or after.bot:
            return
        for guild in after.mutual_guilds:
            await self.bot.logs.server(guild, "avatar", "Avatar update", f"{after.mention}", author=after, ident=after.id,
                                       thumbnail=after.display_avatar.url, color=0x3B6FD4)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after) -> None:
        added = [e.name for e in after if e not in before]
        removed = [e.name for e in before if e not in after]
        if added or removed:
            await self.bot.logs.server(guild, "emoji", "Emojis changed", fields=[
                ("Added", ", ".join(added) or "none"), ("Removed", ", ".join(removed) or "none")])

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            await self.bot.logs.server(invite.guild, "invite", "Invite created", fields=[
                ("Code", invite.code), ("By", who(invite.inviter) if invite.inviter else "unknown"),
            ])

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            await self.bot.logs.server(invite.guild, "invite", "Invite deleted", fields=[("Code", invite.code)])

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self.bot.logs.server(guild, "moderation", "Member banned", f"{plain(user.name, 80)} was banned.", author=user, ident=user.id)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        await self.bot.logs.server(guild, "moderation", "Member unbanned", f"{plain(user.name, 80)} was unbanned.", author=user, ident=user.id)


    @commands.hybrid_group(name="alertrole", fallback="list", description="Roles pinged for urgent security alerts")
    @commands.guild_only()
    async def alertrole(self, ctx: commands.Context) -> None:
        roles = (await self.bot.logs.settings(ctx.guild.id)).alert_role_ids
        text = "\n".join(f"• <@&{r}>" for r in roles) or "None yet."
        await ctx.send(embed=info_embed("Security alert roles", text + "\n\nOnly urgent alerts ping these roles: the same person acting twice in a short time, or a response. "
                                        "A single incident is only recorded, and server logs never ping.\nChange the list with `alertrole add @role` and `alertrole remove @role`."))

    @alertrole.command(name="add", description="Ping this role for urgent security alerts")
    async def alertrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        if role.is_default():
            raise InvalidInput("I won't ping @everyone. Pick a staff role.")
        current = list((await self.bot.logs.settings(ctx.guild.id)).alert_role_ids)
        if role.id in current:
            raise InvalidInput(f"{role.mention} is already on the list.")
        if len(current) >= 5:
            raise InvalidInput("You can have up to 5 alert roles. Remove one first.")
        await self.bot.logs.set_alert_roles(ctx.guild.id, current + [role.id])
        await ctx.send(embed=ok_embed(f"{role.mention} will be pinged for urgent security alerts."))

    @alertrole.command(name="remove", description="Stop pinging this role")
    async def alertrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        current = list((await self.bot.logs.settings(ctx.guild.id)).alert_role_ids)
        if role.id not in current:
            raise InvalidInput(f"{role.mention} isn't on the list.")
        current.remove(role.id)
        await self.bot.logs.set_alert_roles(ctx.guild.id, current)
        await ctx.send(embed=ok_embed(f"{role.mention} is no longer pinged."))


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(LoggingModule(bot))

"""Reputation: a role while a member "reps" the server, and the role is taken away again when they stop.

Two conditions, each with its own role and log channel:
  * Status rep: the member's custom status contains one of the target texts (for example /hopeless).
  * Tag rep: the member wears THIS server's tag on their profile.

Honest limits (Discord's, not ours):
  * Reading custom statuses needs the Presence Intent: turn it on in the Developer Portal AND set ENABLE_PRESENCE_INTENT=1.
  * Server tags are read from the profile data Discord sends with members and users; if this discord.py version or
    Discord doesn't send it for someone, the tag can't be seen and nothing is changed for them.
  * A member who shows as offline or invisible has no readable status, so they are never penalised for it.
A background pass re-checks everyone the bot gave a rep role to, so a missed event can't leave a role behind forever.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core_branding as branding
from core_checks import server_admin
from core_embeds import ERROR, SILVER, SUCCESS, WARNING
from core_errors import InvalidInput
from core_rep_logic import MAX_TARGETS, MAX_TARGET_LEN, normalize_target, plan, status_matches, tag_matches
from core_ui_v2 import send_note
from svc_log_service import plain

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.rep")
KINDS = ("status", "tag")


@dataclass
class RepConfig:
    status_enabled: bool = False
    status_role_id: Optional[int] = None
    status_log_id: Optional[int] = None
    targets: list[str] = field(default_factory=list)
    tag_enabled: bool = False
    tag_role_id: Optional[int] = None
    tag_log_id: Optional[int] = None


def custom_status_text(member: discord.Member) -> Optional[str]:
    for activity in member.activities:
        if isinstance(activity, discord.CustomActivity):
            return activity.name or getattr(activity, "state", None)
    return None


def wears_tag(member: discord.Member) -> Optional[bool]:
    """True/False when the tag information is available, None when this discord.py or Discord doesn't provide it."""
    if not hasattr(member, "primary_guild"):
        return None
    guild_tag = member.primary_guild
    if guild_tag is None:
        return False
    return tag_matches(getattr(guild_tag, "identity_enabled", None), getattr(guild_tag, "identity_guild_id", None), member.guild.id)


class Rep(commands.Cog, name="Reputation"):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self._cfg: dict[int, RepConfig] = {}

    async def cog_load(self) -> None:
        self.enforcer.start()

    async def cog_unload(self) -> None:
        self.enforcer.cancel()

    # ------------------------------------------------------------------------- settings
    async def config(self, guild_id: int) -> RepConfig:
        cached = self._cfg.get(guild_id)
        if cached is None:
            row = await self.bot.db.fetchrow("SELECT * FROM rep_settings WHERE guild_id = $1", guild_id)
            cached = RepConfig(row["status_enabled"], row["status_role_id"], row["status_log_id"], list(row["status_targets"]),
                               row["tag_enabled"], row["tag_role_id"], row["tag_log_id"]) if row else RepConfig()
            self._cfg[guild_id] = cached
        return cached

    async def save(self, guild_id: int, **fields) -> None:
        allowed = {"status_enabled", "status_role_id", "status_log_id", "status_targets", "tag_enabled", "tag_role_id", "tag_log_id"}
        assert set(fields) <= allowed
        names = list(fields)
        marks = ", ".join(f"${i + 2}" for i in range(len(names)))
        sets = ", ".join(f"{n} = EXCLUDED.{n}" for n in names)
        async with self.bot.db.transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute(f"INSERT INTO rep_settings (guild_id, {', '.join(names)}) VALUES ($1, {marks}) ON CONFLICT (guild_id) DO UPDATE SET {sets}",
                               guild_id, *fields.values())
        self._cfg.pop(guild_id, None)

    @property
    def presence_on(self) -> bool:
        return bool(self.bot.intents.presences)

    # ------------------------------------------------------------------------- the core: grant and remove
    def _role_for(self, cfg: RepConfig, kind: str) -> Optional[int]:
        return cfg.status_role_id if kind == "status" else cfg.tag_role_id

    async def _log(self, guild: discord.Guild, kind: str, cfg: RepConfig, text: str, color: int) -> None:
        channel_id = cfg.status_log_id if kind == "status" else cfg.tag_log_id
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await send_note(channel, text, color=color)
            except discord.HTTPException:
                pass

    async def apply(self, member: discord.Member, kind: str, qualifies: bool, cfg: RepConfig) -> Optional[str]:
        """Bring one member's role in line with one condition. Returns 'granted', 'removed' or None."""
        guild = member.guild
        role_id = self._role_for(cfg, kind)
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            return None
        tracked = bool(await self.bot.db.fetchval("SELECT 1 FROM rep_members WHERE guild_id = $1 AND user_id = $2 AND kind = $3", guild.id, member.id, kind))
        other = "tag" if kind == "status" else "status"
        other_keeps = self._role_for(cfg, other) == role_id and bool(
            await self.bot.db.fetchval("SELECT 1 FROM rep_members WHERE guild_id = $1 AND user_id = $2 AND kind = $3", guild.id, member.id, other))
        change = plan(qualifies=qualifies, tracked=tracked, has_role=role in member.roles, other_condition_keeps_role=other_keeps)
        me = guild.me
        what = "status" if kind == "status" else "server tag"
        if change.grant:
            if not me.guild_permissions.manage_roles or role >= me.top_role or role.managed:
                await self.bot.logs.security(guild, "Rep role could not be given", f"{role.mention} is above my role, or I lack Manage Roles.", [("Member", member.mention)])
                return None
            try:
                await member.add_roles(role, reason=f"rep: {what}")
            except discord.HTTPException:
                log.info("rep grant failed for %s in %s", member.id, guild.id)
                return None
            await self.bot.db.execute("INSERT INTO rep_members (guild_id, user_id, kind) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", guild.id, member.id, kind)
            try:  # a short thank-you; a closed DM never blocks the role
                await member.send(f"Thanks for repping **{plain(guild.name, 80)}** with your {what}! You've been given the **{plain(role.name, 60)}** role.")
            except discord.HTTPException:
                pass
            await self._log(guild, kind, cfg, f"{branding.WHITECHECK} {member.mention} now reps with their {what} and received {role.mention}.", SUCCESS)
            return "granted"
        if change.remove:
            try:
                await member.remove_roles(role, reason=f"rep: {what} removed")
            except discord.HTTPException:
                log.info("rep removal failed for %s in %s", member.id, guild.id)
                return None
            await self.bot.db.execute("DELETE FROM rep_members WHERE guild_id = $1 AND user_id = $2 AND kind = $3", guild.id, member.id, kind)
            await self._log(guild, kind, cfg, f"{member.mention} stopped repping with their {what}, so {role.mention} was removed.", WARNING)  # no DM on removal
            return "removed"
        if not qualifies and tracked:  # they don't qualify and the role is already gone (or another condition keeps it): just stop tracking this one
            await self.bot.db.execute("DELETE FROM rep_members WHERE guild_id = $1 AND user_id = $2 AND kind = $3", guild.id, member.id, kind)
        return None

    async def evaluate(self, member: discord.Member) -> list[str]:
        """Re-check one member against every active condition."""
        if member.bot:
            return []
        cfg = await self.config(member.guild.id)
        results = []
        if cfg.status_enabled and cfg.status_role_id and cfg.targets and self.presence_on and member.status is not discord.Status.offline:
            results.append(await self.apply(member, "status", status_matches(custom_status_text(member), cfg.targets), cfg))
        if cfg.tag_enabled and cfg.tag_role_id:
            wearing = wears_tag(member)
            if wearing is not None:
                results.append(await self.apply(member, "tag", wearing, cfg))
        return [r for r in results if r]

    # ------------------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.bot or after.guild is None:
            return
        cfg = await self.config(after.guild.id)
        if not (cfg.status_enabled and cfg.targets):
            return
        if custom_status_text(before) == custom_status_text(after) and before.status == after.status:
            return
        await self.evaluate(after)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        cfg = await self.config(after.guild.id)
        if cfg.tag_enabled and wears_tag(before) != wears_tag(after):
            await self.evaluate(after)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        if getattr(before, "primary_guild", None) == getattr(after, "primary_guild", None):
            return
        for guild in after.mutual_guilds:
            cfg = await self.config(guild.id)
            member = guild.get_member(after.id)
            if cfg.tag_enabled and member is not None:
                await self.evaluate(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.bot.db.execute("DELETE FROM rep_members WHERE guild_id = $1 AND user_id = $2", member.guild.id, member.id)

    @tasks.loop(minutes=10)
    async def enforcer(self) -> None:
        """Re-check everyone the bot gave a rep role to, so a missed event can't leave a role behind."""
        try:
            rows = await self.bot.db.fetch("SELECT guild_id, user_id, kind FROM rep_members")
            for row in rows:
                guild = self.bot.get_guild(row["guild_id"])
                member = guild.get_member(row["user_id"]) if guild else None
                if guild is None:
                    continue
                if member is None:
                    await self.bot.db.execute("DELETE FROM rep_members WHERE guild_id = $1 AND user_id = $2", row["guild_id"], row["user_id"])
                    continue
                await self.evaluate(member)
        except Exception:
            log.warning("the rep enforcement pass failed", exc_info=True)

    @enforcer.before_loop
    async def _before_enforcer(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------------- status commands
    @commands.hybrid_command(name="setreprole", description="Set the role given for status rep")
    @app_commands.describe(role="The role members get while their status matches")
    @commands.guild_only()
    @server_admin()
    async def setreprole(self, ctx: commands.Context, role: discord.Role) -> None:
        self._check_role(ctx, role)
        await self.save(ctx.guild.id, status_role_id=role.id)
        cfg = await self.config(ctx.guild.id)
        running = cfg.status_enabled and bool(cfg.targets)
        lines = [f"Rep role set to {role.mention} (`{plain(role.name, 60)}`)", f"System: `{'Running' if running else 'Paused'}`"]
        if not cfg.targets:
            lines.append("Add at least one target keyword before the system can run.")
        await send_note(ctx, "\n".join(lines), color=SUCCESS)

    @commands.hybrid_group(name="setreptarget", fallback="list", invoke_without_command=True, description="Show the status targets")
    @commands.guild_only()
    @server_admin()
    async def setreptarget(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        await send_note(ctx, f"**Targets** (`{len(cfg.targets)}`)\n" + ("\n".join(f"`{plain(t, 60)}`" for t in cfg.targets) or "None. Add one with `setreptarget add <text>`."), color=SILVER)

    @setreptarget.command(name="add", description="Add a status target")
    @app_commands.describe(text="Text a custom status must contain, like /hopeless")
    @server_admin()
    async def setreptarget_add(self, ctx: commands.Context, *, text: str) -> None:
        target = normalize_target(text)
        if not target or len(target) > MAX_TARGET_LEN:
            raise InvalidInput(f"A target must be 1 to {MAX_TARGET_LEN} characters.")
        cfg = await self.config(ctx.guild.id)
        if target in cfg.targets:
            raise InvalidInput("That target is already on the list.")
        if len(cfg.targets) >= MAX_TARGETS:
            raise InvalidInput(f"You can have up to {MAX_TARGETS} targets.")
        targets = [*cfg.targets, target]
        await self.save(ctx.guild.id, status_targets=targets)
        await send_note(ctx, f"Added target `{plain(target, 60)}`\nTotal targets: `{len(targets)}`\nStatuses containing this text will match.", color=SUCCESS)

    @setreptarget.command(name="remove", description="Remove a status target")
    @app_commands.describe(text="The target to remove")
    @server_admin()
    async def setreptarget_remove(self, ctx: commands.Context, *, text: str) -> None:
        target = normalize_target(text)
        cfg = await self.config(ctx.guild.id)
        if target not in cfg.targets:
            raise InvalidInput("That isn't one of the targets.")
        targets = [t for t in cfg.targets if t != target]
        await self.save(ctx.guild.id, status_targets=targets, **({"status_enabled": False} if not targets else {}))
        extra = "" if targets else "\nNo targets are left, so status rep was turned off."
        await send_note(ctx, f"Removed target `{plain(target, 60)}`\nTotal targets: `{len(targets)}`{extra}", color=SUCCESS)

    async def _status_panel(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        role = ctx.guild.get_role(cfg.status_role_id) if cfg.status_role_id else None
        log_channel = f"<#{cfg.status_log_id}>" if cfg.status_log_id else "`None`"
        text = [f"## Rep Status", f"**Status:** {'`On`' if cfg.status_enabled else '`Off`'}",
                f"**Role:** {role.mention + ' (`' + plain(role.name, 40) + '`)' if role else '`None`'}", f"**Log:** {log_channel}",
                f"**Targets** (`{len(cfg.targets)}`)", ("\n".join(f"› `{plain(t, 60)}`" for t in cfg.targets) or "› `None`"),
                f"`{ctx.clean_prefix}rep enable` / `{ctx.clean_prefix}rep disable`"]
        if not self.presence_on:
            text.append("⚠ The Presence Intent is off, so statuses can't be read. Turn it on in the Developer Portal and set `ENABLE_PRESENCE_INTENT=1`.")
        await send_note(ctx, "\n".join(text), color=SILVER)

    @commands.hybrid_command(name="repstatus", description="View the status rep configuration")
    @commands.guild_only()
    @server_admin()
    async def repstatus(self, ctx: commands.Context) -> None:
        await self._status_panel(ctx)

    @commands.hybrid_group(name="rep", fallback="status", invoke_without_command=True, description="View status rep, or turn it on or off")
    @commands.guild_only()
    @server_admin()
    async def rep(self, ctx: commands.Context) -> None:
        await self._status_panel(ctx)

    @rep.command(name="enable", description="Turn status rep on")
    @server_admin()
    async def rep_enable(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        if not cfg.status_role_id or ctx.guild.get_role(cfg.status_role_id) is None:
            await send_note(ctx, f"Can't enable: no rep role set.\nSet one first with `{ctx.clean_prefix}setreprole @role`", color=ERROR)
            return
        if not cfg.targets:
            await send_note(ctx, f"Can't enable: no targets set.\nAdd one with `{ctx.clean_prefix}setreptarget add <text>`", color=WARNING)
            return
        if not self.presence_on:
            await send_note(ctx, "Can't enable: I can't read statuses yet.\nTurn on **Presence Intent** in the Discord Developer Portal (Bot, then Privileged Gateway Intents), "
                                 "then set the variable `ENABLE_PRESENCE_INTENT` to `1` where the bot runs and restart it.", color=WARNING)
            return
        await self.save(ctx.guild.id, status_enabled=True)
        await send_note(ctx, f"{branding.WHITECHECK} Status rep is on. Use `{ctx.clean_prefix}repscan` to catch up members who already have it.", color=SUCCESS)

    @rep.command(name="disable", description="Turn status rep off")
    @server_admin()
    async def rep_disable(self, ctx: commands.Context) -> None:
        await self.save(ctx.guild.id, status_enabled=False)
        await send_note(ctx, "Status rep is off. Roles already given stay until you remove them.", color=SILVER)

    @commands.hybrid_command(name="setreplog", description="Set the channel for status rep logs")
    @app_commands.describe(channel="Where rep grants and removals are logged")
    @commands.guild_only()
    @server_admin()
    async def setreplog(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.save(ctx.guild.id, status_log_id=channel.id)
        await send_note(ctx, f"{branding.WHITECHECK} Status rep logs go to {channel.mention}.", color=SUCCESS)

    @commands.hybrid_command(name="repcheck", description="Check one member's status rep")
    @app_commands.describe(member="Who to check")
    @commands.guild_only()
    @server_admin()
    async def repcheck(self, ctx: commands.Context, member: discord.Member) -> None:
        cfg = await self.config(ctx.guild.id)
        role = ctx.guild.get_role(cfg.status_role_id) if cfg.status_role_id else None
        text = custom_status_text(member)
        matches = status_matches(text, cfg.targets)
        tracked = bool(await self.bot.db.fetchval("SELECT 1 FROM rep_members WHERE guild_id = $1 AND user_id = $2 AND kind = 'status'", ctx.guild.id, member.id))
        lines = [f"**{discord.utils.escape_markdown(member.display_name)}**", f"Custom status: {('`' + plain(text, 80) + '`') if text else '`none visible`'}",
                 f"Matches a target: `{'yes' if matches else 'no'}`", f"Has the role: `{'yes' if role and role in member.roles else 'no'}`", f"Given by me: `{'yes' if tracked else 'no'}`"]
        if not self.presence_on:
            lines.append("⚠ Presence Intent is off, so statuses aren't visible to me.")
        await send_note(ctx, "\n".join(lines), color=SILVER)

    @commands.hybrid_command(name="repscan", description="Scan online members for status rep and fix roles")
    @commands.guild_only()
    @server_admin()
    async def repscan(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        if not (cfg.status_enabled and self.presence_on):
            raise InvalidInput("Status rep isn't running. Turn it on with `rep enable` first.")
        granted = removed = 0
        for member in list(ctx.guild.members):
            for outcome in await self.evaluate(member):
                granted += outcome == "granted"
                removed += outcome == "removed"
        await send_note(ctx, f"{branding.WHITECHECK} Scan done: **{granted}** given, **{removed}** removed. Offline members can't be read and were skipped.", color=SUCCESS)

    # ------------------------------------------------------------------------- tag commands
    @commands.hybrid_command(name="settagrole", description="Set the role given for wearing this server's tag")
    @app_commands.describe(role="The role members get while they wear the tag")
    @commands.guild_only()
    @server_admin()
    async def settagrole(self, ctx: commands.Context, role: discord.Role) -> None:
        self._check_role(ctx, role)
        await self.save(ctx.guild.id, tag_role_id=role.id)
        await send_note(ctx, f"Tag role set to {role.mention} (`{plain(role.name, 60)}`)\nTurn it on with `{ctx.clean_prefix}reptag enable`.", color=SUCCESS)

    async def _tag_panel(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        role = ctx.guild.get_role(cfg.tag_role_id) if cfg.tag_role_id else None
        text = ["## Server Tag Rep", f"**Status:** {'`On`' if cfg.tag_enabled else '`Off`'}",
                f"**Role:** {role.mention + ' (`' + plain(role.name, 40) + '`)' if role else '`None`'}",
                f"**Log:** {('<#' + str(cfg.tag_log_id) + '>') if cfg.tag_log_id else '`None`'}",
                f"`{ctx.clean_prefix}reptag enable` / `{ctx.clean_prefix}reptag disable`"]
        sample = next((m for m in ctx.guild.members if not m.bot), None)
        if sample is not None and wears_tag(sample) is None:
            text.append("⚠ This discord.py version doesn't expose server tags, so tag rep can't work until it is updated.")
        await send_note(ctx, "\n".join(text), color=SILVER)

    @commands.hybrid_group(name="reptag", fallback="status", invoke_without_command=True, description="View server tag rep, or turn it on or off")
    @commands.guild_only()
    @server_admin()
    async def reptag(self, ctx: commands.Context) -> None:
        await self._tag_panel(ctx)

    @reptag.command(name="enable", description="Turn server tag rep on")
    @server_admin()
    async def reptag_enable(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        if not cfg.tag_role_id or ctx.guild.get_role(cfg.tag_role_id) is None:
            await send_note(ctx, f"Can't enable: no tag role set.\nSet one first with `{ctx.clean_prefix}settagrole @role`", color=ERROR)
            return
        await self.save(ctx.guild.id, tag_enabled=True)
        await send_note(ctx, f"{branding.WHITECHECK} Server tag rep is on. Use `{ctx.clean_prefix}tagscan` to catch up members who already wear it.", color=SUCCESS)

    @reptag.command(name="disable", description="Turn server tag rep off")
    @server_admin()
    async def reptag_disable(self, ctx: commands.Context) -> None:
        await self.save(ctx.guild.id, tag_enabled=False)
        await send_note(ctx, "Server tag rep is off. Roles already given stay until you remove them.", color=SILVER)

    @commands.hybrid_command(name="settaglog", description="Set the channel for server tag rep logs")
    @app_commands.describe(channel="Where tag rep grants and removals are logged")
    @commands.guild_only()
    @server_admin()
    async def settaglog(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.save(ctx.guild.id, tag_log_id=channel.id)
        await send_note(ctx, f"{branding.WHITECHECK} Tag rep logs go to {channel.mention}.", color=SUCCESS)

    @commands.hybrid_command(name="tagcheck", description="Check whether a member wears this server's tag")
    @app_commands.describe(member="Who to check")
    @commands.guild_only()
    @server_admin()
    async def tagcheck(self, ctx: commands.Context, member: discord.Member) -> None:
        wearing = wears_tag(member)
        answer = "`I can't see tag data in this setup`" if wearing is None else f"`{'yes' if wearing else 'no'}`"
        await send_note(ctx, f"**{discord.utils.escape_markdown(member.display_name)}** wears this server's tag: {answer}", color=SILVER)

    @commands.hybrid_command(name="tagscan", description="Scan members for this server's tag and fix roles")
    @commands.guild_only()
    @server_admin()
    async def tagscan(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        if not cfg.tag_enabled:
            raise InvalidInput("Server tag rep isn't on. Turn it on with `reptag enable` first.")
        granted = removed = unknown = 0
        for member in list(ctx.guild.members):
            if member.bot:
                continue
            if wears_tag(member) is None:
                unknown += 1
                continue
            for outcome in await self.evaluate(member):
                granted += outcome == "granted"
                removed += outcome == "removed"
        extra = f" {unknown} members had no readable tag data." if unknown else ""
        await send_note(ctx, f"{branding.WHITECHECK} Scan done: **{granted}** given, **{removed}** removed.{extra}", color=SUCCESS)

    # ------------------------------------------------------------------------- shared
    def _check_role(self, ctx: commands.Context, role: discord.Role) -> None:
        if role.is_default() or role.managed:
            raise InvalidInput("Pick a normal role (not @everyone or a bot role).")
        if role >= ctx.guild.me.top_role:
            raise InvalidInput("That role is at or above my top role. Move my role above it in Server Settings, then Roles.")
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            raise InvalidInput("You can't set a role that is at or above your own top role.")


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Rep(bot))

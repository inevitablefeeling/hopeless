"""Prestige: per-server access (paid with internal credits), per-server bot profile, and tag roles.

Credits are internal numbers only. Per-server bot avatar, banner and bio depend on what Discord allows
bots to do; if Discord refuses, the command says so and nothing is reported as changed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Union

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from core_checks import owner_only
from core_embeds import error_embed, info_embed, ok_embed
from core_errors import InvalidInput, NotAuthorized
from core_prestige_logic import enable_cost, image_data_uri, tag_matches, validate_grant

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.prestige")
Target = Union[commands.Context, discord.Interaction]
_ENSURE = "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING"


def _actor(target: Target):
    return target.user if isinstance(target, discord.Interaction) else target.author


async def _reply(target: Target, embed: discord.Embed) -> None:
    if isinstance(target, discord.Interaction):
        if target.response.is_done():
            await target.followup.send(embed=embed, ephemeral=True)
        else:
            await target.response.send_message(embed=embed, ephemeral=True)
    else:
        await target.send(embed=embed)


class Prestige(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self._active: set[int] = set()
        self._tags: dict[int, Optional[tuple[int, str]]] = {}

    async def cog_load(self) -> None:
        rows = await self.bot.db.fetch("SELECT guild_id FROM prestige_servers")
        self._active = {r["guild_id"] for r in rows}

    # --- rules -------------------------------------------------------------------------------
    def _require_admin(self, target: Target) -> None:
        """Editing the bot's profile here and tag roles: the server owner (the bot owner may help too)."""
        member, guild = _actor(target), target.guild
        if guild is None:
            raise commands.NoPrivateMessage()
        if guild.owner_id != member.id and not self.bot.permissions.is_owner(member.id):
            raise NotAuthorized("Only the server owner can change the bot's profile or tag roles here.")

    def _require_global(self, target: Target) -> None:
        uid = _actor(target).id
        if not self.bot.globals.has_perm(uid, "prestige"):  # the owner always; administrators while they hold the prestige permission
            raise NotAuthorized("Only the bot owner, or a global administrator with the `prestige` permission, can do that.")

    def _require_active(self, guild: discord.Guild) -> None:
        if guild.id not in self._active:
            raise InvalidInput("This needs Prestige. Ask the bot owner or a bot admin to activate it for this server.")

    # --- credits -----------------------------------------------------------------------------
    async def _grant(self, target: Target, user: discord.User, amount: int) -> None:
        if not self.bot.permissions.is_owner(_actor(target).id):
            raise NotAuthorized("Only the bot owner can grant credits.")
        amount = validate_grant(amount)
        balance = await self.bot.db.fetchval(
            "INSERT INTO prestige_credits (user_id, balance) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET balance = prestige_credits.balance + $2 RETURNING balance",
            user.id, amount)
        log.info("credits granted user=%s amount=%s by=%s", user.id, amount, _actor(target).id)
        await _reply(target, ok_embed(f"Granted {amount} credit(s) to {user.mention}. Their balance is {balance}."))

    async def _balance(self, target: Target) -> None:
        balance = await self.bot.db.fetchval("SELECT balance FROM prestige_credits WHERE user_id = $1", _actor(target).id) or 0
        await _reply(target, info_embed("Your Prestige credits", f"You have **{balance}** credit(s). Enabling Prestige for a server costs 1."))

    # --- access ------------------------------------------------------------------------------
    async def _add(self, target: Target, guild_id: Optional[int] = None) -> None:
        """Activating Prestige is for the bot owner or a bot admin only (a bot admin spends one granted credit)."""
        self._require_global(target)
        uid = _actor(target).id
        gid = guild_id or (target.guild.id if target.guild else None)
        if gid is None:
            raise InvalidInput("Run this in a server, or give a server id.")
        if gid in self._active:
            await _reply(target, info_embed(None, "Prestige is already active for that server."))
            return
        cost = enable_cost(is_owner=self.bot.permissions.is_owner(uid))
        async with self.bot.db.transaction() as conn:
            if cost:
                left = await conn.fetchval("UPDATE prestige_credits SET balance = balance - $2 WHERE user_id = $1 AND balance >= $2 RETURNING balance", uid, cost)
                if left is None:
                    raise InvalidInput("You don't have a Prestige credit. The bot owner can grant you some.")
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", gid)
            await conn.execute("INSERT INTO prestige_servers (guild_id, enabled_by, source) VALUES ($1, $2, $3)", gid, uid, "credit" if cost else "owner")
        self._active.add(gid)
        await _reply(target, ok_embed("Prestige is now active for that server." + (f" It cost {cost} credit." if cost else "")))

    async def _check(self, target: Target, guild_id: Optional[int]) -> None:
        gid = target.guild.id if target.guild else None
        if guild_id is not None and guild_id != gid:
            self._require_global(target)
            gid = guild_id
        if gid is None:
            raise InvalidInput("Run this in a server, or give a server id.")
        row = await self.bot.db.fetchrow("SELECT enabled_by, source, enabled_at FROM prestige_servers WHERE guild_id = $1", gid)
        if row is None:
            await _reply(target, info_embed("Prestige", "Not enabled for that server."))
        else:
            await _reply(target, info_embed("Prestige", f"Enabled {discord.utils.format_dt(row['enabled_at'], 'R')} ({row['source']})."))

    async def _list(self, target: Target) -> None:
        self._require_global(target)
        rows = await self.bot.db.fetch("SELECT guild_id FROM prestige_servers ORDER BY enabled_at")
        lines = [f"• {discord.utils.escape_markdown(g.name)} (`{g.id}`)" if (g := self.bot.get_guild(r['guild_id'])) else f"• unknown (`{r['guild_id']}`)" for r in rows]
        await _reply(target, info_embed(f"Prestige servers ({len(rows)})", "\n".join(lines)[:3800] or "None."))

    async def _remove(self, target: Target, guild_id: Optional[int]) -> None:
        self._require_global(target)
        gid = guild_id or (target.guild.id if target.guild else None)
        if gid is None:
            raise InvalidInput("Give a server id.")
        n = await self.bot.db.execute("DELETE FROM prestige_servers WHERE guild_id = $1", gid)
        self._active.discard(gid)
        await _reply(target, ok_embed("Prestige removed.") if n.endswith("1") else info_embed(None, "That server didn't have Prestige."))

    # --- bot profile -------------------------------------------------------------------------
    async def _profile(self, target: Target, field: str, text: Optional[str], image: Optional[discord.Attachment]) -> None:
        self._require_admin(target)
        guild = target.guild
        self._require_active(guild)
        if isinstance(target, discord.Interaction):
            await target.response.defer(ephemeral=True)
        try:
            if field == "name":
                await guild.me.edit(nick=None if (text or "").lower() == "reset" else (text or "")[:32] or None, reason="prestige profile")
            else:
                if field == "bio":
                    payload = {"bio": None if (text or "").lower() == "reset" else (text or "")[:190]}
                elif (text or "").lower() == "reset":
                    payload = {field: None}
                else:
                    if image is None:
                        raise InvalidInput("Attach an image, or say `reset`.")
                    payload = {field: image_data_uri(image.content_type, await image.read())}
                route = discord.http.Route("PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild.id)
                await self.bot.http.request(route, json=payload)
        except discord.HTTPException as exc:
            await _reply(target, error_embed(f"Discord refused to change the bot's {field} here (HTTP {exc.status}). Per-server bot profiles may not be available for this bot, so nothing changed."))
            return
        await _reply(target, ok_embed(f"Bot {field} updated for this server."))

    # --- tag roles ---------------------------------------------------------------------------
    async def _tag_cfg(self, guild_id: int) -> Optional[tuple[int, str]]:
        if guild_id not in self._tags:
            row = await self.bot.db.fetchrow("SELECT role_id, tag FROM tag_roles WHERE guild_id = $1", guild_id)
            self._tags[guild_id] = (row["role_id"], row["tag"]) if row else None
        return self._tags[guild_id]

    async def _set_tagrole(self, target: Target, role: Optional[discord.Role], tag: Optional[str]) -> None:
        self._require_admin(target)
        guild = target.guild
        self._require_active(guild)
        if role is None:
            await self.bot.db.execute("DELETE FROM tag_roles WHERE guild_id = $1", guild.id)
            self._tags.pop(guild.id, None)
            await _reply(target, ok_embed("Tag role removed."))
            return
        if not tag or len(tag) > 32:
            raise InvalidInput("Give the tag text (up to 32 characters), for example `[HOPE]`.")
        if role.is_default() or role.managed or role >= guild.me.top_role:
            raise InvalidInput("I can't assign that role.")
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, guild.id)
            await conn.execute("INSERT INTO tag_roles (guild_id, role_id, tag) VALUES ($1,$2,$3) ON CONFLICT (guild_id) DO UPDATE SET role_id = EXCLUDED.role_id, tag = EXCLUDED.tag", guild.id, role.id, tag)
        self._tags.pop(guild.id, None)
        await _reply(target, ok_embed(f"Members whose display name contains `{tag}` will get {role.mention}."))

    async def _tag_info(self, target: Target) -> None:
        if target.guild is None:
            raise commands.NoPrivateMessage()
        cfg = await self._tag_cfg(target.guild.id)
        text = (f"Role: <@&{cfg[0]}>\nTag text: `{cfg[1]}`" if cfg else "No tag role is configured.") + \
               "\n\nDiscord's official server tags aren't visible to bots, so the tag is matched in the member's display name."
        await _reply(target, info_embed("Tag role", text))

    async def _sync_member(self, member: discord.Member) -> None:
        if member.bot or member.guild.id not in self._active:
            return
        cfg = await self._tag_cfg(member.guild.id)
        if not cfg:
            return
        role = member.guild.get_role(cfg[0])
        if role is None or role >= member.guild.me.top_role:
            return
        has_tag, has_role = tag_matches(member.display_name, cfg[1]), role in member.roles
        try:
            if has_tag and not has_role:
                await member.add_roles(role, reason="tag role")
            elif has_role and not has_tag:
                await member.remove_roles(role, reason="tag role")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.display_name != after.display_name:
            await self._sync_member(after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._sync_member(member)

    # --- prefix commands ---------------------------------------------------------------------
    @commands.command(name="prestigecredit", help="Grant internal credits (bot owner only)")
    @owner_only()
    async def prestigecredit(self, ctx: commands.Context, user: discord.User, amount: int) -> None:
        await self._grant(ctx, user, amount)

    @commands.command(name="prestigecredits", help="Show your credit balance")
    async def prestigecredits(self, ctx: commands.Context) -> None:
        await self._balance(ctx)

    @commands.group(name="prestige", invoke_without_command=True, help="Show Prestige information")
    async def prestige(self, ctx: commands.Context) -> None:
        active = ctx.guild is not None and ctx.guild.id in self._active
        await ctx.send(embed=info_embed("Prestige", f"This server: **{'enabled' if active else 'not enabled'}**.\nPrestige unlocks the per-server bot profile and tag roles. Only the bot owner or a bot admin can activate it."))

    @prestige.command(name="add", help="Activate Prestige for a server (bot owner or bot admin only)")
    async def prestige_add(self, ctx: commands.Context, guild_id: Optional[int] = None) -> None:
        await self._add(ctx, guild_id)

    @prestige.command(name="check", help="Check Prestige status")
    async def prestige_check(self, ctx: commands.Context, guild_id: Optional[int] = None) -> None:
        await self._check(ctx, guild_id)

    @prestige.command(name="list", help="List Prestige servers")
    async def prestige_list(self, ctx: commands.Context) -> None:
        await self._list(ctx)

    @prestige.command(name="remove", help="Remove Prestige access")
    async def prestige_remove(self, ctx: commands.Context, guild_id: Optional[int] = None) -> None:
        await self._remove(ctx, guild_id)

    @commands.command(name="setbotname", help="Set the bot's nickname in this server")
    async def setbotname(self, ctx: commands.Context, *, name: str) -> None:
        await self._profile(ctx, "name", name, None)

    @commands.command(name="setbotbio", help="Set the bot's bio in this server where supported")
    async def setbotbio(self, ctx: commands.Context, *, text: str) -> None:
        await self._profile(ctx, "bio", text, None)

    @commands.command(name="setbotpfp", help="Set the bot's avatar in this server (attach an image)")
    async def setbotpfp(self, ctx: commands.Context, reset: Optional[str] = None) -> None:
        await self._profile(ctx, "avatar", reset, ctx.message.attachments[0] if ctx.message.attachments else None)

    @commands.command(name="setbotbanner", help="Set the bot's banner in this server (attach an image)")
    async def setbotbanner(self, ctx: commands.Context, reset: Optional[str] = None) -> None:
        await self._profile(ctx, "banner", reset, ctx.message.attachments[0] if ctx.message.attachments else None)

    @commands.command(name="tagrole", help="Give a role to members wearing a tag (no role = remove)")
    async def tagrole(self, ctx: commands.Context, role: Optional[discord.Role] = None, *, tag: Optional[str] = None) -> None:
        await self._set_tagrole(ctx, role, tag)

    @commands.command(name="tagroleinfo", help="Show the tag role configuration")
    async def tagroleinfo(self, ctx: commands.Context) -> None:
        await self._tag_info(ctx)

    # --- slash commands (/prestige ...) ------------------------------------------------------
    slash = app_commands.Group(name="prestige", description="Prestige access, credits and bot profile")

    @slash.command(name="overview", description="Show Prestige information")
    async def s_overview(self, interaction: discord.Interaction) -> None:
        active = interaction.guild is not None and interaction.guild.id in self._active
        await _reply(interaction, info_embed("Prestige", f"This server: **{'enabled' if active else 'not enabled'}**. Enable it with /prestige add."))

    @slash.command(name="add", description="Activate Prestige for a server (bot owner or bot admin only)")
    @app_commands.describe(guild_id="Server id (default: this one)")
    async def s_add(self, interaction: discord.Interaction, guild_id: Optional[str] = None) -> None:
        await self._add(interaction, int(guild_id) if guild_id and guild_id.isdigit() else None)

    @slash.command(name="check", description="Check Prestige status")
    @app_commands.describe(guild_id="Another server's id (bot staff only)")
    async def s_check(self, interaction: discord.Interaction, guild_id: Optional[str] = None) -> None:
        await self._check(interaction, int(guild_id) if guild_id and guild_id.isdigit() else None)

    @slash.command(name="list", description="List Prestige servers (bot staff only)")
    async def s_list(self, interaction: discord.Interaction) -> None:
        await self._list(interaction)

    @slash.command(name="remove", description="Remove a server's Prestige access (bot staff only)")
    @app_commands.describe(guild_id="Server id (default: this one)")
    async def s_remove(self, interaction: discord.Interaction, guild_id: Optional[str] = None) -> None:
        await self._remove(interaction, int(guild_id) if guild_id and guild_id.isdigit() else None)

    @slash.command(name="credit", description="Grant credits (bot owner only)")
    async def s_credit(self, interaction: discord.Interaction, user: discord.User, amount: int) -> None:
        await self._grant(interaction, user, amount)

    @slash.command(name="credits", description="Show your credit balance")
    async def s_credits(self, interaction: discord.Interaction) -> None:
        await self._balance(interaction)

    @slash.command(name="botname", description="Set the bot's nickname in this server")
    async def s_botname(self, interaction: discord.Interaction, name: str) -> None:
        await self._profile(interaction, "name", name, None)

    @slash.command(name="botbio", description="Set the bot's bio in this server where supported")
    async def s_botbio(self, interaction: discord.Interaction, text: str) -> None:
        await self._profile(interaction, "bio", text, None)

    @slash.command(name="botpfp", description="Set the bot's avatar in this server")
    async def s_botpfp(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None, reset: bool = False) -> None:
        await self._profile(interaction, "avatar", "reset" if reset else None, image)

    @slash.command(name="botbanner", description="Set the bot's banner in this server")
    async def s_botbanner(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None, reset: bool = False) -> None:
        await self._profile(interaction, "banner", "reset" if reset else None, image)

    @slash.command(name="tagrole", description="Give a role to members wearing a tag")
    async def s_tagrole(self, interaction: discord.Interaction, role: Optional[discord.Role] = None, tag: Optional[str] = None) -> None:
        await self._set_tagrole(interaction, role, tag)

    @slash.command(name="tagroleinfo", description="Show the tag role configuration")
    async def s_tagroleinfo(self, interaction: discord.Interaction) -> None:
        await self._tag_info(interaction)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Prestige(bot))

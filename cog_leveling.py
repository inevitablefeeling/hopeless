"""Leveling: XP for chatting, ranks, a leaderboard, level-role rewards and level-up cards.

The same tracker counts messages, which giveaways and polls can require (counting starts from when I began tracking).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core_branding as branding
from core_checks import server_admin
from core_embeds import BRAND, SILVER, SUCCESS
from core_errors import InvalidInput
from core_leveling_logic import progress, roles_for_level
from core_ui_v2 import send_card, send_note
from svc_log_service import plain

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.leveling")


class Leveling(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.flusher.start()

    async def cog_unload(self) -> None:
        self.flusher.cancel()
        await self.bot.activity.flush()

    @tasks.loop(seconds=30)
    async def flusher(self) -> None:
        await self.bot.activity.flush()

    # --- earning -----------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None or not isinstance(message.author, discord.Member):
            return
        if self.bot.globals.ban_for(message.author.id):
            return
        try:
            result = await self.bot.activity.record(message.guild.id, message.author.id)
        except Exception:
            log.warning("could not record activity", exc_info=True)
            return
        if result is None:
            return
        _, level, xp = result
        settings = await self.bot.activity.settings(message.guild.id)
        await self._grant_roles(message.author, level, settings)
        channel = message.guild.get_channel(settings.log_channel_id) if settings.log_channel_id else None
        channel = channel if isinstance(channel, discord.TextChannel) else message.channel
        try:
            await send_note(channel, f"## Level Up\n{message.author.mention}! You reached **Level {level}**.\n-# Total XP: {xp:,} • <t:{int(time.time())}:R>", color=SUCCESS)
        except discord.HTTPException:
            pass

    async def _grant_roles(self, member: discord.Member, level: int, settings) -> None:
        me = member.guild.me
        if not settings.roles or not me.guild_permissions.manage_roles:
            return
        for role_id in roles_for_level(settings.roles, level):
            role = member.guild.get_role(role_id)
            if role and role not in member.roles and role < me.top_role and not role.managed:
                try:
                    await member.add_roles(role, reason=f"reached level {level}")
                except discord.HTTPException:
                    log.info("could not give level role %s in %s", role_id, member.guild.id)

    # --- everyone ----------------------------------------------------------------------------
    @commands.hybrid_command(name="rank", description="Show your rank card and level progress")
    @app_commands.describe(member="Whose rank to show (default: you)")
    @commands.guild_only()
    async def rank(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        member = member or ctx.author
        entry = await self.bot.activity.entry(ctx.guild.id, member.id)
        await self.bot.activity.flush()
        level, into, need = progress(entry.xp)
        place = await self.bot.activity.rank_of(ctx.guild.id, member.id)
        await send_card(ctx, title=f"{discord.utils.escape_markdown(member.display_name)}'s rank", color=BRAND, thumbnail=member.display_avatar.url, footer="Leveling",
                        fields=[("Level", str(level)), ("XP", f"{into:,} / {need:,} (total {entry.xp:,})"), ("Rank", f"#{place}" if place else "unranked"),
                                ("Messages", f"{entry.messages:,}")])

    @commands.hybrid_command(name="leaderboard", aliases=["lb"], description="Show the server's top 10 by XP")
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.bot.activity.leaderboard(ctx.guild.id, 10)
        if not rows:
            await send_note(ctx, "Nobody has XP yet. Start chatting!", color=SILVER)
            return
        lines = [f"**{i}.** <@{r['user_id']}> • Level {r['level']} • {r['xp']:,} XP" for i, r in enumerate(rows, 1)]
        await send_card(ctx, title=f"{plain(ctx.guild.name, 60)} leaderboard", description="\n".join(lines), color=BRAND, footer="Leveling")

    # --- configuration (server administrators) ----------------------------------------------
    @commands.hybrid_group(name="levelrole", fallback="list", invoke_without_command=True, description="Show the level role rewards")
    @commands.guild_only()
    @server_admin()
    async def levelrole(self, ctx: commands.Context) -> None:
        settings = await self.bot.activity.settings(ctx.guild.id)
        if not settings.roles:
            await send_note(ctx, "No level roles yet. Add one with `levelrole add <level> @role`.", color=SILVER)
            return
        await send_note(ctx, "**Level roles**\n" + "\n".join(f"Level **{lvl}** → <@&{rid}>" for lvl, rid in sorted(settings.roles.items())))

    @levelrole.command(name="add", description="Give a role at a level")
    @app_commands.describe(level="The level (1 to 500)", role="The role to give")
    @server_admin()
    async def levelrole_add(self, ctx: commands.Context, level: int, role: discord.Role) -> None:
        if not 1 <= level <= 500:
            raise InvalidInput("The level must be between 1 and 500.")
        if role.is_default() or role.managed:
            raise InvalidInput("Pick a normal role (not @everyone or a bot role).")
        me = ctx.guild.me
        note = "" if role < me.top_role else "\nHeads up: that role is above mine, so I can't give it yet. Move my role above it."
        await self.bot.db.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", ctx.guild.id)
        await self.bot.db.execute("INSERT INTO level_roles (guild_id, level, role_id) VALUES ($1,$2,$3) ON CONFLICT (guild_id, level) DO UPDATE SET role_id = EXCLUDED.role_id",
                                  ctx.guild.id, level, role.id)
        self.bot.activity.forget_settings(ctx.guild.id)
        await send_note(ctx, f"{branding.WHITECHECK} {role.mention} will be given at **level {level}**.{note}", color=SUCCESS)

    @levelrole.command(name="remove", description="Remove the role reward for a level")
    @app_commands.describe(level="The level to clear")
    @server_admin()
    async def levelrole_remove(self, ctx: commands.Context, level: int) -> None:
        gone = await self.bot.db.fetchval("DELETE FROM level_roles WHERE guild_id = $1 AND level = $2 RETURNING 1", ctx.guild.id, level)
        if not gone:
            raise InvalidInput(f"There's no role reward at level {level}.")
        self.bot.activity.forget_settings(ctx.guild.id)
        await send_note(ctx, f"{branding.WHITECHECK} Removed the reward for level **{level}**.", color=SUCCESS)

    @commands.hybrid_group(name="setlevellog", fallback="set", invoke_without_command=True, description="Choose where level-up cards are posted")
    @app_commands.describe(channel="The channel for level-up cards")
    @commands.guild_only()
    @server_admin()
    async def setlevellog(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.bot.activity.save_settings(ctx.guild.id, log_channel_id=channel.id)
        await send_note(ctx, f"{branding.WHITECHECK} Level-up cards will be posted in {channel.mention}.", color=SUCCESS)

    @setlevellog.command(name="reset", description="Post level-up cards in the channel where people chat")
    @server_admin()
    async def setlevellog_reset(self, ctx: commands.Context) -> None:
        await self.bot.activity.save_settings(ctx.guild.id, log_channel_id=None)
        await send_note(ctx, f"{branding.WHITECHECK} Level-up cards will be posted where the member chatted.", color=SUCCESS)

    @commands.hybrid_group(name="leveling", fallback="status", invoke_without_command=True, description="Show whether leveling is on")
    @commands.guild_only()
    @server_admin()
    async def leveling(self, ctx: commands.Context) -> None:
        settings = await self.bot.activity.settings(ctx.guild.id)
        await send_note(ctx, f"Leveling is **{'on' if settings.enabled else 'off'}**. XP per message: {settings.xp_min}-{settings.xp_max}, once every {settings.cooldown}s.")

    @leveling.command(name="enable", description="Turn XP on")
    @server_admin()
    async def leveling_enable(self, ctx: commands.Context) -> None:
        await self.bot.activity.save_settings(ctx.guild.id, enabled=True)
        await send_note(ctx, f"{branding.WHITECHECK} Leveling is on.", color=SUCCESS)

    @leveling.command(name="disable", description="Turn XP off (message counts keep working)")
    @server_admin()
    async def leveling_disable(self, ctx: commands.Context) -> None:
        await self.bot.activity.save_settings(ctx.guild.id, enabled=False)
        await send_note(ctx, "Leveling is off. Message counts for giveaways and polls keep working.", color=SILVER)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Leveling(bot))

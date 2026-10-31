"""Extra owners: people the server owner trusts with owner-level control of THIS server only.

They get security-admin powers here (antinuke, whitelist, logging, setup, server configuration) and nothing
global: no global bans, no staff management, no other server. Only the server owner can add or remove them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import core_branding as branding
from core_embeds import SUCCESS
from core_errors import InvalidInput
from core_ui_v2 import send_note
from svc_extra_owner_service import MAX_EXTRA_OWNERS
from svc_log_service import who

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot


class ExtraOwners(commands.Cog, name="Extra owners"):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    def _owner_only(self, ctx: commands.Context) -> None:
        if ctx.author.id != ctx.guild.owner_id and not self.bot.permissions.is_owner(ctx.author.id):
            raise commands.CheckFailure("Only the server owner can add or remove extra owners.")

    @commands.hybrid_group(name="extraowner", fallback="list", invoke_without_command=True, description="Show this server's extra owners")
    @commands.guild_only()
    async def extraowner(self, ctx: commands.Context) -> None:
        if not (ctx.author.id == ctx.guild.owner_id or self.bot.extraowners.contains(ctx.guild.id, ctx.author.id) or ctx.author.guild_permissions.administrator):
            raise commands.CheckFailure("Only the server owner, extra owners and administrators can see this list.")
        owners = self.bot.extraowners.list(ctx.guild.id)
        await send_note(ctx, "**Extra owners**\n" + ("\n".join(f"<@{u}>" for u in owners) if owners else "None. The server owner can add one with `extraowner add @user`."))

    @extraowner.command(name="add", description="Make someone an extra owner of this server")
    @app_commands.describe(user="The person to trust (a member of this server)")
    async def extraowner_add(self, ctx: commands.Context, user: discord.Member) -> None:
        self._owner_only(ctx)
        if user.bot:
            raise InvalidInput("A bot can't be an extra owner.")
        if user.id == ctx.guild.owner_id:
            raise InvalidInput("They already own this server.")
        if self.bot.extraowners.contains(ctx.guild.id, user.id):
            raise InvalidInput(f"{user.mention} is already an extra owner.")
        if len(self.bot.extraowners.list(ctx.guild.id)) >= MAX_EXTRA_OWNERS:
            raise InvalidInput(f"A server can have up to {MAX_EXTRA_OWNERS} extra owners.")
        await self.bot.extraowners.add(ctx.guild.id, user.id, ctx.author.id)
        await self.bot.logs.security(ctx.guild, "Extra owner added", fields=[("Added", who(user)), ("By", who(ctx.author))])
        await send_note(ctx, f"{branding.WHITECHECK} {user.mention} is now an extra owner of this server. This gives no power outside it.", color=SUCCESS)

    @extraowner.command(name="remove", description="Remove an extra owner")
    @app_commands.describe(user="The extra owner to remove")
    async def extraowner_remove(self, ctx: commands.Context, user: discord.User) -> None:
        self._owner_only(ctx)
        if not await self.bot.extraowners.remove(ctx.guild.id, user.id):
            raise InvalidInput(f"{user.mention} isn't an extra owner.")
        await self.bot.logs.security(ctx.guild, "Extra owner removed", fields=[("Removed", who(user)), ("By", who(ctx.author))])
        await send_note(ctx, f"{branding.WHITECHECK} {user.mention} is no longer an extra owner.", color=SUCCESS)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(ExtraOwners(bot))

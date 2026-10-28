"""Whitelist module: scoped, per-server exemptions for trusted users and roles.

Everything here needs the server owner or the Administrator permission, checked on every call.
Stage 1 stores and manages the entries; the antinuke and automod modules enforce them when they ship.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from core_checks import ensure_security_admin
from core_command_registry import registry
from core_embeds import info_embed, ok_embed
from core_errors import HopelessError, InvalidInput
from core_formatting import chunk_lines
from core_whitelist_logic import (
    TARGET_ROLE,
    TARGET_USER,
    describe_scope,
    normalize_scope,
    valid_scopes,
)

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.whitelist")

Target = Union[discord.Member, discord.Role]


def _target_parts(target: Target) -> tuple[str, int]:
    return (TARGET_ROLE if isinstance(target, discord.Role) else TARGET_USER, target.id)


def _mention(target_type: str, target_id: int) -> str:
    return f"<@&{target_id}>" if target_type == TARGET_ROLE else f"<@{target_id}>"


class Whitelist(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ensure_security_admin(ctx)

    # --- add ---------------------------------------------------------------------------------
    @commands.hybrid_group(
        name="whitelist",
        fallback="add",
        aliases=["wl"],
        description="Exempt trusted users and roles from security rules",
    )
    @app_commands.describe(
        target="The user or role to exempt",
        scope="antinuke (default), automod, or one module such as antinuke:ban",
    )
    async def whitelist(self, ctx: commands.Context, target: Target, scope: Optional[str] = None) -> None:
        scope_key = normalize_scope(scope)
        if isinstance(target, discord.Role) and target.is_default():
            raise InvalidInput("You can't whitelist @everyone. That would exempt every member.")
        target_type, target_id = _target_parts(target)
        added = await self.bot.whitelist.add(
            guild_id=ctx.guild.id, actor_id=ctx.author.id, target_type=target_type, target_id=target_id, scope=scope_key
        )
        area = scope_key.split(":")[0]
        verb = "is now exempt from" if added else "was already exempt from"
        lines = [f"{target.mention} {verb} {describe_scope(scope_key)}."]
        if scope_key == area:
            lines.append(f"That covers every {area} rule and nothing outside {area}.")
        if not registry.is_live(area):
            lines.append(f"-# {area.title()} isn't live yet. The entry is saved and takes effect when it ships.")
        await ctx.send(embed=ok_embed("\n".join(lines)))

    # --- list / scopes -----------------------------------------------------------------------
    @whitelist.command(name="list", description="Show every whitelist entry")
    async def whitelist_list(self, ctx: commands.Context) -> None:
        entries = await self.bot.whitelist.entries(ctx.guild.id)
        if not entries:
            await ctx.send(embed=info_embed("Whitelist", "No entries yet. Add one with the whitelist command."))
            return
        by_scope: dict[str, list[str]] = {}
        for entry in entries:
            by_scope.setdefault(entry.scope, []).append(f"• {_mention(entry.target_type, entry.target_id)}")
        embed = info_embed(
            f"Whitelist ({len(entries)})",
            "Each entry exempts only the scope shown. An area never covers another area.",
        )
        fields = 0
        for scope, lines in sorted(by_scope.items()):
            for index, chunk in enumerate(chunk_lines(lines)):
                if fields >= 24:
                    embed.set_footer(text="More entries exist than fit here.")
                    break
                name = describe_scope(scope) if index == 0 else f"{describe_scope(scope)} (continued)"
                embed.add_field(name=name, value=chunk, inline=False)
                fields += 1
        await ctx.send(embed=embed)

    @whitelist.command(name="scopes", description="List the scopes an exemption can cover")
    async def whitelist_scopes(self, ctx: commands.Context) -> None:
        lines = [f"`{scope}`" for scope in valid_scopes()]
        embed = info_embed(
            "Whitelist scopes",
            "Use `antinuke` or `automod` to cover a whole area, or one module to cover just that.",
        )
        for index, chunk in enumerate(chunk_lines(lines)):
            embed.add_field(name="Scopes" if index == 0 else "Scopes (continued)", value=chunk.replace("\n", " "), inline=False)
        await ctx.send(embed=embed)

    # --- remove ------------------------------------------------------------------------------
    @whitelist.command(name="remove", description="Remove a user or role from the whitelist")
    @app_commands.describe(target="The user or role to remove", scope="Only remove this scope (default: all of their scopes)")
    async def whitelist_remove(self, ctx: commands.Context, target: Target, scope: Optional[str] = None) -> None:
        await self._remove(ctx, target, scope)

    @commands.command(name="whitelistremove", aliases=["wlr"], help="Shortcut for whitelist remove")
    async def whitelistremove(self, ctx: commands.Context, target: Target, scope: Optional[str] = None) -> None:
        await self._remove(ctx, target, scope)

    async def _remove(self, ctx: commands.Context, target: Target, scope: Optional[str]) -> None:
        target_type, target_id = _target_parts(target)
        removed = await self.bot.whitelist.remove(
            guild_id=ctx.guild.id, actor_id=ctx.author.id, target_type=target_type, target_id=target_id, scope=scope
        )
        if removed:
            what = describe_scope(normalize_scope(scope)) if scope else "everything"
            await ctx.send(embed=ok_embed(f"{target.mention} is no longer exempt from {what}."))
        else:
            await ctx.send(embed=info_embed(None, f"{target.mention} had no matching whitelist entry."))

    # --- keep the list honest ----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """A member who leaves loses their exemption, so rejoining never restores it silently."""
        await self._prune(member.guild.id, TARGET_USER, member.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._prune(role.guild.id, TARGET_ROLE, role.id)

    async def _prune(self, guild_id: int, target_type: str, target_id: int) -> None:
        try:
            entries = await self.bot.whitelist.entries(guild_id)  # cached, so busy servers don't hit the database
            if not any(e.target_type == target_type and e.target_id == target_id for e in entries):
                return
            actor = self.bot.user.id if self.bot.user else 0
            await self.bot.whitelist.remove(
                guild_id=guild_id, actor_id=actor, target_type=target_type, target_id=target_id
            )
        except HopelessError:
            log.warning("could not prune whitelist entries for %s:%s in guild %s", target_type, target_id, guild_id)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Whitelist(bot))

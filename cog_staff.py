"""Global bot staff management. Direct slash commands only, and only the bot owner may run them.

The owner check runs on every invocation (Cog.interaction_check); hiding a command is never relied on.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core_checks import owner_only
from core_embeds import info_embed, ok_embed
from core_errors import InvalidInput
from core_permissions import GlobalRole

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.staff")

_TITLES = {GlobalRole.ADMINISTRATOR: "administrator", GlobalRole.MODERATOR: "moderator"}


class Staff(commands.Cog):
    administrator = app_commands.Group(
        name="administrator", description="Manage global bot administrators (bot owner only)"
    )
    mod = app_commands.Group(name="mod", description="Manage global bot moderators (bot owner only)")

    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.bot.permissions.is_owner(interaction.user.id):
            return True
        raise app_commands.CheckFailure("Only the bot owner can use this command.")

    # --- shared logic ------------------------------------------------------------------------
    async def _add(self, interaction: discord.Interaction, user: discord.User, role: GlobalRole) -> None:
        if user.bot:
            raise InvalidInput("Bots can't be global staff.")
        result = await self.bot.permissions.add_staff(actor_id=interaction.user.id, target_id=user.id, role=role)
        if result in ("added", "changed"):  # a new role starts from its default global permissions
            await self.bot.globals.reset_perms(interaction.user.id, user.id, role)
        title = _TITLES[role]
        text = {
            "added": f"{user.mention} is now a global bot {title}.",
            "changed": f"{user.mention} was moved to global bot {title}.",
            "unchanged": f"{user.mention} is already a global bot {title}.",
        }[result]
        await interaction.response.send_message(embed=ok_embed(text), ephemeral=True)

    async def _remove(self, interaction: discord.Interaction, user: discord.User, role: GlobalRole) -> None:
        removed = await self.bot.permissions.remove_staff(actor_id=interaction.user.id, target_id=user.id, role=role)
        if removed:
            self.bot.globals.forget_staff(user.id)
            await self.bot.globals.audit(interaction.user.id, f"staff.{role.value}.remove", user.id)
        text = (
            f"{user.mention} is no longer a global bot {_TITLES[role]}."
            if removed
            else f"{user.mention} isn't a global bot {_TITLES[role]}."
        )
        await interaction.response.send_message(embed=ok_embed(text) if removed else info_embed(None, text), ephemeral=True)

    async def _list(self, interaction: discord.Interaction, role: GlobalRole) -> None:
        staff = self.bot.permissions.list_staff(role)
        title = f"Global bot {_TITLES[role]}s"
        body = "\n".join(f"• <@{uid}> (`{uid}`)" for uid, _ in staff) if staff else "Nobody yet."
        await interaction.response.send_message(embed=info_embed(title, body), ephemeral=True)

    # --- /administrator ----------------------------------------------------------------------
    @administrator.command(name="add", description="Grant global bot administrator access")
    @app_commands.describe(user="The user to promote")
    async def administrator_add(self, interaction: discord.Interaction, user: discord.User) -> None:
        await self._add(interaction, user, GlobalRole.ADMINISTRATOR)

    @administrator.command(name="remove", description="Remove global bot administrator access")
    @app_commands.describe(user="The user to demote")
    async def administrator_remove(self, interaction: discord.Interaction, user: discord.User) -> None:
        await self._remove(interaction, user, GlobalRole.ADMINISTRATOR)

    @administrator.command(name="list", description="List global bot administrators")
    async def administrator_list(self, interaction: discord.Interaction) -> None:
        await self._list(interaction, GlobalRole.ADMINISTRATOR)

    # --- /mod --------------------------------------------------------------------------------
    @mod.command(name="add", description="Grant global bot moderator access")
    @app_commands.describe(user="The user to promote")
    async def mod_add(self, interaction: discord.Interaction, user: discord.User) -> None:
        await self._add(interaction, user, GlobalRole.MODERATOR)

    @mod.command(name="remove", description="Remove global bot moderator access")
    @app_commands.describe(user="The user to demote")
    async def mod_remove(self, interaction: discord.Interaction, user: discord.User) -> None:
        await self._remove(interaction, user, GlobalRole.MODERATOR)

    @mod.command(name="list", description="List global bot moderators")
    async def mod_list(self, interaction: discord.Interaction) -> None:
        await self._list(interaction, GlobalRole.MODERATOR)

    # --- prefix-only: re-sync slash commands -------------------------------------------------
    @commands.command(name="sync", hidden=True, help="Re-sync slash commands (bot owner only)")
    @owner_only()
    async def sync(self, ctx: commands.Context) -> None:
        count = await self.bot.sync_application_commands()
        await ctx.send(embed=ok_embed(f"Synced {count} slash commands."))


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Staff(bot))

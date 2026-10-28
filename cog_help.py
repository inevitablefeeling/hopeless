"""Help module: the interactive panel and per-command lookup."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import core_branding as branding
from core_command_registry import registry
from core_embeds import error_embed, info_embed
from core_formatting import clean_inline
import view_help_panel as help_panel

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot


class Help(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    def _prefix(self, guild: Optional[discord.Guild]) -> str:
        return self.bot.prefixes.get(guild.id if guild else None)

    @commands.hybrid_command(name="help", description="Open the help panel or look up one command")
    @app_commands.describe(command="A command to look up, such as whitelist")
    async def show_help(self, ctx: commands.Context, *, command: Optional[str] = None) -> None:
        prefix = self._prefix(ctx.guild)
        if command:
            await self._send_command_help(ctx, command, prefix)
            return
        embed, view = help_panel.render(
            self.bot, owner_id=ctx.author.id, guild_id=ctx.guild.id if ctx.guild else None, key=help_panel.HOME
        )
        banner = branding.banner_file()
        if banner is not None:
            await ctx.send(embed=embed, view=view, file=banner)
        else:
            await ctx.send(embed=embed, view=view)

    async def _send_command_help(self, ctx: commands.Context, query: str, prefix: str) -> None:
        text = query.strip()
        if text.startswith(prefix):
            text = text[len(prefix):]
        spec = registry.find(text)
        if spec is not None and registry.module(spec.module).hidden and not help_panel.is_global_staff(self.bot, ctx.author.id):
            spec = None  # restricted commands don't exist as far as everyone else is concerned (no hints either)
            text = text[:40]
        if spec is None:
            hints = registry.suggest(text)
            message = f"There's no command called `{clean_inline(text)}`."
            if hints:
                message += " Did you mean " + ", ".join(f"`{prefix}{hint}`" for hint in hints) + "?"
            await ctx.send(embed=error_embed(message), ephemeral=True)
            return
        if not spec.implemented:
            module = registry.module(spec.module)
            await ctx.send(
                embed=error_embed(
                    f"`{prefix}{spec.label.lstrip('/')}` isn't available yet. It belongs to the "
                    f"{module.title} module, which is still being built."
                ),
                ephemeral=True,
            )
            return
        usage = None
        command = self.bot.get_command(spec.name) if spec.name else None
        if command is not None:
            usage = f"{prefix}{command.qualified_name} {command.signature}".strip()
        await ctx.send(embed=help_panel.command_embed(spec, icons=self.bot.icons, prefix=prefix, usage=usage))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Mentioning the bot on its own tells you the prefix - the way back if you forget it."""
        user = self.bot.user
        if user is None or message.author.bot or self.bot.globals.ban_for(message.author.id):
            return
        if message.content.strip() not in (f"<@{user.id}>", f"<@!{user.id}>"):
            return
        prefix = self._prefix(message.guild)
        try:
            await message.reply(
                embed=info_embed(None, f"My prefix here is `{prefix}`. Try `{prefix}help` or `/help`."),
                mention_author=False,
            )
        except discord.HTTPException:
            pass


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Help(bot))

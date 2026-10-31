"""The help panel as a real Components V2 message: a container with a header, separators and the controls.

Layout (see core/help_model.py for the content):
    Container
      Section(title, facts, bot avatar as thumbnail)
      Separator
      TextDisplay(...)            <- home tips, a category's commands, or the all-commands overview
      Separator
      ActionRow(Select "Browse a Category…")
      ActionRow(Home, All Commands, Close)

The panel is not persistent: it stops responding after 15 minutes or a bot restart, and the member just runs
help again. Every press re-checks that the person pressing it opened the panel, and the list of categories is
rebuilt for that person (restricted categories are shown to global staff only, and enforced here).
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

import core_branding as branding
from core_help_model import ALL, HOME, all_blocks, categories, category_blocks, category_by_key, home_lines, total_commands

log = logging.getLogger("hopeless.help")


def is_global_staff(bot, user_id: int) -> bool:
    return bot.permissions.is_owner(user_id) or bot.permissions.global_role(user_id) is not None


class HelpLayout(discord.ui.LayoutView):
    def __init__(self, bot, owner_id: int, guild_id: Optional[int], key: str = HOME) -> None:
        super().__init__(timeout=900)
        self.bot, self.owner_id, self.guild_id = bot, owner_id, guild_id
        self.staff = is_global_staff(bot, owner_id)
        self.key = key if key in (HOME, ALL) or category_by_key(key, self.staff) else HOME  # enforced, not just hidden
        self._build()

    # --- content -----------------------------------------------------------------------------
    def _prefix(self) -> str:
        return self.bot.prefixes.get(self.guild_id)

    def _build(self) -> None:
        self.clear_items()
        prefix, bullet = self._prefix(), "›"  # plain text: the panel carries no emoji
        avatar = self.bot.user.display_avatar.url if self.bot.user is not None else None
        container = discord.ui.Container(accent_colour=discord.Colour(branding.ACCENT_COLOR))

        if self.key == HOME:
            facts, tips = home_lines(prefix, self.staff)
            head = [discord.ui.TextDisplay(f"## {branding.BOT_NAME} Help"), discord.ui.TextDisplay(f"> {branding.TAGLINE}\n{facts}")]
            body = [discord.ui.TextDisplay("\n".join(f"{bullet} {line}" for line in tips.split("\n")))]
        elif self.key == ALL:
            head = [discord.ui.TextDisplay(f"## {branding.BOT_NAME} Help | All Commands"),
                    discord.ui.TextDisplay(f"`{total_commands(self.staff)}` commands in `{len(categories(self.staff))}` categories")]
            body = [discord.ui.TextDisplay(block) for block in all_blocks(prefix, self.staff)]
        else:
            cat = category_by_key(self.key, self.staff)
            head = [discord.ui.TextDisplay(f"## {branding.BOT_NAME} Help | {cat.title}"), discord.ui.TextDisplay(f"> {cat.summary}")]
            body = [discord.ui.TextDisplay(block) for block in category_blocks(cat, prefix)]

        if avatar:
            container.add_item(discord.ui.Section(*head, accessory=discord.ui.Thumbnail(avatar)))
        else:
            for item in head:
                container.add_item(item)
        container.add_item(discord.ui.Separator())
        for index, item in enumerate(body):
            if index:
                container.add_item(discord.ui.Separator(visible=False))
            container.add_item(item)
        container.add_item(discord.ui.Separator())

        select = discord.ui.Select(placeholder="Browse a Category…", options=[
            discord.SelectOption(label=cat.title, value=cat.key, description=(cat.blurb or f"{cat.title} commands")[:100], default=cat.key == self.key)
            for cat in categories(self.staff)][:25])
        select.callback = self._on_select
        row = discord.ui.ActionRow()
        row.add_item(select)
        container.add_item(row)

        buttons = discord.ui.ActionRow()
        for label, handler, style in (("Home", self._home, discord.ButtonStyle.secondary), ("All Commands", self._all, discord.ButtonStyle.secondary),
                                      ("Close", self._close, discord.ButtonStyle.danger)):
            button = discord.ui.Button(label=label, style=style)
            button.callback = handler
            buttons.add_item(button)
        container.add_item(buttons)
        self.add_item(container)
        self._select = select

    # --- interactions ------------------------------------------------------------------------
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This help menu belongs to someone else. Run the help command to open your own.", ephemeral=True)
        return False

    async def _show(self, interaction: discord.Interaction, key: str) -> None:
        self.key = key if key in (HOME, ALL) or category_by_key(key, is_global_staff(self.bot, interaction.user.id)) else HOME
        self.staff = is_global_staff(self.bot, interaction.user.id)
        self._build()
        await interaction.response.edit_message(view=self)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, self._select.values[0])

    async def _home(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, HOME)

    async def _all(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, ALL)

    async def _close(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass
        self.stop()

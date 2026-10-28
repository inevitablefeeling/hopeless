"""Pagination for long lists (server list, ban list, audit log...)."""
from __future__ import annotations

from typing import Sequence

import discord

from core_embeds import NAME


class PaginatorView(discord.ui.View):
    def __init__(self, pages: Sequence[discord.Embed], user_id: int, system: str = "") -> None:
        super().__init__(timeout=180)
        self.pages, self.user_id, self.system, self.index = list(pages), user_id, system, 0
        self.prev = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.next = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
        self.prev.callback, self.next.callback = self._back, self._forward
        self.add_item(self.prev)
        self.add_item(self.next)
        self._sync()

    def page(self) -> discord.Embed:
        embed = self.pages[self.index]
        embed.set_footer(text=f"{NAME} • {self.system + ' • ' if self.system else ''}Page {self.index + 1}/{len(self.pages)}")
        return embed

    def _sync(self) -> None:
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This list belongs to someone else.", ephemeral=True)
        return False

    async def _move(self, interaction: discord.Interaction, step: int) -> None:
        self.index = max(0, min(len(self.pages) - 1, self.index + step))
        self._sync()
        await interaction.response.edit_message(embed=self.page(), view=self)

    async def _back(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, -1)

    async def _forward(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, 1)


def paginate(title: str, lines: Sequence[str], *, per_page: int = 8, color: int = 0x9B111E, empty: str = "Nothing to show.") -> list[discord.Embed]:
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[empty]]
    return [discord.Embed(title=title, description="\n".join(chunk), color=color) for chunk in chunks]


async def send_pages(ctx, pages: list[discord.Embed], system: str = "", *, ephemeral: bool = False):
    """Send one page, or a paginated message the caller can flip through."""
    if len(pages) == 1:
        pages[0].set_footer(text=f"{NAME} • {system}" if system else NAME)
        return await ctx.send(embed=pages[0], ephemeral=ephemeral)
    view = PaginatorView(pages, ctx.author.id, system)
    return await ctx.send(embed=view.page(), view=view, ephemeral=ephemeral)

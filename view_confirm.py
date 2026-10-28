"""Preview-then-confirm flow used before anything creates or changes roles, channels or settings."""
from __future__ import annotations

from typing import Optional, Sequence

import discord


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int, *, labels: Sequence[str] = ("Confirm", "Cancel"), timeout: float = 120) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.choice: Optional[int] = None  # index of the pressed button, None = timed out
        for index, label in enumerate(labels):
            button = discord.ui.Button(
                label=label, style=discord.ButtonStyle.primary if index == 0 else discord.ButtonStyle.secondary
            )
            button.callback = self._make_callback(index)
            self.add_item(button)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction) -> None:
            self.choice = index
            for item in self.children:
                item.disabled = True
            try:
                await interaction.response.edit_message(view=self)
            except discord.HTTPException:
                pass
            self.stop()

        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Only the person who started this can answer.", ephemeral=True)
        return False


async def ask(ctx, embed: discord.Embed, *, labels: Sequence[str] = ("Confirm", "Cancel")) -> Optional[int]:
    """Send `embed` with buttons from a command context. Returns the chosen index, or None on timeout."""
    view = ConfirmView(ctx.author.id, labels=labels)
    message = await ctx.send(embed=embed, view=view)
    await view.wait()
    if view.choice is None:
        for item in view.children:
            item.disabled = True
        try:
            await message.edit(view=view)
        except discord.HTTPException:
            pass
    return view.choice


async def ask_interaction(interaction: discord.Interaction, embed: discord.Embed, *,
                          labels: Sequence[str] = ("Confirm", "Cancel")) -> Optional[int]:
    """Same, but for a component interaction that has already been deferred."""
    view = ConfirmView(interaction.user.id, labels=labels)
    message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
    await view.wait()
    if view.choice is None:
        for item in view.children:
            item.disabled = True
        try:
            await message.edit(view=view)
        except discord.HTTPException:
            pass
    return view.choice

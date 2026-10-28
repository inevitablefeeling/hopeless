"""Cards: Discord's newer message layout (Components V2: a container with an accent bar, text, a thumbnail and
separators) when the installed discord.py supports it, and a classic embed otherwise.

One function, send_card(), builds both versions from the same description. If the newer layout can't be built or
Discord refuses it, the classic embed is sent instead, so a card is never lost. Panels that carry persistent
buttons and menus (help, tickets, voice, the designer) stay classic on purpose: they are the parts that must
keep working across restarts.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

import discord

from core_embeds import BRAND, NAME

log = logging.getLogger("hopeless.ui")
_MAX_FIELDS = 12


def v2_available() -> bool:
    return all(hasattr(discord.ui, name) for name in ("LayoutView", "Container", "TextDisplay", "Separator", "Section", "Thumbnail"))


def build_embed(*, title: str, description: str = "", fields: Sequence[tuple[str, str]] = (), color: int = BRAND,
                footer: Optional[str] = None, thumbnail: Optional[str] = None, image: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or None, color=color)
    for name, value in list(fields)[:_MAX_FIELDS]:
        embed.add_field(name=name, value=value or "-", inline=len(value) < 40 and "\n" not in value)
    if footer:
        embed.set_footer(text=f"{NAME} • {footer}")
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    return embed


def build_layout(*, title: str, description: str = "", fields: Sequence[tuple[str, str]] = (), color: int = BRAND,
                 footer: Optional[str] = None, thumbnail: Optional[str] = None, image: Optional[str] = None):
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=discord.Colour(color))
    head = [discord.ui.TextDisplay(f"## {title}")]
    if description:
        head.append(discord.ui.TextDisplay(description))
    if thumbnail:
        container.add_item(discord.ui.Section(*head, accessory=discord.ui.Thumbnail(thumbnail)))
    else:
        for item in head:
            container.add_item(item)
    shown = list(fields)[:_MAX_FIELDS]
    if shown:
        container.add_item(discord.ui.Separator())
        for name, value in shown:
            container.add_item(discord.ui.TextDisplay(f"**{name}**\n{value or '-'}"))
    if image:
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=image)
        container.add_item(gallery)
    if footer:
        container.add_item(discord.ui.Separator(visible=False))
        container.add_item(discord.ui.TextDisplay(f"-# {NAME} • {footer}"))
    view.add_item(container)
    return view


async def send_card(target, *, reply: bool = False, delete_after: Optional[float] = None, ephemeral: bool = False,
                    mention_author: Optional[bool] = None, **card):
    """Send a card to a command context, a channel or (with reply=True) a message. Returns the sent message."""
    send = target.reply if reply else target.send
    kwargs: dict = {}
    if delete_after is not None:
        kwargs["delete_after"] = delete_after
    if mention_author is not None and reply:
        kwargs["mention_author"] = mention_author
    if ephemeral and hasattr(target, "interaction"):
        kwargs["ephemeral"] = True
    if v2_available():
        try:
            return await send(view=build_layout(**card), **kwargs)
        except (AttributeError, TypeError, ValueError):
            log.warning("could not build the new card layout; sending a classic embed", exc_info=True)
        except discord.HTTPException:
            log.info("Discord refused the new card layout; sending a classic embed", exc_info=True)
    return await send(embed=build_embed(**card), **kwargs)

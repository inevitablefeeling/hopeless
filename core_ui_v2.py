"""Cards and notes: Discord's Components V2 layout (a container with an accent bar, text, thumbnail and separators)
when the installed discord.py supports it, and a classic embed otherwise.

Everything here builds the V2 version and the embed version from the same description. If the V2 layout can't be
built or Discord refuses it, the embed is sent instead, so a message is never lost. Remember the V2 rules: a V2
message carries NO plain `content` and NO embeds, so anything that must ping goes inside a text component and the
allowed_mentions you pass; the fallback embed uses normal content.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

import discord

from core_embeds import BRAND, NAME

log = logging.getLogger("hopeless.ui")
_MAX_FIELDS = 12
Field = tuple[str, str]


def v2_available() -> bool:
    needed = ("LayoutView", "Container", "TextDisplay", "Separator", "Section", "Thumbnail", "ActionRow")
    return all(hasattr(discord.ui, name) for name in needed)


def footer_text(footer: Optional[str]) -> Optional[str]:
    return f"{NAME} • {footer}" if footer else None


# --------------------------------------------------------------------------------------------- embeds
def build_embed(*, title: str, description: str = "", fields: Sequence[Field] = (), color: int = BRAND, footer: Optional[str] = None,
                thumbnail: Optional[str] = None, image: Optional[str] = None, author: Optional[tuple[str, Optional[str]]] = None,
                raw_footer: Optional[str] = None, timestamp=None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or None, color=color, timestamp=timestamp)
    if author:
        embed.set_author(name=author[0], icon_url=author[1])
    for name, value in list(fields)[:_MAX_FIELDS]:
        embed.add_field(name=name, value=value or "-", inline=len(value) < 40 and "\n" not in value)
    text = raw_footer or footer_text(footer)
    if text:
        embed.set_footer(text=text)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    return embed


def note_embed(text: str, color: int = BRAND) -> discord.Embed:
    return discord.Embed(description=text, color=color)


# --------------------------------------------------------------------------------------------- V2 layouts
def build_layout(*, title: str, description: str = "", fields: Sequence[Field] = (), color: int = BRAND, footer: Optional[str] = None,
                 thumbnail: Optional[str] = None, image: Optional[str] = None, author: Optional[tuple[str, Optional[str]]] = None,
                 raw_footer: Optional[str] = None, top_text: Optional[str] = None, timestamp=None):
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=discord.Colour(color))
    head = []
    if top_text:
        head.append(discord.ui.TextDisplay(top_text))
    if author:
        head.append(discord.ui.TextDisplay(f"**{author[0]}**"))
    head.append(discord.ui.TextDisplay(f"## {title}"))
    if description:
        head.append(discord.ui.TextDisplay(description))
    icon = thumbnail or (author[1] if author and author[1] else None)
    if icon:
        container.add_item(discord.ui.Section(*head[:3], accessory=discord.ui.Thumbnail(icon)))
        for extra in head[3:]:
            container.add_item(extra)
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
    text = raw_footer or footer_text(footer)
    if text:
        container.add_item(discord.ui.Separator(visible=False))
        container.add_item(discord.ui.TextDisplay(f"-# {text}"))
    view.add_item(container)
    return view


def build_note(text: str, color: int = BRAND):
    """A compact one-line V2 message: a slim container with a single text display."""
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=discord.Colour(color))
    container.add_item(discord.ui.TextDisplay(text))
    view.add_item(container)
    return view


# --------------------------------------------------------------------------------------------- sending
def _kwargs(target, reply: bool, delete_after, ephemeral, mention_author, allowed_mentions) -> dict:
    kwargs: dict = {}
    if delete_after is not None:
        kwargs["delete_after"] = delete_after
    if mention_author is not None and reply:
        kwargs["mention_author"] = mention_author
    if allowed_mentions is not None:
        kwargs["allowed_mentions"] = allowed_mentions
    if ephemeral and hasattr(target, "interaction"):
        kwargs["ephemeral"] = True
    return kwargs


async def send_layout(target, make_layout, make_embed, *, reply: bool = False, delete_after: Optional[float] = None, ephemeral: bool = False,
                      mention_author: Optional[bool] = None, allowed_mentions=None, content: Optional[str] = None):
    """Send make_layout() as a V2 message; if that can't work, send make_embed() (plus `content`) instead."""
    send = target.reply if reply else target.send
    kwargs = _kwargs(target, reply, delete_after, ephemeral, mention_author, allowed_mentions)
    if v2_available():
        try:
            return await send(view=make_layout(), **kwargs)
        except (AttributeError, TypeError, ValueError):
            log.warning("could not build the V2 layout; sending a classic embed", exc_info=True)
        except discord.HTTPException:
            log.info("Discord refused the V2 layout; sending a classic embed", exc_info=True)
    if content:
        kwargs["content"] = content
    return await send(embed=make_embed(), **kwargs)


async def send_card(target, *, reply: bool = False, delete_after: Optional[float] = None, ephemeral: bool = False,
                    mention_author: Optional[bool] = None, allowed_mentions=None, content: Optional[str] = None, **card):
    """A titled card. `content` is text that goes above the card (inside it for V2, as normal content for the embed)."""
    return await send_layout(target, lambda: build_layout(top_text=content, **card), lambda: build_embed(**card), reply=reply,
                             delete_after=delete_after, ephemeral=ephemeral, mention_author=mention_author,
                             allowed_mentions=allowed_mentions, content=content)


async def send_note(target, text: str, *, color: int = BRAND, reply: bool = False, delete_after: Optional[float] = None,
                    ephemeral: bool = False, mention_author: Optional[bool] = None, allowed_mentions=None):
    """A compact one-line message (confirmations, AFK notices)."""
    return await send_layout(target, lambda: build_note(text, color), lambda: note_embed(text, color), reply=reply, delete_after=delete_after,
                             ephemeral=ephemeral, mention_author=mention_author,
                             allowed_mentions=allowed_mentions if allowed_mentions is not None else discord.AllowedMentions.none())

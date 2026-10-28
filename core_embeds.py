"""The HOPELESS design system: one set of colours, titles and footers for every embed.

Colours: brand red for identity, green only for success, amber for warnings and permission problems, a stronger
red for errors and dangerous actions, charcoal for neutral status. Titles are short; system embeds carry a
footer "HOPELESS • <System>" so you can tell at a glance which part of the bot spoke.
"""
from __future__ import annotations

from typing import Optional

import discord

import core_branding as branding
from core_emoji import ICONS

BRAND = branding.ACCENT_COLOR  # deep red
SUCCESS = 0x2E9E5B
WARNING = 0xE0A030
ERROR = 0xC0262D
NEUTRAL = 0x2B2D31
SILVER = 0xC0C0C0
NAME = branding.BOT_NAME


def footer(embed: discord.Embed, system: Optional[str] = None) -> discord.Embed:
    embed.set_footer(text=f"{NAME} • {system}" if system else NAME)
    return embed


def system_embed(system: str, title: str, description: str = "", *, color: int = BRAND) -> discord.Embed:
    """A titled embed for one of the bot's systems (Tickets, Moderation, Presence...)."""
    return footer(discord.Embed(title=title, description=description, color=color), system)


def ok_embed(text: str, title: str = "Success") -> discord.Embed:
    return discord.Embed(title=f"{ICONS.get('success')} {title}", description=text, color=SUCCESS)


def error_embed(text: str, title: str = "Error") -> discord.Embed:
    return discord.Embed(title=f"{ICONS.get('error')} {title}", description=text, color=ERROR)


def warn_embed(text: str, title: str = "Careful") -> discord.Embed:
    return discord.Embed(title=f"⚠ {title}", description=text, color=WARNING)


def deny_embed(text: str, title: str = "Permission denied") -> discord.Embed:
    return discord.Embed(title=f"⛔ {title}", description=text, color=WARNING)


def loading_embed(text: str = "Working on it…") -> discord.Embed:
    return discord.Embed(description=f"⏳ {text}", color=NEUTRAL)


def info_embed(title: Optional[str], description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=BRAND)


def toggle_embed(name: str, enabled: bool, extra: str = "") -> discord.Embed:
    icon = ICONS.get("toggle_on" if enabled else "toggle_off")
    text = f"{icon} `{name}` is now **{'enabled' if enabled else 'disabled'}**."
    return ok_embed(text + (f"\n{extra}" if extra else ""))

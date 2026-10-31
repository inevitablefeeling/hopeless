"""Branding constants for the HOPELESS identity."""
from __future__ import annotations

from pathlib import Path

BOT_NAME = "HOPELESS"
BOT_VERSION = "1.1.0"
# One line, lowercase, shown on the help home page. Change it here and it updates everywhere.
TAGLINE = "for those who abandoned hope"
BOT_DESCRIPTION = "security-first moderation and anti-nuke bot"
WHITECHECK = "<a:vnx_whitecheck:1565301514711465989>"  # the success mark used in short confirmations
ACCENT_COLOR = 0x9B111E  # deep red

ASSETS_DIR = Path(__file__).resolve().parent
BANNER_FILENAME = "hopeless_banner.jpg"
BANNER_PATH = ASSETS_DIR / BANNER_FILENAME
BANNER_ATTACHMENT_URL = f"attachment://{BANNER_FILENAME}"


def has_banner() -> bool:
    return BANNER_PATH.is_file()


def banner_file():
    """A fresh discord.File for the help banner, or None when the asset is missing."""
    if not has_banner():
        return None
    import discord  # imported lazily so pure-logic modules stay importable without discord.py

    return discord.File(BANNER_PATH, filename=BANNER_FILENAME)

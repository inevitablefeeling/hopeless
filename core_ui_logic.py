"""Pure helpers for user-supplied design settings (colours, links, emoji, styles)."""
from __future__ import annotations

import re
from typing import Optional

from core_errors import InvalidInput

NAMED_COLORS = {"red": 0x9B111E, "black": 0x111111, "charcoal": 0x2B2D31, "white": 0xF2F2F2, "silver": 0xC0C0C0,
                "green": 0x2E9E5B, "amber": 0xE0A030, "blue": 0x3B6FD4, "gray": 0x808080, "grey": 0x808080}
BUTTON_STYLES = ("primary", "secondary", "success", "danger")
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_CUSTOM_EMOJI = re.compile(r"^<a?:\w{2,32}:\d{15,25}>$")


def parse_color(text: Optional[str]) -> Optional[int]:
    """'#9B111E', '9b111e' or a name -> int. Empty means 'use the default' (None)."""
    value = (text or "").strip().lower()
    if not value or value in ("default", "none", "reset"):
        return None
    if value in NAMED_COLORS:
        return NAMED_COLORS[value]
    match = _HEX.match(value)
    if match:
        return int(match.group(1), 16)
    raise InvalidInput("I couldn't read that colour. Use a hex code like `#9B111E` or a name like `red`.")


def valid_https_url(text: Optional[str], *, what: str = "link") -> Optional[str]:
    value = (text or "").strip()
    if not value or value.lower() in ("none", "reset", "remove"):
        return None
    if not value.startswith("https://") or " " in value or len(value) > 500:
        raise InvalidInput(f"The {what} must be a direct `https://` link.")
    return value


def valid_emoji(text: Optional[str]) -> Optional[str]:
    """A unicode emoji or <:name:id> / <a:name:id>. Anything else is rejected (Discord would refuse it)."""
    value = (text or "").strip()
    if not value or value.lower() in ("none", "reset", "remove"):
        return None
    if _CUSTOM_EMOJI.match(value):
        return value
    if len(value) <= 12 and not any(ch.isascii() and ch.isalnum() for ch in value) and not any(ch.isspace() for ch in value):
        return value
    raise InvalidInput("Use a normal emoji or a custom one like `<:name:123456789012345678>`.")


def valid_button_style(text: Optional[str]) -> str:
    value = (text or "primary").strip().lower()
    if value not in BUTTON_STYLES:
        raise InvalidInput("Button style must be one of: " + ", ".join(BUTTON_STYLES) + ".")
    return value


def clean_text(text: Optional[str], limit: int, *, what: str = "text", required: bool = False) -> Optional[str]:
    value = (text or "").strip()
    if not value:
        if required:
            raise InvalidInput(f"The {what} can't be empty.")
        return None
    if len(value) > limit:
        raise InvalidInput(f"The {what} can be at most {limit} characters.")
    return value


def parse_bool(text: Optional[str], default: bool = True) -> bool:
    value = (text or "").strip().lower()
    if not value:
        return default
    if value in ("yes", "y", "true", "on", "1"):
        return True
    if value in ("no", "n", "false", "off", "0"):
        return False
    raise InvalidInput("Answer yes or no.")

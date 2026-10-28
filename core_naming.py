"""Pure naming helpers for voice rooms and ticket channels."""
from __future__ import annotations

import re


def room_name(template: str, display_name: str) -> str:
    safe = re.sub(r"[\x00-\x1f<>@#]", "", display_name).strip() or "member"
    return (template.replace("{user}", safe) or safe)[:100]


def ticket_channel_name(template: str, number: int, user_name: str, category: str = "") -> str:
    safe = re.sub(r"[^a-z0-9-]+", "-", user_name.lower()).strip("-") or "member"
    cat = re.sub(r"[^a-z0-9-]+", "-", category.lower()).strip("-")
    cat = re.sub(r"[^a-z0-9-]+", "-", category.lower()).strip("-")
    name = template.replace("{number}", f"{number:04d}").replace("{user}", safe).replace("{category}", cat).replace("{category}", cat)
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:90] or f"ticket-{number:04d}"

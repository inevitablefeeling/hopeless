"""Pure Prestige rules. Credits are internal to the bot; there is no payment processing of any kind."""
from __future__ import annotations

import base64
from typing import Optional

from core_errors import InvalidInput

MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_CREDIT_GRANT = 1000


def enable_cost(*, is_owner: bool) -> int:
    """Credits needed to activate Prestige for a server. Only the bot owner or a bot admin may activate;
    the owner does it free, a bot admin spends one credit the owner granted."""
    return 0 if is_owner else 1


def validate_grant(amount: int) -> int:
    if not isinstance(amount, int) or amount < 1 or amount > MAX_CREDIT_GRANT:
        raise InvalidInput(f"Grant between 1 and {MAX_CREDIT_GRANT} credits at a time.")
    return amount


def image_data_uri(content_type: Optional[str], data: bytes) -> str:
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind not in IMAGE_TYPES:
        raise InvalidInput("Use a png, jpg, gif or webp image.")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise InvalidInput("The image must be under 8 MB.")
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"


def tag_matches(display_name: str, tag: str) -> bool:
    return bool(tag) and tag.lower() in display_name.lower()

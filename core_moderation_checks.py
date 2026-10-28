"""Pure moderation logic: role-hierarchy checks, duration parsing, welcome placeholders."""
from __future__ import annotations

import re
from typing import Optional

from core_errors import InvalidInput

MAX_TIMEOUT_SECONDS = 28 * 86_400  # Discord's limit
_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86_400, "day": 86_400, "days": 86_400,
    "w": 604_800, "week": 604_800, "weeks": 604_800,
}
_DURATION = re.compile(r"(\d+)([a-z]+)")


def parse_duration(text: str, *, maximum: Optional[int] = None) -> int:
    """'1h30m', '1hr', '45 min', '2d' -> seconds. Raises InvalidInput with a helpful message."""
    raw = (text or "").strip().lower().replace(" ", "")
    pairs = _DURATION.findall(raw)
    if not raw or "".join(f"{n}{u}" for n, u in pairs) != raw or any(u not in _UNITS for _, u in pairs):
        raise InvalidInput("Invalid duration. Examples: `10m`, `1h`, `2d`, `1h30m`.")
    seconds = sum(int(n) * _UNITS[u] for n, u in pairs)
    if seconds <= 0:
        raise InvalidInput("The duration must be longer than zero.")
    if maximum is not None and seconds > maximum:
        raise InvalidInput(f"That's too long. The maximum is {maximum // 86_400} days.")
    return seconds


def short_duration(seconds: int) -> str:
    """3600 -> '1h', 5400 -> '1h30m', 90061 -> '1d1h1m1s'."""
    parts = []
    for unit, size in (("d", 86_400), ("h", 3600), ("m", 60), ("s", 1)):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts) or "0s"


def hierarchy_problem(
    *,
    actor_id: int,
    target_id: int,
    actor_top: int,
    target_top: int,
    bot_top: int,
    guild_owner_id: int,
    bot_id: int,
    bot_owner_id: int,
) -> Optional[str]:
    """Why an actor may not moderate a target, or None when it is allowed."""
    if target_id == actor_id:
        return "You can't do that to yourself."
    if target_id == bot_id:
        return "I won't do that to myself."
    if target_id == guild_owner_id:
        return "You can't moderate the server owner."
    if target_id == bot_owner_id:
        return "The bot owner can't be moderated with ordinary commands."
    if actor_id != guild_owner_id and actor_top <= target_top:
        return "That member's top role is equal to or above yours."
    if bot_top <= target_top:
        return "That member's top role is equal to or above mine, so I can't act on them."
    return None


_PLACEHOLDER = re.compile(r"\{(\w+)\}")
PLACEHOLDERS = ("user", "username", "server", "membercount")


def unknown_placeholders(template: str) -> list[str]:
    return sorted({m for m in _PLACEHOLDER.findall(template) if m not in PLACEHOLDERS})


def render_template(template: str, values: dict[str, str]) -> str:
    """Fill {user}, {username}, {server}, {membercount}. Unknown or missing ones are left untouched."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def roles_to_restore(saved, current, assignable) -> list[int]:
    """Roles to give back after jail: saved ones that still exist, that I may assign, and that the
    member doesn't already have. Roles gained or removed during the jail period are never overwritten."""
    have, ok = set(current), set(assignable)
    return [role_id for role_id in saved if role_id in ok and role_id not in have]

"""Pure rules for global staff permissions and global bans."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from core_errors import InvalidInput

GLOBAL_PERMS = {
    "gban": "issue and lift global bans",
    "servers": "see the list of servers the bot is in",
    "invite": "generate an invite for a server",
    "audit": "read the global audit log",
    "prestige": "activate or remove Prestige",
}
DEFAULT_ADMIN_PERMS = frozenset({"prestige"})  # a new administrator starts with this and nothing else


def validate_perm(name: str) -> str:
    name = (name or "").strip().lower()
    if name not in GLOBAL_PERMS:
        raise InvalidInput("Unknown permission. Choose from: " + ", ".join(f"`{p}`" for p in GLOBAL_PERMS) + ".")
    return name


def is_active(expires_at: Optional[datetime], now: datetime) -> bool:
    return expires_at is None or now < expires_at


def ban_denied(*, actor_id: int, actor_is_owner: bool, actor_has_gban: bool, target_id: int,
               target_is_owner: bool, target_role: Optional[str], bot_id: int) -> Optional[str]:
    """Why this global ban is not allowed (None = allowed)."""
    if not (actor_is_owner or actor_has_gban):
        return "You aren't authorised to issue global bans."
    if target_id == actor_id:
        return "You can't globally ban yourself."
    if target_is_owner:
        return "The bot owner can't be globally banned."
    if target_id == bot_id:
        return "I can't globally ban myself."
    if target_role is not None and not actor_is_owner:
        return "Only the bot owner can globally ban global staff."
    return None

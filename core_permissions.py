"""Permission categories. These are deliberately separate and never imply each other:

* Bot owner            - BOT_OWNER_ID environment variable, checked at execution time.
* Global bot staff     - administrators / moderators granted by the owner (bot-wide, not per server).
* Server security admin - guild owner or a member with Discord's Administrator permission.
* Server moderators    - the individual Discord permission each action needs (Ban Members, ...).
* Ticket staff / voice-room owners - scoped to their own ticket or room (added with those modules).

Global staff gain nothing inside a server, and server admins gain nothing globally.
"""
from __future__ import annotations

from enum import Enum


class GlobalRole(str, Enum):
    ADMINISTRATOR = "administrator"
    MODERATOR = "moderator"


def is_server_security_admin(*, is_guild_owner: bool, has_administrator: bool) -> bool:
    """Who may change security-sensitive settings (whitelist, antinuke) in one server."""
    return is_guild_owner or has_administrator

"""One place that decides who may do what. Pure functions over plain facts, so every rule is unit-tested.

Server side:  guild owner > Administrator > Manage Server ("server administrator") > moderators > ticket staff > members.
Global side:  bot owner (BOT_OWNER_ID) > global administrators > global moderators. Global power is never
inferred from server power: a server owner is not a bot owner, and an Administrator is not a global admin.
Configured moderator roles NARROW access (the Discord permission is still required); they never widen it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Level(str, Enum):
    PUBLIC = "public"
    MEMBER = "member"
    MODERATOR = "moderator"
    TICKET_STAFF = "ticket staff"
    ADMIN = "server administrator"
    SECURITY_ADMIN = "server owner or Administrator"
    SERVER_OWNER = "server owner"
    ROOM_OWNER = "voice room owner"
    GLOBAL_STAFF = "global staff"
    GLOBAL_PERM = "global staff with the permission"
    GLOBAL_OWNER = "bot owner"


@dataclass(frozen=True)
class Facts:
    is_global_owner: bool = False
    global_role: Optional[str] = None  # "administrator" | "moderator" | None
    global_perms: frozenset = frozenset()
    is_guild_owner: bool = False
    has_administrator: bool = False
    discord_perms: frozenset = frozenset()
    in_mod_role: bool = False
    mod_roles_configured: bool = False
    in_ticket_staff_role: bool = False


def is_server_admin(f: Facts) -> bool:
    """Server configuration tier: owner, Administrator or Manage Server."""
    return f.is_guild_owner or f.has_administrator or "manage_guild" in f.discord_perms


def is_security_admin(f: Facts) -> bool:
    """Security-sensitive tier (antinuke, whitelist, logging): owner or Administrator only."""
    return f.is_guild_owner or f.has_administrator


def can_moderate(f: Facts, perm: Optional[str]) -> bool:
    """Owner/Administrator always. Otherwise the Discord permission is REQUIRED, and when moderator roles are
    configured the member must also hold one of them."""
    if is_security_admin(f):
        return True
    if perm is None or perm not in f.discord_perms:
        return False
    return (not f.mod_roles_configured) or f.in_mod_role


def can_ticket_staff(f: Facts) -> bool:
    return f.in_ticket_staff_role or is_server_admin(f)


def can_global(f: Facts, perm: Optional[str] = None) -> bool:
    if f.is_global_owner:
        return True
    if f.global_role is None:
        return False
    return True if perm is None else perm in f.global_perms


def allowed(level: Level, f: Facts, needs: Optional[str] = None) -> bool:
    if level in (Level.PUBLIC, Level.MEMBER, Level.ROOM_OWNER):
        return True  # ROOM_OWNER is enforced against the room itself inside the voice handlers
    if level is Level.MODERATOR:
        return can_moderate(f, needs)
    if level is Level.TICKET_STAFF:
        return can_ticket_staff(f)
    if level is Level.ADMIN:
        return is_server_admin(f)
    if level is Level.SECURITY_ADMIN:
        return is_security_admin(f)
    if level is Level.SERVER_OWNER:
        return f.is_guild_owner
    if level is Level.GLOBAL_STAFF:
        return can_global(f)
    if level is Level.GLOBAL_PERM:
        return can_global(f, needs)
    if level is Level.GLOBAL_OWNER:
        return f.is_global_owner
    return False

"""Pure leveling rules. XP needed to go from level n to n+1 is 5n^2 + 50n + 100 (the common 'MEE6' curve)."""
from __future__ import annotations

import random
from typing import Optional


def xp_to_next(level: int) -> int:
    return 5 * level * level + 50 * level + 100


def total_xp_for(level: int) -> int:
    return sum(xp_to_next(n) for n in range(level))


def level_for(xp: int) -> int:
    level = 0
    while xp >= total_xp_for(level + 1):
        level += 1
    return level


def progress(xp: int) -> tuple[int, int, int]:
    """(level, xp into this level, xp needed for the next one)."""
    level = level_for(xp)
    return level, xp - total_xp_for(level), xp_to_next(level)


def can_award(last_ts: Optional[float], now: float, cooldown: int) -> bool:
    return last_ts is None or now - last_ts >= cooldown


def roll_xp(lo: int, hi: int, rng: Optional[random.Random] = None) -> int:
    rng = rng or random
    lo, hi = (hi, lo) if lo > hi else (lo, hi)
    return rng.randint(lo, hi)


def roles_for_level(level_roles: dict[int, int], level: int) -> list[int]:
    """Role ids earned at this level: every configured level reward at or below it."""
    return [role for lvl, role in sorted(level_roles.items()) if lvl <= level]

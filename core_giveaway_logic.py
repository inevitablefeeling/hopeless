"""Pure giveaway rules: winner selection and rerolls."""
from __future__ import annotations

import random
from typing import Iterable, Optional, Sequence

from core_errors import InvalidInput

MAX_WINNERS = 20
MAX_PRIZE = 200


def validate_prize(prize: str) -> str:
    prize = (prize or "").strip()
    if not prize:
        raise InvalidInput("Say what the prize is.")
    if len(prize) > MAX_PRIZE:
        raise InvalidInput(f"Keep the prize under {MAX_PRIZE} characters.")
    return prize


def validate_winners(count: int) -> int:
    if not 1 <= count <= MAX_WINNERS:
        raise InvalidInput(f"Winners must be between 1 and {MAX_WINNERS}.")
    return count


def pick_winners(entrants: Sequence[int], count: int, *, rng: Optional[random.Random] = None, exclude: Iterable[int] = ()) -> list[int]:
    """Up to `count` distinct winners, chosen uniformly at random, never anyone in `exclude`. Fewer if too few entered."""
    rng = rng or random.SystemRandom()
    banned = set(exclude)
    pool = [user for user in dict.fromkeys(entrants) if user not in banned]  # each person once, order kept
    return rng.sample(pool, min(count, len(pool)))

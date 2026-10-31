"""Short, plain wording for errors and rate limits, with enough variety that it never reads like a script.

Rules for everything in here: no jargon, no cute phrasing, never blame Discord or mention how fast anything goes,
and never the same sentence twice in a row for the same kind of message.
"""
from __future__ import annotations

import math
import random
from typing import Optional, Sequence

_last: dict[str, int] = {}
_rng = random.Random()

TRY_AGAIN: Sequence[str] = (
    "Try again.", "Try that again.", "That didn't go through. Try again.", "Try again in a moment.",
    "Give it another try.", "Please try again.", "Run it again.", "Try it once more.",
)
NO_RESPONSE: Sequence[str] = (
    "The bot didn't respond. Try again.", "The bot didn't respond.", "No response. Try again.", "The bot didn't answer. Try again.",
    "Nothing came back. Try again.", "The bot didn't respond, try again.", "The bot isn't responding. Try again in a moment.",
    "No answer from the bot. Try again.",
)
COOLDOWN: Sequence[str] = (
    "Try this command again in **{n} {unit}**.", "You can use this command again in **{n} {unit}**.",
    "Wait **{n} {unit}** and try this command again.", "Try this again in **{n} {unit}**.",
    "This command is ready again in **{n} {unit}**.", "Try again in **{n} {unit}**.",
    "Give it **{n} {unit}** and try again.", "Too many commands. Try again in **{n} {unit}**.",
)


def pick(key: str, options: Sequence[str], rng: Optional[random.Random] = None) -> str:
    """A random option that is never the same one this key returned last time."""
    rng = rng or _rng
    choices = [i for i in range(len(options)) if i != _last.get(key)]
    index = rng.choice(choices)
    _last[key] = index
    return options[index]


def try_again() -> str:
    return pick("error", TRY_AGAIN + NO_RESPONSE)


def no_response() -> str:
    return pick("no_response", NO_RESPONSE)


def cooldown(seconds: float) -> str:
    """'Try this command again in **2 seconds**.' Whole seconds, rounded up, at least 1."""
    n = max(1, math.ceil(seconds))
    return pick("cooldown", COOLDOWN).format(n=n, unit="second" if n == 1 else "seconds")

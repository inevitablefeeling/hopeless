"""Pure poll rules: option parsing, tallies, ties and vote changes."""
from __future__ import annotations

from typing import Optional, Sequence

from core_errors import InvalidInput

MAX_OPTIONS = 10
MAX_OPTION_LEN = 80
MAX_QUESTION = 200


def parse_options(text: str) -> list[str]:
    """'Pizza | Tacos | Sushi' (or separated by commas or new lines) -> clean options, 2 to 10, no duplicates."""
    raw = (text or "").replace("\n", "|").replace(";", "|")
    parts = raw.split("|") if "|" in raw else raw.split(",")
    options = [p.strip() for p in parts if p.strip()]
    seen, clean = set(), []
    for option in options:
        if len(option) > MAX_OPTION_LEN:
            raise InvalidInput(f"Each option can be at most {MAX_OPTION_LEN} characters.")
        if option.lower() in seen:
            raise InvalidInput(f"The option **{option[:40]}** appears twice.")
        seen.add(option.lower())
        clean.append(option)
    if len(clean) < 2:
        raise InvalidInput("A poll needs at least 2 options, separated with `|`. Example: `Pizza | Tacos | Sushi`")
    if len(clean) > MAX_OPTIONS:
        raise InvalidInput(f"A poll can have at most {MAX_OPTIONS} options.")
    return clean


def validate_question(question: str) -> str:
    question = (question or "").strip()
    if not question:
        raise InvalidInput("Write the poll question.")
    if len(question) > MAX_QUESTION:
        raise InvalidInput(f"Keep the question under {MAX_QUESTION} characters.")
    return question


def tally(votes: Sequence[int], option_count: int) -> list[int]:
    """Votes per option from a list of chosen option indexes (out-of-range indexes are ignored)."""
    counts = [0] * option_count
    for index in votes:
        if 0 <= index < option_count:
            counts[index] += 1
    return counts


def leaders(counts: Sequence[int]) -> list[int]:
    """Indexes of the winning option(s). More than one means a tie; nobody voting means no winner."""
    top = max(counts, default=0)
    return [i for i, c in enumerate(counts) if c == top] if top > 0 else []


def percent(count: int, total: int) -> int:
    return round(100 * count / total) if total else 0


def bar(count: int, total: int, width: int = 10) -> str:
    filled = round(width * count / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def vote_outcome(previous: Optional[int], chosen: int, allow_change: bool) -> str:
    """'new' (first vote), 'same' (already voted for this), 'changed' (allowed switch) or 'locked' (not allowed)."""
    if previous is None:
        return "new"
    if previous == chosen:
        return "same"
    return "changed" if allow_change else "locked"

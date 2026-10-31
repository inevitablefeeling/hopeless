"""Pure reputation rules: does a member qualify, and what should change."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

MAX_TARGETS = 10
MAX_TARGET_LEN = 60


def normalize_target(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def status_matches(status_text: Optional[str], targets: Iterable[str]) -> bool:
    """True when the custom status contains any target text (case-insensitive)."""
    haystack = normalize_target(status_text or "")
    return bool(haystack) and any(t and normalize_target(t) in haystack for t in targets)


def tag_matches(identity_enabled: Optional[bool], identity_guild_id: Optional[int], guild_id: int) -> bool:
    """True when the member wears THIS server's tag (the tag must be shown on their profile and belong to this server)."""
    return bool(identity_enabled) and identity_guild_id == guild_id


@dataclass(frozen=True)
class Change:
    grant: bool = False
    remove: bool = False


def plan(*, qualifies: bool, tracked: bool, has_role: bool, other_condition_keeps_role: bool = False) -> Change:
    """What to do for one condition.

    grant   when they qualify and don't have the role yet.
    remove  when they stopped qualifying, we granted it (tracked) and no other condition still justifies the role.
    """
    if qualifies:
        return Change(grant=not has_role)
    if tracked and has_role and not other_condition_keeps_role:
        return Change(remove=True)
    return Change()

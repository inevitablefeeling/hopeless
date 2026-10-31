"""Which log channel an event goes to, and who deleted a message (pure, so it is unit-tested).

HOPELESS uses exactly two log channels: #logs for ordinary server events and #security-logs for antinuke alerts
and security incidents.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

CHANNELS = ("server", "security")
CHANNEL_NAMES = {"server": "logs", "security": "security-logs"}
DELETE_WINDOW = 8.0  # seconds: an audit entry older than this is not evidence about the deletion we just saw


def channel_for(event: str) -> str:
    """Every ordinary event, including moderation actions, goes to #logs; only security alerts use #security-logs."""
    return "server"


@dataclass(frozen=True)
class DeleteEntry:
    """The few facts of a 'message delete' audit-log entry that matter for attribution."""
    entry_id: int
    user_id: int
    target_id: Optional[int]    # the author whose message was deleted
    channel_id: Optional[int]   # the channel it was deleted in
    created_ts: float


def pick_deleter(entries: Iterable[DeleteEntry], *, author_id: int, channel_id: int, now_ts: float,
                 window: float = DELETE_WINDOW) -> Optional[int]:
    """The moderator who deleted a message, only when an audit entry matches the author, the channel and the timing.

    Returns None (shown as "Unknown") otherwise. Deleting your own message creates no audit entry, and a stale or
    unrelated entry is never used as a guess.
    """
    best: Optional[DeleteEntry] = None
    for entry in entries:
        if entry.target_id != author_id or entry.channel_id != channel_id:
            continue
        if not (0 <= now_ts - entry.created_ts <= window):
            continue
        if best is None or entry.created_ts > best.created_ts:
            best = entry
    return best.user_id if best else None

"""Pure antinuke decisions. No Discord objects here, so every rule is unit-tested."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from core_errors import InvalidInput

PUNISHMENTS = ("log", "kick", "ban", "jail", "strip")
DEFAULT_PUNISHMENT = "kick"
PUNISHMENT_HELP = (
    "log (alert only), kick, ban, jail, strip (remove the dangerous roles/permissions)"
)

DEFAULT_ALERT_AT = 2
DEFAULT_PUNISH_AT = 3
DEFAULT_WINDOW = 60
BULK_THRESHOLD = 5  # destructive events by one actor inside the window


class Level(str, Enum):
    RECORD = "record"  # first incident: note it
    ALERT = "alert"  # second: urgent alert
    PUNISH = "punish"  # third or more: apply the configured response


def decide(count: int, *, alert_at: int = DEFAULT_ALERT_AT, punish_at: int = DEFAULT_PUNISH_AT) -> Level:
    if count >= punish_at:
        return Level.PUNISH
    if count >= alert_at:
        return Level.ALERT
    return Level.RECORD


def validate_punishment(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in PUNISHMENTS:
        raise InvalidInput(f"Pick one of: {PUNISHMENT_HELP}.")
    return value


@dataclass(frozen=True)
class AuditCandidate:
    entry_id: int
    actor_id: Optional[int]
    target_id: Optional[int]
    created_ts: float


def best_audit_match(
    entries: Iterable[AuditCandidate], *, target_id: Optional[int], event_ts: float, max_age: float = 15.0
) -> Optional[AuditCandidate]:
    """Pick the audit entry that really belongs to this event.

    The newest entry is NOT assumed to be the right one: it must name the same target, have a known
    actor, and sit within `max_age` seconds of the event. Of those, the closest in time wins.
    Returns None when nothing matches reliably - callers must then not punish anyone.
    """
    best: Optional[AuditCandidate] = None
    for entry in entries:
        if entry.actor_id is None:
            continue
        if target_id is not None and entry.target_id != target_id:
            continue
        if abs(event_ts - entry.created_ts) > max_age:
            continue
        if best is None or abs(event_ts - entry.created_ts) < abs(event_ts - best.created_ts):
            best = entry
    return best


# Discord permission bits that make someone dangerous in a raid.
DANGEROUS_BITS = {
    "kick_members": 1 << 1,
    "ban_members": 1 << 2,
    "administrator": 1 << 3,
    "manage_channels": 1 << 4,
    "manage_guild": 1 << 5,
    "mention_everyone": 1 << 17,
    "manage_roles": 1 << 28,
    "manage_webhooks": 1 << 29,
}


def dangerous_gained(old_value: int, new_value: int) -> list[str]:
    """Dangerous permissions present in `new_value` but not in `old_value`."""
    return [name for name, bit in DANGEROUS_BITS.items() if new_value & bit and not old_value & bit]


def has_dangerous(value: int) -> list[str]:
    return [name for name, bit in DANGEROUS_BITS.items() if value & bit]


def skip_reason(
    *,
    actor_id: Optional[int],
    bot_id: int,
    guild_owner_id: int,
    exempt: bool,
) -> Optional[str]:
    """Why an actor must never be punished (None means they may be)."""
    if actor_id is None:
        return "the responsible actor could not be identified reliably"
    if actor_id == bot_id:
        return "the actor is this bot"
    if actor_id == guild_owner_id:
        return "the actor is the server owner"
    if exempt:
        return "the actor is whitelisted for this protection"
    return None


# key -> (title, description of what counts)
MODULE_KEYS = (
    "ban", "kick", "bot_add", "channel_create", "channel_delete", "channel_update", "role_create",
    "role_delete", "role_update", "webhook_create", "guild_update", "emoji_update", "sticker_update",
    "integration_update", "invite_create", "invite_delete", "webhook_update", "webhook_delete",
    "role_permission_escalation", "channel_permission_escalation", "admin_grant", "mass_mention",
    "mass_message_delete", "vanity_change", "bulk_destruction",
)
# Modules that count towards the combined "bulk destruction" detector.
DESTRUCTIVE = frozenset({"ban", "kick", "channel_delete", "role_delete", "webhook_delete", "mass_message_delete"})
# Sensible defaults used by the one-click setup (everything on; first offense only alerts).


def module_display(key: str) -> str:
    """The name people type and see in the status list: ban -> antiban, bot_add -> antibotadd."""
    return "anti" + key.replace("_", "")


RESTORE_WINDOW = 180.0  # only deletions inside this burst are recreated, never older, intentional ones


def recent_ids(entries: list[tuple[float, int]], now: float, window: float = RESTORE_WINDOW) -> list[int]:
    """Ids deleted within `window` seconds of `now` (the burst), oldest first."""
    return [item_id for stamp, item_id in sorted(entries) if now - stamp <= window]


def is_urgent(level: "Level") -> bool:
    """Urgent = the second incident or worse. A single incident is only recorded and nobody is pinged."""
    return level in (Level.ALERT, Level.PUNISH)


# --- snapshot safety --------------------------------------------------------------------------------------
class SnapshotGuard:
    """Keeps the last known good snapshot from being overwritten by attack damage or by a restoration.

    While an incident is suspected (an untrusted or unidentified actor changed something) and while a restoration
    is running, snapshots are not refreshed. After either, a quiet period passes before they resume.
    """

    def __init__(self, quiet: float = 600.0) -> None:
        self.quiet = quiet
        self._until: dict[int, float] = {}
        self._restoring: set[int] = set()

    def mark_incident(self, guild_id: int, now: float) -> None:
        self._until[guild_id] = max(self._until.get(guild_id, 0.0), now + self.quiet)

    def begin_restore(self, guild_id: int) -> None:
        self._restoring.add(guild_id)

    def end_restore(self, guild_id: int, now: float) -> None:
        self._restoring.discard(guild_id)
        self.mark_incident(guild_id, now)

    def can_snapshot(self, guild_id: int, now: float) -> bool:
        return guild_id not in self._restoring and now >= self._until.get(guild_id, 0.0)


def restore_order(items: list[dict]) -> list[dict]:
    """Roles first (channel overwrites refer to them), then categories, then other channels, each by position."""
    def rank(item: dict) -> tuple[int, int]:
        if item.get("_type") == "role":
            return (0, item.get("position", 0))
        return (1 if item.get("kind") == "category" else 2, item.get("position", 0))
    return sorted(items, key=rank)


def resolve_parent(parent_old_id: Optional[int], existing_ids: set[int], id_map: dict[int, int]) -> Optional[int]:
    """The id of the category a restored channel should go in: the old one if it still exists, else its recreated copy."""
    if parent_old_id is None:
        return None
    if parent_old_id in existing_ids:
        return parent_old_id
    return id_map.get(parent_old_id)

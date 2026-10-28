"""Pure ticket rules: limits, ordering, closing delay, and the transcript text."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from core_errors import InvalidInput

DEFAULT_CATEGORIES = (
    ("Support", "Get help from our staff"), ("Contact", "Reach the team"), ("Partnership", "Partner with us"),
    ("Report", "Report a member or an issue"), ("Ban Appeal", "Appeal a ban"), ("Other", "Anything else"),
)
MAX_CATEGORIES = 25
MAX_DELAY = 60
DEFAULT_DELAY = 3


def can_open(open_count: int, limit: int) -> bool:
    return open_count < max(1, limit)


def clamp_delay(value: object) -> int:
    try:
        seconds = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise InvalidInput(f"The closing delay must be a number of seconds from 0 to {MAX_DELAY}.")
    if not 0 <= seconds <= MAX_DELAY:
        raise InvalidInput(f"The closing delay must be from 0 to {MAX_DELAY} seconds.")
    return seconds


def move(order: Sequence[int], item: int, direction: int) -> list[int]:
    """Swap `item` one place up (-1) or down (+1); out-of-range moves leave the order unchanged."""
    items = list(order)
    if item not in items:
        return items
    i = items.index(item)
    j = i + direction
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
    return items


def staff_role_ids(config_roles: Iterable[int], legacy_role: Optional[int]) -> set[int]:
    roles = {int(r) for r in config_roles}
    if legacy_role:
        roles.add(int(legacy_role))
    return roles


def build_transcript(header: dict[str, str], events: Sequence[tuple[datetime, str]], messages: Sequence[dict]) -> str:
    """Plain-text transcript: ticket facts, staff actions, then the conversation with attachment info."""
    stamp = lambda dt: dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")  # noqa: E731
    lines = ["HOPELESS TICKET TRANSCRIPT", "=" * 40]
    lines += [f"{key}: {value}" for key, value in header.items()]
    lines += ["", "STAFF ACTIONS", "-" * 40]
    lines += [f"[{stamp(at)}] {text}" for at, text in events] or ["(none)"]
    lines += ["", "MESSAGES", "-" * 40]
    for m in messages:
        body = m.get("content") or ""
        lines.append(f"[{stamp(m['at'])}] {m['author']} ({m['author_id']}): {body}".rstrip())
        for a in m.get("attachments", []):
            lines.append(f"    attachment: {a['name']} ({a['size']} bytes) {a['url']}")
    if not messages:
        lines.append("(no messages)")
    return "\n".join(lines)

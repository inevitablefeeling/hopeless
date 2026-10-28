"""Server templates. A template is plain data; planning is pure so previews are exact and testable.

Templates only describe roles, categories and channels. They can't carry messages, members, bots or
anything Discord doesn't let us create. Applying never deletes anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

MAX_ROLES = 250
MAX_CHANNELS = 500
STAFF = "Staff"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_\u00c0-\uffff-]+", "-", name.strip().lower()).strip("-") or "channel"


def _text(name, topic="", readonly=False, staff_only=False):
    return {"name": name, "type": "text", "topic": topic, "readonly": readonly, "staff_only": staff_only}


def _voice(name, staff_only=False):
    return {"name": name, "type": "voice", "topic": "", "readonly": False, "staff_only": staff_only}


def _staff_category():
    return {"name": "Staff", "channels": [_text("staff-chat", staff_only=True), _voice("Staff Voice", True)]}


def _roles(*names):
    return [{"name": n, "color": c, "permissions": p, "hoist": h, "mentionable": False} for n, c, p, h in names]


BUILTIN: dict[str, dict] = {
    "general": {
        "label": "General community",
        "roles": _roles(("Staff", 0x9B111E, 0, True), ("Member", 0x808080, 0, False)),
        "categories": [
            {"name": "Info", "channels": [_text("rules", "Read before chatting", readonly=True), _text("announcements", readonly=True)]},
            {"name": "Chat", "channels": [_text("general"), _text("media"), _voice("Lounge")]},
            _staff_category(),
        ],
    },
    "gaming": {
        "label": "Gaming community",
        "roles": _roles(("Staff", 0x9B111E, 0, True), ("Player", 0x808080, 0, False)),
        "categories": [
            {"name": "Info", "channels": [_text("rules", readonly=True), _text("announcements", readonly=True)]},
            {"name": "Chat", "channels": [_text("general"), _text("clips"), _text("looking-for-group")]},
            {"name": "Voice", "channels": [_voice("Lobby"), _voice("Squad 1"), _voice("Squad 2")]},
            _staff_category(),
        ],
    },
    "support": {
        "label": "Support server",
        "roles": _roles(("Staff", 0x9B111E, 0, True), ("Customer", 0x808080, 0, False)),
        "categories": [
            {"name": "Info", "channels": [_text("rules", readonly=True), _text("announcements", readonly=True), _text("faq", readonly=True)]},
            {"name": "Help", "channels": [_text("ask-for-help"), _text("feedback")]},
            _staff_category(),
        ],
    },
    "creator": {
        "label": "Creator community",
        "roles": _roles(("Staff", 0x9B111E, 0, True), ("Supporter", 0x808080, 0, False)),
        "categories": [
            {"name": "Info", "channels": [_text("rules", readonly=True), _text("announcements", readonly=True), _text("new-content", readonly=True)]},
            {"name": "Community", "channels": [_text("general"), _text("fan-art"), _voice("Hangout")]},
            _staff_category(),
        ],
    },
    "private": {
        "label": "Small private community",
        "roles": _roles(("Staff", 0x9B111E, 0, True)),
        "categories": [
            {"name": "Chat", "channels": [_text("general"), _text("media"), _voice("Hangout")]},
            _staff_category(),
        ],
    },
}


@dataclass(frozen=True)
class PlannedAction:
    kind: str  # role | category | channel
    name: str
    parent: Optional[str]  # category name for channels
    exists: bool

    @property
    def line(self) -> str:
        where = f" in **{self.parent}**" if self.parent else ""
        status = "keep" if self.exists else "create"
        return f"{status} {self.kind} **{self.name}**{where}"


def plan_template(
    template: dict,
    *,
    roles: set[str],
    categories: set[str],
    channels: set[tuple[str, str]],
) -> list[PlannedAction]:
    """What applying `template` would do. `roles`/`categories` hold lowercase names; `channels` holds
    (lowercase category name, slugged channel name). Existing items are kept, never replaced."""
    actions = [PlannedAction("role", r["name"], None, r["name"].lower() in roles) for r in template.get("roles", [])]
    for category in template.get("categories", []):
        cname = category["name"]
        actions.append(PlannedAction("category", cname, None, cname.lower() in categories))
        for channel in category.get("channels", []):
            exists = (cname.lower(), slug(channel["name"])) in channels
            actions.append(PlannedAction("channel", channel["name"], cname, exists))
    return actions


def limits_problem(actions: list[PlannedAction], *, role_count: int, channel_count: int) -> Optional[str]:
    new_roles = sum(1 for a in actions if a.kind == "role" and not a.exists)
    new_channels = sum(1 for a in actions if a.kind in ("category", "channel") and not a.exists)
    if role_count + new_roles > MAX_ROLES:
        return f"This would exceed Discord's {MAX_ROLES}-role limit."
    if channel_count + new_channels > MAX_CHANNELS:
        return f"This would exceed Discord's {MAX_CHANNELS}-channel limit."
    return None


def validate_template(data: object) -> dict:
    """Light structural check for templates loaded from the database."""
    if not isinstance(data, dict) or not isinstance(data.get("categories", []), list):
        raise ValueError("invalid template")
    for category in data.get("categories", []):
        if not isinstance(category.get("name"), str) or not isinstance(category.get("channels", []), list):
            raise ValueError("invalid template")
    return data

"""Which log channel an event goes to (pure, so it is unit-tested)."""
from __future__ import annotations

CHANNELS = ("message", "member", "server", "voice", "emoji", "invite", "ticket", "security")
CHANNEL_NAMES = {key: f"{key}-logs" for key in CHANNELS}
_ROUTES = {
    "message_edit": "message", "message_delete": "message",
    "member_join": "member", "member_leave": "member", "member_update": "member",
    "role_create": "server", "role_delete": "server", "role_update": "server",
    "channel_create": "server", "channel_delete": "server", "channel_update": "server",
    "voice": "voice", "emoji": "emoji", "invite": "invite", "ticket": "ticket", "moderation": "security",
}


def channel_for(event: str) -> str:
    """The channel key for a server-log event; unknown events fall back to server-logs."""
    return _ROUTES.get(event, "server")

"""Keys and titles for every antinuke protection and automod rule.

Whitelist scopes are validated against this list. Listing a protection here does NOT mean it is
implemented; implementation status lives in core.command_registry and CHECKLIST.md.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityModule:
    key: str
    title: str


ANTINUKE_MODULES: tuple[SecurityModule, ...] = (
    SecurityModule("ban", "Anti-Ban"),
    SecurityModule("kick", "Anti-Kick"),
    SecurityModule("bot_add", "Anti-Bot Add"),
    SecurityModule("channel_create", "Anti-Channel Create"),
    SecurityModule("channel_delete", "Anti-Channel Delete"),
    SecurityModule("channel_update", "Anti-Channel Update"),
    SecurityModule("role_create", "Anti-Role Create"),
    SecurityModule("role_delete", "Anti-Role Delete"),
    SecurityModule("role_update", "Anti-Role Update"),
    SecurityModule("webhook_create", "Anti-Webhook Create"),
    SecurityModule("guild_update", "Anti-Guild Update"),
    SecurityModule("emoji_update", "Anti-Emoji Update"),
    SecurityModule("sticker_update", "Anti-Sticker Update"),
    SecurityModule("integration_update", "Anti-Integration Changes"),
    SecurityModule("invite_create", "Anti-Invite Create"),
    SecurityModule("invite_delete", "Anti-Invite Delete"),
    SecurityModule("webhook_update", "Anti-Webhook Update"),
    SecurityModule("webhook_delete", "Anti-Webhook Delete"),
    SecurityModule("role_permission_escalation", "Anti-Role Permission Escalation"),
    SecurityModule("channel_permission_escalation", "Anti-Channel Permission Escalation"),
    SecurityModule("admin_grant", "Anti-Administrator Grant"),
    SecurityModule("mass_mention", "Anti-Mass Mention (privileged actors)"),
    SecurityModule("mass_message_delete", "Anti-Mass Message Delete"),
    SecurityModule("vanity_change", "Anti-Vanity URL Change"),
    SecurityModule("bulk_destruction", "Anti-Bulk Destruction"),
)

AUTOMOD_RULES: tuple[SecurityModule, ...] = (
    SecurityModule("link", "Anti-Link"),
    SecurityModule("mention", "Anti-Mention"),
    SecurityModule("nsfw", "Anti-NSFW"),
    SecurityModule("raid", "Anti-Raid"),
    SecurityModule("spam", "Anti-Spam"),
)

AREAS: dict[str, tuple[SecurityModule, ...]] = {
    "antinuke": ANTINUKE_MODULES,
    "automod": AUTOMOD_RULES,
}

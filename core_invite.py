"""Invite URL. Requests only the permissions the modules need - never Administrator."""
from __future__ import annotations

import discord

# Every permission here is used by a planned or existing module (see README for the per-module table).
INVITE_PERMISSIONS = dict(
    view_channel=True,
    send_messages=True,
    embed_links=True,
    attach_files=True,
    read_message_history=True,
    add_reactions=True,
    use_external_emojis=True,
    manage_messages=True,
    manage_roles=True,
    manage_channels=True,
    manage_webhooks=True,
    manage_guild=True,  # reading invites for invite logging
    view_audit_log=True,
    kick_members=True,
    ban_members=True,
    moderate_members=True,
    move_members=True,
)


def invite_url(client_id: int) -> str:
    return discord.utils.oauth_url(
        client_id,
        permissions=discord.Permissions(**INVITE_PERMISSIONS),
        scopes=("bot", "applications.commands"),
    )

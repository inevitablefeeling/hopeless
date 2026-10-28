"""One place that turns exceptions into short, safe messages.

Users never see stack traces, tokens or database details. Unexpected errors get a short id that is
also written to the private log so the owner can find them.
"""
from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core_embeds import deny_embed, error_embed
from core_errors import HopelessError, NotAuthorized

log = logging.getLogger("hopeless.errors")

# Hybrid slash commands can surface the same failure through both the command-error and the
# tree-error hook. This makes sure each interaction is answered once, in either order.
_handled: "OrderedDict[int, None]" = OrderedDict()


def _claim(interaction_id: int) -> bool:
    if interaction_id in _handled:
        return False
    _handled[interaction_id] = None
    while len(_handled) > 256:
        _handled.popitem(last=False)
    return True


def _unwrap(error: BaseException) -> BaseException:
    for _ in range(4):
        inner = getattr(error, "original", None)
        if inner is not None and isinstance(
            error, (commands.CommandInvokeError, commands.HybridCommandError, app_commands.CommandInvokeError)
        ):
            error = inner
        else:
            break
    return error


def _perm_names(missing: list[str]) -> str:
    return ", ".join(name.replace("_", " ").title() for name in missing)


def describe_error(error: BaseException, *, usage: Optional[str] = None) -> tuple[Optional[str], bool]:
    """Return (text for the user or None to stay silent, whether this is an unexpected error)."""
    error = _unwrap(error)
    hint = f" Usage: `{usage}`" if usage else ""

    if isinstance(error, HopelessError):
        return error.user_message, False
    if isinstance(error, commands.CommandNotFound):
        return None, False
    if isinstance(error, commands.NoPrivateMessage):
        return "This command only works in servers.", False
    if isinstance(error, (commands.MissingPermissions, app_commands.MissingPermissions)):
        return f"You need the {_perm_names(error.missing_permissions)} permission to do that.", False
    if isinstance(error, (commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
        return f"I'm missing the {_perm_names(error.missing_permissions)} permission for that.", False
    if isinstance(error, commands.MissingRequiredArgument):
        return f"You're missing `{error.param.name}`.{hint}", False
    if isinstance(error, (commands.BadArgument, commands.BadUnionArgument, commands.BadLiteralArgument)):
        return f"I couldn't understand one of your arguments.{hint}", False
    if isinstance(error, commands.TooManyArguments):
        return f"That's too many arguments.{hint}", False
    if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
        return f"Slow down. Try again in {error.retry_after:.0f}s.", False
    if isinstance(error, (commands.CheckFailure, app_commands.CheckFailure)):
        message = str(error)
        if not message or "check functions" in message:
            return "You can't use this command here.", False
        return message, False
    if isinstance(error, discord.Forbidden):
        return "Discord won't let me do that. Check my permissions and that my role sits above the target.", False
    if isinstance(error, discord.NotFound):
        return "That no longer exists.", False
    if isinstance(error, discord.HTTPException):
        return f"Discord rejected that request (HTTP {error.status}). Please try again.", False
    return "Something went wrong on my side.", True


def _embed_for(error: BaseException, text: str) -> discord.Embed:
    """Permission problems get the amber 'Permission denied' look; real errors stay red."""
    error = _unwrap(error)
    denial = (commands.MissingPermissions, app_commands.MissingPermissions, commands.CheckFailure, app_commands.CheckFailure, NotAuthorized)
    if isinstance(error, denial) and not isinstance(error, (commands.NoPrivateMessage, commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
        return deny_embed(text)
    return error_embed(text)


async def handle_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    interaction = ctx.interaction
    if interaction is not None and not _claim(interaction.id):
        return
    usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}".strip() if ctx.command else None
    text, unexpected = describe_error(error, usage=usage)
    if unexpected:
        error_id = uuid.uuid4().hex[:8]
        log.error(
            "unhandled command error id=%s command=%s guild=%s user=%s",
            error_id,
            ctx.command.qualified_name if ctx.command else "?",
            ctx.guild.id if ctx.guild else None,
            ctx.author.id,
            exc_info=_unwrap(error),
        )
        text = f"{text} Error id `{error_id}`."
    if text is None:
        return
    try:
        await ctx.send(embed=_embed_for(error, text), ephemeral=True)
    except discord.HTTPException:
        log.warning("could not deliver an error message in channel %s", getattr(ctx.channel, "id", None))


async def handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if not _claim(interaction.id):
        return
    text, unexpected = describe_error(error)
    if unexpected:
        error_id = uuid.uuid4().hex[:8]
        log.error(
            "unhandled app command error id=%s command=%s guild=%s user=%s",
            error_id,
            interaction.command.qualified_name if interaction.command else "?",
            interaction.guild_id,
            interaction.user.id,
            exc_info=_unwrap(error),
        )
        text = f"{text} Error id `{error_id}`."
    if text is None:
        return
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=_embed_for(error, text), ephemeral=True)
        else:
            await interaction.response.send_message(embed=_embed_for(error, text), ephemeral=True)
    except discord.HTTPException:
        log.warning("could not deliver an error message for interaction %s", interaction.id)

"""Discord command checks. All of them run at execution time, never just by hiding a command."""
from __future__ import annotations

from discord.ext import commands

from core_permissions import is_server_security_admin


def owner_only():
    """Prefix/hybrid command check: only BOT_OWNER_ID may run it."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.bot.permissions.is_owner(ctx.author.id):
            return True
        raise commands.CheckFailure("Only the bot owner can use this command.")

    return commands.check(predicate)


def ensure_security_admin(ctx: commands.Context) -> bool:
    """Guild owner or Administrator permission. Raises a CheckFailure with a clear message otherwise."""
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    member = ctx.author
    allowed = is_server_security_admin(
        is_guild_owner=ctx.guild.owner_id == member.id,
        has_administrator=member.guild_permissions.administrator,
    )
    if not allowed:
        raise commands.CheckFailure(
            "Only the server owner or members with the Administrator permission can change security settings."
        )
    return True


# ---------------------------------------------------------------------------------------------
# Central authorization (see core.authz). Every check runs at execution time, for prefix commands, slash
# commands and aliases alike (they share the same command object). Buttons and menus call the same helpers.
# ---------------------------------------------------------------------------------------------
import core_authz as authz


class GloballyBanned(commands.CheckFailure):
    """The caller is on the bot-wide ban list."""


def facts_for(bot, user, guild=None, mod_roles=()) -> authz.Facts:
    member = guild.get_member(user.id) if guild is not None else None
    role = bot.permissions.global_role(user.id)
    perms = frozenset(name for name, value in member.guild_permissions if value) if member is not None else frozenset()
    return authz.Facts(
        is_global_owner=bot.permissions.is_owner(user.id),
        global_role=role.value if role is not None else None,
        global_perms=bot.globals.perms_of(user.id),
        is_guild_owner=bool(guild is not None and guild.owner_id == user.id),
        has_administrator=bool(member is not None and member.guild_permissions.administrator),
        discord_perms=perms,
        in_mod_role=bool(member is not None and any(r.id in set(mod_roles) for r in member.roles)),
        mod_roles_configured=bool(mod_roles),
    )


def moderator(perm: str):
    """Moderation: the Discord permission is REQUIRED; if the server configured moderator roles, one is required too."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        mod_roles = await ctx.bot.staffroles.mod_roles(ctx.guild.id)
        facts = facts_for(ctx.bot, ctx.author, ctx.guild, mod_roles)
        if authz.can_moderate(facts, perm):
            return True
        if perm in facts.discord_perms:  # has the permission but not a configured moderator role
            raise commands.CheckFailure("This server limits moderation to its moderator roles, and you don't have one.")
        raise commands.MissingPermissions([perm])

    return commands.check(predicate)


def server_admin():
    """Server configuration: owner, Administrator or Manage Server."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if authz.is_server_admin(facts_for(ctx.bot, ctx.author, ctx.guild)):
            return True
        raise commands.MissingPermissions(["manage_guild"])

    return commands.check(predicate)


def global_owner():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.bot.permissions.is_owner(ctx.author.id):
            return True
        raise commands.CheckFailure("Only the bot owner can use this command.")

    return commands.check(predicate)


def global_staff():
    async def predicate(ctx: commands.Context) -> bool:
        if authz.can_global(facts_for(ctx.bot, ctx.author, ctx.guild)):
            return True
        raise commands.CheckFailure("This command is for global bot staff only.")

    return commands.check(predicate)


def global_perm(perm: str):
    async def predicate(ctx: commands.Context) -> bool:
        if authz.can_global(facts_for(ctx.bot, ctx.author, ctx.guild), perm):
            return True
        raise commands.CheckFailure(f"This needs the global `{perm}` permission, which only the bot owner can grant.")

    return commands.check(predicate)


async def global_ban_gate(ctx: commands.Context) -> bool:
    """Registered as a bot-wide check: a globally banned user can't use any command, alias or hybrid slash command."""
    message = ctx.bot.globals.ban_message(ctx.author.id)
    if message:
        raise GloballyBanned(message)
    return True

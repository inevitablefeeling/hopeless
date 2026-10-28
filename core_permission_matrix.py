"""The permission matrix: for every registered command, who may use it. Derived from core.authz so the
document, the tests and the enforcement all describe the same rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core_authz import Facts, Level, allowed
from core_command_registry import CommandSpec, registry


@dataclass(frozen=True)
class Rule:
    level: Level
    needs: Optional[str] = None  # Discord permission (moderation) or global permission name
    logged: bool = False


MODULE_DEFAULT = {
    "home": Rule(Level.PUBLIC), "utility": Rule(Level.PUBLIC),
    "antinuke": Rule(Level.SECURITY_ADMIN, None, True), "whitelist": Rule(Level.SECURITY_ADMIN, None, True),
    "logging": Rule(Level.SECURITY_ADMIN), "setup": Rule(Level.SECURITY_ADMIN, None, True),
    "moderation": Rule(Level.MODERATOR, None, True), "automod": Rule(Level.ADMIN, None, True),
    "welcome": Rule(Level.ADMIN), "tickets": Rule(Level.ADMIN, None, True), "voice": Rule(Level.ADMIN),
    "prestige": Rule(Level.ADMIN, None, True), "owner": Rule(Level.GLOBAL_OWNER, None, True),
}

_MOD = lambda perm: Rule(Level.MODERATOR, perm, True)  # noqa: E731
OVERRIDES: dict[str, Rule] = {
    # moderation: the Discord permission each action needs
    "ban": _MOD("ban_members"), "softban": _MOD("ban_members"), "unban": _MOD("ban_members"),
    "kick": _MOD("kick_members"), "timeout": _MOD("moderate_members"), "unmute": _MOD("moderate_members"),
    "jail": _MOD("manage_roles"), "unjail": _MOD("manage_roles"), "jlist": _MOD("manage_roles"),
    "lock": _MOD("manage_channels"), "unlock": _MOD("manage_channels"), "slowmode": _MOD("manage_channels"),
    "purge": _MOD("manage_messages"), "purgeuser": _MOD("manage_messages"),
    "role": _MOD("manage_roles"), "roleall": _MOD("manage_roles"),
    "jailrole": Rule(Level.ADMIN, None, True), "autorole": Rule(Level.ADMIN, None, True),
    "setprefix": Rule(Level.ADMIN, None, True), "modrole": Rule(Level.ADMIN, None, True),
    "modrole add": Rule(Level.ADMIN, None, True), "modrole remove": Rule(Level.ADMIN, None, True),
    # utility
    "afk": Rule(Level.MEMBER),
    "snipe": Rule(Level.MODERATOR, "manage_messages"), "editsnipe": Rule(Level.MODERATOR, "manage_messages"),
    "clearsnipe": Rule(Level.MODERATOR, "manage_messages"),
    # tickets
    "ticketnote": Rule(Level.TICKET_STAFF, None, True), "ticketnotes": Rule(Level.TICKET_STAFF),
    "ticketpriority": Rule(Level.TICKET_STAFF, None, True), "ticketstats": Rule(Level.TICKET_STAFF),
    "ticketadd": Rule(Level.TICKET_STAFF, None, True), "ticketremove": Rule(Level.TICKET_STAFF, None, True),
    "voiceinterface": Rule(Level.ADMIN),
    # prestige
    "prestigecredit": Rule(Level.GLOBAL_OWNER, None, True), "prestigecredits": Rule(Level.MEMBER),
    "prestige": Rule(Level.MEMBER), "prestige check": Rule(Level.MEMBER), "tagroleinfo": Rule(Level.MEMBER),
    "prestige add": Rule(Level.GLOBAL_PERM, "prestige", True), "prestige remove": Rule(Level.GLOBAL_PERM, "prestige", True),
    "prestige list": Rule(Level.GLOBAL_PERM, "prestige"),
    "setbotname": Rule(Level.SERVER_OWNER, None, True), "setbotpfp": Rule(Level.SERVER_OWNER, None, True),
    "setbotbanner": Rule(Level.SERVER_OWNER, None, True), "setbotbio": Rule(Level.SERVER_OWNER, None, True),
    "tagrole": Rule(Level.SERVER_OWNER, None, True),
    # global administration
    "gban": Rule(Level.GLOBAL_PERM, "gban", True), "gunban": Rule(Level.GLOBAL_PERM, "gban", True),
    "gbancheck": Rule(Level.GLOBAL_PERM, "gban"), "gbanlist": Rule(Level.GLOBAL_PERM, "gban"),
    "gbaninfo": Rule(Level.GLOBAL_PERM, "gban"), "gperm": Rule(Level.GLOBAL_OWNER, None, True),
    "gperm add": Rule(Level.GLOBAL_OWNER, None, True), "gperm remove": Rule(Level.GLOBAL_OWNER, None, True),
    "stafflist": Rule(Level.GLOBAL_STAFF), "helpadm": Rule(Level.GLOBAL_STAFF),
    "serverlist": Rule(Level.GLOBAL_PERM, "servers"), "generatelink": Rule(Level.GLOBAL_PERM, "invite", True),
    "gaudit": Rule(Level.GLOBAL_PERM, "audit"),
}


def rule_for(spec: CommandSpec) -> Rule:
    return OVERRIDES.get(spec.name or "") or OVERRIDES.get(spec.slash or "") or MODULE_DEFAULT[spec.module]


_ALL_MOD_PERMS = frozenset({"ban_members", "kick_members", "moderate_members", "manage_roles", "manage_channels", "manage_messages"})
SAMPLES = {
    "member": Facts(),
    "moderator": Facts(discord_perms=_ALL_MOD_PERMS, in_mod_role=True),
    "server admin (Manage Server)": Facts(discord_perms=_ALL_MOD_PERMS | {"manage_guild"}),
    "server owner": Facts(is_guild_owner=True),
    "global admin (no extra perms)": Facts(global_role="administrator"),
    "bot owner": Facts(is_global_owner=True),
}


def can(sample: str, spec: CommandSpec) -> bool:
    rule = rule_for(spec)
    return allowed(rule.level, SAMPLES[sample], rule.needs)


def matrix_rows() -> list[dict]:
    rows = []
    for spec in registry.specs():
        rule = rule_for(spec)
        yes = lambda s: "yes" if can(s, spec) else "no"  # noqa: E731
        discord_perm = rule.needs.replace("_", " ").title() if rule.level is Level.MODERATOR and rule.needs else "-"
        global_needed = ("owner only" if rule.level is Level.GLOBAL_OWNER else f"`{rule.needs}` permission" if rule.level is Level.GLOBAL_PERM
                         else "any global staff" if rule.level is Level.GLOBAL_STAFF else "no")
        rows.append({
            "command": spec.name or f"/{spec.slash}", "prefix": "yes" if spec.name else "no", "slash": "yes" if spec.slash else "no",
            "who": rule.level.value, "discord": discord_perm, "moderators": yes("moderator"),
            "admins": yes("server admin (Manage Server)"), "owner_only": "yes" if rule.level is Level.SERVER_OWNER else "no",
            "global": global_needed, "logged": "yes" if rule.logged else "no", "module": spec.module,
        })
    return rows

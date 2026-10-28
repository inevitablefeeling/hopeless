"""Whitelist scopes and exemption resolution.

A scope is either an *area* ("antinuke", "automod") or one module in an area ("antinuke:ban").

* An area-wide entry covers every module in that area, and nothing in any other area.
* A module entry covers only that module.

There is deliberately no "everything" scope, so a general exemption can never silently switch off
every security feature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from core_errors import InvalidInput
from core_formatting import clean_inline
from core_security_modules import AREAS

TARGET_USER = "user"
TARGET_ROLE = "role"
TARGET_TYPES = (TARGET_USER, TARGET_ROLE)
DEFAULT_SCOPE = "antinuke"


@dataclass(frozen=True)
class WhitelistEntry:
    guild_id: int
    target_type: str  # "user" | "role"
    target_id: int
    scope: str
    added_by: int
    added_at: Optional[datetime] = None


def _forms(key: str) -> set[str]:
    """Spellings accepted for a module key: ban, bot_add, botadd, bot-add, antibotadd, anti-bot-add."""
    compact = key.replace("_", "")
    hyphen = key.replace("_", "-")
    return {key, compact, hyphen, f"anti{compact}", f"anti-{hyphen}"}


def valid_scopes() -> list[str]:
    scopes: list[str] = []
    for area, modules in AREAS.items():
        scopes.append(area)
        scopes.extend(f"{area}:{module.key}" for module in modules)
    return scopes


def _alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for area, modules in AREAS.items():
        for module in modules:
            for form in _forms(module.key):
                aliases.setdefault(form, f"{area}:{module.key}")
    return aliases


_EXTRA = {
    "antibulkban": "antinuke:bulk_destruction", "antibulkkick": "antinuke:bulk_destruction",
    "antibulkchannel": "antinuke:bulk_destruction", "antibulkrole": "antinuke:bulk_destruction",
    "antipermassign": "antinuke:admin_grant", "antivccreate": "antinuke:channel_create",
    "antivcdelete": "antinuke:channel_delete", "antivcupdate": "antinuke:channel_update",
}
_ALIASES = {**_alias_map(), **_EXTRA}


def normalize_scope(raw: Optional[str]) -> str:
    """Turn user input into a canonical scope. Empty input means the default (antinuke)."""
    if raw is None or not raw.strip():
        return DEFAULT_SCOPE
    text = raw.strip().lower()
    if text in AREAS:
        return text
    if ":" in text:
        area, _, name = text.partition(":")
        for module in AREAS.get(area, ()):
            if name in _forms(module.key):
                return f"{area}:{module.key}"
    elif text in _ALIASES:
        return _ALIASES[text]
    raise InvalidInput(
        f"I don't know the scope `{clean_inline(raw)}`. Use `antinuke`, `automod`, or a module such as "
        "`antinuke:ban` or `automod:spam`."
    )


def scope_covers(entry_scope: str, check_scope: str) -> bool:
    """True when a whitelist entry with `entry_scope` exempts the feature `check_scope`."""
    if entry_scope == check_scope:
        return True
    return ":" not in entry_scope and check_scope.startswith(entry_scope + ":")


def is_exempt(
    entries: Iterable[WhitelistEntry], *, user_id: int, role_ids: Iterable[int], scope: str
) -> bool:
    roles = set(role_ids)
    for entry in entries:
        if not scope_covers(entry.scope, scope):
            continue
        if entry.target_type == TARGET_USER and entry.target_id == user_id:
            return True
        if entry.target_type == TARGET_ROLE and entry.target_id in roles:
            return True
    return False


def describe_scope(scope: str) -> str:
    """Human wording for a scope, used in confirmations and lists."""
    if scope in AREAS:
        return f"every {scope} protection" if scope == "antinuke" else f"every {scope} rule"
    area, _, key = scope.partition(":")
    for module in AREAS.get(area, ()):
        if module.key == key:
            return module.title
    return scope

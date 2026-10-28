"""Pure automod logic."""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlparse

from core_errors import InvalidInput

RULES = ("link", "mention", "nsfw", "raid", "spam")
ACTIONS = ("delete", "warn", "timeout", "kick")
RULE_DEFAULTS = {  # rule -> (threshold, window seconds, action)
    "link": (1, 0, "delete"),
    "mention": (5, 0, "timeout"),
    "raid": (8, 10, "alert"),
    "spam": (6, 8, "timeout"),
}
_TLDS = "com|net|org|gg|io|me|xyz|co|ru|tk|ly|link|app|dev|info|us|uk|de|fr|tv|cc|to|ws|club|site|online"
_URL = re.compile(
    rf"(?:https?://|www\.)[^\s<>]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:{_TLDS})\b(?:/[^\s<>]*)?", re.IGNORECASE
)


def extract_domains(text: str) -> list[str]:
    """Hostnames of every link-like thing in the text, lowercased, without www."""
    domains: list[str] = []
    for match in _URL.findall(text or ""):
        raw = match if "://" in match else f"http://{match}"
        host = (urlparse(raw).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            domains.append(host)
    return domains


def domain_allowed(domain: str, allowed: Iterable[str]) -> bool:
    return any(domain == a or domain.endswith("." + a) for a in allowed)


def disallowed_links(text: str, allowed: Iterable[str]) -> list[str]:
    allowed = list(allowed)
    return [d for d in extract_domains(text) if not domain_allowed(d, allowed)]


def normalize_domain(raw: str) -> str:
    host = (urlparse(raw if "://" in raw else f"http://{raw}").hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if "." not in host:
        raise InvalidInput("That doesn't look like a domain. Try `example.com`.")
    return host


def count_mentions(user_ids: Iterable[int], role_ids: Iterable[int], mentions_everyone: bool) -> int:
    return len(set(user_ids)) + len(set(role_ids)) + (5 if mentions_everyone else 0)


def validate_action(rule: str, action: str) -> str:
    action = (action or "").strip().lower()
    valid = ("alert", "kick") if rule == "raid" else ACTIONS
    if action not in valid:
        raise InvalidInput(f"For {rule} choose one of: {', '.join(valid)}.")
    return action

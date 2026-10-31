"""Environment-driven configuration. Secrets only ever come from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

DEFAULT_PREFIX = "C"
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, repr=False)
class Settings:
    discord_token: str
    bot_owner_id: int
    database_url: str
    support_url: Optional[str] = None
    sync_guild_id: Optional[int] = None
    log_level: str = "INFO"
    default_prefix: str = DEFAULT_PREFIX
    presence_intent: bool = False  # ENABLE_PRESENCE_INTENT=1, only after turning on "Presence Intent" in the Developer Portal

    def __repr__(self) -> str:  # never leak the token or database password into logs
        return (
            f"Settings(owner={self.bot_owner_id}, sync_guild={self.sync_guild_id}, "
            f"log_level={self.log_level}, default_prefix={self.default_prefix!r})"
        )


def _snowflake(env: Mapping[str, str], name: str, problems: list[str], *, required: bool) -> Optional[int]:
    raw = (env.get(name) or "").strip()
    if not raw:
        if required:
            problems.append(f"{name} is required")
        return None
    if not raw.isdigit() or int(raw) <= 0:
        problems.append(f"{name} must be a numeric Discord ID")
        return None
    return int(raw)


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Read and validate settings. Every problem is reported at once."""
    env = os.environ if env is None else env
    problems: list[str] = []

    token = (env.get("DISCORD_TOKEN") or "").strip()
    if not token:
        problems.append("DISCORD_TOKEN is required")

    owner_id = _snowflake(env, "BOT_OWNER_ID", problems, required=True)

    database_url = (env.get("DATABASE_URL") or "").strip()
    if not database_url:
        problems.append("DATABASE_URL is required")
    elif not database_url.startswith(("postgres://", "postgresql://")):
        problems.append("DATABASE_URL must start with postgres:// or postgresql://")

    support_url = (env.get("SUPPORT_URL") or "").strip() or None
    if support_url and not support_url.startswith("https://"):
        problems.append("SUPPORT_URL must start with https://")

    sync_guild_id = _snowflake(env, "SYNC_GUILD_ID", problems, required=False)

    log_level = (env.get("LOG_LEVEL") or "INFO").strip().upper()
    if log_level not in _LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}")

    if problems:
        raise ConfigError("Invalid configuration:\n- " + "\n- ".join(problems))

    assert owner_id is not None
    return Settings(
        discord_token=token,
        bot_owner_id=owner_id,
        database_url=database_url,
        support_url=support_url,
        sync_guild_id=sync_guild_id,
        log_level=log_level,
        presence_intent=(env.get("ENABLE_PRESENCE_INTENT") or "").strip().lower() in ("1", "true", "yes", "on"),
    )

"""HOPELESS entry point.  Run with:  python bot.py"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import ConfigError, Settings, load_settings
from core_checks import global_ban_gate, rate_gate
from core_command_registry import registry
from core_emoji import ICONS, sync_application_emojis
from core_error_handling import handle_app_command_error, handle_command_error
from core_errors import DatabaseUnavailable
from core_guild_settings import PrefixService
from core_logging import setup_logging
import db_repositories as repos
from db_connection import Database
from db_migrations import run_migrations
from svc_afk_service import AfkService
from svc_activity_service import ActivityService
from svc_extra_owner_service import ExtraOwnerService
from svc_global_service import GlobalService
from svc_log_service import LogService
from svc_permission_service import PermissionService
from svc_snipe_service import SnipeService
from svc_staff_service import StaffRoleService
from svc_stats_service import StatsService
from svc_whitelist_service import WhitelistService
import view_help_panel as help_panel

log = logging.getLogger("hopeless.bot")

EXTENSIONS = (
    "cog_help", "cog_moderation", "cog_whitelist", "cog_utility", "cog_staff", "cog_logging_module",
    "cog_welcome", "cog_antinuke", "cog_automod", "cog_tickets", "cog_voice", "cog_prestige", "cog_setup", "cog_globaladmin", "cog_engage", "cog_leveling", "cog_rep", "cog_extraowners",
)
STATS_FLUSH_SECONDS = 60
SNIPE_PRUNE_SECONDS = 300


@dataclass
class Stores:
    """The persistence layer. Tests pass in-memory stores; production uses PostgreSQL."""

    prefix: Any
    staff: Any
    whitelist: Any
    afk: Any
    usage: Any

    @classmethod
    def postgres(cls, db: Database) -> "Stores":
        return cls(
            repos.PgPrefixStore(db),
            repos.PgStaffStore(db),
            repos.PgWhitelistStore(db),
            repos.PgAfkStore(db),
            repos.PgUsageStore(db),
        )


def _prefix_callable(bot: "HopelessBot", message: discord.Message):
    prefix = bot.prefixes.get(message.guild.id if message.guild else None)
    return commands.when_mentioned_or(prefix)(bot, message)


class HopelessTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Globally banned users can't use any slash command (the owner can never be banned)."""
        bot = interaction.client
        message = bot.globals.ban_message(interaction.user.id) if hasattr(bot, "globals") else None
        if message:
            raise app_commands.CheckFailure(message)
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await handle_app_command_error(interaction, error)


class HopelessBot(commands.Bot):
    def __init__(self, settings: Settings, db: Database, stores: Optional[Stores] = None) -> None:
        intents = discord.Intents.default()
        intents.members = True  # privileged: enable "Server Members Intent" in the developer portal
        intents.message_content = True  # privileged: enable "Message Content Intent" as well
        intents.presences = settings.presence_intent  # privileged and OFF by default: needed only for status rep
        super().__init__(
            command_prefix=_prefix_callable,
            intents=intents,
            case_insensitive=True,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),  # never ping by accident; opt in per message
            owner_id=settings.bot_owner_id,
            tree_cls=HopelessTree,
        )
        self.settings = settings
        self.db = db
        stores = stores or Stores.postgres(db)
        self.prefixes = PrefixService(stores.prefix, settings.default_prefix)
        self.permissions = PermissionService(settings, stores.staff)
        self.whitelist = WhitelistService(stores.whitelist)
        self.afk = AfkService(stores.afk)
        self.globals = GlobalService(db, self.permissions)  # global bans, global staff permissions, audit
        self.staffroles = StaffRoleService(db)  # per-server moderator roles
        self.activity_service = ActivityService(db)  # message counts and XP
        self.extraowners = ExtraOwnerService(db)  # server-scoped extra owners (never global)
        self.add_check(global_ban_gate)  # prefix commands, aliases and hybrid slash commands
        self.add_check(rate_gate)  # a few commands per few seconds per member
        self.stats = StatsService(stores.usage)
        self.snipes = SnipeService()
        self.logs = LogService(db)
        self.logs.bot = self  # lets the log webhook use the bot's name and avatar
        self.icons = ICONS  # the shared resolver that embeds and views read from
        self._emoji_synced = False
        self.started_at = datetime.now(timezone.utc)
        self._monotonic_start = time.monotonic()
        self._tasks: set[asyncio.Task] = set()
        self._background_started = False

    @property
    def uptime(self) -> float:
        return time.monotonic() - self._monotonic_start

    # --- startup -----------------------------------------------------------------------------
    async def setup_hook(self) -> None:
        """Runs once per process (not on reconnects), so background tasks can't be duplicated."""
        await self.db.connect()
        applied = await run_migrations(self.db)
        if applied:
            log.info("applied migrations: %s", applied)
        await self.prepare()
        try:
            await self.sync_application_commands()
        except discord.HTTPException:
            log.exception("slash command sync failed; prefix commands still work")

    async def prepare(self) -> None:
        """Everything that doesn't need Discord or the database connection itself."""
        await self.prefixes.load()
        await self.permissions.load()
        try:
            await self.globals.load()
        except Exception:  # a problem here must not stop the bot from starting; bans simply aren't enforced until it loads
            log.warning("could not load global administration data", exc_info=True)
        try:
            await self.extraowners.load()
        except Exception:
            log.warning("could not load extra owners", exc_info=True)
        await self.afk.load()
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        self.add_dynamic_items(*help_panel.DYNAMIC_ITEMS)
        for problem in self.registry_problems():
            log.error("command registry mismatch: %s", problem)
        self._start_background_tasks()

    async def sync_application_commands(self) -> int:
        if self.settings.sync_guild_id:  # instant updates in one test server
            guild = discord.Object(id=self.settings.sync_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("synced %d application commands", len(synced))
        return len(synced)

    # --- registry parity ---------------------------------------------------------------------
    def _has_slash(self, path: str) -> bool:
        first, *rest = path.split()
        node: Any = self.tree.get_command(first)
        for part in rest:
            if node is None or not hasattr(node, "get_command"):
                return False
            node = node.get_command(part)
        return node is not None

    def registry_problems(self) -> list[str]:
        """Every IMPLEMENTED registry entry must exist, and every loaded command must be registered."""
        problems: list[str] = []
        known_names: set[str] = set()
        for spec in registry.specs():
            if spec.name:
                known_names.add(spec.name)
            if not spec.implemented:
                continue
            if spec.name and self.get_command(spec.name) is None:
                problems.append(f"prefix command missing: {spec.name}")
            if spec.slash and not self._has_slash(spec.slash):
                problems.append(f"slash command missing: /{spec.slash}")
        for command in self.walk_commands():
            if command.qualified_name not in known_names:
                problems.append(f"command not in registry: {command.qualified_name}")
        return problems

    # --- background work ---------------------------------------------------------------------
    def _spawn(self, coro, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _start_background_tasks(self) -> None:
        if self._background_started:
            return
        self._background_started = True
        self._spawn(self._stats_loop(), "stats-flush")
        self._spawn(self._prune_loop(), "snipe-prune")

    def stop_background_tasks(self) -> None:
        for task in list(self._tasks):
            task.cancel()

    async def _stats_loop(self) -> None:
        while True:
            await asyncio.sleep(STATS_FLUSH_SECONDS)
            try:
                await self.stats.flush()
            except Exception:
                log.warning("stats flush failed; will retry", exc_info=True)

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(SNIPE_PRUNE_SECONDS)
            self.snipes.prune()

    # --- emoji -------------------------------------------------------------------------------
    async def refresh_icons(self) -> None:
        if not self._emoji_synced:  # copy the configured emojis into the bot's own application emojis, once per start
            self._emoji_synced = True
            await sync_application_emojis(self)
        ids = {emoji.id for emoji in self.emojis}
        try:
            ids |= {emoji.id for emoji in await self.fetch_application_emojis()}
        except (AttributeError, discord.HTTPException):
            pass  # older discord.py, or no application emojis
        self.icons.refresh(ids)

    # --- events ------------------------------------------------------------------------------
    async def on_ready(self) -> None:
        await self.refresh_icons()
        log.info("ready as %s in %d servers", self.user, len(self.guilds))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.refresh_icons()

    async def on_guild_emojis_update(self, guild: discord.Guild, before, after) -> None:
        await self.refresh_icons()

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        await handle_command_error(ctx, error)

    async def on_command_completion(self, ctx: commands.Context) -> None:
        if ctx.command is not None:
            self.stats.record(ctx.guild.id if ctx.guild else None, ctx.command.qualified_name)

    # --- shutdown ----------------------------------------------------------------------------
    async def close(self) -> None:
        self.stop_background_tasks()
        try:
            await self.stats.flush()
        except Exception:
            log.warning("could not flush stats during shutdown", exc_info=True)
        await self.db.close()
        await super().close()


async def run() -> int:
    try:
        from dotenv import load_dotenv  # optional: only used for local runs

        load_dotenv()
    except ImportError:
        pass

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging(settings.log_level)

    bot = HopelessBot(settings, Database(settings.database_url))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # e.g. Windows
            pass

    async with bot:
        start_task = asyncio.create_task(bot.start(settings.discord_token), name="bot-start")
        stop_task = asyncio.create_task(stop.wait(), name="stop-wait")
        done, _ = await asyncio.wait({start_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            log.info("shutdown signal received")
            await bot.close()
            await asyncio.gather(start_task, return_exceptions=True)
            return 0
        stop_task.cancel()
        try:
            start_task.result()
        except discord.LoginFailure:
            log.error("Discord rejected the token. Check DISCORD_TOKEN.")
        except discord.PrivilegedIntentsRequired:
            log.error(
                "Privileged intents are off. In the Discord developer portal open your app, then Bot, "
                "and enable Server Members Intent and Message Content Intent."
            )
        except DatabaseUnavailable:
            log.error("Could not reach the database. Check DATABASE_URL and that the database is running.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))

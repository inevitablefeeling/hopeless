"""Antinuke: real detection driven by gateway events plus audit-log attribution.

Principles: never punish unless the actor is identified reliably (audit entry must match the target and
time), never punish the owner, this bot or whitelisted actors, count incidents in a window before
escalating (record, then alert, then punish), and never claim a recovery worked unless it did.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import discord
from discord import AuditLogAction as A
from discord import app_commands
from discord.ext import commands

from core_antinuke_logic import (
    BULK_THRESHOLD, DEFAULT_ALERT_AT, DEFAULT_PUNISH_AT, DEFAULT_PUNISHMENT, DEFAULT_WINDOW, DESTRUCTIVE, Level,
    MODULE_KEYS, PUNISHMENT_HELP, AuditCandidate, best_audit_match, dangerous_gained, decide, has_dangerous,
    is_urgent, module_display, recent_ids, skip_reason, validate_punishment,
)
from core_antinuke_logic import SnapshotGuard, resolve_parent, restore_order
from core_checks import ensure_security_admin
from core_embeds import NEUTRAL, info_embed, ok_embed, toggle_embed
from core_emoji import ICONS
from core_errors import InvalidInput
from core_formatting import chunk_lines
from core_security_modules import ANTINUKE_MODULES
from core_sliding import SlidingCounter
from core_whitelist_logic import normalize_scope
from svc_log_service import plain, who
from view_confirm import ask

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.antinuke")
TITLES = {m.key: m.title for m in ANTINUKE_MODULES}
# Events where "I couldn't tell who did it" is normal noise (the bot's own edits, other bots' edits...).
# They are still counted and stored, but they don't post an alert unless the actor is known.
LOW_SIGNAL = frozenset({"channel_update", "role_update", "guild_update", "emoji_update", "sticker_update",
                        "integration_update", "invite_delete", "webhook_update", "channel_create", "role_create"})
AUDIT_DELAY = 1.2  # seconds: let Discord write the audit entry before we read it
SHORTCUTS = {
    "antiban": ["ban"], "antibotadd": ["bot_add"], "antichannelcreate": ["channel_create"],
    "antichanneldelete": ["channel_delete"], "antichannelupdate": ["channel_update"],
    "antiguildupdate": ["guild_update"], "antikick": ["kick"], "antirolecreate": ["role_create"],
    "antiroledelete": ["role_delete"], "antiroleupdate": ["role_update"],
    "antiwebhook": ["webhook_create", "webhook_update", "webhook_delete"],
}
_ENSURE = "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING"
_ENSURE_AN = "INSERT INTO antinuke_guild (guild_id) VALUES ($1) ON CONFLICT DO NOTHING"


@dataclass(frozen=True)
class Effective:
    enabled: bool
    punishment: str
    alert_at: int
    punish_at: int
    window: int
    restore: bool


@dataclass
class GuildCfg:
    enabled: bool
    punishment: str
    alert_at: int
    punish_at: int
    window: int
    modules: dict[str, dict]


def snap_channel(ch: discord.abc.GuildChannel) -> dict:
    overwrites = []
    for target, ow in ch.overwrites.items():
        allow, deny = ow.pair()
        overwrites.append({"id": target.id, "type": "role" if isinstance(target, discord.Role) else "member",
                           "allow": allow.value, "deny": deny.value})
    kind = "category" if isinstance(ch, discord.CategoryChannel) else "voice" if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)) else "text"
    return {
        "name": ch.name, "kind": kind, "position": ch.position, "category_id": getattr(ch, "category_id", None),
        "topic": getattr(ch, "topic", None), "nsfw": getattr(ch, "nsfw", False),
        "slowmode": getattr(ch, "slowmode_delay", 0), "bitrate": getattr(ch, "bitrate", None),
        "user_limit": getattr(ch, "user_limit", None), "overwrites": overwrites,
    }


def snap_role(role: discord.Role) -> dict:
    return {"name": role.name, "colour": role.colour.value, "permissions": role.permissions.value,
            "hoist": role.hoist, "mentionable": role.mentionable, "position": role.position}


class Antinuke(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self.tracker = SlidingCounter()
        self.bulk = SlidingCounter()
        self._cache: dict[int, GuildCfg] = {}
        self._pending_restore: dict[tuple[int, int, str], list[tuple[float, int]]] = {}
        self._punished: dict[tuple[int, int], float] = {}
        self._snapshotted = False
        self.guard = SnapshotGuard()  # protects the last known good snapshot
        self._restore_locks: dict[int, asyncio.Lock] = {}
        self._restored: set[tuple[int, int]] = set()
        self._tasks: set = set()

    async def cog_load(self) -> None:
        for name, keys in SHORTCUTS.items():
            self.bot.add_command(commands.Command(self._make_shortcut(keys), name=name, help=f"Configure {', '.join(TITLES[k] for k in keys)}"))

    async def cog_unload(self) -> None:
        for name in SHORTCUTS:
            self.bot.remove_command(name)

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ensure_security_admin(ctx)

    # --- configuration -----------------------------------------------------------------------
    async def config(self, guild_id: int) -> GuildCfg:
        cfg = self._cache.get(guild_id)
        if cfg is None:
            g = await self.bot.db.fetchrow("SELECT * FROM antinuke_guild WHERE guild_id = $1", guild_id)
            rows = await self.bot.db.fetch("SELECT * FROM antinuke_modules WHERE guild_id = $1", guild_id)
            cfg = GuildCfg(
                bool(g and g["enabled"]), g["default_punishment"] if g else DEFAULT_PUNISHMENT,
                g["alert_at"] if g else DEFAULT_ALERT_AT, g["punish_at"] if g else DEFAULT_PUNISH_AT,
                g["window_seconds"] if g else DEFAULT_WINDOW, {r["key"]: dict(r) for r in rows},
            )
            self._cache[guild_id] = cfg
        return cfg

    @staticmethod
    def effective(cfg: GuildCfg, key: str) -> Effective:
        row = cfg.modules.get(key, {})
        return Effective(
            row.get("enabled", True), row.get("punishment") or cfg.punishment,
            row.get("alert_at") or cfg.alert_at, row.get("punish_at") or cfg.punish_at,
            row.get("window_seconds") or cfg.window, row.get("restore", True),
        )

    async def _set_guild_enabled(self, guild_id: int, enabled: bool) -> None:
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, guild_id)
            await conn.execute(_ENSURE_AN, guild_id)
            await conn.execute("UPDATE antinuke_guild SET enabled = $2 WHERE guild_id = $1", guild_id, enabled)
        self._cache.pop(guild_id, None)

    async def _set_modules(self, guild_id: int, keys: list[str], enabled: bool) -> None:
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, guild_id)
            await conn.execute(_ENSURE_AN, guild_id)
            for key in keys:
                await conn.execute(
                    "INSERT INTO antinuke_modules (guild_id, key, enabled) VALUES ($1, $2, $3) "
                    "ON CONFLICT (guild_id, key) DO UPDATE SET enabled = EXCLUDED.enabled",
                    guild_id, key, enabled,
                )
        self._cache.pop(guild_id, None)

    # --- setup plan (also used by the /setup panel) ------------------------------------------
    def plan_setup(self, guild: discord.Guild) -> list[str]:
        lines = [
            "turn antinuke on for this server",
            f"enable all {len(MODULE_KEYS)} protections (first incident is recorded, second alerts, third applies the response)",
            f"default response: kick the responsible actor after {DEFAULT_PUNISH_AT} incidents in {DEFAULT_WINDOW}s (change it with the punishment command)",
            "save snapshots of channels and roles so deleted ones can be recreated",
            "the owner, this bot and whitelisted users/roles are never punished",
        ]
        missing = [p for p in ("view_audit_log", "ban_members", "kick_members", "manage_roles", "manage_channels", "manage_webhooks")
                   if not getattr(guild.me.guild_permissions, p)]
        if missing:
            lines.append("⚠ I'm missing: " + ", ".join(p.replace("_", " ") for p in missing) + ". Detection or responses will be limited")
        return lines

    async def apply_setup(self, guild: discord.Guild) -> list[str]:
        await self._set_guild_enabled(guild.id, True)
        await self._set_modules(guild.id, list(MODULE_KEYS), True)
        count = await self.snapshot_guild(guild)
        return [f"antinuke is on with {len(MODULE_KEYS)} protections", f"saved {count} snapshots"]

    # --- snapshots / recovery ----------------------------------------------------------------
    async def snapshot_guild(self, guild: discord.Guild) -> int:
        if not self.guard.can_snapshot(guild.id, time.time()):
            log.info("snapshot skipped for %s: an incident or restoration is in progress", guild.id)
            return 0
        rows = [(guild.id, "channel", c.id, snap_channel(c)) for c in guild.channels]
        rows += [(guild.id, "role", r.id, snap_role(r)) for r in guild.roles if not r.is_default() and not r.managed]
        import json
        await self.bot.db.executemany(
            "INSERT INTO snapshots (guild_id, kind, target_id, data) VALUES ($1, $2, $3, $4::jsonb) "
            "ON CONFLICT (guild_id, kind, target_id) DO UPDATE SET data = EXCLUDED.data, taken_at = now()",
            [(g, k, t, json.dumps(d)) for g, k, t, d in rows],
        )
        return len(rows)

    async def _is_managed_channel(self, channel_id: int) -> bool:
        """Ticket channels and temporary voice rooms are created and removed by this bot as part of normal use."""
        return bool(await self.bot.db.fetchval(
            "SELECT 1 FROM tickets WHERE channel_id = $1 UNION ALL SELECT 1 FROM voice_rooms "
            "WHERE channel_id = $1 OR waiting_channel_id = $1 LIMIT 1", channel_id))

    async def _snapshot_one(self, guild_id: int, kind: str, target_id: int, data: dict) -> None:
        import json
        cfg = await self.config(guild_id)
        if cfg.enabled:
            await self._write_snapshot(guild_id, kind, target_id, data)

    async def _write_snapshot(self, guild_id: int, kind: str, target_id: int, data: dict) -> None:
        import json
        await self.bot.db.execute(
            "INSERT INTO snapshots (guild_id, kind, target_id, data) VALUES ($1, $2, $3, $4::jsonb) "
            "ON CONFLICT (guild_id, kind, target_id) DO UPDATE SET data = EXCLUDED.data, taken_at = now()",
            guild_id, kind, target_id, json.dumps(data),
        )

    def _snapshot_later(self, guild: discord.Guild, kind: str, target_id: int) -> None:
        """Refresh one snapshot only after the audit log has had time to show whether the change was an attack."""
        task = asyncio.create_task(self._snapshot_settled(guild, kind, target_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _snapshot_settled(self, guild: discord.Guild, kind: str, target_id: int) -> None:
        await asyncio.sleep(AUDIT_DELAY + 4)
        if not self.guard.can_snapshot(guild.id, time.time()):
            return
        obj = guild.get_channel(target_id) if kind == "channel" else guild.get_role(target_id)
        if obj is not None:
            await self._snapshot_one(guild.id, kind, target_id, snap_channel(obj) if kind == "channel" else snap_role(obj))

    async def _load_snapshot(self, guild_id: int, kind: str, old_id: int) -> Optional[dict]:
        import json
        row = await self.bot.db.fetchrow("SELECT data FROM snapshots WHERE guild_id = $1 AND kind = $2 AND target_id = $3", guild_id, kind, old_id)
        if row is None:
            return None
        return json.loads(row["data"]) if isinstance(row["data"], str) else dict(row["data"])

    async def _restore(self, guild: discord.Guild, kind: str, old_id: int, data: dict, id_map: dict[int, int]) -> str:
        """Recreate one role or channel from its snapshot. `id_map` maps old ids to recreated ones, so a restored
        channel lands in its restored category and restored roles keep their channel permissions."""
        try:
            if kind == "role":
                role = await guild.create_role(
                    name=data["name"], permissions=discord.Permissions(data["permissions"]), colour=discord.Colour(data["colour"]),
                    hoist=data["hoist"], mentionable=data["mentionable"], reason="antinuke recovery",
                )
                try:
                    await role.edit(position=min(data["position"], guild.me.top_role.position - 1), reason="antinuke recovery")
                except discord.HTTPException:
                    pass
                id_map[old_id] = role.id
                await self._write_snapshot(guild.id, "role", role.id, data)
                await self.bot.db.execute("DELETE FROM snapshots WHERE guild_id = $1 AND kind = 'role' AND target_id = $2", guild.id, old_id)
                return f"role **{plain(data['name'])}** was recreated (new id {role.id}; members must be given it again)"
            overwrites = {}
            for ow in data["overwrites"]:
                if ow["type"] == "role":
                    target = guild.get_role(ow["id"]) or guild.get_role(id_map.get(ow["id"], 0))
                else:
                    target = guild.get_member(ow["id"])
                if target is not None:
                    overwrites[target] = discord.PermissionOverwrite.from_pair(discord.Permissions(ow["allow"]), discord.Permissions(ow["deny"]))
            parent_id = resolve_parent(data.get("category_id"), {c.id for c in guild.categories}, id_map)
            category = guild.get_channel(parent_id) if parent_id else None
            kw: dict[str, Any] = {"name": data["name"], "overwrites": overwrites, "reason": "antinuke recovery"}
            if data["kind"] == "category":
                channel = await guild.create_category(**kw)
            elif data["kind"] == "voice":
                channel = await guild.create_voice_channel(category=category, bitrate=min(data.get("bitrate") or 64000, guild.bitrate_limit),
                                                           user_limit=data.get("user_limit") or 0, **kw)
            else:
                channel = await guild.create_text_channel(category=category, topic=data.get("topic"), nsfw=bool(data.get("nsfw")),
                                                          slowmode_delay=data.get("slowmode") or 0, **kw)
            try:
                await channel.edit(position=data.get("position", 0), reason="antinuke recovery")
            except discord.HTTPException:
                pass
            id_map[old_id] = channel.id
            await self._write_snapshot(guild.id, "channel", channel.id, {**data, "category_id": category.id if category else None})
            await self.bot.db.execute("DELETE FROM snapshots WHERE guild_id = $1 AND kind = 'channel' AND target_id = $2", guild.id, old_id)
            where = f" in **{plain(category.name)}**" if category else ""
            return f"{data['kind']} **{plain(data['name'])}** was recreated{where} (new id {channel.id}; the old messages and id can't be restored)"
        except discord.HTTPException as exc:
            return f"{kind} **{plain(data.get('name', old_id))}** could NOT be recreated (HTTP {exc.status})"

    async def _recover(self, guild: discord.Guild, actor_id: int) -> list[str]:
        """Recreate what this actor deleted in the current burst (roles, then categories, then channels). Older
        deletions were probably intentional. One restoration runs at a time per server and never twice for the same id."""
        lock = self._restore_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            self.guard.begin_restore(guild.id)
            try:
                results, now, wanted = [], time.time(), []
                cfg = await self.config(guild.id)
                for (g, a, key), entries in list(self._pending_restore.items()):
                    if g != guild.id or a != actor_id:
                        continue
                    del self._pending_restore[(g, a, key)]
                    if not self.effective(cfg, key).restore:
                        continue
                    for old_id in recent_ids(entries, now):
                        kind = "role" if key == "role_delete" else "channel"
                        if kind == "channel" and await self._is_managed_channel(old_id):
                            continue  # tickets and temporary voice rooms are meant to disappear
                        if (guild.id, old_id) in self._restored:
                            continue
                        wanted.append((kind, old_id))
                items = []
                for kind, old_id in wanted:
                    data = await self._load_snapshot(guild.id, kind, old_id)
                    if data is None:
                        results.append(f"{kind} {old_id}: no snapshot was saved, so it can't be recreated")
                    else:
                        items.append({**data, "_type": kind, "_old": old_id})
                id_map: dict[int, int] = {}
                for item in restore_order(items):
                    self._restored.add((guild.id, item["_old"]))
                    results.append(await self._restore(guild, item["_type"], item["_old"], item, id_map))
                if len(self._restored) > 500:
                    self._restored.clear()
                return results
            finally:
                self.guard.end_restore(guild.id, time.time())

    # --- attribution and evaluation ----------------------------------------------------------
    async def _attribute(self, guild: discord.Guild, mapping: dict, target_id: Optional[int], event_ts: float):
        if not guild.me.guild_permissions.view_audit_log:
            return None, None
        found: list[tuple[AuditCandidate, Any]] = []
        for action in mapping:
            try:
                async for entry in guild.audit_logs(limit=10, action=action):
                    found.append((AuditCandidate(entry.id, entry.user_id, getattr(entry.target, "id", None), entry.created_at.timestamp()), action))
            except discord.HTTPException:
                continue
        best = best_audit_match([c for c, _ in found], target_id=target_id, event_ts=event_ts)
        if best is None:
            return None, None
        action = next(a for c, a in found if c.entry_id == best.entry_id)
        return best.actor_id, mapping[action]

    async def _handle(self, guild: discord.Guild, mapping: dict, *, target_id: Optional[int] = None, extra: str = "",
                      quiet: bool = False, actor_override: Optional[int] = None, restore: Optional[int] = None) -> None:
        cfg = await self.config(guild.id)
        if not cfg.enabled:
            return
        keys = list(mapping.values())
        if all(not self.effective(cfg, k).enabled for k in keys):
            return
        if actor_override is not None:
            actor_id, key, how = actor_override, keys[0], "known directly from the event"
        else:
            await asyncio.sleep(AUDIT_DELAY)
            actor_id, key = await self._attribute(guild, mapping, target_id, time.time())
            how = "matched in the audit log by target and time"
            if key is None:
                if quiet:
                    return
                key = keys[0]
        mod = self.effective(cfg, key)
        if mod.enabled:
            await self._evaluate(guild, key, actor_id, target_id, extra, cfg, mod, restore, how if actor_id else "")

    async def _incident(self, guild_id: int, key: str, actor_id: Optional[int], target_id: Optional[int], level: Level, detail: str) -> None:
        try:
            await self.bot.db.execute(
                "INSERT INTO antinuke_incidents (guild_id, key, actor_id, target_id, level, detail) VALUES ($1,$2,$3,$4,$5,$6)",
                guild_id, key, actor_id, target_id, level.value, detail[:500],
            )
        except Exception:
            log.warning("could not store incident", exc_info=True)

    async def _evaluate(self, guild, key, actor_id, target_id, extra, cfg, mod, restore, how: str = "") -> None:
        member = guild.get_member(actor_id) if actor_id else None
        exempt = bool(actor_id) and await self.bot.whitelist.is_exempt(
            guild.id, actor_id, [r.id for r in member.roles] if member else [], f"antinuke:{key}"
        )
        reason = skip_reason(actor_id=actor_id, bot_id=self.bot.user.id, guild_owner_id=guild.owner_id, exempt=exempt)
        title = TITLES[key]
        shield, siren = ICONS.get("antinuke"), ICONS.get("siren")
        if reason is None or actor_id is None:  # an untrusted or unidentified actor: don't let this state overwrite the trusted snapshot
            self.guard.mark_incident(guild.id, time.time())
        if reason:
            if actor_id is None:  # always stored; alerted only for high-signal events. Nobody is punished.
                await self._incident(guild.id, key, None, target_id, Level.RECORD, f"{extra} | not punished: {reason}")
                if key not in LOW_SIGNAL:
                    await self.bot.logs.security(
                        guild, f"{shield} {title}: unidentified",
                        "A change happened, but Discord's audit log had no entry that clearly matches it, so I can't say who did it. "
                        "I did not punish anyone.",
                        [("What happened", extra or "-")])
            return
        now = time.time()
        count = self.tracker.add((guild.id, actor_id, key), now, mod.window)
        level = decide(count, alert_at=mod.alert_at, punish_at=mod.punish_at)
        if key in DESTRUCTIVE:
            bulk = self.bulk.add((guild.id, actor_id), now, mod.window)
            if self.effective(cfg, "bulk_destruction").enabled and bulk >= BULK_THRESHOLD and level is not Level.PUNISH:
                level = Level.PUNISH
                extra += f" ({bulk} destructive actions within {mod.window}s)"
        if restore is not None:
            bucket = self._pending_restore.setdefault((guild.id, actor_id, key), [])
            if len(bucket) < 50:
                bucket.append((now, restore))
        await self._incident(guild.id, key, actor_id, target_id, level, extra)
        who_text = who(member) if member else f"user {actor_id}"
        fields = [("Who", who_text), ("What happened", extra or "-"), ("How many", f"{count} in the last {mod.window}s")]
        if how:
            fields.append(("How I know", how))
        if level is Level.RECORD:
            await self.bot.logs.security(
                guild, f"{shield} {title}: recorded",
                f"First time in {mod.window}s, so I only wrote it down. Nobody was pinged and nothing was done to {plain(who_text, 60)}.", fields)
            return
        if level is Level.ALERT:
            await self.bot.logs.security(
                guild, f"{siren} URGENT: {title}",
                f"{plain(who_text, 60)} did this {count} times in {mod.window}s. One more ({mod.punish_at}) and I will respond with: **{mod.punishment}**.",
                fields, urgent=True)
            return
        last = self._punished.get((guild.id, actor_id), 0)
        if now - last < 30:
            return
        self._punished[(guild.id, actor_id)] = now
        outcome = await self._punish(guild, member, actor_id, mod.punishment)
        recovered = await self._recover(guild, actor_id)
        fields += [("What I did to them", outcome), ("What I restored", "\n".join(recovered) or "nothing needed restoring")]
        await self.bot.logs.security(guild, f"{siren} RESPONSE: {title}", f"Configured response: **{mod.punishment}**.", fields, urgent=True)

    async def _punish(self, guild: discord.Guild, member: Optional[discord.Member], actor_id: int, punishment: str) -> str:
        reason = "antinuke response"
        try:
            if punishment == "log":
                return "logged only (no punishment configured)"
            if punishment == "ban":
                await guild.ban(discord.Object(id=actor_id), reason=reason, delete_message_seconds=0)
                return "banned"
            if member is None:
                return "not applied: the actor is no longer in the server"
            if member.top_role >= guild.me.top_role:
                return "NOT applied: the actor's top role is at or above mine"
            if punishment == "kick":
                await member.kick(reason=reason)
                return "kicked"
            if punishment == "strip":
                roles = [r for r in member.roles[1:] if has_dangerous(r.permissions.value) and not r.managed and r < guild.me.top_role]
                if roles:
                    await member.remove_roles(*roles, reason=reason)
                return f"removed {len(roles)} dangerous role(s)"
            if punishment == "jail":
                mod = self.bot.get_cog("Moderation")
                await mod.jail_member(guild, member, jailed_by=self.bot.user.id, reason=reason, seconds=None)
                return "jailed"
        except (discord.HTTPException, InvalidInput) as exc:
            return f"FAILED: {getattr(exc, 'user_message', None) or getattr(exc, 'status', exc)}"
        return "unknown response"

    # --- gateway events ----------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._snapshotted:
            return
        self._snapshotted = True
        for guild in self.bot.guilds:
            try:
                if (await self.config(guild.id)).enabled:
                    await self.snapshot_guild(guild)
            except Exception:
                log.warning("startup snapshot failed for %s", guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user) -> None:
        await self._handle(guild, {A.ban: "ban"}, target_id=user.id, extra=f"banned {who(user)}")

    @commands.Cog.listener()
    async def on_member_remove(self, member) -> None:
        await self._handle(member.guild, {A.kick: "kick"}, target_id=member.id, extra=f"removed {who(member)}", quiet=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not member.bot:
            return
        guild = member.guild
        cfg = await self.config(guild.id)
        if not cfg.enabled or not self.effective(cfg, "bot_add").enabled:
            return
        await asyncio.sleep(AUDIT_DELAY)
        actor_id, _ = await self._attribute(guild, {A.bot_add: "bot_add"}, member.id, time.time())
        actor = guild.get_member(actor_id) if actor_id else None
        shield, siren = ICONS.get("antinuke"), ICONS.get("siren")
        if actor_id is None:
            await self.bot.logs.security(guild, f"{shield} Bot added: unidentified",
                                         "A bot joined, but I can't tell who added it. I left it in place.", [("Bot", who(member))])
            return
        trusted = (actor_id == guild.owner_id
                   or await self.bot.whitelist.is_exempt(guild.id, actor_id, [r.id for r in actor.roles] if actor else [], "antinuke:bot_add")
                   or await self.bot.whitelist.is_exempt(guild.id, member.id, [], "antinuke:bot_add"))
        if trusted:
            return
        if actor is not None and (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            # Administrators legitimately add bots. I never remove those; if the bot misbehaves, the other
            # protections catch it as the actor and respond to the bot itself.
            await self.bot.logs.security(guild, f"{shield} Bot added",
                                         f"{plain(who(actor), 80)} added a bot. They can manage the server, so I left it in place.",
                                         [("Bot", who(member)), ("Added by", who(actor)),
                                          ("To remove it", "kick it yourself, or whitelist the person who adds bots to stop these notes")])
            return
        try:
            await member.kick(reason="antinuke: bot added by someone without permission to manage the server")
            removal = "I removed the bot"
        except discord.HTTPException:
            removal = "I could NOT remove the bot"
        await self.bot.logs.security(guild, f"{siren} URGENT: bot added without permission",
                                     f"A bot was added by someone who shouldn't be able to add bots. {removal}.",
                                     [("Bot", who(member)), ("Added by", who(actor) if actor else str(actor_id))], urgent=True)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel) -> None:
        self._snapshot_later(channel.guild, "channel", channel.id)
        await self._handle(channel.guild, {A.channel_create: "channel_create"}, target_id=channel.id, extra=f"created {plain(channel.name)}")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel) -> None:
        if await self._is_managed_channel(channel.id):
            return
        await self._handle(channel.guild, {A.channel_delete: "channel_delete"}, target_id=channel.id, extra=f"deleted {plain(channel.name)}", restore=channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after) -> None:
        if await self._is_managed_channel(after.id):
            return
        self._snapshot_later(after.guild, "channel", after.id)
        gained = []
        for target, ow in after.overwrites.items():
            old = before.overwrites_for(target).pair()[0].value
            gained += [f"{n} for {plain(getattr(target, 'name', target.id))}" for n in dangerous_gained(old, ow.pair()[0].value)]
        if gained:
            await self._handle(after.guild, {A.overwrite_update: "channel_permission_escalation", A.overwrite_create: "channel_permission_escalation"},
                               target_id=after.id, extra="granted " + ", ".join(gained[:5]))
        if before.name != after.name or getattr(before, "topic", None) != getattr(after, "topic", None) or before.overwrites != after.overwrites:
            await self._handle(after.guild, {A.channel_update: "channel_update", A.overwrite_create: "channel_update",
                                             A.overwrite_update: "channel_update", A.overwrite_delete: "channel_update"},
                               target_id=after.id, extra=f"changed {plain(after.name)}")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role) -> None:
        if role.managed:
            return
        self._snapshot_later(role.guild, "role", role.id)
        await self._handle(role.guild, {A.role_create: "role_create"}, target_id=role.id, extra=f"created {plain(role.name)}")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role) -> None:
        if role.managed:  # a bot's own role disappears when the bot leaves; that isn't an attack
            return
        await self._handle(role.guild, {A.role_delete: "role_delete"}, target_id=role.id, extra=f"deleted {plain(role.name)}", restore=role.id)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after) -> None:
        if after.managed:
            return
        self._snapshot_later(after.guild, "role", after.id)
        gained = dangerous_gained(before.permissions.value, after.permissions.value)
        if gained:
            await self._handle(after.guild, {A.role_update: "role_permission_escalation"}, target_id=after.id,
                               extra=f"{plain(after.name)} gained " + ", ".join(gained))
        if (before.name, before.permissions, before.colour) != (after.name, after.permissions, after.colour):
            await self._handle(after.guild, {A.role_update: "role_update"}, target_id=after.id, extra=f"changed {plain(after.name)}")

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel) -> None:
        await self._handle(channel.guild, {A.webhook_create: "webhook_create", A.webhook_update: "webhook_update", A.webhook_delete: "webhook_delete"},
                           extra=f"webhooks changed in {plain(channel.name)}")

    @commands.Cog.listener()
    async def on_guild_update(self, before, after) -> None:
        if before.vanity_url_code != after.vanity_url_code:
            await self._handle(after, {A.guild_update: "vanity_change"}, target_id=after.id, extra="vanity URL changed")
        if (before.name, before.icon, before.verification_level, before.explicit_content_filter) != (
                after.name, after.icon, after.verification_level, after.explicit_content_filter):
            await self._handle(after, {A.guild_update: "guild_update"}, target_id=after.id, extra="server settings changed")

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after) -> None:
        await self._handle(guild, {A.emoji_create: "emoji_update", A.emoji_update: "emoji_update", A.emoji_delete: "emoji_update"}, extra="emojis changed")

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after) -> None:
        await self._handle(guild, {A.sticker_create: "sticker_update", A.sticker_update: "sticker_update", A.sticker_delete: "sticker_update"}, extra="stickers changed")

    @commands.Cog.listener()
    async def on_guild_integrations_update(self, guild) -> None:
        await self._handle(guild, {A.integration_create: "integration_update", A.integration_update: "integration_update", A.integration_delete: "integration_update"}, extra="integrations changed")

    @commands.Cog.listener()
    async def on_invite_create(self, invite) -> None:
        if invite.guild is not None and invite.inviter is not None:
            await self._handle(invite.guild, {"direct": "invite_create"}, extra=f"invite {invite.code} created", actor_override=invite.inviter.id)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite) -> None:
        if invite.guild is not None:
            await self._handle(invite.guild, {A.invite_delete: "invite_delete"}, extra=f"invite {invite.code} deleted")

    @commands.Cog.listener()
    async def on_member_update(self, before, after) -> None:
        new_roles = [r for r in after.roles if r not in before.roles and r.permissions.administrator]
        if new_roles:
            await self._handle(after.guild, {A.member_role_update: "admin_grant"}, target_id=after.id,
                               extra=f"{who(after)} was given administrator via {', '.join(plain(r.name) for r in new_roles)}")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is not None and len(payload.message_ids) >= 10:
            await self._handle(guild, {A.message_bulk_delete: "mass_message_delete"}, target_id=payload.channel_id, extra=f"{len(payload.message_ids)} messages bulk-deleted")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        perms = message.author.guild_permissions
        if not (perms.administrator or perms.mention_everyone or perms.manage_guild):
            return
        if not (message.mention_everyone or len(message.mentions) + len(message.role_mentions) >= 10):
            return
        cfg = await self.config(message.guild.id)
        if not cfg.enabled or not self.effective(cfg, "mass_mention").enabled:
            return
        if message.author.id != message.guild.owner_id and not await self.bot.whitelist.is_exempt(
                message.guild.id, message.author.id, [r.id for r in message.author.roles], "antinuke:mass_mention"):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        await self._handle(message.guild, {"direct": "mass_mention"}, extra="mass mention by a privileged member", actor_override=message.author.id)

    # --- commands ----------------------------------------------------------------------------
    async def _status_embed(self, guild: discord.Guild) -> discord.Embed:
        cfg = await self.config(guild.id)
        lines = [f"{ICONS.get('check' if self.effective(cfg, m.key).enabled else 'cross')} `{module_display(m.key)}`" for m in ANTINUKE_MODULES]
        embed = discord.Embed(title=f"{ICONS.get('antinuke')} Anti-Nuke — {'Enabled' if cfg.enabled else 'Disabled'}",
                              description="\n".join(lines), color=NEUTRAL)
        embed.set_footer(text=f"Punishment: {cfg.punishment} • alert at {cfg.alert_at}, respond at {cfg.punish_at} within {cfg.window}s")
        return embed

    def _parse_state(self, state: Optional[str]) -> Optional[bool]:
        if state is None or state.lower() == "status":
            return None
        if state.lower() not in ("on", "off", "enable", "disable"):
            raise InvalidInput("Use `on`, `off` or `status`.")
        return state.lower() in ("on", "enable")

    @commands.hybrid_group(name="antinuke", fallback="toggle", description="Turn antinuke on or off, or show its status")
    @app_commands.describe(state="on, off or status")
    async def antinuke(self, ctx: commands.Context, state: Optional[str] = None) -> None:
        new = self._parse_state(state)
        if new is None:
            await ctx.send(embed=await self._status_embed(ctx.guild))
            return
        await self._set_guild_enabled(ctx.guild.id, new)
        if new:
            await self.snapshot_guild(ctx.guild)
        await self.bot.logs.security(ctx.guild, "Antinuke setting changed", fields=[("Antinuke", "on" if new else "off"), ("By", who(ctx.author))])
        await ctx.send(embed=toggle_embed("antinuke", new))

    @antinuke.command(name="status", description="Show every protection and its state")
    async def an_status(self, ctx: commands.Context) -> None:
        await ctx.send(embed=await self._status_embed(ctx.guild))

    @antinuke.command(name="module", description="Turn one protection on or off")
    @app_commands.describe(module="For example ban, channel_delete, bot_add", state="on or off")
    async def an_module(self, ctx: commands.Context, module: str, state: str) -> None:
        scope = normalize_scope(module if ":" in module else f"antinuke:{module}")
        if not scope.startswith("antinuke:"):
            raise InvalidInput("That isn't an antinuke protection.")
        new = self._parse_state(state)
        if new is None:
            raise InvalidInput("Use `on` or `off`.")
        await self._set_modules(ctx.guild.id, [scope.split(":")[1]], new)
        await self.bot.logs.security(ctx.guild, "Antinuke setting changed", fields=[("Protection", TITLES[scope.split(":")[1]]), ("State", "on" if new else "off"), ("By", who(ctx.author))])
        await ctx.send(embed=toggle_embed(module_display(scope.split(":")[1]), new))

    @antinuke.command(name="set", description="Set thresholds and response for one protection")
    @app_commands.describe(module="Protection key", alert_at="Alert at this many incidents", punish_at="Respond at this many", window="Window in seconds", punishment="log, kick, ban, jail or strip")
    async def an_set(self, ctx: commands.Context, module: str, alert_at: commands.Range[int, 1, 50], punish_at: commands.Range[int, 1, 50],
                     window: commands.Range[int, 5, 3600], punishment: Optional[str] = None) -> None:
        scope = normalize_scope(module if ":" in module else f"antinuke:{module}")
        if not scope.startswith("antinuke:"):
            raise InvalidInput("That isn't an antinuke protection.")
        if punish_at < alert_at:
            raise InvalidInput("The response threshold can't be lower than the alert threshold.")
        key = scope.split(":")[1]
        pun = validate_punishment(punishment) if punishment else None
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, ctx.guild.id)
            await conn.execute(_ENSURE_AN, ctx.guild.id)
            await conn.execute(
                "INSERT INTO antinuke_modules (guild_id, key, alert_at, punish_at, window_seconds, punishment) VALUES ($1,$2,$3,$4,$5,$6) "
                "ON CONFLICT (guild_id, key) DO UPDATE SET alert_at = $3, punish_at = $4, window_seconds = $5, punishment = COALESCE($6, antinuke_modules.punishment)",
                ctx.guild.id, key, alert_at, punish_at, window, pun,
            )
        self._cache.pop(ctx.guild.id, None)
        await self.bot.logs.security(ctx.guild, "Antinuke setting changed", fields=[("Protection", TITLES[key]), ("Thresholds", f"{alert_at}/{punish_at} in {window}s, response {pun or 'unchanged'}"), ("By", who(ctx.author))])
        await ctx.send(embed=ok_embed(f"{TITLES[key]}: alert at {alert_at}, respond at {punish_at}, window {window}s" + (f", response {pun}." if pun else ".")))

    @antinuke.command(name="setup", description="One-click setup with a confirmation preview")
    async def an_setup(self, ctx: commands.Context) -> None:
        embed = info_embed("Antinuke setup", "\n".join(f"• {line}" for line in self.plan_setup(ctx.guild)))
        if await ask(ctx, embed) != 0:
            await ctx.send(embed=info_embed(None, "Cancelled. Nothing was changed."))
            return
        results = await self.apply_setup(ctx.guild)
        await self.bot.logs.security(ctx.guild, "Antinuke setup applied", fields=[("By", who(ctx.author))])
        await ctx.send(embed=ok_embed("\n".join(results)))

    @antinuke.command(name="log", description="Set or show the security alert channel")
    @app_commands.describe(channel="Channel for security alerts (leave empty to show)")
    async def an_log(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        if channel is None:
            s = await self.bot.logs.settings(ctx.guild.id)
            await ctx.send(embed=info_embed("Security alerts", f"<#{s.security_channel_id}>" if s.security_channel_id else "Not set. Run `setuplogs` or `antinukelog #channel`."))
            return
        await self.bot.logs.save_channels(ctx.guild.id, None, channel.id)
        await ctx.send(embed=ok_embed(f"Security alerts will go to {channel.mention}."))

    @commands.command(name="antinukelog", help="Set or show the security alert channel")
    async def antinukelog(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        await self.an_log.callback(self, ctx, channel)

    def _make_shortcut(self, keys: list[str]):
        async def shortcut(ctx: commands.Context, state: Optional[str] = None) -> None:
            ensure_security_admin(ctx)
            cfg = await self.config(ctx.guild.id)
            if state is not None and state.lower() == "status":
                lines = [f"{ICONS.get('check' if self.effective(cfg, k).enabled else 'cross')} `{module_display(k)}`" for k in keys]
                await ctx.send(embed=info_embed("Antinuke", "\n".join(lines) + ("" if cfg.enabled else "\n\nAnti-nuke itself is OFF (`antinuke on`).")))
                return
            new = (not all(self.effective(cfg, k).enabled for k in keys)) if state is None else self._parse_state(state)
            await self._set_modules(ctx.guild.id, keys, new)
            await ctx.send(embed=toggle_embed(" + ".join(module_display(k) for k in keys), new,
                                              "" if cfg.enabled else "-# Anti-nuke itself is off. Turn it on with `antinuke on`."))
        return shortcut

    @commands.hybrid_command(name="punishment", description="Choose what happens when anti-nuke triggers")
    @app_commands.describe(choice="strip, ban, kick, jail or log (leave empty to show the current one)")
    async def punishment(self, ctx: commands.Context, choice: Optional[str] = None) -> None:
        cfg = await self.config(ctx.guild.id)
        if choice is None:
            await ctx.send(embed=info_embed("Anti-nuke punishment", f"Current response: **{cfg.punishment}**.\nChange it: `punishment strip|ban|kick|jail`."))
            return
        pun = validate_punishment(choice)
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, ctx.guild.id)
            await conn.execute(_ENSURE_AN, ctx.guild.id)
            await conn.execute("UPDATE antinuke_guild SET default_punishment = $2 WHERE guild_id = $1", ctx.guild.id, pun)
        self._cache.pop(ctx.guild.id, None)
        await self.bot.logs.security(ctx.guild, "Antinuke setting changed", fields=[("Punishment", pun), ("By", who(ctx.author))])
        await ctx.send(embed=ok_embed(f"Anti-nuke punishment is now **{pun}**."))


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Antinuke(bot))

"""Automod. Rules are per server, each with its own threshold, response and exemptions.

An exemption only ever covers the rule it was made for (whitelist scope automod:<rule>, or the whole
automod area when explicitly chosen). The server owner is the only automatic exemption.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core_automod_logic import (
    RULE_DEFAULTS, count_mentions, disallowed_links, normalize_domain, validate_action,
)
from core_embeds import NEUTRAL, info_embed, ok_embed, toggle_embed
from core_emoji import ICONS
from core_errors import InvalidInput
from core_sliding import SlidingCounter
from svc_log_service import plain, who

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.automod")
_ENSURE = "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING"
COLUMNS = {"enabled", "threshold", "window_seconds", "action"}
NSFW_NOTE = (
    "-# Heads-up: I can't scan images myself (that needs a classification service I don't have), so this switch "
    "doesn't block anything yet. Discord's own explicit media filter does: Server Settings, Safety Setup."
)
NAMES = {"link": "antilink", "mention": "antimention", "spam": "antispam", "raid": "antiraid", "nsfw": "antinsfw"}
_MENTION = re.compile(r"<(@!?|@&|#)(\d+)>")


class Automod(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self.spam = SlidingCounter()
        self.joins = SlidingCounter()
        self._cooldown: dict[tuple, float] = {}
        self._raid_until: dict[int, float] = {}
        self._rules: dict[int, dict[str, dict]] = {}
        self._exempt: dict[int, set[tuple[str, str, str]]] = {}

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not ctx.author.guild_permissions.manage_guild:
            raise commands.MissingPermissions(["manage_guild"])
        return True

    # --- storage -----------------------------------------------------------------------------
    async def rules(self, guild_id: int) -> dict[str, dict]:
        cached = self._rules.get(guild_id)
        if cached is None:
            rows = await self.bot.db.fetch("SELECT * FROM automod_rules WHERE guild_id = $1", guild_id)
            cached = {r["rule"]: dict(r) for r in rows}
            self._rules[guild_id] = cached
        return cached

    async def exemptions(self, guild_id: int) -> set[tuple[str, str, str]]:
        cached = self._exempt.get(guild_id)
        if cached is None:
            rows = await self.bot.db.fetch("SELECT rule, kind, value FROM automod_exemptions WHERE guild_id = $1", guild_id)
            cached = {(r["rule"], r["kind"], r["value"]) for r in rows}
            self._exempt[guild_id] = cached
        return cached

    async def _upsert(self, guild_id: int, rule: str, **cols) -> None:
        assert set(cols) <= COLUMNS
        names = list(cols)
        sets = ", ".join(f"{n} = ${i + 3}" for i, n in enumerate(names))
        marks = ", ".join(f"${i + 3}" for i in range(len(names)))
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, guild_id)
            await conn.execute(
                f"INSERT INTO automod_rules (guild_id, rule, {', '.join(names)}) VALUES ($1, $2, {marks}) "
                f"ON CONFLICT (guild_id, rule) DO UPDATE SET {sets}",
                guild_id, rule, *cols.values(),
            )
        self._rules.pop(guild_id, None)

    def _eff(self, rules: dict, rule: str) -> dict:
        row = rules.get(rule, {})
        d_thr, d_win, d_act = RULE_DEFAULTS.get(rule, (1, 0, "delete"))
        return {"enabled": bool(row.get("enabled")), "threshold": row.get("threshold") or d_thr,
                "window": row.get("window_seconds") if row.get("window_seconds") is not None else d_win,
                "action": row.get("action") or d_act}

    # --- setup plan (used by /setup) ---------------------------------------------------------
    def plan_defaults(self, guild: discord.Guild) -> list[str]:
        return ["turn on the link filter (deletes links)", "turn on mention limits (5 mentions, 10 minute timeout)",
                "turn on spam detection (6 messages in 8s, 10 minute timeout)",
                "turn on raid detection (8 joins in 10s, alert only)", "image scanning is not offered (see antinsfw)"]

    async def apply_defaults(self, guild: discord.Guild) -> list[str]:
        for rule in ("link", "mention", "spam", "raid"):
            thr, win, act = RULE_DEFAULTS[rule]
            await self._upsert(guild.id, rule, enabled=True, threshold=thr, window_seconds=win, action=act)
        return ["automod defaults are on: link, mention, spam, raid"]

    # --- enforcement -------------------------------------------------------------------------
    async def _exempt_for(self, message: discord.Message, rule: str, rules_exempt: set) -> bool:
        if message.author.id == message.guild.owner_id or ("channel" in {k for r, k, v in rules_exempt if r == rule and v == str(message.channel.id)}):
            return True
        return await self.bot.whitelist.is_exempt(message.guild.id, message.author.id, [r.id for r in message.author.roles], f"automod:{rule}")

    async def _act(self, message: discord.Message, rule: str, action: str, why: str) -> None:
        member, result = message.author, "done"
        try:
            if action == "warn":
                await message.channel.send(f"{discord.utils.escape_markdown(member.display_name)}: {why}", delete_after=10, allowed_mentions=discord.AllowedMentions.none())
            else:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                if action in ("timeout", "kick") and member.top_role >= message.guild.me.top_role:
                    result = "not applied: their top role is at or above mine"
                elif action == "timeout":
                    await member.timeout(timedelta(minutes=10), reason=f"automod {rule}")
                elif action == "kick":
                    await member.kick(reason=f"automod {rule}")
        except discord.HTTPException as exc:
            result = f"FAILED (HTTP {exc.status})"
        await self.bot.logs.security(message.guild, f"Automod: {rule}", why, [("Member", who(member)), ("Channel", f"#{plain(message.channel.name, 80)}"), ("Action", f"{action}: {result}")])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        rules = await self.rules(message.guild.id)
        if not any(r.get("enabled") for r in rules.values()):
            return
        exempt = await self.exemptions(message.guild.id)
        now = time.time()

        link = self._eff(rules, "link")
        if link["enabled"] and message.content and not await self._exempt_for(message, "link", exempt):
            allowed = {v for r, k, v in exempt if r == "link" and k == "domain"}
            bad = disallowed_links(message.content, allowed)
            if bad:
                await self._act(message, "link", link["action"], f"links aren't allowed here ({plain(bad[0], 60)})")
                return

        mention = self._eff(rules, "mention")
        if mention["enabled"] and not await self._exempt_for(message, "mention", exempt):
            total = count_mentions([u.id for u in message.mentions], [r.id for r in message.role_mentions], message.mention_everyone)
            if total >= mention["threshold"]:
                await self._act(message, "mention", mention["action"], f"too many mentions ({total})")
                return

        spam = self._eff(rules, "spam")
        if spam["enabled"] and not await self._exempt_for(message, "spam", exempt):
            key = (message.guild.id, message.author.id)
            count = self.spam.add(key, now, spam["window"])
            if now < self._cooldown.get(key, 0):  # same incident: don't punish twice
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                return
            if count >= spam["threshold"]:
                self._cooldown[key] = now + max(spam["window"], 10)
                self.spam.reset(key)
                if len(self._cooldown) > 5000:
                    self._cooldown = {k: t for k, t in self._cooldown.items() if t > now}
                await self._act(message, "spam", spam["action"], f"spam ({count} messages in {spam['window']}s)")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        rules = await self.rules(member.guild.id)
        raid = self._eff(rules, "raid")
        if not raid["enabled"]:
            return
        now = time.time()
        count = self.joins.add(member.guild.id, now, raid["window"])
        if count >= raid["threshold"] and now >= self._raid_until.get(member.guild.id, 0):
            self._raid_until[member.guild.id] = now + 300
            await self.bot.logs.security(member.guild, "URGENT: possible raid", f"{count} members joined within {raid['window']}s.",
                                         [("Response", "kick new joiners for 5 minutes" if raid["action"] == "kick" else "alert only")])
        if raid["action"] == "kick" and now < self._raid_until.get(member.guild.id, 0):
            try:
                await member.kick(reason="automod: raid mode")
            except discord.HTTPException:
                log.warning("raid kick failed in %s", member.guild.id)

    # --- commands ----------------------------------------------------------------------------
    async def _configure(self, ctx: commands.Context, rule: str, state: Optional[str], threshold: Optional[int],
                         window: Optional[int], action: Optional[str]) -> None:
        rules = await self.rules(ctx.guild.id)
        eff = self._eff(rules, rule)
        state = (state or "toggle").lower()
        if state == "toggle":
            state = "off" if eff["enabled"] else "on"
        if state in ("on", "off"):
            await self._upsert(ctx.guild.id, rule, enabled=state == "on", threshold=eff["threshold"], window_seconds=eff["window"], action=eff["action"])
            await self.bot.logs.security(ctx.guild, "Automod setting changed", fields=[("Rule", NAMES[rule]), ("State", state), ("By", who(ctx.author))])
            await ctx.send(embed=toggle_embed(NAMES[rule], state == "on", NSFW_NOTE if rule == "nsfw" and state == "on" else ""))
        elif state == "set":
            if rule == "nsfw":
                raise InvalidInput("Anti-NSFW has no settings.")
            if threshold is None or action is None:
                raise InvalidInput("Use `set <threshold> " + ("" if rule in ("link", "mention") else "<seconds> ") + "<action>`.")
            if rule not in ("link", "mention") and window is None:
                raise InvalidInput("Include the window in seconds.")
            await self._upsert(ctx.guild.id, rule, threshold=max(1, threshold), window_seconds=window if window is not None else eff["window"], action=validate_action(rule, action))
            await ctx.send(embed=ok_embed(f"`{NAMES[rule]}` updated."))
        elif state == "status":
            icon = ICONS.get("toggle_on" if eff["enabled"] else "toggle_off")
            await ctx.send(embed=info_embed(f"Automod: {NAMES[rule]}", f"{icon} {'on' if eff['enabled'] else 'off'}, threshold {eff['threshold']}, window {eff['window']}s, action {eff['action']}"))
        else:
            raise InvalidInput("Use `on`, `off`, `status` or `set` (no argument flips it).")

    @commands.hybrid_command(name="automod", description="Show which automod rules are enabled")
    async def automod(self, ctx: commands.Context) -> None:
        rules = await self.rules(ctx.guild.id)
        lines = [f"{ICONS.get('toggle_on' if self._eff(rules, r)['enabled'] else 'toggle_off')} `{NAMES[r]}`" for r in ("link", "mention", "spam", "raid", "nsfw")]
        embed = discord.Embed(title=f"{ICONS.get('info')} Automod Status", description="\n".join(lines), color=NEUTRAL)
        embed.set_footer(text="Toggle any rule by typing its name. Users and roles are exempted with the whitelist command.")
        await ctx.send(embed=embed)

    @commands.hybrid_group(name="antilink", fallback="toggle", description="Configure the link filter")
    @app_commands.describe(state="on, off, status or set", threshold="(set) unused for links", action="(set) delete, warn, timeout or kick")
    async def antilink(self, ctx: commands.Context, state: Optional[str] = None, threshold: Optional[int] = None, action: Optional[str] = None) -> None:
        await self._configure(ctx, "link", state, threshold if threshold is not None else 1, 0, action)

    async def _exemption(self, ctx: commands.Context, value: str, add: bool) -> None:
        match = _MENTION.fullmatch(value.strip())
        if match and match.group(1) in ("@", "@!", "@&"):
            target_type = "role" if match.group(1) == "@&" else "user"
            if add:
                await self.bot.whitelist.add(guild_id=ctx.guild.id, actor_id=ctx.author.id, target_type=target_type, target_id=int(match.group(2)), scope="automod:link")
            else:
                await self.bot.whitelist.remove(guild_id=ctx.guild.id, actor_id=ctx.author.id, target_type=target_type, target_id=int(match.group(2)), scope="automod:link")
            await ctx.send(embed=ok_embed(("Exempted" if add else "Removed the exemption for") + f" {value.strip()} (link filter only)."))
            return
        kind, text = ("channel", match.group(2)) if match else ("domain", normalize_domain(value))
        sql = ("INSERT INTO automod_exemptions (guild_id, rule, kind, value) VALUES ($1, 'link', $2, $3) ON CONFLICT DO NOTHING" if add
               else "DELETE FROM automod_exemptions WHERE guild_id = $1 AND rule = 'link' AND kind = $2 AND value = $3")
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, ctx.guild.id)
            await conn.execute(sql, ctx.guild.id, kind, text)
        self._exempt.pop(ctx.guild.id, None)
        await ctx.send(embed=ok_embed(f"{kind} `{text}` " + ("is now allowed by the link filter." if add else "is no longer exempt.")))

    @antilink.command(name="whitelist", description="Allow a domain, channel, user or role in the link filter")
    @app_commands.describe(value="A domain like example.com, or a #channel / @user / @role mention")
    async def al_whitelist(self, ctx: commands.Context, *, value: str) -> None:
        await self._exemption(ctx, value, True)

    @antilink.command(name="unwhitelist", description="Remove a link filter exemption")
    @app_commands.describe(value="A domain like example.com, or a #channel / @user / @role mention")
    async def al_unwhitelist(self, ctx: commands.Context, *, value: str) -> None:
        await self._exemption(ctx, value, False)

    @commands.hybrid_command(name="antimention", description="Configure the mass-mention rule")
    @app_commands.describe(state="on, off, status or set", threshold="(set) mentions allowed per message", action="(set) delete, warn, timeout or kick")
    async def antimention(self, ctx: commands.Context, state: Optional[str] = None, threshold: Optional[int] = None, action: Optional[str] = None) -> None:
        await self._configure(ctx, "mention", state, threshold, 0, action)

    @commands.hybrid_command(name="antispam", description="Configure spam detection")
    @app_commands.describe(state="on, off, status or set", messages="(set) messages allowed in the window", seconds="(set) window in seconds", action="(set) delete, warn, timeout or kick")
    async def antispam(self, ctx: commands.Context, state: Optional[str] = None, messages: Optional[int] = None, seconds: Optional[int] = None, action: Optional[str] = None) -> None:
        await self._configure(ctx, "spam", state, messages, seconds, action)

    @commands.hybrid_command(name="antiraid", description="Configure raid detection")
    @app_commands.describe(state="on, off, status or set", joins="(set) joins allowed in the window", seconds="(set) window in seconds", action="(set) alert or kick")
    async def antiraid(self, ctx: commands.Context, state: Optional[str] = None, joins: Optional[int] = None, seconds: Optional[int] = None, action: Optional[str] = None) -> None:
        await self._configure(ctx, "raid", state, joins, seconds, action)

    @commands.hybrid_command(name="antinsfw", description="Toggle the NSFW rule (image scanning isn't available)")
    @app_commands.describe(state="on, off or status (no argument flips it)")
    async def antinsfw(self, ctx: commands.Context, state: Optional[str] = None) -> None:
        await self._configure(ctx, "nsfw", state, None, None, None)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Automod(bot))

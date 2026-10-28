"""Moderation module. Every action re-checks permissions and role hierarchy at execution time and is logged."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core_checks import moderator, server_admin
from core_embeds import info_embed, ok_embed
from core_errors import InvalidInput
from core_formatting import chunk_lines
from core_moderation_checks import MAX_TIMEOUT_SECONDS, hierarchy_problem, parse_duration, roles_to_restore, short_duration
from svc_log_service import who

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.moderation")
_ENSURE = "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING"
REASONED = {"ban", "kick", "softban", "timeout", "unban", "unmute", "jail", "unjail"}


class Moderation(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self._task: Optional[asyncio.Task] = None
        self._roleall_running: set[int] = set()

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._jail_loop(), name="jail-release")

    async def cog_unload(self) -> None:
        if self._task:
            self._task.cancel()

    # --- shared helpers ----------------------------------------------------------------------
    def _problem(self, ctx: commands.Context, member: discord.Member) -> Optional[str]:
        return hierarchy_problem(
            actor_id=ctx.author.id, target_id=member.id, actor_top=ctx.author.top_role.position,
            target_top=member.top_role.position, bot_top=ctx.guild.me.top_role.position,
            guild_owner_id=ctx.guild.owner_id, bot_id=self.bot.user.id, bot_owner_id=self.bot.settings.bot_owner_id,
        )

    def _guard(self, ctx: commands.Context, member: Optional[discord.Member]) -> None:
        if member is None:
            return
        problem = self._problem(ctx, member)
        if problem:
            raise InvalidInput(problem)

    @staticmethod
    def _reason(ctx: commands.Context, reason: Optional[str]) -> str:
        return f"{ctx.author} ({ctx.author.id}): {reason or 'no reason given'}"[:500]

    async def _record(self, guild: discord.Guild, action: str, target, actor_id: int, reason: Optional[str]) -> None:
        await self.bot.db.execute(
            "INSERT INTO mod_actions (guild_id, action, target_id, actor_id, reason) VALUES ($1, $2, $3, $4, $5)",
            guild.id, action, getattr(target, "id", target), actor_id, reason,
        )
        await self.bot.logs.security(guild, f"Moderation: {action}", fields=[
            ("Target", who(target) if hasattr(target, "id") else str(target)), ("Moderator", str(actor_id)),
            ("Reason", reason or "no reason given"),
        ])

    async def _done(self, ctx: commands.Context, action: str, target, reason: Optional[str], text: str) -> None:
        await self._record(ctx.guild, action, target, ctx.author.id, reason)
        if action in REASONED:
            text += f"\n**Reason:** {reason or 'No reason provided'}"
        await ctx.send(embed=ok_embed(text))

    @staticmethod
    def _assignable(guild: discord.Guild, role: discord.Role) -> Optional[str]:
        if role.is_default() or role.managed:
            return "That role can't be assigned manually."
        if role >= guild.me.top_role:
            return "That role is at or above my top role."
        return None

    # --- ban / kick / softban / unban --------------------------------------------------------
    @commands.hybrid_command(name="ban", description="Ban a user")
    @app_commands.describe(user="Who to ban", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @moderator("ban_members")
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, user: discord.User, *, reason: Optional[str] = None) -> None:
        self._guard(ctx, ctx.guild.get_member(user.id))
        await ctx.guild.ban(user, reason=self._reason(ctx, reason), delete_message_seconds=0)
        await self._done(ctx, "ban", user, reason, f"Banned **{user}**.")

    @commands.hybrid_command(name="kick", description="Kick a member")
    @app_commands.describe(member="Who to kick", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @moderator("kick_members")
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        self._guard(ctx, member)
        await member.kick(reason=self._reason(ctx, reason))
        await self._done(ctx, "kick", member, reason, f"Kicked **{member}**.")

    @commands.hybrid_command(name="softban", description="Ban then unban to clear a member's recent messages")
    @app_commands.describe(member="Who to softban", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @moderator("ban_members")
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        self._guard(ctx, member)
        await ctx.guild.ban(member, reason=f"softban: {self._reason(ctx, reason)}"[:500], delete_message_seconds=86_400)
        await ctx.guild.unban(member, reason="softban release")
        await self._done(ctx, "softban", member, reason, f"Softbanned **{member}**. Their last day of messages was removed.")

    @commands.hybrid_command(name="unban", description="Unban a user")
    @app_commands.describe(user="Who to unban", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @moderator("ban_members")
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user: discord.User, *, reason: Optional[str] = None) -> None:
        try:
            await ctx.guild.unban(user, reason=self._reason(ctx, reason))
        except discord.NotFound:
            raise InvalidInput(f"**{user}** isn't banned.")
        await self._done(ctx, "unban", user, reason, f"Unbanned **{user}**.")

    # --- timeout -----------------------------------------------------------------------------
    @commands.hybrid_command(name="timeout", aliases=["mute"], description="Time a member out")
    @app_commands.describe(member="Who", duration="For example 30m, 2h, 1d", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @moderator("moderate_members")
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: Optional[str] = None) -> None:
        self._guard(ctx, member)
        seconds = parse_duration(duration, maximum=MAX_TIMEOUT_SECONDS)
        await member.timeout(timedelta(seconds=seconds), reason=self._reason(ctx, reason))
        await self._done(ctx, "timeout", member, reason, f"Timed out **{member}** for `{short_duration(seconds)}`.")

    @commands.hybrid_command(name="unmute", aliases=["untimeout"], description="Remove a member's timeout")
    @app_commands.describe(member="Who", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @moderator("moderate_members")
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        self._guard(ctx, member)
        await member.timeout(None, reason=self._reason(ctx, reason))
        await self._done(ctx, "unmute", member, reason, f"Removed the timeout from **{member}**.")

    # --- jail --------------------------------------------------------------------------------
    @commands.hybrid_command(name="jailrole", description="Choose the role given to jailed members")
    @app_commands.describe(role="The jail role (it should deny viewing or sending in your channels)")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @server_admin()
    async def jailrole(self, ctx: commands.Context, role: discord.Role) -> None:
        problem = self._assignable(ctx.guild, role)
        if problem:
            raise InvalidInput(problem)
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, ctx.guild.id)
            await conn.execute(
                "INSERT INTO mod_settings (guild_id, jail_role_id) VALUES ($1, $2) "
                "ON CONFLICT (guild_id) DO UPDATE SET jail_role_id = EXCLUDED.jail_role_id",
                ctx.guild.id, role.id,
            )
        await ctx.send(embed=ok_embed(f"{role.mention} is now the jail role. Make sure it can't see or send in the channels you want protected."))

    async def jail_member(self, guild: discord.Guild, member: discord.Member, *, jailed_by: int,
                          reason: Optional[str], seconds: Optional[int]) -> None:
        jail_id = await self.bot.db.fetchval("SELECT jail_role_id FROM mod_settings WHERE guild_id = $1", guild.id)
        role = guild.get_role(jail_id) if jail_id else None
        if role is None:
            raise InvalidInput("No jail role is set. Use `jailrole @role` first.")
        if role >= guild.me.top_role:
            raise InvalidInput("My top role must be above the jail role.")
        if await self.bot.db.fetchval("SELECT 1 FROM jail_records WHERE guild_id = $1 AND user_id = $2", guild.id, member.id):
            raise InvalidInput(f"**{member}** is already jailed.")
        top = guild.me.top_role
        removable = [r for r in member.roles[1:] if not r.managed and r < top and r != role]
        kept = [r for r in member.roles[1:] if r not in removable and r != role]
        release_at = datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, guild.id)
            await conn.execute(
                "INSERT INTO jail_records (guild_id, user_id, role_ids, reason, jailed_by, release_at) VALUES ($1,$2,$3,$4,$5,$6)",
                guild.id, member.id, [r.id for r in removable], reason, jailed_by, release_at,
            )
        try:  # the record is saved first, so a crash can never lose the member's roles
            await member.edit(roles=[*kept, role], reason=f"jail: {reason or 'no reason'}"[:500])
        except discord.HTTPException:
            await self.bot.db.execute("DELETE FROM jail_records WHERE guild_id = $1 AND user_id = $2", guild.id, member.id)
            raise

    async def release_member(self, guild: discord.Guild, user_id: int, *, reason: str) -> str:
        record = await self.bot.db.fetchrow("SELECT role_ids FROM jail_records WHERE guild_id = $1 AND user_id = $2", guild.id, user_id)
        if record is None:
            return "not jailed"
        member = guild.get_member(user_id)
        result = "released"
        if member is None:
            result = "left the server (nothing to restore)"
        else:
            jail_id = await self.bot.db.fetchval("SELECT jail_role_id FROM mod_settings WHERE guild_id = $1", guild.id)
            top = guild.me.top_role
            assignable = {r.id for r in guild.roles if not r.is_default() and not r.managed and r < top}
            back = roles_to_restore(record["role_ids"], [r.id for r in member.roles], assignable)
            roles = [r for r in member.roles[1:] if r.id != jail_id] + [guild.get_role(i) for i in back]
            await member.edit(roles=roles, reason=reason[:500])
        await self.bot.db.execute("DELETE FROM jail_records WHERE guild_id = $1 AND user_id = $2", guild.id, user_id)
        return result

    @commands.hybrid_command(name="jail", description="Jail a member, optionally for a limited time")
    @app_commands.describe(member="Who", duration="Optional, for example 1h or 2d", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @moderator("manage_roles")
    @commands.bot_has_permissions(manage_roles=True)
    async def jail(self, ctx: commands.Context, member: discord.Member, duration: Optional[str] = None, *, reason: Optional[str] = None) -> None:
        self._guard(ctx, member)
        seconds = parse_duration(duration) if duration else None
        await self.jail_member(ctx.guild, member, jailed_by=ctx.author.id, reason=reason, seconds=seconds)
        until = f" until {discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=seconds), 'R')}" if seconds else ""
        await self._done(ctx, "jail", member, reason, f"Jailed **{member}**{until}.")

    @commands.hybrid_command(name="unjail", description="Release a jailed member")
    @app_commands.describe(member="Who")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @moderator("manage_roles")
    @commands.bot_has_permissions(manage_roles=True)
    async def unjail(self, ctx: commands.Context, member: discord.Member) -> None:
        result = await self.release_member(ctx.guild, member.id, reason=f"unjail by {ctx.author}")
        if result == "not jailed":
            raise InvalidInput(f"**{member}** isn't jailed.")
        await self._done(ctx, "unjail", member, None, f"Released **{member}**. Their earlier roles were restored where possible.")

    @commands.hybrid_command(name="jlist", description="List jailed members")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @moderator("manage_roles")
    async def jlist(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.fetch("SELECT user_id, release_at, reason FROM jail_records WHERE guild_id = $1 ORDER BY jailed_at", ctx.guild.id)
        if not rows:
            await ctx.send(embed=info_embed("Jailed members", "Nobody is jailed."))
            return
        lines = [
            f"• <@{r['user_id']}> " + (f"until {discord.utils.format_dt(r['release_at'], 'R')}" if r["release_at"] else "until released")
            + (f": {r['reason'][:60]}" if r["reason"] else "")
            for r in rows
        ]
        embed = info_embed(f"Jailed members ({len(rows)})", "")
        for index, chunk in enumerate(chunk_lines(lines)):
            embed.add_field(name="Members" if index == 0 else "More", value=chunk, inline=False)
        await ctx.send(embed=embed)

    async def _jail_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                due = await self.bot.db.fetch("SELECT guild_id, user_id FROM jail_records WHERE release_at IS NOT NULL AND release_at <= now()")
                for row in due:
                    guild = self.bot.get_guild(row["guild_id"])
                    if guild is None:
                        continue
                    try:
                        await self.release_member(guild, row["user_id"], reason="jail time ended")
                        await self.bot.logs.security(guild, "Jail released", fields=[("User", str(row["user_id"])), ("Reason", "time ended")])
                    except discord.HTTPException:
                        log.warning("could not auto-release %s in %s", row["user_id"], row["guild_id"])
            except Exception:
                log.warning("jail loop error", exc_info=True)
            await asyncio.sleep(30)

    # --- lock / unlock -----------------------------------------------------------------------
    @commands.hybrid_command(name="lock", description="Stop @everyone from sending in a channel")
    @app_commands.describe(channel="Channel to lock (default: this one)")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @moderator("manage_channels")
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or ctx.channel
        if await self.bot.db.fetchval("SELECT 1 FROM channel_locks WHERE guild_id = $1 AND channel_id = $2", ctx.guild.id, channel.id):
            raise InvalidInput(f"{channel.mention} is already locked.")
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        previous = {None: "none", True: "allow", False: "deny"}[overwrite.send_messages]
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, ctx.guild.id)
            await conn.execute("INSERT INTO channel_locks (guild_id, channel_id, previous, locked_by) VALUES ($1,$2,$3,$4)", ctx.guild.id, channel.id, previous, ctx.author.id)
        overwrite.send_messages = False
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=self._reason(ctx, "lock"))
        except discord.HTTPException:
            await self.bot.db.execute("DELETE FROM channel_locks WHERE guild_id = $1 AND channel_id = $2", ctx.guild.id, channel.id)
            raise
        await self._done(ctx, "lock", channel, None, f"Locked {channel.mention}.")

    @commands.hybrid_command(name="unlock", description="Restore a locked channel")
    @app_commands.describe(channel="Channel to unlock (default: this one)")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @moderator("manage_channels")
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or ctx.channel
        previous = await self.bot.db.fetchval("SELECT previous FROM channel_locks WHERE guild_id = $1 AND channel_id = $2", ctx.guild.id, channel.id)
        if previous is None:
            raise InvalidInput(f"{channel.mention} wasn't locked by me.")
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = {"none": None, "allow": True, "deny": False}[previous]  # only this one permission is touched
        await channel.set_permissions(ctx.guild.default_role, overwrite=None if overwrite.is_empty() else overwrite, reason=self._reason(ctx, "unlock"))
        await self.bot.db.execute("DELETE FROM channel_locks WHERE guild_id = $1 AND channel_id = $2", ctx.guild.id, channel.id)
        await self._done(ctx, "unlock", channel, None, f"Unlocked {channel.mention}.")

    # --- purge -------------------------------------------------------------------------------
    async def _prepare_purge(self, ctx: commands.Context) -> None:
        if ctx.interaction is None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        else:
            await ctx.defer(ephemeral=True)

    @commands.hybrid_command(name="purge", aliases=["clear"], description="Delete recent messages")
    @app_commands.describe(amount="How many recent messages (1-100)")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @moderator("manage_messages")
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(self, ctx: commands.Context, amount: commands.Range[int, 1, 100]) -> None:
        await self._prepare_purge(ctx)
        deleted = await ctx.channel.purge(limit=amount, bulk=True, reason=self._reason(ctx, "purge"))
        await self._record(ctx.guild, "purge", ctx.channel, ctx.author.id, f"{len(deleted)} messages")
        await ctx.send(embed=ok_embed(f"Deleted {len(deleted)} message(s). Discord can't bulk-delete messages older than 14 days."), delete_after=8)

    @commands.hybrid_command(name="purgeuser", description="Delete recent messages from one user")
    @app_commands.describe(user="Whose messages", amount="How many of their messages (1-100)")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @moderator("manage_messages")
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purgeuser(self, ctx: commands.Context, user: discord.User, amount: commands.Range[int, 1, 100] = 20) -> None:
        await self._prepare_purge(ctx)
        left = [amount]

        def check(message: discord.Message) -> bool:
            if message.author.id == user.id and left[0] > 0:
                left[0] -= 1
                return True
            return False

        deleted = await ctx.channel.purge(limit=300, check=check, bulk=True, reason=self._reason(ctx, "purgeuser"))
        await self._record(ctx.guild, "purgeuser", user, ctx.author.id, f"{len(deleted)} messages in #{ctx.channel.name}")
        await ctx.send(embed=ok_embed(f"Deleted {len(deleted)} message(s) from **{user}** (searched the last 300)."), delete_after=8)

    # --- roles -------------------------------------------------------------------------------
    def _role_rules(self, ctx: commands.Context, role: discord.Role) -> None:
        problem = self._assignable(ctx.guild, role)
        if problem:
            raise InvalidInput(problem)
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            raise InvalidInput("That role is at or above your top role.")

    @commands.hybrid_command(name="role", description="Add a role to a member, or remove it if they have it")
    @app_commands.describe(member="Who", role="Which role")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @moderator("manage_roles")
    @commands.bot_has_permissions(manage_roles=True)
    async def role(self, ctx: commands.Context, member: discord.Member, role: discord.Role) -> None:
        self._role_rules(ctx, role)
        if member.id != ctx.author.id:
            self._guard(ctx, member)
        if role in member.roles:
            await member.remove_roles(role, reason=self._reason(ctx, "role removed"))
            await self._done(ctx, "role_remove", member, role.name, f"Removed {role.mention} from **{member}**.")
        else:
            await member.add_roles(role, reason=self._reason(ctx, "role added"))
            await self._done(ctx, "role_add", member, role.name, f"Gave {role.mention} to **{member}**.")

    @commands.hybrid_command(name="roleall", aliases=["ra"], description="Give a role to everyone who lacks it")
    @app_commands.describe(role="Which role", who_gets_it="humans (default), bots or all")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @moderator("manage_roles")
    @commands.bot_has_permissions(manage_roles=True)
    async def roleall(self, ctx: commands.Context, role: discord.Role, who_gets_it: Optional[str] = "humans") -> None:
        self._role_rules(ctx, role)
        mode = (who_gets_it or "humans").lower()
        if mode not in ("humans", "bots", "all"):
            raise InvalidInput("Choose `humans`, `bots` or `all`.")
        if ctx.guild.id in self._roleall_running:
            raise InvalidInput("A role-all job is already running in this server.")
        targets = [m for m in ctx.guild.members if role not in m.roles and (mode == "all" or (mode == "bots") == m.bot)]
        if not targets:
            raise InvalidInput("Everyone eligible already has that role.")
        self._roleall_running.add(ctx.guild.id)
        try:
            await ctx.send(embed=info_embed(None, f"Giving {role.mention} to {len(targets)} member(s). I go slowly to respect Discord's rate limits (about {len(targets) * 0.5 / 60:.0f} min)."))
            done = failed = 0
            for member in targets:
                try:
                    await member.add_roles(role, reason=self._reason(ctx, "roleall"))
                    done += 1
                except discord.HTTPException:
                    failed += 1
                await asyncio.sleep(0.5)
            await self._record(ctx.guild, "roleall", role, ctx.author.id, f"{done} given, {failed} failed")
            await ctx.channel.send(embed=ok_embed(f"Role-all finished: {done} given, {failed} failed."))
        finally:
            self._roleall_running.discard(ctx.guild.id)

    @commands.hybrid_command(name="autorole", aliases=["ar"], description="Roles given to new members (no role lists them)")
    @app_commands.describe(role="Add this role to the list, or remove it if already listed")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @server_admin()
    async def autorole(self, ctx: commands.Context, role: Optional[discord.Role] = None) -> None:
        current = await self.bot.db.fetchval("SELECT autorole_ids FROM mod_settings WHERE guild_id = $1", ctx.guild.id) or []
        if role is None:
            await ctx.send(embed=info_embed("Autoroles", "\n".join(f"• <@&{r}>" for r in current) or "None set."))
            return
        if role.id in current:
            await self.bot.db.execute("UPDATE mod_settings SET autorole_ids = array_remove(autorole_ids, $2) WHERE guild_id = $1", ctx.guild.id, role.id)
            await ctx.send(embed=ok_embed(f"{role.mention} is no longer given to new members."))
            return
        problem = self._assignable(ctx.guild, role)
        if problem:
            raise InvalidInput(problem)
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, ctx.guild.id)
            await conn.execute(
                "INSERT INTO mod_settings (guild_id, autorole_ids) VALUES ($1, ARRAY[$2::BIGINT]) "
                "ON CONFLICT (guild_id) DO UPDATE SET autorole_ids = array_append(mod_settings.autorole_ids, $2::BIGINT)",
                ctx.guild.id, role.id,
            )
        notes = []
        if not ctx.guild.me.guild_permissions.manage_roles:
            notes.append("⚠ I'm missing the **Manage Roles** permission, so I can't give it yet.")
        if "MEMBER_VERIFICATION_GATE_ENABLED" in ctx.guild.features:
            notes.append("-# This server uses membership screening, so the role is given as soon as a new member accepts the rules.")
        await ctx.send(embed=ok_embed(f"New members will now get {role.mention}." + ("\n" + "\n".join(notes) if notes else "")))

    async def _apply_autoroles(self, member: discord.Member, *, why: str) -> None:
        """Give a new member the configured roles. Every failure is logged and explained, never silent."""
        guild = member.guild
        ids = await self.bot.db.fetchval("SELECT autorole_ids FROM mod_settings WHERE guild_id = $1", guild.id) or []
        if not ids or member.bot:
            return
        if await self.bot.db.fetchval("SELECT 1 FROM jail_records WHERE guild_id = $1 AND user_id = $2", guild.id, member.id):
            return  # a jailed member must not get roles back by rejoining
        if member.pending:  # membership screening: the role is given once they accept the rules
            log.info("autorole waiting for %s to pass membership screening in %s", member.id, guild.id)
            return
        if not guild.me.guild_permissions.manage_roles:
            await self.bot.logs.security(guild, "Autorole could not run", "I'm missing the Manage Roles permission.", [("Member", who(member))])
            return
        given, problems = [], []
        for role_id in ids:
            role = guild.get_role(role_id)
            if role is None:
                problems.append(f"role {role_id} no longer exists")
                continue
            problem = self._assignable(guild, role)
            if problem:
                problems.append(f"{role.name}: {problem}")
                continue
            try:
                await member.add_roles(role, reason="autorole", atomic=False)  # one role at a time; doesn't rely on a stale cache
                given.append(role.name)
            except discord.HTTPException as exc:
                problems.append(f"{role.name}: Discord refused (HTTP {exc.status})")
        if given:
            log.info("autorole (%s): gave %s to %s in %s", why, given, member.id, guild.id)
        if problems:
            log.warning("autorole problems in %s: %s", guild.id, problems)
            await self.bot.logs.security(guild, "Autorole could not give every role", "\n".join(problems), [("Member", who(member))])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        try:
            record = await self.bot.db.fetchrow("SELECT 1 FROM jail_records WHERE guild_id = $1 AND user_id = $2", guild.id, member.id)
            if record is not None:  # policy: leaving and rejoining does not escape jail
                jail_id = await self.bot.db.fetchval("SELECT jail_role_id FROM mod_settings WHERE guild_id = $1", guild.id)
                role = guild.get_role(jail_id) if jail_id else None
                if role is not None:
                    await member.add_roles(role, reason="still jailed")
                return
            await self._apply_autoroles(member, why="join")
        except discord.HTTPException:
            log.warning("join handling failed for %s in %s", member.id, guild.id, exc_info=True)
        except Exception:
            log.warning("autorole crashed for %s in %s", member.id, guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.pending and not after.pending:  # they just accepted the server rules
            try:
                await self._apply_autoroles(after, why="screening passed")
            except Exception:
                log.warning("autorole crashed after screening for %s", after.id, exc_info=True)

    # --- slowmode / prefix -------------------------------------------------------------------
    @commands.hybrid_command(name="slowmode", description="Set or clear slowmode (0 clears it)")
    @app_commands.describe(seconds="0 to 21600", channel="Default: this channel")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @moderator("manage_channels")
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: commands.Range[int, 0, 21600], channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or ctx.channel
        await channel.edit(slowmode_delay=seconds, reason=self._reason(ctx, "slowmode"))
        text = f"Slowmode in {channel.mention} is now {seconds}s." if seconds else f"Slowmode in {channel.mention} is off."
        await self._done(ctx, "slowmode", channel, str(seconds), text)

    @commands.hybrid_command(name="setprefix", description="Set this server's command prefix")
    @app_commands.describe(prefix="New prefix (1-5 characters), or 'reset' for the default")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @server_admin()
    async def setprefix(self, ctx: commands.Context, prefix: str) -> None:
        new_prefix = await self.bot.prefixes.set(ctx.guild.id, prefix)
        log.info("prefix changed guild=%s by=%s", ctx.guild.id, ctx.author.id)
        await ctx.send(embed=ok_embed(
            f"Prefix for this server is now `{new_prefix}`. Try `{new_prefix}help`.\n"
            "-# If you ever forget it, mention me or use `/help`."
        ))

    # --- moderator roles ---------------------------------------------------------------------
    @commands.hybrid_group(name="modrole", fallback="list", description="Show the server's moderator roles")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @server_admin()
    async def modrole(self, ctx: commands.Context) -> None:
        roles = await self.bot.staffroles.mod_roles(ctx.guild.id)
        body = "\n".join(f"• <@&{r}>" for r in roles) if roles else "None set. Anyone with the right Discord permission can moderate."
        note = ("\n\nModerator roles **narrow** access: a member needs the Discord permission for a command **and** one of these roles. "
                "Administrators and the server owner are never restricted.")
        await ctx.send(embed=info_embed("Moderator roles", body + note))

    @modrole.command(name="add", description="Limit moderation to members holding this role")
    @app_commands.describe(role="The moderator role")
    @server_admin()
    async def modrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        if role.is_default() or role.managed:
            raise InvalidInput("Pick a normal role (not @everyone or a bot role).")
        roles = await self.bot.staffroles.mod_roles(ctx.guild.id)
        if role.id in roles:
            raise InvalidInput(f"{role.mention} is already a moderator role.")
        if len(roles) >= 10:
            raise InvalidInput("You can have up to 10 moderator roles.")
        await self.bot.staffroles.set_mod_roles(ctx.guild.id, [*roles, role.id])
        await ctx.send(embed=ok_embed(f"{role.mention} is now a moderator role. Members will need it (plus the Discord permission) to use moderation commands."))

    @modrole.command(name="remove", description="Stop treating this role as a moderator role")
    @app_commands.describe(role="The moderator role to remove")
    @server_admin()
    async def modrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        roles = await self.bot.staffroles.mod_roles(ctx.guild.id)
        if role.id not in roles:
            raise InvalidInput(f"{role.mention} isn't a moderator role.")
        remaining = [r for r in roles if r != role.id]
        await self.bot.staffroles.set_mod_roles(ctx.guild.id, remaining)
        extra = "" if remaining else "\nNo moderator roles are left, so anyone with the right Discord permission can moderate again."
        await ctx.send(embed=ok_embed(f"{role.mention} is no longer a moderator role." + extra))


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Moderation(bot))

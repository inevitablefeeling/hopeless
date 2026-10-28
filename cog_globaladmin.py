"""Global administration: the bot owner, global staff, global bans and bot-wide tools.

These are bot-wide powers and are completely separate from server permissions. A server owner or a server
Administrator gets nothing here. Everything sensitive is checked inside the command and written to the audit log.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import core_authz as authz
from core_checks import facts_for, global_owner, global_perm, global_staff
from core_command_registry import registry
from core_embeds import BRAND, ERROR, NEUTRAL, SUCCESS, ok_embed, system_embed
from core_errors import InvalidInput
from core_formatting import clean_inline
from core_global_logic import GLOBAL_PERMS
from core_moderation_checks import parse_duration, short_duration
from core_permission_matrix import rule_for
from core_permissions import GlobalRole
from core_ui_v2 import send_card
from view_paginator import paginate, send_pages

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.globaladmin")
SYSTEM = "Global Administration"


class GlobalAdmin(commands.Cog, name="Global administration"):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    async def _name(self, user_id: int) -> str:
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                return "unknown user"
        return discord.utils.escape_markdown(user.name)

    # --- global bans -------------------------------------------------------------------------
    @commands.hybrid_command(name="gban", description="Globally ban a user from HOPELESS (not from any server)")
    @app_commands.describe(user="Who to ban", duration="Optional: 7d, 12h... (empty = permanent)", reason="Why")
    @global_perm("gban")
    async def gban(self, ctx: commands.Context, user: discord.User, duration: Optional[str] = None, *, reason: Optional[str] = None) -> None:
        seconds = None
        if duration:
            try:
                seconds = parse_duration(duration)
            except InvalidInput:  # not a duration: it's the start of the reason
                reason = f"{duration} {reason or ''}".strip()
        case = await self.bot.globals.ban(actor_id=ctx.author.id, target_id=user.id, reason=reason or "No reason provided",
                                          seconds=seconds, bot_id=self.bot.user.id)
        embed = system_embed(SYSTEM, "GLOBAL BAN ISSUED", f"{user.mention} can no longer use HOPELESS. They were **not** banned from any server.", color=ERROR)
        embed.add_field(name="Case", value=f"#{case}")
        embed.add_field(name="Length", value=short_duration(seconds) if seconds else "permanent")
        embed.add_field(name="Reason", value=clean_inline(reason or "No reason provided", 300), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="gunban", description="Lift a global ban")
    @app_commands.describe(user="Who to unban", reason="Why")
    @global_perm("gban")
    async def gunban(self, ctx: commands.Context, user: discord.User, *, reason: Optional[str] = None) -> None:
        case = await self.bot.globals.unban(actor_id=ctx.author.id, target_id=user.id, reason=reason or "No reason provided")
        await ctx.send(embed=system_embed(SYSTEM, "GLOBAL BAN LIFTED", f"{user.mention} can use HOPELESS again (case #{case}).", color=SUCCESS))

    @commands.hybrid_command(name="gbancheck", description="Check whether a user is globally banned")
    @global_perm("gban")
    async def gbancheck(self, ctx: commands.Context, user: discord.User) -> None:
        entry = self.bot.globals.ban_for(user.id)
        if entry is None:
            await ctx.send(embed=system_embed(SYSTEM, "NOT GLOBALLY BANNED", f"{user.mention} has no active global ban.", color=SUCCESS))
            return
        case, expires, reason = entry
        embed = system_embed(SYSTEM, "GLOBALLY BANNED", f"{user.mention} is banned from HOPELESS.", color=ERROR)
        embed.add_field(name="Case", value=f"#{case}")
        embed.add_field(name="Expires", value=f"<t:{int(expires.timestamp())}:R>" if expires else "never")
        embed.add_field(name="Reason", value=clean_inline(reason, 300), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="gbanlist", description="List active global bans")
    @global_perm("gban")
    async def gbanlist(self, ctx: commands.Context) -> None:
        rows = await self.bot.globals.get_cases(active_only=True)
        lines = [f"**#{r['case_id']}** <@{r['user_id']}> (`{r['user_id']}`)\n└ {clean_inline(r['reason'], 80)}" for r in rows]
        await send_pages(ctx, paginate(f"Global bans ({len(rows)})", lines, color=ERROR, empty="Nobody is globally banned."), SYSTEM)

    @commands.hybrid_command(name="gbaninfo", description="Full details and history for a user or case number")
    @app_commands.describe(target="A user, or a case number like 12")
    @global_perm("gban")
    async def gbaninfo(self, ctx: commands.Context, target: str) -> None:
        text = target.strip().strip("<@!>").lstrip("#")
        if not text.isdigit():
            raise InvalidInput("Give a user or a case number.")
        number = int(text)
        rows = await self.bot.globals.get_cases(case_id=number) if number < 10_000_000 else []
        rows = rows or await self.bot.globals.get_cases(user_id=number)
        if not rows:
            raise InvalidInput("No global ban records found for that.")
        lines = []
        for r in rows:
            state = "active" if r["active"] else f"lifted by <@{r['lifted_by']}>" if r["lifted_by"] else "expired"
            until = f"expires <t:{int(r['expires_at'].timestamp())}:R>" if r["expires_at"] else "permanent"
            lines.append(f"**Case #{r['case_id']}** for <@{r['user_id']}>: {state}\nIssued by <@{r['issued_by']}> <t:{int(r['created_at'].timestamp())}:R>, {until}\nReason: {clean_inline(r['reason'], 120)}")
        await send_pages(ctx, paginate("Global ban details", lines, per_page=3, color=ERROR), SYSTEM)

    # --- global staff ------------------------------------------------------------------------
    @commands.hybrid_command(name="stafflist", description="List the bot owner and global staff")
    @global_staff()
    async def stafflist(self, ctx: commands.Context) -> None:
        owner = self.bot.settings.bot_owner_id
        sections = [f"**OWNER**\n1. {await self._name(owner)} (`{owner}`)"]
        for role, label in ((GlobalRole.ADMINISTRATOR, "ADMIN"), (GlobalRole.MODERATOR, "MOD")):
            members = self.bot.permissions.list_staff(role)
            lines = [f"{i}. {await self._name(uid)} (`{uid}`)" for i, (uid, _) in enumerate(members, 1)] or ["nobody"]
            sections.append(f"**{label}**\n" + "\n".join(lines))
        await send_card(ctx, title="Global staff list", description="\n\n".join(sections), color=BRAND, footer=SYSTEM)

    @commands.hybrid_group(name="gperm", fallback="list", description="Show global staff permissions")
    @app_commands.describe(user="Whose permissions to show (empty = everyone)")
    @global_owner()
    async def gperm(self, ctx: commands.Context, user: Optional[discord.User] = None) -> None:
        catalogue = "\n".join(f"`{name}`: {text}" for name, text in GLOBAL_PERMS.items())
        staff = [(user.id, self.bot.permissions.global_role(user.id))] if user else [(u, r) for u, r in self.bot.permissions.list_staff()]
        lines = [f"<@{uid}> ({role.value if role else 'not staff'}): " + (", ".join(f"`{p}`" for p in sorted(self.bot.globals.perms_of(uid))) or "none") for uid, role in staff]
        await ctx.send(embed=system_embed(SYSTEM, "Global permissions", ("\n".join(lines) or "No global staff yet.") + f"\n\n**Available**\n{catalogue}"))

    @gperm.command(name="add", description="Grant a global permission to global staff")
    @global_owner()
    async def gperm_add(self, ctx: commands.Context, user: discord.User, permission: str) -> None:
        perms = await self.bot.globals.change_perm(ctx.author.id, user.id, permission, True)
        await ctx.send(embed=ok_embed(f"{user.mention} now has: " + ", ".join(f"`{p}`" for p in sorted(perms))))

    @gperm.command(name="remove", description="Take a global permission away")
    @global_owner()
    async def gperm_remove(self, ctx: commands.Context, user: discord.User, permission: str) -> None:
        perms = await self.bot.globals.change_perm(ctx.author.id, user.id, permission, False)
        await ctx.send(embed=ok_embed(f"{user.mention} now has: " + (", ".join(f"`{p}`" for p in sorted(perms)) or "no extra permissions")))

    @commands.hybrid_command(name="helpadm", description="Commands you can use as global staff")
    @global_staff()
    async def helpadm(self, ctx: commands.Context) -> None:
        facts = facts_for(self.bot, ctx.author, ctx.guild)
        lines = []
        for spec in registry.commands("owner", implemented_only=True):
            if spec.name and authz.allowed(rule_for(spec).level, facts, rule_for(spec).needs):
                lines.append(f"`{ctx.clean_prefix}{spec.name}` — {spec.description}")
        await send_pages(ctx, paginate("Bot Admin commands", lines, per_page=10, color=BRAND, empty="No global commands are available to you."), SYSTEM)

    # --- bot-wide tools ----------------------------------------------------------------------
    @commands.hybrid_command(name="serverlist", description="Servers the bot is in")
    @global_perm("servers")
    async def serverlist(self, ctx: commands.Context) -> None:
        guilds = sorted(self.bot.guilds, key=lambda g: -(g.member_count or 0))
        lines = [f"**{discord.utils.escape_markdown(g.name)}**\n└ `{g.id}` • {g.member_count or 0:,} members" for g in guilds]
        await self.bot.globals.audit(ctx.author.id, "serverlist", None, f"{len(guilds)} servers")
        await send_pages(ctx, paginate(f"Servers ({len(guilds)})", lines, color=BRAND), SYSTEM, ephemeral=True)

    @commands.hybrid_command(name="generatelink", description="Create a one-use invite for a server by ID (sent privately)")
    @app_commands.describe(server_id="The server's ID")
    @global_perm("invite")
    async def generatelink(self, ctx: commands.Context, server_id: str) -> None:
        guild = self.bot.get_guild(int(server_id)) if server_id.strip().isdigit() else None
        if guild is None:
            raise InvalidInput("I'm not in a server with that ID.")
        channel = next((c for c in guild.text_channels if c.permissions_for(guild.me).create_instant_invite), None)
        if channel is None:
            raise InvalidInput("I can't create invites in any channel of that server.")
        invite = await channel.create_invite(max_age=300, max_uses=1, unique=True, reason=f"global staff invite by {ctx.author}")
        await self.bot.globals.audit(ctx.author.id, "generatelink", guild.id, guild.name[:80])
        embed = system_embed(SYSTEM, "Invite created", f"{invite.url}\nOne use, expires in 5 minutes.", color=SUCCESS)
        if ctx.interaction is not None:
            await ctx.send(embed=embed, ephemeral=True)
            return
        try:
            await ctx.author.send(embed=embed)
            await ctx.send(embed=ok_embed("I sent the invite to your DMs."), delete_after=8)
        except discord.HTTPException:
            raise InvalidInput("I can't DM you, and I won't post an invite link in a channel. Use the slash command instead.")

    @commands.hybrid_command(name="gaudit", description="The global administration log")
    @global_perm("audit")
    async def gaudit(self, ctx: commands.Context) -> None:
        rows = await self.bot.globals.recent_audit()
        lines = [f"<t:{int(r['created_at'].timestamp())}:R> **{r['action']}** by <@{r['actor_id']}>" + (f" on `{r['target_id']}`" if r["target_id"] else "")
                 + (f"\n└ {clean_inline(r['detail'], 90)}" if r["detail"] else "") for r in rows]
        await send_pages(ctx, paginate("Global audit log", lines, color=NEUTRAL, empty="Nothing logged yet."), SYSTEM, ephemeral=True)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(GlobalAdmin(bot))

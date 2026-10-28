"""Utility module: information commands, snipes, AFK status and statistics."""
from __future__ import annotations

import logging
import math
import platform
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import core_branding as branding
from core_command_registry import registry
from core_checks import moderator
from core_embeds import BRAND, SILVER, SUCCESS, error_embed, info_embed, ok_embed
from core_emoji import ICONS
from core_formatting import format_duration, long_duration, truncate
from core_invite import invite_url
from core_ui_v2 import send_card
from svc_log_service import plain
from svc_snipe_service import SnipedMessage

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.utility")

_KEY_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels",
    "kick_members", "ban_members", "moderate_members", "manage_messages",
)


def _asset_links(asset: discord.Asset) -> str:
    formats = ["png", "jpg", "webp"] + (["gif"] if asset.is_animated() else [])
    return " • ".join(f"[{fmt}]({asset.replace(size=1024, format=fmt).url})" for fmt in formats)


def _link_view(label: str, url: str) -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=label, url=url))
    return view


class Utility(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    def _prefix(self, ctx: commands.Context) -> str:
        return self.bot.prefixes.get(ctx.guild.id if ctx.guild else None)

    # --- listeners: snipes and AFK -----------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        attachments = tuple(a.filename for a in message.attachments)
        if not message.content and not attachments:
            return
        self.bot.snipes.record_delete(
            message.channel.id,
            SnipedMessage(
                author_id=message.author.id,
                author_name=message.author.display_name,
                content=message.content,
                attachment_names=attachments,
                created_ts=message.created_at.timestamp(),
            ),
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        self.bot.snipes.record_edit(
            before.channel.id,
            SnipedMessage(
                author_id=before.author.id,
                author_name=before.author.display_name,
                content=before.content,
                after_content=after.content,
                created_ts=before.created_at.timestamp(),
            ),
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:  # bots and webhooks never count
            return
        afk = self.bot.afk
        guild_id = message.guild.id

        record = afk.get(guild_id, message.author.id)
        if record is not None and afk.should_auto_clear(record):
            await afk.clear(guild_id, message.author.id)
            try:
                await send_card(message, reply=True, mention_author=False, delete_after=20, **self._afk_cleared(message.author, record))
            except discord.HTTPException:
                pass

        away_list: list[tuple[str, str]] = []
        for user in message.mentions[:5]:
            if user.id == message.author.id or user.bot:
                continue
            target = afk.get(guild_id, user.id)
            if target is not None and afk.notice_allowed(message.channel.id, user.id):
                away = (discord.utils.utcnow() - target.since).total_seconds()
                away_list.append((discord.utils.escape_markdown(user.display_name),
                                  f"{plain(target.reason, 150)}\naway for {long_duration(away)} • since {discord.utils.format_dt(target.since, 'R')}"))
        if away_list:
            try:
                await send_card(message, reply=True, mention_author=False, delete_after=30,
                                title="USER IS AFK" if len(away_list) == 1 else "THESE MEMBERS ARE AFK",
                                description="They'll see your message when they're back." if len(away_list) == 1 else "",
                                color=SILVER, footer="Presence System", fields=away_list)
            except discord.HTTPException:
                pass

    @staticmethod
    def _afk_cleared(member: discord.abc.User, record) -> dict:
        away = (discord.utils.utcnow() - record.since).total_seconds()
        return dict(title="AFK STATUS CLEARED", description=f"Welcome back, {member.mention}.\nYour AFK status has been removed.", color=SUCCESS,
                    footer="Presence System", fields=[("Previous reason", plain(record.reason, 200)), ("Duration", long_duration(away))])

    # --- afk ---------------------------------------------------------------------------------
    @commands.hybrid_command(name="afk", description="Set your AFK status. Talk again to clear it.")
    @app_commands.describe(reason="Why you're away (or 'off' to clear it)")
    @commands.guild_only()
    async def afk(self, ctx: commands.Context, *, reason: Optional[str] = None) -> None:
        if reason and reason.strip().lower() in {"off", "clear", "back"}:
            cleared = await self.bot.afk.clear(ctx.guild.id, ctx.author.id)
            if cleared:
                await send_card(ctx, ephemeral=True, delete_after=20, **self._afk_cleared(ctx.author, cleared))
            else:
                await ctx.send(embed=info_embed(None, "You weren't AFK."), ephemeral=True, delete_after=10)
            return
        record = await self.bot.afk.set(ctx.guild.id, ctx.author.id, reason)
        await send_card(ctx, ephemeral=True, delete_after=15, title="AFK STATUS SET", description=f"{ctx.author.mention} is now away.", color=SILVER,
                        footer="Presence System", fields=[("Reason", plain(record.reason, 200)), ("Since", discord.utils.format_dt(record.since, "R")),
                                                          ("Clears", "when you next send a message")])

    # --- avatar / banner ---------------------------------------------------------------------
    @commands.hybrid_command(name="avatar", aliases=["av"], description="Show a user's avatar")
    @app_commands.describe(user="Whose avatar to show (default: you)")
    async def avatar(self, ctx: commands.Context, user: Optional[discord.User] = None) -> None:
        user = user or ctx.author
        member = ctx.guild.get_member(user.id) if ctx.guild else None
        asset = (member or user).display_avatar
        embed = info_embed(f"{user.display_name}'s avatar", _asset_links(asset))
        embed.set_image(url=asset.with_size(1024).url)
        if member is not None and member.guild_avatar is not None:
            embed.set_footer(text="Showing their server avatar")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="banner", description="Show a user's profile banner")
    @app_commands.describe(user="Whose banner to show (default: you)")
    async def banner(self, ctx: commands.Context, user: Optional[discord.User] = None) -> None:
        user = user or ctx.author
        full = await self.bot.fetch_user(user.id)  # banners are only included when fetched from the API
        if full.banner is None:
            await ctx.send(embed=error_embed(f"{user.display_name} has no profile banner."), ephemeral=True)
            return
        embed = info_embed(f"{user.display_name}'s banner", _asset_links(full.banner))
        embed.set_image(url=full.banner.with_size(1024).url)
        await ctx.send(embed=embed)

    # --- bot information ---------------------------------------------------------------------
    async def _runtime_card(self, title: str, description: str = "") -> dict:
        bot = self.bot
        latency = f"{round(bot.latency * 1000)} ms" if math.isfinite(bot.latency) else "n/a"
        db_seconds = await bot.db.ping()
        database = f"online ({round(db_seconds * 1000)} ms)" if db_seconds is not None else "unreachable"
        members = sum(guild.member_count or 0 for guild in bot.guilds)
        prefix_count = len({c.qualified_name for c in bot.walk_commands()})
        return dict(
            title=f"{ICONS.get('gear')} {title}", description=description, color=BRAND, footer="Statistics",
            thumbnail=bot.user.display_avatar.url if bot.user is not None else None,
            fields=[("Servers", f"{len(bot.guilds):,}"), ("Users", f"~{members:,} (approximate: adds up every server's member count)"), ("Latency", latency),
                    ("Uptime", format_duration(bot.uptime)), ("Commands", f"{prefix_count} prefix commands • {len(bot.tree.get_commands())} slash groups"),
                    ("Database", database), ("Python", platform.python_version()), ("discord.py", discord.__version__), ("Version", branding.BOT_VERSION)],
        )

    @commands.hybrid_command(name="botinfo", description="Show bot version, latency and uptime")
    async def botinfo(self, ctx: commands.Context) -> None:
        await send_card(ctx, **await self._runtime_card(f"{branding.BOT_NAME} {branding.BOT_VERSION}", branding.BOT_DESCRIPTION))

    @commands.hybrid_command(name="botinvite", description="Get the bot invite link")
    async def botinvite(self, ctx: commands.Context) -> None:
        url = invite_url(self.bot.user.id)
        await ctx.send(
            embed=info_embed(
                f"Invite {branding.BOT_NAME}",
                "The link asks only for the permissions the modules need. It never asks for Administrator.",
            ),
            view=_link_view(f"Invite {branding.BOT_NAME}", url),
        )

    @commands.hybrid_command(name="ping", aliases=["latency"], description="Show gateway and database latency")
    async def ping(self, ctx: commands.Context) -> None:
        gateway = f"{round(self.bot.latency * 1000)} ms" if math.isfinite(self.bot.latency) else "n/a"
        db_seconds = await self.bot.db.ping()
        database = f"{round(db_seconds * 1000)} ms" if db_seconds is not None else "unreachable"
        embed = info_embed("Pong", "")
        embed.add_field(name="Gateway", value=gateway)
        embed.add_field(name="Database", value=database)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="uptime", description="Show how long the bot has been running")
    async def uptime(self, ctx: commands.Context) -> None:
        since = discord.utils.format_dt(self.bot.started_at, "R")
        await ctx.send(embed=info_embed(None, f"Up for **{format_duration(self.bot.uptime)}** (started {since})."))

    @commands.hybrid_command(name="stats", description="Show usage statistics")
    async def stats(self, ctx: commands.Context) -> None:
        summary = await self.bot.stats.summary()
        card = await self._runtime_card(f"{branding.BOT_NAME} Stats")
        card["fields"].insert(6, ("Commands run", f"{summary.total_uses:,}"))
        if summary.top_commands:
            card["fields"].insert(7, ("Most used", "\n".join(f"`{name}`: {uses:,}" for name, uses in summary.top_commands)))
        await send_card(ctx, **card)

    @commands.hybrid_command(name="support", description="Get help or join the support server")
    async def support(self, ctx: commands.Context) -> None:
        url = self.bot.settings.support_url
        if not url:
            await ctx.send(embed=info_embed(None, "No support server has been configured for this bot yet."))
            return
        await ctx.send(embed=info_embed("Support", "Questions or problems? Join the support server."), view=_link_view("Support server", url))

    @commands.hybrid_command(name="tutorial", description="Learn how to set the bot up")
    async def tutorial(self, ctx: commands.Context) -> None:
        p = self._prefix(ctx)
        text = (
            "**1.** Put the bot's role **above** the roles it should manage (and give it high permissions, or Administrator, if needed).\n\n"
            f"**2.** `{p}antinuke setup` — One-click setup: enables every anti-nuke module with a shared rate limit. Or use `{p}setup` for a guided panel.\n\n"
            f"**3.** `{p}setuplogs` — Create the private log channels for the server.\n\n"
            f"**4.** `{p}alertrole add @role` — Choose who gets pinged when something urgent happens.\n\n"
            f"**5.** `{p}punishment strip|ban|kick|jail` — Choose what happens when anti-nuke triggers.\n\n"
            f"**6.** `{p}whitelist` / `{p}wl` — Whitelist trusted users or roles so they bypass anti-nuke.\n\n"
            f"**7.** `{p}jailrole @role` — Set a jail role if you use jail as punishment or moderation.\n\n"
            f"**8.** Optional: `{p}setwelcome`, tickets (`{p}psetup`), voice (`{p}jtc`), automod toggles (`{p}antilink`, `{p}antispam`, etc.).\n\n"
            f"**9.** Need help? `{p}help` or `{p}support`."
        )
        await ctx.send(embed=discord.Embed(title="Tutorial", description=text, color=0x2B2D31))

    # --- snipes ------------------------------------------------------------------------------
    @staticmethod
    def _snipe_embed(item: SnipedMessage, *, edited: bool) -> discord.Embed:
        embed = discord.Embed(color=branding.ACCENT_COLOR)
        embed.set_author(name=item.author_name)
        if item.created_ts:
            embed.timestamp = datetime.fromtimestamp(item.created_ts, tz=timezone.utc)
        if edited:
            embed.add_field(name="Before", value=truncate(item.content or "(empty)", 1000), inline=False)
            embed.add_field(name="After", value=truncate(item.after_content or "(empty)", 1000), inline=False)
        else:
            embed.description = truncate(item.content or "(no text)", 3500)
        if item.attachment_names:
            embed.add_field(name="Attachments", value=truncate(", ".join(item.attachment_names), 1000), inline=False)
        embed.set_footer(text="Kept in memory for a limited time")
        return embed

    @commands.hybrid_command(name="snipe", description="Show the last deleted message in this channel")
    @app_commands.describe(index="1 is the most recent (up to 10)")
    @commands.guild_only()
    @moderator("manage_messages")
    async def snipe(self, ctx: commands.Context, index: commands.Range[int, 1, 10] = 1) -> None:
        item = self.bot.snipes.get_deleted(ctx.channel.id, index)
        if item is None:
            await ctx.send(embed=info_embed(None, "Nothing to snipe here."), ephemeral=True)
            return
        await ctx.send(embed=self._snipe_embed(item, edited=False))

    @commands.hybrid_command(name="editsnipe", description="Show the last edited message in this channel")
    @app_commands.describe(index="1 is the most recent (up to 10)")
    @commands.guild_only()
    @moderator("manage_messages")
    async def editsnipe(self, ctx: commands.Context, index: commands.Range[int, 1, 10] = 1) -> None:
        item = self.bot.snipes.get_edited(ctx.channel.id, index)
        if item is None:
            await ctx.send(embed=info_embed(None, "No edited messages to show here."), ephemeral=True)
            return
        await ctx.send(embed=self._snipe_embed(item, edited=True))

    @commands.hybrid_command(name="clearsnipe", description="Clear stored snipes for this channel")
    @app_commands.default_permissions(manage_messages=True)
    @commands.guild_only()
    @moderator("manage_messages")
    async def clearsnipe(self, ctx: commands.Context) -> None:
        count = self.bot.snipes.clear(ctx.channel.id)
        await ctx.send(embed=ok_embed(f"Cleared {count} stored message{'s' if count != 1 else ''}."))

    # --- server / user information -----------------------------------------------------------
    @commands.hybrid_command(name="serverinfo", aliases=["si"], description="Show information about this server")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots = len(guild.members) - humans
        approx = "" if guild.chunked else " (approximate: not every member is loaded)"
        await send_card(
            ctx, title=discord.utils.escape_markdown(guild.name), description=guild.description or "", color=BRAND, footer="Server Information",
            thumbnail=guild.icon.url if guild.icon is not None else None,
            fields=[("Owner", f"<@{guild.owner_id}>"),
                    ("Members", f"{guild.member_count or len(guild.members):,} total • {humans:,} humans • {bots:,} bots{approx}"),
                    ("Created", f"{discord.utils.format_dt(guild.created_at, 'D')} ({discord.utils.format_dt(guild.created_at, 'R')})"),
                    ("Channels", f"{len(guild.text_channels)} text • {len(guild.voice_channels)} voice • {len(guild.categories)} categories"),
                    ("Roles", str(max(len(guild.roles) - 1, 0))), ("Boosts", f"Level {guild.premium_tier} • {guild.premium_subscription_count} boosts"),
                    ("Verification", guild.verification_level.name.replace("_", " ").title()), ("Server ID", f"`{guild.id}`")])

    @commands.hybrid_command(name="userinfo", aliases=["ui", "whois"], description="Show information about a user")
    @app_commands.describe(user="Who to look up (default: you)")
    async def userinfo(self, ctx: commands.Context, user: Optional[discord.User] = None) -> None:
        user = user or ctx.author
        member = ctx.guild.get_member(user.id) if ctx.guild else None
        embed = discord.Embed(title=str(user), color=branding.ACCENT_COLOR)
        embed.set_thumbnail(url=(member or user).display_avatar.url)
        embed.add_field(name="ID", value=str(user.id))
        embed.add_field(name="Account created", value=discord.utils.format_dt(user.created_at, "F"))
        if user.bot:
            embed.add_field(name="Type", value="Bot")
        if member is not None:
            if member.joined_at is not None:
                embed.add_field(name="Joined this server", value=discord.utils.format_dt(member.joined_at, "F"))
            if member.premium_since is not None:
                embed.add_field(name="Boosting since", value=discord.utils.format_dt(member.premium_since, "R"))
            roles = [role.mention for role in reversed(member.roles[1:])]
            shown = roles[:15]
            extra = len(roles) - len(shown)
            embed.add_field(
                name=f"Roles ({len(roles)})",
                value=(" ".join(shown) + (f" and {extra} more" if extra else "")) or "None",
                inline=False,
            )
            perms = [name.replace("_", " ").title() for name in _KEY_PERMISSIONS if getattr(member.guild_permissions, name)]
            if perms:
                embed.add_field(name="Key permissions", value=", ".join(perms), inline=False)
        badges = [name.replace("_", " ").title() for name, enabled in user.public_flags if enabled]
        if badges:
            embed.add_field(name="Badges", value=", ".join(badges), inline=False)
        await ctx.send(embed=embed)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Utility(bot))

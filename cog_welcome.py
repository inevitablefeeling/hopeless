"""Welcome and leave messages, fully customizable and configured separately for every server."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core_checks import can_manage_server
from core_embeds import BRAND, info_embed, ok_embed
from core_emoji import ICONS
from core_errors import InvalidInput
from core_moderation_checks import PLACEHOLDERS, render_template, unknown_placeholders
from core_ui_logic import parse_color, valid_https_url

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.welcome")
DEFAULT_WELCOME = "🎉 Welcome to **{server}**, {user}!\n\n{bullet} **Username:** {user}\n{bullet} **Member Count:** #{membercount}"
DEFAULT_LEAVE = "**{username}** has left {server}."
COLUMNS = {
    "enabled", "channel_id", "message", "dm_enabled", "dm_message", "leave_enabled", "leave_channel_id", "leave_message",
    "image_enabled", "image_url", "use_embed", "title", "color", "thumbnail", "footer", "rules_text", "leave_title",
}
TOGGLES = {"welcome": "enabled", "leave": "leave_enabled", "dm": "dm_enabled"}


def validate_message(text: str, *, limit: int = 1500) -> str:
    text = (text or "").strip()
    if not text:
        raise InvalidInput("The message can't be empty.")
    if len(text) > limit:
        raise InvalidInput(f"Keep it under {limit} characters.")
    unknown = [u for u in unknown_placeholders(text) if u != "bullet"]
    if unknown:
        raise InvalidInput("Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in unknown)
                           + ". Valid ones: " + ", ".join("{" + p + "}" for p in PLACEHOLDERS) + ".")
    return text


class Welcome(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not can_manage_server(ctx.bot, ctx.author):
            raise commands.MissingPermissions(["manage_guild"])
        return True

    # --- storage -----------------------------------------------------------------------------
    async def _get(self, guild_id: int) -> dict:
        row = await self.bot.db.fetchrow("SELECT * FROM welcome_settings WHERE guild_id = $1", guild_id)
        return dict(row) if row else {}

    async def _set(self, guild_id: int, **fields) -> None:
        assert set(fields) <= COLUMNS
        names = list(fields)
        sets = ", ".join(f"{n} = ${i + 2}" for i, n in enumerate(names))
        marks = ", ".join(f"${i + 2}" for i in range(len(names)))
        async with self.bot.db.transaction() as conn:
            await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            await conn.execute(f"INSERT INTO welcome_settings (guild_id, {', '.join(names)}) VALUES ($1, {marks}) "
                               f"ON CONFLICT (guild_id) DO UPDATE SET {sets}", guild_id, *fields.values())

    # --- rendering ---------------------------------------------------------------------------
    @staticmethod
    def _values(member: discord.abc.User, guild: discord.Guild) -> dict[str, str]:
        return {"user": member.mention, "username": discord.utils.escape_markdown(member.name),
                "server": discord.utils.escape_markdown(guild.name), "membercount": str(guild.member_count or 0),
                "bullet": ICONS.get("bullet")}

    def build(self, s: dict, member: discord.abc.User, guild: discord.Guild, *, leave: bool = False) -> discord.Embed:
        values = self._values(member, guild)
        text = (s.get("leave_message") or DEFAULT_LEAVE) if leave else (s.get("message") or DEFAULT_WELCOME)
        title = (s.get("leave_title") or "Member left") if leave else (s.get("title") or "New Member!")
        embed = discord.Embed(title=render_template(title, values)[:250], description=render_template(text, values)[:3500],
                              color=s["color"] if s.get("color") is not None else BRAND)
        if s.get("rules_text") and not leave:
            embed.add_field(name="Rules", value=render_template(s["rules_text"], values)[:1000], inline=False)
        thumb = s.get("thumbnail") or "member"
        if thumb == "member":
            embed.set_thumbnail(url=member.display_avatar.url)
        elif thumb == "server" and guild.icon is not None:
            embed.set_thumbnail(url=guild.icon.url)
        elif thumb.startswith("https://"):
            embed.set_thumbnail(url=thumb)
        if s.get("image_enabled") and s.get("image_url") and not leave:
            embed.set_image(url=s["image_url"])
        embed.set_footer(text=render_template(s["footer"], values)[:200] if s.get("footer") else f"{guild.name} • {guild.member_count or 0} members")
        return embed

    # --- commands ----------------------------------------------------------------------------
    @commands.hybrid_group(name="setwelcome", fallback="show", description="Show welcome settings, or set the welcome channel")
    @app_commands.describe(channel="Set the welcome channel (leave empty to just show settings)")
    async def setwelcome(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        if channel is not None:
            await self._set(ctx.guild.id, channel_id=channel.id)
            await ctx.send(embed=ok_embed(f"Welcome messages will be posted in {channel.mention} once turned on (`setwelcome toggle welcome`)."))
            return
        s = await self._get(ctx.guild.id)
        state = lambda flag: "on" if s.get(flag) else "off"  # noqa: E731
        chan = lambda key: f"<#{s[key]}>" if s.get(key) else "not set"  # noqa: E731
        text = (f"**Welcome** {state('enabled')} in {chan('channel_id')}  •  style: {'embed' if s.get('use_embed', True) else 'plain text'}\n"
                f"**Title:** {s.get('title') or 'New Member!'}\n**Message:** `{(s.get('message') or DEFAULT_WELCOME)[:200]}`\n"
                f"**Colour:** {('#%06X' % s['color']) if s.get('color') is not None else 'default'} • **Thumbnail:** {s.get('thumbnail') or 'member'} • "
                f"**Image:** {state('image_enabled')}\n**Footer:** {s.get('footer') or 'server name and member count'}\n**Rules:** {'set' if s.get('rules_text') else 'none'}\n\n"
                f"**Direct message** {state('dm_enabled')}\n**Leave** {state('leave_enabled')} in {chan('leave_channel_id')}")
        await ctx.send(embed=info_embed("Welcome settings", text))

    @setwelcome.command(name="message", description="Set the welcome message")
    @app_commands.describe(text="Use {user} {username} {server} {membercount}")
    async def sw_message(self, ctx: commands.Context, *, text: str) -> None:
        await self._set(ctx.guild.id, message=validate_message(text))
        await ctx.send(embed=ok_embed("Welcome message saved. Preview it with `setwelcome test`."))

    @setwelcome.command(name="title", description="Set the welcome embed title")
    async def sw_title(self, ctx: commands.Context, *, text: str) -> None:
        await self._set(ctx.guild.id, title=validate_message(text, limit=200))
        await ctx.send(embed=ok_embed("Welcome title saved."))

    @setwelcome.command(name="leavetitle", description="Set the leave embed title")
    async def sw_leavetitle(self, ctx: commands.Context, *, text: str) -> None:
        await self._set(ctx.guild.id, leave_title=validate_message(text, limit=200))
        await ctx.send(embed=ok_embed("Leave title saved."))

    @setwelcome.command(name="color", description="Set the embed colour (#9B111E or a name like red)")
    async def sw_color(self, ctx: commands.Context, value: str) -> None:
        color = parse_color(value)
        await self._set(ctx.guild.id, color=color)
        await ctx.send(embed=ok_embed("Colour saved." if color is not None else "Using the default colour."))

    @setwelcome.command(name="footer", description="Set the embed footer (or 'reset')")
    async def sw_footer(self, ctx: commands.Context, *, text: str) -> None:
        await self._set(ctx.guild.id, footer=None if text.strip().lower() == "reset" else validate_message(text, limit=200))
        await ctx.send(embed=ok_embed("Footer saved."))

    @setwelcome.command(name="thumbnail", description="Thumbnail: member, server, none, or an https image link")
    async def sw_thumbnail(self, ctx: commands.Context, value: str) -> None:
        value = value.strip()
        if value.lower() in ("member", "server", "none"):
            await self._set(ctx.guild.id, thumbnail=value.lower())
        else:
            await self._set(ctx.guild.id, thumbnail=valid_https_url(value, what="thumbnail link") or "none")
        await ctx.send(embed=ok_embed("Thumbnail saved."))

    @setwelcome.command(name="rules", description="Add a rules section to the welcome embed (or 'reset')")
    async def sw_rules(self, ctx: commands.Context, *, text: str) -> None:
        await self._set(ctx.guild.id, rules_text=None if text.strip().lower() == "reset" else validate_message(text, limit=1000))
        await ctx.send(embed=ok_embed("Rules section saved."))

    @setwelcome.command(name="style", description="embed (default) or plain text")
    async def sw_style(self, ctx: commands.Context, style: str) -> None:
        style = style.strip().lower()
        if style not in ("embed", "text"):
            raise InvalidInput("Choose `embed` or `text`.")
        await self._set(ctx.guild.id, use_embed=style == "embed")
        await ctx.send(embed=ok_embed(f"Welcome messages will use the {style} style."))

    @setwelcome.command(name="dm", description="Set the welcome direct message")
    @app_commands.describe(text="Use {user} {username} {server} {membercount}")
    async def sw_dm(self, ctx: commands.Context, *, text: str) -> None:
        await self._set(ctx.guild.id, dm_message=validate_message(text))
        await ctx.send(embed=ok_embed("Welcome DM saved. Turn it on with `setwelcome toggle dm`."))

    @setwelcome.command(name="leave", description="Set the leave message")
    @app_commands.describe(text="Use {user} {username} {server} {membercount}")
    async def sw_leave(self, ctx: commands.Context, *, text: str) -> None:
        await self._set(ctx.guild.id, leave_message=validate_message(text))
        await ctx.send(embed=ok_embed("Leave message saved."))

    @setwelcome.command(name="leavechannel", description="Set the channel for leave messages")
    async def sw_leavechannel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self._set(ctx.guild.id, leave_channel_id=channel.id)
        await ctx.send(embed=ok_embed(f"Leave messages will go to {channel.mention} once turned on."))

    @setwelcome.command(name="placeholders", description="List the placeholders you can use")
    async def sw_placeholders(self, ctx: commands.Context) -> None:
        text = "`{user}` mentions the member\n`{username}` their name\n`{server}` the server name\n`{membercount}` the member count"
        await ctx.send(embed=info_embed("Placeholders", text))

    @setwelcome.command(name="toggle", description="Turn welcome, leave or dm messages on or off")
    @app_commands.describe(kind="welcome, leave or dm")
    async def sw_toggle(self, ctx: commands.Context, kind: str) -> None:
        column = TOGGLES.get(kind.lower())
        if column is None:
            raise InvalidInput("Choose `welcome`, `leave` or `dm`.")
        s = await self._get(ctx.guild.id)
        new = not s.get(column, False)
        if new and column == "enabled" and not s.get("channel_id"):
            raise InvalidInput("Set a welcome channel first: `setwelcome #channel`.")
        if new and column == "leave_enabled" and not s.get("leave_channel_id"):
            raise InvalidInput("Set a leave channel first: `setwelcome leavechannel #channel`.")
        if new and column == "dm_enabled" and not s.get("dm_message"):
            raise InvalidInput("Set a DM first: `setwelcome dm <text>`.")
        await self._set(ctx.guild.id, **{column: new})
        await ctx.send(embed=ok_embed(f"{kind.lower()} messages are now {'on' if new else 'off'}."))

    @setwelcome.command(name="reset", description="Reset every welcome and leave setting")
    async def sw_reset(self, ctx: commands.Context) -> None:
        await self.bot.db.execute("DELETE FROM welcome_settings WHERE guild_id = $1", ctx.guild.id)
        await ctx.send(embed=ok_embed("Welcome settings reset."))

    @setwelcome.command(name="test", description="Preview the welcome and leave messages (only you see it)")
    async def sw_test(self, ctx: commands.Context) -> None:
        s = await self._get(ctx.guild.id)
        await ctx.send(content="**Preview.** This is what new members and leavers will see:",
                       embeds=[self.build(s, ctx.author, ctx.guild), self.build(s, ctx.author, ctx.guild, leave=True)], ephemeral=True)

    @commands.hybrid_group(name="welcomeimage", fallback="show", description="Show welcome image settings")
    async def welcomeimage(self, ctx: commands.Context) -> None:
        s = await self._get(ctx.guild.id)
        await ctx.send(embed=info_embed("Welcome image", f"{'On' if s.get('image_enabled') else 'Off'}\n{s.get('image_url') or 'No image set.'}"))

    @welcomeimage.command(name="on", description="Show the image with welcome messages")
    async def wi_on(self, ctx: commands.Context) -> None:
        s = await self._get(ctx.guild.id)
        if not s.get("image_url"):
            raise InvalidInput("Set an image first: `welcomeimage set <https link>`.")
        await self._set(ctx.guild.id, image_enabled=True)
        await ctx.send(embed=ok_embed("Welcome image is on."))

    @welcomeimage.command(name="off", description="Stop showing the welcome image")
    async def wi_off(self, ctx: commands.Context) -> None:
        await self._set(ctx.guild.id, image_enabled=False)
        await ctx.send(embed=ok_embed("Welcome image is off."))

    @welcomeimage.command(name="set", description="Set the welcome banner from an https link")
    @app_commands.describe(url="Direct link to a png, jpg, gif or webp image")
    async def wi_set(self, ctx: commands.Context, url: str) -> None:
        await self._set(ctx.guild.id, image_url=valid_https_url(url, what="image link"))
        await ctx.send(embed=ok_embed("Welcome banner saved. Turn it on with `welcomeimage on`."))

    # --- events ------------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            s = await self._get(member.guild.id)
        except Exception:
            log.warning("could not load welcome settings", exc_info=True)
            return
        if s.get("enabled") and s.get("channel_id"):
            channel = member.guild.get_channel(s["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    if s.get("use_embed", True):
                        await channel.send(embed=self.build(s, member, member.guild), allowed_mentions=discord.AllowedMentions.none())
                    else:
                        text = render_template(s.get("message") or DEFAULT_WELCOME, self._values(member, member.guild))
                        await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))
                except discord.HTTPException:
                    log.warning("welcome message failed in guild %s", member.guild.id)
        if s.get("dm_enabled") and s.get("dm_message") and not member.bot:
            try:
                await member.send(render_template(s["dm_message"], {**self._values(member, member.guild), "user": discord.utils.escape_markdown(member.display_name)}))
            except discord.HTTPException:
                pass  # DMs closed

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        try:
            s = await self._get(member.guild.id)
        except Exception:
            return
        if s.get("leave_enabled") and s.get("leave_channel_id"):
            channel = member.guild.get_channel(s["leave_channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    if s.get("use_embed", True):
                        await channel.send(embed=self.build(s, member, member.guild, leave=True), allowed_mentions=discord.AllowedMentions.none())
                    else:
                        await channel.send(render_template(s.get("leave_message") or DEFAULT_LEAVE, self._values(member, member.guild)), allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    log.warning("leave message failed in guild %s", member.guild.id)


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Welcome(bot))

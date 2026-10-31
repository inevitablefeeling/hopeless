"""Giveaways and polls.

Both are Components V2 messages (a container with text and buttons); if Discord or the installed discord.py refuses
that layout, the same content is sent as a classic embed with the same buttons. The buttons are DynamicItems whose
custom_id carries the giveaway or poll id, so they keep working after a restart without any stored view state.
Active giveaways and polls live in PostgreSQL and a small loop ends whatever is due, including anything that came due
while the bot was offline.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core_branding as branding
from core_checks import moderator
from core_eligibility import check_eligibility
from core_embeds import BRAND, ERROR, SILVER, SUCCESS, error_embed, ok_embed
from core_errors import InvalidInput
from core_giveaway_logic import pick_winners, validate_prize, validate_winners
from core_moderation_checks import parse_duration
from core_poll_logic import bar, leaders, parse_options, percent, tally, validate_question, vote_outcome
from svc_log_service import plain
from core_ui_v2 import send_note, v2_available

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.engage")
MAX_DURATION = 30 * 24 * 3600
MIN_DURATION = 10
NO_MENTIONS = discord.AllowedMentions.none()
TRACKING_NOTE = "Counting starts from when I began tracking messages."


def _ts(dt: datetime, style: str = "R") -> str:
    return discord.utils.format_dt(dt, style)


def _requirements(role_id: Optional[int], min_messages: int) -> str:
    parts = []
    if role_id:
        parts.append(f"Role: <@&{role_id}>")
    if min_messages:
        parts.append(f"At least {min_messages} messages")
    return " • ".join(parts) or "None"


# =============================================================================================
# Buttons (persistent through restarts)
# =============================================================================================
class GiveawayEnter(discord.ui.DynamicItem[discord.ui.Button], template=r"hopeless:gw:enter:(?P<id>[0-9]+)"):
    def __init__(self, gid: int, *, disabled: bool = False) -> None:
        super().__init__(discord.ui.Button(style=discord.ButtonStyle.danger, label="Enter giveaway", custom_id=f"hopeless:gw:enter:{gid}", disabled=disabled))
        self.gid = gid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match) -> "GiveawayEnter":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Engage")
        await cog.enter_giveaway(interaction, self.gid)


class PollVote(discord.ui.DynamicItem[discord.ui.Button], template=r"hopeless:poll:vote:(?P<pid>[0-9]+):(?P<opt>[0-9]+)"):
    def __init__(self, pid: int, index: int, label: str = "", *, disabled: bool = False) -> None:
        super().__init__(discord.ui.Button(style=discord.ButtonStyle.secondary, label=(label or f"Option {index + 1}")[:80],
                                           custom_id=f"hopeless:poll:vote:{pid}:{index}", disabled=disabled))
        self.pid, self.index = pid, index

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match) -> "PollVote":
        return cls(int(match["pid"]), int(match["opt"]), item.label or "")

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Engage")
        await cog.vote_poll(interaction, self.pid, self.index)


# =============================================================================================
# Flags for the commands (prefix: `prize: Nitro duration: 1h`; slash: separate options)
# =============================================================================================
class GiveawayFlags(commands.FlagConverter):
    prize: str = commands.flag(description="What is being given away")
    duration: str = commands.flag(description="How long it runs, like 30m, 2h or 7d")
    winners: int = commands.flag(default=1, description="How many winners (1 to 20)")
    role: Optional[discord.Role] = commands.flag(default=None, description="Only members with this role may enter")
    messages: int = commands.flag(default=0, description="Minimum messages needed (counted from when I started tracking)")
    channel: Optional[discord.TextChannel] = commands.flag(default=None, description="Where to post it (default: here)")


class PollFlags(commands.FlagConverter):
    question: str = commands.flag(description="The question")
    options: str = commands.flag(description="Choices separated with |, like: Pizza | Tacos | Sushi")
    duration: Optional[str] = commands.flag(default=None, description="Optional: close automatically after 30m, 2h, 1d")
    role: Optional[discord.Role] = commands.flag(default=None, description="Only members with this role may vote")
    messages: int = commands.flag(default=0, description="Minimum messages needed to vote")
    change: bool = commands.flag(default=False, description="Allow people to change their vote")
    channel: Optional[discord.TextChannel] = commands.flag(default=None, description="Where to post it (default: here)")


class Engage(commands.Cog, name="Engage"):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self._refresh: dict[tuple[str, int], asyncio.Task] = {}
        self._tasks: set = set()

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(GiveawayEnter, PollVote)
        self.sweeper.start()

    async def cog_unload(self) -> None:
        self.sweeper.cancel()

    # =========================================================================================
    # Message building (V2 first, classic embed as the fallback)
    # =========================================================================================
    @staticmethod
    def _giveaway_text(row, entries: int) -> str:
        ends = row["ends_at"]
        lines = [f"**Prize:** {plain(row['prize'], 200)}", f"**Hosted by:** <@{row['host_id']}>",
                 f"**Winners:** {row['winner_count']}", f"**Entries:** {entries}",
                 f"**Requirements:** {_requirements(row['required_role_id'], row['min_messages'])}"]
        lines.append(f"**Ended:** {_ts(ends)}" if row["ended"] else f"**Ends:** {_ts(ends)} ({_ts(ends, 'f')})")
        if row["ended"]:
            winners = row["winner_ids"]
            lines.append("**Winners:** " + (", ".join(f"<@{w}>" for w in winners) if winners else "nobody entered or qualified"))
        return "\n".join(lines)

    def giveaway_layout(self, row, entries: int):
        view = discord.ui.LayoutView(timeout=None)
        box = discord.ui.Container(accent_colour=discord.Colour(SILVER if row["ended"] else BRAND))
        box.add_item(discord.ui.TextDisplay(f"## {'Giveaway ended' if row['ended'] else 'Giveaway'}"))
        box.add_item(discord.ui.TextDisplay(self._giveaway_text(row, entries)))
        if not row["ended"]:
            box.add_item(discord.ui.Separator())
            actions = discord.ui.ActionRow()
            actions.add_item(GiveawayEnter(row["id"]))
            box.add_item(actions)
        box.add_item(discord.ui.Separator(visible=False))
        box.add_item(discord.ui.TextDisplay(f"-# {branding.BOT_NAME} • giveaway #{row['id']}"))
        view.add_item(box)
        return view

    def giveaway_classic(self, row, entries: int):
        embed = discord.Embed(title="Giveaway ended" if row["ended"] else "Giveaway", description=self._giveaway_text(row, entries),
                              color=SILVER if row["ended"] else BRAND)
        embed.set_footer(text=f"{branding.BOT_NAME} • giveaway #{row['id']}")
        view = discord.ui.View(timeout=None)
        if not row["ended"]:
            view.add_item(GiveawayEnter(row["id"]))
        return embed, view

    @staticmethod
    def _poll_text(row, counts: list[int]) -> str:
        total = sum(counts)
        lines = [f"**{plain(row['question'], 200)}**", ""]
        for index, option in enumerate(row["options"]):
            lines.append(f"**{index + 1}.** {plain(option, 80)}\n`{bar(counts[index], total)}` {percent(counts[index], total)}% ({counts[index]})")
        lines.append("")
        lines.append(f"**Votes:** {total} • **Requirements:** {_requirements(row['required_role_id'], row['min_messages'])}")
        lines.append("Votes can be changed." if row["allow_change"] else "Votes are final.")
        if row["closed"]:
            top = leaders(counts)
            lines.append("**Result:** " + ("no votes" if not top else (f"tie between " + ", ".join(plain(row['options'][i], 40) for i in top)) if len(top) > 1
                                           else f"**{plain(row['options'][top[0]], 80)}** wins"))
        elif row["ends_at"]:
            lines.append(f"**Closes:** {_ts(row['ends_at'])}")
        return "\n".join(lines)

    def poll_layout(self, row, counts: list[int]):
        view = discord.ui.LayoutView(timeout=None)
        box = discord.ui.Container(accent_colour=discord.Colour(SILVER if row["closed"] else BRAND))
        box.add_item(discord.ui.TextDisplay(f"## {'Poll closed' if row['closed'] else 'Poll'}"))
        box.add_item(discord.ui.TextDisplay(self._poll_text(row, counts)))
        if not row["closed"]:
            box.add_item(discord.ui.Separator())
            options = row["options"]
            for start in range(0, len(options), 5):
                actions = discord.ui.ActionRow()
                for index in range(start, min(start + 5, len(options))):
                    actions.add_item(PollVote(row["id"], index, f"{index + 1}. {options[index][:30]}"))
                box.add_item(actions)
        box.add_item(discord.ui.Separator(visible=False))
        box.add_item(discord.ui.TextDisplay(f"-# {branding.BOT_NAME} • poll #{row['id']}"))
        view.add_item(box)
        return view

    def poll_classic(self, row, counts: list[int]):
        embed = discord.Embed(title="Poll closed" if row["closed"] else "Poll", description=self._poll_text(row, counts), color=SILVER if row["closed"] else BRAND)
        embed.set_footer(text=f"{branding.BOT_NAME} • poll #{row['id']}")
        view = discord.ui.View(timeout=None)
        if not row["closed"]:
            for index, option in enumerate(row["options"]):
                view.add_item(PollVote(row["id"], index, f"{index + 1}. {option[:30]}"))
        return embed, view

    async def _send(self, channel: discord.abc.Messageable, layout, classic):
        """Send V2 if possible, else the classic embed. Returns (message, used_v2)."""
        if v2_available():
            try:
                return await channel.send(view=layout(), allowed_mentions=NO_MENTIONS), True
            except (AttributeError, TypeError, ValueError, discord.HTTPException) as exc:
                if isinstance(exc, discord.Forbidden):
                    raise
                log.warning("V2 message failed (%s); sending the classic version", exc)
        embed, view = classic()
        return await channel.send(embed=embed, view=view, allowed_mentions=NO_MENTIONS), False

    async def _edit(self, message: discord.Message, v2: bool, layout, classic) -> None:
        try:
            if v2:
                await message.edit(view=layout())
            else:
                embed, view = classic()
                await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            log.warning("could not update message %s", message.id, exc_info=True)

    async def _fetch_message(self, guild_id: int, channel_id: int, message_id: Optional[int]) -> Optional[discord.Message]:
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if channel is None or message_id is None:
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException:
            return None

    # =========================================================================================
    # Giveaways
    # =========================================================================================
    async def _giveaway_entries(self, gid: int) -> int:
        return int(await self.bot.db.fetchval("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = $1", gid) or 0)

    async def _refresh_giveaway(self, gid: int) -> None:
        row = await self.bot.db.fetchrow("SELECT * FROM giveaways WHERE id = $1", gid)
        if row is None:
            return
        message = await self._fetch_message(row["guild_id"], row["channel_id"], row["message_id"])
        if message is None:
            return
        entries = await self._giveaway_entries(gid)
        await self._edit(message, row["v2"], lambda: self.giveaway_layout(row, entries), lambda: self.giveaway_classic(row, entries))

    def _debounce(self, kind: str, ident: int, coro_factory) -> None:
        """Update a message at most every few seconds however many people click."""
        key = (kind, ident)
        if key in self._refresh and not self._refresh[key].done():
            return

        async def run() -> None:
            await asyncio.sleep(6)
            await coro_factory()

        task = asyncio.create_task(run())
        self._refresh[key] = task
        task.add_done_callback(lambda t: self._refresh.pop(key, None))

    async def enter_giveaway(self, interaction: discord.Interaction, gid: int) -> None:
        if (ban := self.bot.globals.ban_message(interaction.user.id)):
            await interaction.response.send_message(embed=error_embed(ban, title="Permission denied"), ephemeral=True)
            return
        row = await self.bot.db.fetchrow("SELECT * FROM giveaways WHERE id = $1", gid)
        if row is None or row["ended"] or row["ends_at"] <= datetime.now(timezone.utc):
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if member is None or interaction.guild.id != row["guild_id"]:
            await interaction.response.send_message("You can only enter from inside the server.", ephemeral=True)
            return
        reason = check_eligibility(role_ids=[r.id for r in member.roles], required_role_id=row["required_role_id"],
                                   messages=await self.bot.activity.messages(row["guild_id"], member.id), min_messages=row["min_messages"], tracking_note=TRACKING_NOTE)
        if reason:
            await interaction.response.send_message(embed=error_embed(reason, title="You can't enter yet"), ephemeral=True)
            return
        removed = await self.bot.db.fetchval("DELETE FROM giveaway_entries WHERE giveaway_id = $1 AND user_id = $2 RETURNING 1", gid, member.id)
        if removed:
            text = "You left the giveaway. Press the button again to re-enter."
        else:
            await self.bot.db.execute("INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", gid, member.id)
            text = "You're in. Good luck!"
        await interaction.response.send_message(text, ephemeral=True)
        self._debounce("gw", gid, lambda: self._refresh_giveaway(gid))

    async def _draw(self, row, *, exclude=(), count: Optional[int] = None) -> list[int]:
        """Pick winners from entrants who are still in the server and still meet the requirements right now."""
        guild = self.bot.get_guild(row["guild_id"])
        if guild is None:
            return []
        entrants = [r["user_id"] for r in await self.bot.db.fetch("SELECT user_id FROM giveaway_entries WHERE giveaway_id = $1", row["id"])]
        eligible = []
        for user_id in entrants:
            member = guild.get_member(user_id)
            if member is None or member.bot:
                continue
            if check_eligibility(role_ids=[r.id for r in member.roles], required_role_id=row["required_role_id"],
                                 messages=await self.bot.activity.messages(guild.id, user_id), min_messages=row["min_messages"]) is None:
                eligible.append(user_id)
        return pick_winners(eligible, count or row["winner_count"], exclude=exclude)

    async def end_giveaway(self, gid: int, *, announce: bool = True):
        """End a giveaway exactly once (the UPDATE is the lock), draw, update the message and announce the result."""
        row = await self.bot.db.fetchrow("UPDATE giveaways SET ended = TRUE WHERE id = $1 AND NOT ended RETURNING *", gid)
        if row is None:
            return None
        winners = await self._draw(row)
        row = await self.bot.db.fetchrow("UPDATE giveaways SET winner_ids = $2 WHERE id = $1 RETURNING *", gid, winners)
        message = await self._fetch_message(row["guild_id"], row["channel_id"], row["message_id"])
        entries = await self._giveaway_entries(gid)
        if message is not None:
            await self._edit(message, row["v2"], lambda: self.giveaway_layout(row, entries), lambda: self.giveaway_classic(row, entries))
            if announce:
                text = (f"Congratulations {', '.join(f'<@{w}>' for w in winners)}! You won **{plain(row['prize'], 150)}**." if winners
                        else f"No one qualified for **{plain(row['prize'], 150)}**.")
                try:
                    await message.reply(text, allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=w) for w in winners]), mention_author=False)
                except discord.HTTPException:
                    log.warning("could not announce giveaway %s", gid)
        return row

    @commands.hybrid_group(name="giveaway", fallback="list", invoke_without_command=True, description="Show the active giveaways in this server")
    @commands.guild_only()
    async def giveaway(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.fetch("SELECT * FROM giveaways WHERE guild_id = $1 AND NOT ended ORDER BY ends_at LIMIT 15", ctx.guild.id)
        if not rows:
            await send_note(ctx, "No active giveaways. Start one with `giveaway start prize: <prize> duration: 1h`.")
            return
        lines = [f"**#{r['id']}** {plain(r['prize'], 60)} • ends {_ts(r['ends_at'])} • <#{r['channel_id']}>" for r in rows]
        await send_note(ctx, "**Active giveaways**\n" + "\n".join(lines))

    @giveaway.command(name="start", description="Start a giveaway")
    @commands.has_permissions(manage_guild=True)
    @moderator("manage_guild")
    async def giveaway_start(self, ctx: commands.Context, *, flags: GiveawayFlags) -> None:
        prize, winners = validate_prize(flags.prize), validate_winners(flags.winners)
        seconds = parse_duration(flags.duration)
        if not MIN_DURATION <= seconds <= MAX_DURATION:
            raise InvalidInput("A giveaway can run from 10 seconds to 30 days.")
        if flags.messages < 0 or flags.messages > 100_000:
            raise InvalidInput("Minimum messages must be between 0 and 100000.")
        role = flags.role if flags.role and not flags.role.is_default() else None
        channel = flags.channel or ctx.channel
        if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(ctx.guild.me).send_messages:
            raise InvalidInput("I can't send messages in that channel.")
        ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await self.bot.db.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", ctx.guild.id)
        row = await self.bot.db.fetchrow(
            "INSERT INTO giveaways (guild_id, channel_id, host_id, prize, winner_count, ends_at, required_role_id, min_messages) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *", ctx.guild.id, channel.id, ctx.author.id, prize, winners, ends_at, role.id if role else None, flags.messages)
        try:
            message, v2 = await self._send(channel, lambda: self.giveaway_layout(row, 0), lambda: self.giveaway_classic(row, 0))
        except discord.HTTPException:
            await self.bot.db.execute("DELETE FROM giveaways WHERE id = $1", row["id"])
            raise InvalidInput("I couldn't post the giveaway there. Check my permissions in that channel.")
        await self.bot.db.execute("UPDATE giveaways SET message_id = $2, v2 = $3 WHERE id = $1", row["id"], message.id, v2)
        await send_note(ctx, f"{branding.WHITECHECK} Giveaway **#{row['id']}** started in {channel.mention}. It ends {_ts(ends_at)}.", color=SUCCESS)

    @giveaway.command(name="end", description="End a giveaway now and pick the winners")
    @app_commands.describe(giveaway_id="The giveaway number, like 3")
    @commands.has_permissions(manage_guild=True)
    @moderator("manage_guild")
    async def giveaway_end(self, ctx: commands.Context, giveaway_id: int) -> None:
        row = await self.bot.db.fetchrow("SELECT guild_id, ended FROM giveaways WHERE id = $1", giveaway_id)
        if row is None or row["guild_id"] != ctx.guild.id:  # another server's giveaway is never reachable
            raise InvalidInput("I couldn't find that giveaway in this server.")
        if row["ended"]:
            raise InvalidInput("That giveaway has already ended. Use `giveaway reroll` to pick new winners.")
        await self.end_giveaway(giveaway_id)
        await send_note(ctx, f"{branding.WHITECHECK} Giveaway **#{giveaway_id}** ended.", color=SUCCESS)

    @giveaway.command(name="reroll", description="Pick new winners for an ended giveaway")
    @app_commands.describe(giveaway_id="The giveaway number", count="How many new winners (default 1)")
    @commands.has_permissions(manage_guild=True)
    @moderator("manage_guild")
    async def giveaway_reroll(self, ctx: commands.Context, giveaway_id: int, count: int = 1) -> None:
        row = await self.bot.db.fetchrow("SELECT * FROM giveaways WHERE id = $1", giveaway_id)
        if row is None or row["guild_id"] != ctx.guild.id:
            raise InvalidInput("I couldn't find that giveaway in this server.")
        if not row["ended"]:
            raise InvalidInput("That giveaway hasn't ended yet. Use `giveaway end` first.")
        new = await self._draw(row, exclude=row["winner_ids"], count=validate_winners(count))
        if not new:
            raise InvalidInput("There's nobody else eligible to pick.")
        await self.bot.db.execute("UPDATE giveaways SET winner_ids = winner_ids || $2::bigint[] WHERE id = $1", giveaway_id, new)
        await ctx.send(f"{branding.WHITECHECK} New winner{'s' if len(new) > 1 else ''} for **{plain(row['prize'], 150)}**: {', '.join(f'<@{w}>' for w in new)}",
                       allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=w) for w in new]))
        await self._refresh_giveaway(giveaway_id)

    # =========================================================================================
    # Polls
    # =========================================================================================
    async def _poll_counts(self, row) -> list[int]:
        votes = [r["option_index"] for r in await self.bot.db.fetch("SELECT option_index FROM poll_votes WHERE poll_id = $1", row["id"])]
        return tally(votes, len(row["options"]))

    async def _refresh_poll(self, pid: int) -> None:
        row = await self.bot.db.fetchrow("SELECT * FROM polls WHERE id = $1", pid)
        if row is None:
            return
        message = await self._fetch_message(row["guild_id"], row["channel_id"], row["message_id"])
        if message is None:
            return
        counts = await self._poll_counts(row)
        await self._edit(message, row["v2"], lambda: self.poll_layout(row, counts), lambda: self.poll_classic(row, counts))

    async def vote_poll(self, interaction: discord.Interaction, pid: int, index: int) -> None:
        if (ban := self.bot.globals.ban_message(interaction.user.id)):
            await interaction.response.send_message(embed=error_embed(ban, title="Permission denied"), ephemeral=True)
            return
        row = await self.bot.db.fetchrow("SELECT * FROM polls WHERE id = $1", pid)
        if row is None or row["closed"] or (row["ends_at"] and row["ends_at"] <= datetime.now(timezone.utc)):
            await interaction.response.send_message("This poll is closed.", ephemeral=True)
            return
        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if member is None or interaction.guild.id != row["guild_id"] or not 0 <= index < len(row["options"]):
            await interaction.response.send_message("You can only vote from inside the server.", ephemeral=True)
            return
        reason = check_eligibility(role_ids=[r.id for r in member.roles], required_role_id=row["required_role_id"],
                                   messages=await self.bot.activity.messages(row["guild_id"], member.id), min_messages=row["min_messages"], tracking_note=TRACKING_NOTE)
        if reason:
            await interaction.response.send_message(embed=error_embed(reason, title="You can't vote yet"), ephemeral=True)
            return
        previous = await self.bot.db.fetchval("SELECT option_index FROM poll_votes WHERE poll_id = $1 AND user_id = $2", pid, member.id)
        outcome = vote_outcome(previous, index, row["allow_change"])
        name = plain(row["options"][index], 80)
        if outcome == "locked":
            await interaction.response.send_message(f"You already voted for **{plain(row['options'][previous], 80)}**. This poll doesn't allow changing votes.", ephemeral=True)
            return
        if outcome == "same":
            await interaction.response.send_message(f"You already voted for **{name}**.", ephemeral=True)
            return
        await self.bot.db.execute("INSERT INTO poll_votes (poll_id, user_id, option_index) VALUES ($1,$2,$3) "
                                  "ON CONFLICT (poll_id, user_id) DO UPDATE SET option_index = EXCLUDED.option_index, voted_at = now()", pid, member.id, index)
        await interaction.response.send_message(f"Vote {'changed to' if outcome == 'changed' else 'recorded:'} **{name}**.", ephemeral=True)
        self._debounce("poll", pid, lambda: self._refresh_poll(pid))

    async def close_poll(self, pid: int):
        row = await self.bot.db.fetchrow("UPDATE polls SET closed = TRUE WHERE id = $1 AND NOT closed RETURNING *", pid)
        if row is None:
            return None
        message = await self._fetch_message(row["guild_id"], row["channel_id"], row["message_id"])
        if message is not None:
            counts = await self._poll_counts(row)
            await self._edit(message, row["v2"], lambda: self.poll_layout(row, counts), lambda: self.poll_classic(row, counts))
        return row

    @commands.hybrid_group(name="poll", fallback="list", invoke_without_command=True, description="Show the open polls in this server")
    @commands.guild_only()
    async def poll(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.fetch("SELECT * FROM polls WHERE guild_id = $1 AND NOT closed ORDER BY id DESC LIMIT 15", ctx.guild.id)
        if not rows:
            await send_note(ctx, "No open polls. Start one with `poll create question: <question> options: A | B | C`.")
            return
        await send_note(ctx, "**Open polls**\n" + "\n".join(f"**#{r['id']}** {plain(r['question'], 70)} • <#{r['channel_id']}>" for r in rows))

    @poll.command(name="create", description="Create a poll")
    @commands.has_permissions(manage_messages=True)
    @moderator("manage_messages")
    async def poll_create(self, ctx: commands.Context, *, flags: PollFlags) -> None:
        question, options = validate_question(flags.question), parse_options(flags.options)
        ends_at = None
        if flags.duration:
            seconds = parse_duration(flags.duration)
            if not MIN_DURATION <= seconds <= MAX_DURATION:
                raise InvalidInput("A poll can run from 10 seconds to 30 days.")
            ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        if flags.messages < 0 or flags.messages > 100_000:
            raise InvalidInput("Minimum messages must be between 0 and 100000.")
        role = flags.role if flags.role and not flags.role.is_default() else None
        channel = flags.channel or ctx.channel
        if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(ctx.guild.me).send_messages:
            raise InvalidInput("I can't send messages in that channel.")
        await self.bot.db.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", ctx.guild.id)
        row = await self.bot.db.fetchrow(
            "INSERT INTO polls (guild_id, channel_id, creator_id, question, options, ends_at, required_role_id, min_messages, allow_change) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *", ctx.guild.id, channel.id, ctx.author.id, question, options, ends_at,
            role.id if role else None, flags.messages, flags.change)
        counts = [0] * len(options)
        try:
            message, v2 = await self._send(channel, lambda: self.poll_layout(row, counts), lambda: self.poll_classic(row, counts))
        except discord.HTTPException:
            await self.bot.db.execute("DELETE FROM polls WHERE id = $1", row["id"])
            raise InvalidInput("I couldn't post the poll there. Check my permissions in that channel.")
        await self.bot.db.execute("UPDATE polls SET message_id = $2, v2 = $3 WHERE id = $1", row["id"], message.id, v2)
        await send_note(ctx, f"{branding.WHITECHECK} Poll **#{row['id']}** posted in {channel.mention}.", color=SUCCESS)

    @poll.command(name="close", description="Close a poll now and show the result")
    @app_commands.describe(poll_id="The poll number, like 4")
    @commands.has_permissions(manage_messages=True)
    @moderator("manage_messages")
    async def poll_close(self, ctx: commands.Context, poll_id: int) -> None:
        row = await self.bot.db.fetchrow("SELECT guild_id, closed FROM polls WHERE id = $1", poll_id)
        if row is None or row["guild_id"] != ctx.guild.id:
            raise InvalidInput("I couldn't find that poll in this server.")
        if row["closed"]:
            raise InvalidInput("That poll is already closed.")
        await self.close_poll(poll_id)
        await send_note(ctx, f"{branding.WHITECHECK} Poll **#{poll_id}** closed.", color=SUCCESS)

    @poll.command(name="results", description="Show a poll's current results")
    @app_commands.describe(poll_id="The poll number")
    async def poll_results(self, ctx: commands.Context, poll_id: int) -> None:
        row = await self.bot.db.fetchrow("SELECT * FROM polls WHERE id = $1", poll_id)
        if row is None or row["guild_id"] != ctx.guild.id:
            raise InvalidInput("I couldn't find that poll in this server.")
        await send_note(ctx, self._poll_text(row, await self._poll_counts(row)), color=SILVER if row["closed"] else BRAND)

    # =========================================================================================
    # Ending what is due (also catches anything that came due while the bot was offline)
    # =========================================================================================
    @tasks.loop(seconds=15)
    async def sweeper(self) -> None:
        try:
            for row in await self.bot.db.fetch("SELECT id FROM giveaways WHERE NOT ended AND ends_at <= now() LIMIT 20"):
                await self.end_giveaway(row["id"])
            for row in await self.bot.db.fetch("SELECT id FROM polls WHERE NOT closed AND ends_at IS NOT NULL AND ends_at <= now() LIMIT 20"):
                await self.close_poll(row["id"])
        except Exception:
            log.warning("the giveaway and poll sweep failed", exc_info=True)

    @sweeper.before_loop
    async def _before_sweeper(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Engage(bot))

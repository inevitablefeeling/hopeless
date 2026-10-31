"""Voice module: join-to-create rooms plus the control panel.

The panel mirrors the reference layout: Name, Limit, Privacy, Waiting Room, Chat, Trust, Untrust, Invite,
Kick, Region, Block, Unblock, Claim, Transfer, Delete. Buttons act on the room the clicker is sitting in,
so one panel message serves everyone, survives restarts, and every action re-checks ownership.

Waiting Room: Discord has no native waiting rooms, so this builds one honestly - the room is locked and a
holding voice channel is created next to it. People who join the holding channel are announced in the
room's chat; Invite (or Trust) lets them in and moves them across.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core_checks import can_manage_server
from core_embeds import NEUTRAL, error_embed, info_embed, ok_embed
from core_emoji import ICONS
from core_errors import InvalidInput
from core_naming import room_name
from view_confirm import ask

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.voice")
_ENSURE = "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING"
S, D, N = discord.ButtonStyle.success, discord.ButtonStyle.danger, discord.ButtonStyle.secondary
BUTTONS = [
    ("name", "Name", N, 0), ("limit", "Limit", N, 0), ("privacy", "Privacy", N, 0), ("waiting", "Waiting Room", N, 0), ("chat", "Chat", N, 0),
    ("trust", "Trust", S, 1), ("untrust", "Untrust", D, 1), ("invite", "Invite", N, 1), ("kick", "Kick", D, 1), ("region", "Region", N, 1),
    ("block", "Block", D, 2), ("unblock", "Unblock", S, 2), ("claim", "Claim", S, 2), ("transfer", "Transfer", N, 2), ("delete", "Delete", D, 2),
]
MEMBER_ACTIONS = {"trust", "untrust", "invite", "kick", "block", "unblock", "transfer"}
# What STAFF (people who don't own the room) need from Discord. Room owners control their own room; everyone
# else needs the real permission: moving or disconnecting people needs Move Members, editing the channel
# needs Manage Channels. Having one never stands in for the other.
MANAGE = ("manage_channels",)
MOVE = ("move_members",)
BOTH = ("manage_channels", "move_members")
ACTION_NEEDS = {"kick": MOVE, "invite": BOTH, "block": BOTH, "waiting": BOTH}
MEMBER_NEEDS = {"trust": MANAGE, "untrust": MANAGE, "unblock": MANAGE, "transfer": MANAGE, "kick": MOVE, "invite": BOTH, "block": BOTH}
PERM_NAMES = {"manage_channels": "Manage Channels", "move_members": "Move Members"}
REGIONS = [("Automatic", "auto"), ("Brazil", "brazil"), ("Hong Kong", "hongkong"), ("India", "india"), ("Japan", "japan"),
           ("Rotterdam", "rotterdam"), ("Russia", "russia"), ("Singapore", "singapore"), ("South Africa", "southafrica"),
           ("Sydney", "sydney"), ("US Central", "us-central"), ("US East", "us-east"), ("US South", "us-south"), ("US West", "us-west")]


class NameModal(discord.ui.Modal, title="Rename your room"):
    value = discord.ui.TextInput(label="New name", max_length=100)

    def __init__(self, cog: "Voice", channel_id: int) -> None:
        super().__init__()
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ctx = await self.cog._authorise(interaction, self.channel_id)
        if ctx is None:
            return
        name = room_name("{user}", str(self.value)) if str(self.value).strip() else None
        if not name:
            await interaction.response.send_message(embed=error_embed("The name can't be empty."), ephemeral=True)
            return
        await ctx.edit(name=name, reason="voice room rename")
        await interaction.response.send_message(embed=ok_embed(f"Renamed to **{name}**."), ephemeral=True)


class LimitModal(discord.ui.Modal, title="Set the user limit"):
    value = discord.ui.TextInput(label="Limit (0 = unlimited, max 99)", max_length=2)

    def __init__(self, cog: "Voice", channel_id: int) -> None:
        super().__init__()
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ctx = await self.cog._authorise(interaction, self.channel_id)
        if ctx is None:
            return
        text = str(self.value).strip()
        if not text.isdigit() or int(text) > 99:
            await interaction.response.send_message(embed=error_embed("Enter a number from 0 to 99."), ephemeral=True)
            return
        await ctx.edit(user_limit=int(text), reason="voice room limit")
        await interaction.response.send_message(embed=ok_embed("No limit." if text == "0" else f"Limit set to {text}."), ephemeral=True)


class PickMember(discord.ui.View):
    def __init__(self, cog: "Voice", action: str, channel_id: int, user_id: int) -> None:
        super().__init__(timeout=60)
        self.cog, self.action, self.channel_id, self.user_id = cog, action, channel_id, user_id
        select = discord.ui.UserSelect(placeholder=f"Choose who to {action}")
        select.callback = self._picked
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _picked(self, interaction: discord.Interaction) -> None:
        member = interaction.guild.get_member(self.select.values[0].id)
        await self.cog.apply_member_action(interaction, self.action, self.channel_id, member)


class PickRegion(discord.ui.View):
    def __init__(self, cog: "Voice", channel_id: int, user_id: int) -> None:
        super().__init__(timeout=60)
        self.cog, self.channel_id, self.user_id = cog, channel_id, user_id
        select = discord.ui.Select(placeholder="Voice region", options=[discord.SelectOption(label=l, value=v) for l, v in REGIONS])
        select.callback = self._picked
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _picked(self, interaction: discord.Interaction) -> None:
        channel = await self.cog._authorise(interaction, self.channel_id)
        if channel is None:
            return
        value = self.select.values[0]
        try:
            await channel.edit(rtc_region=None if value == "auto" else value, reason="voice room region")
        except discord.HTTPException:
            await interaction.response.edit_message(content="I couldn't change the region. Try again.", view=None)
            return
        await interaction.response.edit_message(content=f"Region set to {value}.", view=None)


class VoicePanelView(discord.ui.View):
    def __init__(self, cog: "Voice") -> None:
        super().__init__(timeout=None)
        for key, label, style, row in BUTTONS:
            button = discord.ui.Button(label=label, style=style, custom_id=f"hopeless:voice:{key}", emoji="🟢", row=row)
            button.callback = self._make(key)
            self.add_item(button)
        self.cog = cog

    def _make(self, key: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.handle_button(interaction, key)
        return callback


class Voice(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self._creating: set[int] = set()
        self._last_create: dict[int, float] = {}
        self._reconciled = False

    async def cog_load(self) -> None:
        self.bot.add_view(VoicePanelView(self))

    # --- helpers -----------------------------------------------------------------------------
    async def _config(self, guild_id: int):
        return await self.bot.db.fetchrow("SELECT * FROM voice_config WHERE guild_id = $1", guild_id)

    async def _room_row(self, channel_id: int):
        return await self.bot.db.fetchrow("SELECT * FROM voice_rooms WHERE channel_id = $1", channel_id)

    async def _deny_banned(self, interaction: discord.Interaction) -> bool:
        message = self.bot.globals.ban_message(interaction.user.id)
        if message:
            await self._say(interaction, message)
        return bool(message)

    @staticmethod
    def _bot_can_move(guild: discord.Guild) -> bool:
        return guild.me.guild_permissions.move_members

    async def _authorise(self, interaction: discord.Interaction, channel_id: int, needs: tuple = MANAGE) -> Optional[discord.VoiceChannel]:
        """Re-check everything at the moment of the action: not globally banned, the room still exists, and the
        caller owns it or (as staff) holds EVERY Discord permission this action needs. Replies and returns None if not."""
        if await self._deny_banned(interaction):
            return None
        row = await self._room_row(channel_id)
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        user = interaction.user
        if row is None or channel is None:
            await self._say(interaction, "That room no longer exists.")
            return None
        if row["owner_id"] != user.id:
            missing = [PERM_NAMES[p] for p in needs if not getattr(user.guild_permissions, p)]
            if missing:
                await self._say(interaction, "Only the room owner can do that. Staff also need: " + ", ".join(missing) + ".")
                return None
            try:  # staff stepping into someone else's room is worth a log line
                await self.bot.logs.server(interaction.guild, "voice", "Staff action in a voice room", fields=[
                    ("Staff", f"{user.name} ({user.id})"), ("Room", f"{channel.name} ({channel.id})"),
                    ("Needed", ", ".join(PERM_NAMES[p] for p in needs))])
            except Exception:
                log.debug("could not log staff voice action", exc_info=True)
        return channel

    @staticmethod
    async def _say(interaction: discord.Interaction, text: str) -> None:
        embed = error_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _own_room(self, interaction: discord.Interaction, needs: tuple = MANAGE) -> Optional[discord.VoiceChannel]:
        state = interaction.user.voice
        if state is None or state.channel is None:
            await self._say(interaction, "Join your voice room first, then use the panel.")
            return None
        if await self._room_row(state.channel.id) is None:
            await self._say(interaction, "That isn't one of my temporary rooms.")
            return None
        return await self._authorise(interaction, state.channel.id, needs)

    # --- panel buttons -----------------------------------------------------------------------
    async def handle_button(self, interaction: discord.Interaction, key: str) -> None:
        if interaction.guild is None:
            return
        if await self._deny_banned(interaction):
            return
        if key == "claim":
            await self._claim(interaction)
            return
        needs = MEMBER_NEEDS[key] if key in MEMBER_ACTIONS else ACTION_NEEDS.get(key, MANAGE)
        channel = await self._own_room(interaction, needs)
        if channel is None:
            return
        everyone = interaction.guild.default_role
        row = await self._room_row(channel.id)
        if key == "name":
            await interaction.response.send_modal(NameModal(self, channel.id))
        elif key == "limit":
            await interaction.response.send_modal(LimitModal(self, channel.id))
        elif key == "privacy":
            locked = channel.overwrites_for(everyone).connect is False
            await channel.set_permissions(everyone, connect=None if locked else False, reason="voice privacy")
            await self._say_ok(interaction, "Your room is now open to everyone." if locked else "Your room is now locked. Only trusted or invited people can join.")
        elif key == "chat":
            ow = channel.overwrites_for(everyone)
            muted = ow.send_messages is False
            await channel.set_permissions(everyone, send_messages=None if muted else False, reason="voice chat toggle")
            await self._say_ok(interaction, "Text chat is on for everyone." if muted else "Text chat is limited to the owner and trusted members.")
        elif key == "waiting":
            await self._toggle_waiting(interaction, channel, row)
        elif key == "region":
            await interaction.response.send_message("Pick a region:", view=PickRegion(self, channel.id, interaction.user.id), ephemeral=True)
        elif key == "delete":
            await self._delete_room(channel, row)
            await self._say_ok(interaction, "Room deleted.")
        elif key in MEMBER_ACTIONS:
            await interaction.response.send_message(f"Who do you want to {key}?", view=PickMember(self, key, channel.id, interaction.user.id), ephemeral=True)

    @staticmethod
    async def _say_ok(interaction: discord.Interaction, text: str) -> None:
        embed = ok_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _claim(self, interaction: discord.Interaction) -> None:
        state = interaction.user.voice
        row = await self._room_row(state.channel.id) if state and state.channel else None
        if row is None:
            await self._say(interaction, "Join the temporary room you want to claim first.")
            return
        if row["owner_id"] == interaction.user.id:
            await self._say(interaction, "You already own this room.")
            return
        if any(m.id == row["owner_id"] for m in state.channel.members):
            await self._say(interaction, "The owner is still in the room. You can claim it once they leave.")
            return
        await self._set_owner(state.channel, row["owner_id"], interaction.user)
        await self._say_ok(interaction, "You now own this room.")

    async def _set_owner(self, channel: discord.VoiceChannel, old_owner_id: int, new_owner: discord.Member) -> None:
        await self.bot.db.execute("UPDATE voice_rooms SET owner_id = $2 WHERE channel_id = $1", channel.id, new_owner.id)
        await channel.set_permissions(new_owner, connect=True, view_channel=True, reason="voice room owner")
        old = channel.guild.get_member(old_owner_id)
        if old is not None and old.id != new_owner.id:
            ow = channel.overwrites_for(old)
            if ow.connect is True:
                await channel.set_permissions(old, overwrite=None, reason="voice room owner changed")

    # --- member actions ----------------------------------------------------------------------
    async def apply_member_action(self, interaction: discord.Interaction, action: str, channel_id: int, member: Optional[discord.Member]) -> None:
        channel = await self._authorise(interaction, channel_id, MEMBER_NEEDS.get(action, MANAGE))
        if channel is None:
            return
        row = await self._room_row(channel_id)

        async def finish(text: str) -> None:
            await interaction.response.edit_message(content=text, view=None)

        if member is None or member.bot:
            await finish("Pick a human member of this server.")
            return
        if action in ("invite", "kick", "block") and not self._bot_can_move(interaction.guild):
            await finish("I need the Move Members permission to do that. Ask a server admin to grant it to my role.")
            return
        if member.id == interaction.user.id and action in ("kick", "block", "untrust", "transfer"):
            await finish("You can't do that to yourself.")
            return
        if member.id == row["owner_id"] and action in ("kick", "block", "untrust"):
            await finish("You can't do that to the room's owner.")
            return
        if member.guild_permissions.administrator and action in ("kick", "block"):
            await finish("Administrators can't be kicked or blocked from rooms.")
            return
        ow = channel.overwrites_for(member)
        in_room = member.voice is not None and member.voice.channel == channel
        waiting_id = row["waiting_channel_id"]
        in_waiting = bool(waiting_id and member.voice and member.voice.channel and member.voice.channel.id == waiting_id)
        try:
            if action == "trust":
                await channel.set_permissions(member, connect=True, view_channel=True, reason="voice trust")
                await finish(f"{member.display_name} is trusted and can join even when the room is locked.")
            elif action == "untrust":
                if ow.connect is True:
                    await channel.set_permissions(member, overwrite=None, reason="voice untrust")
                await finish(f"{member.display_name} is no longer trusted.")
            elif action == "invite":
                await channel.set_permissions(member, connect=True, view_channel=True, reason="voice invite")
                if in_waiting:
                    await member.move_to(channel)
                    await finish(f"{member.display_name} was let in from the waiting room.")
                else:
                    try:
                        await member.send(f"You were invited to a voice room in **{discord.utils.escape_markdown(interaction.guild.name)}**: {channel.jump_url}")
                        await finish(f"Invited {member.display_name} (they were sent the link).")
                    except discord.HTTPException:
                        await finish(f"{member.display_name} can now join, but I couldn't DM them. Send them {channel.jump_url}")
            elif action == "kick":
                if not in_room:
                    await finish(f"{member.display_name} isn't in your room.")
                else:
                    await member.move_to(None, reason="voice kick")
                    await finish(f"Removed {member.display_name} from the room.")
            elif action == "block":
                await channel.set_permissions(member, connect=False, reason="voice block")
                if in_room:
                    await member.move_to(None, reason="voice block")
                await finish(f"{member.display_name} is blocked from this room.")
            elif action == "unblock":
                if ow.connect is False:
                    await channel.set_permissions(member, overwrite=None, reason="voice unblock")
                await finish(f"{member.display_name} is unblocked.")
            elif action == "transfer":
                if not in_room:
                    await finish("The new owner has to be in the room.")
                else:
                    await self._set_owner(channel, row["owner_id"], member)
                    await finish(f"{member.display_name} now owns the room.")
        except discord.HTTPException:
            await finish("I couldn't do that. Check that I have Manage Channels and Move Members.")

    # --- waiting room ------------------------------------------------------------------------
    async def _toggle_waiting(self, interaction: discord.Interaction, channel: discord.VoiceChannel, row) -> None:
        guild, everyone = interaction.guild, interaction.guild.default_role
        waiting = guild.get_channel(row["waiting_channel_id"]) if row["waiting_channel_id"] else None
        await interaction.response.defer(ephemeral=True)
        if waiting is None:
            waiting = await guild.create_voice_channel(f"⏳ waiting: {channel.name}"[:100], category=channel.category, reason="voice waiting room")
            await self.bot.db.execute("UPDATE voice_rooms SET waiting_channel_id = $2 WHERE channel_id = $1", channel.id, waiting.id)
            await channel.set_permissions(everyone, connect=False, reason="waiting room lock")
            await self._say_ok(interaction, "Waiting room on. Your room is locked; people who join the waiting channel are announced in your room's chat. Use Invite to let them in.")
            return
        await channel.set_permissions(everyone, connect=None, reason="waiting room off")
        if self._bot_can_move(guild):
            for member in list(waiting.members):
                if member.voice is None or member.voice.channel != waiting:  # they left while we were working
                    continue
                try:
                    await member.move_to(channel)
                except discord.HTTPException:
                    pass
        await self.bot.db.execute("UPDATE voice_rooms SET waiting_channel_id = NULL WHERE channel_id = $1", channel.id)
        await waiting.delete(reason="waiting room off")
        await self._say_ok(interaction, "Waiting room off. Your room is open again and anyone waiting was moved in.")

    # --- room lifecycle ----------------------------------------------------------------------
    async def _delete_room(self, channel: discord.VoiceChannel, row) -> None:
        waiting = channel.guild.get_channel(row["waiting_channel_id"]) if row and row["waiting_channel_id"] else None
        await self.bot.db.execute("DELETE FROM voice_rooms WHERE channel_id = $1", channel.id)
        for target in (waiting, channel):
            if target is not None:
                try:
                    await target.delete(reason="temporary voice room removed")
                except discord.HTTPException:
                    log.warning("could not delete voice channel %s", target.id)

    async def _create_room(self, member: discord.Member, cfg) -> None:
        guild = member.guild
        if member.id in self._creating or time.monotonic() - self._last_create.get(member.id, 0) < 3:
            return
        self._creating.add(member.id)
        try:
            category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
            name = room_name(cfg["name_template"], member.display_name)
            overwrites = {member: discord.PermissionOverwrite(connect=True, view_channel=True)}
            channel = await guild.create_voice_channel(name, category=category, user_limit=cfg["user_limit"], overwrites=overwrites, reason="join-to-create")
            await self.bot.db.execute("INSERT INTO voice_rooms (channel_id, guild_id, owner_id) VALUES ($1, $2, $3)", channel.id, guild.id, member.id)
            try:
                await member.move_to(channel)
            except discord.HTTPException:  # they left before we could move them
                await self._delete_room(channel, None)
            self._last_create[member.id] = time.monotonic()
        except discord.HTTPException:
            log.warning("could not create a voice room in guild %s", guild.id)
        finally:
            self._creating.discard(member.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.bot or before.channel == after.channel:
            return
        try:
            if after.channel is not None:
                cfg = await self._config(member.guild.id)
                if cfg and after.channel.id == cfg["join_channel_id"]:
                    await self._create_room(member, cfg)
                else:
                    await self._on_waiting_join(member, after.channel)
            if before.channel is not None:
                await self._on_leave(member, before.channel)
        except Exception:
            log.warning("voice state handling failed", exc_info=True)

    async def _on_waiting_join(self, member: discord.Member, channel: discord.VoiceChannel) -> None:
        row = await self.bot.db.fetchrow("SELECT * FROM voice_rooms WHERE waiting_channel_id = $1", channel.id)
        if row is None:
            return
        room = member.guild.get_channel(row["channel_id"])
        if room is None:
            return
        if room.overwrites_for(member).connect is True:  # trusted or invited: straight in
            if not self._bot_can_move(member.guild):
                log.info("can't admit %s from the waiting room in %s: I lack Move Members", member.id, member.guild.id)
                return
            if member.voice is None or member.voice.channel != channel:
                return
            try:
                await member.move_to(room)
            except discord.HTTPException:
                log.info("could not move %s out of the waiting room", member.id)
            return
        try:
            await room.send(f"{discord.utils.escape_markdown(member.display_name)} is in the waiting room. The owner can press Invite to let them in.", allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def _on_leave(self, member: discord.Member, channel: discord.VoiceChannel) -> None:
        row = await self._room_row(channel.id)
        if row is None:
            return
        humans = [m for m in channel.members if not m.bot]
        if not humans:
            await self._delete_room(channel, row)
        elif row["owner_id"] == member.id:
            await self._set_owner(channel, member.id, humans[0])

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """After a restart: forget rooms that vanished and remove empty ones."""
        if self._reconciled:
            return
        self._reconciled = True
        try:
            for row in await self.bot.db.fetch("SELECT * FROM voice_rooms"):
                guild = self.bot.get_guild(row["guild_id"])
                if guild is None:
                    continue
                channel = guild.get_channel(row["channel_id"])
                if channel is None:
                    await self.bot.db.execute("DELETE FROM voice_rooms WHERE channel_id = $1", row["channel_id"])
                elif not [m for m in channel.members if not m.bot]:
                    await self._delete_room(channel, row)
        except Exception:
            log.warning("voice reconcile failed", exc_info=True)

    # --- configuration commands --------------------------------------------------------------
    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not can_manage_server(ctx.bot, ctx.author):
            raise commands.MissingPermissions(["manage_guild"])
        return True

    async def _save_many(self, guild_id: int, **cols) -> None:
        for column, value in cols.items():
            await self._save(guild_id, column, value)

    async def _save(self, guild_id: int, column: str, value) -> None:
        assert column in {"category_id", "join_channel_id", "interface_channel_id", "user_limit", "name_template"}
        async with self.bot.db.transaction() as conn:
            await conn.execute(_ENSURE, guild_id)
            await conn.execute(
                f"INSERT INTO voice_config (guild_id, {column}) VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET {column} = EXCLUDED.{column}",
                guild_id, value,
            )

    def plan_jtc(self, guild: discord.Guild) -> list[str]:
        lines = ["create the category **Voice Channels** (or reuse the one already set up)",
                 "create the join channel **j2c**", "create the **config** channel and post the control panel in it"]
        if not guild.me.guild_permissions.manage_channels:
            lines.append("⚠ I lack Manage Channels")
        return lines

    def _panel_embed(self) -> discord.Embed:
        return discord.Embed(
            title=f"{ICONS.get('gear')} Voice",
            description=("This **interface** can be used to manage Voice channels.\n"
                         "Join the create-a-channel voice channel to get your own, then use the buttons below."),
            color=NEUTRAL,
        )

    async def ensure_voice(self, guild: discord.Guild, category: Optional[discord.CategoryChannel] = None):
        """Create whatever is missing (category, join channel, interface channel) and save it. Safe to repeat.
        Passing `category` makes that category the place rooms are created, and builds anything else that is missing in it."""
        cfg = await self._config(guild.id)
        pick = lambda key: guild.get_channel(cfg[key]) if cfg and cfg[key] else None  # noqa: E731
        category = category or pick("category_id")
        join, interface = pick("join_channel_id"), pick("interface_channel_id")
        created = []
        try:
            if category is None:
                category = await guild.create_category("Voice Channels", reason="HOPELESS voice setup")
                created.append("category")
            if join is None:
                join = await guild.create_voice_channel("j2c", category=category, reason="HOPELESS voice setup")
                created.append("join channel")
            if interface is None:
                interface = await guild.create_text_channel(
                    "config", category=category, reason="HOPELESS voice setup",
                    topic="Use the buttons to manage your own voice channel.",
                    overwrites={guild.default_role: discord.PermissionOverwrite(send_messages=False, add_reactions=False)},
                )
                await interface.send(embed=self._panel_embed(), view=VoicePanelView(self))
                created.append("interface")
        except discord.HTTPException as exc:
            raise InvalidInput("I couldn't create the channels. Give me the Manage Channels permission and try again.")
        await self._save_many(guild.id, category_id=category.id, join_channel_id=join.id, interface_channel_id=interface.id)
        return category, join, interface, created

    def _setup_summary(self, guild: discord.Guild, category, join, interface, created) -> discord.Embed:
        file_icon = ICONS.get("logging")
        made = lambda name: " (created now)" if name in created else ""  # noqa: E731
        text = (f"> configuring Voice for **{discord.utils.escape_markdown(guild.name)}**\n\n"
                f"{file_icon} **category** : {discord.utils.escape_markdown(category.name)}{made('category')}\n"
                f"{file_icon} **join channel** : {join.mention}{made('join channel')}\n"
                f"{file_icon} **interface** : {interface.mention}{made('interface')}\n\n"
                f"Join {join.mention} to get your own voice channel.")
        return discord.Embed(title=f"{ICONS.get('gear')} Voice Setup Complete", description=text, color=NEUTRAL)

    async def apply_jtc(self, guild: discord.Guild) -> list[str]:
        category, join, interface, created = await self.ensure_voice(guild)
        return [("created: " + ", ".join(created)) if created else "everything already existed",
                f"join {join.name} to get your own room; the panel is in #{interface.name}"]

    @commands.hybrid_command(name="jtc", description="One command: category, join channel and interface channel")
    async def jtc(self, ctx: commands.Context) -> None:
        category, join, interface, created = await self.ensure_voice(ctx.guild)
        await ctx.send(embed=self._setup_summary(ctx.guild, category, join, interface, created))

    @commands.hybrid_group(name="voicesetup", fallback="show", description="Show the voice settings")
    async def voicesetup(self, ctx: commands.Context) -> None:
        cfg = await self._config(ctx.guild.id)
        if not cfg:
            await ctx.send(embed=info_embed("Voice settings", "Not set up yet. Run `jtc` for a one-step setup."))
            return
        if not cfg["join_channel_id"] or not ctx.guild.get_channel(cfg["join_channel_id"]):
            await ctx.send(embed=info_embed("Voice settings", "There's no join channel yet, so nobody can create a room. "
                                            "Run `jtc`, or `voicesetup category <category>`, and I'll create it."))
            return
        text = (f"Category: {'<#%d>' % cfg['category_id'] if cfg['category_id'] else 'none'}\nJoin channel: "
                f"{'<#%d>' % cfg['join_channel_id'] if cfg['join_channel_id'] else 'none'}\nLimit: {cfg['user_limit'] or 'unlimited'}\nName: `{cfg['name_template']}`")
        await ctx.send(embed=info_embed("Voice settings", text))

    @voicesetup.command(name="category", description="Choose the category for voice rooms")
    async def vs_category(self, ctx: commands.Context, category: discord.CategoryChannel) -> None:
        # Saving only the category used to leave "Join channel: none", so nothing could ever create a room.
        # Now the join channel (and the interface channel) are built in that category if they're missing.
        category, join, interface, created = await self.ensure_voice(ctx.guild, category=category)
        await ctx.send(embed=self._setup_summary(ctx.guild, category, join, interface, created))

    @voicesetup.command(name="joinchannel", description="Choose the join-to-create channel")
    async def vs_join(self, ctx: commands.Context, channel: discord.VoiceChannel) -> None:
        await self._save(ctx.guild.id, "join_channel_id", channel.id)
        await ctx.send(embed=ok_embed(f"Joining {channel.mention} now creates a room."))

    @voicesetup.command(name="limit", description="Default user limit for new rooms (0 = unlimited)")
    async def vs_limit(self, ctx: commands.Context, number: commands.Range[int, 0, 99]) -> None:
        await self._save(ctx.guild.id, "user_limit", number)
        await ctx.send(embed=ok_embed("New rooms are unlimited." if number == 0 else f"New rooms hold up to {number} people."))

    @voicesetup.command(name="nametemplate", description="Room name template, for example {user}'s room")
    async def vs_name(self, ctx: commands.Context, *, template: str) -> None:
        if not template.strip() or len(template) > 90:
            raise InvalidInput("Give a template up to 90 characters. Use `{user}` for the member's name.")
        await self._save(ctx.guild.id, "name_template", template.strip())
        await ctx.send(embed=ok_embed(f"Room names will look like **{room_name(template.strip(), ctx.author.display_name)}**."))

    @commands.hybrid_command(name="voiceinterface", description="Post the voice room control panel in this channel")
    async def voiceinterface(self, ctx: commands.Context) -> None:
        await ctx.send(embed=self._panel_embed(), view=VoicePanelView(self))


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Voice(bot))

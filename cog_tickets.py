"""Tickets: customizable panels, private channels, claim/close/transcript, private staff notes.

Closing deletes the channel after a short, configurable delay (default 3 seconds). The transcript is attempted
BEFORE deletion and the result is reported honestly. Staff-only actions are re-checked inside every handler;
hiding a button is never the security.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import core_branding as branding
from core_checks import can_manage_server
from core_embeds import BRAND, ERROR, WARNING, error_embed, footer, info_embed, ok_embed, system_embed, warn_embed
from core_emoji import ICONS
from core_errors import InvalidInput
from core_formatting import clean_inline, format_duration
from core_moderation_checks import render_template
from core_naming import ticket_channel_name
from core_ticket_logic import DEFAULT_CATEGORIES, DEFAULT_DELAY, build_transcript, can_open, staff_role_ids
from svc_log_service import plain
from view_confirm import ask

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.tickets")
SYSTEM = "Ticket System"
_ENSURE = "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING"
PRIORITIES = ("low", "normal", "high", "urgent")
STYLES = {"primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
          "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger}
CONFIG_COLUMNS = {"category_id", "support_role_id", "panel_channel_id", "panel_message_id", "autoclose_hours", "inactivity_hours",
                  "categories", "staff_role_ids", "opening_message", "instructions", "transcript_channel_id", "close_delay_seconds",
                  "ticket_limit", "transcripts_enabled", "default_priority", "naming"}
PANEL_COLUMNS = {"channel_id", "message_id", "title", "description", "color", "banner_url", "thumbnail_url", "footer",
                 "button_label", "button_style", "button_emoji", "select_placeholder", "name"}
DEFAULT_OPENING = "Thanks for opening a ticket, {user}. Describe what you need and our staff will be with you soon."


# ---------------------------------------------------------------------------------------------
# Persistent panel components. The panel id is in the custom_id, so any number of panels survive restarts.
# Old panels (no id) keep working and use the server's first panel.
# ---------------------------------------------------------------------------------------------
class PanelCreateButton(discord.ui.DynamicItem[discord.ui.Button], template=r"hopeless:ticket:create(?::(?P<pid>[0-9]+))?"):
    def __init__(self, pid: Optional[int] = None, label: str = "Create Ticket", style: str = "primary", emoji: Optional[str] = None) -> None:
        super().__init__(discord.ui.Button(label=(label or "Create Ticket")[:80], style=STYLES.get(style, STYLES["primary"]),
                                           custom_id=f"hopeless:ticket:create:{pid}" if pid else "hopeless:ticket:create", emoji=emoji or "🎫"))
        self.pid = pid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match, /):
        return cls(int(match["pid"]) if match["pid"] else None)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.get_cog("Tickets").open_ticket(interaction, self.pid, None)


class PanelSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"hopeless:ticket:category(?::(?P<pid>[0-9]+))?"):
    def __init__(self, pid: Optional[int] = None, placeholder: str = "Or choose a category...", options: Optional[list] = None) -> None:
        super().__init__(discord.ui.Select(custom_id=f"hopeless:ticket:category:{pid}" if pid else "hopeless:ticket:category",
                                           placeholder=(placeholder or "Or choose a category...")[:150],
                                           options=options or [discord.SelectOption(label="Loading", value="x")]))
        self.pid = pid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match, /):
        return cls(int(match["pid"]) if match["pid"] else None)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.get_cog("Tickets").open_ticket(interaction, self.pid, self.item.values[0])


class TicketControls(discord.ui.View):
    """Buttons inside every ticket. Each press re-checks who is allowed."""

    def __init__(self, cog: "Tickets") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        for key, label, style in (("claim", "Claim", discord.ButtonStyle.success), ("unclaim", "Unclaim", discord.ButtonStyle.secondary),
                                  ("close", "Close", discord.ButtonStyle.danger), ("transcript", "Transcript", discord.ButtonStyle.secondary),
                                  ("delete", "Delete", discord.ButtonStyle.danger)):
            button = discord.ui.Button(label=label, style=style, custom_id=f"hopeless:ticket:{key}")
            button.callback = self._make(key)
            self.add_item(button)

    def _make(self, key: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.control(interaction, key)
        return callback


class LegacyReopen(discord.ui.View):
    """Tickets made by the first version had a Reopen button. Closed tickets are deleted now, so say so."""

    def __init__(self, cog: "Tickets") -> None:
        super().__init__(timeout=None)
        button = discord.ui.Button(label="Reopen", custom_id="hopeless:ticket:reopen")
        button.callback = self._cb
        self.add_item(button)

    async def _cb(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=warn_embed("Closed tickets are deleted, so they can't be reopened. Open a new ticket instead."), ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot
        self._task: Optional[asyncio.Task] = None
        self._opening: set[int] = set()
        self._closing: set[int] = set()
        self._open_channels: set[int] = set()
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self.bot.add_view(TicketControls(self))
        self.bot.add_view(LegacyReopen(self))
        self.bot.add_dynamic_items(PanelCreateButton, PanelSelect)
        self._task = asyncio.create_task(self._loop(), name="ticket-maintenance")

    async def cog_unload(self) -> None:
        if self._task:
            self._task.cancel()

    # --- data helpers ------------------------------------------------------------------------
    async def config(self, guild_id: int):
        return await self.bot.db.fetchrow("SELECT * FROM ticket_config WHERE guild_id = $1", guild_id)

    async def ensure_config(self, guild_id: int):
        cfg = await self.config(guild_id)
        if cfg is None:
            async with self.bot.db.transaction() as conn:
                await conn.execute(_ENSURE, guild_id)
                await conn.execute("INSERT INTO ticket_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
            cfg = await self.config(guild_id)
        return cfg

    async def save_config(self, guild_id: int, **cols) -> None:
        assert set(cols) <= CONFIG_COLUMNS
        await self.ensure_config(guild_id)
        sets = ", ".join(f"{n} = ${i + 2}" for i, n in enumerate(cols))
        await self.bot.db.execute(f"UPDATE ticket_config SET {sets} WHERE guild_id = $1", guild_id, *cols.values())

    async def panels(self, guild_id: int):
        return await self.bot.db.fetch("SELECT * FROM ticket_panels WHERE guild_id = $1 ORDER BY id", guild_id)

    async def get_panel(self, guild_id: int, ref=None):
        """A panel by id or name; with no reference, the server's first panel (created on demand)."""
        if ref is None:
            rows = await self.panels(guild_id)
            return rows[0] if rows else await self.create_panel(guild_id, "default")
        if isinstance(ref, int) or str(ref).isdigit():
            row = await self.bot.db.fetchrow("SELECT * FROM ticket_panels WHERE guild_id = $1 AND id = $2", guild_id, int(ref))
            if row:
                return row
        return await self.bot.db.fetchrow("SELECT * FROM ticket_panels WHERE guild_id = $1 AND lower(name) = lower($2)", guild_id, str(ref))

    async def create_panel(self, guild_id: int, name: str):
        cfg = await self.ensure_config(guild_id)
        legacy = [c for c in (cfg["categories"] or []) if c]
        names = [(n, d) for n, d in DEFAULT_CATEGORIES]
        if legacy and set(legacy) != {"General Support", "Reports", "Appeals"} and set(legacy) != {n for n, _ in DEFAULT_CATEGORIES}:
            names = [(n, None) for n in legacy]  # keep topics a server set up in the first version
        try:
            async with self.bot.db.transaction() as conn:
                pid = await conn.fetchval("INSERT INTO ticket_panels (guild_id, name) VALUES ($1, $2) RETURNING id", guild_id, name)
                for i, (n, d) in enumerate(names):
                    await conn.execute("INSERT INTO ticket_categories (panel_id, name, description, position) VALUES ($1,$2,$3,$4)", pid, n, d, i)
        except Exception as exc:
            if exc.__class__.__name__ == "UniqueViolationError":
                raise InvalidInput(f"A panel called `{name}` already exists.") from exc
            raise
        return await self.get_panel(guild_id, pid)

    async def save_panel(self, panel_id: int, **cols) -> None:
        assert set(cols) <= PANEL_COLUMNS
        sets = ", ".join(f"{n} = ${i + 2}" for i, n in enumerate(cols))
        await self.bot.db.execute(f"UPDATE ticket_panels SET {sets} WHERE id = $1", panel_id, *cols.values())

    async def categories(self, panel_id: int, enabled_only: bool = False):
        extra = " AND enabled" if enabled_only else ""
        return await self.bot.db.fetch(f"SELECT * FROM ticket_categories WHERE panel_id = $1{extra} ORDER BY position, id", panel_id)

    async def _event(self, ticket_id: int, actor_id: Optional[int], action: str, detail: Optional[str] = None) -> None:
        try:
            await self.bot.db.execute("INSERT INTO ticket_events (ticket_id, actor_id, action, detail) VALUES ($1,$2,$3,$4)", ticket_id, actor_id, action, (detail or "")[:300])
        except Exception:
            log.warning("could not record ticket event %s", action, exc_info=True)

    @staticmethod
    def staff_roles(cfg) -> set[int]:
        return staff_role_ids(cfg["staff_role_ids"] or [], cfg["support_role_id"]) if cfg else set()

    def is_staff(self, member: discord.Member, cfg) -> bool:
        perms = member.guild_permissions
        return perms.administrator or perms.manage_guild or any(r.id in self.staff_roles(cfg) for r in member.roles)

    async def _ticket(self, channel_id: int):
        return await self.bot.db.fetchrow("SELECT * FROM tickets WHERE channel_id = $1", channel_id)

    async def _staff_ticket(self, ctx: commands.Context):
        cfg = await self.config(ctx.guild.id)
        if not self.is_staff(ctx.author, cfg):
            raise commands.CheckFailure("Only ticket staff can do that.")
        ticket = await self._ticket(ctx.channel.id)
        if ticket is None:
            raise InvalidInput("Run this inside a ticket channel.")
        return cfg, ticket

    # --- panel rendering ---------------------------------------------------------------------
    def render_panel(self, panel, cats, *, live: bool = True) -> tuple[discord.Embed, Optional[discord.ui.View]]:
        embed = discord.Embed(title=f"{ICONS.get('gear')} {panel['title']}", description=panel["description"], color=panel["color"] if panel["color"] is not None else BRAND)
        if panel["banner_url"]:
            embed.set_image(url=panel["banner_url"])
        if panel["thumbnail_url"]:
            embed.set_thumbnail(url=panel["thumbnail_url"])
        footer(embed, panel["footer"] or SYSTEM) if not panel["footer"] else embed.set_footer(text=panel["footer"])
        view = discord.ui.View(timeout=None)
        view.add_item(PanelCreateButton(panel["id"], panel["button_label"], panel["button_style"], panel["button_emoji"]))
        options = [discord.SelectOption(label=c["name"][:100], value=f"c{c['id']}", description=(c["description"] or None) and c["description"][:100],
                                        emoji=c["emoji"] or ICONS.component("chevron")) for c in cats[:25]]
        if options:
            view.add_item(PanelSelect(panel["id"], panel["select_placeholder"], options))
        return embed, view

    async def publish_panel(self, guild: discord.Guild, panel, channel: discord.TextChannel) -> discord.Message:
        cats = await self.categories(panel["id"], enabled_only=True)
        embed, view = self.render_panel(panel, cats)
        if panel["channel_id"] == channel.id and panel["message_id"]:
            try:
                message = await channel.fetch_message(panel["message_id"])
                await message.edit(embed=embed, view=view)
                return message
            except discord.HTTPException:
                pass
        message = await channel.send(embed=embed, view=view)
        await self.save_panel(panel["id"], channel_id=channel.id, message_id=message.id)
        return message

    async def refresh_live_panel(self, guild: discord.Guild, panel_id: int) -> bool:
        """After an edit, update the panel that is already posted (no need to recreate it)."""
        panel = await self.get_panel(guild.id, panel_id)
        channel = guild.get_channel(panel["channel_id"]) if panel and panel["channel_id"] else None
        if not isinstance(channel, discord.TextChannel):
            return False
        try:
            await self.publish_panel(guild, panel, channel)
            return True
        except discord.HTTPException:
            return False

    # --- opening -----------------------------------------------------------------------------
    async def open_ticket(self, interaction: discord.Interaction, panel_id: Optional[int], choice: Optional[str]) -> None:
        guild, user = interaction.guild, interaction.user
        if guild is None:
            return
        banned = self.bot.globals.ban_message(user.id)
        if banned:
            await interaction.response.send_message(embed=error_embed(banned), ephemeral=True)
            return
        cfg = await self.ensure_config(guild.id)
        panel = await self.get_panel(guild.id, panel_id)
        category_name, category_id = "General", None
        if choice:
            cats = await self.categories(panel["id"], enabled_only=True)
            picked = next((c for c in cats if choice == f"c{c['id']}"), None)
            if picked is None and choice.isdigit() and int(choice) < len(cats):  # panels from the first version used positions
                picked = cats[int(choice)]
            if picked is None:
                await interaction.response.send_message(embed=error_embed("That topic isn't available any more."), ephemeral=True)
                return
            category_name, category_id = picked["name"], picked["id"]
        if user.id in self._opening:
            await interaction.response.send_message(embed=info_embed(None, "Your ticket is already being created."), ephemeral=True)
            return
        self._opening.add(user.id)
        await interaction.response.defer(ephemeral=True)
        ticket_id = None
        try:
            async with self.bot.db.transaction() as conn:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ticket:{guild.id}:{user.id}")
                open_now = await conn.fetchval("SELECT COUNT(*) FROM tickets WHERE guild_id = $1 AND owner_id = $2 AND status IN ('open','closing')", guild.id, user.id)
                if not can_open(open_now, cfg["ticket_limit"]):
                    existing = await conn.fetchval("SELECT channel_id FROM tickets WHERE guild_id = $1 AND owner_id = $2 AND status = 'open' ORDER BY id DESC LIMIT 1", guild.id, user.id)
                    where = f" Your open ticket: <#{existing}>" if existing else ""
                    await interaction.followup.send(embed=info_embed(None, f"You've reached this server's limit of {cfg['ticket_limit']} open ticket(s).{where}"), ephemeral=True)
                    return
                number = await conn.fetchval("SELECT COALESCE(MAX(number), 0) + 1 FROM tickets WHERE guild_id = $1", guild.id)
                ticket_id = await conn.fetchval(
                    "INSERT INTO tickets (guild_id, number, owner_id, category, panel_id, category_id, priority) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
                    guild.id, number, user.id, category_name, panel["id"], category_id, cfg["default_priority"])
            roles = [r for r in (guild.get_role(i) for i in self.staff_roles(cfg)) if r is not None]
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, embed_links=True, attach_files=True),
            }
            for role in roles:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True, manage_messages=True)
            parent = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
            channel = await guild.create_text_channel(
                ticket_channel_name(cfg["naming"], number, user.name, category_name), category=parent, overwrites=overwrites,
                topic=f"Ticket #{number} | {category_name} | owner {user.id} | priority {cfg['default_priority']}", reason="ticket opened")
            await self.bot.db.execute("UPDATE tickets SET channel_id = $2 WHERE id = $1", ticket_id, channel.id)
            self._open_channels.add(channel.id)
            await self._event(ticket_id, user.id, "opened", category_name)
            values = {"user": user.mention, "username": discord.utils.escape_markdown(user.name), "server": discord.utils.escape_markdown(guild.name), "membercount": str(guild.member_count or 0)}
            embed = system_embed(SYSTEM, f"Ticket #{number:04d} • {plain(category_name, 60)}",
                                 render_template(cfg["opening_message"] or DEFAULT_OPENING, values),
                                 color=panel["color"] if panel["color"] is not None else BRAND)
            embed.add_field(name="Category", value=plain(category_name, 80))
            embed.add_field(name="Opened by", value=user.mention)
            embed.add_field(name="Priority", value=cfg["default_priority"].title())
            if cfg["instructions"]:
                embed.add_field(name="Before you continue", value=plain(cfg["instructions"], 900), inline=False)
            await channel.send(user.mention, embed=embed, view=TicketControls(self), allowed_mentions=discord.AllowedMentions(users=[user]))
            await self.bot.logs.server(guild, "ticket", "Ticket opened", fields=[("Ticket", f"#{number:04d} ({category_name})"), ("Owner", f"{user.name} ({user.id})")])
            await interaction.followup.send(embed=ok_embed(f"Your ticket is ready: {channel.mention}"), ephemeral=True)
        except discord.HTTPException:
            if ticket_id is not None:
                await self.bot.db.execute("DELETE FROM tickets WHERE id = $1 AND channel_id IS NULL", ticket_id)
            await interaction.followup.send(embed=error_embed("I couldn't create your ticket. Ask an admin to check my permissions."), ephemeral=True)
        finally:
            self._opening.discard(user.id)

    # --- buttons inside a ticket -------------------------------------------------------------
    async def control(self, interaction: discord.Interaction, key: str) -> None:
        banned = self.bot.globals.ban_message(interaction.user.id)
        if banned:
            await interaction.response.send_message(embed=error_embed(banned), ephemeral=True)
            return
        ticket = await self._ticket(interaction.channel_id)
        if ticket is None:
            await interaction.response.send_message(embed=error_embed("This isn't a tracked ticket."), ephemeral=True)
            return
        cfg = await self.config(interaction.guild.id)
        member = interaction.user
        staff, owner = self.is_staff(member, cfg), member.id == ticket["owner_id"]
        if key in ("claim", "unclaim", "transcript", "delete") and not staff:
            await interaction.response.send_message(embed=error_embed("Only ticket staff can do that.", title="Permission denied"), ephemeral=True)
            return
        if key == "close" and not (staff or owner):
            await interaction.response.send_message(embed=error_embed("Only staff or the ticket's owner can close it.", title="Permission denied"), ephemeral=True)
            return
        lock = self._locks.setdefault(ticket["id"], asyncio.Lock())  # simultaneous presses are handled one at a time
        async with lock:
            channel = interaction.channel
            if ticket["status"] != "open" and key != "transcript":
                await interaction.response.send_message(embed=info_embed(None, "This ticket is already closing."), ephemeral=True)
                return
            if key == "claim":
                won = await self.bot.db.fetchval("UPDATE tickets SET claimed_by = $2 WHERE id = $1 AND claimed_by IS NULL AND status = 'open' RETURNING id", ticket["id"], member.id)
                if won is None:
                    await interaction.response.send_message(embed=info_embed(None, "Someone already claimed this ticket."), ephemeral=True)
                    return
                await self._event(ticket["id"], member.id, "claimed")
                await interaction.response.send_message(embed=system_embed(SYSTEM, "Ticket claimed", f"{member.mention} is handling this ticket.", color=BRAND))
                await self.bot.logs.server(interaction.guild, "ticket", "Ticket claimed", fields=[("Ticket", f"#{ticket['number']:04d}"), ("Staff", f"{member.name} ({member.id})")])
            elif key == "unclaim":
                if ticket["claimed_by"] not in (member.id, None) and not member.guild_permissions.manage_guild:
                    await interaction.response.send_message(embed=error_embed("Only the staff member who claimed it, or a server manager, can unclaim.", title="Permission denied"), ephemeral=True)
                    return
                await self.bot.db.execute("UPDATE tickets SET claimed_by = NULL WHERE id = $1", ticket["id"])
                await self._event(ticket["id"], member.id, "unclaimed")
                await interaction.response.send_message(embed=system_embed(SYSTEM, "Ticket unclaimed", "This ticket is open for any staff member.", color=BRAND))
                await self.bot.logs.server(interaction.guild, "ticket", "Ticket unclaimed", fields=[("Ticket", f"#{ticket['number']:04d}"), ("Staff", f"{member.name} ({member.id})")])
            elif key == "transcript":
                await interaction.response.defer(ephemeral=True)
                ok, note, file = await self._build_transcript_file(channel, ticket)
                await interaction.followup.send(file=file, ephemeral=True) if file else await interaction.followup.send(embed=error_embed(note), ephemeral=True)
            elif key in ("close", "delete"):
                await interaction.response.defer()
                await self.close_ticket(channel, ticket, by=member.name, by_id=member.id,
                                        reason="deleted by staff" if key == "delete" else "closed", delay=0 if key == "delete" else None)

    # --- closing: transcript first, short delay, delete, record --------------------------------
    async def close_ticket(self, channel: discord.TextChannel, ticket, *, by: str, reason: str, by_id: Optional[int] = None, delay: Optional[int] = None) -> None:
        won = await self.bot.db.fetchrow("UPDATE tickets SET status = 'closing', close_started_at = now() WHERE id = $1 AND status = 'open' RETURNING *", ticket["id"])
        if won is None or ticket["id"] in self._closing:
            return  # somebody else is already closing it
        self._closing.add(ticket["id"])
        self._open_channels.discard(channel.id)
        try:
            await self._finish_close(channel, won, by, reason, delay, by_id)
        finally:
            self._closing.discard(ticket["id"])

    async def _finish_close(self, channel: discord.TextChannel, ticket, by: str, reason: str, delay: Optional[int], by_id: Optional[int]) -> None:
        guild = channel.guild
        cfg = await self.ensure_config(guild.id)
        delay = cfg["close_delay_seconds"] if delay is None else delay
        await self._event(ticket["id"], by_id, "closed", reason)
        try:
            await channel.send(embed=system_embed(SYSTEM, "Ticket closing", f"Closed by **{discord.utils.escape_markdown(by)}** ({reason}).\nThis channel will be deleted in **{delay} second{'s' if delay != 1 else ''}**.", color=WARNING))
        except discord.HTTPException:
            pass
        if cfg["transcripts_enabled"]:
            saved, note = await self._save_transcript(channel, ticket, cfg)
        else:
            saved, note = None, "transcripts are turned off"
        if saved is False:
            await self._event(ticket["id"], None, "transcript_failed", note)
        try:
            await channel.send(embed=(ok_embed(f"Transcript: {note}.", title="Transcript saved") if saved else
                                      error_embed(f"The transcript was **not** saved: {note}.", title="Transcript failed") if saved is False else
                                      info_embed(None, f"No transcript: {note}.")))
        except discord.HTTPException:
            pass
        await asyncio.sleep(max(0, delay))
        deleted, problem = True, ""
        try:
            await channel.delete(reason=f"ticket #{ticket['number']} closed")
        except discord.HTTPException as exc:
            deleted, problem = False, f"Discord refused to delete the channel (HTTP {exc.status})"
            try:
                await channel.send(embed=error_embed("I couldn't delete this channel. A server manager can remove it by hand, and my role needs the Manage Channels permission."))
            except discord.HTTPException:
                pass
        await self.bot.db.execute("UPDATE tickets SET status = 'closed', closed_at = now(), channel_id = CASE WHEN $2 THEN NULL ELSE channel_id END WHERE id = $1", ticket["id"], deleted)
        await self._event(ticket["id"], by_id, "deleted" if deleted else "delete_failed", problem or None)
        await self.bot.logs.server(guild, "ticket", "Ticket closed", fields=[
            ("Ticket", f"#{ticket['number']:04d} ({ticket['category']})"), ("Closed by", by), ("Reason", reason),
            ("Transcript", note if saved is not None else "off"), ("Channel", "deleted" if deleted else problem)])

    async def _build_transcript_file(self, channel: discord.TextChannel, ticket):
        """(ok, note, discord.File). Includes the ticket facts, staff actions and the message history."""
        try:
            guild = channel.guild
            owner = guild.get_member(ticket["owner_id"])
            events = await self.bot.db.fetch("SELECT actor_id, action, detail, created_at FROM ticket_events WHERE ticket_id = $1 ORDER BY id", ticket["id"])
            lines = []
            for e in events:
                who_ = guild.get_member(e["actor_id"]) if e["actor_id"] else None
                lines.append((e["created_at"], f"{who_.name if who_ else (e['actor_id'] or 'system')}: {e['action']}" + (f" ({e['detail']})" if e["detail"] else "")))
            messages = []
            async for m in channel.history(limit=1000, oldest_first=True):
                messages.append({"at": m.created_at, "author": str(m.author), "author_id": m.author.id, "content": m.clean_content,
                                 "attachments": [{"name": a.filename, "size": a.size, "url": a.url} for a in m.attachments]})
            claimed = guild.get_member(ticket["claimed_by"]) if ticket["claimed_by"] else None
            header = {"Ticket": f"#{ticket['number']:04d}", "Creator": f"{owner.name if owner else 'left the server'} ({ticket['owner_id']})",
                      "Category": ticket["category"], "Priority": ticket["priority"], "Claimed by": claimed.name if claimed else "nobody",
                      "Opened": ticket["created_at"].strftime("%Y-%m-%d %H:%M:%S UTC"), "Closed": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                      "Server": guild.name, "Messages": f"{len(messages)} (up to the last 1000)"}
            text = build_transcript(header, lines, messages)
            return True, "ready", discord.File(io.BytesIO(text.encode("utf-8")), filename=f"ticket-{ticket['number']:04d}.txt")
        except discord.HTTPException as exc:
            return False, f"I couldn't read the channel history (HTTP {exc.status})", None
        except Exception:
            log.warning("transcript build failed", exc_info=True)
            return False, "building the transcript failed", None

    async def _save_transcript(self, channel: discord.TextChannel, ticket, cfg) -> tuple[bool, str]:
        """Attempt to store the transcript and say truthfully what happened. Never claims success it didn't get."""
        ok, note, file = await self._build_transcript_file(channel, ticket)
        if not ok or file is None:
            return False, note
        guild = channel.guild
        embed = system_embed(SYSTEM, f"Transcript: ticket #{ticket['number']:04d}", f"Category: {plain(ticket['category'], 60)}\nOwner: <@{ticket['owner_id']}>", color=BRAND)
        dest = guild.get_channel(cfg["transcript_channel_id"]) if cfg["transcript_channel_id"] else None
        if isinstance(dest, discord.TextChannel):
            try:
                await dest.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
                return True, f"saved to {dest.mention}"
            except discord.HTTPException as exc:
                return False, f"I couldn't post it in {dest.mention} (HTTP {exc.status})"
        if await self.bot.logs.server(guild, "ticket", f"Transcript: ticket #{ticket['number']:04d}", f"Category: {ticket['category']}", file=file):
            return True, "saved to the private ticket logs"
        return False, "no transcript channel or ticket-logs channel is set up (use the ticket designer to choose one)"

    # --- activity, auto-close, restart recovery -----------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.channel.id not in self._open_channels:
            return
        await self.bot.db.execute("UPDATE tickets SET last_activity = now(), warned = FALSE WHERE channel_id = $1 AND status = 'open'", message.channel.id)

    async def _resume_closing(self) -> None:
        """If the bot restarted mid-close, finish those tickets exactly once."""
        for row in await self.bot.db.fetch("SELECT * FROM tickets WHERE status = 'closing'"):
            channel = self.bot.get_channel(row["channel_id"]) if row["channel_id"] else None
            if isinstance(channel, discord.TextChannel) and row["id"] not in self._closing:
                self._closing.add(row["id"])
                asyncio.create_task(self._finish_then_release(channel, row))
            else:
                await self.bot.db.execute("UPDATE tickets SET status = 'closed', closed_at = COALESCE(closed_at, now()), channel_id = NULL WHERE id = $1", row["id"])

    async def _finish_then_release(self, channel, row) -> None:
        try:
            await self._finish_close(channel, row, "automatic (restart recovery)", "finishing an interrupted close", None, None)
        finally:
            self._closing.discard(row["id"])

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            rows = await self.bot.db.fetch("SELECT channel_id FROM tickets WHERE status = 'open' AND channel_id IS NOT NULL")
            self._open_channels = {r["channel_id"] for r in rows}
            await self._resume_closing()
        except Exception:
            log.warning("ticket startup recovery failed", exc_info=True)
        while True:
            await asyncio.sleep(600)
            try:
                now = datetime.now(timezone.utc)
                rows = await self.bot.db.fetch(
                    "SELECT t.*, c.autoclose_hours, c.inactivity_hours FROM tickets t JOIN ticket_config c USING (guild_id) "
                    "WHERE t.status = 'open' AND t.channel_id IS NOT NULL AND (c.autoclose_hours IS NOT NULL OR c.inactivity_hours IS NOT NULL)")
                for r in rows:
                    channel = self.bot.get_channel(r["channel_id"])
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    idle = (now - r["last_activity"]).total_seconds() / 3600
                    if r["autoclose_hours"] and idle >= r["autoclose_hours"]:
                        await self.close_ticket(channel, r, by="automatic", reason=f"no activity for {r['autoclose_hours']}h")
                    elif r["inactivity_hours"] and idle >= r["inactivity_hours"] and not r["warned"]:
                        await self.bot.db.execute("UPDATE tickets SET warned = TRUE WHERE id = $1", r["id"])
                        await channel.send(embed=warn_embed(f"This ticket has been quiet for {r['inactivity_hours']}h. Reply to keep it open.", title="Still there?"))
            except Exception:
                log.warning("ticket maintenance error", exc_info=True)

    # --- quick setup (also used by /setup) -----------------------------------------------------
    def plan_quick(self, guild: discord.Guild, cfg=None) -> list[str]:
        have_cat = bool(cfg and cfg["category_id"] and guild.get_channel(cfg["category_id"]))
        have_staff = bool(cfg and any(guild.get_role(i) for i in self.staff_roles(cfg)))
        return ["reuse the ticket category" if have_cat else "create the category **Tickets**",
                "reuse the staff role(s)" if have_staff else "create the role **Ticket Staff**",
                "post the ticket panel (the channel **open-a-ticket** is created if no panel exists yet)",
                "closed tickets are deleted 3 seconds after closing, with a transcript attempt first"]

    async def apply_quick(self, guild: discord.Guild) -> list[str]:
        cfg = await self.ensure_config(guild.id)
        panel = await self.get_panel(guild.id)
        results = []
        try:
            category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
            if category is None:
                category = await guild.create_category("Tickets", overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)}, reason="ticket setup")
                results.append("created the Tickets category")
            roles = self.staff_roles(cfg)
            if not any(guild.get_role(i) for i in roles):
                role = await guild.create_role(name="Ticket Staff", reason="ticket setup")
                roles = {role.id}
                results.append("created the Ticket Staff role (give it to your staff)")
            await self.save_config(guild.id, category_id=category.id, staff_role_ids=sorted(roles))
            channel = guild.get_channel(panel["channel_id"]) if panel["channel_id"] else None
            if not isinstance(channel, discord.TextChannel):
                channel = await guild.create_text_channel("open-a-ticket", reason="ticket setup", overwrites={guild.default_role: discord.PermissionOverwrite(send_messages=False)})
                results.append("created #open-a-ticket")
            await self.publish_panel(guild, panel, channel)
            results.append("posted the ticket panel")
        except discord.HTTPException as exc:
            results.append(f"stopped early: Discord refused (HTTP {exc.status}). Fix my permissions and run it again")
        return results

    # --- commands ----------------------------------------------------------------------------
    def _manager(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not can_manage_server(ctx.bot, ctx.author):
            raise commands.MissingPermissions(["manage_guild"])

    @commands.hybrid_command(name="psetup", description="One-click ticket setup with a preview")
    async def psetup(self, ctx: commands.Context) -> None:
        self._manager(ctx)
        cfg = await self.config(ctx.guild.id)
        if await ask(ctx, info_embed("Ticket setup", "\n".join(f"• {l}" for l in self.plan_quick(ctx.guild, cfg)))) != 0:
            await ctx.send(embed=info_embed(None, "Cancelled. Nothing was changed."))
            return
        await ctx.send(embed=ok_embed("\n".join(await self.apply_quick(ctx.guild))))

    @commands.hybrid_group(name="ticketpanel", fallback="post", description="Post or refresh a ticket panel in this channel")
    @app_commands.describe(name="Panel name (default: the first panel)")
    async def ticketpanel(self, ctx: commands.Context, *, name: Optional[str] = None) -> None:
        self._manager(ctx)
        panel = await self.get_panel(ctx.guild.id, name)
        if panel is None:
            raise InvalidInput("There's no panel with that name. See `ticketpanel list`.")
        await self.publish_panel(ctx.guild, panel, ctx.channel)
        await ctx.send(embed=ok_embed(f"Panel **{plain(panel['name'], 40)}** is posted here. Customize it with `ticketdesign {panel['name']}`."), ephemeral=True)

    @ticketpanel.command(name="new", description="Create another ticket panel")
    async def tp_new(self, ctx: commands.Context, *, name: str) -> None:
        self._manager(ctx)
        name = " ".join(name.split())
        if not 1 <= len(name) <= 40:
            raise InvalidInput("Give the panel a name of 1 to 40 characters.")
        if len(await self.panels(ctx.guild.id)) >= 10:
            raise InvalidInput("A server can have up to 10 panels.")
        await self.create_panel(ctx.guild.id, name)
        await ctx.send(embed=ok_embed(f"Panel **{plain(name, 40)}** created. Customize it with `ticketdesign {name}`, then post it with `ticketpanel {name}`."))

    @ticketpanel.command(name="list", description="List this server's ticket panels")
    async def tp_list(self, ctx: commands.Context) -> None:
        self._manager(ctx)
        rows = await self.panels(ctx.guild.id)
        lines = [f"**{plain(r['name'], 40)}**: {len(await self.categories(r['id']))} topics, " + (f"posted in <#{r['channel_id']}>" if r["channel_id"] else "not posted yet") for r in rows]
        await ctx.send(embed=info_embed("Ticket panels", "\n".join(lines) or "None yet. `psetup` or `ticketpanel new <name>` creates one."))

    @ticketpanel.command(name="delete", description="Delete a ticket panel")
    async def tp_delete(self, ctx: commands.Context, *, name: str) -> None:
        self._manager(ctx)
        panel = await self.get_panel(ctx.guild.id, name)
        if panel is None:
            raise InvalidInput("There's no panel with that name.")
        if len(await self.panels(ctx.guild.id)) <= 1:
            raise InvalidInput("You can't delete your only panel. Edit it instead.")
        if await ask(ctx, warn_embed(f"Delete the panel **{plain(panel['name'], 40)}** and its topics? Open tickets are not affected.", title="Delete panel?")) != 0:
            await ctx.send(embed=info_embed(None, "Cancelled."))
            return
        channel = ctx.guild.get_channel(panel["channel_id"]) if panel["channel_id"] else None
        await self.bot.db.execute("DELETE FROM ticket_panels WHERE id = $1", panel["id"])
        if isinstance(channel, discord.TextChannel) and panel["message_id"]:
            try:
                message = await channel.fetch_message(panel["message_id"])
                await message.edit(embed=info_embed("Panel removed", "This ticket panel is no longer available."), view=None)
            except discord.HTTPException:
                pass
        await ctx.send(embed=ok_embed("Panel deleted."))

    @commands.hybrid_group(name="ticketsetup", fallback="show", description="Show ticket settings")
    async def ticketsetup(self, ctx: commands.Context) -> None:
        self._manager(ctx)
        cfg = await self.ensure_config(ctx.guild.id)
        roles = " ".join(f"<@&{i}>" for i in sorted(self.staff_roles(cfg))) or "none"
        text = (f"**Staff roles:** {roles}\n**Ticket category:** {'<#%d>' % cfg['category_id'] if cfg['category_id'] else 'none'}\n"
                f"**Transcripts:** {'<#%d>' % cfg['transcript_channel_id'] if cfg['transcript_channel_id'] else ('ticket-logs channel' if cfg['transcripts_enabled'] else 'off')}\n"
                f"**Closing delay:** {cfg['close_delay_seconds']}s • **Tickets per member:** {cfg['ticket_limit']}\n"
                f"**Naming:** `{cfg['naming']}`\n\nEverything here is easiest with `ticketdesign`.")
        await ctx.send(embed=system_embed(SYSTEM, "Ticket settings", text))

    @ticketsetup.command(name="autoclose", description="Close inactive tickets after this many hours (empty = off)")
    async def ts_autoclose(self, ctx: commands.Context, hours: Optional[commands.Range[int, 1, 720]] = None) -> None:
        self._manager(ctx)
        await self.save_config(ctx.guild.id, autoclose_hours=hours)
        await ctx.send(embed=ok_embed(f"Auto-close after {hours}h of silence." if hours else "Auto-close is off."))

    @ticketsetup.command(name="inactivity", description="Warn after this many quiet hours (empty = off)")
    async def ts_inactivity(self, ctx: commands.Context, hours: Optional[commands.Range[int, 1, 720]] = None) -> None:
        self._manager(ctx)
        await self.save_config(ctx.guild.id, inactivity_hours=hours)
        await ctx.send(embed=ok_embed(f"Inactivity warning after {hours}h." if hours else "Inactivity warnings are off."))

    @ticketsetup.command(name="category", description="Choose the category channel tickets are created in")
    async def ts_category(self, ctx: commands.Context, category: discord.CategoryChannel) -> None:
        self._manager(ctx)
        await self.save_config(ctx.guild.id, category_id=category.id)
        await ctx.send(embed=ok_embed(f"Tickets will open in **{plain(category.name)}**."))

    @ticketsetup.command(name="role", description="Add (or remove, if already listed) a ticket staff role")
    async def ts_role(self, ctx: commands.Context, role: discord.Role) -> None:
        self._manager(ctx)
        cfg = await self.ensure_config(ctx.guild.id)
        current = set(self.staff_roles(cfg))
        if role.is_default():
            raise InvalidInput("@everyone can't be ticket staff.")
        if role.id in current:
            current.discard(role.id)
            text = f"{role.mention} is no longer ticket staff."
        else:
            current.add(role.id)
            text = f"{role.mention} is now ticket staff."
        await self.save_config(ctx.guild.id, staff_role_ids=sorted(current), support_role_id=None)
        await ctx.send(embed=ok_embed(text))

    @ticketsetup.command(name="categories", description="Replace the topics of the first panel (comma separated)")
    @app_commands.describe(topics="For example: Support, Contact, Partnership, Report, Ban Appeal, Other")
    async def ts_categories(self, ctx: commands.Context, *, topics: str) -> None:
        self._manager(ctx)
        names = [" ".join(t.split())[:80] for t in topics.split(",") if t.strip()]
        if not 1 <= len(names) <= 25:
            raise InvalidInput("Give between 1 and 25 topics separated by commas.")
        panel = await self.get_panel(ctx.guild.id)
        async with self.bot.db.transaction() as conn:
            await conn.execute("DELETE FROM ticket_categories WHERE panel_id = $1", panel["id"])
            for i, n in enumerate(names):
                await conn.execute("INSERT INTO ticket_categories (panel_id, name, position) VALUES ($1,$2,$3)", panel["id"], n, i)
        await self.refresh_live_panel(ctx.guild, panel["id"])
        await ctx.send(embed=ok_embed(f"Topics saved: {', '.join(names)}."))

    @commands.hybrid_command(name="ticketdesign", description="Open the ticket designer to customize a panel")
    @app_commands.describe(name="Panel to edit (default: the first panel)")
    async def ticketdesign(self, ctx: commands.Context, *, name: Optional[str] = None) -> None:
        self._manager(ctx)
        panel = await self.get_panel(ctx.guild.id, name)
        if panel is None:
            raise InvalidInput("There's no panel with that name. See `ticketpanel list`.")
        designer = TicketDesigner(self, ctx.author.id, ctx.guild.id, panel["id"])
        await ctx.send(embed=await designer.summary(), view=designer, ephemeral=True)

    # --- staff tools inside a ticket ---------------------------------------------------------
    @commands.hybrid_command(name="ticketnote", description="Add a private staff note to this ticket")
    @app_commands.describe(note="The note (only staff can read it)")
    async def ticketnote(self, ctx: commands.Context, *, note: str) -> None:
        _, ticket = await self._staff_ticket(ctx)
        if ctx.interaction is None:  # a typed command is visible to the ticket owner, so remove it
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        await self.bot.db.execute("INSERT INTO ticket_notes (ticket_id, author_id, note) VALUES ($1,$2,$3)", ticket["id"], ctx.author.id, note[:1500])
        await self._event(ticket["id"], ctx.author.id, "note_added")
        await ctx.send(embed=ok_embed("Note saved. Only staff can read it. Tip: `/ticketnote` keeps the text private from the start."), ephemeral=True, delete_after=6)

    @commands.hybrid_command(name="ticketnotes", description="Show this ticket's staff notes (sent privately)")
    async def ticketnotes(self, ctx: commands.Context) -> None:
        _, ticket = await self._staff_ticket(ctx)
        rows = await self.bot.db.fetch("SELECT author_id, note, created_at FROM ticket_notes WHERE ticket_id = $1 ORDER BY id", ticket["id"])
        body = "\n\n".join(f"<@{r['author_id']}> {discord.utils.format_dt(r['created_at'], 'R')}\n{plain(r['note'], 400)}" for r in rows) or "No notes."
        embed = system_embed(SYSTEM, f"Notes for ticket #{ticket['number']:04d}", body[:3800])
        if ctx.interaction is not None:
            await ctx.send(embed=embed, ephemeral=True)
            return
        try:
            await ctx.author.send(embed=embed)
            await ctx.send(embed=ok_embed("I sent the notes to your DMs."), delete_after=6)
        except discord.HTTPException:
            await ctx.send(embed=error_embed("I can't DM you. Use `/ticketnotes` instead, so the notes stay private."), delete_after=10)

    @commands.hybrid_command(name="ticketpriority", description="Set this ticket's priority")
    @app_commands.describe(level="low, normal, high or urgent")
    async def ticketpriority(self, ctx: commands.Context, level: str) -> None:
        _, ticket = await self._staff_ticket(ctx)
        level = level.lower()
        if level not in PRIORITIES:
            raise InvalidInput("Priority must be low, normal, high or urgent.")
        await self.bot.db.execute("UPDATE tickets SET priority = $2 WHERE id = $1", ticket["id"], level)
        await self._event(ticket["id"], ctx.author.id, "priority", level)
        try:
            await ctx.channel.edit(topic=re.sub(r"priority \w+$", f"priority {level}", ctx.channel.topic or "") or f"priority {level}")
        except discord.HTTPException:
            pass
        await ctx.send(embed=ok_embed(f"Priority is now **{level}**."))

    @commands.hybrid_command(name="ticketadd", description="Add a member to this ticket")
    async def ticketadd(self, ctx: commands.Context, member: discord.Member) -> None:
        _, ticket = await self._staff_ticket(ctx)
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
        await self._event(ticket["id"], ctx.author.id, "member_added", str(member.id))
        await ctx.send(embed=ok_embed(f"Added {member.mention} to this ticket."))

    @commands.hybrid_command(name="ticketremove", description="Remove a member from this ticket")
    async def ticketremove(self, ctx: commands.Context, member: discord.Member) -> None:
        _, ticket = await self._staff_ticket(ctx)
        if member.id == ticket["owner_id"]:
            raise InvalidInput("You can't remove the ticket's owner.")
        await ctx.channel.set_permissions(member, overwrite=None)
        await self._event(ticket["id"], ctx.author.id, "member_removed", str(member.id))
        await ctx.send(embed=ok_embed(f"Removed {member.mention} from this ticket."))

    @commands.hybrid_command(name="ticketstats", description="Show ticket statistics")
    async def ticketstats(self, ctx: commands.Context) -> None:
        cfg = await self.config(ctx.guild.id)
        if not self.is_staff(ctx.author, cfg):
            raise commands.CheckFailure("Only ticket staff can see ticket statistics.")
        g = ctx.guild.id
        row = await self.bot.db.fetchrow(
            "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status='open') AS open, COUNT(*) FILTER (WHERE status='closed') AS closed, "
            "AVG(EXTRACT(EPOCH FROM (closed_at - created_at))) FILTER (WHERE closed_at IS NOT NULL) AS avg_seconds FROM tickets WHERE guild_id = $1", g)
        by_priority = await self.bot.db.fetch("SELECT priority, COUNT(*) AS n FROM tickets WHERE guild_id = $1 AND status = 'open' GROUP BY priority", g)
        by_cat = await self.bot.db.fetch("SELECT category, COUNT(*) AS n FROM tickets WHERE guild_id = $1 GROUP BY category ORDER BY n DESC LIMIT 5", g)
        embed = system_embed(SYSTEM, "Ticket statistics")
        embed.add_field(name="Total", value=str(row["total"]))
        embed.add_field(name="Open", value=str(row["open"]))
        embed.add_field(name="Closed", value=str(row["closed"]))
        embed.add_field(name="Average time to close", value=format_duration(row["avg_seconds"]) if row["avg_seconds"] else "n/a")
        embed.add_field(name="Open by priority", value="\n".join(f"{r['priority']}: {r['n']}" for r in by_priority) or "none", inline=False)
        embed.add_field(name="Top topics", value="\n".join(f"{plain(r['category'], 40)}: {r['n']}" for r in by_cat) or "none", inline=False)
        await ctx.send(embed=embed)


# =============================================================================================
# The ticket designer: a guided, private editor for one panel. Every change is saved at once, the live panel
# (if posted) is updated in place, and nothing is shared between servers.
# =============================================================================================
from core_ticket_logic import clamp_delay, move  # noqa: E402
from core_ui_logic import (clean_text, parse_bool, parse_color, valid_button_style, valid_emoji, valid_https_url)  # noqa: E402

TOPICS = (("text", "Panel text and colour"), ("images", "Banner and thumbnail"), ("button", "Button and menu"),
          ("categories", "Categories"), ("staff", "Staff roles"), ("support", "Where tickets are created"),
          ("transcripts", "Transcript destination"), ("behaviour", "Naming, limits, closing delay"),
          ("messages", "Opening message and instructions"))


class _DesignModal(discord.ui.Modal):
    def __init__(self, designer: "TicketDesigner", title: str) -> None:
        super().__init__(title=title)
        self.d = designer

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if isinstance(error, InvalidInput):
            text = error.user_message
        else:
            log.warning("ticket designer modal failed", exc_info=error)
            import core_messages as plain_messages
            text = plain_messages.try_again()
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed(text), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(text), ephemeral=True)


def _input(label: str, value, *, long: bool = False, required: bool = False, limit: int = 100, placeholder: str = "") -> discord.ui.TextInput:
    assert len(label) <= 45, f"modal label too long for Discord: {label!r}"
    return discord.ui.TextInput(label=label, default=None if value is None else str(value), required=required, max_length=limit,
                                style=discord.TextStyle.paragraph if long else discord.TextStyle.short, placeholder=placeholder or None)


class TextModal(_DesignModal):
    def __init__(self, d, panel) -> None:
        super().__init__(d, "Panel text and colour")
        self.title_in = _input("Title", panel["title"], required=True, limit=100)
        self.desc_in = _input("Description", panel["description"], long=True, required=True, limit=1500)
        self.color_in = _input("Colour (#9B111E or a name)", f"#{panel['color']:06X}" if panel["color"] is not None else "", limit=20, placeholder="empty = default")
        self.footer_in = _input("Footer (empty = default)", panel["footer"] or "", limit=100)
        for item in (self.title_in, self.desc_in, self.color_in, self.footer_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.d.cog.save_panel(self.d.panel_id, title=clean_text(self.title_in.value, 100, required=True, what="title"),
                                    description=clean_text(self.desc_in.value, 1500, required=True, what="description"),
                                    color=parse_color(self.color_in.value), footer=clean_text(self.footer_in.value, 100))
        await self.d.after_edit(interaction)


class ImagesModal(_DesignModal):
    def __init__(self, d, panel) -> None:
        super().__init__(d, "Banner and thumbnail")
        self.banner = _input("Banner image link (https)", panel["banner_url"] or "", limit=400, placeholder="empty = none")
        self.thumb = _input("Thumbnail link (https)", panel["thumbnail_url"] or "", limit=400, placeholder="empty = none")
        self.add_item(self.banner)
        self.add_item(self.thumb)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.d.cog.save_panel(self.d.panel_id, banner_url=valid_https_url(self.banner.value, what="banner link"),
                                    thumbnail_url=valid_https_url(self.thumb.value, what="thumbnail link"))
        await self.d.after_edit(interaction)


class ButtonModal(_DesignModal):
    def __init__(self, d, panel) -> None:
        super().__init__(d, "Button and menu")
        self.label = _input("Button label", panel["button_label"], required=True, limit=80)
        self.style = _input("Style: primary, secondary, success, danger", panel["button_style"], required=True, limit=12)
        self.emoji = _input("Button emoji (empty = default)", panel["button_emoji"] or "", limit=60)
        self.placeholder = _input("Menu placeholder", panel["select_placeholder"], required=True, limit=100)
        for item in (self.label, self.style, self.emoji, self.placeholder):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.d.cog.save_panel(self.d.panel_id, button_label=clean_text(self.label.value, 80, required=True, what="button label"),
                                    button_style=valid_button_style(self.style.value), button_emoji=valid_emoji(self.emoji.value),
                                    select_placeholder=clean_text(self.placeholder.value, 100, required=True, what="menu placeholder"))
        await self.d.after_edit(interaction)


class BehaviourModal(_DesignModal):
    def __init__(self, d, cfg) -> None:
        super().__init__(d, "Naming, limits, closing")
        self.naming = _input("Channel name ({number} {user} {category})", cfg["naming"], required=True, limit=60)
        self.delay = _input("Seconds until a closed ticket is deleted", cfg["close_delay_seconds"], required=True, limit=2, placeholder="0 to 60 (default 3)")
        self.limit = _input("Open tickets allowed per member (1-10)", cfg["ticket_limit"], required=True, limit=2)
        self.transcripts = _input("Save transcripts? yes or no", "yes" if cfg["transcripts_enabled"] else "no", required=True, limit=3)
        self.priority = _input("Default priority: low, normal, high, urgent", cfg["default_priority"], required=True, limit=6)
        for item in (self.naming, self.delay, self.limit, self.transcripts, self.priority):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        limit = self.limit.value.strip()
        if not limit.isdigit() or not 1 <= int(limit) <= 10:
            raise InvalidInput("Tickets per member must be a number from 1 to 10.")
        priority = self.priority.value.strip().lower()
        if priority not in PRIORITIES:
            raise InvalidInput("Default priority must be low, normal, high or urgent.")
        naming = clean_text(self.naming.value, 60, required=True, what="channel name format")
        await self.d.cog.save_config(self.d.guild_id, naming=naming, close_delay_seconds=clamp_delay(self.delay.value.strip()),
                                     ticket_limit=int(limit), transcripts_enabled=parse_bool(self.transcripts.value), default_priority=priority)
        await self.d.after_edit(interaction)


class MessagesModal(_DesignModal):
    def __init__(self, d, cfg) -> None:
        super().__init__(d, "Ticket messages")
        self.opening = _input("Opening message ({user} {username} {server})", cfg["opening_message"] or "", long=True, limit=1000, placeholder=DEFAULT_OPENING)
        self.instructions = _input("Extra instructions (empty = none)", cfg["instructions"] or "", long=True, limit=900)
        self.add_item(self.opening)
        self.add_item(self.instructions)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.d.cog.save_config(self.d.guild_id, opening_message=clean_text(self.opening.value, 1000), instructions=clean_text(self.instructions.value, 900))
        await self.d.after_edit(interaction)


class CategoryModal(_DesignModal):
    def __init__(self, d, manager: "CategoryManager", row=None) -> None:
        super().__init__(d, "Edit category" if row else "Add category")
        self.manager, self.row = manager, row
        self.name = _input("Name", row["name"] if row else "", required=True, limit=100)
        self.description = _input("Description (shown in the menu)", (row["description"] or "") if row else "", limit=100)
        self.emoji = _input("Emoji (empty = default)", (row["emoji"] or "") if row else "", limit=60)
        for item in (self.name, self.description, self.emoji):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = clean_text(self.name.value, 100, required=True, what="name")
        description, emoji = clean_text(self.description.value, 100), valid_emoji(self.emoji.value)
        db = self.d.cog.bot.db
        if self.row:
            await db.execute("UPDATE ticket_categories SET name=$2, description=$3, emoji=$4 WHERE id=$1", self.row["id"], name, description, emoji)
        else:
            if len(await self.d.cog.categories(self.d.panel_id)) >= 25:
                raise InvalidInput("A panel can have up to 25 categories.")
            new_id = await db.fetchval("INSERT INTO ticket_categories (panel_id, name, description, emoji, position) VALUES ($1,$2,$3,$4,"
                                       "COALESCE((SELECT MAX(position) + 1 FROM ticket_categories WHERE panel_id = $1), 0)) RETURNING id", self.d.panel_id, name, description, emoji)
            self.manager.selected = new_id
        await self.manager.render(interaction, refresh_live=True)


class CategoryManager(discord.ui.View):
    """Add, edit, reorder, disable and remove a panel's categories."""

    def __init__(self, designer: "TicketDesigner") -> None:
        super().__init__(timeout=900)
        self.d, self.selected = designer, None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.d.interaction_check(interaction)

    async def render(self, interaction: discord.Interaction, *, refresh_live: bool = False) -> None:
        cog = self.d.cog
        cats = await cog.categories(self.d.panel_id)
        if refresh_live:
            await cog.refresh_live_panel(interaction.guild, self.d.panel_id)
        if self.selected not in [c["id"] for c in cats]:
            self.selected = cats[0]["id"] if cats else None
        self.clear_items()
        if cats:
            select = discord.ui.Select(placeholder="Choose a category to edit", options=[
                discord.SelectOption(label=f"{i + 1}. {c['name']}"[:100], value=str(c["id"]), description=("hidden from the menu" if not c["enabled"] else None),
                                     default=c["id"] == self.selected) for i, c in enumerate(cats)])
            select.callback = self._chosen
            self.select = select
            self.add_item(select)
        for label, style, handler, row in (("Add", discord.ButtonStyle.success, self._add, 1), ("Edit", discord.ButtonStyle.secondary, self._edit, 1),
                                           ("Up", discord.ButtonStyle.secondary, self._up, 1), ("Down", discord.ButtonStyle.secondary, self._down, 1),
                                           ("On/Off", discord.ButtonStyle.secondary, self._toggle, 2), ("Remove", discord.ButtonStyle.danger, self._remove, 2),
                                           ("Back", discord.ButtonStyle.primary, self._back, 2)):
            button = discord.ui.Button(label=label, style=style, row=row, disabled=(not cats and label not in ("Add", "Back")))
            button.callback = handler
            self.add_item(button)
        lines = [f"{'▸' if c['id'] == self.selected else '•'} **{plain(c['name'], 60)}**" + ("" if c["enabled"] else " (off)") + (f": {plain(c['description'], 60)}" if c["description"] else "") for c in cats]
        embed = system_embed(SYSTEM, "Categories", "\n".join(lines) or "No categories yet. Add one.")
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def _row(self):
        cats = await self.d.cog.categories(self.d.panel_id)
        return next((c for c in cats if c["id"] == self.selected), None), cats

    async def _chosen(self, interaction: discord.Interaction) -> None:
        self.selected = int(self.select.values[0])
        await self.render(interaction)

    async def _add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CategoryModal(self.d, self))

    async def _edit(self, interaction: discord.Interaction) -> None:
        row, _ = await self._row()
        await interaction.response.send_modal(CategoryModal(self.d, self, row))

    async def _reorder(self, interaction: discord.Interaction, step: int) -> None:
        row, cats = await self._row()
        order = move([c["id"] for c in cats], row["id"], step)
        for position, category_id in enumerate(order):
            await self.d.cog.bot.db.execute("UPDATE ticket_categories SET position = $2 WHERE id = $1", category_id, position)
        await self.render(interaction, refresh_live=True)

    async def _up(self, interaction: discord.Interaction) -> None:
        await self._reorder(interaction, -1)

    async def _down(self, interaction: discord.Interaction) -> None:
        await self._reorder(interaction, 1)

    async def _toggle(self, interaction: discord.Interaction) -> None:
        row, _ = await self._row()
        await self.d.cog.bot.db.execute("UPDATE ticket_categories SET enabled = NOT enabled WHERE id = $1", row["id"])
        await self.render(interaction, refresh_live=True)

    async def _remove(self, interaction: discord.Interaction) -> None:
        row, _ = await self._row()
        await self.d.cog.bot.db.execute("DELETE FROM ticket_categories WHERE id = $1", row["id"])
        await self.render(interaction, refresh_live=True)

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=await self.d.summary(), view=self.d)


class PickerView(discord.ui.View):
    """A single role or channel picker with Back (and optionally Clear)."""

    def __init__(self, designer: "TicketDesigner", select: discord.ui.Select, save, clear=None) -> None:
        super().__init__(timeout=600)
        self.d, self.select, self.save = designer, select, save
        select.callback = self._picked
        self.add_item(select)
        if clear:
            button = discord.ui.Button(label="Clear", style=discord.ButtonStyle.secondary)

            async def cleared(interaction: discord.Interaction) -> None:
                await clear()
                await self.d.after_edit(interaction)

            button.callback = cleared
            self.add_item(button)
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.primary)
        back.callback = self._back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.d.interaction_check(interaction)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await self.save(self.select.values)
        await self.d.after_edit(interaction)

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=await self.d.summary(), view=self.d)


class TicketDesigner(discord.ui.View):
    def __init__(self, cog: Tickets, user_id: int, guild_id: int, panel_id: int) -> None:
        super().__init__(timeout=900)
        self.cog, self.user_id, self.guild_id, self.panel_id = cog, user_id, guild_id, panel_id
        self.menu = discord.ui.Select(placeholder="What do you want to edit?", options=[discord.SelectOption(label=label, value=key) for key, label in TOPICS])
        self.menu.callback = self._pick
        self.add_item(self.menu)
        for label, style, handler in (("Preview", discord.ButtonStyle.secondary, self._preview), ("Post or update here", discord.ButtonStyle.success, self._publish),
                                      ("Done", discord.ButtonStyle.primary, self._done)):
            button = discord.ui.Button(label=label, style=style)
            button.callback = handler
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id and can_manage_server(self.cog.bot, interaction.user):  # re-checked on every press
            return True
        await interaction.response.send_message(embed=error_embed("This designer belongs to someone else, or you no longer have Manage Server.", title="Permission denied"), ephemeral=True)
        return False

    async def summary(self) -> discord.Embed:
        panel = await self.cog.get_panel(self.guild_id, self.panel_id)
        cfg = await self.cog.ensure_config(self.guild_id)
        cats = await self.cog.categories(self.panel_id)
        roles = " ".join(f"<@&{i}>" for i in sorted(self.cog.staff_roles(cfg))) or "none yet"
        embed = system_embed(SYSTEM, f"Ticket designer: {plain(panel['name'], 40)}", "Pick something to edit. Changes are saved as you go and the posted panel updates by itself.",
                             color=panel["color"] if panel["color"] is not None else BRAND)
        embed.add_field(name="Panel", value=f"**{plain(panel['title'], 60)}**\nButton: {plain(panel['button_label'], 40)} ({panel['button_style']})\nMenu: {plain(panel['select_placeholder'], 40)}")
        embed.add_field(name="Categories", value=", ".join(plain(c["name"], 24) + ("" if c["enabled"] else " (off)") for c in cats)[:900] or "none")
        embed.add_field(name="Staff roles", value=roles, inline=False)
        embed.add_field(name="Tickets", value=f"Category: {'<#%d>' % cfg['category_id'] if cfg['category_id'] else 'top of the channel list'}\n"
                                              f"Transcripts: {'<#%d>' % cfg['transcript_channel_id'] if cfg['transcript_channel_id'] else ('ticket-logs' if cfg['transcripts_enabled'] else 'off')}\n"
                                              f"Closes after {cfg['close_delay_seconds']}s • limit {cfg['ticket_limit']} per member • names `{cfg['naming']}`")
        embed.add_field(name="Posted", value=(f"<#{panel['channel_id']}>" if panel["channel_id"] else "not posted yet"), inline=False)
        return embed

    async def after_edit(self, interaction: discord.Interaction) -> None:
        await self.cog.refresh_live_panel(interaction.guild, self.panel_id)
        embed = await self.summary()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def _pick(self, interaction: discord.Interaction) -> None:
        key = self.menu.values[0]
        cog = self.cog
        panel, cfg = await cog.get_panel(self.guild_id, self.panel_id), await cog.ensure_config(self.guild_id)
        if key == "text":
            await interaction.response.send_modal(TextModal(self, panel))
        elif key == "images":
            await interaction.response.send_modal(ImagesModal(self, panel))
        elif key == "button":
            await interaction.response.send_modal(ButtonModal(self, panel))
        elif key == "behaviour":
            await interaction.response.send_modal(BehaviourModal(self, cfg))
        elif key == "messages":
            await interaction.response.send_modal(MessagesModal(self, cfg))
        elif key == "categories":
            await CategoryManager(self).render(interaction)
        elif key == "staff":
            async def save_roles(values):
                await cog.save_config(self.guild_id, staff_role_ids=sorted({r.id for r in values if not r.is_default()}), support_role_id=None)
            select = discord.ui.RoleSelect(placeholder="Choose up to 5 staff roles", min_values=1, max_values=5)
            await interaction.response.edit_message(embed=system_embed(SYSTEM, "Staff roles", "Members with these roles can claim, close and read tickets. Server managers always can."),
                                                    view=PickerView(self, select, save_roles))
        elif key == "support":
            async def save_cat(values):
                await cog.save_config(self.guild_id, category_id=values[0].id)

            async def clear_cat():
                await cog.save_config(self.guild_id, category_id=None)
            select = discord.ui.ChannelSelect(placeholder="Choose the category for ticket channels", channel_types=[discord.ChannelType.category], max_values=1)
            await interaction.response.edit_message(embed=system_embed(SYSTEM, "Ticket category", "New ticket channels are created inside this category."),
                                                    view=PickerView(self, select, save_cat, clear_cat))
        elif key == "transcripts":
            async def save_dest(values):
                await cog.save_config(self.guild_id, transcript_channel_id=values[0].id, transcripts_enabled=True)

            async def clear_dest():
                await cog.save_config(self.guild_id, transcript_channel_id=None)
            select = discord.ui.ChannelSelect(placeholder="Choose where transcripts are saved", channel_types=[discord.ChannelType.text], max_values=1)
            await interaction.response.edit_message(embed=system_embed(SYSTEM, "Transcript destination", "Transcripts go to this channel just before a closed ticket is deleted. "
                                                                       "Clear it to use the private ticket-logs channel instead."), view=PickerView(self, select, save_dest, clear_dest))

    async def _preview(self, interaction: discord.Interaction) -> None:
        panel = await self.cog.get_panel(self.guild_id, self.panel_id)
        embed, view = self.cog.render_panel(panel, await self.cog.categories(self.panel_id, enabled_only=True))
        for child in view.children:
            getattr(child, "item", child).disabled = True
        await interaction.response.send_message(content="**Preview.** Nothing here is clickable.", embed=embed, view=view, ephemeral=True)

    async def _publish(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(embed=error_embed("Open the designer in a text channel to post the panel there."), ephemeral=True)
            return
        panel = await self.cog.get_panel(self.guild_id, self.panel_id)
        try:
            await self.cog.publish_panel(interaction.guild, panel, interaction.channel)
        except discord.HTTPException:
            await interaction.response.send_message(embed=error_embed("I couldn't post in this channel. Check my permissions there."), ephemeral=True)
            return
        await self.after_edit(interaction)

    async def _done(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=await self.summary(), view=self)
        self.stop()


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Tickets(bot))

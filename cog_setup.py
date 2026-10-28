"""Interactive setup panel and server templates.

Opening the panel changes nothing. Every action shows a preview first and needs an explicit confirmation.
Templates only ever add or (optionally) update topics; they never delete channels, roles or settings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

import core_branding as branding
from core_checks import ensure_security_admin
from core_embeds import error_embed, info_embed, ok_embed
from core_errors import InvalidInput
from core_server_templates import BUILTIN, PlannedAction, limits_problem, plan_template, slug, validate_template
from view_confirm import ask, ask_interaction

if TYPE_CHECKING:  # pragma: no cover
    from bot import HopelessBot

log = logging.getLogger("hopeless.setup")
NAME_RE = re.compile(r"^[\w .-]{1,32}$")
MAX_SAVED = 25


def serialize_guild(guild: discord.Guild) -> dict:
    roles = [{"name": r.name, "color": r.colour.value, "permissions": r.permissions.value, "hoist": r.hoist, "mentionable": r.mentionable}
             for r in reversed(guild.roles[1:]) if not r.managed][:100]
    categories = []
    for cat in guild.categories:
        channels = []
        for ch in cat.channels:
            if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
                continue
            overwrites = []
            for target, ow in ch.overwrites.items():
                if isinstance(target, discord.Role):
                    allow, deny = ow.pair()
                    overwrites.append({"role": "@everyone" if target.is_default() else target.name, "allow": allow.value, "deny": deny.value})
            channels.append({"name": ch.name, "type": "text" if isinstance(ch, discord.TextChannel) else "voice",
                             "topic": getattr(ch, "topic", "") or "", "nsfw": getattr(ch, "nsfw", False),
                             "slowmode": getattr(ch, "slowmode_delay", 0), "readonly": False, "staff_only": False, "overwrites": overwrites})
        categories.append({"name": cat.name, "channels": channels[:50]})
    return {"roles": roles, "categories": categories[:50]}


class SetupView(discord.ui.View):
    def __init__(self, cog: "Setup", user_id: int) -> None:
        super().__init__(timeout=300)
        self.cog, self.user_id = cog, user_id
        select = discord.ui.Select(placeholder="Choose what to set up", options=[
            discord.SelectOption(label=label, value=value, description=desc)
            for value, label, desc in (
                ("logging", "Logging", "Private Logs category with a channel for each kind of log"),
                ("antinuke", "Antinuke", "Turn on all protections with safe defaults"),
                ("automod", "Automod", "Link, mention, spam and raid defaults"),
                ("tickets", "Tickets", "Category, support role and panel"),
                ("voice", "Voice", "Join channel, interface channel and control panel"),
                ("all", "All supported modules", "One preview, one confirmation"),
            )])
        select.callback = self._picked
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This setup panel belongs to someone else. Run `/setup` yourself.", ephemeral=True)
        return False

    async def _picked(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.cog.run_setup(interaction, self.select.values[0])


class Setup(commands.Cog):
    def __init__(self, bot: "HopelessBot") -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ensure_security_admin(ctx)

    # --- module adapters: (title, plan, apply) -----------------------------------------------
    def _adapters(self, guild: discord.Guild) -> dict[str, tuple[str, Callable[[], Awaitable[list[str]]], Callable[[], Awaitable[list[str]]]]]:
        g = guild
        logs, an, am = self.bot.get_cog("Logging"), self.bot.get_cog("Antinuke"), self.bot.get_cog("Automod")
        tk, vc = self.bot.get_cog("Tickets"), self.bot.get_cog("Voice")

        async def sync(fn, *a):
            return fn(*a)

        async def tk_plan():
            return tk.plan_quick(g, await tk.config(g.id))

        return {
            "logging": ("Logging", lambda: sync(logs.plan_setup, g), lambda: logs.apply_setup(g)),
            "antinuke": ("Antinuke", lambda: sync(an.plan_setup, g), lambda: an.apply_setup(g)),
            "automod": ("Automod", lambda: sync(am.plan_defaults, g), lambda: am.apply_defaults(g)),
            "tickets": ("Tickets", tk_plan, lambda: tk.apply_quick(g)),
            "voice": ("Voice", lambda: sync(vc.plan_jtc, g), lambda: vc.apply_jtc(g)),
        }

    async def run_setup(self, interaction: discord.Interaction, choice: str) -> None:
        adapters = self._adapters(interaction.guild)
        keys = list(adapters) if choice == "all" else [choice]
        sections = []
        for key in keys:
            title, plan, _ = adapters[key]
            sections.append(f"**{title}**\n" + "\n".join(f"• {line}" for line in await plan()))
        embed = info_embed("Preview: nothing has been changed yet", "\n\n".join(sections) + "\n\nWelcome messages and Prestige need your own choices, so they aren't included here.")
        if await ask_interaction(interaction, embed) != 0:
            await interaction.followup.send(embed=info_embed(None, "Cancelled. Nothing was changed."), ephemeral=True)
            return
        results = []
        for key in keys:
            title, _, apply = adapters[key]
            try:
                results.append(f"**{title}**: " + "; ".join(await apply()))
            except Exception:
                log.warning("setup step %s failed", key, exc_info=True)
                results.append(f"**{title}**: failed, nothing was reported as done")
        await interaction.followup.send(embed=ok_embed("\n".join(results)), ephemeral=True)

    @commands.hybrid_command(name="setup", description="Open the interactive setup panel")
    async def setup_panel(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title=f"{self.bot.icons.get('setup')} Setup",
            description="Pick a module below. I'll show exactly what I would create or change and wait for your confirmation. Opening this panel changes nothing.",
            color=branding.ACCENT_COLOR,
        )
        await ctx.send(embed=embed, view=SetupView(self, ctx.author.id))

    # --- templates ---------------------------------------------------------------------------
    async def _find_template(self, user_id: int, name: str) -> tuple[str, dict]:
        key = name.strip().lower()
        if key in BUILTIN:
            return BUILTIN[key]["label"], BUILTIN[key]
        row = await self.bot.db.fetchrow("SELECT name, data FROM templates WHERE owner_user_id = $1 AND lower(name) = $2", user_id, key)
        if row is None:
            raise InvalidInput("I don't know that template. Run `template list` to see the choices.")
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        return row["name"], validate_template(data)

    @staticmethod
    def _existing(guild: discord.Guild):
        roles = {r.name.lower() for r in guild.roles}
        cats = {c.name.lower() for c in guild.categories}
        chans = {((c.category.name.lower() if c.category else ""), slug(c.name)) for c in guild.channels if not isinstance(c, discord.CategoryChannel)}
        return roles, cats, chans

    @commands.hybrid_group(name="template", fallback="list", description="List built-in and saved templates")
    async def template(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.fetch("SELECT name FROM templates WHERE owner_user_id = $1 ORDER BY name", ctx.author.id)
        built = "\n".join(f"• `{key}`: {t['label']}" for key, t in BUILTIN.items())
        saved = "\n".join(f"• `{r['name']}`" for r in rows) or "None yet. Save this server with `template save <name>`."
        await ctx.send(embed=info_embed("Templates", f"**Built in**\n{built}\n\n**Yours**\n{saved}"))

    @template.command(name="save", description="Save this server's roles and channels as a template")
    @app_commands.describe(name="A name for the template")
    async def template_save(self, ctx: commands.Context, *, name: str) -> None:
        name = name.strip()
        if not NAME_RE.match(name) or name.lower() in BUILTIN:
            raise InvalidInput("Pick a name of 1-32 letters, numbers, spaces, dots or dashes that isn't a built-in name.")
        count = await self.bot.db.fetchval("SELECT COUNT(*) FROM templates WHERE owner_user_id = $1", ctx.author.id)
        exists = await self.bot.db.fetchval("SELECT 1 FROM templates WHERE owner_user_id = $1 AND lower(name) = lower($2)", ctx.author.id, name)
        if count >= MAX_SAVED and not exists:
            raise InvalidInput(f"You can keep {MAX_SAVED} templates. Overwrite one by reusing its name.")
        data = serialize_guild(ctx.guild)
        await self.bot.db.execute(
            "INSERT INTO templates (owner_user_id, name, data) VALUES ($1, $2, $3::jsonb) "
            "ON CONFLICT (owner_user_id, name) DO UPDATE SET data = EXCLUDED.data, created_at = now()", ctx.author.id, name, json.dumps(data))
        await ctx.send(embed=ok_embed(f"Saved **{name}**: {len(data['roles'])} roles and {sum(len(c['channels']) for c in data['categories'])} channels in {len(data['categories'])} categories. "
                                      "Messages, members and uncategorised channels aren't included."))

    @template.command(name="apply", description="Preview and apply a template to this server")
    @app_commands.describe(name="A built-in or saved template")
    async def template_apply(self, ctx: commands.Context, *, name: str) -> None:
        label, data = await self._find_template(ctx.author.id, name)
        roles, cats, chans = self._existing(ctx.guild)
        actions = plan_template(data, roles=roles, categories=cats, channels=chans)
        problem = limits_problem(actions, role_count=len(ctx.guild.roles), channel_count=len(ctx.guild.channels))
        if problem:
            raise InvalidInput(problem)
        new = [a for a in actions if not a.exists]
        if not new:
            await ctx.send(embed=info_embed(label, "Everything in this template already exists here. Nothing to do."))
            return
        shown = "\n".join(f"• {a.line}" for a in new[:20]) + (f"\n…and {len(new) - 20} more" if len(new) > 20 else "")
        embed = info_embed(f"Apply {label}?", f"{len(new)} new item(s), {len(actions) - len(new)} already exist and are kept.\n\n{shown}\n\n"
                                              "Nothing is ever deleted. Choose how to treat things that already exist.")
        choice = await ask(ctx, embed, labels=("Add missing only", "Add missing + update topics", "Cancel"))
        if choice is None or choice == 2:
            await ctx.send(embed=info_embed(None, "Cancelled. Nothing was changed."))
            return
        await ctx.send(embed=info_embed(None, f"Applying {len(new)} item(s). I pace myself for Discord's rate limits, so this can take a minute."))
        results = await self.apply_template(ctx.guild, data, actions, update_topics=choice == 1)
        await ctx.channel.send(embed=ok_embed(results))

    async def apply_template(self, guild: discord.Guild, data: dict, actions: list[PlannedAction], *, update_topics: bool) -> str:
        created = failed = kept = 0
        role_map = {r.name.lower(): r for r in guild.roles}
        for spec in data.get("roles", []):
            if spec["name"].lower() in role_map:
                kept += 1
                continue
            try:
                role_map[spec["name"].lower()] = await guild.create_role(
                    name=spec["name"], colour=discord.Colour(spec.get("color", 0)), permissions=discord.Permissions(spec.get("permissions", 0)),
                    hoist=spec.get("hoist", False), mentionable=spec.get("mentionable", False), reason="HOPELESS template")
                created += 1
            except discord.HTTPException:
                failed += 1
            await asyncio.sleep(0.4)
        cat_map = {c.name.lower(): c for c in guild.categories}
        for cat in data.get("categories", []):
            category = cat_map.get(cat["name"].lower())
            if category is None:
                try:
                    category = cat_map[cat["name"].lower()] = await guild.create_category(cat["name"], reason="HOPELESS template")
                    created += 1
                except discord.HTTPException:
                    failed += 1
                    continue
                await asyncio.sleep(0.4)
            else:
                kept += 1
            existing = {slug(c.name): c for c in category.channels}
            for spec in cat.get("channels", []):
                found = existing.get(slug(spec["name"]))
                if found is not None:
                    kept += 1
                    if update_topics and isinstance(found, discord.TextChannel) and spec.get("topic") and found.topic != spec["topic"]:
                        try:
                            await found.edit(topic=spec["topic"], reason="HOPELESS template")
                        except discord.HTTPException:
                            failed += 1
                    continue
                overwrites: dict = {}
                staff = role_map.get("staff")
                if spec.get("staff_only"):
                    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                    if staff:
                        overwrites[staff] = discord.PermissionOverwrite(view_channel=True)
                if spec.get("readonly"):
                    overwrites[guild.default_role] = discord.PermissionOverwrite(send_messages=False)
                for ow in spec.get("overwrites", []):
                    role = guild.default_role if ow["role"] == "@everyone" else role_map.get(ow["role"].lower())
                    if role is not None:
                        overwrites[role] = discord.PermissionOverwrite.from_pair(discord.Permissions(ow["allow"]), discord.Permissions(ow["deny"]))
                try:
                    if spec["type"] == "voice":
                        await guild.create_voice_channel(spec["name"], category=category, overwrites=overwrites, reason="HOPELESS template")
                    else:
                        await guild.create_text_channel(spec["name"], category=category, topic=spec.get("topic") or None, nsfw=bool(spec.get("nsfw")),
                                                        slowmode_delay=spec.get("slowmode") or 0, overwrites=overwrites, reason="HOPELESS template")
                    created += 1
                except discord.HTTPException:
                    failed += 1
                await asyncio.sleep(0.4)
        return f"Template applied: {created} created, {kept} already existed, {failed} failed." + (" Check my permissions and the server limits for the failures." if failed else "")


async def setup(bot: "HopelessBot") -> None:
    await bot.add_cog(Setup(bot))

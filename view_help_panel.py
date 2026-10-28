"""Help panel: embeds plus components that keep working after a restart.

Components are DynamicItems whose custom_id carries the id of the member who opened the panel
("hopeless:help:nav:<user id>"). That lets any restarted bot recognise the buttons without
stored state, and lets us tell other members the menu isn't theirs.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

import core_branding as branding
from core_command_registry import CommandSpec, registry
from core_emoji import EmojiResolver
from core_formatting import chunk_lines
from core_invite import invite_url

log = logging.getLogger("hopeless.help")

HOME = "home"


# --- embeds ------------------------------------------------------------------------------------
def home_embed(*, icons: EmojiResolver, prefix: str) -> discord.Embed:
    bullet = icons.get("bullet")  # the red chevron
    lines = [
        branding.BOT_DESCRIPTION,
        "",
        "**Quick facts**",
        f"{bullet} **Prefix** `{prefix}`",
        f"{bullet} **Commands** `{registry.public_command_count()}`",
        f"{bullet} **Modules** `{len(registry.visible_modules())}`",
        "",
        f"-# Use `{prefix}help <command>` for details • prefix commands look like `{prefix}help`",
    ]
    embed = discord.Embed(
        title=f"{icons.get('help')} help", description="\n".join(lines), color=branding.ACCENT_COLOR
    )
    if branding.has_banner():
        embed.set_image(url=branding.BANNER_ATTACHMENT_URL)
    return embed


def module_embed(key: str, *, icons: EmojiResolver, prefix: str) -> discord.Embed:
    module = registry.module(key)
    embed = discord.Embed(
        title=f"{icons.get(key)} {module.title}", description=module.summary, color=branding.ACCENT_COLOR
    )
    specs = [s for s in registry.commands(key, implemented_only=True) if s.name]
    if specs:
        lines = []
        for spec in specs:
            if spec.name:  # prefix commands only: slash versions still work but aren't advertised
                lines.append(f"`{prefix}{spec.name}` — {spec.description}")
        for index, chunk in enumerate(chunk_lines(lines)):
            title = f"Commands ({len(lines)})" if index == 0 else "Commands (continued)"
            embed.add_field(name=title, value=chunk, inline=False)
    else:
        embed.add_field(name="Commands", value="None yet. This module is still being built.", inline=False)
    embed.add_field(name="Required permissions", value=module.permissions or "None.", inline=False)
    embed.set_footer(text=f"{branding.BOT_NAME} • {prefix}help <command> for details")
    if branding.has_banner():
        embed.set_thumbnail(url=branding.BANNER_ATTACHMENT_URL)
    return embed


def is_global_staff(bot, user_id: int) -> bool:
    """Global staff (and the owner) may see the restricted 'Global administration' pages."""
    return bot.permissions.is_owner(user_id) or bot.permissions.global_role(user_id) is not None


def who_can_use(spec: CommandSpec) -> str:
    """The real rule from the permission matrix, in words (the same rule the command enforces)."""
    from core_authz import Level  # local import keeps this module light for tests that don't need it
    from core_permission_matrix import rule_for
    rule = rule_for(spec)
    pretty = (rule.needs or "").replace("_", " ").title()
    if rule.level is Level.MODERATOR:
        return f"Moderators: needs the **{pretty}** permission (and a moderator role, if the server set any). Administrators always can."
    if rule.level is Level.GLOBAL_PERM:
        return f"Global staff holding the `{rule.needs}` permission. The bot owner always can."
    return rule.level.value.capitalize()


def command_embed(spec: CommandSpec, *, icons: EmojiResolver, prefix: str, usage: Optional[str] = None) -> discord.Embed:
    module = registry.module(spec.module)
    title = f"{prefix}{spec.name or spec.slash}"
    embed = discord.Embed(title=title, description=spec.description, color=branding.ACCENT_COLOR)
    embed.add_field(name="Module", value=f"{icons.get(module.key)} {module.title}")
    embed.add_field(name="Who can use it", value=who_can_use(spec), inline=False)
    if usage:
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
    if spec.slash:
        embed.add_field(name="Slash command", value=f"`/{spec.slash}`")
    if spec.aliases:
        embed.add_field(name="Aliases", value=", ".join(f"`{prefix}{a}`" for a in spec.aliases), inline=False)
    return embed


# --- page rendering ----------------------------------------------------------------------------
def render(bot, *, owner_id: int, guild_id: Optional[int], key: str) -> tuple[discord.Embed, discord.ui.View]:
    prefix = bot.prefixes.get(guild_id)
    staff = is_global_staff(bot, owner_id)
    visible = {module.key for module in registry.visible_modules(include_restricted=staff)}  # enforced here, not just hidden
    if key == HOME or key not in visible:
        key = HOME
        embed = home_embed(icons=bot.icons, prefix=prefix)
    else:
        embed = module_embed(key, icons=bot.icons, prefix=prefix)
    return embed, build_view(bot, owner_id=owner_id, current=key)


def build_view(bot, *, owner_id: int, current: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(HelpSelect(owner_id, icons=bot.icons, current=current, staff=is_global_staff(bot, owner_id)))
    view.add_item(HelpHomeButton(owner_id, icons=bot.icons))
    if bot.user is not None:
        view.add_item(discord.ui.Button(label="Invite", url=invite_url(bot.user.id)))
    if bot.settings.support_url:
        view.add_item(discord.ui.Button(label="Support", url=bot.settings.support_url))
    view.add_item(HelpCloseButton(owner_id))
    return view


async def _only_owner(interaction: discord.Interaction, owner_id: int) -> bool:
    if interaction.user.id == owner_id:
        return True
    await interaction.response.send_message(
        "This help menu belongs to someone else. Run the help command to open your own.", ephemeral=True
    )
    return False


async def navigate(interaction: discord.Interaction, owner_id: int, key: str) -> None:
    embed, view = render(interaction.client, owner_id=owner_id, guild_id=interaction.guild_id, key=key)
    try:
        await interaction.response.edit_message(embed=embed, view=view)
    except (discord.NotFound, discord.InteractionResponded):
        log.debug("help interaction expired or was already answered")


# --- components --------------------------------------------------------------------------------
class HelpSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"hopeless:help:nav:(?P<uid>[0-9]+)"):
    def __init__(self, owner_id: int, *, icons: EmojiResolver, current: str = HOME, staff: bool = False) -> None:
        options = [
            discord.SelectOption(
                label=module.title,
                value=module.key,
                description=module.blurb[:100],
                emoji=icons.component(module.key),
                default=module.key == current,
            )
            for module in registry.visible_modules(include_restricted=staff)
        ]
        super().__init__(
            discord.ui.Select(
                custom_id=f"hopeless:help:nav:{owner_id}", placeholder="Choose a module", options=options
            )
        )
        self.owner_id = owner_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match, /):
        return cls(int(match["uid"]), icons=interaction.client.icons, staff=is_global_staff(interaction.client, int(match["uid"])))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _only_owner(interaction, self.owner_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        await navigate(interaction, self.owner_id, self.item.values[0])


class HelpHomeButton(discord.ui.DynamicItem[discord.ui.Button], template=r"hopeless:help:home:(?P<uid>[0-9]+)"):
    def __init__(self, owner_id: int, *, icons: EmojiResolver) -> None:
        super().__init__(
            discord.ui.Button(
                label="Home",
                style=discord.ButtonStyle.secondary,
                custom_id=f"hopeless:help:home:{owner_id}",
                emoji=icons.component("home"),
            )
        )
        self.owner_id = owner_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match, /):
        return cls(int(match["uid"]), icons=interaction.client.icons)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _only_owner(interaction, self.owner_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        await navigate(interaction, self.owner_id, HOME)


class HelpCloseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"hopeless:help:close:(?P<uid>[0-9]+)"):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Close", style=discord.ButtonStyle.danger, custom_id=f"hopeless:help:close:{owner_id}"
            )
        )
        self.owner_id = owner_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match, /):
        return cls(int(match["uid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _only_owner(interaction, self.owner_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()
            if interaction.message is not None:
                await interaction.message.delete()
        except discord.HTTPException:
            log.debug("could not close help message")


DYNAMIC_ITEMS = (HelpSelect, HelpHomeButton, HelpCloseButton)

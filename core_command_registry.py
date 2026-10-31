"""Single source of truth for modules and commands.

* The help panel, command counts and module pages are generated from here, never hardcoded.
* Only commands marked IMPLEMENTED are shown or counted, so the bot never advertises something
  that does not exist. HopelessBot.registry_problems() checks every IMPLEMENTED entry against the
  commands that are really loaded.
* `reference` is the command's name in the reference material. It is internal documentation only
  and must never be shown to users (a test enforces that no user-facing text leaks old names).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(str, Enum):
    IMPLEMENTED = "implemented"
    PLANNED = "planned"


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    title: str
    blurb: str  # one line in the dropdown (max 100 chars)
    summary: str  # what the module does, shown on its page
    permissions: str  # required permissions, shown on its page
    hidden: bool = False


@dataclass(frozen=True)
class CommandSpec:
    module: str
    reference: str  # internal only
    name: Optional[str]  # prefix command incl. subcommand, without the prefix; None = slash only
    slash: Optional[str]  # slash path such as "whitelist add"; None = prefix only
    aliases: tuple[str, ...]
    permission: str
    description: str
    status: Status

    @property
    def implemented(self) -> bool:
        return self.status is Status.IMPLEMENTED

    @property
    def label(self) -> str:
        return self.name or f"/{self.slash}"


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        "home", "Home", "Return to the help home page",
        "Browse every available module, see this server's prefix, and look up any command.",
        "No special permissions.",
    ),
    ModuleSpec(
        "antinuke", "Antinuke", "Anti-nuke protection, punishments, and setup",
        "Audit-log based detection of destructive changes, with per-module toggles, thresholds, "
        "whitelists, snapshots and configurable responses.",
        "You: Administrator or server owner. Me: View Audit Log, Manage Roles, Manage Channels, "
        "Manage Webhooks, Kick Members, Ban Members.",
    ),
    ModuleSpec(
        "whitelist", "Whitelist", "Trusted users and roles that bypass security, entirely or per-module",
        "Exempt trusted users and roles from one protection or from a whole area (antinuke or automod). "
        "An exemption in one area never switches off another.",
        "You: Administrator or server owner. Me: nothing special.",
    ),
    ModuleSpec(
        "moderation", "Moderation", "Ban, kick, mute, jail, and server management",
        "Moderation actions with role-hierarchy checks, temporary jail, channel locks, purges, role tools "
        "and the per-server prefix.",
        "You: the Discord permission each action needs (for example Ban Members). "
        "Me: the same permission and a role above the target.",
    ),
    ModuleSpec(
        "utility", "Utility", "Information, snipes, AFK, and general tools",
        "Profile and server information, deleted and edited message snipes with short retention, "
        "AFK status and bot statistics.",
        "Most commands need nothing. Clearing snipes needs Manage Messages. Me: Embed Links.",
    ),
    ModuleSpec(
        "automod", "Automod", "Anti-link, anti-spam, anti-raid, and more",
        "Rule-based filtering for links, mentions, spam and raids, each with thresholds, exemptions "
        "and a configurable response.",
        "You: Manage Server. Me: Manage Messages, Moderate Members.",
    ),
    ModuleSpec(
        "welcome", "Welcome", "Welcome messages, banners, and DMs for new members",
        "Welcome and leave messages with placeholders, optional images and direct messages, "
        "configured separately for every server.",
        "You: Manage Server. Me: Send Messages, Embed Links, Attach Files.",
    ),
    ModuleSpec(
        "prestige", "Prestige", "Per-server bot profile presentation, credits, and tag roles",
        "Prestige access for servers (granted with internal credits), per-server bot profile settings "
        "where Discord allows them, and tag roles.",
        "Activating and removing Prestige: bot owner or bot admin only. Bot banner, bio, name and tag roles: server owner.",
    ),
    ModuleSpec(
        "logging", "Logging", "Full event logging for your server",
        "Two standard channels, server-logs and security-logs, with per-event settings and no mention pings.",
        "You: Administrator or server owner. Me: Manage Channels, Send Messages, Embed Links.",
    ),
    ModuleSpec(
        "tickets", "Tickets", "Support ticket panel, per-ticket controls, and staff tools",
        "Private ticket channels with claim, priority, staff notes, transcripts, inactivity warnings "
        "and auto-close.",
        "You: Manage Server. Me: Manage Channels, Manage Roles, Send Messages.",
    ),
    ModuleSpec(
        "voice", "Voice", "Join-to-create temp voice channels with a full owner control panel",
        "Temporary voice rooms created when someone joins, with an owner control panel and automatic cleanup.",
        "You: Manage Channels for setup. Me: Manage Channels, Move Members.",
    ),
    ModuleSpec(
        "setup", "Setup", "Interactive setup panel and server templates",
        "Guided setup for each module and reusable server templates, always with a preview and an "
        "explicit confirmation.",
        "You: Administrator or server owner. Me: depends on what you set up.",
    ),
    ModuleSpec(
        "leveling", "Leveling", "XP, ranks, leaderboard and level role rewards",
        "Members earn XP for chatting (once a minute), level up, and can earn roles. The same tracker counts messages, "
        "which giveaways and polls can require.",
        "Most commands need nothing. Setup needs Manage Server. Me: Manage Roles for level roles.",
    ),
    ModuleSpec(
        "rep", "Reputation", "Roles for a status or server tag, removed again when they stop",
        "Give a role while a member's custom status contains your text or while they wear this server's tag, and take it "
        "away automatically when they stop. Status rep needs the Presence Intent.",
        "You: Manage Server. Me: Manage Roles.",
    ),
    ModuleSpec(
        "owners", "Extra Owners", "Trusted people with owner-level control of this server only",
        "Extra owners can manage this server's security and settings. They have no global power at all.",
        "Adding or removing: the server owner only.",
    ),
    ModuleSpec("owner", "Global administration", "Bot-wide tools for the bot owner and global staff",
               "Bot-wide powers: global bans, global staff permissions, the server list and the audit log. Server permissions never grant these.",
               "You: bot owner, or global staff holding the matching permission.", hidden=True),
)

# module | reference | name | slash | aliases | permission | description | status (I = implemented, P = planned)
# "-" means none. "new" in reference means the command has no counterpart in the reference material.
_TABLE = """
home | xhelp | help | help | - | None | Open the help panel or look up one command. | I

antinuke | xantinuke | antinuke | antinuke toggle | - | Administrator | Enable/disable anti-nuke, or view module status with no argument. | I
antinuke | xantinuke status | antinuke status | antinuke status | - | Administrator | Show the enabled/disabled state of every anti-nuke module. | I
antinuke | xantinuke module | antinuke module | antinuke module | - | Administrator | Toggle any of the 25 anti-nuke modules by name, e.g. antinuke module antiban on. | I
antinuke | xsetup | antinuke setup | antinuke setup | - | Administrator | One-click setup: enables every anti-nuke module with a shared rate limit (preview first). | I
antinuke | xantinukelog | antinukelog | - | - | Administrator | Set (or view) the channel anti-nuke alerts get posted to. | I
antinuke | xantiban | antiban | - | - | Administrator | Toggle the Anti Ban module. | I
antinuke | xantibotadd | antibotadd | - | - | Administrator | Toggle the Anti Bot Add module. | I
antinuke | xantichannelcreate | antichannelcreate | - | - | Administrator | Toggle the Anti Channel Create module. | I
antinuke | xantichanneldelete | antichanneldelete | - | - | Administrator | Toggle the Anti Channel Delete module (recreates deleted channels when triggered). | I
antinuke | xantichannelupdate | antichannelupdate | - | - | Administrator | Toggle the Anti Channel Update module. | I
antinuke | xantiguildupdate | antiguildupdate | - | - | Administrator | Toggle the Anti Guild Update module. | I
antinuke | xantikick | antikick | - | - | Administrator | Toggle the Anti Kick module. | I
antinuke | xantirolecreate | antirolecreate | - | - | Administrator | Toggle the Anti Role Create module. | I
antinuke | xantiroledelete | antiroledelete | - | - | Administrator | Toggle the Anti Role Delete module (recreates deleted roles when triggered). | I
antinuke | xantiroleupdate | antiroleupdate | - | - | Administrator | Toggle the Anti Role Update module. | I
antinuke | xantiwebhook | antiwebhook | - | - | Administrator | Toggle the Anti Webhook modules. | I

automod | xautomod | automod | automod | - | Manage Server | Show which automod rules are enabled. | I
automod | xantilink | antilink | antilink toggle | - | Manage Server | Toggle the link filter (no argument flips it). | I
automod | xantilink whitelist | antilink whitelist | antilink whitelist | - | Manage Server | Exempt a user, role, channel or domain from the link filter | I
automod | xantilink unwhitelist | antilink unwhitelist | antilink unwhitelist | - | Manage Server | Remove a link filter exemption | I
automod | xantimention | antimention | antimention | - | Manage Server | Toggle the mass-mention rule (no argument flips it). | I
automod | xantinsfw | antinsfw | antinsfw | - | Manage Server | Toggle the NSFW rule (image scanning isn't available; see the note it shows). | I
automod | xantiraid | antiraid | antiraid | - | Manage Server | Toggle raid detection (no argument flips it). | I
automod | xantispam | antispam | antispam | - | Manage Server | Toggle spam detection (no argument flips it). | I

moderation | xban | ban | ban | - | Ban Members | Ban a member | I
moderation | xkick | kick | kick | - | Kick Members | Kick a member | I
moderation | xsoftban | softban | softban | - | Ban Members | Ban then unban to clear recent messages | I
moderation | xtimeout | timeout | timeout | mute | Moderate Members | Time a member out | I
moderation | xunban | unban | unban | - | Ban Members | Unban a user | I
moderation | xunmute | unmute | unmute | untimeout | Moderate Members | Remove a member's timeout | I
moderation | xjail | jail | jail | - | Manage Roles | Jail a member, optionally for a limited time | I
moderation | xjlist | jlist | jlist | - | Manage Roles | List jailed members | I
moderation | xunjail | unjail | unjail | - | Manage Roles | Release a jailed member | I
moderation | xlock | lock | lock | - | Manage Channels | Lock a channel | I
moderation | unlock | unlock | unlock | - | Manage Channels | Unlock a channel | I
moderation | xpurge | purge | purge | clear | Manage Messages | Delete recent messages | I
moderation | xpurgeuser | purgeuser | purgeuser | - | Manage Messages | Delete recent messages from one user | I
moderation | xrole | role | role set | - | Manage Roles | Add or remove a role | I
moderation | new | role add | role add | - | Manage Roles | Give a member a role. | I
moderation | new | role remove | role remove | - | Manage Roles | Take a role from a member. | I
moderation | xroleall | roleall | roleall | ra | Manage Roles | Add or remove a role for everyone | I
moderation | xautorole | autorole | autorole | ar | Manage Roles | Choose roles given to new members | I
moderation | xsetprefix | setprefix | setprefix | - | Manage Server | Set this server's command prefix | I
moderation | xslowmode | slowmode | slowmode | - | Manage Channels | Set or clear channel slowmode | I

whitelist | whitelist | whitelist | whitelist add | wl | Administrator | Exempt a user or role from antinuke, or from a chosen scope | I
whitelist | whitelist list | whitelist list | whitelist list | - | Administrator | Show every whitelist entry | I
whitelist | new | whitelist remove | whitelist remove | - | Administrator | Remove a user or role from the whitelist | I
whitelist | whitelistremove | whitelistremove | - | wlr | Administrator | Shortcut for whitelist remove | I
whitelist | new | whitelist scopes | whitelist scopes | - | Administrator | List the scopes an exemption can cover | I

utility | xafk | afk | afk | - | None | Set your AFK status | I
utility | xavatar | avatar | avatar | av | None | Show a user's avatar | I
utility | xbanner | banner | banner | - | None | Show a user's profile banner | I
utility | xbotinfo | botinfo | botinfo | - | None | Show bot version, latency and uptime | I
utility | xbotinvite | botinvite | botinvite | - | None | Get the bot invite link | I
utility | xclearsnipe | clearsnipe | clearsnipe | - | Manage Messages | Clear stored snipes for this channel | I
utility | xeditsnipe | editsnipe | editsnipe | - | None | Show the last edited message in this channel | I
utility | xping | ping | ping | latency | None | Show gateway and database latency | I
utility | xserverinfo | serverinfo | serverinfo | si | None | Show information about this server | I
utility | snipe | snipe | snipe | - | None | Show the last deleted message in this channel | I
utility | xstats | stats | stats | - | None | Show usage statistics | I
utility | support | support | support | - | None | Get help or join the support server | I
utility | xtutorial | tutorial | tutorial | - | None | Learn how to use the bot | I
utility | xuptime | uptime | uptime | - | None | Show how long the bot has been running | I
utility | xuserinfo | userinfo | userinfo | ui, whois | None | Show information about a user | I

welcome | xsetwelcome | setwelcome | setwelcome show | - | Manage Server | Show the welcome configuration | I
welcome | xsetwelcome dm | setwelcome dm | setwelcome dm | - | Manage Server | Set the welcome direct message | I
welcome | xsetwelcome message | setwelcome message | setwelcome message | - | Manage Server | Set the welcome message | I
welcome | xsetwelcome placeholders | setwelcome placeholders | setwelcome placeholders | - | Manage Server | List usable placeholders | I
welcome | xsetwelcome reset | setwelcome reset | setwelcome reset | - | Manage Server | Reset welcome settings | I
welcome | xsetwelcome test | setwelcome test | setwelcome test | - | Manage Server | Preview the welcome message | I
welcome | xsetwelcome toggle | setwelcome toggle | setwelcome toggle | - | Manage Server | Turn welcome messages on or off | I
welcome | welcomeimage | welcomeimage | welcomeimage show | - | Manage Server | Show the welcome image settings | I
welcome | welcomeimage on | welcomeimage on | welcomeimage on | - | Manage Server | Turn the welcome image on | I
welcome | welcomeimage off | welcomeimage off | welcomeimage off | - | Manage Server | Turn the welcome image off | I
welcome | welcomeimage set | welcomeimage set | welcomeimage set | - | Manage Server | Set the welcome image | I

logging | xsetuplogs | setuplogs | setuplogs | - | Administrator | Create the private Logs category with a channel for each kind of log. | I

tickets | xpsetup | psetup | psetup | - | Manage Server | One-click ticket setup: category, support role and a ticket panel. | I
tickets | xticketnote | ticketnote | ticketnote | - | Ticket staff | Add a private staff note to a ticket | I
tickets | xticketnotes | ticketnotes | ticketnotes | - | Ticket staff | Show a ticket's staff notes | I
tickets | xticketpanel | ticketpanel | ticketpanel post | - | Manage Server | Post or refresh a ticket panel in this channel. | I
tickets | xticketpriority | ticketpriority | ticketpriority | - | Ticket staff | Set a ticket's priority | I
tickets | xticketsetup | ticketsetup | ticketsetup show | - | Manage Server | Configure tickets | I
tickets | xticketsetup autoclose | ticketsetup autoclose | ticketsetup autoclose | - | Manage Server | Set or disable automatic closing | I
tickets | xticketsetup category | ticketsetup category | ticketsetup category | - | Manage Server | Choose the ticket category | I
tickets | xticketsetup inactivity | ticketsetup inactivity | ticketsetup inactivity | - | Manage Server | Set or disable inactivity warnings | I
tickets | xticketsetup role | ticketsetup role | ticketsetup role | - | Manage Server | Choose the support role | I
tickets | xticketstats | ticketstats | ticketstats | - | Ticket staff | Show ticket statistics | I

voice | xhuntvoiceinterface | voiceinterface | voiceinterface | - | Manage Channels | Post the voice room control panel in this channel. | I
voice | xhuntvoicesetup | voicesetup | voicesetup show | - | Manage Channels | Configure join-to-create voice | I
voice | xhuntvoicesetup category | voicesetup category | voicesetup category | - | Manage Channels | Choose the category for voice rooms | I
voice | xhuntvoicesetup joinchannel | voicesetup joinchannel | voicesetup joinchannel | - | Manage Channels | Choose the join-to-create channel | I
voice | xhuntvoicesetup limit | voicesetup limit | voicesetup limit | - | Manage Channels | Set the default user limit | I
voice | xhuntvoicesetup nametemplate | voicesetup nametemplate | voicesetup nametemplate | - | Manage Channels | Set the room name template | I
voice | xjtc | jtc | jtc | - | Manage Channels | One command: creates the voice category, the join channel and the interface channel. | I

prestige | xpremcredit | prestigecredit | prestige credit | - | Bot owner | Grant internal credits | I
prestige | xpremcredits | prestigecredits | prestige credits | - | Credit holder | Show your credit balance | I
prestige | xpremium | prestige | prestige overview | - | None | Show Prestige information. | I
prestige | xpremium add | prestige add | prestige add | - | Bot owner / bot admin | Activate Prestige for a server (bot owner or bot admin only). | I
prestige | xpremium check | prestige check | prestige check | - | Server owner / bot admin | Check a server's Prestige status | I
prestige | xpremium list | prestige list | prestige list | - | Bot owner / bot admin | List Prestige servers (bot owner or bot admin only). | I
prestige | xpremium remove | prestige remove | prestige remove | - | Bot owner / bot admin | Remove a server's Prestige access (bot owner or bot admin only). | I
prestige | xsetbotbanner | setbotbanner | prestige botbanner | - | Server owner | Set the bot's banner in this server (server owner, needs Prestige). | I
prestige | xsetbotbio | setbotbio | prestige botbio | - | Server owner | Set the bot's bio in this server (server owner, needs Prestige). | I
prestige | xsetbotname | setbotname | prestige botname | - | Server owner | Set the bot's nickname in this server (server owner, needs Prestige). | I
prestige | xsetbotpfp | setbotpfp | prestige botpfp | - | Server owner | Set the bot's avatar in this server (server owner, needs Prestige). | I
prestige | xtagrole | tagrole | prestige tagrole | - | Server owner | Give a role to members wearing a tag (server owner, needs Prestige). | I
prestige | xtagroleinfo | tagroleinfo | prestige tagroleinfo | - | Server owner | Show the tag role configuration. | I

setup | new | setup | setup | - | Administrator | Open the interactive setup panel | I
setup | new | template | template list | - | Administrator | List built-in and saved templates | I
setup | new | template save | template save | - | Administrator | Save this server as a template | I
setup | new | template apply | template apply | - | Administrator | Preview and apply a template | I

antinuke | new | antinuke set | antinuke set | - | Administrator | Set thresholds and response for one protection | I
antinuke | new | antinuke log | antinuke log | - | Administrator | Set or show the security alert channel (slash form) | I
moderation | new | jailrole | jailrole | - | Manage Roles | Choose the role given to jailed members | I
welcome | new | setwelcome leave | setwelcome leave | - | Manage Server | Set the leave message | I
welcome | new | setwelcome leavechannel | setwelcome leavechannel | - | Manage Server | Set the channel for leave messages | I
logging | new | logevents | logevents | - | Administrator | Turn a server-log event on or off, or list them | I
tickets | new | ticketadd | ticketadd | - | Ticket staff | Add a member to a ticket | I
tickets | new | ticketremove | ticketremove | - | Ticket staff | Remove a member from a ticket | I

antinuke | new | punishment | punishment | - | Administrator | Choose what happens when anti-nuke triggers (strip, ban, kick or jail). | I
tickets | new | ticketsetup categories | ticketsetup categories | - | Manage Server | Set the topics people can choose on the ticket panel. | I

logging | new | alertrole | alertrole list | - | Administrator | Show the roles pinged for urgent security alerts. | I
logging | new | alertrole add | alertrole add | - | Administrator | Ping this role for urgent security alerts only. | I
logging | new | alertrole remove | alertrole remove | - | Administrator | Stop pinging this role. | I

utility | new | giveaway | giveaway list | - | None | Show the active giveaways in this server. | I
utility | new | giveaway start | giveaway start | - | Manage Server | Start a giveaway (optional role and message requirements). | I
utility | new | giveaway end | giveaway end | - | Manage Server | End a giveaway now and pick the winners. | I
utility | new | giveaway reroll | giveaway reroll | - | Manage Server | Pick new winners for an ended giveaway. | I
utility | new | poll | poll list | - | None | Show the open polls in this server. | I
utility | new | poll create | poll create | - | Manage Messages | Create a poll (optional role and message requirements). | I
utility | new | poll close | poll close | - | Manage Messages | Close a poll now and show the result. | I
utility | new | poll results | poll results | - | None | Show a poll's current results. | I
leveling | new | rank | rank | - | None | Show your rank card and level progress. | I
leveling | new | leaderboard | leaderboard | lb | None | Show the server's top 10 by XP. | I
leveling | new | levelrole | levelrole list | - | Manage Server | Show the level role rewards. | I
leveling | new | levelrole add | levelrole add | - | Manage Server | Give a role at a level. | I
leveling | new | levelrole remove | levelrole remove | - | Manage Server | Remove the role reward for a level. | I
leveling | new | setlevellog | setlevellog set | - | Manage Server | Choose where level-up cards are posted. | I
leveling | new | setlevellog reset | setlevellog reset | - | Manage Server | Post level-up cards where the member chatted. | I
leveling | new | leveling | leveling status | - | Manage Server | Show whether leveling is on. | I
leveling | new | leveling enable | leveling enable | - | Manage Server | Turn XP on. | I
leveling | new | leveling disable | leveling disable | - | Manage Server | Turn XP off (message counts keep working). | I
rep | new | setreprole | setreprole | - | Manage Server | Set the role given for status rep. | I
rep | new | setreptarget | setreptarget list | - | Manage Server | Show the status targets. | I
rep | new | setreptarget add | setreptarget add | - | Manage Server | Add a status target. | I
rep | new | setreptarget remove | setreptarget remove | - | Manage Server | Remove a status target. | I
rep | new | repstatus | repstatus | - | Manage Server | View the status rep configuration. | I
rep | new | rep | rep status | - | Manage Server | View status rep, or turn it on or off. | I
rep | new | rep enable | rep enable | - | Manage Server | Turn status rep on. | I
rep | new | rep disable | rep disable | - | Manage Server | Turn status rep off. | I
rep | new | setreplog | setreplog | - | Manage Server | Set the channel for status rep logs. | I
rep | new | repcheck | repcheck | - | Manage Server | Check one member's status rep. | I
rep | new | repscan | repscan | - | Manage Server | Scan online members for status rep and fix roles. | I
rep | new | settagrole | settagrole | - | Manage Server | Set the role given for wearing this server's tag. | I
rep | new | reptag | reptag status | - | Manage Server | View server tag rep, or turn it on or off. | I
rep | new | reptag enable | reptag enable | - | Manage Server | Turn server tag rep on. | I
rep | new | reptag disable | reptag disable | - | Manage Server | Turn server tag rep off. | I
rep | new | settaglog | settaglog | - | Manage Server | Set the channel for server tag rep logs. | I
rep | new | tagcheck | tagcheck | - | Manage Server | Check whether a member wears this server's tag. | I
rep | new | tagscan | tagscan | - | Manage Server | Scan members for this server's tag and fix roles. | I
owners | new | extraowner | extraowner list | - | Server owner | Show this server's extra owners. | I
owners | new | extraowner add | extraowner add | - | Server owner | Make someone an extra owner of this server. | I
owners | new | extraowner remove | extraowner remove | - | Server owner | Remove an extra owner. | I
owner | new | gban | gban | - | Global staff (gban) | Globally ban a user from the bot (not from any server). | I
owner | new | gunban | gunban | - | Global staff (gban) | Lift a global ban. | I
owner | new | gbancheck | gbancheck | - | Global staff (gban) | Check whether a user is globally banned. | I
owner | new | gbanlist | gbanlist | - | Global staff (gban) | List active global bans. | I
owner | new | gbaninfo | gbaninfo | - | Global staff (gban) | Show a user's or a case's global ban details. | I
owner | new | gperm | gperm list | - | Bot owner | Show global staff permissions. | I
owner | new | gperm add | gperm add | - | Bot owner | Grant a global permission to global staff. | I
owner | new | gperm remove | gperm remove | - | Bot owner | Take a global permission away. | I
owner | new | stafflist | stafflist | - | Global staff | List the bot owner and global staff. | I
owner | new | helpadm | helpadm | - | Global staff | Show the global commands you can use. | I
owner | new | serverlist | serverlist | - | Global staff (servers) | List the servers the bot is in. | I
owner | new | generatelink | generatelink | - | Global staff (invite) | Create a one-use invite for a server, sent privately. | I
owner | new | gaudit | gaudit | - | Global staff (audit) | Read the global administration log. | I
moderation | new | modrole | modrole list | - | Server administrator | Show the server's moderator roles. | I
moderation | new | modrole add | modrole add | - | Server administrator | Add a moderator role. | I
moderation | new | modrole remove | modrole remove | - | Server administrator | Remove a moderator role. | I
tickets | new | ticketpanel new | ticketpanel new | - | Manage Server | Create another ticket panel. | I
tickets | new | ticketpanel list | ticketpanel list | - | Manage Server | List this server's ticket panels. | I
tickets | new | ticketpanel delete | ticketpanel delete | - | Manage Server | Delete a ticket panel. | I
tickets | new | ticketdesign | ticketdesign | - | Manage Server | Open the ticket designer: text, colours, images, buttons, categories, staff, transcripts. | I
welcome | new | setwelcome title | setwelcome title | - | Manage Server | Set the welcome embed title. | I
welcome | new | setwelcome leavetitle | setwelcome leavetitle | - | Manage Server | Set the leave embed title. | I
welcome | new | setwelcome color | setwelcome color | - | Manage Server | Set the embed colour. | I
welcome | new | setwelcome footer | setwelcome footer | - | Manage Server | Set the embed footer. | I
welcome | new | setwelcome thumbnail | setwelcome thumbnail | - | Manage Server | Choose the thumbnail: member, server, none or a link. | I
welcome | new | setwelcome rules | setwelcome rules | - | Manage Server | Add a rules section to the welcome embed. | I
welcome | new | setwelcome style | setwelcome style | - | Manage Server | Choose embed or plain text. | I
owner | new | sync | - | - | Bot owner | Re-sync slash commands | I
owner | new | - | administrator add | - | Bot owner | Grant global bot administrator access | I
owner | new | - | administrator remove | - | Bot owner | Remove global bot administrator access | I
owner | new | - | administrator list | - | Bot owner | List global bot administrators | I
owner | new | - | mod add | - | Bot owner | Grant global bot moderator access | I
owner | new | - | mod remove | - | Bot owner | Remove global bot moderator access | I
owner | new | - | mod list | - | Bot owner | List global bot moderators | I
"""


def _none(value: str) -> Optional[str]:
    return None if value == "-" else value


def _parse_table(table: str) -> tuple[CommandSpec, ...]:
    specs: list[CommandSpec] = []
    for number, line in enumerate(table.strip().splitlines(), start=1):
        if not line.strip():
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) != 8:
            raise ValueError(f"registry row {number} has {len(cells)} cells, expected 8: {line!r}")
        module, reference, name, slash, aliases, permission, description, status = cells
        specs.append(
            CommandSpec(
                module=module,
                reference=reference,
                name=_none(name),
                slash=_none(slash),
                aliases=tuple(a.strip() for a in aliases.split(",")) if aliases != "-" else (),
                permission=permission,
                description=description,
                status=Status.IMPLEMENTED if status == "I" else Status.PLANNED,
            )
        )
    return tuple(specs)


class Registry:
    def __init__(self, modules: tuple[ModuleSpec, ...], specs: tuple[CommandSpec, ...]) -> None:
        self._modules = {module.key: module for module in modules}
        self._order = tuple(module.key for module in modules)
        self._specs = specs
        self._lookup: dict[str, CommandSpec] = {}
        for spec in specs:
            if spec.name:
                self._lookup.setdefault(spec.name, spec)
                if " " not in spec.name:
                    for alias in spec.aliases:
                        self._lookup.setdefault(alias, spec)
            if spec.slash:
                self._lookup.setdefault(spec.slash, spec)

    def modules(self) -> tuple[ModuleSpec, ...]:
        return tuple(self._modules[key] for key in self._order)

    def module(self, key: str) -> ModuleSpec:
        return self._modules[key]

    def specs(self) -> tuple[CommandSpec, ...]:
        return self._specs

    def commands(self, module: Optional[str] = None, *, implemented_only: bool = False) -> list[CommandSpec]:
        return [
            spec
            for spec in self._specs
            if (module is None or spec.module == module) and (spec.implemented or not implemented_only)
        ]

    def is_live(self, module: str) -> bool:
        return any(spec.implemented for spec in self._specs if spec.module == module)

    def visible_modules(self, include_restricted: bool = False) -> list[ModuleSpec]:
        """Modules shown in the help dropdown: Home, plus every module that has a real command. Restricted
        (hidden) modules, such as global administration, are shown only when asked for (to global staff)."""
        return [
            module
            for module in self.modules()
            if (include_restricted or not module.hidden) and (module.key == "home" or self.is_live(module.key))
        ]

    def public_command_count(self) -> int:
        """Implemented commands in non-hidden modules (what the help panel advertises)."""
        return sum(1 for spec in self._specs if spec.implemented and not self._modules[spec.module].hidden)

    @staticmethod
    def _normalise(query: str) -> str:
        return " ".join(query.strip().lstrip("/").lower().split())

    def find(self, query: str) -> Optional[CommandSpec]:
        return self._lookup.get(self._normalise(query))

    def suggest(self, query: str, limit: int = 3) -> list[str]:
        """Close matches among commands that actually exist."""
        names = [spec.name for spec in self._specs if spec.implemented and spec.name and spec.module != "owner"]
        return difflib.get_close_matches(self._normalise(query), names, n=limit, cutoff=0.6)


SLASH_PART = re.compile(r"^[a-z0-9_-]{1,32}$")

registry = Registry(MODULES, _parse_table(_TABLE))

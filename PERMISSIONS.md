# HOPELESS permission matrix

Generated from the same rules the bot enforces (`core/permission_matrix.py` and `core/authz.py`), so this file cannot disagree with the code. Every check runs when a command, button or menu is used; hiding something in `help` is never the security.

## The two permission systems

They are separate. Power in one never grants power in the other.

| System | Order, highest first |
|---|---|
| Server | server owner, Administrator, Manage Server, moderators (a Discord permission, optionally narrowed to moderator roles), ticket staff, members |
| Global (bot-wide) | bot owner (`BOT_OWNER_ID`), global administrators (only the permissions the owner grants with `gperm`), global moderators |

- A server owner or server Administrator is **not** global staff and gets nothing from the Global administration module.
- Moderator roles (`modrole`) only **narrow** access. A member always needs the Discord permission for the action; a role alone never grants it. Administrators and the server owner are never narrowed.
- A global administrator starts with the `prestige` permission only. The owner grants more with `gperm add @user <permission>`: `gban`, `servers`, `invite`, `audit`, `prestige`.
- The bot owner can never be globally banned. Only the owner can globally ban global staff.

## Reading the tables

**Who** is the minimum level. **Discord permission** is what a moderator needs for that action. **Mods** and **Admins** say whether a typical moderator (holding every moderation permission and a moderator role) or a Manage Server admin may use it. **Logged** means the action is recorded in the moderation log, the security log or the global audit log.

## Home

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `help` | public | - | yes | yes | no | no |

## Antinuke

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `antinuke` | server owner or Administrator | - | no | no | no | yes |
| `antinuke status` | server owner or Administrator | - | no | no | no | yes |
| `antinuke module` | server owner or Administrator | - | no | no | no | yes |
| `antinuke setup` | server owner or Administrator | - | no | no | no | yes |
| `antinukelog` | server owner or Administrator | - | no | no | no | yes |
| `antiban` | server owner or Administrator | - | no | no | no | yes |
| `antibotadd` | server owner or Administrator | - | no | no | no | yes |
| `antichannelcreate` | server owner or Administrator | - | no | no | no | yes |
| `antichanneldelete` | server owner or Administrator | - | no | no | no | yes |
| `antichannelupdate` | server owner or Administrator | - | no | no | no | yes |
| `antiguildupdate` | server owner or Administrator | - | no | no | no | yes |
| `antikick` | server owner or Administrator | - | no | no | no | yes |
| `antirolecreate` | server owner or Administrator | - | no | no | no | yes |
| `antiroledelete` | server owner or Administrator | - | no | no | no | yes |
| `antiroleupdate` | server owner or Administrator | - | no | no | no | yes |
| `antiwebhook` | server owner or Administrator | - | no | no | no | yes |
| `antinuke set` | server owner or Administrator | - | no | no | no | yes |
| `antinuke log` | server owner or Administrator | - | no | no | no | yes |
| `punishment` | server owner or Administrator | - | no | no | no | yes |

## Whitelist

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `whitelist` | server owner or Administrator | - | no | no | no | yes |
| `whitelist list` | server owner or Administrator | - | no | no | no | yes |
| `whitelist remove` | server owner or Administrator | - | no | no | no | yes |
| `whitelistremove` | server owner or Administrator | - | no | no | no | yes |
| `whitelist scopes` | server owner or Administrator | - | no | no | no | yes |

## Moderation

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `ban` | moderator | Ban Members | yes | yes | no | yes |
| `kick` | moderator | Kick Members | yes | yes | no | yes |
| `softban` | moderator | Ban Members | yes | yes | no | yes |
| `timeout` | moderator | Moderate Members | yes | yes | no | yes |
| `unban` | moderator | Ban Members | yes | yes | no | yes |
| `unmute` | moderator | Moderate Members | yes | yes | no | yes |
| `jail` | moderator | Manage Roles | yes | yes | no | yes |
| `jlist` | moderator | Manage Roles | yes | yes | no | yes |
| `unjail` | moderator | Manage Roles | yes | yes | no | yes |
| `lock` | moderator | Manage Channels | yes | yes | no | yes |
| `unlock` | moderator | Manage Channels | yes | yes | no | yes |
| `purge` | moderator | Manage Messages | yes | yes | no | yes |
| `purgeuser` | moderator | Manage Messages | yes | yes | no | yes |
| `role` | moderator | Manage Roles | yes | yes | no | yes |
| `role add` | moderator | Manage Roles | yes | yes | no | yes |
| `role remove` | moderator | Manage Roles | yes | yes | no | yes |
| `roleall` | moderator | Manage Roles | yes | yes | no | yes |
| `autorole` | server administrator | - | no | yes | no | yes |
| `setprefix` | server administrator | - | no | yes | no | yes |
| `slowmode` | moderator | Manage Channels | yes | yes | no | yes |
| `jailrole` | server administrator | - | no | yes | no | yes |
| `modrole` | server administrator | - | no | yes | no | yes |
| `modrole add` | server administrator | - | no | yes | no | yes |
| `modrole remove` | server administrator | - | no | yes | no | yes |

## Utility

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `afk` | member | - | yes | yes | no | no |
| `avatar` | public | - | yes | yes | no | no |
| `banner` | public | - | yes | yes | no | no |
| `botinfo` | public | - | yes | yes | no | no |
| `botinvite` | public | - | yes | yes | no | no |
| `clearsnipe` | moderator | Manage Messages | yes | yes | no | no |
| `editsnipe` | moderator | Manage Messages | yes | yes | no | no |
| `ping` | public | - | yes | yes | no | no |
| `serverinfo` | public | - | yes | yes | no | no |
| `snipe` | moderator | Manage Messages | yes | yes | no | no |
| `stats` | public | - | yes | yes | no | no |
| `support` | public | - | yes | yes | no | no |
| `tutorial` | public | - | yes | yes | no | no |
| `uptime` | public | - | yes | yes | no | no |
| `userinfo` | public | - | yes | yes | no | no |
| `giveaway` | member | - | yes | yes | no | no |
| `giveaway start` | moderator | Manage Guild | no | yes | no | yes |
| `giveaway end` | moderator | Manage Guild | no | yes | no | yes |
| `giveaway reroll` | moderator | Manage Guild | no | yes | no | yes |
| `poll` | member | - | yes | yes | no | no |
| `poll create` | moderator | Manage Messages | yes | yes | no | yes |
| `poll close` | moderator | Manage Messages | yes | yes | no | yes |
| `poll results` | member | - | yes | yes | no | no |

## Automod

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `automod` | server administrator | - | no | yes | no | yes |
| `antilink` | server administrator | - | no | yes | no | yes |
| `antilink whitelist` | server administrator | - | no | yes | no | yes |
| `antilink unwhitelist` | server administrator | - | no | yes | no | yes |
| `antimention` | server administrator | - | no | yes | no | yes |
| `antinsfw` | server administrator | - | no | yes | no | yes |
| `antiraid` | server administrator | - | no | yes | no | yes |
| `antispam` | server administrator | - | no | yes | no | yes |

## Welcome

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `setwelcome` | server administrator | - | no | yes | no | no |
| `setwelcome dm` | server administrator | - | no | yes | no | no |
| `setwelcome message` | server administrator | - | no | yes | no | no |
| `setwelcome placeholders` | server administrator | - | no | yes | no | no |
| `setwelcome reset` | server administrator | - | no | yes | no | no |
| `setwelcome test` | server administrator | - | no | yes | no | no |
| `setwelcome toggle` | server administrator | - | no | yes | no | no |
| `welcomeimage` | server administrator | - | no | yes | no | no |
| `welcomeimage on` | server administrator | - | no | yes | no | no |
| `welcomeimage off` | server administrator | - | no | yes | no | no |
| `welcomeimage set` | server administrator | - | no | yes | no | no |
| `setwelcome leave` | server administrator | - | no | yes | no | no |
| `setwelcome leavechannel` | server administrator | - | no | yes | no | no |
| `setwelcome title` | server administrator | - | no | yes | no | no |
| `setwelcome leavetitle` | server administrator | - | no | yes | no | no |
| `setwelcome color` | server administrator | - | no | yes | no | no |
| `setwelcome footer` | server administrator | - | no | yes | no | no |
| `setwelcome thumbnail` | server administrator | - | no | yes | no | no |
| `setwelcome rules` | server administrator | - | no | yes | no | no |
| `setwelcome style` | server administrator | - | no | yes | no | no |

## Prestige

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `prestigecredit` | bot owner | - | no | no | owner only | yes |
| `prestigecredits` | member | - | yes | yes | no | no |
| `prestige` | member | - | yes | yes | no | no |
| `prestige add` | global staff with the permission | - | no | no | `prestige` permission | yes |
| `prestige check` | member | - | yes | yes | no | no |
| `prestige list` | global staff with the permission | - | no | no | `prestige` permission | no |
| `prestige remove` | global staff with the permission | - | no | no | `prestige` permission | yes |
| `setbotbanner` | server owner | - | no | no | no | yes |
| `setbotbio` | server owner | - | no | no | no | yes |
| `setbotname` | server owner | - | no | no | no | yes |
| `setbotpfp` | server owner | - | no | no | no | yes |
| `tagrole` | server owner | - | no | no | no | yes |
| `tagroleinfo` | member | - | yes | yes | no | no |

## Logging

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `setuplogs` | server owner or Administrator | - | no | no | no | no |
| `logevents` | server owner or Administrator | - | no | no | no | no |
| `alertrole` | server owner or Administrator | - | no | no | no | no |
| `alertrole add` | server owner or Administrator | - | no | no | no | no |
| `alertrole remove` | server owner or Administrator | - | no | no | no | no |

## Tickets

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `psetup` | server administrator | - | no | yes | no | yes |
| `ticketnote` | ticket staff | - | no | yes | no | yes |
| `ticketnotes` | ticket staff | - | no | yes | no | no |
| `ticketpanel` | server administrator | - | no | yes | no | yes |
| `ticketpriority` | ticket staff | - | no | yes | no | yes |
| `ticketsetup` | server administrator | - | no | yes | no | yes |
| `ticketsetup autoclose` | server administrator | - | no | yes | no | yes |
| `ticketsetup category` | server administrator | - | no | yes | no | yes |
| `ticketsetup inactivity` | server administrator | - | no | yes | no | yes |
| `ticketsetup role` | server administrator | - | no | yes | no | yes |
| `ticketstats` | ticket staff | - | no | yes | no | no |
| `ticketadd` | ticket staff | - | no | yes | no | yes |
| `ticketremove` | ticket staff | - | no | yes | no | yes |
| `ticketsetup categories` | server administrator | - | no | yes | no | yes |
| `ticketpanel new` | server administrator | - | no | yes | no | yes |
| `ticketpanel list` | server administrator | - | no | yes | no | yes |
| `ticketpanel delete` | server administrator | - | no | yes | no | yes |
| `ticketdesign` | server administrator | - | no | yes | no | yes |

## Voice

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `voiceinterface` | server administrator | - | no | yes | no | no |
| `voicesetup` | server administrator | - | no | yes | no | no |
| `voicesetup category` | server administrator | - | no | yes | no | no |
| `voicesetup joinchannel` | server administrator | - | no | yes | no | no |
| `voicesetup limit` | server administrator | - | no | yes | no | no |
| `voicesetup nametemplate` | server administrator | - | no | yes | no | no |
| `jtc` | server administrator | - | no | yes | no | no |

## Setup

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `setup` | server owner or Administrator | - | no | no | no | yes |
| `template` | server owner or Administrator | - | no | no | no | yes |
| `template save` | server owner or Administrator | - | no | no | no | yes |
| `template apply` | server owner or Administrator | - | no | no | no | yes |

## Leveling

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `rank` | member | - | yes | yes | no | no |
| `leaderboard` | member | - | yes | yes | no | no |
| `levelrole` | server administrator | - | no | yes | no | yes |
| `levelrole add` | server administrator | - | no | yes | no | yes |
| `levelrole remove` | server administrator | - | no | yes | no | yes |
| `setlevellog` | server administrator | - | no | yes | no | yes |
| `setlevellog reset` | server administrator | - | no | yes | no | yes |
| `leveling` | server administrator | - | no | yes | no | yes |
| `leveling enable` | server administrator | - | no | yes | no | yes |
| `leveling disable` | server administrator | - | no | yes | no | yes |

## Reputation

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `setreprole` | server administrator | - | no | yes | no | yes |
| `setreptarget` | server administrator | - | no | yes | no | yes |
| `setreptarget add` | server administrator | - | no | yes | no | yes |
| `setreptarget remove` | server administrator | - | no | yes | no | yes |
| `repstatus` | server administrator | - | no | yes | no | yes |
| `rep` | server administrator | - | no | yes | no | yes |
| `rep enable` | server administrator | - | no | yes | no | yes |
| `rep disable` | server administrator | - | no | yes | no | yes |
| `setreplog` | server administrator | - | no | yes | no | yes |
| `repcheck` | server administrator | - | no | yes | no | yes |
| `repscan` | server administrator | - | no | yes | no | yes |
| `settagrole` | server administrator | - | no | yes | no | yes |
| `reptag` | server administrator | - | no | yes | no | yes |
| `reptag enable` | server administrator | - | no | yes | no | yes |
| `reptag disable` | server administrator | - | no | yes | no | yes |
| `settaglog` | server administrator | - | no | yes | no | yes |
| `tagcheck` | server administrator | - | no | yes | no | yes |
| `tagscan` | server administrator | - | no | yes | no | yes |

## Extra Owners

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `extraowner` | server administrator | - | no | yes | no | no |
| `extraowner add` | server owner | - | no | no | no | yes |
| `extraowner remove` | server owner | - | no | no | no | yes |

## Global administration

| Command | Who | Discord permission | Mods | Admins | Global | Logged |
|---|---|---|---|---|---|---|
| `gban` | global staff with the permission | - | no | no | `gban` permission | yes |
| `gunban` | global staff with the permission | - | no | no | `gban` permission | yes |
| `gbancheck` | global staff with the permission | - | no | no | `gban` permission | no |
| `gbanlist` | global staff with the permission | - | no | no | `gban` permission | no |
| `gbaninfo` | global staff with the permission | - | no | no | `gban` permission | no |
| `gperm` | bot owner | - | no | no | owner only | yes |
| `gperm add` | bot owner | - | no | no | owner only | yes |
| `gperm remove` | bot owner | - | no | no | owner only | yes |
| `stafflist` | global staff | - | no | no | any global staff | no |
| `helpadm` | global staff | - | no | no | any global staff | no |
| `serverlist` | global staff with the permission | - | no | no | `servers` permission | no |
| `generatelink` | global staff with the permission | - | no | no | `invite` permission | yes |
| `gaudit` | global staff with the permission | - | no | no | `audit` permission | no |
| `sync` | bot owner | - | no | no | owner only | yes |
| `/administrator add` | bot owner | - | no | no | owner only | yes |
| `/administrator remove` | bot owner | - | no | no | owner only | yes |
| `/administrator list` | bot owner | - | no | no | owner only | yes |
| `/mod add` | bot owner | - | no | no | owner only | yes |
| `/mod remove` | bot owner | - | no | no | owner only | yes |
| `/mod list` | bot owner | - | no | no | owner only | yes |

## Voice rooms

Voice buttons and commands check the room itself, every time:

- The room's **owner** can use every control on their own room.
- **Staff** may act on someone else's room only if they hold the Discord permissions the action needs: Kick needs **Move Members**; Invite, Block and the Waiting Room need **Manage Channels** and **Move Members**; the rest need **Manage Channels**. Staff actions are logged.
- The bot checks its own Move Members and Manage Channels permissions before moving anyone and says so if it can't.

## Prestige

- Activating, removing and listing Prestige servers: the bot owner, or a global administrator who holds the `prestige` permission.
- `prestige check` works for anyone on their own server; checking another server needs the global `prestige` permission.
- Editing a server's bot name, avatar, banner, bio and tag roles: the server owner (the bot owner may also help), and the server needs Prestige.

## Tickets

- Opening a ticket: any member (not globally banned), up to the server's limit.
- Claim, unclaim, transcript, delete and the staff tools: ticket staff roles, or Manage Server.
- Close: ticket staff, or the ticket's owner. Closed tickets are deleted after the configured delay (default 3 seconds) and cannot be reopened.

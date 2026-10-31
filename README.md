# HOPELESS

A security-first Discord bot: Python 3.12, discord.py, PostgreSQL, deployable on Railway. Every command works as a prefix command (default prefix `C`, for example `Chelp`) and as a slash command. Mentioning the bot also works as a prefix.

**Start here on an iPhone: `IPHONE_GUIDE.md`.** `CHECKLIST.md` lists exactly what was tested and what was not. `PERMISSIONS.md` is the full permission matrix: who can use every command.

## Modules

| Module | What it does |
|---|---|
| Home | `help`: interactive panel with a module dropdown. It lists prefix commands only (slash versions still work, they just aren't shown) |
| Antinuke | 25 protections driven by audit-log attribution, thresholds, punishments. Snapshots keep the last known good state (never overwritten during an attack or a restoration); recovery restores roles first, then categories, then channels inside their categories, with permissions and positions |
| Whitelist | scoped exemptions for users and roles (never a blanket "everything" switch) |
| Moderation | ban, kick, softban, timeout, unban, unmute, jail (timed, restores roles), lock, purge, `role`, `roleall`, `autorole`, slowmode, per-server prefix, and `modrole` to limit moderation to chosen roles |
| Utility | avatar, banner, server and user info, AFK (a one-line status that clears when you talk again), snipes (moderators only), bot stats, **giveaways** and **polls** (optional required role and minimum message count, buttons that survive restarts, automatic ending) |
| Automod | link, mention, spam, raid rules with per-rule exemptions |
| Welcome | welcome, leave and DM messages as embeds you can customize (title, colour, footer, thumbnail, rules, banner image, or plain text), with a live preview (`setwelcome test`) |
| Logging | exactly two channels: `#logs` for ordinary events (messages deleted or edited with who deleted them when the audit log proves it, joins, leaves, roles, channels, voice, avatars) and `#security-logs` for antinuke alerts. Cards are posted by a "HOPELESS Logging" webhook so they look separate from the bot |
| Leveling | XP for chatting, `rank`, `leaderboard`, `levelrole`, level-up cards, `setlevellog` |
| Reputation | a role while a member's custom status contains your text or while they wear this server's tag, **removed again automatically** when they stop (`setreprole`, `setreptarget`, `rep`, `settagrole`, `reptag`, scans and checks) |
| Extra Owners | `extraowner add/remove/list`: owner-level control of one server only, never global |
| Tickets | fully customizable panels (`ticketdesign`: text, colour, banner, thumbnail, footer, button label/style/emoji, menu placeholder, categories you can add, edit, reorder, hide and remove, staff roles, ticket category, transcript channel, naming, limits, opening message, instructions), several panels per server, a live preview, private channels, claim, priority, private notes, transcripts. Closed tickets are deleted after 3 seconds by default (a transcript is attempted first and reported honestly) |
| Voice | `jtc` is one command that creates the category, the join channel `j2c` and a `config` channel with the panel (`voicesetup category` now builds whatever is missing in that category too); join-to-create rooms and the full control panel (Name, Limit, Privacy, Waiting Room, Chat, Trust, Untrust, Invite, Kick, Region, Block, Unblock, Claim, Transfer, Delete). Staff acting on someone else's room need the real Discord permissions (Move Members to kick or move) |
| Setup | `/setup` panel and server templates, always with a preview and confirmation |
| Prestige | activated only by the bot owner or a global administrator holding the `prestige` permission (an admin spends a credit the owner granted); the server owner edits the per-server bot name, avatar, banner, bio and tag roles |
| Global administration | bot-wide tools, visible in `help` only to global staff: global bans (`gban`, `gunban`, `gbancheck`, `gbanlist`, `gbaninfo`), `gperm` (the owner grants each global administrator exactly the powers they should have), `stafflist`, `serverlist`, `generatelink` (sent privately), `gaudit` |


Global staff are managed by the owner with slash commands: `/administrator add|remove|list` and `/mod add|remove|list`. A new global administrator starts with the `prestige` permission only; grant more with `gperm add @user gban` (also `servers`, `invite`, `audit`). A global ban only stops someone from using the bot (commands, aliases, slash commands, buttons and menus); it never bans anyone from a Discord server. The bot owner can never be banned.

## Things worth knowing

- **Plain errors.** Unexpected errors just say "try again" or "the bot didn't respond", in a different wording each time. Running commands too fast gets "try this command again in **N seconds**" (3 commands per 6 seconds per member; the bot owner is never limited). Only the bot owner and global staff see an error id; it is always in the logs.
- **Components V2.** Help, AFK, server info, stats, giveaways, polls, level-ups and the log cards are real Components V2 messages (a container with text, separators and buttons) with an automatic classic-embed fallback if Discord refuses them. Ticket, voice and designer panels stay classic.
- **Status rep needs the Presence Intent.** Turn it on in the Developer Portal (Bot, then Privileged Gateway Intents) AND set the variable `ENABLE_PRESENCE_INTENT=1`. Without both the bot still starts normally, and `rep enable` explains what is missing. Server-tag rep reads the tag data Discord sends with members; it can't be seen for someone Discord doesn't send it for.
- **Message counts start when tracking starts.** Discord has no historical message counter, so a "minimum messages" requirement counts from when the bot began tracking that member.
- **Slash command limit.** Discord allows 100 top-level slash commands; HOPELESS uses 99. New features should join an existing group.
- **Two separate permission systems.** Server roles (owner, Administrator, Manage Server, moderators, ticket staff, members) and bot-wide global staff never grant each other anything: a server owner is not a bot owner. Moderator roles (`modrole add @role`) only narrow who may moderate; the Discord permission is always still required. Details in `PERMISSIONS.md`.
- **Snipe commands are moderator-only** now (they can reveal deleted messages). The restriction is one line per command if you prefer them public.
- **Cards.** Info commands (AFK, server info, stats, staff list) use Discord's newer message layout when your discord.py supports it, and fall back to a normal embed automatically. Panels that carry buttons and menus (help, tickets, voice) stay classic embeds on purpose.
- **Prefix `C` is risky.** A sentence starting with a capital C plus a command name runs that command ("Clock in at 9" would try `lock`). Change it per server with `Csetprefix !`. Mention the bot alone to see the current prefix.
- **Emojis work everywhere.** All icons are defined in one place (`core/emoji.py`, or `core_emoji.py` in the upload edition). At startup the bot copies your custom emojis into its own application emojis, so they show in every server without adding them anywhere. Anything that can't be copied falls back to a normal emoji. To add or swap one, edit that file or upload `emoji_<key>.png` (for example `emoji_success.png`).
- **Security alerts are quiet unless it matters.** One incident is only recorded. The same person acting twice in a short time, or a response, is urgent and pings the roles you chose with `alertrole add @role`. Server logs never ping.
- **Antinuke never touches bot-managed things.** Ticket channels, temporary voice rooms and a bot's own role are ignored, deleted channels are only recreated if they were part of the same burst, and a bot added by an Administrator is left alone (a note is logged).
- **Antinuke only acts when it is sure.** It punishes only when an audit-log entry matches the target and the time, never the server owner, itself or whitelisted actors. Incidents are counted in a window: the first is recorded, the second is urgent (pings your alert roles), the third applies your configured response (`punishment`, default kick). Deleted channels and roles are recreated from snapshots **with new IDs**, and messages can't be restored. Recovery is reported honestly, including failures.
- **Whitelist scopes.** `antinuke` and `automod` are separate areas; `antinuke:ban` covers one protection. Members who leave lose their entries.
- **Voice Waiting Room** is built from a locked room plus a holding voice channel, because Discord has no native waiting rooms.
- **Anti-NSFW does not scan images.** `antinsfw` flips the setting like the other rules, but the reply says plainly that nothing is blocked yet and points to Discord's own filter.
- **Per-server bot avatar, banner and bio** (server owner only, needs Prestige) depend on Discord allowing it for your bot. If Discord refuses, the command says so and nothing is reported as changed. Nickname works normally.
- **Tag roles** match the tag text in a member's display name (Discord's official server tags aren't visible to bots).
- **Ticket notes** are private to staff. Use `/ticketnote`; a typed `Cticketnote` is deleted straight away but is briefly visible in the ticket.
- **Snipes** live in RAM only, for at most one hour. **Jail** survives restarts; someone jailed who leaves and rejoins is jailed again.

## Versions

Python 3.12. `discord.py>=2.6,<3`, `asyncpg>=0.30,<1` (`requirements.txt`). The build sandbox had no network, so these were not installed or run there. After your first good deploy you can pin exact versions.

## Environment variables

`ENABLE_PRESENCE_INTENT` (optional, `1` to turn on) is needed only for status rep, after enabling the Presence Intent in the Developer Portal.

`DISCORD_TOKEN`, `BOT_OWNER_ID`, `DATABASE_URL` (required). `SUPPORT_URL`, `SYNC_GUILD_ID`, `LOG_LEVEL`, `LOG_FORMAT=json`, `NIXPACKS_PYTHON_VERSION=3.12` (optional). See `.env.example`. Never commit real values.

## Discord permissions by module

| Module | The member needs | The bot needs |
|---|---|---|
| Home, Utility | nothing (snipe, editsnipe, clearsnipe: Manage Messages) | View Channel, Send Messages, Embed Links, Read Message History |
| Whitelist, Setup, Logging, Antinuke, Prestige | server owner or Administrator | per module below |
| Antinuke | Administrator | View Audit Log, Manage Roles, Manage Channels, Manage Webhooks, Kick Members, Ban Members |
| Moderation | the matching permission (Ban Members, Kick Members, Moderate Members, Manage Roles, Manage Channels, Manage Messages) | the same, plus a role above the target |
| Automod, Welcome | Manage Server | Manage Messages, Moderate Members (automod); Send Messages, Embed Links (welcome) |
| Logging | exactly two channels: `#logs` for ordinary events (messages deleted or edited with who deleted them when the audit log proves it, joins, leaves, roles, channels, voice, avatars) and `#security-logs` for antinuke alerts. Cards are posted by a "HOPELESS Logging" webhook so they look separate from the bot |
| Leveling | XP for chatting, `rank`, `leaderboard`, `levelrole`, level-up cards, `setlevellog` |
| Reputation | a role while a member's custom status contains your text or while they wear this server's tag, **removed again automatically** when they stop (`setreprole`, `setreptarget`, `rep`, `settagrole`, `reptag`, scans and checks) |
| Extra Owners | `extraowner add/remove/list`: owner-level control of one server only, never global |
| Tickets | Manage Server | Manage Channels, Manage Roles |
| Voice | Manage Server to set up. On someone else's room: Manage Channels, plus Move Members to kick, invite, block or use the waiting room | Manage Channels, Move Members |

The invite link from `Cbotinvite` requests only these, never Administrator. The full per-command matrix is `PERMISSIONS.md`.

## Layout (upload edition)

Every file sits in one folder so it can be uploaded from an iPhone in one go. The prefixes show where a file belongs: `core_` building blocks, `svc_` services, `db_` database, `cog_` one per module, `view_` interactive components. `bot.py` is the entry point, the `000N_*.sql` files are the migrations (applied automatically; never edit a deployed one), `hopeless_banner.jpg` is the help image. To add your own emoji image, upload `emoji_<name>.png` (for example `emoji_success.png`). The full test suite lives in `hopeless-bot.zip`.

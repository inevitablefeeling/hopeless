# HOPELESS checklist

Legend: **[x]** done and verified as stated, **[~]** written but not yet run against real Discord or PostgreSQL, **[ ]** not done.

## Honest summary

All modules are written, plus the second-round redesign: a permission model with a documented matrix, global administration and global bans, customizable tickets with a 3-second close, embed welcome messages, redesigned AFK, server info and stats, and the voice and autorole fixes. The pure logic is covered by unit tests that ran and passed (and a few were deliberately broken to prove the tests catch it). The Discord-facing code and the SQL have **never** run against Discord or a real database, because the build sandbox had no network and no discord.py. Static checks (imports, undefined names, duplicate commands, registry parity) did run and are clean. Expect small mistakes on first launch; the Railway logs will show them, and `Cping` / `Chelp` are the first things to try.

## Modules

| Module | Code | Logic unit-tested | Run on Discord |
|---|---|---|---|
| Home, help panel | [x] | [x] counts, emoji fallback | [~] |
| Whitelist | [x] | [x] | [~] |
| Utility | [x] | [x] snipe, AFK, stats | [~] |
| Moderation (all 18 plus jailrole) | [x] | [x] hierarchy, durations, jail role restore | [~] |
| Logging (Logs category, alert roles) | [x] | [x] routing | [~] |
| Welcome (embeds, preview) | [x] | [x] placeholders, colours, links | [~] |
| Antinuke (25 protections) | [x] | [x] thresholds, attribution, escalation, never-punish rules | [~] |
| Automod | [x] | [x] links, mentions, spam, raid windows | [~] |
| Tickets (designer, panels, 3-second close, transcripts) | [x] | [x] limits, ordering, delay, transcript text, naming | [~] |
| Voice (all 15 panel buttons, one-command setup) | [x] | [x] naming | [~] |
| Setup and templates | [x] | [x] planner, limits, built-ins | [~] |
| Prestige | [x] | [x] cost rule, image validation, tag match | [~] |
| Owner staff commands | [x] | [x] | [~] |
| Global administration (bans, perms, audit) | [x] | [x] ban rules, expiry, permission names | [~] |
| Permission model and matrix | [x] | [x] truth tables, invariants, mutation-checked | [~] |

## Acceptance checklist

- [x] Bot is named HOPELESS; reference material examined; every mapped command name is in the registry (tested)
- [x] The two renamed modules are Prestige and Voice; no old names in user-facing registry text (tested)
- [x] Default prefix `C`; prefix is per server, persists and resets per server (tested)
- [~] Prefix commands use the configured prefix; slash and prefix share one implementation (hybrid commands)
- [x] Slash names follow Discord rules (tested); [~] registration with Discord
- [x] Command and module counts come from the registry; startup compares the registry with loaded commands and logs mismatches
- [~] Help panel works with the supplied emoji and fallbacks (fallback logic tested)
- [x] Global staff commands are owner-only; owner read from `BOT_OWNER_ID` on every call (tested)
- [x] Permission rules: moderators can't reach admin or global commands, a server owner is not a bot owner, a global admin has only granted powers (tested)
- [~] Server permissions checked at execution time on real Discord
- [~] Voice staff actions need Move Members and Manage Channels (logic written; not run on Discord)
- [~] Closed tickets are deleted after the configured delay with a transcript attempt first (not run on Discord)
- [~] Autorole retries, logs why it fails and waits for membership screening (not run on Discord)
- [~] Antinuke modules perform real actions from real events; thresholds configurable; recovery honest
- [x] Whitelist exemptions independent per area and module (tested)
- [~] Automod, Moderation, Welcome, Logging, Tickets, Voice, Templates, Prestige behave as described
- [x] Guild data isolated (tested for prefix, whitelist, AFK)
- [x] Migrations: ordering, checksum protection, idempotence tested with a fake connection; [~] the SQL on real PostgreSQL
- [x] Secrets only in environment variables; placeholders in `.env.example`
- [~] Deployment guide written for iPhone; not followed end to end yet
- [x] Known limitations documented below

## Tests

Command: `python -m unittest discover -s tests -t .`

- **Ran and passed:** 202 tests (structured project and upload edition give the same result).
- **Ran and failed:** none.
- **Skipped here (need discord.py and asyncpg):** 3 smoke tests in `tests/test_discord_glue.py` (load every cog, registry parity, help panel).
- **Static checks that ran clean:** every file compiles; no undefined names; every `from x import y` and every `module.attribute` on project modules resolves; no duplicate command names or aliases; the registry matches the commands the cogs define (151 prefix commands, 143 slash paths, 79 top-level slash commands, limit 100).
- **Bugs found and fixed by those checks this round:** a broken import in the upload edition (a comment after an import confused the file-flattening script) that would have crashed every moderation check.
- **Not run:** anything against real Discord or PostgreSQL. Cogs, views, the new card layout (Components V2) and the new SQL (migration `0004_redesign.sql`, checked by hand against the earlier schema) have not run.

## Limitations and decisions

- Prefix `C` collides with ordinary words.
- Antinuke can't act without View Audit Log, and can't punish above its own role. Recreated channels and roles get new IDs; messages are gone.
- Voice Waiting Room is a locked room plus a holding channel (Discord has no native feature).
- Anti-NSFW does not scan images; its toggle says so.
- The emoji copy at startup needs discord.py 2.5 or newer and uses Discord's public emoji CDN; if either fails, normal emojis are used.
- Per-server bot avatar, banner and bio depend on Discord; nickname works.
- Tag roles match display-name text.
- Templates never delete anything and skip uncategorised channels and members' overwrites.
- Role-all is deliberately slow (about 0.5s per member).
- Moderator roles only narrow access; they never grant a Discord permission.
- Snipe commands are moderator-only (they can show deleted messages).
- Closed tickets are deleted, not archived, and can't be reopened. Transcripts are text files of up to the last 1000 messages. If no transcript destination or ticket-logs channel exists, the close message says the transcript was not saved.
- Components V2 cards fall back to normal embeds if discord.py or Discord refuses them. Buttons and menus stay in classic embeds.
- Global bans stop bot use only; they never ban from a Discord server.
- Autorole's root cause on your server could not be confirmed from here. It now logs and explains every failure in the security log, so the next failure names its reason.
- Unfinished or unverified items are marked [~] or [ ] above.

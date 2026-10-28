# HOPELESS on an iPhone

Use **hopeless-upload.zip**. Every file inside sits in one folder, so you can upload them all in one go. No folders to create, no workflow, no tricks.

## Already running the old version? Update in 5 minutes

1. Save the new **hopeless-upload.zip**, tap it in Files to unzip.
2. On GitHub open your repo, **Add file**, **Upload files**, **choose your files**, open the unzipped folder, **Select** then **Select All**, and commit. Files with the same name are replaced, and new ones (like `0004_redesign.sql`, `core_authz.py` and `cog_globaladmin.py`) are added. Don't delete anything.
3. Railway redeploys by itself and installs the newer discord.py from `requirements.txt`. In **View logs** you should see `applying migrations: ['redesign']` and `emoji sync`.
4. Try these: `Cticketdesign` (guided ticket editor with a preview), then `Cticketpanel` in your ticket channel to post or refresh the panel. `Cjtc` for voice. `Csetwelcome test` to preview welcome embeds. `Cmodrole add @role` if you want moderation limited to certain roles. `Chelp` shows everything.
5. **Bot owner only:** the new global tools are in `Chelpadm`. A new global administrator can only do `prestige` until you grant more, for example `Cgperm add @user gban`.
6. The permission list for every command is in **PERMISSIONS.md**.

## Part 1: Unzip it

1. In Claude tap **hopeless-upload.zip**, tap the share button, then **Save to Files**.
2. Open the **Files** app and tap the zip once. A folder called `hopeless-upload` appears next to it. It holds 57 files.

## Part 2: Discord application

1. Open discord.com/developers/applications in Safari and log in. Tap **New Application**, name it, create it.
2. Tap **Bot**. Tap **Reset Token**, confirm, and copy the token into a private note. Never share it.
3. On the same **Bot** page turn ON **Server Members Intent** and **Message Content Intent**, then tap **Save Changes**. Without this the bot won't start.
4. Get your own user ID: Discord app, Settings, Advanced, turn on **Developer Mode**. Long-press your own name anywhere and tap **Copy User ID**. Put it in your note. This is `BOT_OWNER_ID`.
5. Your custom emojis only show if the bot is in the server that owns them. Invite the bot there too, or you'll see normal emojis instead.

## Part 3: Put the files on GitHub

1. github.com, tap **+**, **New repository**. Name it `hopeless-bot`, choose **Private**, tap **Create repository**.
2. On the empty repo page tap **uploading an existing file**.
3. Tap **choose your files**. Go to Files, open the `hopeless-upload` folder. Tap **Select** (top right), then **Select All** (or tap every file), then **Open** or **Add**.
4. Wait until all 57 files are listed. This can take a minute on mobile data. Scroll down and tap **Commit changes**.
5. Check the repo shows `bot.py`, `Procfile`, `railway.json`, `requirements.txt` and the `cog_...`, `core_...`, `svc_...`, `db_...`, `view_...` files, plus `0001_core.sql` and `0002_modules.sql`. If the count is short, tap **Add file**, **Upload files** and add the missing ones.

## Part 4: Run it on Railway

1. railway.com, log in with GitHub. **New Project**, **Deploy from GitHub repo**, pick `hopeless-bot`. The first build may fail until the variables exist. That's fine.
2. In the same project tap **New** (or **+ Create**), **Database**, **Add PostgreSQL**.
3. Tap your bot service (not the database), then **Variables**, and add:
   - `DISCORD_TOKEN` = your token
   - `BOT_OWNER_ID` = your user ID
   - `DATABASE_URL` = tap **Add reference** and pick the database's `DATABASE_URL` (or type `${{Postgres.DATABASE_URL}}`, with your database service's real name instead of `Postgres`)
   - `NIXPACKS_PYTHON_VERSION` = `3.12`
   - optional `SUPPORT_URL` = your https Discord invite link
   - optional `SYNC_GUILD_ID` = a test server's ID, so slash commands show up there instantly
4. Tap **Deployments**, then **Redeploy**, then **View logs**. You want to see: `database pool ready`, `applying migration 0001_core`, `applying migration 0002_modules`, `synced ... application commands`, `ready as ...`.
5. Pricing and free allowances change. Check railway.com/pricing before relying on a free plan.

## Part 5: Invite it and try it

1. Developer portal, **OAuth2**, **URL Generator**. Tick `bot` and `applications.commands`. For Bot Permissions tick Administrator while you test in your own server (the bot itself never asks for it in its own invite link). Copy the link, open it, pick your server, **Authorize**.
2. In your server, Server Settings, Roles: drag the bot's role **above** the roles it needs to manage.
3. Try `Chelp`, `/help`, `Cping`, then `/setup` for guided setup with previews. If slash commands don't appear, wait a minute or run `Csync`.

## Updating later

On GitHub open a file, tap the pencil, change it, **Commit changes**. Railway redeploys by itself. To replace files, upload them again with the same names (GitHub overwrites).

## Problems

- **Bot offline**: Railway, View logs. "Privileged intents" means Part 2 step 3 wasn't saved. "Discord rejected the token" means a wrong or reset token.
- **"Could not reach the database"**: `DATABASE_URL` must be a reference to the database, not typed by hand.
- **Railway says it doesn't know what to run**: make sure `Procfile` and `railway.json` uploaded.
- **`chelp` does nothing**: the prefix is a capital `C`. Mention the bot to see the prefix.

## Optional: the workflow method (only if you want to use hopeless-bot.zip)

`hopeless-bot.zip` keeps the original folder layout and the test suite. GitHub can unzip it for you with the file **unpack.yml**, but the file only works if it sits at exactly `.github/workflows/unpack.yml`.

1. In your repo tap **Add file**, **Create new file**. In the name box type `.github/workflows/unpack.yml` (typing each `/` makes a folder). Paste the whole text of unpack.yml into the big box. Commit.
2. **If you already uploaded unpack.yml and Actions shows nothing**: it's sitting in the wrong place. Open it, tap the pencil, put the cursor at the very start of the name box, type `.github/workflows/` in front of `unpack.yml`, and commit. That moves it.
3. Upload `hopeless-bot.zip` to the repo (Add file, Upload files). The workflow then runs by itself. You can also open the **Actions** tab, tap **Unpack HOPELESS**, then **Run workflow**.
4. If it fails with a permissions message: repo Settings, Actions, General, Workflow permissions, **Read and write permissions**, Save, run it again.

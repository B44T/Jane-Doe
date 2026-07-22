# Jane Doe by B4T

One Python bot replacing reaction-role, moderation, birthday, ticket, welcome/leave/boost, poll, giveaway, emoji-copy and glued-message bots, with a private Flask dashboard.

The message editor loads, previews and edits persistent buttons and dropdowns, including the saved private responses from Abouts posts. The separate **Abouts** editor publishes an embed with a customizable dropdown whose options each send a private dismissible response. **All bot embeds** can open or permanently delete bot messages. Server emoji tools show static/animated capacity and support upload, resizing and renaming. The Overview includes a server-specific bot name and avatar editor.

## Start locally

1. Install Python 3.11 or newer.
2. Open a terminal in this folder and run `python -m pip install -r requirements.txt`.
3. Rename `.env.example` to `.env`.
4. Open `.env` and paste the bot token after `DISCORD_TOKEN=`.
5. Change `DASHBOARD_SECRET` to a private password.
6. Run `python bot.py`.
7. The console prints the dashboard URL and reports when Discord and Railway are ready.

`bot.py` starts both the Discord connection and the dashboard. `run.py` remains an internal launcher and does not need to be started directly.

## Railway deployment

1. Reset the bot token in Discord Developer Portal; never deploy a token that has been shared in chat.
2. Put this folder in a private GitHub repository, or deploy the folder with the Railway CLI.
3. Create a Railway project and deploy the repository. `railway.json` starts `python bot.py` and supplies the health check.
4. Add a persistent Railway volume and mount it at `/data`.
5. Add these Railway Variables: `DISCORD_TOKEN`, `APPLICATION_ID`, `DISCORD_PUBLIC_KEY`, `DASHBOARD_SECRET`, and `DATA_DIR=/data`. `GUILD_ID` remains optional.
6. In the service Networking settings, generate a Railway domain. Railway supplies `PORT` automatically; do not create a `PORT` variable yourself.
7. Open the generated HTTPS domain and sign in with `DASHBOARD_SECRET`.

The Discord bot and dashboard run in one Railway service. Waitress serves the dashboard with eight worker threads, and `/health` keeps Railway's web health check available while separately reporting Discord readiness. The volume preserves SQLite records and uploaded images/GIFs across deployments. Because this build targets one server, it retains that server's member records so leave logs can accurately show the departed member's roles and profile.

Persistent buttons and dropdowns depend on their saved callback configuration in `bot.db`. Railway keeps it on the `/data` volume. Local Windows runs now keep it in `%USERPROFILE%\\.jane-doe-by-b4t` and automatically recover the most complete database from older Jane Doe download folders when first upgrading, so replacing the program folder does not disconnect previously posted components.

Every persistent ticket, poll, role, Abouts, message-action and glue component is restored at startup. A second fallback dispatcher handles an older component directly from its persistent custom ID if Discord misses the normal restored-view registration, preventing silent interaction timeouts while its saved configuration exists.

## Discord portal

Enable **Server Members Intent** and **Message Content Intent**. Keep the bot role above every role it needs to assign. Do not give the bot Administrator.

## Commands

- `/birthday month day [year]`
- `/warn member reason`
- `/timeout member minutes [reason]`
- `/kick member [reason]`
- `/ban member [reason]`
- `/purge amount`
- `/stealemoji emoji [name]`
- `/poll question option_1 option_2 [option_3] [option_4]`
- `/event name starts_in_minutes duration_minutes location [description]`
- `/confess message` — anonymously posts to the configured confession channel
- `/hug`, `/kiss`, `/slap`, `/pat`, `/cuddle`, `/bite` — uses dashboard GIF lists

The visual dashboard handles embeds, announcement settings, ticket panels, reaction roles, giveaways, emojis, glued messages, birthdays and preview-before-delete purges. The **Other** page shows upcoming birthdays and only deletes the exact messages shown in its purge preview. Birthday editing is handled by `/birthday`; announcement text and channel remain centralized under **Announcements**.

Uploaded action GIFs are saved immediately, restored with visual previews, randomly selected by their slash commands, and removable through the themed confirmation dialog. Confession appearance, uploaded assets, glued-message controls, checkboxes, drafts and other saved settings reload across restarts.

The **Commands** area beneath Server Emojis lists every enabled slash command, explains it, links to related dashboard sections, and can permanently remove a command from Discord. Destructive dashboard actions use the themed confirmation dialog instead of browser alerts.

All Bot Embeds uses a permanent SQLite index of message IDs, channels, content and embed data. Opening the page performs no Discord history scan; only the explicit Refresh button scans the latest 100 messages in readable channels to recover older unindexed posts. Navigating between dashboard pages or deleting one cached message does not trigger another scan. Every Discord message preview uses the selected server's current bot nickname and avatar and updates immediately after the server profile is changed.

Every anonymous confession includes customizable Submit and Reply buttons. Their names default to **Submit a confession** and **Reply anonymously**; each optional emoji may be Unicode, a custom Discord emoji, or blank. Submit opens a private modal, while Reply posts a numbered anonymous response inside a thread attached to that confession. Members may also type normally in the configured confession channel: the bot copies attachments, deletes the identifiable original, and reposts it anonymously. `/confess` remains available. Identity lookup commands and stored author mappings are removed.

The **Action logs** dashboard page controls member joins, member leaves, message deletions, message edits, and voice joins/leaves/moves independently. Every log embed includes the member's display name, `@username`, mention and profile picture. Join logs show ordinal member count, exact account age and a configurable new-account warning; leave logs preserve role mentions. Automatic-confession source deletions are suppressed from logs so anonymous content is never exposed.

Ticket panels support a single button or a dropdown with up to 25 described ticket types. Role panels support up to 25 saved role choices displayed as buttons or a multi-select menu. Panel definitions, GIF URLs, confession records, poll votes, birthdays, drafts and settings persist across restarts.

Message Editor supports custom link, private-response and role-toggle buttons. Poll choices use individual add/remove rows. Events may optionally post an announcement with link buttons. Giveaways support a custom Unicode or server entry emoji. Glued messages can include a customizable button that reveals a saved copy-friendly template privately.

Slash commands are synced directly to Discord at startup so they appear correctly. This build is optimized for one server: set `GUILD_ID` to that server's ID. Discord.py's gateway cache keeps custom emojis current without restarting or repeatedly fetching them from Discord; the dashboard checks the local cache once per minute and also offers manual refresh.

## Notes

- The public key and application ID are not secrets and are preconfigured.
- Never share `.env`, `data/bot.db`, or the bot token.
- SQLite data is stored in `DATA_DIR/bot.db` (normally `/data/bot.db` on Railway). Back up that file before moving hosts.
- This release uses a password-protected local dashboard. Discord OAuth login can be added once a permanent dashboard domain exists.

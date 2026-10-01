# Quantix Discord Bot

A production-ready Discord bot starter for the Quantix community.

## Features
- Slash commands
- Ticket panel with buttons
- Welcome messages
- Moderation: clear, kick, ban, timeout
- Announcements
- Giveaways
- User/server info
- Customer verification (local demo codes + optional website API hook)
- Automatic roles after successful verification
- SQLite database for settings, verification codes, tickets and giveaways
- Docker/Railway-ready 24/7 deployment

## Local setup
1. Install Python 3.12+.
2. Create a Discord application and bot in the Discord Developer Portal.
3. Enable Server Members Intent.
4. Copy `.env.example` to `.env`.
5. Put your bot token in `.env`.
6. Run:
   ```bash
   pip install -r requirements.txt
   python bot.py
   ```

## Invite permissions/scopes
Use OAuth2 scopes `bot` and `applications.commands`. Give the bot permissions needed for moderation, role management, channels, messages and embeds. Keep the bot role above the customer/moderation roles it needs to manage.

## First commands
- `/setup` — saves important server IDs
- `/ticketpanel` — posts the ticket button panel
- `/createcode` — creates a one-use verification code
- `/verify` — customer enters a code and receives the configured role
- `/announce` — professional announcement embed
- `/giveaway` — start a giveaway

## 24/7 Railway deployment
1. Push this folder to a GitHub repo.
2. Create a Railway project from the repo.
3. Add `DISCORD_TOKEN` as an environment variable.
4. Deploy one replica with sleeping disabled.

The bot connects outbound to Discord. Two-way desktop license-manager sync additionally uses the HTTPS service domain.

## V2.3 — persistent Rules verification
- `/rulespanel` posts the Quantix rules panel with a persistent **I agree — verify me** button.
- The button survives Railway/bot restarts because its View is re-registered on startup.
- Clicking it immediately acknowledges the interaction, preventing Discord's "application did not respond" timeout.
- Verified role: `1555124964175380502` by default. Optional Railway override: `VERIFIED_ROLE_ID`.
- Optional `RULES_CHANNEL_ID` makes `/rulespanel` post into a specific rules channel.
- The bot needs **Manage Roles**, and its bot role must sit above the Verified role.
- Delete the old broken rules message and run `/rulespanel` once to create the new persistent panel.


## V2.4 — Discord License Key Management

The QTX2 integration replaces the V2.4 random-code generator with the existing Pro/Elite utility's signed offline license format. Create, extend and reset-HWID issue a signed key and DM it after database registration. Resend and signed revocation import/export commands are included. Existing utilities validate these keys offline; database revocation alone cannot remotely disable them. See **LICENSE_KEY_SETUP.md** for signing-key variables, migration, tests and deployment. For Railway, mount a Volume at `/data` and set `DB_PATH=/data/quantix.db` so licenses survive redeploys.

## Desktop manager synchronization

Updated Pro and Elite managers synchronize signed licenses and revoke/restore/delete state every 30 seconds while open. The HTTPS endpoint `/api/manager/sync/{tier}` authenticates edition-specific signed requests with short-lived timestamps and one-use nonces. Version checks prevent stale desktop restores. No signing private key is transmitted by the managers. Existing JSON databases are preserved and backed up before first merge.

## Honeypot moderation

Human messages in a text channel named exactly honeypot (case-insensitive) trigger a DM with https://discord.gg/quantix, followed by a kick. The configured GUILD_ID limits the rule to that server. Bot/webhook/system messages are ignored. The server owner and members at or above the bot's highest role cannot be kicked. The bot requires View Channel and Kick Members. Closed DMs do not prevent the kick; delivery and kick failures go to the configured moderation log. Existing messages are not scanned. No additional Railway variables are required.


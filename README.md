# AU Schools Discord Bot

Python discord.py community bot. Minecraft integrations and unselected cogs are excluded by the explicit enabled-cog list in main.py. This repository contains only the active Discord bot modules.

## Setup
Install requirements.txt, configure .env, then run python main.py. Never commit .env. The web server exposes only /health. Persistent storage uses DATA_DIR.

## Roles and channels
No new role assignments are selected by this rebrand. Existing saved settings are preserved. Set ACCESS_ROLE_ID to the agreed staff role; with 0 only administrators pass the global command gate. Configure application managers with /apply set-reviewer-role, approval roles with /apply set-approve-role, pings with /apply set-ping-role, and channels with /apply set-channels. Ticket staff/admin roles remain configurable through /config commands. Old application presets are no longer seeded.

## Branded content
/server-info shows AU Schools community information and live Discord server statistics. /edit-serverinfo edits the community description. /ticket panel and /apply panel publish branded panels. Existing messages in Discord must be edited/reposted after restart; code changes do not rewrite already-posted messages. The supplied logo is applied as the bot avatar on startup. A banner can be configured separately in Discord Developer Portal.

## Compatibility
Persistent component custom IDs are retained so existing ticket/application buttons continue working. Historical records and existing configured roles are preserved. No Minecraft HTTP routes are served.

## AU Schools identity
Default guild: 1563853559944974366. Green embeds match the included green and gold logo in assets/. Never put the Discord token in GitHub: set DISCORD_TOKEN privately in the hosting environment. Enable Server Members and Message Content intents in Discord Developer Portal. Set GROQ_API_KEY only if using AI ticket summaries.

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
Default guild: 1563853559944974366. Green embeds match the included green and gold logo in assets/. Never put the Discord token in GitHub: set DISCORD_TOKEN privately in the hosting environment. Enable Server Members and Message Content intents in Discord Developer Portal. AI ticket summaries have been removed.

## Community dashboard
`/dashboard` opens a private member dashboard. Staff with Manage Server can publish a persistent dashboard with `/dashboard channel:#your-channel`. It includes Guidelines, Server Information, Community, Help & Support, and Applications. Dashboard navigation and the nested support/application menus respond privately.

## Support workflow
Ticket controls: Claim / Release, Close Ticket (requires a reason), Save Transcript, Set Priority, Add Member, Remove Member, Request Management, Staff Resources. Ticket openers can export and close their own tickets; management/staff access is checked on each staff action. Staff Report tickets use the configured management role, and their transcripts are not sent to the ordinary ticket log. Existing ticket control messages are refreshed when the bot reconnects. Transcript generation must succeed before a manual close continues. Exports include up to 1,000 messages. Discord administrators can always access tickets.

## Application workflow
Application channels and send/embed/attachment permissions are checked before the interview starts. One interview per member and one pending/on-hold submission per position are allowed. Interviews support cancel and a five-minute timeout per question. Full answers are attached to the private review message; concise confirmations do not reveal the review channel. Reviewers must provide a reason for Approve, Decline or Hold. Final review decisions are protected against duplicate clicks and retained in the review channel; approval-role failures are reported to the reviewer. Role and channel settings remain editable with `/apply` and `/ticket config`.

## Offline verification
Run `python tests/smoke_test.py` after installing requirements. Tests use a temporary database and mocked Discord interactions; they do not send messages or log the bot in.

The dashboard uses Discord Components V2: one green-accent card with a heading, welcome text, dropdown and green/gold brand strip. Requires discord.py 2.6 or newer. Repost existing dashboards with /dashboard channel:#your-channel to use the new layout.

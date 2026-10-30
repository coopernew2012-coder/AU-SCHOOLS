# AU Schools Discord Bot

Python Discord bot for moderation, private support threads and reviewed Minecraft-server partnerships. The dashboard, captcha verification, mass kick, blacklisted-addons cards, legacy DM applications, and configuration/panel-editing slash commands have been removed from the active bot.

## Run
Install requirements.txt, configure the ignored .env with DISCORD_TOKEN, and run python main.py. Enable Server Members and Message Content intents. Keep DATA_DIR persistent; it contains the SQLite database and transcripts. No secrets belong in GitHub.

## Ticket commands
- `/ticket panel action:send channel:#channel` publishes the five-option dropdown.
- `/close` closes the current ticket with a reason.
- `/claim` and `/unclaim` manage staff assignments.
- `/transcript` exports the current ticket.
- `/partnerclose` closes a partnership ticket.
- `/partnership request-proof` restarts the ad/invite/tags/proof steps if corrections are needed.
- `/partnership reset-cooldown` lets staff reset a member's partnership cooldown.
Normal moderation and information commands remain. Editing commands, `/welcome` tests/settings and role configuration menus are not exposed. Existing automatic welcome/autorole behaviour and saved roles are preserved.

## Private threads
The panel offers Support, Partnership, Player/Staff Report, Staff Apply, and Other Inquiries. No paid advertisements. Threads are created in a text channel under category 1563853563468447867; `thread_ticket_parent` stores its channel ID. The opener is added automatically; the relevant saved staff/reviewer role members are added if configured. Users with Discord Manage Threads or Administrator can see private threads. Close archives and locks the thread and saves a local transcript (up to 1,000 messages). Ticket numbers and in-progress steps survive restarts. One open ticket per member; partnerships have a 12-hour cooldown.

Staff Apply uses six questions directly in the ticket, matching the reference. Legacy DM application panels are no longer used.

## Partnerships
Minecraft servers only, minimum 30 members, no fake or botted members. The bot checks invite validity and approximate member count; reviewers must verify Minecraft focus, genuine membership, and screenshot evidence. Flow: advertisement plus invite -> up to five selectable tags -> our advertisement/ping agreement -> uploaded screenshot -> staff review. Approved ads post to 1563853561727811622, then the ticket archives. Publication failures leave the ticket available for retry; duplicate approval clicks do not post twice. Accept approves; Deny requires three distinct reviewer votes, each with a reason. Proof is reuploaded to the review message.

Ping rules by partner size (ours / theirs):
- 30–49: none / @everyone
- 50–99: @here / @everyone
- 100–149: @here / @here
- 150–199: @everyone / @here
- 200+: @everyone / none

Informational mentions in tickets are suppressed; only the agreed ping in the approved public partnership triggers notifications. The invite and ping tier are rechecked at approval. A changed tier needs new proof. Our exact ad is bundled in assets/partnership-ad.txt and seeds new installations. Other settings are changed directly in configuration/SQLite as requested; no editing slash commands are available.

## Offline tests
Run `python tests/smoke_test.py`. It uses a temporary database and mocked Discord interactions; it does not publish messages or log in.

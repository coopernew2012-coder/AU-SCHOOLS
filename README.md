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
The panel offers Support, Partnership, Player/Staff Report, Staff Apply, and Other Inquiries. No paid advertisements. Threads are created in a text channel under category 1563853563468447867; `thread_ticket_parent` stores its channel ID. Review messages include Discord username and user ID; no application text file is attached. Long answers are split across embed messages without truncation. The opener is added automatically; the relevant saved staff/reviewer role members are added if configured. Users with Discord Manage Threads or Administrator can see private threads. Close saves a local transcript and deletes the thread (up to 1,000 messages). Ticket numbers and in-progress steps survive restarts. One open ticket per member per category; partnerships have a 12-hour cooldown.

Staff Apply starts a six-question DM interview. On completion, it creates a private review thread, adds and pings the applicant, posts all answers as embed fields, and shows Deny, Accept and Hold buttons. Decisions require authorised staff and a reason. Hold keeps the thread open; final decisions notify the applicant and save a transcript before deleting the review thread. No application-editing commands are exposed.

## Partnerships
Minecraft servers only, minimum 30 members, no fake or botted members. The bot checks invite validity and approximate member count; reviewers must verify Minecraft focus, genuine membership, and screenshot evidence. Flow: advertisement plus invite -> up to five selectable tags -> our advertisement/ping agreement -> uploaded screenshot -> staff review. Approved ads include selected lowercase hashtags below the advertisement and post to 1563853561727811622, then the ticket is deleted. Publication failures leave the ticket available for retry; duplicate approval clicks do not post twice. Accept approves; Deny immediately rejects with one authorised reviewer and a reason. Proof is reuploaded to the review message.

Ping rules by partner size (ours / theirs):
- 30–49: none / @everyone
- 50–99: @here / @everyone
- 100–149: @here / @here
- 150–199: @everyone / @here
- 200+: @everyone / none

Informational mentions in tickets are suppressed; only the agreed ping in the approved public partnership triggers notifications. The invite and ping tier are rechecked at approval. A changed tier needs new proof. Our exact ad is bundled in assets/partnership-ad.txt and seeds new installations. Other settings are changed directly in configuration/SQLite as requested; no editing slash commands are available.

## Offline tests
Run `python tests/smoke_test.py`. It uses a temporary database and mocked Discord interactions; it does not publish messages or log in.

Removed commands: warnings (warn/warnings/clear-warnings), user-info, slowmode, role-info, rules, ping, massban and avatar. Historical database records are preserved.

Opening messages have no Close Ticket button or partnership requirements block. Rules and tier checks still apply internally. A partner below 30 members is mentioned in a rejection message and the thread is deleted after a persisted 10-second deadline, which resumes after restarts. New threads open in channel 1563853562176344166. Run python tests/dm_flow_test.py to verify the DM application and deletion changes offline.

Approved partnership ads are plain Discord messages, with the agreed ping first and selected hashtags below the ad. Ads longer than one Discord message are split without dropping text, and sent-part IDs are persisted so retries can resume.

## AU Schools role thresholds
- Staff applications and Player/Staff Report: Senior Manager 1563853560008015933 and above.
- All other ticket categories: Moderator 1563853559970136219 and above.
- Kick, ban, timeout/mute and reversals: Admin 1563853559970136222 and above; targets must rank below the moderator.
- Accepted staff: grant Moderator 1563853559970136219 before confirming acceptance.
- On join: Member 1563853559944974375.
Thread membership reconciles on reconnect and role changes. Applicants/openers retain access to their own threads. Discord Administrator and Manage Threads permissions can independently bypass private-thread visibility; bot review actions still check the configured hierarchy. Native permission defaults do not hide moderation commands; the bot enforces thresholds at execution.

## Response card styling
Application DMs, questions, completion links, decisions, proof instructions, review cards, transcript downloads, and most moderation/information responses use native Components V2 containers with bold headings, status colours, dividers and contextual footnotes. Ticket panels, partnership ad text, tag selection, brief acknowledgements and dense history tables remain compact. Transcripts extract TextDisplay content from cards. Default mentions are suppressed; intentional applicant and partnership pings opt in explicitly.
All ticket threads remain under the existing Tickets parent. Discord staff-add system notices are retained, as requested, rather than weakening staff-app/report visibility.
Run python tests/card_style_test.py for layout and transcript coverage.

## Conversational ticket helper
Support, Player/Staff Report and Other Inquiries start with “How can I help you?” and identify the AI helper. Staff Apply and Partnership are excluded. The helper uses Groq with `GROQ_API_KEY` in the ignored `.env` and optional `GROQ_MODEL` (default `openai/gpt-oss-20b`). `assets/support-info.json` contains the verified IP, border and server/gameplay rules; missing version, edition or mod approvals go to staff.

Only the opener's messages in an open, unclaimed ticket trigger AI answers. When any other human sends a normal message or reply, the helper tags that person once, says goodbye and permanently hands over that ticket. Bot messages and Discord system notices do not trigger handoff. The pause survives restarts; pending AI answers are suppressed, and recent history is checked at startup for existing human conversations. `/unclaim` does not undo a human handoff. Asking for staff or finishing the conversation also pauses the assistant instead of repeating replies while staff are awaited.

A bounded history keeps each conversation separate across restarts and is removed when its thread is deleted. Reports notify Senior Manager and above; other support handoffs notify Moderator and above, at most once per five minutes per thread. `/claim` pauses the helper. API failures fall back to staff. The AI cannot run commands, change permissions or make moderation decisions. Conversational replies stay plain text; existing panels and application/partnership flows are preserved.

Run `python tests/support_ai_test.py` for exclusions, history isolation, staff roles, cooldowns and failure handling.

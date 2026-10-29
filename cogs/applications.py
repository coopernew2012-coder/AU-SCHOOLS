"""Application system — Python port of the Node.js bot's dropdown application flow.

Flow (same as the original):
  /apply panel  ->  panel embed with a category dropdown (Staff/Developer/Media)
      user picks a type     ->  cooldown check, then questions asked one-by-one in DMs
      all answered          ->  submission embed posted to the review channel
                                with Approve / Deny / Hold buttons
      staff decides (modal reason)  ->  applicant DM'd, approve grants a role,
                                deny starts a re-apply cooldown, result posted to
                                the approved/denied log channel

Everything is editable per guild: panel text, application types, questions,
channels, ping role, approve roles, reviewer role, cooldown hours.
"""

import io
import json
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import ACCENT_COLOR
from database import get_db, get_setting, set_setting
from utils import success_embed, error_embed

# ------------------------------------------------------------------
# Defaults (from the Node.js config.js APPLICATION_TYPES)
# ------------------------------------------------------------------

DEFAULT_TYPES: dict[str, dict] = {
    "Staff": {
        "emoji": "🛡️",
        "questions": [
            "Why do you want to become staff?",
            "Do you have prior moderation experience?",
            "How would you handle a conflict between two members?",
            "How many hours per week can you commit?",
            "Have you ever filled this position before in any other servers?",
            "What extent of knowledge/experience do you have with this role/position?",
        ],
    },
    "Developer": {
        "emoji": "💻",
        "questions": [
            "What programming languages do you know, and for how long?",
            "Link examples of past work (bots, websites, tools, repos).",
            "What have you built for communities before (bots, websites, tools, integrations)?",
            "Which technologies do you know: Python/discord.py, databases, web development, or other tools?",
            "How many hours per week can you commit?",
            "Have you ever filled this position before in any other servers?",
            "What extent of knowledge/experience do you have with this role/position?",
        ],
    },
    "Media": {
        "emoji": "🎬",
        "questions": [
            "Why do you want to join the media team?",
            "What content have you created before (videos, edits, thumbnails, graphics)?",
            "What software/tools do you use?",
            "How many hours per week can you commit?",
            "Have you ever filled this position before in any other servers?",
            "What extent of knowledge/experience do you have with this role/position?",
        ],
    },
}

DEFAULT_PANEL_TITLE = "AU SCHOOLS APPLICATIONS"

DEFAULT_PANEL_DESCRIPTION = 'Join the **AU Schools** team. Choose a position below to begin your application.\n\n**🛡️ Staff**\nSupport our members, uphold community standards and help keep AU Schools welcoming.\n\n**💻 Developer**\nBuild and maintain useful bots, websites and community tools.\n\n**🎬 Media**\nCreate engaging graphics, videos and promotional content.\n\n**Before you apply**\n• Enable DMs so we can send your questions.\n• Answer honestly and include examples where relevant.\n• Submit one application per position and wait for a decision.\n\nYour responses are reviewed privately by the application team. You will receive the outcome by DM when possible.'

DEFAULT_COOLDOWN_HOURS = 72  # 3 days, like the Node bot

# Pre-Developer panel text. If a guild's stored description is EXACTLY
# this (i.e. never customized), it is auto-migrated to the new default —
# custom panel text is never touched.
LEGACY_PANEL_DESCRIPTION = (
    "Choose the application that best matches what you're applying for. "
    "Please ensure you meet the requirements before submitting. "
    "Low-effort or joke applications will be denied.\n\n"
    "**Staff Applications**\n"
    "Help moderate the server, assist community members, and enforce the server rules.\n"
    "**Requirements:**\n"
    "- Active within the community\n"
    "- Mature and respectful\n"
    "- Good understanding of server rules\n\n"
    "**Builder Applications**\n"
    "Help create maps, spawns, arenas, and other server builds.\n"
    "**Requirements:**\n"
    "- Must be 13 years of age or older\n"
    "- Able to build to a high standard\n"
    "- Experience with server builds is recommended\n\n"
    "**Media Applications**\n"
    "Help create content, edits, thumbnails, and promotional media for the server.\n"
    "**Requirements:**\n"
    "- Must be 13 years of age or older\n"
    "- Prior content creation/editing experience\n"
    "- Able to consistently produce quality content\n\n"
    "**Important Information**\n"
    "- Do not submit troll or spam applications.\n"
    "- Put genuine effort into every response.\n"
    "- Staff may request proof of experience or previous work.\n"
    "- Any false or misleading information will result in an instant denial."
)

# Application roles and channels are configured with /apply commands.

# Question-per-message timeout, same as Node (5 minutes)
QUESTION_TIMEOUT = 300

# In-flight DM interviews (user_id -> True) so a user can't run two at once
_active_interviews: set[int] = set()


# ------------------------------------------------------------------
# Schema migration (add editable columns to the existing tables)
# ------------------------------------------------------------------

async def _migrate() -> None:
    """Create the application tables if missing and add editable columns.
    Self-healing so cog load never depends on init_db ordering."""
    db = await get_db()
    try:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS application_templates (
                guild_id              TEXT NOT NULL,
                app_name              TEXT NOT NULL,
                submission_channel_id TEXT,
                approved_channel_id   TEXT,
                denied_channel_id     TEXT,
                review_role_id        TEXT,
                approve_role_id       TEXT,
                submission_title      TEXT,
                submission_image      TEXT,
                submission_thumbnail  TEXT,
                PRIMARY KEY (guild_id, app_name)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS application_questions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                app_name   TEXT NOT NULL,
                question   TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS application_submissions (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id               TEXT NOT NULL,
                app_name               TEXT NOT NULL,
                user_id                TEXT NOT NULL,
                user_name              TEXT,
                answers_json           TEXT,
                submission_message_id  TEXT,
                submission_channel_id  TEXT,
                status                 TEXT DEFAULT 'pending',
                submitted_at           TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS application_cooldowns (
                guild_id  TEXT NOT NULL,
                user_id   TEXT NOT NULL,
                app_name  TEXT NOT NULL,
                denied_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, app_name)
            )"""
        )
        existing = set()
        async with db.execute("PRAGMA table_info(application_templates)") as cursor:
            async for row in cursor:
                existing.add(row[1])
        additions = {
            "emoji": "TEXT",
            "panel_title": "TEXT",
            "panel_description": "TEXT",
            "ping_role_id": "TEXT",
            "cooldown_hours": "INTEGER",
        }
        for col, coltype in additions.items():
            if col not in existing:
                await db.execute(f"ALTER TABLE application_templates ADD COLUMN {col} {coltype}")
        await db.commit()
    finally:
        await db.close()


async def ensure_defaults(guild_id: int) -> None:
    """Seed application types without assigning any roles or channels."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM application_templates WHERE guild_id=?",
            (str(guild_id),),
        ) as cursor:
            count = (await cursor.fetchone())["n"]
        if not count:
            for name, defn in DEFAULT_TYPES.items():
                await db.execute(
                    "INSERT OR IGNORE INTO application_templates (guild_id, app_name, emoji) VALUES (?, ?, ?)",
                    (str(guild_id), name, defn["emoji"]),
                )
                for i, q in enumerate(defn["questions"], start=1):
                    await db.execute(
                        "INSERT INTO application_questions (guild_id, app_name, question, sort_order) VALUES (?, ?, ?, ?)",
                        (str(guild_id), name, q, i),
                    )

        await db.commit()
    finally:
        await db.close()


async def get_types(guild_id: int) -> list[dict]:
    """Application types for a guild (seeded with defaults if empty)."""
    await ensure_defaults(guild_id)
    db = await get_db()
    try:
        async with db.execute(
            "SELECT app_name, emoji, submission_channel_id, approved_channel_id, "
            "denied_channel_id, ping_role_id, approve_role_id FROM application_templates "
            "WHERE guild_id=? ORDER BY rowid",
            (str(guild_id),),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_questions(guild_id: int, app_name: str) -> list[str]:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT question FROM application_questions WHERE guild_id=? AND app_name=? ORDER BY sort_order",
            (str(guild_id), app_name),
        ) as cursor:
            return [r["question"] for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_panel_text(guild_id: int) -> tuple[str, str]:
    """Panel title/description with defaults. A stored description that is
    byte-identical to the legacy (Builder-era) default is migrated to the
    current default; genuinely customized text is left alone."""
    title = await get_setting(guild_id, "apply_panel_title") or DEFAULT_PANEL_TITLE
    desc = await get_setting(guild_id, "apply_panel_description")
    if desc and desc.strip() == LEGACY_PANEL_DESCRIPTION.strip():
        await set_setting(guild_id, "apply_panel_description", DEFAULT_PANEL_DESCRIPTION)
        desc = DEFAULT_PANEL_DESCRIPTION
    return title, desc or DEFAULT_PANEL_DESCRIPTION


def plain_embed(title: str | None = None, description: str | None = None) -> discord.Embed:
    e = discord.Embed(color=ACCENT_COLOR, timestamp=discord.utils.utcnow())
    e.set_footer(text="AU Schools")
    if title:
        e.title = title
    if description:
        e.description = description
    return e


async def is_application_staff(interaction: discord.Interaction) -> bool:
    """Reviewer = Manage Server, or the configured reviewer role."""
    if isinstance(interaction.user, discord.Member):
        if interaction.user.guild_permissions.manage_guild:
            return True
        role_id = await get_setting(interaction.guild.id, "apply_reviewer_role")
        if role_id and str(role_id) in {str(r.id) for r in interaction.user.roles}:
            return True
    return False


# ------------------------------------------------------------------
# Cooldowns (denied applicants wait before re-applying)
# ------------------------------------------------------------------

async def cooldown_remaining(guild_id: int, user_id: int, app_name: str) -> tuple[float, int]:
    """Returns (remaining_seconds, cooldown_hours configured for this type)."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT cooldown_hours FROM application_templates WHERE guild_id=? AND app_name=?",
            (str(guild_id), app_name),
        ) as cursor:
            row = await cursor.fetchone()
        hours = (row["cooldown_hours"] if row and row["cooldown_hours"] else DEFAULT_COOLDOWN_HOURS)
        async with db.execute(
            "SELECT denied_at FROM application_cooldowns WHERE guild_id=? AND user_id=? AND app_name=?",
            (str(guild_id), str(user_id), app_name),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return 0.0, hours
        remaining = hours * 3600 - (time.time() - row["denied_at"])
        return (remaining if remaining > 0 else 0.0), hours
    finally:
        await db.close()


async def set_cooldown(guild_id: int, user_id: int, app_name: str) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO application_cooldowns (guild_id, user_id, app_name, denied_at) VALUES (?, ?, ?, ?)",
            (str(guild_id), str(user_id), app_name, time.time()),
        )
        await db.commit()
    finally:
        await db.close()


async def clear_cooldown(guild_id: int, user_id: int, app_name: str) -> None:
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM application_cooldowns WHERE guild_id=? AND user_id=? AND app_name=?",
            (str(guild_id), str(user_id), app_name),
        )
        await db.commit()
    finally:
        await db.close()


def format_remaining(seconds: float) -> str:
    hours = int(-(-seconds // 3600))  # ceil
    if hours >= 24:
        return f"{hours // 24} day(s)"
    return f"{hours} hour(s)"


# ------------------------------------------------------------------
# UI pieces
# ------------------------------------------------------------------

class ApplyPanelView(discord.ui.View):
    """Standing panel with the application dropdown. Survives restarts."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Choose a team to apply for",
        custom_id="bliss_app_select",
        min_values=1,
        max_values=1,
    )
    async def app_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        types = await get_types(interaction.guild.id)
        # Keep the dropdown in sync with config (rebuild options live)
        if types:
            select.options = [
                discord.SelectOption(
                    label=f"{t['app_name']} Application",
                    value=t["app_name"],
                    emoji=(t["emoji"] or "📝") if len(t["emoji"] or "") <= 3 else None,
                )
                for t in types[:25]
            ]
        choice = select.values[0]
        if not any(t["app_name"] == choice for t in types):
            await interaction.response.send_message(
                embed=error_embed("That application no longer exists."), ephemeral=True
            )
            return
        await start_application(interaction, choice)


class AppReviewButton(discord.ui.DynamicItem[discord.ui.Button],
                      template=r"app2-(?P<action>approve|deny|hold)-(?P<key>\d+)"):
    """Dynamic review button — matches Approve/Deny/Hold on any submission
    message, even ones posted before this deploy."""

    def __init__(self, action: str, key: str):
        label = {"approve": "Approve", "deny": "Deny", "hold": "Hold"}[action]
        style = {
            "approve": discord.ButtonStyle.success,
            "deny": discord.ButtonStyle.danger,
            "hold": discord.ButtonStyle.secondary,
        }[action]
        emoji = {"approve": "✅", "deny": "❌", "hold": "⏸️"}[action]
        super().__init__(
            discord.ui.Button(
                label=label, style=style, emoji=emoji,
                custom_id=f"app2-{action}-{key}",
            )
        )
        self.action = action
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match: "re.Match[str]"):
        return cls(match["action"], match["key"])

    async def callback(self, interaction: discord.Interaction):
        if not await is_application_staff(interaction):
            await interaction.response.send_message(
                embed=error_embed("You're not an application reviewer."), ephemeral=True
            )
            return
        await interaction.response.send_modal(ReviewDecisionModal(self.action, self.key))


def build_review_row(message_id: int) -> discord.ui.View:
    """Approve / Deny / Hold row keyed to a submission message."""
    view = discord.ui.View(timeout=None)
    for action in ("approve", "deny", "hold"):
        view.add_item(AppReviewButton(action, str(message_id)))
    return view


class ReviewDecisionModal(discord.ui.Modal, title="Provide a reason"):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        placeholder="Enter the reason for this decision...",
        max_length=1000,
        required=True,
    )

    def __init__(self, action: str, message_id: int):
        super().__init__()
        self.action = action  # approve | deny | hold
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        await finalize_decision(
            interaction, self.action, self.message_id, self.reason.value.strip()
        )


# ------------------------------------------------------------------
# Core flow
# ------------------------------------------------------------------

async def start_application(interaction: discord.Interaction, app_name: str) -> None:
    if interaction.user.id in _active_interviews:
        return await interaction.response.send_message("You already have an application in progress. Finish it or type cancel in DMs.", ephemeral=True)
    _active_interviews.add(interaction.user.id)
    try:
        await _run_application(interaction, app_name)
    except discord.HTTPException:
        if interaction.response.is_done():
            await interaction.followup.send("Your application could not be completed. Please contact staff or try again; nothing is confirmed until you receive a submitted message.", ephemeral=True)
        else:
            await interaction.response.send_message("Discord could not complete this request. Please try again.", ephemeral=True)
    finally:
        _active_interviews.discard(interaction.user.id)


async def _run_application(interaction: discord.Interaction, app_name: str) -> None:
    guild = interaction.guild
    user = interaction.user
    types = await get_types(guild.id)
    configured = next((t for t in types if t['app_name'] == app_name), None)
    channel_id = configured['submission_channel_id'] if configured else None
    channel = guild.get_channel(int(channel_id)) if channel_id and channel_id.isdigit() else None
    if not isinstance(channel, discord.TextChannel) or not all((channel.permissions_for(guild.me).view_channel, channel.permissions_for(guild.me).send_messages, channel.permissions_for(guild.me).embed_links, channel.permissions_for(guild.me).attach_files)):
        return await interaction.response.send_message(embed=error_embed("Applications for this team are temporarily unavailable. Please contact staff."), ephemeral=True)
    db = await get_db()
    try:
        async with db.execute("SELECT 1 FROM application_submissions WHERE guild_id=? AND user_id=? AND app_name=? AND status IN ('pending','on-hold')", (str(guild.id), str(user.id), app_name)) as cursor:
            pending = await cursor.fetchone()
    finally:
        await db.close()
    if pending:
        return await interaction.response.send_message("You already have an application awaiting review for this team.", ephemeral=True)

    remaining, _hours = await cooldown_remaining(guild.id, user.id, app_name)
    if remaining > 0:
        await interaction.response.send_message(
            embed=plain_embed(
                "Application on Cooldown",
                f"You were recently denied for **{app_name}**. You can re-apply in **{format_remaining(remaining)}**.",
            ),
            ephemeral=True,
        )
        return

    questions = await get_questions(guild.id, app_name)
    if not questions:
        await interaction.response.send_message(
            embed=error_embed(f"**{app_name}** has no questions configured yet. Notify staff."),
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=plain_embed(
            "Application Started",
            f"I've sent you a DM to begin your **{app_name}** application.",
        ),
        ephemeral=True,
    )

    try:
        dm = await user.create_dm()
        await dm.send(
            embed=plain_embed(
                f"{app_name} Application",
                f"Answer each question by sending a message here. "
                f"**{len(questions)} questions** — type `cancel` at any time to abort. "
                f"5 minute timeout per question.",
            )
        )
    except discord.HTTPException:
        await interaction.followup.send(
            embed=error_embed("I couldn't DM you — enable your DMs and pick again."),
            ephemeral=True,
        )
        return

    try:
        answers: list[tuple[str, str]] = []
        for i, q in enumerate(questions, start=1):
            embed = plain_embed(f"AU Schools | {app_name} — Question {i}/{len(questions)}", q)
            embed.set_footer(text="AU Schools • Reply below • cancel to exit • 5 minutes per question")
            await dm.send(embed=embed)

            def check(m: discord.Message, _uid=user.id):
                return m.author.id == _uid and isinstance(m.channel, discord.DMChannel)

            try:
                msg = await interaction.client.wait_for("message", check=check, timeout=QUESTION_TIMEOUT)
            except TimeoutError:
                await dm.send(embed=plain_embed(None, "Application timed out. You can start again from the panel."))
                return

            if msg.content.strip().lower() == "cancel":
                await dm.send(embed=plain_embed(None, "Application cancelled. Nothing was submitted."))
                return
            answers.append((q, msg.content))
    except discord.HTTPException:
        raise

    # Find the submission channel (per-type, then fallback to any type that has one)
    types = await get_types(guild.id)
    me = next((t for t in types if t["app_name"] == app_name), None)
    sub_channel_id = (me["submission_channel_id"] if me else None) or next(
        (t["submission_channel_id"] for t in types if t["submission_channel_id"]), None
    )
    sub_channel = guild.get_channel(int(sub_channel_id)) if sub_channel_id else None
    if sub_channel is None:
        await dm.send(embed=plain_embed(None, "❌ Submission channel misconfigured. Notify staff."))
        return

    embed = plain_embed(f"New Application: {app_name}", f"Submitted by {user.mention} (`{user.id}`)")
    budget = 4200
    for q, a in answers[:20]:
        text = (a or "No answer")[:min(600, max(1, budget - len(q[:160])))]
        if budget < len(q[:160]) + len(text):
            break
        embed.add_field(name=q[:160], value=text, inline=False)
        budget -= len(q[:160]) + len(text)
    full_answers = "\n\n".join(f"{i}. {q}\n{a}" for i, (q, a) in enumerate(answers, 1))
    embed.set_footer(text=f"User ID: {user.id}")

    ping_role_id = me["ping_role_id"] if me else None
    content = f"<@&{ping_role_id}>" if ping_role_id else None
    sub_msg = await sub_channel.send(content=content, embed=embed,
        file=discord.File(io.BytesIO(full_answers.encode('utf-8')), filename='application-answers.txt'),
        allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(id=int(ping_role_id))] if ping_role_id and ping_role_id.isdigit() else []))

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO application_submissions (guild_id, app_name, user_id, user_name, answers_json, "
            "submission_message_id, submission_channel_id, status, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (str(guild.id), app_name, str(user.id), str(user), json.dumps(answers),
             str(sub_msg.id), str(sub_channel.id), discord.utils.utcnow().isoformat()),
        )
        await db.commit()
    finally:
        await db.close()

    await sub_msg.edit(view=build_review_row(sub_msg.id))
    await dm.send(embed=plain_embed(
        None,
        f"✅ {'Dev' if app_name == 'Developer' else app_name} app submitted"))


async def finalize_decision(
    interaction: discord.Interaction, action: str, message_id: int, reason: str
) -> None:
    action_word = {"approve": "approved", "deny": "denied", "hold": "on-hold"}.get(action)
    if not action_word:
        return

    if not await is_application_staff(interaction):
        return await interaction.response.send_message("Your application manager access is required.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT * FROM application_submissions WHERE submission_message_id=? AND guild_id=?",
            (str(message_id), str(interaction.guild.id)),
        ) as cursor:
            sub = await cursor.fetchone()
        if not sub:
            await interaction.followup.send(
                embed=error_embed("Submission not found."), ephemeral=True
            )
            return

        if sub['status'] not in ('pending', 'on-hold'):
            await db.rollback()
            return await interaction.followup.send("This application has already received a final decision.", ephemeral=True)
        await db.execute(
            "UPDATE application_submissions SET status=? WHERE id=?",
            (action_word, sub["id"]),
        )
        await db.commit()
    finally:
        await db.close()

    guild = interaction.guild
    applicant_id = int(sub["user_id"])
    app_name = sub["app_name"]
    member = guild.get_member(applicant_id)

    # DM the applicant
    if member:
        try:
            title_word = action_word[0].upper() + action_word[1:]
            await member.send(
                embed=plain_embed(
                    f"Application {title_word}",
                    f"Your application for **{app_name}** has been **{action_word}**.\n\n**Reason:** {reason}",
                )
            )
        except discord.Forbidden:
            pass

    role_warning = ""
    # Side effects
    if action == "approve":
        approve_role_id = await _approve_role_for(guild.id, app_name)
        if approve_role_id and member:
            role = guild.get_role(int(approve_role_id))
            if role:
                try:
                    await member.add_roles(role, reason=f"Application approved: {reason[:200]}")
                except discord.HTTPException:
                    role_warning = " The approval role could not be assigned; check my role position and Manage Roles permission."
            else:
                role_warning = " The configured approval role no longer exists."
    elif action == "deny":
        await set_cooldown(guild.id, applicant_id, app_name)

    # Result embed
    result = plain_embed(
        f"Application {action_word[0].upper() + action_word[1:]}: {app_name}",
        f"**Applicant:** <@{applicant_id}> (`{applicant_id}`)\n"
        f"**Reviewed by:** {interaction.user.mention}\n"
        f"**Reason:** {reason}",
    )
    try:
        budget = 4000
        for q, a in json.loads(sub["answers_json"] or "[]")[:20]:
            name = str(q)[:160]
            value = (a or "No answer")[:min(500, max(1, budget - len(name)))]
            if budget < len(name) + len(value):
                break
            result.add_field(name=name, value=value, inline=False)
            budget -= len(name) + len(value)
    except Exception:
        pass
    result.set_footer(text=f"Submission #{sub['id']}")

    if action == "hold":
        # Keep the message (and its buttons) active for a later decision.
        await interaction.message.edit(embed=result, view=build_review_row(message_id))
        await interaction.followup.send("Application placed on hold. It remains available for review.", ephemeral=True)
        return

    # Preserve the review record and optionally copy the outcome to its result channel.
    dest_id = await (_approved_channel_for(guild.id, app_name) if action == "approve"
                     else _denied_channel_for(guild.id, app_name))
    dest = guild.get_channel(int(dest_id)) if dest_id else None
    if dest:
        try:
            await dest.send(embed=result)
        except discord.Forbidden:
            pass
    try:
        await interaction.message.edit(embed=result, view=None)
    except (discord.Forbidden, discord.NotFound):
        pass
    await interaction.followup.send(
        embed=success_embed(f"Application **{action_word}**. The review record has been updated.{role_warning}"), ephemeral=True
    )


async def _fetch_type(guild_id: int, app_name: str):
    db = await get_db()
    try:
        async with db.execute(
            "SELECT approve_role_id FROM application_templates WHERE guild_id=? AND app_name=?",
            (str(guild_id), app_name),
        ) as cursor:
            row = await cursor.fetchone()
        return row["approve_role_id"] if row else None
    finally:
        await db.close()


async def _approve_role_for(guild_id: int, app_name: str) -> str | None:
    return await _fetch_type(guild_id, app_name)


async def _denied_channel_for(guild_id: int, app_name: str) -> str | None:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT denied_channel_id FROM application_templates WHERE guild_id=? AND app_name=?",
            (str(guild_id), app_name),
        ) as cursor:
            row = await cursor.fetchone()
        return row["denied_channel_id"] if row else None
    finally:
        await db.close()


async def _approved_channel_for(guild_id: int, app_name: str) -> str | None:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT approved_channel_id FROM application_templates WHERE guild_id=? AND app_name=?",
            (str(guild_id), app_name),
        ) as cursor:
            row = await cursor.fetchone()
        return row["approved_channel_id"] if row else None
    finally:
        await db.close()


# ------------------------------------------------------------------
# The cog — /apply command tree (everything editable)
# ------------------------------------------------------------------

class Applications(commands.Cog):
    apply = app_commands.Group(
        name="apply",
        description="Application system (panel, types, questions, review).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await _migrate()

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(ApplyPanelView())

    # ----------------------------------------------------------- panel

    @apply.command(name="panel", description="Post the applications panel (dropdown).")
    @app_commands.describe(channel="Channel to send the panel to (defaults to here).")
    @app_commands.default_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True)
        dest = channel or interaction.channel
        types = await get_types(interaction.guild.id)
        if not types:
            await interaction.followup.send(embed=error_embed("No application types configured — add one with /apply add-type first."))
            return
        title, desc = await get_panel_text(interaction.guild.id)
        embed = discord.Embed(title=title, description=desc, color=ACCENT_COLOR)
        embed.set_footer(text="AU Schools • Applications are completed privately in DMs")
        embed.set_thumbnail(url="https://raw.githubusercontent.com/coopernew2012-coder/AU-SCHOOLS/main/assets/au-schools-logo.png")
        view = ApplyPanelView()
        # Discord rejects select menus sent with zero options — populate
        # them from the current types before posting.
        view.app_select.options = [
            discord.SelectOption(
                label=f"{t['app_name']} Application",
                value=t["app_name"],
                emoji=(t["emoji"] or "\U0001f4dd") if len(t["emoji"] or "") <= 3 else None,
            )
            for t in types[:25]
        ]
        await dest.send(embed=embed, view=view)
        await interaction.followup.send(embed=success_embed(f"Applications panel posted in {dest.mention}."))

    @apply.command(name="panel-edit", description="Edit the applications panel title and description.")
    @app_commands.default_permissions(administrator=True)
    async def panel_edit(self, interaction: discord.Interaction):
        title, desc = await get_panel_text(interaction.guild.id)
        await interaction.response.send_modal(PanelEditModal(title, desc))

    # ----------------------------------------------------------- types

    @apply.command(name="add-type", description="Add an application type (shows in the dropdown).")
    @app_commands.describe(name="Name of the application (e.g. Staff).", emoji="Optional emoji.")
    @app_commands.default_permissions(administrator=True)
    async def add_type(self, interaction: discord.Interaction, name: str, emoji: str | None = None):
        name = name.strip()[:50]
        db = await get_db()
        try:
            await db.execute(
                "INSERT OR IGNORE INTO application_templates (guild_id, app_name, emoji) VALUES (?, ?, ?)",
                (str(interaction.guild.id), name, emoji),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"Added application type **{name}**. Set its questions with `/apply set-questions` and channels with `/apply set-channels`."),
            ephemeral=True,
        )

    @apply.command(name="remove-type", description="Remove an application type and its questions.")
    @app_commands.describe(name="Application type to remove.")
    @app_commands.default_permissions(administrator=True)
    async def remove_type(self, interaction: discord.Interaction, name: str):
        db = await get_db()
        try:
            await db.execute("DELETE FROM application_templates WHERE guild_id=? AND app_name=?", (str(interaction.guild.id), name))
            await db.execute("DELETE FROM application_questions WHERE guild_id=? AND app_name=?", (str(interaction.guild.id), name))
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(embed=success_embed(f"Removed **{name}**."), ephemeral=True)

    @apply.command(name="list", description="List application types and their configuration.")
    @app_commands.default_permissions(administrator=True)
    async def list_types(self, interaction: discord.Interaction):
        types = await get_types(interaction.guild.id)
        if not types:
            await interaction.response.send_message(embed=error_embed("No application types configured."), ephemeral=True)
            return
        embed = plain_embed("Application Types")
        for t in types:
            questions = await get_questions(interaction.guild.id, t["app_name"])
            subs = f"<#{t['submission_channel_id']}>" if t["submission_channel_id"] else "❌ not set"
            appr = f"<#{t['approved_channel_id']}>" if t["approved_channel_id"] else "—"
            deny = f"<#{t['denied_channel_id']}>" if t["denied_channel_id"] else "—"
            ping = f"<@&{t['ping_role_id']}>" if t["ping_role_id"] else "—"
            role = f"<@&{t['approve_role_id']}>" if t["approve_role_id"] else "—"
            embed.add_field(
                name=f"{t['emoji'] or '📝'} {t['app_name']} — {len(questions)} questions",
                value=f"Submissions: {subs} · Approved: {appr} · Denied: {deny}\nPing: {ping} · Approve role: {role}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ----------------------------------------------------------- questions

    @apply.command(name="set-questions", description="Set the questions for an application type (one per line).")
    @app_commands.describe(app_type="Application type to edit.")
    @app_commands.default_permissions(administrator=True)
    async def set_questions(self, interaction: discord.Interaction, app_type: str):
        types = await get_types(interaction.guild.id)
        if not any(t["app_name"] == app_type for t in types):
            await interaction.response.send_message(embed=error_embed(f"Unknown type **{app_type}** — see /apply list."), ephemeral=True)
            return
        current = await get_questions(interaction.guild.id, app_type)
        await interaction.response.send_modal(QuestionsModal(app_type, "\n".join(current)))

    # ----------------------------------------------------------- channels / roles

    @apply.command(name="set-channels", description="Set where submissions and results go for an application type.")
    @app_commands.describe(
        app_type="Application type.",
        submissions="Channel new applications appear in for review.",
        approved="Optional channel approved results are posted to.",
        denied="Optional channel denied results are posted to.",
    )
    @app_commands.default_permissions(administrator=True)
    async def set_channels(
        self, interaction: discord.Interaction, app_type: str,
        submissions: discord.TextChannel,
        approved: discord.TextChannel | None = None,
        denied: discord.TextChannel | None = None,
    ):
        db = await get_db()
        try:
            await db.execute(
                "UPDATE application_templates SET submission_channel_id=? WHERE guild_id=? AND app_name=?",
                (str(submissions.id), str(interaction.guild.id), app_type),
            )
            if approved:
                await db.execute(
                    "UPDATE application_templates SET approved_channel_id=? WHERE guild_id=? AND app_name=?",
                    (str(approved.id), str(interaction.guild.id), app_type),
                )
            if denied:
                await db.execute(
                    "UPDATE application_templates SET denied_channel_id=? WHERE guild_id=? AND app_name=?",
                    (str(denied.id), str(interaction.guild.id), app_type),
                )
            await db.commit()
        finally:
            await db.close()
        parts = [f"Submissions → {submissions.mention}"]
        if approved:
            parts.append(f"Approved → {approved.mention}")
        if denied:
            parts.append(f"Denied → {denied.mention}")
        await interaction.response.send_message(
            embed=success_embed(f"**{app_type}** channels: " + " · ".join(parts)), ephemeral=True
        )

    @apply.command(name="set-ping-role", description="Role pinged when a new application arrives (empty = none).")
    @app_commands.describe(app_type="Application type.", role="Role to ping, or leave empty for none.")
    @app_commands.default_permissions(administrator=True)
    async def set_ping_role(self, interaction: discord.Interaction, app_type: str, role: discord.Role | None = None):
        db = await get_db()
        try:
            await db.execute(
                "UPDATE application_templates SET ping_role_id=? WHERE guild_id=? AND app_name=?",
                (str(role.id) if role else None, str(interaction.guild.id), app_type),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"**{app_type}** ping role: {role.mention if role else 'none'}"), ephemeral=True
        )

    @apply.command(name="set-approve-role", description="Role granted when this application is approved (empty = none).")
    @app_commands.describe(app_type="Application type.", role="Role to grant on approval, or leave empty for none.")
    @app_commands.default_permissions(administrator=True)
    async def set_approve_role(self, interaction: discord.Interaction, app_type: str, role: discord.Role | None = None):
        db = await get_db()
        try:
            await db.execute(
                "UPDATE application_templates SET approve_role_id=? WHERE guild_id=? AND app_name=?",
                (str(role.id) if role else None, str(interaction.guild.id), app_type),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"**{app_type}** approve role: {role.mention if role else 'none'}"), ephemeral=True
        )

    @apply.command(name="set-reviewer-role", description="Set the application manager role allowed to review applications (empty = Manage Server).")
    @app_commands.default_permissions(administrator=True)
    async def set_reviewer_role(self, interaction: discord.Interaction, role: discord.Role | None = None):
        await set_setting(interaction.guild.id, "apply_reviewer_role", str(role.id) if role else None)
        await interaction.response.send_message(
            embed=success_embed(f"Reviewers: {role.mention if role else 'Manage Server only'}"), ephemeral=True
        )

    # ----------------------------------------------------------- cooldowns

    @apply.command(name="set-cooldown", description="Hours a denied applicant must wait before re-applying.")
    @app_commands.describe(app_type="Application type.", hours="Cooldown hours (0 disables).")
    @app_commands.default_permissions(administrator=True)
    async def set_cooldown_cmd(self, interaction: discord.Interaction, app_type: str, hours: int):
        hours = max(0, min(hours, 8760))
        db = await get_db()
        try:
            await db.execute(
                "UPDATE application_templates SET cooldown_hours=? WHERE guild_id=? AND app_name=?",
                (hours or None, str(interaction.guild.id), app_type),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"**{app_type}** deny cooldown: {hours}h" if hours else f"**{app_type}** deny cooldown: disabled"),
            ephemeral=True,
        )

    @apply.command(name="clear-cooldown", description="Clear a user's deny cooldown early.")
    @app_commands.describe(user="User to clear.", app_type="Application type.")
    @app_commands.default_permissions(manage_guild=True)
    async def clear_cooldown_cmd(self, interaction: discord.Interaction, user: discord.User, app_type: str):
        await clear_cooldown(interaction.guild.id, user.id, app_type)
        await interaction.response.send_message(
            embed=success_embed(f"Cleared the **{app_type}** cooldown for {user.mention}."), ephemeral=True
        )


class PanelEditModal(discord.ui.Modal, title="Edit Applications Panel"):
    panel_title = discord.ui.TextInput(
        label="Panel title", style=discord.TextStyle.short, max_length=256, required=True,
    )
    panel_description = discord.ui.TextInput(
        label="Panel description", style=discord.TextStyle.paragraph, max_length=4000, required=True,
    )

    def __init__(self, title: str, description: str):
        super().__init__()
        self.panel_title.default = title[:256]
        self.panel_description.default = description[:4000]

    async def on_submit(self, interaction: discord.Interaction):
        await set_setting(interaction.guild.id, "apply_panel_title", self.panel_title.value.strip())
        await set_setting(interaction.guild.id, "apply_panel_description", self.panel_description.value.strip())
        await interaction.response.send_message(
            embed=success_embed("Panel text updated — post it again with /apply panel to see it."),
            ephemeral=True,
        )


class QuestionsModal(discord.ui.Modal, title="Set Questions (one per line)"):
    questions_input = discord.ui.TextInput(
        label="Questions — one per line",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )

    def __init__(self, app_type: str, current: str):
        super().__init__()
        self.app_type = app_type
        self.questions_input.default = current[:4000]

    async def on_submit(self, interaction: discord.Interaction):
        lines = [ln.strip() for ln in self.questions_input.value.splitlines() if ln.strip()]
        if not lines:
            await interaction.response.send_message(embed=error_embed("Need at least one question."), ephemeral=True)
            return
        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM application_questions WHERE guild_id=? AND app_name=?",
                (str(interaction.guild.id), self.app_type),
            )
            for i, q in enumerate(lines, start=1):
                await db.execute(
                    "INSERT INTO application_questions (guild_id, app_name, question, sort_order) VALUES (?, ?, ?, ?)",
                    (str(interaction.guild.id), self.app_type, q, i),
                )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"**{self.app_type}** now has {len(lines)} questions."),
            ephemeral=True,
        )


# ------------------------------------------------------------------
# Context menu for clearing cooldowns
# ------------------------------------------------------------------
@app_commands.context_menu(name="Clear App Cooldown")
@app_commands.default_permissions(manage_guild=True)
async def clear_cooldown_context(interaction: discord.Interaction, user: discord.User):
    await ensure_defaults(interaction.guild.id)
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM application_cooldowns WHERE guild_id=? AND user_id=?",
            (str(interaction.guild.id), str(user.id)),
        )
        await db.commit()
    finally:
        await db.close()
    await interaction.response.send_message(
        embed=success_embed(f"Cleared all application cooldowns for {user.mention}."), ephemeral=True
    )


async def setup(bot: commands.Bot):
    await bot.add_cog(Applications(bot))
    bot.tree.add_command(clear_cooldown_context)
    # Dynamic review buttons match any app2-<action>-<messageId> custom id
    bot.add_dynamic_items(AppReviewButton)

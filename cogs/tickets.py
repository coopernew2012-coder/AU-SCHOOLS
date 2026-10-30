"""Discord ↔ staff ticket system — port of the AU Schools Node.js tickets.

Flow: /ticket panel send → select menu → per-category question modal →
text channel under the tickets category with a control panel:
Claim / Release / Close / Transcript / Priority / Members / Management / Resources.
On close: HTML transcript DM'd to the opener, attached to the ticket log,
stats bumped. Priority buttons, inactivity auto-close, staff leaderboard.

Everything staff-facing is editable per guild (settings, edited via
/ticket config commands and /edit-rules / /edit-serverinfo in staff.py).
"""
import asyncio
from pathlib import Path
import io
import json
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import ACCENT_COLOR, DATA_DIR
from database import get_db, get_setting, set_setting, delete_setting, get_settings_many
from utils import base_embed, success_embed, error_embed

# Reuse the editable content from the staff cog (same defaults, same settings keys)
from cogs.staff import (
    DEFAULT_RULES,
    DEFAULT_SERVERINFO_TEMPLATE,
    EditRulesModal,
    get_rules,
)

PRIORITY_LABELS = {
    "normal": "🟢 Normal",
    "important": "🟡 Important",
    "urgent": "🔴 Urgent",
}

DEFAULT_QUESTIONS = {'Support': [{'label': 'What do you need help with?', 'style': 'paragraph'}], 'Player Report': [{'label': 'Who are you reporting?', 'style': 'short'}, {'label': 'What happened? Include evidence links.', 'style': 'paragraph'}], 'Bug Report': [{'label': 'What is broken?', 'style': 'paragraph'}, {'label': 'How can we reproduce it?', 'style': 'paragraph'}], 'Unban Appeal': [{'label': 'Your username or Discord ID', 'style': 'short'}, {'label': 'Why were you banned or muted?', 'style': 'paragraph'}, {'label': 'Why should we reconsider?', 'style': 'paragraph'}], 'Staff Report': [{'label': 'Which staff member are you reporting?', 'style': 'short'}, {'label': 'What happened? Include evidence links.', 'style': 'paragraph'}]}

CATEGORY_DETAILS = {'Support': ('💬', 'Questions or help with anything on the server'), 'Player Report': ('🚨', 'Report a cheater, rule breaker or toxic player'), 'Bug Report': ('🐛', 'Something broken? Tell us so we can fix it'), 'Unban Appeal': ('⚖️', 'Banned or muted? Ask for a second chance'), 'Staff Report': ('🛡️', 'Report a staff member to management')}

TICKET_PANEL_TITLE = "AU Schools Support"
TICKET_PANEL_DESCRIPTION = 'Need a hand? Pick what you need below and a private ticket opens just for you.\n\nPlease only open one ticket per problem, and include as much detail as you can. Staff will get to you as soon as possible! 💚\n\n**What can we help with?**\n💬 **Support:** Questions or help with anything on the server\n🚨 **Player Report:** Report a cheater, rule breaker or toxic player\n🐛 **Bug Report:** Something broken? Tell us so we can fix it\n⚖️ **Unban Appeal:** Banned or muted? Ask for a second chance\n🛡️ **Staff Report:** Report a staff member to management'

# Pre-redesign default; a stored description identical to this is migrated.
LEGACY_TICKET_PANEL_DESC = (
    "Need help? Open a support ticket and our staff will assist you as soon as possible.\n"
    "Select a category below to get started."
)


async def get_panel_text(guild_id: int) -> tuple[str, str]:
    """Ticket panel title/description with defaults. A stored description
    identical to the old short default is upgraded to the rich layout;
    customized text is never touched."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT embed_title, embed_desc FROM ticket_panels WHERE guild_id=? LIMIT 1",
            (str(guild_id),),
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await db.close()
    title = (row["embed_title"] if row and row["embed_title"] else None) or TICKET_PANEL_TITLE
    desc = (row["embed_desc"] if row and row["embed_desc"] else None) or None
    if desc and desc.strip() == LEGACY_TICKET_PANEL_DESC.strip():
        desc = None  # migrate to the new default
    if desc is None:
        desc = TICKET_PANEL_DESCRIPTION
    return title, desc


async def get_categories(guild_id: int) -> list[dict]:
    """Categories with parsed questions: [{name, emoji, questions:[{label, style}]}]"""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT name, emoji, questions FROM ticket_categories WHERE guild_id=?",
            (str(guild_id),),
        ) as cursor:
            rows = await cursor.fetchall()
    finally:
        await db.close()
    out = []
    for r in rows:
        try:
            qs = json.loads(r["questions"] or "[]")
        except json.JSONDecodeError:
            qs = []
        if not qs:
            qs = DEFAULT_QUESTIONS.get(r["name"], DEFAULT_QUESTIONS["Support"])
        out.append({"name": r["name"], "emoji": r["emoji"] or "🎫", "questions": qs})
    return out or [
        {"name": name, "emoji": CATEGORY_DETAILS.get(name, ("🎫", ""))[0], "questions": DEFAULT_QUESTIONS.get(name, DEFAULT_QUESTIONS["Support"])}
        for name in DEFAULT_QUESTIONS
    ]


# =====================================================================
# Transcript generation (HTML, styled like the Node version)
# =====================================================================

def _escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


async def fetch_all_messages(channel, cap: int = 1000) -> list[discord.Message]:
    all_msgs: list[discord.Message] = []
    last_id = None
    while len(all_msgs) < cap:
        options = {"limit": 100}
        if last_id:
            options["before"] = last_id
        try:
            batch = [m async for m in channel.history(limit=100, before=discord.Object(id=last_id) if last_id else None)]
        except discord.HTTPException:
            break
        if not batch:
            break
        all_msgs.extend(batch)
        last_id = batch[-1].id
        if len(batch) < 100:
            break
    return list(reversed(all_msgs))  # chronological


async def generate_transcript(channel) -> discord.File:
    messages = await fetch_all_messages(channel)
    rows = []
    for m in messages:
        ts = m.created_at.strftime("%d/%m/%Y, %H:%M:%S")
        author = _escape_html(m.author)
        content = _escape_html(m.content or "").replace("\n", "<br>")
        embeds_html = ""
        for e in m.embeds:
            title = f'<div class="embed-title">{_escape_html(e.title)}</div>' if e.title else ""
            desc = f'<div class="embed-desc">{_escape_html(e.description or "").replace(chr(10), "<br>")}</div>' if e.description else ""
            fields = "".join(
                f'<div class="embed-field"><b>{_escape_html(f.name)}</b><br>{_escape_html(f.value)}</div>'
                for f in e.fields
            )
            embeds_html += f'<div class="embed">{title}{desc}{fields}</div>'
        attachments = "".join(
            f'<div class="attachment"><a href="{a.url}" target="_blank">📎 {_escape_html(a.filename)}</a></div>'
            for a in m.attachments
        )
        rows.append(
            f'<div class="message"><div class="meta"><b>{author}</b> <span class="time">{ts}</span></div>'
            f'<div class="content">{content}</div>{embeds_html}{attachments}</div>'
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Transcript — #{_escape_html(channel.name)}</title>
<style>
body {{ background:#313338; color:#dbdee1; font-family:Arial,sans-serif; padding:20px; }}
.message {{ border-bottom:1px solid #3f4147; padding:10px 0; }}
.meta {{ color:#87CEEB; margin-bottom:4px; }}
.time {{ color:#949ba4; font-size:12px; margin-left:8px; }}
.content {{ white-space:pre-wrap; }}
.embed {{ border-left:4px solid #87CEEB; background:#2b2d31; padding:8px 12px; margin-top:6px; border-radius:4px; }}
.embed-title {{ font-weight:bold; margin-bottom:4px; }}
.embed-field {{ margin-top:6px; }}
.attachment {{ margin-top:6px; }}
.attachment a {{ color:#00a8fc; }}
h1 {{ color:#87CEEB; }}
</style></head>
<body>
<h1>Transcript — #{_escape_html(channel.name)}</h1>
<p>Generated: {datetime.now().strftime('%d/%m/%Y, %H:%M:%S')}</p>
{chr(10).join(rows)}
</body></html>"""

    return discord.File(fp=io.BytesIO(html.encode("utf-8")), filename=f"transcript-{channel.name}.html")


# =====================================================================
# Groq AI summary
# =====================================================================





# =====================================================================
# UI: question modal, panel, control buttons, staff tools
# =====================================================================

class TicketQuestionsModal(discord.ui.Modal):
    """Dynamic modal built from a category's configured questions (max 5)."""

    def __init__(self, cog: "TicketSystem", category: str, questions: list[dict]):
        # Modal title must be <= 45 chars
        super().__init__(title=(f"Open a Ticket — {category}")[:45], timeout=600)
        self.cog = cog
        self.category = category
        self.questions = questions[:5]
        self.inputs: list[discord.ui.TextInput] = []
        for i, q in enumerate(self.questions):
            component = discord.ui.TextInput(
                label=q.get("label", f"Question {i + 1}")[:45],
                style=discord.TextStyle.paragraph if q.get("style") == "paragraph" else discord.TextStyle.short,
                max_length=1000,
                required=True,
            )
            self.add_item(component)
            self.inputs.append(component)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        answers = [
            {"label": q.get("label", f"Question {i + 1}"), "value": comp.value}
            for i, (q, comp) in enumerate(zip(self.questions, self.inputs))
        ]
        await self.cog.create_ticket_channel(interaction, self.category, answers)


class TicketPanelEditModal(discord.ui.Modal, title="Edit Ticket Panel"):
    title_input = discord.ui.TextInput(
        label="Panel title", style=discord.TextStyle.short, max_length=256, required=False
    )
    desc_input = discord.ui.TextInput(
        label="Panel description", style=discord.TextStyle.paragraph, max_length=4000, required=False
    )

    def __init__(self, current_title: str, current_desc: str):
        super().__init__(timeout=1200)
        self.title_input.default = current_title[:256]
        self.desc_input.default = current_desc[:4000]

    async def on_submit(self, interaction: discord.Interaction):
        new_title = self.title_input.value.strip() or "Open a Ticket"
        new_desc = self.desc_input.value.strip()
        db = await get_db()
        try:
            await db.execute(
                "UPDATE ticket_panels SET embed_title=?, embed_desc=? WHERE guild_id=?",
                (new_title, new_desc, str(interaction.guild.id)),
            )
            async with db.execute(
                "SELECT channel_id, message_id FROM ticket_panels WHERE guild_id=? LIMIT 1",
                (str(interaction.guild.id),),
            ) as cursor:
                panel = await cursor.fetchone()
            await db.commit()
        finally:
            await db.close()

        live = ""
        if panel:
            channel = interaction.guild.get_channel(int(panel["channel_id"]))
            if channel:
                try:
                    msg = await channel.fetch_message(int(panel["message_id"]))
                    embed = base_embed(title=new_title, description=new_desc)
                    if msg.embeds and msg.embeds[0].image:
                        embed.set_image(url=msg.embeds[0].image.url)
                    if msg.embeds and msg.embeds[0].thumbnail:
                        embed.set_thumbnail(url=msg.embeds[0].thumbnail.url)
                    await msg.edit(embed=embed)
                    live = " The posted panel was updated too."
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    live = " (Couldn't reach the posted panel — re-send it to apply.)"

        await interaction.response.send_message(
            embed=success_embed(f"Panel updated: **{new_title}**.{live}"), ephemeral=True
        )


from cogs.thread_tickets import TicketPanelView, panel_embed


class TicketControlView(discord.ui.View):
    """The two rows of staff buttons attached to the first message in every ticket."""

    def __init__(self, cog: "TicketSystem | None" = None):
        super().__init__(timeout=None)
        self.cog = cog

    def _cog(self, interaction: discord.Interaction) -> "TicketSystem":
        return self.cog or interaction.client.get_cog("TicketSystem")

    @discord.ui.button(label="Claim / Release", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="pffa_claim_ticket", row=0)
    async def claim(self, interaction, button):
        cog = self._cog(interaction)
        if not await cog.is_ticket_staff(interaction):
            return await interaction.response.send_message(embed=error_embed("Only authorised staff can claim tickets."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("SELECT claimed_by FROM ticket_channels WHERE channel_id=? AND status='open'", (str(interaction.channel.id),)) as cursor:
                rec = await cursor.fetchone()
            if not rec:
                await db.rollback()
                return await interaction.followup.send("This ticket is no longer open.", ephemeral=True)
            if rec['claimed_by'] and rec['claimed_by'] != str(interaction.user.id):
                await db.rollback()
                return await interaction.followup.send(f"This ticket is already assigned to <@{rec['claimed_by']}>.", ephemeral=True)
            released = bool(rec['claimed_by'])
            await db.execute("UPDATE ticket_channels SET claimed_by=? WHERE channel_id=?", (None if released else str(interaction.user.id), str(interaction.channel.id)))
            await db.commit()
        finally:
            await db.close()
        text = f"{interaction.user.mention} {'released this ticket back to the team' if released else 'claimed this ticket and will assist you'}."
        await interaction.channel.send(embed=base_embed(title="Ticket assignment", description=text))
        await interaction.followup.send("Ticket released." if released else "Ticket assigned to you.", ephemeral=True)
        await cog.ticket_log(interaction.guild, text + f" — {interaction.channel.mention}")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="pffa_close_ticket", row=0)
    async def close(self, interaction, button):
        cog = self._cog(interaction)
        rec = await cog.get_ticket(interaction.channel.id)
        if not rec or rec['status'] != 'open':
            return await interaction.response.send_message("This ticket is no longer open.", ephemeral=True)
        if rec['creator_id'] != str(interaction.user.id) and not await cog.is_ticket_staff(interaction):
            return await interaction.response.send_message("Only the opener or authorised staff can close this ticket.", ephemeral=True)
        await interaction.response.send_modal(CloseTicketModal(cog))

    @discord.ui.button(label="Save Transcript", style=discord.ButtonStyle.secondary, emoji="📄", custom_id="au_ticket_transcript", row=0)
    async def transcript(self, interaction, button):
        cog = self._cog(interaction)
        rec = await cog.get_ticket(interaction.channel.id)
        if not rec or (rec['creator_id'] != str(interaction.user.id) and not await cog.is_ticket_staff(interaction)):
            return await interaction.response.send_message("You cannot export this ticket.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(file=await generate_transcript(interaction.channel), ephemeral=True)

    @discord.ui.button(label="Set Priority", style=discord.ButtonStyle.secondary, emoji="🚩", custom_id="au_ticket_priority", row=1)
    async def priority_button(self, interaction, button):
        cog = self._cog(interaction)
        if not await cog.is_ticket_staff(interaction):
            return await interaction.response.send_message("Only authorised staff can change priority.", ephemeral=True)
        await interaction.response.send_message("Choose a priority:", view=PriorityView(cog), ephemeral=True)

    @discord.ui.button(label="Add Member", row=1, style=discord.ButtonStyle.secondary, emoji="➕", custom_id="pffa_add_user_btn")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = self._cog(interaction)
        if not await cog.is_ticket_staff(interaction):
            await interaction.response.send_message(embed=error_embed("❌ Staff only."), ephemeral=True)
            return
        await interaction.response.send_message(
            content="Select a user to add to this ticket:",
            view=UserPickView(cog, "add"),
            ephemeral=True,
        )

    @discord.ui.button(label="Remove Member", row=1, style=discord.ButtonStyle.secondary, emoji="➖", custom_id="pffa_remove_user_btn")
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = self._cog(interaction)
        if not await cog.is_ticket_staff(interaction):
            await interaction.response.send_message(embed=error_embed("❌ Staff only."), ephemeral=True)
            return
        await interaction.response.send_message(
            content="Select a user to remove from this ticket:",
            view=UserPickView(cog, "remove"),
            ephemeral=True,
        )

    @discord.ui.button(label="Request Management", row=1, style=discord.ButtonStyle.danger, emoji="🚨", custom_id="pffa_escalate_ticket")
    async def escalate(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = self._cog(interaction)
        if not await cog.is_ticket_staff(interaction):
            await interaction.response.send_message(embed=error_embed("❌ Staff only."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        management = await get_setting(interaction.guild.id, "ticket_admin_role")
        if management and management.isdigit():
            role = interaction.guild.get_role(int(management))
            if role:
                await channel.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True)
                await channel.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=[role]))
        await cog.set_priority(channel.id, "urgent")
        if not channel.name.startswith("🚨🚨"):
            try:
                await channel.edit(name=f"🚨🚨{channel.name}"[:100])
            except discord.HTTPException:
                pass
        embed = base_embed(title="🚨 Ticket Escalated")
        embed.description = (
            f"This ticket has been escalated by {interaction.user.mention} and requires priority attention."
        )
        embed.color = discord.Color.red()
        sent = await channel.send(embed=embed)
        try:
            await sent.pin()
        except discord.HTTPException:
            pass
        await interaction.followup.send(embed=success_embed("Management requested; priority set to urgent."), ephemeral=True)
        await cog.ticket_log(
            interaction.guild, f"🚨 **Ticket escalated** — {channel.mention} by {interaction.user.mention}"
        )

    @discord.ui.button(label="Staff Resources", row=1, style=discord.ButtonStyle.secondary, emoji="🛠️", custom_id="pffa_staff_resources")
    async def tools(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = self._cog(interaction)
        if not await cog.is_ticket_staff(interaction):
            await interaction.response.send_message(embed=error_embed("❌ Staff only."), ephemeral=True)
            return
        await interaction.response.send_message(
            content="Staff Tools:", view=StaffToolsView(cog), ephemeral=True
        )


class CloseTicketModal(discord.ui.Modal, title="Close ticket"):
    reason = discord.ui.TextInput(label="Resolution / reason for closing", style=discord.TextStyle.paragraph, max_length=1000, required=True)

    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_submit(self, interaction):
        rec = await self.cog.get_ticket(interaction.channel.id)
        if not rec or rec['status'] != 'open':
            return await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
        if rec['creator_id'] != str(interaction.user.id) and not await self.cog.is_ticket_staff(interaction):
            return await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        lock = self.cog._close_locks.setdefault(interaction.channel.id, asyncio.Lock())
        if lock.locked():
            return await interaction.followup.send("This ticket is already being closed.", ephemeral=True)
        async with lock:
            try:
                await interaction.channel.send(embed=base_embed(title="Ticket resolved", description=f"**Closed by:** {interaction.user.mention}\n**Reason:** {self.reason.value}"))
                await self.cog.close_ticket_with_transcript(interaction.channel, interaction.user, rec['category'])
                await interaction.channel.delete(reason=f"Ticket resolved by {interaction.user}: {self.reason.value[:200]}")
            except (discord.HTTPException, RuntimeError):
                db = await get_db()
                try:
                    await db.execute("UPDATE ticket_channels SET status='open', closed_at=NULL WHERE channel_id=?", (str(interaction.channel.id),))
                    await db.commit()
                finally:
                    await db.close()
                await interaction.followup.send("The ticket could not be fully closed. Staff can retry; the channel has been kept where possible.", ephemeral=True)


class UserPickView(discord.ui.View):
    """Ephemeral user picker for Add/Remove User (built manually for discord.py 2.x compat)."""

    def __init__(self, cog: "TicketSystem", mode: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.mode = mode

        select = discord.ui.UserSelect(placeholder="Select a user", min_values=1, max_values=1)
        select.callback = self.pick_callback
        self.add_item(select)

    async def pick_callback(self, interaction: discord.Interaction):
        if not await self.cog.is_ticket_staff(interaction):
            return await interaction.response.send_message("Your staff access is required.", ephemeral=True)
        await interaction.response.defer()
        target_id = int(interaction.data["values"][0])
        target = interaction.guild.get_member(target_id) or await interaction.guild.fetch_member(target_id)
        if self.mode == "add":
            overwrite = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True
            )
            await interaction.channel.set_permissions(target, overwrite=overwrite)
            await interaction.edit_original_response(
                content=f"✅ Added {target.mention} to the ticket.", view=None
            )
            await interaction.channel.send(
                embed=base_embed(description=f"✅ Added {target.mention} to the ticket.")
            )
            await self.cog.ticket_log(
                interaction.guild,
                f"➕ **User added** — {target.mention} to {interaction.channel.mention} by {interaction.user.mention}",
            )
        else:
            rec = await self.cog.get_ticket(interaction.channel.id)
            if rec and rec["creator_id"] == str(target.id):
                await interaction.edit_original_response(
                    content="❌ You can't remove the ticket creator.", view=None
                )
                return
            if getattr(target, "bot", False):
                await interaction.edit_original_response(content="❌ You can't remove a bot.", view=None)
                return
            await interaction.channel.set_permissions(target, view_channel=False, send_messages=False)
            await interaction.edit_original_response(
                content=f"✅ Removed {target.mention} from the ticket.", view=None
            )
            await interaction.channel.send(
                embed=base_embed(description=f"➖ Removed {target.mention} from the ticket.")
            )
            await self.cog.ticket_log(
                interaction.guild,
                f"➖ **User removed** — {target.mention} from {interaction.channel.mention} by {interaction.user.mention}",
            )


class StaffToolsView(discord.ui.View):
    """🛠️ Tools menu — Server Ad / Rules / Ping Higher-Ups (all editable)."""

    def __init__(self, cog: "TicketSystem"):
        super().__init__(timeout=180)
        self.cog = cog

    async def interaction_check(self, interaction):
        if not await self.cog.is_ticket_staff(interaction):
            await interaction.response.send_message("Authorised ticket staff only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Server Info", style=discord.ButtonStyle.secondary, emoji="📢", custom_id="pffa_resource_ad")
    async def server_ad(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Editable via /edit-serverinfo (staff cog) — placeholders auto-filled
        settings = await get_settings_many(interaction.guild.id)
        ip_addr = settings.get("server_ip") or "COMING SOON"
        invite = settings.get("discord_invite") or "Invite not configured"
        template = settings.get("community_info_text") or DEFAULT_SERVERINFO_TEMPLATE
        body = template.replace("{ip}", ip_addr).replace("{invite}", invite)
        ad = f"# AU Schools\n\n{body}"
        await interaction.response.send_message(ad[:2000], ephemeral=True)

    @discord.ui.button(label="Rules", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="pffa_resource_rules")
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Editable via /edit-rules (staff cog)
        sections = await get_rules(interaction.guild.id)
        name = "AU Schools"
        titles = {
            "discord": f"📜 {name} — Discord Rules",
            "general": f"🌐 {name} — General Rules",
            "smp": f"⚔️ {name} — Community Conduct",
        }
        embeds = []
        for key in ("discord", "general", "smp"):
            e = base_embed(title=titles[key])
            e.description = sections[key]
            embeds.append(e)
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @discord.ui.button(label="Ping Higher Ups", style=discord.ButtonStyle.danger, emoji="📣", custom_id="pffa_resource_ping_higher")
    async def ping_higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            content="Ping higher-ups to this ticket?",
            view=PingConfirmView(self.cog),
            ephemeral=True,
        )


class PingConfirmView(discord.ui.View):
    def __init__(self, cog: "TicketSystem"):
        super().__init__(timeout=120)
        self.cog = cog

    async def interaction_check(self, interaction):
        if not await self.cog.is_ticket_staff(interaction):
            await interaction.response.send_message("Authorised ticket staff only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, custom_id="pffa_resource_ping_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_settings_many(interaction.guild.id)
        pings = []
        rec = await self.cog.get_ticket(interaction.channel.id)
        keys = ("ticket_admin_role",) if rec and rec["category"] == "Staff Report" else ("ticket_admin_role", "ticket_ping_roles")
        for key in keys:
            val = settings.get(key)
            if val:
                pings.extend(f"<@&{rid.strip()}>" for rid in val.split(",") if rid.strip().isdigit())
        pings = list(dict.fromkeys(pings))[:6]
        content = (
            f"{' '.join(pings)}\n\nA higher staff member has been requested for this ticket."
            if pings
            else "⚠️ No higher-up role configured — ask an admin to run `/ticket config set-admin-role`."
        )
        await interaction.response.defer()
        await interaction.channel.send(content=content)
        await interaction.edit_original_response(content="Management notified." if pings else "No management role configured yet.", view=None)
        await self.cog.ticket_log(
            interaction.guild, f"📣 **Higher-ups pinged** in {interaction.channel.mention} by {interaction.user.mention}"
        )


class PriorityView(discord.ui.View):
    def __init__(self, cog: "TicketSystem"):
        super().__init__(timeout=180)
        self.cog = cog

    async def _set(self, interaction: discord.Interaction, level: str):
        if not await self.cog.is_ticket_staff(interaction):
            await interaction.response.send_message(embed=error_embed("❌ Staff only."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.cog.set_priority(interaction.channel.id, level)
        channel = interaction.channel
        prefix = {"normal": "", "important": "🟡", "urgent": "🔴"}[level]
        base_name = channel.name.lstrip("🔴🟡🟢").strip()
        try:
            await channel.edit(name=f"{prefix}{base_name}"[:100])
        except discord.HTTPException:
            pass
        await interaction.followup.send(
            embed=success_embed(f"Priority set to **{PRIORITY_LABELS[level]}**"), ephemeral=True
        )

    @discord.ui.button(label="🟢 Normal", style=discord.ButtonStyle.success, custom_id="pffa_priority_normal")
    async def normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set(interaction, "normal")

    @discord.ui.button(label="🟡 Important", style=discord.ButtonStyle.primary, custom_id="pffa_priority_important")
    async def important(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set(interaction, "important")

    @discord.ui.button(label="🔴 Urgent", style=discord.ButtonStyle.danger, custom_id="pffa_priority_urgent")
    async def urgent(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set(interaction, "urgent")


# =====================================================================
# The cog
# =====================================================================

class TicketSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._close_locks = {}
        self._creation_locks = {}

    # ------------------------------------------------------------- helpers

    async def is_ticket_staff(self, interaction: discord.Interaction) -> bool:
        """Manage Guild, bot-admin, or a configured ticket staff role."""
        if isinstance(interaction.user, discord.Member):
            if interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator:
                return True
            settings = await get_settings_many(interaction.guild.id)
            rec = await self.get_ticket(interaction.channel.id) if interaction.channel else None
            key = "ticket_admin_role" if rec and rec["category"] == "Staff Report" else "ticket_staff_role"
            staff = settings.get(key) or ""
            ids = {r.strip() for r in staff.split(",") if r.strip()}
            if ids & {str(r.id) for r in interaction.user.roles}:
                return True
        return False

    async def ticket_log(self, guild: discord.Guild, description: str):
        channel_id = await get_setting(guild.id, "ticket_log_channel")
        if not channel_id:
            channel_id = await get_setting(guild.id, "mod_log_channel")
        channel = guild.get_channel(int(channel_id)) if channel_id and channel_id.isdigit() else None
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=base_embed(description=description))
            except discord.Forbidden:
                pass

    async def get_ticket(self, channel_id: int):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM ticket_channels WHERE channel_id=?", (str(channel_id),)
            ) as cursor:
                return await cursor.fetchone()
        finally:
            await db.close()

    async def set_claimed(self, channel_id: int, user_id: int):
        db = await get_db()
        try:
            await db.execute(
                "UPDATE ticket_channels SET claimed_by=? WHERE channel_id=?",
                (str(user_id), str(channel_id)),
            )
            await db.commit()
        finally:
            await db.close()

    async def set_priority(self, channel_id: int, level: str):
        db = await get_db()
        try:
            await db.execute(
                "UPDATE ticket_channels SET priority=? WHERE channel_id=?",
                (level, str(channel_id)),
            )
            await db.commit()
        finally:
            await db.close()

    async def touch_activity(self, channel_id: int):
        db = await get_db()
        try:
            await db.execute(
                "UPDATE ticket_channels SET last_activity=?, warned=0 WHERE channel_id=?",
                (time.time(), str(channel_id)),
            )
            await db.commit()
        finally:
            await db.close()

    async def bump_stat(self, guild_id: int, user_id: int, kind: str, category: str):
        """kind is 'opened' or 'closed' — feeds /ticket leaderboard."""
        column = "opened" if kind == "opened" else "closed"
        db = await get_db()
        try:
            await db.execute(
                f"INSERT INTO ticket_stats (guild_id, user_id, category, {column}) VALUES (?, ?, ?, 1) "
                f"ON CONFLICT (guild_id, user_id, category) DO UPDATE SET {column} = {column} + 1",
                (str(guild_id), str(user_id), category),
            )
            await db.commit()
        finally:
            await db.close()

    # ------------------------------------------------------- create / close

    async def create_ticket_channel(self, interaction, category, answers):
        key = (interaction.guild.id, interaction.user.id)
        lock = self._creation_locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            return await interaction.followup.send("Your ticket is already being created.", ephemeral=True)
        async with lock:
            await self._create_ticket_channel(interaction, category, answers)

    async def _create_ticket_channel(
        self, interaction: discord.Interaction, category: str, answers: list[dict]
    ):
        guild = interaction.guild
        settings = await get_settings_many(guild.id)

        # Destination: per-guild category (Discord "category" = channel group)
        cat_id = settings.get("ticket_category")
        parent = guild.get_channel(int(cat_id)) if cat_id and cat_id.isdigit() else None
        if parent is None:
            parent = discord.utils.get(guild.categories, name="Tickets")
        if parent is None:
            try:
                parent = await guild.create_category("Tickets")
            except discord.Forbidden:
                await interaction.followup.send(
                    embed=error_embed(
                        "I couldn't create the **Tickets** category — give me **Manage Channels**, "
                        "or run `/ticket config set-category` and pick one."
                    ),
                    ephemeral=True,
                )
                return

        channel_name = f"ticket-{interaction.user.name}".lower()[:90]
        existing = discord.utils.get(guild.text_channels, name=channel_name)
        if existing:
            await interaction.followup.send(
                embed=error_embed(f"You already have an open ticket: {existing.mention}"), ephemeral=True
            )
            return

        staff_ids = []
        for key in (("ticket_admin_role",) if category == "Staff Report" else ("ticket_staff_role",)):
            val = settings.get(key)
            if val:
                staff_ids.extend(r.strip() for r in val.split(",") if r.strip().isdigit())
        ping_line = " ".join(f"<@&{rid}>" for rid in staff_ids[:3])

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_channels=True
            ),
        }
        for rid in staff_ids:
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=parent,
                topic=category,
                overwrites=overwrites,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack **Manage Channels** permission to create the ticket."),
                ephemeral=True,
            )
            return

        welcome_text = settings.get("ticket_welcome_text") or (
            "Welcome {user}, thank you for reaching out to our support team!\n"
            "Please describe your issue and we will get back to you as soon as possible."
        )
        welcome_text = welcome_text.replace("{user}", interaction.user.mention)

        embed = base_embed(title="AU Schools | Support Ticket")
        embed.description = welcome_text + "\n\n**Keep all details in this ticket.** Staff can claim the request, manage participants and save a transcript. Use **Close Ticket** when your issue is resolved."
        embed.add_field(name="Category", value=category, inline=True)
        embed.add_field(name="Status", value="Open • Awaiting staff", inline=True)
        for i, a in enumerate(answers, 1):
            embed.add_field(name=f"{i}. {a['label']}", value=(a["value"] or "*​*")[:1024], inline=False)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO ticket_channels (channel_id, guild_id, creator_id, category, status, opened_at, last_activity) "
                "VALUES (?, ?, ?, ?, 'open', ?, ?)",
                (str(channel.id), str(guild.id), str(interaction.user.id), category, time.time(), time.time()),
            )
            await db.commit()
        finally:
            await db.close()

        await self.bump_stat(guild.id, interaction.user.id, "opened", category)
        await self.ticket_log(
            guild, f"🎫 **Ticket opened** by {interaction.user.mention} — {category}\nChannel: {channel.mention}"
        )

        await channel.send(
            content=f"{interaction.user.mention} {ping_line}".strip(),
            embed=embed,
            view=TicketControlView(self),
        )
        await interaction.followup.send(
            embed=success_embed(f"Ticket opened: {channel.mention}"), ephemeral=True
        )

    async def close_ticket_with_transcript(self, channel, closed_by_user, category):
        rec = await self.get_ticket(channel.id)
        attachment = None
        try:
            attachment = await generate_transcript(channel)
        except Exception as error:
            raise RuntimeError("Transcript could not be generated; ticket kept open.") from error

        archive_dir = Path(DATA_DIR) / "transcripts"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / f"ticket-{channel.id}.html").write_bytes(attachment.fp.getvalue())

        creator_id = rec["creator_id"] if rec else None
        dm_ok = False
        if creator_id and attachment:
            try:
                creator = await self.bot.fetch_user(int(creator_id))
                await creator.send(
                    content=(
                        "📄 Your ticket has been closed.\n\n"
                        "Thank you for contacting the staff team.\n\n"
                        "A transcript of your ticket has been attached for your records."
                    ),
                    file=discord.File(io.BytesIO(attachment.fp.getvalue()), filename=attachment.filename),
                )
                dm_ok = True
            except (discord.Forbidden, discord.HTTPException):
                dm_ok = False

        # mark closed + bump stats before the channel disappears
        if rec:
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE ticket_channels SET status='closed', closed_at=? WHERE channel_id=?",
                    (time.time(), str(channel.id)),
                )
                await db.commit()
            finally:
                await db.close()
            await self.bump_stat(channel.guild.id, closed_by_user.id, "closed", category)

        channel_id = await get_setting(channel.guild.id, "ticket_log_channel") or await get_setting(
            channel.guild.id, "mod_log_channel"
        )
        log_channel = (
            channel.guild.get_channel(int(channel_id))
            if channel_id and channel_id.isdigit()
            else None
        )
        if category == "Staff Report":
            log_channel = None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            try:
                if not dm_ok and creator_id:
                    warn = base_embed(title="⚠️ Transcript Delivery Failed")
                    warn.description = f"User:\n<@{creator_id}>\n\nReason:\nUser has DMs disabled.\n\nTranscript:\nAttached below."
                    warn.color = discord.Color.red()
                    await log_channel.send(embed=warn, file=attachment)
                else:
                    await log_channel.send(
                        embed=base_embed(
                            description=(
                                f"🔒 **Ticket closed** by {closed_by_user.mention} — {category}\n"
                                f"Channel was: #{channel.name}"
                            )
                        ),
                        file=discord.File(io.BytesIO(attachment.fp.getvalue()), filename=attachment.filename),
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ------------------------------------------------------------- panel/config commands

    ticket_group = app_commands.Group(
        name="ticket",
        description="Manage the ticket system.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @ticket_group.command(name="panel", description="Send the AU Schools ticket dropdown to a channel.")
    @app_commands.describe(action="Send the ticket panel.", channel="Channel to post the ticket panel in.")
    @app_commands.choices(action=[app_commands.Choice(name="send", value="send")])
    @app_commands.default_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction, action: str, channel: discord.TextChannel):
        if action != "send":
            return await interaction.response.send_message("Use action:send to post the ticket panel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = panel_embed()
        try:
            msg = await channel.send(embed=embed, view=TicketPanelView())
        except discord.Forbidden:
            return await interaction.followup.send("I need View Channel, Send Messages and Embed Links there.", ephemeral=True)
        db = await get_db()
        try:
            await db.execute("INSERT INTO ticket_panels (guild_id,channel_id,message_id,embed_title,embed_desc) VALUES (?,?,?,?,?)",
                (str(interaction.guild.id),str(channel.id),str(msg.id),"AU Schools",embed.description))
            await db.commit()
        finally:
            await db.close()
        await interaction.followup.send(f"Ticket panel sent: {msg.jump_url}", ephemeral=True)

    config_group = app_commands.Group(
        name="config",
        description="Configure the ticket system.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
        parent=ticket_group,
    )

    @config_group.command(name="set-category", description="Set the Discord category (channel group) new tickets are created under.")
    @app_commands.describe(category="Category for new ticket channels.")
    @app_commands.default_permissions(administrator=True)
    async def set_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await set_setting(interaction.guild.id, "ticket_category", str(category.id))
        await interaction.response.send_message(
            embed=success_embed(f"New tickets will be created under **{category.name}**."), ephemeral=True
        )

    @config_group.command(name="set-staff-role", description="Set the staff role added to tickets and pinged on open.")
    @app_commands.describe(role="Staff role for tickets.")
    @app_commands.default_permissions(administrator=True)
    async def set_staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await set_setting(interaction.guild.id, "ticket_staff_role", str(role.id))
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} can now see, claim and manage tickets."), ephemeral=True
        )

    @config_group.command(name="set-admin-role", description="The role pinged by the Tools → Ping Higher Ups button.")
    @app_commands.describe(role="Higher-up role pinged on request.")
    @app_commands.default_permissions(administrator=True)
    async def set_admin_role(self, interaction: discord.Interaction, role: discord.Role):
        await set_setting(interaction.guild.id, "ticket_admin_role", str(role.id))
        await interaction.response.send_message(
            embed=success_embed(f"{role.mention} will be pinged by **Ping Higher Ups**."), ephemeral=True
        )

    @config_group.command(name="set-log-channel", description="Set the ticket log channel (opens/closes/transcripts).")
    @app_commands.describe(channel="Channel for ticket logs and transcripts.")
    @app_commands.default_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await set_setting(interaction.guild.id, "ticket_log_channel", str(channel.id))
        await interaction.response.send_message(
            embed=success_embed(f"Ticket logs will go to {channel.mention}."), ephemeral=True
        )

    @config_group.command(name="set-welcome-text", description="Edit the first message posted inside new tickets.")
    @app_commands.describe(text="Use {user} where the opener's mention should go.")
    @app_commands.default_permissions(administrator=True)
    async def set_welcome_text(self, interaction: discord.Interaction, text: str):
        await set_setting(interaction.guild.id, "ticket_welcome_text", text)
        await interaction.response.send_message(
            embed=success_embed("Ticket welcome text updated."), ephemeral=True
        )

    @config_group.command(name="set-autoclose", description="Auto-close tickets after N minutes of inactivity (0 = off).")
    @app_commands.describe(minutes="Minutes of inactivity before auto-close (0 disables).")
    @app_commands.default_permissions(administrator=True)
    async def set_autoclose(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 0, 1440]):
        await set_setting(interaction.guild.id, "ticket_autoclose_minutes", str(minutes))
        await interaction.response.send_message(
            embed=success_embed(
                f"✅ Tickets will auto-close after **{minutes} minutes** of inactivity."
                if minutes > 0 else "✅ Ticket auto-close disabled."
            ),
            ephemeral=True,
        )

    @config_group.command(name="add-category", description="Add a ticket category (dropdown option).")
    @app_commands.describe(
        name="Category name (exactly as it should appear).",
        emoji="Optional emoji.",
    )
    @app_commands.default_permissions(administrator=True)
    async def add_category(self, interaction: discord.Interaction, name: str, emoji: str | None = None):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT 1 FROM ticket_categories WHERE guild_id=? AND name=?",
                (str(interaction.guild.id), name),
            ) as cursor:
                exists = await cursor.fetchone()
            if exists:
                await interaction.response.send_message(
                    embed=error_embed(f"Category **{name}** already exists — remove it first."), ephemeral=True
                )
                return
            await db.execute(
                "INSERT INTO ticket_categories (guild_id, name, emoji, label) VALUES (?, ?, ?, ?)",
                (str(interaction.guild.id), name, emoji, f"{emoji or ''} {name}".strip()),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(
                f"Added **{name}** with the default questions for it. "
                f"Edit them with `/ticket config set-questions {name}`."
            ),
            ephemeral=True,
        )

    @config_group.command(name="remove-category", description="Remove a ticket category.")
    @app_commands.describe(name="Category name to remove.")
    @app_commands.default_permissions(administrator=True)
    async def remove_category(self, interaction: discord.Interaction, name: str):
        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM ticket_categories WHERE guild_id=? AND name=?",
                (str(interaction.guild.id), name),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"Removed **{name}** (restored defaults still exist if it was a default)."),
            ephemeral=True,
        )

    @config_group.command(name="set-questions", description="Edit the questions a category asks when opening a ticket (max 5).")
    @app_commands.describe(
        category_name="Category to edit.",
        questions="One question per line. End a line with |p to make it a paragraph box.",
    )
    @app_commands.default_permissions(administrator=True)
    async def set_questions(self, interaction: discord.Interaction, category_name: str, questions: str):
        lines = [ln.strip() for ln in questions.splitlines() if ln.strip()][:5]
        if not lines:
            await interaction.response.send_message(
                embed=error_embed("Provide at least one question."), ephemeral=True
            )
            return
        parsed = []
        for ln in lines:
            if ln.endswith("|p"):
                parsed.append({"label": ln[:-2].strip(), "style": "paragraph"})
            else:
                parsed.append({"label": ln, "style": "short"})

        db = await get_db()
        try:
            async with db.execute(
                "SELECT 1 FROM ticket_categories WHERE guild_id=? AND name=?",
                (str(interaction.guild.id), category_name),
            ) as cursor:
                exists = await cursor.fetchone()
            if exists:
                await db.execute(
                    "UPDATE ticket_categories SET questions=? WHERE guild_id=? AND name=?",
                    (json.dumps(parsed), str(interaction.guild.id), category_name),
                )
            else:
                await db.execute(
                    "INSERT INTO ticket_categories (guild_id, name, emoji, label, questions) VALUES (?, ?, ?, ?, ?)",
                    (str(interaction.guild.id), category_name, "🎫", category_name, json.dumps(parsed)),
                )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"Questions for **{category_name}** updated ({len(parsed)} questions)."),
            ephemeral=True,
        )

    @config_group.command(name="list-categories", description="List ticket categories and their questions.")
    @app_commands.default_permissions(administrator=True)
    async def list_categories(self, interaction: discord.Interaction):
        cats = await get_categories(interaction.guild.id)
        embed = base_embed(title=f"🎫 Ticket Categories ({len(cats)})")
        for c in cats[:25]:
            qs = "\n".join(f"• {q['label']}" for q in c["questions"]) or "—"
            embed.add_field(name=f"{c['emoji']} {c['name']}", value=qs[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- stats commands

    @ticket_group.command(name="stats", description="Overall ticket statistics (count + average close time).")
    @app_commands.default_permissions(manage_guild=True)
    async def stats(self, interaction: discord.Interaction):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed, "
                "AVG(CASE WHEN status='closed' AND closed_at IS NOT NULL THEN closed_at - opened_at END) AS avg_s "
                "FROM ticket_channels WHERE guild_id=?",
                (str(interaction.guild.id),),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await db.close()

        total = row["total"] or 0
        closed = row["closed"] or 0
        avg_s = row["avg_s"]
        avg_label = "N/A"
        if avg_s:
            mins = avg_s / 60
            avg_label = f"{mins / 60:.1f} hours" if mins >= 60 else f"{round(mins)} minutes"

        embed = base_embed(title="🎫 Ticket Statistics")
        embed.add_field(name="Total Tickets", value=str(total), inline=True)
        embed.add_field(name="Closed Tickets", value=str(closed), inline=True)
        embed.add_field(name="Open Tickets", value=str(total - closed), inline=True)
        embed.add_field(name="Average Close Time", value=avg_label, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket_group.command(name="leaderboard", description="Staff leaderboard by tickets closed.")
    @app_commands.default_permissions(manage_guild=True)
    async def leaderboard(self, interaction: discord.Interaction):
        # Closed counts split by user/category; we need per-user totals,
        # and closes by staff are recorded with the OPENER's id in ticket_stats…
        # So track staff closes via the claim column instead: claimed tickets closed.
        db = await get_db()
        try:
            async with db.execute(
                "SELECT claimed_by AS uid, COUNT(*) AS c FROM ticket_channels "
                "WHERE guild_id=? AND status='closed' AND claimed_by IS NOT NULL "
                "GROUP BY claimed_by ORDER BY c DESC LIMIT 10",
                (str(interaction.guild.id),),
            ) as cursor:
                claim_rows = await cursor.fetchall()
            async with db.execute(
                "SELECT user_id, SUM(closed) AS c FROM ticket_stats WHERE guild_id=? "
                "GROUP BY user_id ORDER BY c DESC LIMIT 10",
                (str(interaction.guild.id),),
            ) as cursor:
                stats_rows = await cursor.fetchall()
        finally:
            await db.close()

        # Merge both sources (claims + ticket_stats closures)
        merged: dict[str, int] = {}
        for r in claim_rows:
            merged[r["uid"]] = max(merged.get(r["uid"], 0), r["c"])
        for r in stats_rows:
            merged[r["user_id"]] = max(merged.get(r["user_id"], 0), r["c"] or 0)
        rows = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:10]

        if not rows:
            await interaction.response.send_message(
                content="No ticket activity recorded yet.", ephemeral=True
            )
            return

        lines = [f"**{i + 1}.** <@{uid}> — {c} tickets" for i, (uid, c) in enumerate(rows)]
        embed = base_embed(title="🏆 Staff Performance")
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket_group.command(name="priority", description="Set this ticket's priority (inside a ticket).")
    @app_commands.default_permissions(manage_guild=True)
    async def priority(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            content="Set ticket priority:", view=PriorityView(self), ephemeral=True
        )

    # ------------------------------------------------------------- listeners

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        # Any message in a tracked, open ticket refreshes activity (auto-close timer)
        db = await get_db()
        try:
            async with db.execute(
                "SELECT 1 FROM ticket_channels WHERE channel_id=? AND status='open'",
                (str(message.channel.id),),
            ) as cursor:
                tracked = await cursor.fetchone()
        finally:
            await db.close()
        if tracked:
            await self.touch_activity(message.channel.id)

    @commands.Cog.listener()
    async def on_ready(self):
        # Persistent views survive restarts
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView(self))
        if not getattr(self, "_controls_refreshed", False):
            self._controls_refreshed = True
            db = await get_db()
            try:
                async with db.execute("SELECT channel_id FROM ticket_channels WHERE status='open'") as cursor:
                    open_tickets = await cursor.fetchall()
            finally:
                await db.close()
            for rec in open_tickets:
                channel = self.bot.get_channel(int(rec['channel_id']))
                if not isinstance(channel, discord.TextChannel):
                    continue
                try:
                    async for message in channel.history(limit=10, oldest_first=True):
                        if message.author.id == self.bot.user.id and any(getattr(item, 'custom_id', '') == 'pffa_close_ticket' for row in message.components for item in row.children):
                            await message.edit(view=TicketControlView(self))
                            break
                except discord.HTTPException as error:
                    print(f"Could not refresh ticket controls in {channel.id}: {error}")
        # Start the auto-close loop only once we're connected
        if not self.autoclose_loop.is_running():
            self.autoclose_loop.start()

    # ------------------------------------------------------------- auto-close

    @tasks.loop(minutes=1)
    async def autoclose_loop(self):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT channel_id, guild_id, category, last_activity, warned FROM ticket_channels WHERE status='open'"
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await db.close()

        now = time.time()
        for r in rows:
            guild = self.bot.get_guild(int(r["guild_id"]))
            if not guild:
                continue
            minutes = await get_setting(guild.id, "ticket_autoclose_minutes")
            minutes = int(minutes) if minutes and minutes.isdigit() else 0
            if minutes <= 0 or not r["last_activity"]:
                continue
            channel = guild.get_channel(int(r["channel_id"]))
            if channel is None:
                continue

            inactive = now - r["last_activity"]
            limit = minutes * 60
            if not r["warned"] and inactive >= limit * 0.75:
                db2 = await get_db()
                try:
                    await db2.execute(
                        "UPDATE ticket_channels SET warned=1 WHERE channel_id=?", (r["channel_id"],)
                    )
                    await db2.commit()
                finally:
                    await db2.close()
                try:
                    await channel.send(
                        embed=discord.Embed(
                            title="⏰ Ticket Closing Soon",
                            description=(
                                f"This ticket has been inactive for **{int(inactive // 60)} minutes**.\n\n"
                                "Reply to keep it open."
                            ),
                            color=discord.Color.orange(),
                        )
                    )
                except discord.HTTPException:
                    pass
            elif r["warned"] and inactive >= limit:
                db3 = await get_db()
                try:
                    await db3.execute("DELETE FROM ticket_channels WHERE channel_id=?", (r["channel_id"],))
                    await db3.commit()
                finally:
                    await db3.close()
                await self.close_ticket_with_transcript(channel, self.bot.user, r["category"] or "General Support")
                try:
                    await channel.delete(reason="Ticket auto-closed (inactivity)")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await self.ticket_log(guild, f"⏰ **Ticket auto-closed** — #{channel.name} (inactive)")


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketSystem(bot))

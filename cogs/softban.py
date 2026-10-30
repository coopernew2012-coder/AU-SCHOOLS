from ui_cards import card_from_embed
import json
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import SOFTBAN_CHANNEL_ID, DATA_DIR
from database import get_setting, set_setting, delete_setting
from utils import base_embed, success_embed, error_embed

# --------------------------------------------------------------- JSON store
# <DATA_DIR>/softbans.json — { "<guild_id>": { "<user_id>": [records...] } }
# Each record: { user_id, timestamp, moderator, reason, message_count_deleted }
SOFTBAN_FILE = os.path.join(DATA_DIR, "softbans.json")


def _load_store() -> dict:
    try:
        with open(SOFTBAN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_store(data: dict):
    os.makedirs(os.path.dirname(SOFTBAN_FILE), exist_ok=True)
    with open(SOFTBAN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def record_softban(guild_id: int, user_id: int, moderator: str, reason: str, deleted: int):
    data = _load_store()
    guild_bucket = data.setdefault(str(guild_id), {})
    user_list = guild_bucket.setdefault(str(user_id), [])
    user_list.append(
        {
            "user_id": user_id,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "moderator": moderator,
            "reason": reason,
            "message_count_deleted": deleted,
        }
    )
    _save_store(data)


def clear_softbans(guild_id: int, user_id: int) -> int:
    """Remove all softban records for a user; returns how many were removed."""
    data = _load_store()
    bucket = data.get(str(guild_id), {})
    removed = len(bucket.get(str(user_id), []))
    if removed:
        del bucket[str(user_id)]
        if not bucket:
            data.pop(str(guild_id), None)
        _save_store(data)
    return removed


def get_softbans(guild_id: int, user_id: int) -> list:
    return _load_store().get(str(guild_id), {}).get(str(user_id), [])


# --------------------------------------------------------------- cooldown
# Simple per-guild anti-abuse cooldown: max 10 softbans per 10 minutes.
COOLDOWN_MAX_ACTIONS = 10
COOLDOWN_WINDOW_SECONDS = 600
_cooldown_log: dict[int, list[float]] = {}


def check_cooldown(guild_id: int) -> tuple[bool, int]:
    """Returns (allowed, seconds_until_next_allowed)."""
    now = time.time()
    stamps = [t for t in _cooldown_log.get(guild_id, []) if now - t < COOLDOWN_WINDOW_SECONDS]
    _cooldown_log[guild_id] = stamps
    if len(stamps) >= COOLDOWN_MAX_ACTIONS:
        return False, int(COOLDOWN_WINDOW_SECONDS - (now - stamps[0]))
    stamps.append(now)
    return True, 0


async def perform_softban(
    guild: discord.Guild,
    user_id: int,
    reason: str,
    moderator_label: str,
    bot: commands.Bot,
) -> dict:
    """Ban, purge the last 7 days of messages, then immediately unban."""
    result = {"ok": False, "banned": False, "unbanned": False, "deleted": 0, "error": None}

    allowed, wait = check_cooldown(guild.id)
    if not allowed:
        result["error"] = (
            f"Softban cooldown active — too many softbans recently. "
            f"Try again in {wait // 60}m {wait % 60}s (max {COOLDOWN_MAX_ACTIONS} per "
            f"{COOLDOWN_WINDOW_SECONDS // 60} minutes)."
        )
        return result

    try:
        await guild.ban(
            discord.Object(id=user_id),
            reason=f"Softban: {reason}",
            delete_message_days=7,
        )
        result["banned"] = True
    except discord.Forbidden:
        result["error"] = "I lack **Ban Members** permission."
        return result
    except discord.NotFound:
        result["error"] = "User not found in this server."
        return result
    except discord.HTTPException as e:
        result["error"] = f"Ban failed: {e}"
        return result

    try:
        await guild.unban(discord.Object(id=user_id), reason=f"Softban complete: {reason}")
        result["unbanned"] = True
    except discord.NotFound:
        result["unbanned"] = True  # already unbanned / never cached — treat as done
    except discord.HTTPException:
        result["unbanned"] = False

    # Estimate messages deleted: the ban call with delete_message_days purges up
    # to 7 days; Discord doesn't return a count, so we report the window instead
    # and record what we know.
    result["deleted"] = 604800 if result["banned"] else 0  # seconds of history purged

    # ---- JSON record ----
    record_softban(
        guild.id,
        user_id,
        moderator_label,
        reason,
        7 if result["banned"] else 0,  # days of messages purged
    )

    # ---- mod log ----
    log_channel_id = await get_setting(guild.id, "mod_log_channel")
    channel = guild.get_channel(int(log_channel_id)) if log_channel_id and log_channel_id.isdigit() else None
    if channel and isinstance(channel, discord.TextChannel):
        embed = base_embed(title="🧹 Softban")
        embed.color = discord.Color.orange()
        embed.add_field(name="User", value=f"<@{user_id}> (`{user_id}`)")
        embed.add_field(name="Moderator", value=moderator_label)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(
            name="Messages Deleted",
            value="Past 7 days" if result["banned"] else "N/A",
        )
        embed.add_field(name="Record", value="Saved to softban log (JSON)", inline=False)
        embed.add_field(
            name="Unban Status",
            value="✅ Unbanned immediately" if result["unbanned"] else "⚠️ Unban failed — manual action required",
        )
        try:
            await channel.send(view=card_from_embed(embed,tone='info'))
        except discord.Forbidden:
            pass

    result["ok"] = True
    return result


class SoftbanPanelView(discord.ui.View):
    """Persistent view holding the warning notice under the softban panel embed."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="How does this work?", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="softban_info_btn")
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            view=card_from_embed(error_embed(
                "Sending **any message** in this channel softbans your own account: you are banned, "
                "your last 7 days of messages are deleted, and you are immediately unbanned. "
                "There is no warning and staff are notified."
            ),tone='danger'),
            ephemeral=True,
        )


class QuickSoftbanView(discord.ui.View):
    """Persistent quick-softban panel: button → user select → instant softban."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Softban a user", style=discord.ButtonStyle.danger, emoji="🧹", custom_id="softban_quick_btn")
    async def quick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message(
                view=card_from_embed(error_embed("You need **Ban Members** to use this."),tone='danger'), ephemeral=True
            )
            return
        await interaction.response.send_modal(QuickSoftbanModal())


class QuickSoftbanModal(discord.ui.Modal, title="Quick Softban"):
    user_id_input = discord.ui.TextInput(
        label="User ID to softban",
        placeholder="e.g. 123456789012345678",
        style=discord.TextStyle.short,
        required=True,
    )
    reason_input = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_id_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                view=card_from_embed(error_embed("That doesn't look like a user ID."),tone='danger'), ephemeral=True
            )
            return
        user_id = int(raw)
        reason = self.reason_input.value.strip() or "Quick softban (no reason provided)"
        await interaction.response.defer(ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if member and (member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id):
            await interaction.followup.send(
                view=card_from_embed(error_embed("You cannot moderate this user."),tone='danger'), ephemeral=True
            )
            return

        result = await perform_softban(
            interaction.guild, user_id, reason, interaction.user.mention, interaction.client
        )
        if not result["ok"]:
            await interaction.followup.send(
                view=card_from_embed(error_embed(f"Failed to softban: {result['error']}"),tone='danger'), ephemeral=True
            )
            return
        if not result["unbanned"]:
            await interaction.followup.send(
                view=card_from_embed(error_embed(
                    "Softban completed but the automatic unban **failed** — unban them manually."
                ),tone='danger'),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            view=card_from_embed(success_embed(
                f"Softbanned <@{user_id}> — recent messages deleted, user is not permanently banned."
            ),tone='success'),
            ephemeral=True,
        )


class Softban(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    softban_group = app_commands.Group(
        name="softban",
        description="Softban users and configure the softban channel.",
        guild_only=True,
        default_permissions=discord.Permissions(ban_members=True),
    )

    @softban_group.command(name="user", description="Ban, delete their last 7 days of messages, then immediately unban.")
    @app_commands.describe(user="User to softban.", reason="Reason for the softban.")
    @app_commands.default_permissions(ban_members=True)
    async def softban_user(
        self, interaction: discord.Interaction,
        user: discord.User,
        reason: str = "No reason provided",
    ):
        member = interaction.guild.get_member(user.id)
        if member:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
                await interaction.response.send_message(
                    view=card_from_embed(error_embed("You cannot moderate this user."),tone='danger'), ephemeral=True
                )
                return
            if member.id == self.bot.user.id:
                await interaction.response.send_message(
                    view=card_from_embed(error_embed("I cannot softban myself."),tone='danger'), ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True)
        result = await perform_softban(
            interaction.guild, user.id, reason, interaction.user.mention, self.bot
        )

        if not result["ok"]:
            await interaction.followup.send(
                view=card_from_embed(error_embed(f"Failed to softban: {result['error']}"),tone='danger'), ephemeral=True
            )
            return
        if not result["unbanned"]:
            await interaction.followup.send(
                view=card_from_embed(error_embed(
                    f"{user.mention} was banned and recent messages deleted, but the automatic unban **failed**. "
                    "Please unban them manually with `/softban user` logic or the ban list."
                ),tone='danger'),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            view=card_from_embed(success_embed(
                f"Softbanned {user.mention} — recent messages deleted, user is **not** permanently banned."
            ),tone='success'),
            ephemeral=True,
        )

    @softban_group.command(name="unsoftban", description="Clear a user's softban record (they are already unbanned; this clears the log entry).")
    @app_commands.describe(user="User whose softban record to clear.")
    @app_commands.default_permissions(ban_members=True)
    async def unsoftban(self, interaction: discord.Interaction, user: discord.User):
        """Reverse a softban record. The user is already unbanned (softbans auto-unban);
        this clears the JSON record so /softban history shows nothing. If a user was
        hard-banned by mistake, use /unban instead."""
        removed = clear_softbans(interaction.guild.id, user.id)
        if not removed:
            await interaction.response.send_message(
                view=card_from_embed(error_embed(f"No softban records for {user.mention}."),tone='danger'), ephemeral=True
            )
            return

        # If they somehow ended up actually banned, unban them too.
        try:
            await interaction.guild.unban(user, reason=f"Unsoftban by {interaction.user}")
            also_unbanned = " and lifted their ban"
        except discord.NotFound:
            also_unbanned = ""
        except discord.HTTPException:
            also_unbanned = " (⚠️ they appear to be banned but I couldn't unban — check manually)"

        await interaction.response.send_message(
            view=card_from_embed(success_embed(
                f"Cleared {removed} softban record(s) for {user.mention}{also_unbanned}."
            ),tone='success'),
            ephemeral=True,
        )

        log_channel_id = await get_setting(interaction.guild.id, "mod_log_channel")
        channel = (
            interaction.guild.get_channel(int(log_channel_id))
            if log_channel_id and log_channel_id.isdigit()
            else None
        )
        if channel and isinstance(channel, discord.TextChannel):
            embed = base_embed(title="↩️ Softban Reversed")
            embed.color = discord.Color.green()
            embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)")
            embed.add_field(name="Moderator", value=interaction.user.mention)
            embed.add_field(name="Records Cleared", value=str(removed))
            try:
                await channel.send(view=card_from_embed(embed,tone='info'))
            except discord.Forbidden:
                pass

    @softban_group.command(name="history", description="View a user's softban records.")
    @app_commands.describe(user="User to check.")
    @app_commands.default_permissions(ban_members=True)
    async def history(self, interaction: discord.Interaction, user: discord.User):
        """Show all recorded softbans for a user from the JSON log."""
        records = get_softbans(interaction.guild.id, user.id)
        if not records:
            await interaction.response.send_message(
                embed=error_embed(f"No softban records for {user.mention}."), ephemeral=True
            )
            return

        embed = base_embed(title=f"🧹 Softban History — {user.display_name}")
        lines = []
        for i, r in enumerate(reversed(records[-10:]), 1):
            lines.append(
                f"**{i}.** {r['timestamp']} — by {r['moderator']}\n"
                f"> Reason: {r['reason']}\n"
                f"> Messages purged: {r['message_count_deleted']} day(s) of history"
            )
        embed.description = "\n\n".join(lines)[:4096]
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @softban_group.command(name="quick-panel", description="Post a panel with a quick-softban button for the user you specify when clicking.")
    @app_commands.describe(channel="Channel to post the quick-softban panel in.")
    @app_commands.default_permissions(administrator=True)
    async def quick_panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Posts a staff-use panel with a button that opens a user picker; picking a
        user immediately softbans them (with reason prompt)."""
        await interaction.response.defer(ephemeral=True)
        embed = base_embed(title="🧹 Quick Softban")
        embed.description = (
            "Staff tool: click the button and select a user to instantly softban them "
            "(ban → delete last 7 days of messages → unban).\n\n"
            "⚠️ Use with care — actions are logged to the mod log."
        )
        try:
            await channel.send(embed=embed, view=QuickSoftbanView())
        except discord.Forbidden:
            await interaction.followup.send(
                view=card_from_embed(error_embed("I lack permission to post in that channel."),tone='danger'), ephemeral=True
            )
            return
        await interaction.followup.send(
            view=card_from_embed(success_embed(f"Quick softban panel posted in {channel.mention}."),tone='success'), ephemeral=True
        )

    @softban_group.command(name="set-channel", description="Set the channel where any message auto-softbans the sender.")
    @app_commands.describe(channel="Channel that triggers a softban on message.")
    @app_commands.default_permissions(administrator=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await set_setting(interaction.guild.id, "softban_channel", str(channel.id))
        await interaction.response.send_message(
            view=card_from_embed(success_embed(
                f"Softban channel set to {channel.mention}. Messages there now trigger an instant softban."
            ),tone='success'),
            ephemeral=True,
        )
        await self.post_panel(channel)

    @softban_group.command(name="clear-channel", description="Disable the auto-softban channel.")
    @app_commands.default_permissions(administrator=True)
    async def clear_channel(self, interaction: discord.Interaction):
        await delete_setting(interaction.guild.id, "softban_channel")
        await interaction.response.send_message(
            view=card_from_embed(success_embed("Softban channel disabled."),tone='success'), ephemeral=True
        )

    @softban_group.command(name="panel", description="Post or refresh the softban info panel in the configured channel.")
    @app_commands.default_permissions(administrator=True)
    async def send_panel(self, interaction: discord.Interaction):
        channel_id = await get_setting(interaction.guild.id, "softban_channel")
        channel = (
            interaction.guild.get_channel(int(channel_id))
            if channel_id and channel_id.isdigit()
            else None
        )
        if not channel:
            await interaction.response.send_message(
                view=card_from_embed(error_embed("No softban channel configured. Use `/softban set-channel` first."),tone='danger'),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.post_panel(channel)
        await interaction.followup.send(
            view=card_from_embed(success_embed(f"Softban panel posted/refreshed in {channel.mention}."),tone='success'), ephemeral=True
        )

    async def post_panel(self, channel: discord.TextChannel):
        """Post (or edit) the standing softban info panel."""
        embed = base_embed(title="🧹 Softban Channel")
        embed.description = (
            "This channel is used to softban users who are suspected of spam or unwanted activity.\n\n"
            "**What is a soft ban?**\n"
            "A soft ban removes a user's recent messages without permanently banning them.\n\n"
            "**How it works:**\n"
            "1. Ban the user from the server.\n"
            "2. Delete all of their messages from the past 7 days.\n"
            "3. Immediately unban them.\n\n"
            "⚠️ Sending **any message** in this channel triggers this process on your own account."
        )
        embed.color = discord.Color.orange()

        async for msg in channel.history(limit=25):
            if msg.author == channel.guild.me and msg.embeds and msg.embeds[0].title == "🧹 Softban Channel":
                try:
                    await msg.edit(embed=embed, view=SoftbanPanelView())
                    return
                except Exception:
                    break
        try:
            await channel.send(embed=embed, view=SoftbanPanelView())
        except discord.Forbidden:
            pass

    # ------------------------------------------------------------- listener

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not isinstance(message.author, discord.Member):
            return
        # Administrators are exempt from the auto-trigger
        if message.author.guild_permissions.administrator:
            return

        channel_id = await get_setting(message.guild.id, "softban_channel")
        if not channel_id or str(message.channel.id) != channel_id:
            return

        me = message.guild.me
        if not me.guild_permissions.ban_members:
            try:
                await message.channel.send(
                    view=card_from_embed(error_embed("⚠️ I lack **Ban Members** permission — softban aborted."),tone='danger')
                )
            except discord.Forbidden:
                pass
            return

        # Remove the triggering message so it doesn't linger
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        result = await perform_softban(
            message.guild,
            message.author.id,
            f"Sent a message in the softban channel (#{message.channel.name})",
            "Automatic (softban channel)",
            self.bot,
        )
        if not result["ok"]:
            try:
                await message.channel.send(
                    view=card_from_embed(error_embed(f"⚠️ Failed to softban {message.author.mention}: {result['error']}"),tone='danger')
                )
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(SoftbanPanelView())
        self.bot.add_view(QuickSoftbanView())


async def setup(bot: commands.Bot):
    await bot.add_cog(Softban(bot))

from ui_cards import card_from_embed
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import base_embed, success_embed, error_embed


def fmt_msg(text: str, member: discord.Member) -> str:
    return text.replace("{user}", str(member)).replace("{user_mention}", member.mention).replace("{server_name}", member.guild.name).replace("{member_count}", str(member.guild.member_count))


async def _upsert(guild_id: int, **cols):
    """Update only the given columns of welcome_config without wiping the rest.
    (INSERT OR REPLACE deletes the whole row — that was the toggle bug.)"""
    db = await get_db()
    try:
        await db.execute(
            f"INSERT INTO welcome_config (guild_id, {', '.join(cols)}) VALUES (?{', ?' * len(cols)}) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}",
            (str(guild_id), *cols.values()),
        )
        await db.commit()
    finally:
        await db.close()


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    welcome_group = app_commands.Group(name="welcome", description="Configure welcome/goodbye messages.", guild_only=True, default_permissions=discord.Permissions(administrator=True))

    @welcome_group.command(name="toggle", description="Enable or disable welcome messages.")
    @app_commands.describe(state="Enable or disable welcome messages.")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @app_commands.default_permissions(administrator=True)
    async def toggle_welcome(self, interaction: discord.Interaction, state: str):
        enabled = 1 if state == "on" else 0
        await _upsert(interaction.guild.id, enabled=enabled)
        await interaction.response.send_message(view=card_from_embed(success_embed(f"Welcome messages: **{state.upper()}**"),tone='success'), ephemeral=True)

    @welcome_group.command(name="channel", description="Set the channel for welcome messages.")
    @app_commands.describe(channel="Channel for welcome messages.")
    @app_commands.default_permissions(administrator=True)
    async def set_welcome_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await _upsert(interaction.guild.id, channel_id=str(channel.id))
        await interaction.response.send_message(view=card_from_embed(success_embed(f"Welcome channel set to {channel.mention}."),tone='success'), ephemeral=True)

    @welcome_group.command(name="message", description="Set the welcome message text. Use {user}, {user_mention}, {server_name}, {member_count}.")
    @app_commands.describe(text="Use {user}, {user_mention}, {server_name}, {member_count}.")
    @app_commands.default_permissions(administrator=True)
    async def set_welcome_message(self, interaction: discord.Interaction, text: str):
        await _upsert(interaction.guild.id, message=text)
        await interaction.response.send_message(view=card_from_embed(success_embed("Welcome message updated."),tone='success'), ephemeral=True)

    @welcome_group.command(name="goodbye-toggle", description="Enable or disable goodbye messages.")
    @app_commands.describe(state="Enable or disable goodbye messages.")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @app_commands.default_permissions(administrator=True)
    async def goodbye_toggle(self, interaction: discord.Interaction, state: str):
        enabled = 1 if state == "on" else 0
        await _upsert(interaction.guild.id, goodbye_enabled=enabled)
        await interaction.response.send_message(view=card_from_embed(success_embed(f"Goodbye messages: **{state.upper()}**"),tone='success'), ephemeral=True)

    @welcome_group.command(name="goodbye-channel", description="Set the channel for goodbye messages.")
    @app_commands.describe(channel="Channel for goodbye messages.")
    @app_commands.default_permissions(administrator=True)
    async def set_goodbye_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await _upsert(interaction.guild.id, goodbye_channel_id=str(channel.id))
        await interaction.response.send_message(view=card_from_embed(success_embed(f"Goodbye channel set to {channel.mention}."),tone='success'), ephemeral=True)

    @welcome_group.command(name="goodbye-message", description="Set the goodbye message text. Use {user}, {user_mention}, {server_name}.")
    @app_commands.describe(text="Use {user}, {user_mention}, {server_name}.")
    @app_commands.default_permissions(administrator=True)
    async def set_goodbye_message(self, interaction: discord.Interaction, text: str):
        await _upsert(interaction.guild.id, goodbye_message=text)
        await interaction.response.send_message(view=card_from_embed(success_embed("Goodbye message updated."),tone='success'), ephemeral=True)

    @welcome_group.command(name="status", description="Show the full welcome/goodbye configuration at a glance.")
    @app_commands.default_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        """One view of everything: toggles, channels, messages — and what's missing."""
        db = await get_db()
        try:
            async with db.execute("SELECT * FROM welcome_config WHERE guild_id=?", (str(interaction.guild.id),)) as cursor:
                row = await cursor.fetchone()
        finally:
            await db.close()

        def chan_label(cid):
            if not cid:
                return "**not set** — run `/welcome channel`"
            ch = interaction.guild.get_channel(int(cid)) if cid.isdigit() else None
            return ch.mention if ch else f"deleted channel ({cid}) — run `/welcome channel` again"

        if not row:
            await interaction.response.send_message(
                view=card_from_embed(error_embed(
                    "Nothing configured yet.\n"
                    "Setup: `/welcome channel` → `/welcome toggle on` → `/welcome message` (optional)"
                ),tone='danger'),
                ephemeral=True,
            )
            return

        embed = base_embed(title="👋 Welcome / Goodbye Settings")
        embed.add_field(
            name="Welcome",
            value=(
                f"Toggle: **{'ON' if row['enabled'] else 'OFF'}**\n"
                f"Channel: {chan_label(row['channel_id'])}\n"
                f"Message: {(row['message'] or 'default')[:80]}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Goodbye",
            value=(
                f"Toggle: **{'ON' if row['goodbye_enabled'] else 'OFF'}**\n"
                f"Channel: {chan_label(row['goodbye_channel_id'])}\n"
                f"Message: {(row['goodbye_message'] or 'default')[:80]}"
            ),
            inline=False,
        )
        embed.set_footer(text="Settings are stored on the bot's disk — make sure DATA_DIR=/data is set on Railway with a Volume, or they reset on every deploy.")
        await interaction.response.send_message(view=card_from_embed(embed,tone='info'), ephemeral=True)

    @welcome_group.command(name="test", description="Send a test welcome message using your info to verify the configuration.")
    @app_commands.default_permissions(administrator=True)
    async def test_welcome(self, interaction: discord.Interaction):
        db = await get_db()
        try:
            async with db.execute("SELECT * FROM welcome_config WHERE guild_id=?", (str(interaction.guild.id),)) as cursor:
                row = await cursor.fetchone()
        finally:
            await db.close()
        if not row or not row["enabled"]:
            await interaction.response.send_message(
                view=card_from_embed(error_embed("Welcome messages are **OFF** — run `/welcome toggle on` first."),tone='danger'), ephemeral=True
            )
            return
        if not row["channel_id"]:
            await interaction.response.send_message(
                view=card_from_embed(error_embed(
                    "The toggle is ON but no **channel** is set — run `/welcome channel #your-channel`.\n"
                    "(If you set one before, it was lost in a redeploy — see `/welcome status`.)"
                ),tone='danger'),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(row["channel_id"]))
        if not channel:
            await interaction.response.send_message(
                view=card_from_embed(error_embed(
                    "The saved welcome channel was **deleted** — run `/welcome channel` again to pick a new one."
                ),tone='danger'),
                ephemeral=True,
            )
            return
        msg = fmt_msg(row["message"] or "Welcome {user_mention}!", interaction.user)
        embed = base_embed(title=f"Welcome to AU Schools!", description=msg)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await channel.send(view=card_from_embed(embed,tone='info'))
        await interaction.response.send_message(view=card_from_embed(success_embed(f"Test welcome sent to {channel.mention}."),tone='success'), ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        db = await get_db()
        try:
            async with db.execute("SELECT * FROM welcome_config WHERE guild_id=?", (str(member.guild.id),)) as cursor:
                row = await cursor.fetchone()
        finally:
            await db.close()
        if not row or not row["enabled"] or not row["channel_id"]:
            return
        channel = member.guild.get_channel(int(row["channel_id"]))
        if not channel:
            return
        msg = fmt_msg(row["message"] or "Welcome {user_mention} to {server_name}!", member)
        embed = base_embed(title=f"Welcome to AU Schools!", description=msg)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(view=card_from_embed(embed,tone='info'))
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        db = await get_db()
        try:
            async with db.execute("SELECT * FROM welcome_config WHERE guild_id=?", (str(member.guild.id),)) as cursor:
                row = await cursor.fetchone()
        finally:
            await db.close()
        if not row or not row["goodbye_enabled"] or not row["goodbye_channel_id"]:
            return
        channel = member.guild.get_channel(int(row["goodbye_channel_id"]))
        if not channel:
            return
        msg = fmt_msg(row["goodbye_message"] or "{user_mention} has left. Goodbye!", member)
        embed = base_embed(title="AU Schools | Goodbye!", description=msg)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(view=card_from_embed(embed,tone='info'))
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))

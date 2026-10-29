from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db, get_setting
from utils import base_embed, success_embed, error_embed


class Warnings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_action(self, guild, action, moderator, target, reason=None):
        channel_id = await get_setting(guild.id, "mod_log_channel")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not channel:
            return
        embed = base_embed(title=f"Moderation: {action}")
        embed.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)")
        embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)")
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass

    @app_commands.command(name="warn", description="Issue a warning to a member and log it to the moderation channel.")
    @app_commands.describe(member="Member to warn.", reason="Reason for the warning.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None):
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO warnings (guild_id, user_id, moderator, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
                (str(interaction.guild.id), str(member.id), str(interaction.user.id), reason, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

            async with db.execute(
                "SELECT COUNT(*) as count FROM warnings WHERE guild_id=? AND user_id=?",
                (str(interaction.guild.id), str(member.id)),
            ) as cursor:
                row = await cursor.fetchone()
            count = row["count"]
        finally:
            await db.close()

        embed = success_embed(f"{member.mention} has been warned (warning #{count}).")
        if reason:
            embed.add_field(name="Reason", value=reason)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._log_action(interaction.guild, f"Warn (#{count})", interaction.user, member, reason)

        try:
            dm_embed = base_embed(
                title=f"You were warned in {interaction.guild.name}",
                description=f"**Reason:** {reason or 'No reason provided.'}",
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

    @app_commands.command(name="warnings", description="View the warning history for a member.")
    @app_commands.describe(member="Member to check warnings for.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY timestamp DESC LIMIT 25",
                (str(interaction.guild.id), str(member.id)),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await db.close()

        if not rows:
            embed = base_embed(title=f"Warnings: {member.display_name}", description="No warnings on record.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = base_embed(title=f"Warnings: {member.display_name} ({len(rows)} total)")
        for i, w in enumerate(rows, 1):
            mod = interaction.guild.get_member(int(w["moderator"]))
            mod_name = mod.mention if mod else f"Unknown ({w['moderator']})"
            ts = datetime.fromisoformat(w["timestamp"]).strftime("%Y-%m-%d %H:%M")
            embed.add_field(
                name=f"#{i} - {ts}",
                value=f"**By:** {mod_name}\n**Reason:** {w['reason'] or 'N/A'}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clear-warnings", description="Delete all warnings for a member.")
    @app_commands.describe(member="Member to clear all warnings for.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def clear_warnings(self, interaction: discord.Interaction, member: discord.Member):
        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM warnings WHERE guild_id=? AND user_id=?",
                (str(interaction.guild.id), str(member.id)),
            )
            await db.commit()
        finally:
            await db.close()
        await interaction.response.send_message(
            embed=success_embed(f"Cleared all warnings for {member.mention}."),
            ephemeral=True,
        )
        await self._log_action(interaction.guild, "Clear Warnings", interaction.user, member)


async def setup(bot: commands.Bot):
    await bot.add_cog(Warnings(bot))

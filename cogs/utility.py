from ui_cards import card_from_embed
import discord
from discord import app_commands
from discord.ext import commands
from database import get_setting
from utils import base_embed, success_embed, error_embed
from cogs.staff import DEFAULT_SERVERINFO_TEMPLATE
from config import DISCORD_INVITE


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="say", description="Send a message as AU Schools in a channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(message="Message to send.", channel="Destination; defaults to this channel.")
    async def say(self, interaction: discord.Interaction, message: app_commands.Range[str, 1, 2000], channel: discord.TextChannel | None = None):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("Manage Messages permission is required.", ephemeral=True)
        destination = channel or interaction.channel
        if not destination.permissions_for(interaction.user).view_channel or not destination.permissions_for(interaction.user).send_messages:
            return await interaction.response.send_message("You need permission to send messages in that channel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            sent = await destination.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return await interaction.followup.send("I cannot send messages in that channel.", ephemeral=True)
        await interaction.followup.send(f"Message sent: {sent.jump_url}", ephemeral=True)

    @app_commands.command(name="ping", description="Check the bot's current latency.")
    @app_commands.guild_only()
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        embed = base_embed(title="Pong!", description=f"Latency: **{latency}ms**")
        await interaction.response.send_message(view=card_from_embed(embed,tone='info'))

    @app_commands.command(name="server-info", description="Display detailed information about this server.")
    @app_commands.guild_only()
    async def server_info(self, interaction: discord.Interaction):
        guild = interaction.guild
        created = discord.utils.format_dt(guild.created_at, style="F")
        embed = base_embed(title="AU Schools | Server Information")
        template = await get_setting(guild.id, "community_info_text") or DEFAULT_SERVERINFO_TEMPLATE
        invite = await get_setting(guild.id, "discord_invite") or DISCORD_INVITE or "Invite not configured"
        embed.description = template.replace("{invite}", invite)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url if self.bot.user else None)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown")
        embed.add_field(name="Created", value=created)
        embed.add_field(name="Members", value=str(guild.member_count))
        embed.add_field(name="Roles", value=str(len(guild.roles)))
        embed.add_field(name="Channels", value=str(len(guild.channels)))
        embed.add_field(name="Boost Tier", value=str(guild.premium_tier))
        embed.add_field(name="Emojis", value=f"{len(guild.emojis)}/{guild.emoji_limit}")
        embed.set_footer(text=f"Server ID: {guild.id}")
        await interaction.response.send_message(view=card_from_embed(embed,tone='info'))

    @app_commands.command(name="user-info", description="Display detailed information about yourself or another member.")
    @app_commands.describe(member="User to inspect (defaults to you).")
    @app_commands.guild_only()
    async def user_info(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        embed = base_embed(title=f"User Info: {member.display_name}")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Username", value=f"{member.name}#{member.discriminator}" if member.discriminator != "0" else member.name)
        embed.add_field(name="ID", value=str(member.id))
        embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "?")
        embed.add_field(name="Created", value=discord.utils.format_dt(member.created_at, style="R"))
        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.name}")
        await interaction.response.send_message(view=card_from_embed(embed,tone='info'))

    @app_commands.command(name="role-info", description="Display detailed information about a specific role.")
    @app_commands.describe(role="Role to inspect.")
    @app_commands.guild_only()
    async def role_info(self, interaction: discord.Interaction, role: discord.Role):
        embed = base_embed(title=f"Role Info: {role.name}")
        embed.add_field(name="ID", value=str(role.id))
        embed.add_field(name="Color", value=str(role.color))
        embed.add_field(name="Position", value=str(role.position))
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No")
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No")
        embed.add_field(name="Members", value=str(len(role.members)))
        embed.add_field(name="Created", value=discord.utils.format_dt(role.created_at, style="F"))
        embed.set_footer(text=f"Role ID: {role.id}")
        await interaction.response.send_message(view=card_from_embed(embed,tone='info'), ephemeral=True)

    @app_commands.command(name="avatar", description="Display a user's avatar in full size.")
    @app_commands.describe(member="User whose avatar to display.")
    @app_commands.guild_only()
    async def avatar(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        embed = base_embed(title=f"{member.display_name}'s Avatar")
        embed.set_image(url=member.display_avatar.url)
        await interaction.response.send_message(view=card_from_embed(embed,tone='info'))

    @app_commands.command(name="slowmode", description="Set the slowmode delay for the current channel (0 to disable).")
    @app_commands.describe(seconds="Slowmode delay (0 to disable).")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await interaction.response.send_message(
                view=card_from_embed(success_embed("Slowmode disabled."),tone='success'), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                view=card_from_embed(success_embed(f"Slowmode set to **{seconds}s**."),tone='success'), ephemeral=True
            )

    @app_commands.command(name="lock", description="Lock a channel to prevent @everyone from sending messages.")
    @app_commands.describe(channel="Channel to lock (defaults to current).")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        channel = channel or interaction.channel
        await channel.set_permissions(interaction.guild.default_role, send_messages=False)
        await interaction.response.send_message(
            view=card_from_embed(success_embed(f"🔒 {channel.mention} has been locked."),tone='success'), ephemeral=True
        )

    @app_commands.command(name="unlock", description="Unlock a channel to allow @everyone to send messages again.")
    @app_commands.describe(channel="Channel to unlock (defaults to current).")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        channel = channel or interaction.channel
        await channel.set_permissions(interaction.guild.default_role, send_messages=True)
        await interaction.response.send_message(
            view=card_from_embed(success_embed(f"🔓 {channel.mention} has been unlocked."),tone='success'), ephemeral=True
        )






    @app_commands.command(name="purge-user", description="Delete recent messages from a specific user (1-100).")
    @app_commands.describe(member="User whose messages to purge.", count="Number of messages (1-100).")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge_user(
        self, interaction: discord.Interaction, member: discord.Member,
        count: app_commands.Range[int, 1, 100]
    ):
        await interaction.response.defer(ephemeral=True)
        def check(m):
            return m.author.id == member.id
        try:
            deleted = await interaction.channel.purge(limit=count, check=check)
        except discord.Forbidden:
            await interaction.followup.send(
                view=card_from_embed(error_embed("Missing permissions to delete messages."),tone='danger'),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            view=card_from_embed(success_embed(f"Purged {len(deleted)} messages from {member.mention}."),tone='success'),
            ephemeral=True,
        )




async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))

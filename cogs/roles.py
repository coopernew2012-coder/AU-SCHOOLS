from ui_cards import card_from_embed
import discord
from discord import app_commands
from discord.ext import commands
from database import get_setting
from utils import base_embed, success_embed, error_embed


async def log_role_action(bot, guild, action, moderator, target, role, reason=None):
    channel_id = await get_setting(guild.id, "mod_log_channel")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return
    embed = base_embed(title=f"Role Action: {action}")
    embed.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)")
    embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)")
    embed.add_field(name="Role", value=f"{role.mention} (`{role.id}`)")
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    try:
        await channel.send(view=card_from_embed(embed,tone='info'))
    except discord.Forbidden:
        pass


class RoleManagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    role_cmds = app_commands.Group(
        name="role",
        description="Manage member roles.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @role_cmds.command(name="add", description="Add a role to a member.")
    @app_commands.describe(member="Target member.", role="Role to add.", reason="Optional reason.")
    @app_commands.default_permissions(manage_roles=True)
    async def role_add(
        self, interaction: discord.Interaction, member: discord.Member,
        role: discord.Role, reason: str | None = None,
    ):
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                view=card_from_embed(error_embed("I cannot assign a role equal to or above my own highest role."),tone='danger'),
                ephemeral=True,
            )
            return
        if role in member.roles:
            await interaction.response.send_message(
                view=card_from_embed(error_embed(f"{member.mention} already has {role.mention}."),tone='danger'),
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason=reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                view=card_from_embed(error_embed("Missing permissions to add that role."),tone='danger'),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            view=card_from_embed(success_embed(f"Added {role.mention} to {member.mention}."),tone='success'),
            ephemeral=True,
        )
        await log_role_action(self.bot, interaction.guild, "Add Role", interaction.user, member, role, reason)

    @role_cmds.command(name="remove", description="Remove a role from a member.")
    @app_commands.describe(member="Target member.", role="Role to remove.", reason="Optional reason.")
    @app_commands.default_permissions(manage_roles=True)
    async def role_remove(
        self, interaction: discord.Interaction, member: discord.Member,
        role: discord.Role, reason: str | None = None,
    ):
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                view=card_from_embed(error_embed("I cannot remove a role equal to or above my own highest role."),tone='danger'),
                ephemeral=True,
            )
            return
        if role not in member.roles:
            await interaction.response.send_message(
                view=card_from_embed(error_embed(f"{member.mention} does not have {role.mention}."),tone='danger'),
                ephemeral=True,
            )
            return

        try:
            await member.remove_roles(role, reason=reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                view=card_from_embed(error_embed("Missing permissions to remove that role."),tone='danger'),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            view=card_from_embed(success_embed(f"Removed {role.mention} from {member.mention}."),tone='success'),
            ephemeral=True,
        )
        await log_role_action(self.bot, interaction.guild, "Remove Role", interaction.user, member, role, reason)

    @role_cmds.command(name="mass", description="Bulk add or remove a role from all members who have a specific role.")
    @app_commands.describe(role="Role to add or remove from members.", action="add or remove.", target_role="Everyone with this role will be affected.")
    @app_commands.choices(action=[
        app_commands.Choice(name="add", value="add"),
        app_commands.Choice(name="remove", value="remove"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def role_mass(
        self, interaction: discord.Interaction, role: discord.Role,
        action: str, target_role: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)

        members = list(set(target_role.members))
        success = 0
        failed = 0

        for m in members:
            try:
                if action == "add":
                    await m.add_roles(role)
                else:
                    await m.remove_roles(role)
                success += 1
            except discord.Forbidden:
                failed += 1

        await interaction.followup.send(
            view=card_from_embed(success_embed(
                f"Mass role {'add' if action == 'add' else 'remove'} complete.\n"
                f"**{role.mention}**: {success} succeeded, {failed} failed ({len(members)} total)."
            ),tone='success'),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleManagement(bot))

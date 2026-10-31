import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from config import MAIN_GUILD_ID
from access import JOIN_ROLE
from utils import base_embed, success_embed, error_embed


class AutoRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    autorole = app_commands.Group(
        name="autorole",
        description="Configure automatic role assignment on member join.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @autorole.command(name="add", description="Add a role to be automatically assigned when a member joins.")
    @app_commands.describe(role="Role to auto-assign on member join.")
    @app_commands.default_permissions(administrator=True)
    async def autorole_add(self, interaction: discord.Interaction, role: discord.Role):
        db = await get_db()
        try:
            await db.execute(
                "INSERT OR IGNORE INTO autorole_roles (guild_id, role_id) VALUES (?, ?)",
                (str(interaction.guild.id), str(role.id)),
            )
            await db.commit()
        finally:
            await db.close()

        await interaction.response.send_message(
            embed=success_embed(f"Added {role.mention} to auto-roles."),
            ephemeral=True,
        )

    @autorole.command(name="remove", description="Remove a role from the auto-assign list.")
    @app_commands.describe(role="Role to remove from auto-assign.")
    @app_commands.default_permissions(administrator=True)
    async def autorole_remove(self, interaction: discord.Interaction, role: discord.Role):
        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM autorole_roles WHERE guild_id=? AND role_id=?",
                (str(interaction.guild.id), str(role.id)),
            )
            await db.commit()
        finally:
            await db.close()

        await interaction.response.send_message(
            embed=success_embed(f"Removed {role.mention} from auto-roles."),
            ephemeral=True,
        )

    @autorole.command(name="list", description="Show all roles currently configured for auto-assignment.")
    @app_commands.default_permissions(administrator=True)
    async def autorole_list(self, interaction: discord.Interaction):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT role_id FROM autorole_roles WHERE guild_id=?",
                (str(interaction.guild.id),),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await db.close()

        if not rows:
            await interaction.response.send_message(
                embed=error_embed("No auto-roles configured."),
                ephemeral=True,
            )
            return

        mentions = []
        for row in rows:
            role = interaction.guild.get_role(int(row["role_id"]))
            mentions.append(role.mention if role else f"Unknown ({row['role_id']})")
        embed = base_embed(title="Auto-Roles", description="\n".join(mentions))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole.command(name="toggle", description="Enable or disable the auto-role system.")
    @app_commands.describe(state="on or off")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def toggle_autorole(self, interaction: discord.Interaction, state: str):
        enabled = 1 if state == "on" else 0
        db = await get_db()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO autorole_config (guild_id, enabled) VALUES (?, ?)",
                (str(interaction.guild.id), enabled),
            )
            await db.commit()
        finally:
            await db.close()

        await interaction.response.send_message(
            embed=success_embed(f"Auto-role is now **{state.upper()}**."),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # AU Schools' Member role is mandatory, independent of legacy toggles
        # or any additional saved auto-roles.
        if member.guild.id==MAIN_GUILD_ID:
            role=member.guild.get_role(JOIN_ROLE)
            if role:
                try:
                    await member.add_roles(role,reason='AU Schools automatic member role on join')
                except discord.HTTPException as error:
                    print(f'Join role assignment failed for {member.id}: HTTP {error.status}')
            else:
                print(f'Join role {JOIN_ROLE} is missing in AU Schools')
        db = await get_db()
        try:
            async with db.execute(
                "SELECT enabled FROM autorole_config WHERE guild_id=?",
                (str(member.guild.id),),
            ) as cursor:
                config = await cursor.fetchone()

            if config and not config["enabled"]:
                return

            async with db.execute(
                "SELECT role_id FROM autorole_roles WHERE guild_id=?",
                (str(member.guild.id),),
            ) as cursor:
                roles = await cursor.fetchall()

            if not roles and member.guild.id==MAIN_GUILD_ID:
                roles=[{'role_id':str(JOIN_ROLE)}]

            for role_row in roles:
                if member.guild.id==MAIN_GUILD_ID and int(role_row['role_id'])==JOIN_ROLE:
                    continue
                role = member.guild.get_role(int(role_row["role_id"]))
                if role:
                    try:
                        await member.add_roles(role)
                    except discord.Forbidden:
                        pass
        finally:
            await db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoRole(bot))

import random
import string
import discord
from discord import app_commands
from discord.ext import commands
from database import get_setting, set_setting, delete_setting
from utils import base_embed, success_embed, error_embed


def generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


class VerifyModal(discord.ui.Modal):
    def __init__(self, code: str, role_id: str | None):
        self.code = code
        self.role_id = role_id
        super().__init__(title=f"Enter the code: {code}", timeout=120)
        self.code_input = discord.ui.TextInput(
            label=f"Type this code: {code}",
            placeholder=code,
            min_length=len(code),
            max_length=len(code),
            required=True,
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.code_input.value.strip().upper() != self.code.upper():
            await interaction.response.send_message(
                embed=error_embed("Wrong code! Click Verify to try again."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        if self.role_id and self.role_id.isdigit():
            role = interaction.guild.get_role(int(self.role_id))
            if role:
                try:
                    await interaction.user.add_roles(role)
                except discord.Forbidden:
                    await interaction.followup.send(
                        embed=error_embed("Verified, but role assignment failed. Contact an admin."),
                        ephemeral=True,
                    )
                    return

        await interaction.followup.send(
            embed=success_embed("You are now verified! Welcome to the server."),
            ephemeral=True,
        )


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.green, emoji="✅", custom_id="verify_btn")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_id = await get_setting(interaction.guild.id, "verify_role")
        code = generate_code()
        modal = VerifyModal(code, role_id)
        await interaction.response.send_modal(modal)


class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="verify_setup", description="Create a verification panel with a captcha code button.")
    @app_commands.describe(
        channel="Channel to send the verification panel to.",
        role="Role given to users upon successful verification.",
        title="Panel embed title.",
        description="Panel embed description.",
    )
    @app_commands.default_permissions(administrator=True)
    async def verify_setup(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel, role: discord.Role,
        title: str = "Verification Required",
        description: str = "Click the Verify button below and enter the code shown to gain access to the server.",
    ):
        await set_setting(interaction.guild.id, "verify_role", str(role.id))
        await set_setting(interaction.guild.id, "verify_channel", str(channel.id))

        embed = base_embed(title=title, description=description)
        embed.set_footer(text="You have 2 minutes to enter the code.")
        view = VerifyView()
        msg = await channel.send(embed=embed, view=view)

        await set_setting(interaction.guild.id, "verify_message_id", str(msg.id))

        await interaction.response.send_message(
            embed=success_embed(f"Verification panel created in {channel.mention}. Role: {role.mention}"),
            ephemeral=True,
        )

    @app_commands.command(name="verify_delete", description="Delete the verification panel and settings.")
    @app_commands.default_permissions(administrator=True)
    async def verify_delete(self, interaction: discord.Interaction):
        msg_id = await get_setting(interaction.guild.id, "verify_message_id")
        chan_id = await get_setting(interaction.guild.id, "verify_channel")

        if msg_id and chan_id:
            channel = interaction.guild.get_channel(int(chan_id))
            if channel:
                try:
                    message = await channel.fetch_message(int(msg_id))
                    await message.delete()
                except Exception:
                    pass

        for key in ("verify_role", "verify_channel", "verify_message_id"):
            await delete_setting(interaction.guild.id, key)

        await interaction.response.send_message(
            embed=success_embed("Verification panel and settings deleted."),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(VerifyView())


async def setup(bot: commands.Bot):
    await bot.add_cog(Verify(bot))

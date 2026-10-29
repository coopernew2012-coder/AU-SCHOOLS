"""Persistent AU Schools member dashboard."""
import discord
from discord import app_commands
from discord.ext import commands
from database import get_setting
from utils import base_embed
from cogs.staff import get_rules, DEFAULT_SERVERINFO_TEMPLATE
from cogs.tickets import TicketPanelView, get_categories
from cogs.applications import ApplyPanelView, get_types

LOGO = "https://raw.githubusercontent.com/coopernew2012-coder/AU-SCHOOLS/main/assets/au-schools-logo.png"


def dashboard_embed():
    embed = base_embed(title="AU Schools | Community Dashboard", description=(
        "Welcome to **AU Schools**. Your community, support and applications in one place.\n\n"
        "Use the menu below to read our guidelines, explore the community, "
        "contact support or apply for a team position.\n\n"
        "**Private help • Clear guidelines • Opportunities to get involved**"
    ))
    embed.set_thumbnail(url=LOGO)
    return embed


class LegacyDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(placeholder="View the Dashboard", custom_id="au_schools_dashboard_v1", options=[
        discord.SelectOption(label="Guidelines", value="rules", emoji="📕", description="Read our rules and community standards"),
        discord.SelectOption(label="Server Information", value="info", emoji="📌", description="Community details and live Discord statistics"),
        discord.SelectOption(label="Community", value="community", emoji="💬", description="Find your place in AU Schools"),
        discord.SelectOption(label="Help", value="help", emoji="🎫", description="Get support, report an issue or appeal"),
        discord.SelectOption(label="Apply", value="apply", emoji="📋", description="Apply for a position on our team"),
    ])
    async def navigate(self, interaction, select):
        await interaction.response.defer(ephemeral=True)
        choice = select.values[0]
        view = None
        if choice == "rules":
            sections = await get_rules(interaction.guild.id)
            embed = base_embed(title="AU Schools | Guidelines")
            for key, title in [("discord", "Discord Rules"), ("general", "General Rules"), ("smp", "Community Conduct")]:
                embed.add_field(name=title, value=sections[key][:1024], inline=False)
        elif choice == "info":
            template = await get_setting(interaction.guild.id, "community_info_text") or DEFAULT_SERVERINFO_TEMPLATE
            invite = await get_setting(interaction.guild.id, "discord_invite") or "Not configured"
            embed = base_embed(title="AU Schools | Server Information", description=template.replace("{invite}", invite)[:4000])
            embed.add_field(name="Members", value=str(interaction.guild.member_count or 0))
            embed.add_field(name="Boosts", value=str(interaction.guild.premium_subscription_count or 0))
        elif choice == "community":
            embed = base_embed(title="AU Schools | Community", description=(
                "**Get involved**\nJoin the conversation in our community channels and treat everyone with respect.\n\n"
                "**Need a hand?**\nChoose Help & Support to open a private ticket.\n\n"
                "**Want to contribute?**\nChoose Applications to explore available team positions."
            ))
        elif choice == "help":
            embed = base_embed(title="AU Schools | Help & Support", description="Choose the topic that best matches your request. You will answer a short form before your private ticket opens. Please open one ticket per issue.")
            view = TicketPanelView(await get_categories(interaction.guild.id))
        else:
            types = await get_types(interaction.guild.id)
            if not types:
                return await interaction.followup.send("Applications are not currently available. Please contact staff.", ephemeral=True)
            embed = base_embed(title="AU Schools | Applications", description="Select a team below. We will guide you through the questions in DMs. Your answers are reviewed by the application team; please take your time and answer honestly.")
            view = ApplyPanelView()
            view.app_select.options = [discord.SelectOption(label=t['app_name']+' Application', value=t['app_name'], emoji=t['emoji'] or '📋') for t in types[:25]]
        embed.set_thumbnail(url=LOGO)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class DashboardView(discord.ui.LayoutView):
    """Single card with heading, introduction, dropdown and brand strip."""
    def __init__(self):
        super().__init__(timeout=None)
        navigation = LegacyDashboardView()
        self.navigate = navigation.navigate
        navigation.remove_item(self.navigate)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## AU Schools Dashboard\n"
                "Welcome to **AU Schools** — a community built around respect, connection "
                "and helpful staff support.\n\n"
                "Use the menu below to view our **guidelines**, **server information**, "
                "**community features**, **help options**, and **team applications**."
            ),
            discord.ui.ActionRow(self.navigate),
            discord.ui.MediaGallery(discord.MediaGalleryItem(
                "https://raw.githubusercontent.com/coopernew2012-coder/AU-SCHOOLS/main/assets/dashboard-banner.png",
                description="AU Schools — your community",
            )),
            accent_colour=0x155E32,
        ))


class Dashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(DashboardView())

    @app_commands.command(name="dashboard", description="Open the AU Schools community dashboard or publish it to a channel.")
    @app_commands.guild_only()
    @app_commands.describe(channel="Staff: choose a channel to publish the dashboard. Leave empty to open it privately.")
    async def dashboard(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if channel:
            if not interaction.user.guild_permissions.manage_guild:
                return await interaction.response.send_message("Manage Server permission is required to publish a dashboard.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            try:
                message = await channel.send(view=DashboardView())
            except discord.Forbidden:
                return await interaction.followup.send("I need View Channel, Send Messages and Embed Links in that channel.", ephemeral=True)
            await interaction.followup.send(f"Dashboard published: {message.jump_url}", ephemeral=True)
        else:
            await interaction.response.send_message(view=DashboardView(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Dashboard(bot))

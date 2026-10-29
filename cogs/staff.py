import time

import discord
from discord import app_commands
from discord.ext import commands

from config import DISCORD_INVITE
from database import get_db, get_setting, set_setting
from utils import base_embed, success_embed, error_embed

# ------------------------------------------------------------------
# Editable content defaults — per-guild overrides stored in settings:
#   rules_discord / rules_general / rules_smp
#   blacklist_clients / blacklist_automation / blacklist_vision / blacklist_xray
#   community_info_text  (supports {ip} and {invite} placeholders)
#   server_ip / discord_invite (via /admin-config set-ip / set-invite)
# ------------------------------------------------------------------

DEFAULT_RULES = {
    "discord": "- Be respectful.\n- No harassment, hate speech or NSFW content.\n- No spam, scams or malicious links.\n- Use the correct channels.",
    "general": "- Follow AU Schools rules and staff instructions.\n- Keep support requests in tickets.\n- Do not impersonate members or staff.\n- Do not share personal information without consent.",
    "smp": "- Report concerns to staff.\n- Do not exploit bugs or evade moderation.\n- Keep discussions appropriate and respectful.\n- Do not advertise without permission.",
}

DEFAULT_BLACKLIST = {
    "clients": (
        "- Sigma, Vape, Rise, Raven, Flux, Exhibition, LiquidBounce, Jigsaw\n"
        "- FDP Client, Meteor, Inertia, Aristois, Ares, BleachHack, ZeroDay\n"
        "- Kami Blue, Impact\n"
        "- And anything similar not listed here"
    ),
    "automation": (
        "- Baritone, autoclickers, AutoTotem, AutoCrystal, AutoArmor, AutoGap\n"
        "- AutoPearl, AutoFish, AutoMine, AutoTrap, combat macros, keybind macros\n"
        "- Auto-text macros are allowed!\n"
        "- KillAura, TriggerBot, aim assist, reach, velocity/anti-knockback, criticals\n"
        "- FastBow, BowAimbot, AutoPot, AutoSoup, hitbox expanders, ping spoofing\n"
        "- Anything giving an unfair combat advantage is disallowed"
    ),
    "vision": (
        "- ChestESP, player ESP, entity ESP, tracers, nametag expanders, Xaeros minimap\n"
        "- Anything showing players/entities through walls\n"
        "- Scaffold, fly, speed hacks, no-fall, Jesus, step, autowalk, blink, phase\n"
        "- Timer or game-speed modification\n"
        "- Anything altering default/vanilla movement"
    ),
    "xray": (
        "- X-Ray clients or texture packs, freecam, base-finding tools\n"
        "- World downloaders or similar map-copying tools\n"
        "- Mods that reveal hidden POIs, structures, or ores unfairly are not allowed\n\n"
        "**Commonly Questioned Mods**\n"
        "- Health indicators — allowed\n"
        "- Xaeros Minimap — not allowed\n"
        "- X-Ray for ore finding — not allowed\n"
        "- Armor durability indicator — not allowed"
    ),
}

DEFAULT_SERVERINFO_TEMPLATE = (
    "Welcome to **AU Schools**!\n\n"
    "Read our rules, open a support ticket if you need help, "
    "or use the applications panel to apply for a team position.\n\n"
    "**Discord:** {invite}"
)

RULE_TITLES = {
    "discord": "📜 {name} — Discord Rules",
    "general": "🌐 {name} — General Rules",
    "smp": "⚔️ {name} — Community Conduct",
}

BLACKLIST_TITLES = {
    "clients": "💀 {name} — Disallowed Clients",
    "automation": "🤖 {name} — Automation & Combat",
    "vision": "👁️ {name} — Vision & Movement",
    "xray": "🗺️ {name} — X-Ray, Mapping & FAQ",
}


async def get_rules(guild_id: int) -> dict[str, str]:
    """Rules sections, falling back to the built-in defaults."""
    out = {}
    for section, default in DEFAULT_RULES.items():
        val = await get_setting(guild_id, f"rules_{section}")
        out[section] = val if val else default
    return out


async def get_blacklist(guild_id: int) -> dict[str, str]:
    """Blacklist sections, falling back to the built-in defaults."""
    out = {}
    for section, default in DEFAULT_BLACKLIST.items():
        val = await get_setting(guild_id, f"blacklist_{section}")
        out[section] = val if val else default
    return out


async def is_ticket_staff(interaction: discord.Interaction) -> bool:
    """Staff = Manage Guild permission or a configured ticket staff role."""
    if isinstance(interaction.user, discord.Member):
        if interaction.user.guild_permissions.manage_guild:
            return True
        staff_roles_str = await get_setting(interaction.guild.id, "ticket_staff_roles")
        if staff_roles_str:
            ids = {r.strip() for r in staff_roles_str.split(",") if r.strip()}
            if ids & {str(r.id) for r in interaction.user.roles}:
                return True
    return False


class StaffTools(commands.Cog):
    """Staff utilities: ticket views, user notes, and info cards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # =================================================================
    # /staff — ticket views
    # =================================================================

    staff_group = app_commands.Group(
        name="staff",
        description="Staff tools: ticket views and user notes.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @staff_group.command(name="tickets", description="View a user's tickets (open and closed).")
    @app_commands.describe(user="User whose tickets to view (defaults to all users).")
    async def staff_tickets(self, interaction: discord.Interaction, user: discord.User | None = None):
        if not await is_ticket_staff(interaction):
            await interaction.response.send_message(
                embed=error_embed("Staff only."), ephemeral=True
            )
            return

        db = await get_db()
        try:
            if user:
                async with db.execute(
                    "SELECT channel_id AS thread_id, creator_id AS user_id, category, status, claimed_by FROM ticket_channels "
                    "WHERE guild_id=? AND user_id=? ORDER BY opened_at DESC LIMIT 25",
                    (str(interaction.guild.id), str(user.id)),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    "SELECT channel_id AS thread_id, creator_id AS user_id, category, status, claimed_by FROM ticket_channels "
                    "WHERE guild_id=? ORDER BY opened_at DESC LIMIT 25",
                    (str(interaction.guild.id),),
                ) as cursor:
                    rows = await cursor.fetchall()
        finally:
            await db.close()

        if not rows:
            label = f" for {user.display_name}" if user else ""
            await interaction.response.send_message(
                embed=error_embed(f"No tickets on record{label}."), ephemeral=True
            )
            return

        title = (
            f"🎫 Tickets — {user.display_name}" if user else "🎫 All Tickets (latest 25)"
        )
        embed = base_embed(title=title)
        open_count = sum(1 for r in rows if r["status"] == "open")
        embed.description = f"**{open_count}** open / **{len(rows) - open_count}** closed (of {len(rows)} shown)"
        for r in rows:
            opener = interaction.guild.get_member(int(r["user_id"]))
            opener_name = opener.display_name if opener else f"ID {r['user_id']}"
            claimed = f" | 🖐️ <@{r['claimed_by']}>" if r["claimed_by"] else ""
            status_icon = "🟢" if r["status"] == "open" else "🔒"
            embed.add_field(
                name=f"{status_icon} {r['category'] or 'Ticket'} — {opener_name}",
                value=f"<#{r['thread_id']}>{claimed}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staff_group.command(name="all-tickets", description="View all tickets server-wide (open and closed).")
    async def staff_all_tickets(self, interaction: discord.Interaction):
        if not await is_ticket_staff(interaction):
            await interaction.response.send_message(
                embed=error_embed("Staff only."), ephemeral=True
            )
            return

        db = await get_db()
        try:
            async with db.execute(
                "SELECT COUNT(*) AS total FROM ticket_channels WHERE guild_id=?",
                (str(interaction.guild.id),),
            ) as cursor:
                total = (await cursor.fetchone())["total"]
            async with db.execute(
                "SELECT channel_id AS thread_id, creator_id AS user_id, category, status, claimed_by FROM ticket_channels "
                "WHERE guild_id=? ORDER BY opened_at DESC LIMIT 25",
                (str(interaction.guild.id),),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await db.close()

        embed = base_embed(title=f"🎫 All Tickets ({total} total, latest 25 shown)")
        if not rows:
            embed.description = "No tickets on record."
        else:
            open_count = sum(1 for r in rows if r["status"] == "open")
            embed.description = f"**{open_count}** open / **{len(rows) - open_count}** closed in latest 25"
            for r in rows:
                opener = interaction.guild.get_member(int(r["user_id"]))
                opener_name = opener.display_name if opener else f"ID {r['user_id']}"
                claimed = f" | 🖐️ <@{r['claimed_by']}>" if r["claimed_by"] else ""
                status_icon = "🟢" if r["status"] == "open" else "🔒"
                embed.add_field(
                    name=f"{status_icon} {r['category'] or 'Ticket'} — {opener_name}",
                    value=f"<#{r['thread_id']}>{claimed}",
                    inline=False,
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =================================================================
    # /note — staff notes on users
    # =================================================================

    # =================================================================
    # Public info cards
    # =================================================================



    @app_commands.command(name="rules", description="Post the server rules.")
    async def rules(self, interaction: discord.Interaction):
        """Post the Discord / General / SMP rules embeds (editable via /edit-rules)."""
        name = "AU Schools"
        sections = await get_rules(interaction.guild.id)
        embeds = []
        for section in ("discord", "general", "smp"):
            e = base_embed(title=RULE_TITLES[section].format(name=name))
            e.description = sections[section]
            embeds.append(e)
        await interaction.response.send_message(embeds=embeds)

    @app_commands.command(name="blacklistedaddons", description="Post the list of disallowed mods/addons.")
    async def blacklistedaddons(self, interaction: discord.Interaction):
        """Post the blacklisted clients / mods embeds (editable via /edit-blacklistedaddons)."""
        name = "AU Schools"
        sections = await get_blacklist(interaction.guild.id)
        embeds = []
        for section in ("clients", "automation", "vision", "xray"):
            e = base_embed(title=BLACKLIST_TITLES[section].format(name=name))
            e.description = sections[section]
            embeds.append(e)
        await interaction.response.send_message(embeds=embeds)

    # =================================================================
    # Editable content — modals that override the defaults per guild
    # =================================================================

    @app_commands.command(name="edit-rules", description="Edit the /rules embeds for this server.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def edit_rules(self, interaction: discord.Interaction):
        """Opens a modal prefilled with the current rules for editing."""
        current = await get_rules(interaction.guild.id)
        await interaction.response.send_modal(EditRulesModal(current))

    @app_commands.command(name="edit-blacklistedaddons", description="Edit the /blacklistedaddons embeds for this server.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def edit_blacklistedaddons(self, interaction: discord.Interaction):
        """Opens a modal prefilled with the current blacklist sections."""
        current = await get_blacklist(interaction.guild.id)
        await interaction.response.send_modal(EditBlacklistModal(current))

    @app_commands.command(name="edit-serverinfo", description="Edit the /server-info ad text for this server.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def edit_serverinfo(self, interaction: discord.Interaction):
        """Opens a modal prefilled with the current server-info ad text."""
        current = await get_setting(interaction.guild.id, "community_info_text") or DEFAULT_SERVERINFO_TEMPLATE
        await interaction.response.send_modal(EditServerInfoModal(current))


class EditRulesModal(discord.ui.Modal, title="Edit Rules (leave a box empty to keep it)"):
    def __init__(self, current: dict[str, str]):
        super().__init__(timeout=1200)
        self.section_inputs = {}
        labels = {"discord": "Discord rules", "general": "General rules", "smp": "Community conduct"}
        for section in ("discord", "general", "smp"):
            component = discord.ui.TextInput(
                label=labels[section],
                style=discord.TextStyle.paragraph,
                default=current[section][:4000],
                max_length=4000,
                required=False,
            )
            self.add_item(component)
            self.section_inputs[section] = component

    async def on_submit(self, interaction: discord.Interaction):
        saved = []
        for section, component in self.section_inputs.items():
            if component.value.strip():
                await set_setting(interaction.guild.id, f"rules_{section}", component.value.strip())
                saved.append(section)
        await interaction.response.send_message(
            embed=success_embed(f"Rules updated ({', '.join(saved) if saved else 'nothing changed'}). Use /rules to see them."),
            ephemeral=True,
        )


class EditBlacklistModal(discord.ui.Modal, title="Edit Blacklisted Addons (empty = keep)"):
    def __init__(self, current: dict[str, str]):
        super().__init__(timeout=1200)
        self.section_inputs = {}
        labels = {
            "clients": "Disallowed clients",
            "automation": "Automation & combat",
            "vision": "Vision & movement",
            "xray": "X-Ray, mapping & FAQ",
        }
        for section in ("clients", "automation", "vision", "xray"):
            component = discord.ui.TextInput(
                label=labels[section],
                style=discord.TextStyle.paragraph,
                default=current[section][:4000],
                max_length=4000,
                required=False,
            )
            self.add_item(component)
            self.section_inputs[section] = component

    async def on_submit(self, interaction: discord.Interaction):
        saved = []
        for section, component in self.section_inputs.items():
            if component.value.strip():
                await set_setting(interaction.guild.id, f"blacklist_{section}", component.value.strip())
                saved.append(section)
        await interaction.response.send_message(
            embed=success_embed(f"Blacklist updated ({', '.join(saved) if saved else 'nothing changed'}). Use /blacklistedaddons to see it."),
            ephemeral=True,
        )


class EditServerInfoModal(discord.ui.Modal, title="Edit /server-info Ad Text"):
    ad_text = discord.ui.TextInput(
        label="Community information ({invite} supported)",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )

    def __init__(self, current: str):
        super().__init__(timeout=1200)
        self.ad_text.default = current[:4000]

    async def on_submit(self, interaction: discord.Interaction):
        await set_setting(interaction.guild.id, "community_info_text", self.ad_text.value.strip())
        await interaction.response.send_message(
            embed=success_embed("Server-info ad updated. Use /server-info to see it."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffTools(bot))

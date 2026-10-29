import discord
from discord import app_commands
from discord.ext import commands
from database import get_setting, set_setting, delete_setting, get_db
from utils import base_embed, success_embed, error_embed


class AdminConfig(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    admin_group = app_commands.Group(
        name="admin-config",
        description="Configure bot settings for this server.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @admin_group.command(name="set-log-channel", description="Set the channel where moderation and audit logs are sent.")
    @app_commands.describe(channel="Channel for moderation and action logs.")
    @app_commands.default_permissions(administrator=True)
    async def set_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await set_setting(interaction.guild.id, "mod_log_channel", str(channel.id))
        await interaction.response.send_message(
            embed=success_embed(f"Moderation log channel set to {channel.mention}."),
            ephemeral=True,
        )

    @admin_group.command(name="clear-log-channel", description="Clear the configured moderation log channel.")
    @app_commands.default_permissions(administrator=True)
    async def clear_log_channel(self, interaction: discord.Interaction):
        await delete_setting(interaction.guild.id, "mod_log_channel")
        await interaction.response.send_message(
            embed=success_embed("Moderation log channel cleared."), ephemeral=True
        )

    @admin_group.command(name="ticket-panel-embed", description="Customize the ticket panel embed (title, description, image, thumbnail).")
    @app_commands.describe(
        title="Custom embed title for ticket panels.",
        description="Custom embed description for ticket panels.",
        image_url="Optional image URL for the embed.",
        thumbnail_url="Optional thumbnail URL for the embed.",
    )
    @app_commands.default_permissions(administrator=True)
    async def ticket_panel_embed(
        self,
        interaction: discord.Interaction,
        title: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
    ):
        panel_settings = {}
        if title:
            panel_settings["embed_title"] = title
        if description:
            panel_settings["embed_desc"] = description
        if image_url:
            panel_settings["embed_image"] = image_url
        if thumbnail_url:
            panel_settings["embed_thumbnail"] = thumbnail_url

        if not panel_settings:
            await interaction.response.send_message(
                embed=error_embed("Provide at least one option to update."),
                ephemeral=True,
            )
            return

        db = await get_db()
        try:
            for key, val in panel_settings.items():
                await db.execute(
                    "UPDATE ticket_panels SET {} = ? WHERE guild_id = ?".format(key),
                    (val, str(interaction.guild.id)),
                )
            await db.commit()
        finally:
            await db.close()

        await interaction.response.send_message(
            embed=success_embed(
                f"Updated ticket panel settings: {', '.join(panel_settings.keys())}"
            ),
            ephemeral=True,
        )

    @admin_group.command(name="app-submission-embed", description="Customize the application submission embed (title, image, thumbnail).")
    @app_commands.describe(
        app_name="Application name to customize.",
        title="Custom submission embed title.",
        image_url="Optional image URL.",
        thumbnail_url="Optional thumbnail URL.",
    )
    @app_commands.default_permissions(administrator=True)
    async def app_submission_embed(
        self,
        interaction: discord.Interaction,
        app_name: str,
        title: str | None = None,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
    ):
        updates = {}
        if title:
            updates["submission_title"] = title
        if image_url:
            updates["submission_image"] = image_url
        if thumbnail_url:
            updates["submission_thumbnail"] = thumbnail_url

        if not updates:
            await interaction.response.send_message(
                embed=error_embed("Provide at least one option to update."),
                ephemeral=True,
            )
            return

        db = await get_db()
        try:
            for key, val in updates.items():
                await db.execute(
                    f"UPDATE application_templates SET {key}=? WHERE guild_id=? AND app_name=?",
                    (val, str(interaction.guild.id), app_name.lower()),
                )
            await db.commit()
        finally:
            await db.close()

        await interaction.response.send_message(
            embed=success_embed(f"Updated submission embeds for **{app_name}**."),
            ephemeral=True,
        )

    @admin_group.command(name="preview-ticket-panel", description="Preview how the current ticket panel embed will look.")
    @app_commands.default_permissions(administrator=True)
    async def preview_ticket_panel(self, interaction: discord.Interaction):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT embed_title, embed_desc, embed_image, embed_thumbnail FROM ticket_panels WHERE guild_id=? LIMIT 1",
                (str(interaction.guild.id),),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await db.close()

        title = row["embed_title"] if row else "Open a Ticket"
        desc = row["embed_desc"] if row else "Select a category below to open a support ticket."
        embed = base_embed(title=title, description=desc)
        if row and row["embed_image"]:
            embed.set_image(url=row["embed_image"])
        if row and row["embed_thumbnail"]:
            embed.set_thumbnail(url=row["embed_thumbnail"])

        await interaction.response.send_message(embed=embed, ephemeral=True)





    @admin_group.command(name="set-invite", description="Set the Discord invite shown by /server-info.")
    @app_commands.describe(invite="The Discord invite link (e.g. discord.gg/xxxx).")
    @app_commands.default_permissions(administrator=True)
    async def set_invite(self, interaction: discord.Interaction, invite: str):
        await set_setting(interaction.guild.id, "discord_invite", invite.strip())
        await interaction.response.send_message(
            embed=success_embed(f"Discord invite set to {invite.strip()}"), ephemeral=True
        )


    @admin_group.command(name="show-settings", description="Display all current bot configuration for this server.")
    @app_commands.default_permissions(administrator=True)
    async def show_settings(self, interaction: discord.Interaction):
        keys = [
            "mod_log_channel", "ticket_category", "ticket_staff_role", "ticket_admin_role",
            "ticket_log_channel", "ticket_autoclose_minutes", "autorole_enabled",
            "softban_channel", "discord_invite", "apply_reviewer_role",
        ]
        lines = []
        for key in keys:
            val = await get_setting(interaction.guild.id, key)
            label = val
            if key.endswith("_channel") and val and val.isdigit():
                chan = interaction.guild.get_channel(int(val))
                label = chan.mention if chan else val
            elif key == "ticket_category" and val and val.isdigit():
                cat = interaction.guild.get_channel(int(val))
                label = f"**{cat.name}**" if cat else val
            elif key == "server_ip" and val:
                label = f"`{val}`"
            lines.append(f"**{key}**: {label or 'Not set'}")

        automod_cfg = await get_setting(interaction.guild.id, "automod_config")
        if automod_cfg:
            try:
                import json
                cfg = json.loads(automod_cfg)
                lines.append(f"**automod**: {'Enabled' if cfg.get('enabled') else 'Disabled'}")
            except Exception:
                pass

        welcome_db = await get_db()
        try:
            async with welcome_db.execute(
                "SELECT * FROM welcome_config WHERE guild_id=?",
                (str(interaction.guild.id),),
            ) as cursor:
                wc = await cursor.fetchone()
            if wc:
                lines.append(f"**Welcome**: {'ON' if wc['enabled'] else 'OFF'} (channel: <#{wc['channel_id']}>)")
                lines.append(f"**Goodbye**: {'ON' if wc['goodbye_enabled'] else 'OFF'} (channel: <#{wc['goodbye_channel_id']}>)")
            else:
                lines.append("**Welcome/Goodbye**: Not configured")
        finally:
            await welcome_db.close()

        embed = base_embed(title="Server Configuration")
        embed.description = "\n".join(lines)[:4000]
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminConfig(bot))

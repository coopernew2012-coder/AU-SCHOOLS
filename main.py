import asyncio
import hashlib
from pathlib import Path
import os
import time
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from database import init_db, get_setting, set_setting
from config import DISCORD_TOKEN, ACCENT_COLOR, BOT_USERNAME, MAIN_GUILD_ID
from utils import error_embed
from web_server import start_web_server

# ------------------------------------------------------------------
# Command access control
# ------------------------------------------------------------------
# Discord role required to run any slash command except PUBLIC_COMMANDS.
# Override with ACCESS_ROLE_ID in the environment if it ever changes.
ACCESS_ROLE_ID = int(os.getenv("ACCESS_ROLE_ID", "0") or 0)
# Root command names anyone can use without the role. Everything else
# (config, editing, moderation, ... — including the whole /apply setup
# group) is staff-only. /link stays public so players can claim their
# in-game linking code.
PUBLIC_COMMANDS = {"dashboard"}


class AccessDenied(app_commands.CheckFailure):
    """Raised by the global gate after the user was told why."""


class AUSchoolsBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.moderation = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )
        self._log_cache: dict[int, str | None] = {}
        self._last_presence = 0.0
        self._guilds_synced = False

    async def setup_hook(self):
        await init_db()
        from branding import migrate_branding
        await migrate_branding()
        self.tree.on_error = self._on_app_command_error
        self.tree.interaction_check = self._access_check

        cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
        enabled_cogs = {"dashboard", "admin_config", "applications", "autorole", "moderation",
                        "roles", "softban", "staff", "tickets", "utility",
                        "verify", "warnings", "welcome"}
        for filename in sorted(os.listdir(cogs_dir)):
            if filename[:-3] not in enabled_cogs:
                continue
            if filename.endswith(".py") and not filename.startswith("_"):
                ext = f"cogs.{filename[:-3]}"
                try:
                    await self.load_extension(ext)
                    print(f"Loaded: {ext}")
                except Exception as e:
                    print(f"Failed to load {ext}: {e}")

        # Start cog-owned background tasks (giveaway expiry, poll tallying,
        # level role sync, player count, softmute expiry, ...).
        # Cogs start their own tasks in __init__; nothing extra needed here.
        # Note: tasks started before login reconnect automatically once ready.

        # Global sync keeps things eventually-consistent; per-guild sync in
        # on_ready makes commands appear instantly.
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands.")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    async def _access_check(self, interaction: discord.Interaction) -> bool:
        """Global slash-command gate.

        Everyone may use PUBLIC_COMMANDS (/whitelist). Everything else
        requires ACCESS_ROLE_ID — server administrators bypass as a
        safety net so the owner can never lock themselves out.
        Button/select/modal interactions are not affected by this gate.
        """
        command = interaction.command
        root = command.qualified_name.split(" ")[0] if command else ""
        if root in PUBLIC_COMMANDS:
            return True

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        allowed = member is not None and (
            member.guild_permissions.administrator
            or member.get_role(ACCESS_ROLE_ID) is not None
        )
        if allowed:
            return True

        embed = error_embed(
            (f"You need the <@&{ACCESS_ROLE_ID}> role to use this command."
             if ACCESS_ROLE_ID else "AU Schools staff access has not been configured yet. Only administrators can use this command.")
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass
        raise AccessDenied(
            f"{interaction.user} tried /{root} without the access role."
        )

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, AccessDenied):
            # Already answered with an ephemeral denial message.
            return
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original
        print(f"Slash command error in /{interaction.command.qualified_name if interaction.command else '?'}: {error!r}")
        try:
            embed = error_embed("Something went wrong running that command. The error has been logged.")
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        self._log_cache.clear()
        await self._sync_branding()
        await self._update_presence()

    async def _sync_branding(self):
        """Keep the bot's Discord username matching the configured brand.
        Avatar/about-me/banner can only be changed in the Developer Portal."""
        try:
            if self.user and self.user.name != BOT_USERNAME:
                await self.user.edit(username=BOT_USERNAME)
                print(f"Renamed bot to {BOT_USERNAME!r}.")
        except Exception as e:
            # Rate limits (2/h) or missing permission — not fatal.
            print(f"Could not rename bot: {e}")

        logo_path = Path(__file__).parent / "assets" / "au-schools-logo.png"
        if self.user and logo_path.is_file():
            logo = logo_path.read_bytes()
            digest = hashlib.sha256(logo).hexdigest()
            if await get_setting(0, "brand_avatar_sha256") != digest:
                try:
                    await self.user.edit(avatar=logo)
                    await set_setting(0, "brand_avatar_sha256", digest)
                except discord.HTTPException as error:
                    print(f"Could not update AU Schools avatar: {error}")

        if not self._guilds_synced:
            self._guilds_synced = True
            for guild in self.guilds:
                try:
                    await self.tree.sync(guild=guild)
                    print(f"Synced commands to {guild.name} ({guild.id}).")
                except Exception as e:
                    print(f"Failed to sync to {guild.name}: {e}")

    def _stats_guild(self) -> discord.Guild | None:
        """The guild whose member/boost counts the presence shows.

        Prefers MAIN_GUILD_ID; falls back to the largest connected guild.
        member_count can be None with a partial cache, so fall back to
        approximate_member_count / 0 instead of crashing.
        """
        guild = self.get_guild(MAIN_GUILD_ID) if MAIN_GUILD_ID else None
        if guild is None and self.guilds:
            guild = max(self.guilds, key=lambda g: g.member_count or 0)
        return guild

    async def _update_presence(self):
        now = time.monotonic()
        if now - self._last_presence < 30:
            return
        self._last_presence = now
        guild = self._stats_guild()
        if guild is None:
            return
        total = guild.member_count or guild.approximate_member_count or 0
        boosts = guild.premium_subscription_count or 0
        name = f"{total:,} members"
        if boosts:
            name += f" | {boosts:,} boosts"
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=name,
            )
        )

    async def on_member_join(self, member: discord.Member):
        await self._update_presence()

    async def on_member_remove(self, member: discord.Member):
        await self._update_presence()

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since != after.premium_since:
            self._last_presence = 0.0
            await self._update_presence()

    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        # Boost tier / counts changed server-wide
        if before.premium_subscription_count != after.premium_subscription_count:
            self._last_presence = 0.0
            await self._update_presence()

    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command | app_commands.ContextMenu):
        if not interaction.guild:
            return

        gid = interaction.guild.id
        if gid not in self._log_cache:
            self._log_cache[gid] = await get_setting(gid, "mod_log_channel")

        channel_id = self._log_cache[gid]
        if not channel_id:
            return

        channel = interaction.guild.get_channel(int(channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            self._log_cache[gid] = None
            return

        channel_mention = interaction.channel.mention if interaction.channel else "DM"
        embed = discord.Embed(
            description=f"**{interaction.user.mention}** used `/{command.qualified_name}` in {channel_mention}",
            color=ACCENT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass


async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set in environment variables.")

    try:
        bot = AUSchoolsBot()
        runner = await start_web_server(bot)
    except OSError as e:
        if getattr(e, "winerror", None) == 10048 or getattr(e, "errno", None) == 98:
            print("\nERROR: Port 8080 is already in use — another copy of the bot is")
            print("probably still running. Close it (Task Manager -> python.exe) and try again,")
            print("or set PORT=<other port> in .env.")
            return
        raise
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

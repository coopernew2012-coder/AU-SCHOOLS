import asyncio
from datetime import timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import get_db, get_setting, set_setting, delete_setting
from utils import base_embed, success_embed, error_embed, parse_duration, format_duration


async def log_action(
    bot: commands.Bot,
    guild: discord.Guild,
    action: str,
    moderator: discord.Member,
    target: discord.Member | discord.User,
    reason: str | None = None,
    duration: str | None = None,
):
    channel_id = await get_setting(guild.id, "mod_log_channel")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    embed = base_embed(title=f"Moderation: {action}")
    embed.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)")
    embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)")
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    if duration:
        embed.add_field(name="Duration", value=duration)
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.softmute_expiry.start()

    def cog_unload(self):
        self.softmute_expiry.cancel()

    purge_group = app_commands.Group(
        name="purge",
        description="Bulk message deletion.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_messages=True),
    )

    @purge_group.command(name="all", description="Delete messages from this channel until it's empty (safety-capped).")
    @app_commands.describe(limit="Safety cap on how many messages to delete (default 1000, max 10000).")
    @app_commands.default_permissions(manage_messages=True)
    async def purge_all(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 10000] = 1000,
    ):
        """Loop-purge a channel. Discord won't let bots delete messages older
        than 14 days in bulk, so 'all' means everything still deletable."""
        await interaction.response.defer(ephemeral=True)

        deleted_total = 0
        try:
            while deleted_total < limit:
                batch = min(100, limit - deleted_total)
                deleted = await interaction.channel.purge(limit=batch, bulk=True)
                deleted_total += len(deleted)
                if len(deleted) < batch:
                    break  # channel is out of deletable messages
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack **Manage Messages** permission in this channel."), ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed(f"Stopped after {deleted_total} messages: {e}"), ephemeral=True
            )
            return

        skipped_note = (
            "\n(Really old messages — 14+ days — can't be bulk-deleted by Discord; re-run with a higher cap or clone the channel for a true wipe.)"
            if deleted_total >= limit
            else ""
        )
        await interaction.followup.send(
            embed=success_embed(f"🧹 Deleted **{deleted_total}** messages from {interaction.channel.mention}.{skipped_note}"),
            ephemeral=True,
        )
        await log_action(
            self.bot,
            interaction.guild,
            "Purge All",
            interaction.user,
            interaction.user,
            reason=f"{deleted_total} messages deleted in {interaction.channel.mention}",
        )

    async def record_action(self, guild: discord.Guild, user_id: int, action: str, reason: str | None, mod_id: int):
        """Persist a moderation action for the dashboard and history."""
        from datetime import datetime, timezone
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO mod_actions (guild_id, user_id, action, reason, mod_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (str(guild.id), str(user_id), action, reason, str(mod_id),
                 datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        finally:
            await db.close()

    # ------------------------------------------------------------- dashboard

    mod_group = app_commands.Group(
        name="mod",
        description="Moderation dashboard and history.",
        guild_only=True,
        default_permissions=discord.Permissions(moderate_members=True),
    )

    @mod_group.command(name="dashboard", description="Overview of recent moderation actions in this server.")
    @app_commands.default_permissions(moderate_members=True)
    async def mod_dashboard(self, interaction: discord.Interaction):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT action, COUNT(*) AS c FROM mod_actions WHERE guild_id=? GROUP BY action ORDER BY c DESC",
                (str(interaction.guild.id),),
            ) as cursor:
                counts = await cursor.fetchall()
            async with db.execute(
                "SELECT user_id, action, reason, mod_id, timestamp FROM mod_actions WHERE guild_id=? ORDER BY timestamp DESC LIMIT 10",
                (str(interaction.guild.id),),
            ) as cursor:
                recent = await cursor.fetchall()
        finally:
            await db.close()

        embed = base_embed(title=f"🛡️ Moderation Dashboard — {interaction.guild.name}")
        if counts:
            embed.add_field(
                name="Actions (all time)",
                value="\n".join(f"**{r['action']}**: {r['c']}" for r in counts),
                inline=True,
            )
        else:
            embed.add_field(name="Actions (all time)", value="None recorded", inline=True)

        if recent:
            lines = []
            for r in recent:
                target = f"<@{r['user_id']}>"
                mod = f"<@{r['mod_id']}>"
                ts = f"<t:{int(discord.utils.parse_time(r['timestamp']).timestamp())}:R>" if r['timestamp'] else ""
                lines.append(f"{ts} {mod} → {target}: **{r['action']}**" + (f" — {r['reason']}" if r['reason'] else ""))
            embed.add_field(name="Recent Actions", value="\n".join(lines)[:1024], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @mod_group.command(name="history", description="View a user's moderation history.")
    @app_commands.describe(user="User whose history to view.")
    @app_commands.default_permissions(moderate_members=True)
    async def mod_history(self, interaction: discord.Interaction, user: discord.User):
        db = await get_db()
        try:
            async with db.execute(
                "SELECT action, reason, mod_id, timestamp FROM mod_actions WHERE guild_id=? AND user_id=? ORDER BY timestamp DESC LIMIT 25",
                (str(interaction.guild.id), str(user.id)),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await db.close()

        if not rows:
            await interaction.response.send_message(
                embed=success_embed(f"{user.mention} has a clean record in this server."), ephemeral=True
            )
            return

        embed = base_embed(title=f"📋 Mod History — {user.display_name}")
        embed.set_thumbnail(url=user.display_avatar.url)
        lines = []
        for r in rows:
            ts = f"<t:{int(discord.utils.parse_time(r['timestamp']).timestamp())}:R>" if r['timestamp'] else ""
            lines.append(
                f"{ts} **{r['action']}** by <@{r['mod_id']}>" + (f" — {r['reason']}" if r['reason'] else "")
            )
        embed.description = "\n".join(lines)[:4000]
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- softmute

    @app_commands.command(name="softmute", description="Remove a member's Send Messages permission (they can still read).")
    @app_commands.describe(
        member="Member to softmute.",
        duration="Duration (e.g., 30m, 2h). Omit for indefinite.",
        reason="Reason for the softmute.",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def softmute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str | None = None,
        reason: str | None = None,
    ):
        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                embed=error_embed("You cannot moderate this user."), ephemeral=True
            )
            return

        until_ts = None
        dur_str = "indefinitely"
        if duration:
            delta = parse_duration(duration)
            if delta is None:
                await interaction.response.send_message(
                    embed=error_embed("Invalid duration format. Use e.g. `30m`, `2h`, `1d`."), ephemeral=True
                )
                return
            until_ts = discord.utils.utcnow() + delta
            dur_str = format_duration(delta)

        await interaction.response.defer(ephemeral=True)

        overwrite = discord.PermissionOverwrite(send_messages=False, speak=False, add_reactions=False)
        applied = 0
        failed_channels = 0
        for channel in interaction.guild.channels:
            if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)):
                continue
            try:
                await channel.set_permissions(member, overwrite=overwrite, reason=f"Softmute by {interaction.user}")
                applied += 1
            except discord.Forbidden:
                failed_channels += 1

        db = await get_db()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO softmutes (guild_id, user_id, until) VALUES (?, ?, ?)",
                (str(interaction.guild.id), str(member.id), until_ts.isoformat() if until_ts else None),
            )
            await db.commit()
        finally:
            await db.close()

        await interaction.followup.send(
            embed=success_embed(
                f"{member.mention} softmuted for {dur_str} ({applied} channels"
                + (f", {failed_channels} failed" if failed_channels else "") + ")."
            ),
            ephemeral=True,
        )
        await self.record_action(interaction.guild, member.id, "Softmute", reason, interaction.user.id)
        await log_action(self.bot, interaction.guild, "Softmute", interaction.user, member, reason, dur_str)

    @app_commands.command(name="unsoftmute", description="Restore a softmuted member's send permissions.")
    @app_commands.describe(member="Member to unmute.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def unsoftmute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True)

        restored = 0
        for channel in interaction.guild.channels:
            if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)):
                continue
            try:
                await channel.set_permissions(member, overwrite=None, reason=f"Unsoftmute by {interaction.user}")
                restored += 1
            except discord.Forbidden:
                pass

        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM softmutes WHERE guild_id=? AND user_id=?",
                (str(interaction.guild.id), str(member.id)),
            )
            await db.commit()
        finally:
            await db.close()

        await interaction.followup.send(
            embed=success_embed(f"{member.mention} can send messages again ({restored} channels restored)."),
            ephemeral=True,
        )
        await log_action(self.bot, interaction.guild, "Unsoftmute", interaction.user, member)

    @tasks.loop(minutes=5)
    async def softmute_expiry(self):
        """Background task: lift expired softmutes."""
        await self.bot.wait_until_ready()
        db = await get_db()
        try:
            async with db.execute("SELECT guild_id, user_id, until FROM softmutes WHERE until IS NOT NULL") as cursor:
                rows = await cursor.fetchall()
        finally:
            await db.close()

        now = discord.utils.utcnow()
        for row in rows:
            until = discord.utils.parse_time(row["until"])
            if until and until <= now:
                guild = self.bot.get_guild(int(row["guild_id"]))
                member = guild.get_member(int(row["user_id"])) if guild else None
                if guild and member:
                    for channel in guild.channels:
                        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)):
                            try:
                                await channel.set_permissions(member, overwrite=None, reason="Softmute expired")
                            except discord.Forbidden:
                                pass
                db2 = await get_db()
                try:
                    await db2.execute(
                        "DELETE FROM softmutes WHERE guild_id=? AND user_id=?",
                        (row["guild_id"], row["user_id"]),
                    )
                    await db2.commit()
                finally:
                    await db2.close()

    # ------------------------------------------------------------- mass actions

    @app_commands.command(name="massban", description="Ban multiple users at once by ID.")
    @app_commands.describe(
        user_ids="Space-separated user IDs to ban.",
        reason="Reason applied to all bans.",
        delete_days="Days of messages to delete (0-7).",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def massban(
        self,
        interaction: discord.Interaction,
        user_ids: str,
        reason: str | None = None,
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ):
        await interaction.response.defer(ephemeral=True)

        ids = [x.strip() for x in user_ids.replace(",", " ").split() if x.strip().isdigit()]
        if not ids:
            await interaction.followup.send(
                embed=error_embed("Provide at least one valid user ID."), ephemeral=True
            )
            return
        if len(ids) > 25:
            await interaction.followup.send(
                embed=error_embed("Maximum 25 users per massban."), ephemeral=True
            )
            return

        banned, failed = [], []
        for uid in ids[:25]:
            try:
                await interaction.guild.ban(
                    discord.Object(id=int(uid)),
                    reason=reason or "Massban",
                    delete_message_days=delete_days,
                )
                banned.append(uid)
                await self.record_action(interaction.guild, int(uid), "Massban", reason, interaction.user.id)
            except (discord.Forbidden, discord.HTTPException, ValueError):
                failed.append(uid)

        lines = [f"✅ Banned: {len(banned)}"]
        if failed:
            lines.append(f"❌ Failed: {len(failed)} (`{' '.join(failed[:10])}`)")
        await interaction.followup.send(embed=success_embed("\n".join(lines)), ephemeral=True)
        await log_action(
            self.bot, interaction.guild, "Massban", interaction.user,
            interaction.user, reason=f"{len(banned)} banned, {len(failed)} failed — {reason or 'No reason'}",
        )

    @app_commands.command(name="masskick", description="Kick multiple members at once.")
    @app_commands.describe(
        members="Space-separated member mentions or IDs to kick.",
        reason="Reason applied to all kicks.",
    )
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def masskick(
        self,
        interaction: discord.Interaction,
        members: str,
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        raw_ids = [x.strip() for x in members.replace(",", " ").replace("<@", "").replace("!", "").replace(">", "").split() if x.strip().isdigit()]
        targets = [interaction.guild.get_member(int(uid)) for uid in raw_ids]
        targets = [m for m in targets if m is not None]
        if not targets:
            await interaction.followup.send(
                embed=error_embed("No valid members found."), ephemeral=True
            )
            return

        kicked, failed = [], []
        for member in targets:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
                failed.append(str(member.id))
                continue
            try:
                await member.kick(reason=reason or "Masskick")
                kicked.append(str(member.id))
                await self.record_action(interaction.guild, member.id, "Masskick", reason, interaction.user.id)
            except (discord.Forbidden, discord.HTTPException):
                failed.append(str(member.id))

        lines = [f"✅ Kicked: {len(kicked)}"]
        if failed:
            lines.append(f"❌ Failed: {len(failed)}")
        await interaction.followup.send(embed=success_embed("\n".join(lines)), ephemeral=True)
        await log_action(
            self.bot, interaction.guild, "Masskick", interaction.user,
            interaction.user, reason=f"{len(kicked)} kicked, {len(failed)} failed — {reason or 'No reason'}",
        )

    @app_commands.command(name="mute", description="Timeout a member for a specified duration.")
    @app_commands.describe(
        member="Member to mute.",
        duration="Duration (e.g., 1h, 30m, 7d).",
        reason="Reason for the mute.",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def mute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: str | None = None,
    ):
        delta = parse_duration(duration)
        if delta is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Invalid duration format. Use e.g. `1h`, `30m`, `7d`, `2w`."
                ),
                ephemeral=True,
            )
            return

        until = discord.utils.utcnow() + delta
        await interaction.response.defer(ephemeral=True)

        try:
            await member.timeout(until, reason=reason)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack permissions to timeout that member."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed(f"Failed to mute: {e}"), ephemeral=True
            )
            return

        dur_str = format_duration(delta)
        await interaction.followup.send(
            embed=success_embed(f"{member.mention} has been muted for {dur_str}."),
            ephemeral=True,
        )
        await self.record_action(interaction.guild, member.id, "Mute", reason, interaction.user.id)
        await log_action(self.bot, interaction.guild, "Mute", interaction.user, member, reason, dur_str)

    @app_commands.command(name="unmute", description="Remove a timeout from a member.")
    @app_commands.describe(member="Member to unmute.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def unmute(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.timeout(None)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack permissions to remove the timeout."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed(f"Failed to unmute: {e}"), ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=success_embed(f"{member.mention} has been unmuted."),
            ephemeral=True,
        )
        await log_action(self.bot, interaction.guild, "Unmute", interaction.user, member)

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(member="Member to kick.", reason="Reason for the kick.")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack permissions to kick that member."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed(f"Failed to kick: {e}"), ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=success_embed(f"{member.mention} has been kicked."),
            ephemeral=True,
        )
        await self.record_action(interaction.guild, member.id, "Kick", reason, interaction.user.id)
        await log_action(self.bot, interaction.guild, "Kick", interaction.user, member, reason)

    @app_commands.command(name="ban", description="Ban a member from the server.")
    @app_commands.describe(member="Member to ban.", reason="Reason for the ban.")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.ban(reason=reason)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack permissions to ban that member."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed(f"Failed to ban: {e}"), ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=success_embed(f"{member.mention} has been banned."),
            ephemeral=True,
        )
        await self.record_action(interaction.guild, member.id, "Ban", reason, interaction.user.id)
        await log_action(self.bot, interaction.guild, "Ban", interaction.user, member, reason)

    # NOTE: /softban moved to cogs/softban.py as a full group (/softban user,
    # /softban set-channel, /softban panel) — do not re-register it here.

    @app_commands.command(name="clear", description="Bulk delete messages from the current channel (1-100).")
    @app_commands.describe(count="Number of messages to delete (1-100).")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def clear(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 100],
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=count)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack permissions to delete messages."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed(f"Failed to clear messages: {e}"), ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=success_embed(f"Cleared {len(deleted)} messages."),
            ephemeral=True,
        )
        await log_action(
            self.bot,
            interaction.guild,
            "Clear",
            interaction.user,
            interaction.user,
            reason=f"{len(deleted)} messages deleted in {interaction.channel.mention}",
        )

    @app_commands.command(name="unban", description="Unban a user by their Discord ID.")
    @app_commands.describe(user_id="The ID of the banned user.", reason="Reason for the unban.")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str = "No reason provided",
    ):
        """Unban a user by ID (banned users aren't in the member list)."""
        if not user_id.isdigit():
            await interaction.response.send_message(
                embed=error_embed("That doesn't look like a user ID."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
        except discord.NotFound:
            await interaction.followup.send(
                embed=error_embed("No Discord user with that ID exists."), ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=error_embed("Couldn't look up that user."), ephemeral=True
            )
            return

        try:
            await interaction.guild.unban(user, reason=f"Unban by {interaction.user}: {reason}")
        except discord.NotFound:
            await interaction.followup.send(
                embed=error_embed(f"{user.mention} is not banned."), ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I lack **Ban Members** permission."), ephemeral=True
            )
            return

        try:
            await user.send(f"✅ You have been unbanned from **{interaction.guild.name}**.")
        except discord.HTTPException:
            pass  # DMs disabled — not an error

        await interaction.followup.send(
            embed=success_embed(f"Unbanned {user.mention} (`{user.id}`)."), ephemeral=True
        )
        await log_action(self.bot, interaction.guild, "Unban", interaction.user, user, reason=reason)

    @app_commands.command(name="timeout", description="Timeout a member for a number of minutes (Discord native timeout).")
    @app_commands.describe(user="Member to timeout.", minutes="Duration in minutes (max 40320 = 28 days).", reason="Reason for the timeout.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: str = "No reason provided",
    ):
        """Apply Discord's native timeout (communication disabled)."""
        if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                embed=error_embed("You cannot moderate this user."), ephemeral=True
            )
            return
        if user.id == self.bot.user.id:
            await interaction.response.send_message(
                embed=error_embed("I cannot timeout myself."), ephemeral=True
            )
            return

        try:
            await user.timeout(timedelta(minutes=minutes), reason=f"By {interaction.user}: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("I lack **Moderate Members** permission (or their role is above mine)."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=success_embed(f"Timed out {user.mention} for **{minutes}m**."), ephemeral=True
        )
        await log_action(self.bot, interaction.guild, "Timeout", interaction.user, user, reason=reason, duration=f"{minutes}m")

    @app_commands.command(name="untimeout", description="Remove a timeout from a member.")
    @app_commands.describe(user="Member to remove the timeout from.", reason="Reason.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided",
    ):
        """Remove Discord's native timeout."""
        if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                embed=error_embed("You cannot moderate this user."), ephemeral=True
            )
            return

        try:
            await user.timeout(None, reason=f"Timeout removed by {interaction.user}: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("I lack **Moderate Members** permission (or their role is above mine)."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=success_embed(f"Removed {user.mention}'s timeout."), ephemeral=True
        )
        await log_action(self.bot, interaction.guild, "Untimeout", interaction.user, user, reason=reason)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))

"""Persistent private-thread tickets and reviewed partnership exchanges."""
import asyncio
import json
import re
import time
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from config import DATA_DIR
from database import get_db, get_setting, set_setting
from utils import base_embed

DESTINATION = 1563853562176344166
PARTNER_CHANNEL = 1563853561727811622
TYPES = {
    'Support': ('🛡️', 'support', 'Private, high-priority support issues'),
    'Partnership': ('🤝', 'partnership', 'Partner with our server (12h cooldown)'),
    'Player/Staff Report': ('🚨', 'report', 'Report a player or staff member'),
    'Staff Apply': ('🛡️', 'staffapply', 'Apply to join the staff team'),
    'Other Inquiries': ('🔷', 'other', 'For anything else not listed'),
}
TAGS = ['Java', 'Bedrock', 'SMP', 'Lifesteal', 'PvP', 'Survival', 'Crossplay', 'Cracked', 'Vanilla', 'Minigames', 'Factions', 'Economy', 'Modded', 'PvE', 'Whitelist', 'Europe', 'North America', 'Asia', 'Australia', 'Oceania']
INVITE = re.compile(r'(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]+)', re.I)
STAFF_QUESTIONS = '1) Do you have past/current staffing experience?\n2) How many partnerships can you do per week?\n3) How active can you be on the server?\n4) Why do you want to be a staff member?\n5) What makes you different from other applicants?\n6) Tell us about yourself.'
NO_MENTIONS = discord.AllowedMentions.none()


def ping_rule(count):
    if count < 30:
        raise ValueError('At least 30 members are required')
    if count <= 49:
        return '', '@everyone'
    if count <= 99:
        return '@here', '@everyone'
    if count <= 149:
        return '@here', '@here'
    if count <= 199:
        return '@everyone', '@here'
    return '@everyone', ''


def clean_ad(text):
    return re.sub(r'@(everyone|here)', '', text, flags=re.I).strip()


def panel_embed():
    return discord.Embed(colour=0x155E32, description=(
        '🛡️ **__Support__**\nOnly for private high priority issues.\n\n'
        '🤝 **__Partnership__**\nPartner with our server.\nYou can re-partner every 12 hours.\n\n'
        '🚨 **__Player/Staff Report__**\nReport a player or staff member privately.\n\n'
        '🛡️ **__Staff Apply__**\nApply to join the staff team.\n\n'
        '🔷 **__Other Inquiries__**\nUse this for anything else.'
    ))


async def schema():
    db = await get_db()
    try:
        await db.executescript('''
        CREATE TABLE IF NOT EXISTS au_thread_tickets (
            number INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, opener_id TEXT NOT NULL, kind TEXT NOT NULL,
            thread_id TEXT UNIQUE, stage TEXT NOT NULL DEFAULT 'creating',
            opened_at REAL NOT NULL, closed_at REAL, ad TEXT, invite TEXT,
            server_id TEXT, server_name TEXT, server_icon TEXT, member_count INTEGER,
            tags TEXT NOT NULL DEFAULT '[]', proof_url TEXT,
            tag_message_id TEXT, review_message_id TEXT, publication_id TEXT,
            denials TEXT NOT NULL DEFAULT '[]', reviewer_id TEXT,
            rule_ours TEXT, rule_theirs TEXT, claimed_by TEXT
        );
        CREATE INDEX IF NOT EXISTS au_ticket_owner ON au_thread_tickets(guild_id, opener_id, stage);
        ''')
        async with db.execute('PRAGMA table_info(au_thread_tickets)') as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if 'claimed_by' not in columns:
            await db.execute('ALTER TABLE au_thread_tickets ADD COLUMN claimed_by TEXT')
        await db.commit()
    finally:
        await db.close()


async def record(thread_id):
    db = await get_db()
    try:
        async with db.execute('SELECT * FROM au_thread_tickets WHERE thread_id=?', (str(thread_id),)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    finally:
        await db.close()


async def update(thread_id, **values):
    allowed = {'stage', 'closed_at', 'ad', 'invite', 'server_id', 'server_name', 'server_icon', 'member_count', 'tags', 'proof_url', 'tag_message_id', 'review_message_id', 'publication_id', 'denials', 'reviewer_id', 'rule_ours', 'rule_theirs', 'claimed_by'}
    if not values or not values.keys() <= allowed:
        raise ValueError('Invalid ticket update')
    db = await get_db()
    try:
        await db.execute('UPDATE au_thread_tickets SET ' + ','.join(key + '=?' for key in values) + ' WHERE thread_id=?', (*values.values(), str(thread_id)))
        await db.commit()
    finally:
        await db.close()


class TicketPanelView(discord.ui.View):
    def __init__(self, categories=None):
        super().__init__(timeout=None)

    @discord.ui.select(placeholder='🎫 Choose a ticket type...', custom_id='pffa_ticket_select', options=[
        discord.SelectOption(label=name, value=name, emoji=info[0], description=info[2]) for name, info in TYPES.items()
    ])
    async def choose(self, interaction, select):
        cog = interaction.client.get_cog('ThreadTickets')
        if not cog:
            return await interaction.response.send_message('Tickets are temporarily unavailable.', ephemeral=True)
        await cog.open_ticket(interaction, select.values[0])


class CloseThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Close Ticket', emoji='🔒', style=discord.ButtonStyle.secondary, custom_id='au_thread_close')
    async def close(self, interaction, button):
        rec = await record(interaction.channel.id)
        cog = interaction.client.get_cog('ThreadTickets')
        if not rec or (rec['opener_id'] != str(interaction.user.id) and not await cog.is_staff(interaction, rec)):
            return await interaction.response.send_message('Only the opener or authorised staff can close this ticket.', ephemeral=True)
        await interaction.response.send_modal(CloseModal(cog))


class CloseModal(discord.ui.Modal, title='Close Ticket'):
    reason = discord.ui.TextInput(label='Reason', style=discord.TextStyle.paragraph, max_length=1000)
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog
    async def on_submit(self, interaction):
        rec = await record(interaction.channel.id)
        if not rec or (rec['opener_id'] != str(interaction.user.id) and not await self.cog.is_staff(interaction, rec)):
            return await interaction.response.send_message('You cannot close this ticket.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self.cog.lock(interaction.channel.id):
            rec = await record(interaction.channel.id)
            if rec['stage'] in ('publishing', 'published'):
                return await interaction.followup.send('Finish the partnership approval before closing this ticket.', ephemeral=True)
            await self.cog.close_thread(interaction.channel, interaction.user, self.reason.value)
        await interaction.followup.send('Ticket closed and archived.', ephemeral=True)


def tag_text(selected):
    return '## 🏷️ Step 2/3 — Server Tags\nClick tags to **select/deselect** them (up to 5). Press **Done** below when finished.\n> Selected: **' + (', '.join(selected) or 'None') + '**'


class TagButton(discord.ui.Button):
    def __init__(self, label, selected, index):
        super().__init__(label=label, style=discord.ButtonStyle.success if label in selected else discord.ButtonStyle.secondary, custom_id='au_tag_'+str(index), row=index//5)
        self.tag = label
    async def callback(self, interaction):
        await interaction.response.defer()
        cog = interaction.client.get_cog('ThreadTickets')
        async with cog.lock(interaction.channel.id):
            rec = await record(interaction.channel.id)
            if not rec or rec['stage'] != 'tags' or rec['opener_id'] != str(interaction.user.id):
                return await interaction.followup.send('Only the applicant can change tags at this step.', ephemeral=True)
            selected = json.loads(rec['tags'])
            if self.tag in selected:
                selected.remove(self.tag)
            elif len(selected) < 5:
                selected.append(self.tag)
            else:
                return await interaction.followup.send('Select up to five tags. Deselect one first.', ephemeral=True)
            await update(interaction.channel.id, tags=json.dumps(selected))
            await interaction.message.edit(content=tag_text(selected), view=TagView(selected), allowed_mentions=NO_MENTIONS)


class TagView(discord.ui.View):
    def __init__(self, selected=None):
        super().__init__(timeout=None)
        for i, tag in enumerate(TAGS):
            self.add_item(TagButton(tag, selected or [], i))
        button = discord.ui.Button(label='Done (Continue)', emoji='✅', style=discord.ButtonStyle.primary, custom_id='au_tags_done', row=4)
        button.callback = self.done
        self.add_item(button)
    async def done(self, interaction):
        await interaction.response.defer()
        cog = interaction.client.get_cog('ThreadTickets')
        async with cog.lock(interaction.channel.id):
            rec = await record(interaction.channel.id)
            if not rec or rec['stage'] != 'tags' or rec['opener_id'] != str(interaction.user.id):
                return await interaction.followup.send('This step is not available to you.', ephemeral=True)
            if not json.loads(rec['tags']):
                return await interaction.followup.send('Select at least one server tag.', ephemeral=True)
            minecraft_tags = set(TAGS[:15])
            if not minecraft_tags.intersection(json.loads(rec['tags'])):
                return await interaction.followup.send('Partnerships are for Minecraft servers. Select at least one Minecraft platform or gameplay tag.', ephemeral=True)
            ad = await get_setting(interaction.guild.id, 'partner_ad')
            if not ad:
                return await interaction.followup.send('Ask staff to configure our advertisement with /ad set.', ephemeral=True)
            ours, theirs = ping_rule(rec['member_count'])
            prompt = base_embed(title='📸 Step 3/3 — Proof Screenshot', description='Send the ad below in your server with **' + (theirs or 'no ping') + '**, then upload a **screenshot as proof** here.')
            await interaction.channel.send(embed=prompt, allowed_mentions=NO_MENTIONS)
            await interaction.channel.send(clean_ad(ad)[:1800] + ('\n'+theirs if theirs else ''), allowed_mentions=NO_MENTIONS)
            await update(interaction.channel.id, stage='proof', rule_ours=ours, rule_theirs=theirs)
            disabled = TagView(json.loads(rec['tags']))
            for item in disabled.children:
                item.disabled = True
            await interaction.message.edit(view=disabled)


class ReviewView(discord.ui.View):
    def __init__(self, denials=0):
        super().__init__(timeout=None)
        self.deny.label = f'Deny ({denials}/3)'
    @discord.ui.button(label='Accept', emoji='✅', style=discord.ButtonStyle.success, custom_id='au_partner_accept')
    async def accept(self, interaction, button):
        await interaction.client.get_cog('ThreadTickets').accept(interaction)
    @discord.ui.button(label='Deny (0/3)', emoji='❌', style=discord.ButtonStyle.danger, custom_id='au_partner_deny')
    async def deny(self, interaction, button):
        rec = await record(interaction.channel.id)
        cog = interaction.client.get_cog('ThreadTickets')
        if not await cog.is_staff(interaction, rec):
            return await interaction.response.send_message('Authorised partnership reviewers only.', ephemeral=True)
        await interaction.response.send_modal(DenyModal(cog))


class DenyModal(discord.ui.Modal, title='Deny Partnership'):
    reason = discord.ui.TextInput(label='Reason', style=discord.TextStyle.paragraph, max_length=700)
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog
    async def on_submit(self, interaction):
        rec = await record(interaction.channel.id)
        if not await self.cog.is_staff(interaction, rec):
            return await interaction.response.send_message('Authorised reviewers only.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self.cog.lock(interaction.channel.id):
            rec = await record(interaction.channel.id)
            if rec['stage'] != 'review':
                return await interaction.followup.send('This partnership is not awaiting review.', ephemeral=True)
            votes = json.loads(rec['denials'])
            if str(interaction.user.id) in votes:
                return await interaction.followup.send('Your denial has already been recorded.', ephemeral=True)
            votes.append(str(interaction.user.id))
            await update(interaction.channel.id, denials=json.dumps(votes))
            await interaction.channel.send(embed=base_embed(title='Partnership review', description=f'{interaction.user.mention} voted to deny.\n**Reason:** {self.reason.value}'), allowed_mentions=NO_MENTIONS)
            message = await interaction.channel.fetch_message(int(rec['review_message_id']))
            await message.edit(view=ReviewView(len(votes)) if len(votes)<3 else None)
            if len(votes)>=3:
                await self.cog.close_thread(interaction.channel, interaction.user, 'Denied by three reviewers', status='denied')
            await interaction.followup.send(f'Denial recorded ({len(votes)}/3).', ephemeral=True)


class AdModal(discord.ui.Modal, title='AU Schools Advertisement'):
    text = discord.ui.TextInput(label='Advertisement including our invite', style=discord.TextStyle.paragraph, max_length=1800)
    def __init__(self, current):
        super().__init__(timeout=600)
        self.text.default = current[:1800]
    async def on_submit(self, interaction):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message('Manage Server permission required.', ephemeral=True)
        if not INVITE.search(self.text.value):
            return await interaction.response.send_message('Include our Discord invite in the advertisement.', ephemeral=True)
        await set_setting(interaction.guild.id, 'partner_ad', clean_ad(self.text.value))
        await interaction.response.send_message('Advertisement saved.', ephemeral=True)


class ThreadTickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._locks = {}

    def lock(self, key):
        return self._locks.setdefault(key, asyncio.Lock())

    async def cog_load(self):
        await schema()
        bundled_ad = Path(__file__).resolve().parents[1] / 'assets' / 'partnership-ad.txt'
        if bundled_ad.is_file():
            from config import MAIN_GUILD_ID
            if MAIN_GUILD_ID and not await get_setting(MAIN_GUILD_ID,'partner_ad'):
                await set_setting(MAIN_GUILD_ID,'partner_ad',bundled_ad.read_text(encoding='utf-8'))
        for view in (TicketPanelView(), CloseThreadView(), TagView(), ReviewView()):
            self.bot.add_view(view)

    async def is_staff(self, interaction, rec):
        if not rec or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        if perms.administrator or perms.manage_guild:
            return True
        key = 'ticket_admin_role' if rec['kind']=='Player/Staff Report' else 'partner_review_role' if rec['kind']=='Partnership' else 'ticket_staff_role'
        role_id = await get_setting(interaction.guild.id, key)
        return bool(role_id and str(role_id) in {str(r.id) for r in interaction.user.roles})

    async def parent_channel(self, guild):
        async with self.lock((guild.id, "parent")):
            return await self._parent_channel(guild)

    async def _parent_channel(self, guild):
        saved = await get_setting(guild.id, 'thread_ticket_parent')
        parent = guild.get_channel(int(saved)) if saved and saved.isdigit() else guild.get_channel(DESTINATION)
        if isinstance(parent, discord.TextChannel):
            return parent
        if not isinstance(parent, discord.CategoryChannel):
            raise RuntimeError('Ticket parent channel/category is unavailable.')
        existing = discord.utils.get(parent.text_channels, name='ticket-threads')
        if existing:
            await set_setting(guild.id, 'thread_ticket_parent', str(existing.id))
            return existing
        channel = await guild.create_text_channel('ticket-threads', category=parent, overwrites={
            guild.default_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False, send_messages_in_threads=True, create_public_threads=False, create_private_threads=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_threads=True, create_private_threads=True, send_messages_in_threads=True, attach_files=True, embed_links=True),
        }, reason='AU Schools private ticket threads')
        await set_setting(guild.id, 'thread_ticket_parent', str(channel.id))
        return channel

    async def open_ticket(self, interaction, kind):
        await interaction.response.defer(ephemeral=True)
        aliases = {'General Support':'Support', 'Report a Member':'Player/Staff Report', 'Player Report':'Player/Staff Report', 'Staff Report':'Player/Staff Report', 'Application':'Staff Apply', 'Bug Report':'Support', 'Unban Appeal':'Support'}
        kind = aliases.get(kind, kind)
        if kind not in TYPES:
            return await interaction.followup.send('Use the new ticket panel to select a ticket type.', ephemeral=True)
        key = (interaction.guild.id, interaction.user.id)
        async with self.lock(key):
            db = await get_db()
            try:
                async with db.execute("SELECT thread_id FROM au_thread_tickets WHERE guild_id=? AND opener_id=? AND kind=? AND closed_at IS NULL AND stage!='failed'", (*map(str,key),kind)) as cursor:
                    existing = await cursor.fetchone()
                if existing:
                    return await interaction.followup.send(f"You already have an open ticket in this category: <#{existing['thread_id']}>", ephemeral=True)
                if kind == 'Partnership':
                    async with db.execute("SELECT MAX(opened_at) AS last FROM au_thread_tickets WHERE guild_id=? AND opener_id=? AND kind='Partnership' AND stage!='failed'", tuple(map(str,key))) as cursor:
                        last = (await cursor.fetchone())['last']
                    reset = await get_setting(interaction.guild.id, 'partner_cooldown_reset_'+str(interaction.user.id))
                    if last and time.time()-last < 43200 and not (reset and float(reset)>last):
                        return await interaction.followup.send(f'You can re-partner <t:{int(last+43200)}:R>.', ephemeral=True)
                    if not await get_setting(interaction.guild.id, 'partner_ad'):
                        return await interaction.followup.send('Staff must configure our advertisement with /ad set before partnerships can open.', ephemeral=True)
                cursor = await db.execute('INSERT INTO au_thread_tickets(guild_id,opener_id,kind,opened_at) VALUES(?,?,?,?)', (str(key[0]),str(key[1]),kind,time.time()))
                number = cursor.lastrowid
                await db.commit()
            finally:
                await db.close()
            thread = None
            try:
                parent = await self.parent_channel(interaction.guild)
                emoji, slug, _ = TYPES[kind]
                thread = await parent.create_thread(name=f'{emoji} | {slug}-#{number}', type=discord.ChannelType.private_thread, invitable=False, auto_archive_duration=1440, reason=f'{kind} ticket by {interaction.user}')
                await thread.add_user(interaction.user)
                db = await get_db()
                try:
                    await db.execute('UPDATE au_thread_tickets SET thread_id=?,stage=? WHERE number=?', (str(thread.id),'ad' if kind=='Partnership' else 'open',number))
                    await db.commit()
                finally:
                    await db.close()
                role_key = 'ticket_admin_role' if kind=='Player/Staff Report' else 'partner_review_role' if kind=='Partnership' else 'ticket_staff_role'
                role_id = await get_setting(interaction.guild.id, role_key)
                role = interaction.guild.get_role(int(role_id)) if role_id and role_id.isdigit() else None
                if role:
                    for member in role.members:
                        if not member.bot:
                            await thread.add_user(member)
                welcome = {
                    'Support': f'👋 Welcome {interaction.user.mention}! Please describe your issue in detail and a staff member will assist you shortly.',
                    'Player/Staff Report': f"👋 Welcome {interaction.user.mention}! Please state who you're reporting and describe the incident in detail.",
                    'Staff Apply': f'👋 Welcome {interaction.user.mention}! Please answer the following questions:\n\n'+STAFF_QUESTIONS,
                    'Other Inquiries': f'👋 Welcome {interaction.user.mention}! Please describe your inquiry and a staff member will assist you shortly.',
                    'Partnership': '## 📋 Step 1/4 — Advertisement + Invite Link\nSend your full **advertisement text** and your **Discord invite link** in the same message.\n\n**Requirements**\n• Minecraft servers only.\n• At least 30 members.\n• No botted or fake members.\n\n**Partner Requirements — Our Server: 100 Members**\n30–49: We do not ping; you @everyone.\n50–99: We @here; you @everyone.\n100–149: Both @here.\n150–199: We @everyone; you @here.\n200+: We @everyone; you do not ping.',
                }[kind]
                await thread.send(welcome, view=CloseThreadView(), allowed_mentions=discord.AllowedMentions(everyone=False,roles=False,users=[interaction.user]))
                await interaction.followup.send(f'Ticket opened: {thread.mention}', ephemeral=True)
            except (discord.HTTPException, RuntimeError):
                db = await get_db()
                try:
                    await db.execute("UPDATE au_thread_tickets SET stage='failed',closed_at=? WHERE number=?", (time.time(),number))
                    await db.commit()
                finally:
                    await db.close()
                if thread:
                    try:
                        await thread.edit(archived=True, locked=True)
                    except discord.HTTPException:
                        pass
                await interaction.followup.send('The ticket could not be created. Check my Manage Threads, Create Private Threads and channel permissions.', ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not isinstance(message.channel, discord.Thread):
            return
        rec = await record(message.channel.id)
        if not rec or rec['kind']!='Partnership' or rec['opener_id']!=str(message.author.id):
            return
        async with self.lock(message.channel.id):
            rec = await record(message.channel.id)
            if rec['stage'] == 'ad':
                match = INVITE.search(message.content)
                if not match or len(message.content.strip())<30 or len(message.content)>3500:
                    return await message.channel.send('Send your full advertisement (30–3500 characters) and a valid Discord invite link in one message.', allowed_mentions=NO_MENTIONS)
                try:
                    invite = await self.bot.fetch_invite(match.group(1), with_counts=True)
                except discord.HTTPException:
                    return await message.channel.send('That invite is invalid or unavailable. Send your advertisement again with a working invite.')
                if not invite.guild or invite.guild.id==message.guild.id or invite.approximate_member_count is None:
                    return await message.channel.send('Use an invite to a different Minecraft server with a visible member count.')
                if invite.approximate_member_count < 30:
                    return await message.channel.send('Your server needs at least **30 members** to partner. This invite does not meet the minimum.')
                await update(message.channel.id, stage='tags', ad=message.content, invite='https://discord.gg/'+match.group(1), server_id=str(invite.guild.id), server_name=invite.guild.name, server_icon=str(invite.guild.icon.url) if invite.guild.icon else '', member_count=invite.approximate_member_count)
                sent = await message.channel.send(tag_text([]), view=TagView(), allowed_mentions=NO_MENTIONS)
                await update(message.channel.id, tag_message_id=str(sent.id))
            elif rec['stage'] == 'proof':
                image = next((a for a in message.attachments if a.content_type and a.content_type.startswith('image/')), None)
                if not image:
                    return await message.channel.send('Upload a screenshot image as an attachment so staff can review your advertisement proof.')
                if image.size > 8*1024*1024:
                    return await message.channel.send('Please upload a screenshot smaller than 8 MB.')
                proof = await image.to_file(filename='partnership-proof.png')
                embed = base_embed(title='🔍 Partnership Application — Staff Review')
                embed.add_field(name='🏷️ Server', value=rec['server_name'][:1024])
                embed.add_field(name='🔗 Invite', value=rec['invite'])
                selected = json.loads(rec['tags'])
                embed.add_field(name='⚙️ Type', value=', '.join(t for t in selected if t in ['Java','Bedrock','Crossplay']) or 'Minecraft')
                embed.add_field(name='🏷️ Tags', value=', '.join(selected))
                embed.add_field(name='👥 Members', value=str(rec['member_count']))
                embed.add_field(name='📣 Ping Agreement', value=f"AU Schools: {rec['rule_ours'] or 'No ping'}\nPartner: {rec['rule_theirs'] or 'No ping'}")
                embed.add_field(name='Reviewer checks', value='Confirm this is a Minecraft server, has genuine members, and the screenshot shows our full ad with the required ping.', inline=False)
                embed.set_image(url='attachment://partnership-proof.png')
                if rec['server_icon']:
                    embed.set_thumbnail(url=rec['server_icon'])
                review = await message.channel.send(f"🔍 **Staff Review — {message.author.mention}'s Application**", embed=embed, file=proof, view=ReviewView(), allowed_mentions=NO_MENTIONS)
                await update(message.channel.id, stage='review', review_message_id=str(review.id), proof_url=review.attachments[0].url)
                await message.channel.edit(name=(message.channel.name+' | 🔍 Under review')[:100])

    async def accept(self, interaction):
        rec = await record(interaction.channel.id)
        if not await self.is_staff(interaction, rec):
            return await interaction.response.send_message('Authorised partnership reviewers only.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self.lock(interaction.channel.id):
            rec = await record(interaction.channel.id)
            if rec['stage'] not in ('review', 'publishing', 'published'):
                return await interaction.followup.send('This partnership has already been handled.', ephemeral=True)
            channel_id = await get_setting(interaction.guild.id, 'partner_log_channel') or str(PARTNER_CHANNEL)
            dest = interaction.guild.get_channel(int(channel_id))
            if not isinstance(dest, discord.TextChannel):
                return await interaction.followup.send('The partnership announcement channel is unavailable.', ephemeral=True)
            if not rec['publication_id']:
                marker = f"Partnership #{rec['number']} • AU Schools"
                published = None
                # Persisted intent plus a public message marker allows interrupted approvals to resume.
                await update(interaction.channel.id, stage='publishing', reviewer_id=str(interaction.user.id))
                async for candidate in dest.history(limit=None, after=discord.Object(id=interaction.channel.id)):
                    if candidate.author.id==self.bot.user.id and any(e.footer.text==marker for e in candidate.embeds):
                        published = candidate
                        break
                if published is None:
                    try:
                        invite = await self.bot.fetch_invite(rec['invite'], with_counts=True)
                    except discord.HTTPException:
                        await update(interaction.channel.id, stage='review')
                        return await interaction.followup.send('The invite no longer works. Ask for a valid invite before accepting.', ephemeral=True)
                    if not invite.guild or str(invite.guild.id)!=rec['server_id']:
                        await update(interaction.channel.id, stage='review')
                        return await interaction.followup.send('The invite no longer matches the reviewed server.', ephemeral=True)
                    count = invite.approximate_member_count
                    if count is None or count<30:
                        await update(interaction.channel.id, stage='review')
                        return await interaction.followup.send('This server no longer meets the 30-member minimum.', ephemeral=True)
                    if ping_rule(count)!=(rec['rule_ours'],rec['rule_theirs']):
                        await update(interaction.channel.id, stage='review')
                        return await interaction.followup.send('The server moved to a different ping tier. Use /partnership request-proof to restart the agreement and proof step.', ephemeral=True)
                    embed = base_embed(title=rec['server_name'][:256], description=clean_ad(rec['ad']))
                    embed.add_field(name='Tags', value=', '.join(json.loads(rec['tags'])))
                    embed.add_field(name='Invite', value=rec['invite'])
                    embed.set_footer(text=marker)
                    if rec['server_icon']:
                        embed.set_thumbnail(url=rec['server_icon'])
                    published = await dest.send(content=rec['rule_ours'] or None, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=bool(rec['rule_ours']), users=False, roles=False), nonce=interaction.channel.id)
                await update(interaction.channel.id, stage='published', publication_id=str(published.id))
            await interaction.followup.send('Partnership accepted and published. Closing this ticket.', ephemeral=True)
            await self.close_thread(interaction.channel, interaction.user, 'Partnership approved and published', status='approved')

    async def close_thread(self, thread, user, reason, status='closed'):
        rec = await record(thread.id)
        if not rec or rec['closed_at']:
            return
        if rec['stage'] in ('publishing','published') and status!='approved':
            raise RuntimeError('Finish the partnership approval before closing this ticket.')
        await thread.send(embed=base_embed(title='Ticket Closed', description=f'**Closed by:** {user.mention}\n**Reason:** {reason}'), allowed_mentions=NO_MENTIONS)
        from cogs.tickets import generate_transcript
        transcript = await generate_transcript(thread)
        directory = Path(DATA_DIR)/'transcripts'
        directory.mkdir(parents=True, exist_ok=True)
        (directory/f'thread-{thread.id}.html').write_bytes(transcript.fp.getvalue())
        await thread.edit(archived=True, locked=True, reason=reason[:400])
        await update(thread.id, stage=status, closed_at=time.time())

    async def close_command(self, interaction, partnership_only=False):
        rec = await record(interaction.channel.id)
        if not rec or (partnership_only and rec['kind']!='Partnership'):
            return await interaction.response.send_message('Use this command inside an open partnership ticket.' if partnership_only else 'Use this command inside a ticket thread.',ephemeral=True)
        if rec['closed_at']:
            return await interaction.response.send_message('This ticket is already closed.',ephemeral=True)
        if rec['opener_id']!=str(interaction.user.id) and not await self.is_staff(interaction,rec):
            return await interaction.response.send_message('Only the opener or authorised staff can close this ticket.',ephemeral=True)
        await interaction.response.send_modal(CloseModal(self))

    @app_commands.command(name='close',description='Close this ticket with a reason and save its transcript.')
    @app_commands.guild_only()
    async def close(self,interaction:discord.Interaction):
        await self.close_command(interaction)

    @app_commands.command(name='partnerclose',description='Close the current partnership ticket.')
    @app_commands.guild_only()
    async def partnerclose(self,interaction:discord.Interaction):
        await self.close_command(interaction,partnership_only=True)

    @app_commands.command(name='claim',description='Assign the current ticket to yourself.')
    @app_commands.guild_only()
    async def claim(self,interaction:discord.Interaction):
        await self.set_assignment(interaction,release=False)

    @app_commands.command(name='unclaim',description='Release your ticket assignment back to the staff team.')
    @app_commands.guild_only()
    async def unclaim(self,interaction:discord.Interaction):
        await self.set_assignment(interaction,release=True)

    async def set_assignment(self,interaction,release):
        rec = await record(interaction.channel.id)
        if not rec or not await self.is_staff(interaction,rec):
            return await interaction.response.send_message('Authorised ticket staff only.',ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self.lock(interaction.channel.id):
            rec = await record(interaction.channel.id)
            if rec['closed_at']:
                return await interaction.followup.send('This ticket is closed.',ephemeral=True)
            if rec['claimed_by'] and rec['claimed_by']!=str(interaction.user.id):
                return await interaction.followup.send(f"This ticket is assigned to <@{rec['claimed_by']}>.",ephemeral=True)
            await update(interaction.channel.id,claimed_by=None if release else str(interaction.user.id))
            await interaction.channel.send(f"{interaction.user.mention} {'released this ticket back to the team' if release else 'claimed this ticket and will assist you shortly'}.",allowed_mentions=NO_MENTIONS)
            await interaction.followup.send('Assignment released.' if release else 'Ticket claimed.',ephemeral=True)

    @app_commands.command(name='transcript',description='Save a transcript of this ticket without closing it.')
    @app_commands.guild_only()
    async def transcript(self,interaction:discord.Interaction):
        rec=await record(interaction.channel.id)
        if not rec or (rec['opener_id']!=str(interaction.user.id) and not await self.is_staff(interaction,rec)):
            return await interaction.response.send_message('You cannot export this ticket.',ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        from cogs.tickets import generate_transcript
        await interaction.followup.send(file=await generate_transcript(interaction.channel),ephemeral=True)

    group = app_commands.Group(name='partnership', description='Partnership configuration and review settings', default_permissions=discord.Permissions(manage_guild=True), guild_only=True)

    @group.command(name='set-review-role', description='Role allowed to review partnership applications.')
    async def review_role(self, interaction: discord.Interaction, role: discord.Role):
        await set_setting(interaction.guild.id, 'partner_review_role', str(role.id))
        await interaction.response.send_message('Partnership review role saved. New tickets will add its members.', ephemeral=True)

    @group.command(name='set-log-channel', description='Channel receiving approved partnership advertisements.')
    async def log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await set_setting(interaction.guild.id, 'partner_log_channel', str(channel.id))
        await interaction.response.send_message('Partnership announcement channel saved.', ephemeral=True)

    @group.command(name='set-thread-channel', description='Text channel used as the parent of private ticket threads.')
    async def parent(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await set_setting(interaction.guild.id, 'thread_ticket_parent', str(channel.id))
        await interaction.response.send_message('Private thread parent saved.', ephemeral=True)

    @group.command(name='reset-cooldown', description='Allow a member to open a partnership before 12 hours have passed.')
    async def reset(self, interaction: discord.Interaction, member: discord.Member):
        await set_setting(interaction.guild.id, 'partner_cooldown_reset_'+str(member.id), str(time.time()))
        await interaction.response.send_message('Partnership cooldown reset.', ephemeral=True)

    @group.command(name='request-proof', description='Restart tags and proof when the invite or ping agreement needs updating.')
    async def request_proof(self, interaction: discord.Interaction):
        rec = await record(interaction.channel.id)
        if not rec or rec['kind']!='Partnership' or not await self.is_staff(interaction,rec):
            return await interaction.response.send_message('Use this inside a partnership ticket as a reviewer.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self.lock(interaction.channel.id):
            rec = await record(interaction.channel.id)
            if rec['stage'] not in ('review','tags','proof','ad'):
                return await interaction.followup.send('A completed or publishing partnership cannot be reset.',ephemeral=True)
            if rec['review_message_id']:
                try:
                    message = await interaction.channel.fetch_message(int(rec['review_message_id']))
                    await message.edit(view=None)
                except discord.NotFound:
                    pass
            await update(interaction.channel.id,stage='ad',tags='[]',denials='[]',review_message_id=None,proof_url=None)
            await interaction.channel.send('Please resend your advertisement and current invite to restart the partnership agreement.',allowed_mentions=NO_MENTIONS)
            await interaction.followup.send('Advertisement, tags and proof steps reset.',ephemeral=True)

    ad = app_commands.Group(name='ad', description='Configure the AU Schools partnership advertisement', default_permissions=discord.Permissions(manage_guild=True), guild_only=True)

    @ad.command(name='set', description='Set our advertisement and invite used during partnership exchanges.')
    async def ad_set(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AdModal(await get_setting(interaction.guild.id,'partner_ad') or ''))

    @ad.command(name='view', description='Preview our partnership advertisement.')
    async def ad_view(self, interaction: discord.Interaction):
        await interaction.response.send_message(await get_setting(interaction.guild.id,'partner_ad') or 'No advertisement configured. Use /ad set.',ephemeral=True,allowed_mentions=NO_MENTIONS)


async def setup(bot):
    await bot.add_cog(ThreadTickets(bot))

import os,sys,tempfile,asyncio,json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='au-partnership-tests-')
import discord
from database import init_db,get_db,set_setting
from cogs.thread_tickets import *

def interaction(cog,thread,uid=10):
 return NS(channel=thread,guild=thread.guild,user=NS(id=uid,mention=f'<@{uid}>'),client=NS(get_cog=lambda name:cog),response=NS(defer=AsyncMock(),send_message=AsyncMock()),followup=NS(send=AsyncMock()),message=NS(edit=AsyncMock()))

async def history(*args,**kwargs):
 if False:yield None

async def run():
 await init_db();await schema()
 from main import AUSchoolsBot
 async with AUSchoolsBot() as loaded:
  for name in ['thread_tickets','autorole','moderation','roles','softban','staff','tickets','utility','welcome']:
   await loaded.load_extension('cogs.'+name)
  loaded.apply_command_policy()
  commands={c.qualified_name for c in loaded.tree.walk_commands()}
  assert {'close','claim','unclaim','partnerclose','transcript','ticket panel'}<=commands
  assert not commands & {'dashboard','apply','ad','verify_setup','verify_delete','masskick','blacklistedaddons','welcome','autorole','admin-config','edit-rules','edit-serverinfo','warn','warnings','clear-warnings','user-info','slowmode','role-info','rules','ping','massban','avatar'}
  assert {c.name for c in loaded.tree.get_command('ticket').commands}=={'panel'}
 for count,expected in [(30,('','@everyone')),(49,('','@everyone')),(50,('@here','@everyone')),(99,('@here','@everyone')),(100,('@here','@here')),(149,('@here','@here')),(150,('@everyone','@here')),(199,('@everyone','@here')),(200,('@everyone','')),(5000,('@everyone',''))]:assert ping_rule(count)==expected
 for n in [0,29]:
  try:ping_rule(n)
  except ValueError:pass
  else:raise AssertionError('Minimum not enforced')
 assert len(TagView().children)==21 and TagView().is_persistent() and ReviewView().is_persistent() and TicketPanelView().is_persistent()
 assert len(TicketPanelView().choose.options)==5
 assert 'paid' not in panel_embed().description.lower()
 assert '@everyone' not in clean_ad('Ad @everyone @here')
 dest=MagicMock(spec=discord.TextChannel);dest.id=500;dest.history=history;dest.send=AsyncMock(return_value=NS(id=777))
 guild=NS(id=1,get_channel=lambda _:dest)
 thread=MagicMock(spec=discord.Thread);thread.id=1001;thread.guild=guild;thread.name='🤝 | partnership-#1';thread.mention='<#1001>';thread.edit=AsyncMock();thread.send=AsyncMock(return_value=NS(id=2002,attachments=[],embeds=[discord.Embed().set_image(url='https://cdn.example/proof.png')]))
 bot=NS(user=NS(id=900),fetch_invite=AsyncMock(return_value=NS(guild=NS(id=99,name='Minecraft Community',icon=None),approximate_member_count=150)))
 cog=ThreadTickets(bot)
 db=await get_db();await db.execute("INSERT INTO au_thread_tickets(guild_id,opener_id,kind,thread_id,stage,opened_at) VALUES ('1','10','Partnership','1001','ad',?)",(time.time(),));await db.commit();await db.close()
 author=NS(id=10,bot=False,mention='<@10>')
 msg=NS(author=author,guild=guild,channel=thread,content='Our amazing Minecraft survival community! Join https://discord.gg/test',attachments=[])
 await cog.on_message(msg);rec=await record(1001);assert rec['stage']=='tags' and rec['member_count']==150
 # Five tags maximum, with toggling and owner-only edits.
 for tag in list(TAGS[:6]):
  button=TagButton(tag,[],TAGS.index(tag));await button.callback(interaction(cog,thread))
 rec=await record(1001);assert len(json.loads(rec['tags']))==5
 await TagButton('Java',[],0).callback(interaction(cog,thread,uid=55));assert len(json.loads((await record(1001))['tags']))==5
 await set_setting(1,'partner_ad','AU Schools Minecraft Community https://discord.gg/ours')
 await TagView().done(interaction(cog,thread));rec=await record(1001);assert rec['stage']=='proof' and (rec['rule_ours'],rec['rule_theirs'])==('@everyone','@here')
 assert all(call.kwargs.get('allowed_mentions') is NO_MENTIONS for call in thread.send.call_args_list if call.kwargs.get('allowed_mentions'))
 image=NS(content_type='image/png',size=1024,to_file=AsyncMock(return_value=NS()))
 msg.attachments=[image];msg.content='';await cog.on_message(msg);rec=await record(1001);assert rec['stage']=='review' and rec['review_message_id']=='2002'
 cog.is_staff=AsyncMock(return_value=True)
 async def close_stub(channel,user,reason,status='closed'):await update(channel.id,stage=status,closed_at=time.time())
 cog.close_thread=AsyncMock(side_effect=close_stub)
 # Reproduce the old stuck-proof bug and verify Accept repairs the saved review state.
 await update(1001,stage='proof',review_message_id=None)
 approval=interaction(cog,thread,uid=22)
 approval.message.id=2002;approval.message.author=NS(id=900)
 approval.message.embeds=[discord.Embed(title='🔍 Partnership Application — Staff Review').set_image(url='https://cdn.example/proof.png')]
 await cog.accept(approval);await cog.accept(interaction(cog,thread,uid=22))
 dest.send.assert_awaited_once();cog.close_thread.assert_awaited_once();assert (await record(1001))['stage']=='approved'
 assert dest.send.call_args.kwargs['content']=='@everyone'
 assert '#java\n#bedrock\n#smp' in dest.send.call_args.kwargs['embed'].description
 assert not dest.send.call_args.kwargs['embed'].fields
 assert ReviewView().deny.label=='Deny'
 assert dest.send.call_args.kwargs['allowed_mentions'].everyone is True
 print('PASS: all ping boundaries/minimum; persistent five-type panel and 20 tag buttons; tag limit and ownership; advertisement/invite -> tags -> proof -> review -> publication/close; repeated approval publishes once; mentions constrained to agreed tier.')
asyncio.run(run())

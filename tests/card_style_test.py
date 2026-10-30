import asyncio,sys,os,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='au-card-tests-')
import discord
from ui_cards import *
from cogs.thread_tickets import application_decision_card,staff_application_embeds,StaffApplicationView,ReviewView,panel_embed
from cogs import tickets
async def run():
 denied=application_decision_card('deny','Insufficient experience')
 data=denied.to_components()[0]
 assert data['type']==17 and data['accent_color']==0xED4245
 assert sum(c['type']==14 for c in data['components'])==2
 text=component_text(denied.to_components());assert '❌ Application Denied' in text and '📋 Reason' in text
 cards=[card_from_embed(e,controls=StaffApplicationView()) for e in staff_application_embeds(1,'applicant',[(str(i),'x'*4000) for i in range(6)])]
 assert all(c.content_length()<=4000 and c.total_children_count<=40 for c in cards)
 assert all(all('au_staff_'+action in str(c.to_components()) for action in ('deny','accept','hold')) for c in cards)
 proof=card_from_embed(discord.Embed(title='Review').set_image(url='https://cdn.example/proof.png'),controls=ReviewView())
 assert message_image(NS(embeds=[],components=proof.to_components()))=='https://cdn.example/proof.png'
 assert len(panel_embed().description)<1000 and not isinstance(panel_embed(),discord.ui.LayoutView)
 old=tickets.fetch_all_messages
 async def messages(channel):return [NS(created_at=datetime.now(timezone.utc),author='AU Schools',content='',embeds=[],attachments=[],components=denied.to_components())]
 tickets.fetch_all_messages=messages
 transcript=await tickets.generate_transcript(NS(name='test-thread'))
 assert b'Insufficient experience' in transcript.fp.getvalue() and b'Application Denied' in transcript.fp.getvalue()
 tickets.fetch_all_messages=old
 print('PASS: native card layout, red denial style, real dividers, application length/component limits, persistent review controls, proof image extraction, simple panel and full transcript card text.')
asyncio.run(run())

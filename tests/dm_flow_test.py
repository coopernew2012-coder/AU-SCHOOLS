import os,sys,tempfile,asyncio,io,json,time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='au-dm-tests-')
import discord
from database import init_db,get_db
from cogs.thread_tickets import ThreadTickets,StaffApplicationView,StaffDecisionModal,schema,record,update,staff_application_embeds,application_decision_embed,STAFF_QUESTIONS
import cogs.tickets as legacy
async def run():
 await init_db();await schema()
 assert STAFF_QUESTIONS.startswith('1) Why do you want to become staff?')
 long=staff_application_embeds(10,'test.applicant',[("Question",'x'*12000)])
 assert all(len(e)<=5500 for e in long)
 assert ''.join(f.value for e in long for f in e.fields)=='x'*12000
 assert application_decision_embed('deny','Reason').title=='Application Denied'
 assert 'Your application for **Staff** has been **accepted**.' in application_decision_embed('accept','Reason').description
 dm=NS(id=100,send=AsyncMock())
 user=NS(id=10,name='test.applicant',mention='<@10>',create_dm=AsyncMock(return_value=dm),send=AsyncMock())
 child=NS(id=55,add_user=AsyncMock(),send=AsyncMock(return_value=NS(id=66)),delete=AsyncMock())
 parent=NS(create_thread=AsyncMock(return_value=child))
 bot=NS(wait_for=AsyncMock(side_effect=[NS(content=f'Answer {i}',attachments=[]) for i in range(6)]))
 cog=ThreadTickets(bot);cog.parent_channel=AsyncMock(return_value=parent)
 guild=NS(id=1,members=[])
 role=NS(id=1563853559970136219)
 accepted_member=NS(roles=[],add_roles=AsyncMock())
 guild.get_role=lambda _:role
 guild.get_member=lambda _:accepted_member
 interaction=NS(guild=guild,user=user,followup=NS(send=AsyncMock()))
 await cog.start_staff_application(interaction)
 assert bot.wait_for.await_count==6;parent.create_thread.assert_awaited_once()
 rec=await record(55);assert rec['stage']=='app_review' and len(json.loads(rec['answers_json']))==6
 args=child.send.call_args.kwargs
 assert [x.label for x in args['view'].children]==['Deny','Accept','Hold']
 assert user in args['allowed_mentions'].users
 assert 'file' not in args and 'files' not in args
 assert 'test.applicant' in args['embed'].description and '`10`' in args['embed'].description
 assert not cog._staff_interviews
 assert dm.send.call_args.args[0].count('<#55>')==1
 assert 'https://discord.com/channels/' not in dm.send.call_args.args[0]
 # Manual close saves a transcript and deletes, rather than archiving.
 child.guild=NS(id=1);child.name='staffapply-1'
 original=legacy.generate_transcript;legacy.generate_transcript=AsyncMock(return_value=discord.File(io.BytesIO(b'test transcript'),filename='test.html'))
 cog.bot.get_user=lambda _:user
 cog.is_staff=AsyncMock(return_value=True)
 reviewer=NS(guild=guild,user=NS(id=20,mention='<@20>'),channel=child,response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))
 hold=StaffDecisionModal(cog,'hold');hold.reason._value='Reviewing experience'
 await hold.on_submit(reviewer);assert (await record(55))['stage']=='app_hold';child.delete.assert_not_awaited()
 accept=StaffDecisionModal(cog,'accept');accept.reason._value='Approved'
 await accept.on_submit(reviewer);await accept.on_submit(reviewer)
 accepted_member.add_roles.assert_awaited_once()
 child.delete.assert_awaited_once();assert (await record(55))['closed_at']
 legacy.generate_transcript=original
 # An expired persisted rejection deadline resumes and deletes its thread.
 db=await get_db();await db.execute("INSERT INTO au_thread_tickets(guild_id,opener_id,kind,thread_id,stage,opened_at,delete_after) VALUES('1','10','Partnership','77','rejecting',0,0)");await db.commit();await db.close()
 rejected=NS(id=77);cog.bot=NS(get_channel=lambda _:rejected,user=NS(id=9));cog.close_thread=AsyncMock()
 await cog.delete_low_member_ticket(77)
 cog.close_thread.assert_awaited_once();assert cog.close_thread.call_args.kwargs['status']=='rejected'
 print('PASS: six DM questions -> applicant-pinged review thread with Deny/Accept/Hold; interview cleanup; transcript saved before thread deletion; persisted low-member deletion resumes.')
asyncio.run(run())

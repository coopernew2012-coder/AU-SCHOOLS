import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='au-support-test-')
import discord
from database import init_db,get_db
from access import TICKET_STAFF_ROLE,STAFF_REVIEW_ROLE
from cogs.thread_tickets import ThreadTickets,schema,update
from cogs.support_ai import SupportAI,validate_decision,clean_text,IP_QUESTION


async def run():
    await init_db()
    await schema()
    threads={}
    histories={}
    bot=NS(get_cog=lambda name:tickets if name=='ThreadTickets' else None,get_channel=lambda tid:threads.get(tid),fetch_channel=AsyncMock())
    tickets=ThreadTickets(bot)
    ai=SupportAI(bot)
    await ai.cog_load()
    try:
        assert ai.knowledge['server_ip']=='121.127.47.98:12010'
        assert ai.knowledge['minecraft_version'] is None
        assert IP_QUESTION.fullmatch("what's the ip?")
        assert not IP_QUESTION.fullmatch('Can I use a VPN to change my IP and evade a ban?')
        assert not validate_decision({'reply':'Address: 1.2.3.4:1234','needs_staff':False},ai.knowledge)['needs_staff'] is False
        safe=validate_decision({'reply':'@everyone <@&123> gsk_'+('x'*30),'needs_staff':False},ai.knowledge)
        assert '@everyone' not in safe['reply'] and '<@' not in safe['reply'] and 'gsk_' not in safe['reply']
        try:
            validate_decision({'reply':'bad','needs_staff':'false'},ai.knowledge)
            raise AssertionError('String truthiness accepted')
        except ValueError:
            pass
        seq=10000
        async def new_ticket(tid,kind='Support',claimed=None,opener=42):
            db=await get_db()
            try:
                await db.execute("INSERT INTO au_thread_tickets(guild_id,opener_id,kind,thread_id,stage,opened_at,claimed_by) VALUES('1',?,?,?,'open',?,?)",(str(opener),kind,str(tid),time.time(),claimed))
                await db.commit()
            finally:
                await db.close()
            thread=MagicMock(spec=discord.Thread)
            thread.id=tid
            async def send(*args,**kwargs):
                nonlocal seq
                seq+=1
                return NS(id=seq)
            thread.send=AsyncMock(side_effect=send)
            def history(limit=100):
                async def items():
                    for item in histories.get(tid,[])[:limit]:
                        yield item
                return items()
            thread.history=history
            threads[tid]=thread
            return thread

        def message(thread,text,uid=42,attachments=None,mid=None):
            nonlocal seq
            seq+=1
            return NS(id=mid or seq,channel=thread,guild=NS(id=1),author=NS(id=uid,bot=False),content=text,attachments=attachments or [],type=discord.MessageType.default)

        for index,kind in enumerate(('Staff Apply','Partnership')):
            thread=await new_ticket(10+index,kind)
            await ai.on_message(message(thread,'hello'))
            thread.send.assert_not_awaited()
        claimed=await new_ticket(12,claimed='999')
        await ai.on_message(message(claimed,'ip?'))
        claimed.send.assert_not_awaited()
        takeover=await new_ticket(19)
        await ai.on_message(message(takeover,'Hi, how can I help?',uid=999))
        assert '<@999>' in takeover.send.call_args.args[0] and 'Goodbye' in takeover.send.call_args.args[0]
        assert takeover.send.call_args.kwargs['allowed_mentions'].users[0].id==999
        await ai.on_message(message(takeover,'More staff conversation',uid=998))
        await ai.on_message(message(takeover,'What is the IP?'))
        assert takeover.send.await_count==1,'Handoff repeated or AI interrupted human conversation'
        assert (await ai.state(19))['human_id']=='999'
        ai._human_takeovers.clear()  # Simulate losing the in-memory cache on restart.
        await ai.on_message(message(takeover,'Are you still there?'))
        assert takeover.send.await_count==1,'Handoff did not survive restart'
        thread=await new_ticket(20)
        ignored=message(thread,'another bot')
        ignored.author.bot=True
        await ai.on_message(ignored)
        ignored=message(thread,'staff added to thread',uid=999)
        ignored.type=discord.MessageType.recipient_add
        await ai.on_message(ignored)
        thread.send.assert_not_awaited()
        incoming=message(thread,'ip?')
        await ai.on_message(incoming)
        assert ai.knowledge['server_ip'] in thread.send.call_args.args[0]
        assert not thread.send.call_args.kwargs['allowed_mentions'].roles
        await ai.on_message(incoming)
        assert thread.send.await_count==1,'Duplicate Discord event replied twice'

        ai.ask=AsyncMock(return_value={'reply':'The border is 50,000 blocks.','needs_staff':False})
        await ai.on_message(message(thread,'What about the border?'))
        await ai.on_message(message(thread,'And the gameplay rules?'))
        assert len(ai.ask.call_args.args[0])>=4,'Conversation history missing'
        ai.ask.return_value={'reply':'A staff member will review that.','needs_staff':True}
        await ai.on_message(message(thread,'Please help with this problem'))
        assert f'<@&{TICKET_STAFF_ROLE}>' in thread.send.call_args.args[0]
        assert thread.send.call_args.kwargs['allowed_mentions'].roles[0].id==TICKET_STAFF_ROLE
        await ai.on_message(message(thread,'Here are more details'))
        assert thread.send.await_count==4,'AI continued after requesting staff'

        goodbye=await new_ticket(21)
        await ai.on_message(message(goodbye,'thanks, bye'))
        await ai.on_message(message(goodbye,'wait'))
        assert goodbye.send.await_count==1 and (await ai.state(21))['paused']

        pending=await new_ticket(22)
        entered=asyncio.Event()
        finish=asyncio.Event()
        async def slow_answer(*args):
            entered.set()
            await finish.wait()
            return {'reply':'This stale answer must never be sent.','needs_staff':False}
        ai.ask.side_effect=slow_answer
        pending_task=asyncio.create_task(ai.on_message(message(pending,'What are the rules?')))
        await asyncio.wait_for(entered.wait(),2)
        await ai.on_message(message(pending,'I can help you now.',uid=777))
        finish.set()
        await pending_task
        assert pending.send.await_count==1 and '<@777>' in pending.send.call_args.args[0],'In-flight answer interrupted handoff'
        ai.ask.side_effect=None

        existing=await new_ticket(23)
        histories[23]=[message(existing,'Existing staff conversation',uid=888)]
        await ai.on_ready()
        assert (await ai.state(23))['human_id']=='888','Startup ignored existing human conversation'
        existing.send.assert_not_awaited()  # No retroactive goodbye spam.
        await ai.on_message(message(existing,'Replying to staff'))
        existing.send.assert_not_awaited()

        report=await new_ticket(30,'Player/Staff Report')
        ai.ask.return_value={'reply':'Please describe what happened.','needs_staff':False}
        await ai.on_message(message(report,'Someone broke the rules'))
        assert f'<@&{STAFF_REVIEW_ROLE}>' in report.send.call_args.args[0]
        assert report.send.call_args.kwargs['allowed_mentions'].roles[0].id==STAFF_REVIEW_ROLE
        assert not any('Someone broke the rules' in item['content'] for item in await ai.history(20))

        other=await new_ticket(40,'Other Inquiries')
        ai.ask.side_effect=asyncio.TimeoutError()
        await ai.on_message(message(other,'Can I get some help?'))
        assert f'<@&{TICKET_STAFF_ROLE}>' in other.send.call_args.args[0]
        attempts=ai.ask.await_count
        first_backoff=(await ai.state(40))['backoff_until']
        await ai.on_message(message(other,'More information'))
        assert ai.ask.await_count==attempts,'API retried during backoff'
        assert (await ai.state(40))['backoff_until']==first_backoff,'Backoff was extended by user activity'
        await update(40,claimed_by='999')
        count=other.send.await_count
        await ai.on_message(message(other,'Are you there?'))
        assert other.send.await_count==count,'AI replied after claim'

        for i in range(32):
            await ai.remember(20,'user',str(i))
        assert len(await ai.history(20))==16
        db=await get_db()
        try:
            async with db.execute('SELECT COUNT(*) FROM au_ai_messages WHERE thread_id=?',('20',)) as cursor:
                assert (await cursor.fetchone())[0]==24
        finally:
            await db.close()
        await ai.on_raw_thread_delete(NS(thread_id=20))
        assert await ai.history(20)==[]
        assert (await ai.state(20))['last_staff_ping']==0
        print('PASS: human takeover once, restart persistence, pending-answer suppression, waiting/finished silence, system/bot filtering, exclusions, verified answers, role handoff, isolated history, claim pause, API fallback and cleanup.')
    finally:
        await ai.cog_unload()


asyncio.run(run())

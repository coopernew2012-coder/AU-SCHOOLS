import os,sys,tempfile,asyncio,json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='au-functional-')
from database import init_db,get_db
from main import AUSchoolsBot
from cogs import applications as apps
from cogs.tickets import TicketControlView,TicketPanelView,UserPickView,CloseTicketModal
from cogs.dashboard import DashboardView

def interaction():
 return NS(guild=NS(id=123,get_member=lambda _:None,get_channel=lambda _:None),user=NS(id=456,mention='<@456>'),response=NS(defer=AsyncMock(),send_message=AsyncMock()),followup=NS(send=AsyncMock()),message=NS(edit=AsyncMock()),channel=NS(id=789,mention="<#789>",send=AsyncMock(),delete=AsyncMock(),set_permissions=AsyncMock()),edit_original_response=AsyncMock())

async def main():
 await init_db()
 async with AUSchoolsBot() as bot:
  for name in ['dashboard','admin_config','applications','autorole','moderation','roles','softban','staff','tickets','utility','verify','warnings','welcome']:
   await bot.load_extension('cogs.'+name)
  cog=bot.get_cog('TicketSystem')
  controls=TicketControlView(cog)
  assert controls.is_persistent() and DashboardView().is_persistent()
  assert len(controls.children)==8 and not any('AI' in item.label for item in controls.children)
  assert len(TicketPanelView().category_select.options)==5
  assert bot.tree.get_command('dashboard')
  # Two simultaneous review submissions may produce only one final decision.
  db=await get_db()
  await db.execute("INSERT INTO application_submissions(guild_id,app_name,user_id,submission_message_id,status,answers_json) VALUES ('123','Staff','999','222','pending','[]')")
  await db.commit();await db.close()
  original=apps.is_application_staff;apps.is_application_staff=AsyncMock(return_value=True)
  one,two=interaction(),interaction()
  await asyncio.gather(apps.finalize_decision(one,'approve',222,'Good application'),apps.finalize_decision(two,'approve',222,'Second click'))
  assert one.message.edit.await_count+two.message.edit.await_count==1
  apps.is_application_staff=original
  # User select uses direct values and rechecks staff access.
  fake=NS(is_ticket_staff=AsyncMock(return_value=True),ticket_log=AsyncMock())
  picker=UserPickView(fake,'add');i=interaction();i.data={'values':['999']};i.guild.get_member=lambda _:NS(id=999,mention='<@999>')
  await picker.pick_callback(i)
  i.channel.set_permissions.assert_awaited_once();i.edit_original_response.assert_awaited_once()
  fake.is_ticket_staff=AsyncMock(return_value=False);j=interaction()
  await picker.pick_callback(j);j.channel.set_permissions.assert_not_awaited()
  # If transcript creation fails, closing must not delete the channel.
  fake=NS(get_ticket=AsyncMock(return_value={'creator_id':'456','status':'open','category':'Support'}),_close_locks={},close_ticket_with_transcript=AsyncMock(side_effect=RuntimeError('transcript unavailable')))
  modal=CloseTicketModal(fake);modal.reason._value='Resolved';i=interaction()
  await modal.on_submit(i);i.channel.delete.assert_not_awaited();i.followup.send.assert_awaited_once()
  # Dashboard support navigation acknowledges early and offers a usable dropdown.
  d=DashboardView();d.navigate._values=['help'];i=interaction()
  await d.navigate.callback(i)
  i.response.defer.assert_awaited_once();assert i.followup.send.call_args.kwargs['ephemeral']
  assert len(i.followup.send.call_args.kwargs['view'].category_select.options)==5
  print('PASS: 13 cogs load; persistent views; five support options; atomic double-review protection; member picker and permission denial; transcript failure preserves channel; dashboard navigation.')
asyncio.run(main())


import asyncio,os,tempfile,sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock,AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='au-role-test-')
import discord
from access import *
from main import AUSchoolsBot,AccessDenied
from cogs.thread_tickets import ThreadTickets
class Role:
 def __init__(self,position):self.position=position
 def __ge__(self,other):return self.position>=other.position
roles={STAFF_REVIEW_ROLE:Role(26),TICKET_STAFF_ROLE:Role(18),MODERATION_ROLE:Role(21)}
guild=NS(id=1,owner_id=999,get_role=lambda rid:roles.get(rid))
def member(uid,position):
 m=MagicMock(spec=discord.Member);m.id=uid;m.guild=guild;m.top_role=Role(position);m.bot=False;return m
async def run():
 low,mod,admin,senior,higher=member(12,12),member(18,18),member(21,21),member(26,26),member(28,28)
 guild.members=[low,mod,admin,senior,higher]
 guild.get_member=lambda uid:next((m for m in guild.members if m.id==uid),None)
 assert ticket_staff(mod,'Support') and not ticket_staff(mod,'Staff Apply')
 assert not ticket_staff(admin,'Player/Staff Report') and ticket_staff(senior,'Staff Apply') and ticket_staff(higher,'Player/Staff Report')
 cog=ThreadTickets(NS(user=NS(id=900)))
 thread=NS(guild=guild,fetch_members=AsyncMock(return_value=[NS(id=x) for x in [42,900,18,26]]),add_user=AsyncMock(),remove_user=AsyncMock())
 await cog.sync_ticket_access(thread,{'kind':'Staff Apply','opener_id':'42'})
 assert [call.args[0].id for call in thread.add_user.call_args_list]==[28]
 assert [call.args[0].id for call in thread.remove_user.call_args_list]==[18]
 async with AUSchoolsBot() as client:
  for command_name,candidate,allowed in [('ban',mod,False),('ban',admin,True),('ban',senior,True),('close',low,False),('close',mod,True),('claim',mod,True),('partnerclose',mod,True),('ticket panel',mod,True),('partnership request-proof',mod,True)]:
   interaction=NS(user=candidate,guild=guild,namespace=NS(member=None,user=None),command=NS(qualified_name=command_name),response=NS(send_message=AsyncMock()))
   try:result=await client._access_check(interaction)
   except AccessDenied:result=False
   assert bool(result)==allowed
 print('PASS: role boundaries; reports/apps restricted; thread membership sync; moderation threshold gate.')
asyncio.run(run())

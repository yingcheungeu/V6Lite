"""Render item9 evidence in TEMP with FakeBroker, native callback doubles and no network."""
from pathlib import Path
import sys,json,tempfile,logging,socket,asyncio
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from v6lite.tests.test_batch22_item9_broker_cancel import _pending_three,_broker_cancel,_drive,REASON,LONG_REASON
from ib_insync import IB
try: asyncio.get_event_loop_policy().get_event_loop()
except RuntimeError: asyncio.set_event_loop(asyncio.new_event_loop())
def forbidden(*args,**kwargs):raise RuntimeError('network forbidden in item9 samples')
async def forbidden_async(*args,**kwargs):forbidden()
socket.socket.connect=forbidden; socket.socket.connect_ex=forbidden
IB.connect=forbidden; IB.connectAsync=forbidden_async
logging.disable(logging.CRITICAL)
out=Path(__file__).resolve().parent
scenarios=[]
for label,shape,reason in [('complete_three_sources','complete',REASON),('terminal_metadata_missing','metadata_cleared',REASON),('long_original_reason','complete',LONG_REASON)]:
 tmp=Path(tempfile.mkdtemp(prefix='v6lite_item9_cards_'))
 rig,pid=_pending_three(tmp)
 _broker_cancel(rig,pid,shape=shape,reason=reason)
 _drive(rig)
 messages=[{'title':title,'body':body} for title,body in rig.channel.messages if 'P0' in title]
 scenarios.append({'scenario':label,'synthetic':True,'protection_type':'STP','broker_connections':0,'messages':messages})
 rig.store.close()
(out/'card_samples.json').write_text(json.dumps(scenarios,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['# 第22批第9条：FakeBroker实际渲染样例','', '全部来自合成3股入场、原生callback double和MemoryChannel，状态在TEMP；不是10/9真实账户日志。','']
for row in scenarios:
 lines += ['## '+row['scenario'],'']
 for msg in row['messages']:lines += ['```text',msg['body'],'```','']
(out/'card_samples.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'synthetic':True,'scenarios':len(scenarios),'P0_cards':sum(len(s['messages']) for s in scenarios)},ensure_ascii=False))

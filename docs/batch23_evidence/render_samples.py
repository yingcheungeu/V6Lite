"""Actual B23 FakeBroker cards, captured before JSON/Markdown rendering."""
from pathlib import Path
from datetime import datetime
import asyncio,json,logging,socket,sys
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
from run_batch23_checks import sha,package_sha
assert not any((EV/p).exists() for p in ('card_samples.json','card_samples.md','card_samples_receipt.json')),'card artifacts already sealed'
source_before=package_sha(ROOT/'v6lite')
render_before=sha(Path(__file__))
specs_before=[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in sorted((ROOT/'v6lite/tests').glob('test_batch23*.py'))]
sys.path.insert(0,str(ROOT))
try:asyncio.get_event_loop_policy().get_event_loop()
except RuntimeError:asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB
from v6lite.tests.test_batch23_tp_evidence import (_working,_activate_tp,_terminal_tp,_native_status,_deliver_status,_settle,_begin_tp,_set_book,_safety,_restart,PRICE_REASON)
from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
from v6lite.broker.fake import FakeBroker
from v6lite.models import Purpose,ProtectionRequest

def forbidden(*a,**k):raise AssertionError('No network or broker connection in B23 card samples')
async def forbidden_async(*a,**k):forbidden()
socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden
IB.connect=forbidden;IB.connectAsync=forbidden_async
logging.disable(logging.CRITICAL)
tmp=EV/'sample_tmp'/datetime.now().strftime('%Y%m%dT%H%M%S%f');tmp.mkdir(parents=True)
scenarios=[]

def capture(label,rig,pid,channel,extra=None):
 _safety(rig)
 pos=rig.store.position(pid);protection=rig.store.protection(pid)
 p=sum(int(x.quantity) for x in FakeBroker.positions_fresh(rig.broker) if x.symbol==pos['symbol'])
 s=t=x=0
 for ref,o in rig.broker.orders.items():
  if o.req.symbol!=pos['symbol'] or o.status in {'Cancelled','ApiCancelled','Inactive','Filled'}:continue
  if isinstance(o.req,ProtectionRequest):s+=max(0,int(o.remaining))
  elif str(o.req.side)=='SELL':
   if o.req.purpose in {Purpose.TP1,Purpose.TP2,Purpose.TP3}:t+=max(0,int(o.remaining))
   else:x+=max(0,int(o.remaining))
 result={'scenario':label,'synthetic':True,'broker_connections':0,'symbol':pos['symbol'],'oracle_exchange_truth':{'P':p,'S':s,'T':t,'X':x,'not_covered_by_STP':max(0,p-s)},'local_protection_state':protection['state'] if protection else None,'card_evidence_note':'oracle exchange truth is test-only context; each actual card uses only evidence available to the engine','accepted_embeds':channel.accepted,'messages':[{'title':a,'body':b} for a,b in channel.messages], 'extra':extra or {}}
 assert result['messages'] and len(channel.accepted)==len(channel.messages),label+' must capture actual acknowledged rendered cards'
 assert p>0 and s+t+x<=p
 scenarios.append(result);rig.store.close()

rig,pid=_working(tmp/'protect_cancel',qty=3);channel=_card_channel(rig)
ref=rig.store.protection(pid)['intent_id'];rig.broker.orders[ref].status='Cancelled'
adapter,trade=_native_status(rig,ref,raw='Cancelled',code=202,reason='Order canceled by system: synthetic broker cancellation')
_deliver_status(rig,adapter,trade);_settle(rig)
capture('保护单被券商取消：合成3股',rig,pid,channel)

rig,pid=_working(tmp/'tp_price',qty=6);req=_activate_tp(rig,pid);channel=_card_channel(rig)
_terminal_tp(rig,req,reason=PRICE_REASON)
capture('TP价格被拒：合成6股，完整原因多段',rig,pid,channel,{'tp_quantity':req.quantity,'sent_limit':req.limit_price,'original_reason':PRICE_REASON})

rig,pid=_working(tmp/'persistent_pause',qty=8);req=_activate_tp(rig,pid);channel=_card_channel(rig)
_terminal_tp(rig,req,reason='Order rejected - reason: Synthetic price outside the allowed price range.')
p0_snapshot={'P':8,'S':sum(max(0,int(o.remaining)) for o in rig.broker.orders.values() if isinstance(o.req,ProtectionRequest) and o.status not in {'Cancelled','ApiCancelled','Inactive','Filled'}),'T':0,'X':0}
p0_snapshot['not_covered_by_STP']=8-p0_snapshot['S']
_settle(rig)
for _ in range(4):
 row=rig.store.protection(pid)
 if row['intent_id'] in rig.broker.protection_pending_modifications:rig.broker.confirm_protection_modify(row['intent_id'])
 _settle(rig)
pause=rig.store.control('tp_pause:'+pid);assert pause
assert rig.store.protection(pid)['state']=='WORKING'
assert rig.store.protection(pid)['confirmed_remaining']==8
sent_before=len(rig.broker.sent)
_restart(rig,tmp/'persistent_pause')
assert rig.store.control('tp_pause:'+pid)==pause
rig.advance(seconds=31);_set_book(rig);rig.engine._evaluate_exits(rig.clock.now());rig.engine._run_queue();_settle(rig)
assert len(rig.broker.sent)==sent_before
post_restart_messages=[{'title':a,'body':b} for a,b in rig.channel.messages]
assert not any('止盈' in m['title'] and ('P0' in m['title'] or 'P1' in m['title']) for m in post_restart_messages),'restart emitted a duplicate TP card'
capture('自动止盈暂停：合成8股，恢复全仓STP后健康重启仍不重发',rig,pid,channel,{'exchange_snapshot_when_P0_rendered':p0_snapshot,'persisted_pause':pause,'healthy_restart':True,'post_restart_observed_messages':post_restart_messages,'new_TP_orders_after_restart':0,'pause_card_policy':'Reuse the actual acknowledged price-rejection P0 describing persistent pause; no lower-priority duplicate pause card.'})

rig,pid=_working(tmp/'price_mismatch',qty=4);req=_activate_tp(rig,pid);channel=_card_channel(rig)
adapter,trade=_native_status(rig,req.intent_id,working_price=req.limit_price+.25,cap=req.limit_price+.10)
_deliver_status(rig,adapter,trade)
capture('TP工作价格不一致：合成4股',rig,pid,channel,{'recorded_limit':req.limit_price,'broker_working_limit':req.limit_price+.25,'broker_cap':req.limit_price+.10})

rig,pid=_working(tmp/'second_stale',qty=5);rig.broker.protection_auto_status=False
_begin_tp(rig,pid);ref=rig.store.protection(pid)['intent_id'];channel=_card_channel(rig)
rig.broker.confirm_protection_modify(ref);_set_book(rig,age=3.01);_settle(rig)
assert not [r for r in rig.broker.sent if r.purpose in {Purpose.TP1,Purpose.TP2,Purpose.TP3}]
assert rig.store.control('tp_pause:'+pid) is None
capture('第二次报价检查过期：合成5股，TP未发出并恢复保护',rig,pid,channel,{'quote_age_seconds':3.01,'TP_sent':False,'price_rejection_pause':False})

(EV/'card_samples.json').write_text(json.dumps(scenarios,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['# 第23批实际合成场景卡片','', '全部来自FakeBroker、本地native回调double和实际Alerter/Channel。临时合成状态位于dev；不是John账户日志。','']
for scenario in scenarios:
 lines += ['## '+scenario['scenario'],'','场景完成后的测试真值（不替代卡片渲染时取得的券商证据）：'+json.dumps(scenario['oracle_exchange_truth'],ensure_ascii=False),'']
 if scenario['extra'].get('exchange_snapshot_when_P0_rendered'):lines += ['该P0渲染时的测试真值：'+json.dumps(scenario['extra']['exchange_snapshot_when_P0_rendered'],ensure_ascii=False),'恢复全仓STP及成功重启后没有另发同事件低级别暂停卡。','']
 for message in scenario['messages']:lines += ['```text',message['body'],'```','']
(EV/'card_samples.md').write_text('\n'.join(lines).rstrip()+'\n',encoding='utf-8')
assert package_sha(ROOT/'v6lite')==source_before and sha(Path(__file__))==render_before
receipt={'code_sha':source_before,'source_unchanged':True,'render_sha256':render_before,'specs':specs_before,'card_json_sha256':sha(EV/'card_samples.json'),'card_markdown_sha256':sha(EV/'card_samples.md'),'scenarios':len(scenarios),'actual_acknowledged_cards':sum(len(s['accepted_embeds']) for s in scenarios),'ack_kind':'local synchronous channel double; no actual Discord/network','broker_connections':0}
(EV/'card_samples_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'scenarios':len(scenarios),'actual_cards':sum(len(s['messages']) for s in scenarios),'synthetic':True,'broker_connections':0}))

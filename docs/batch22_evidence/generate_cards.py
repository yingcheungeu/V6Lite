"""Offline FakeBroker rendering samples; all state and channels are TEMP."""
from __future__ import annotations
import json, socket, tempfile, sys
from pathlib import Path
from dataclasses import asdict
from datetime import date
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from v6lite.tests.helpers import Rig
from v6lite.config import ProtectionConfig
from v6lite.report import daily_report

def blocked(*args,**kwargs): raise AssertionError("network prohibited")
socket.socket.connect=blocked
socket.socket.connect_ex=blocked
KINDS=[("P1","gap A warning"),("P0","gap A overdue"),("P0","gap B overdue"),
 ("P0","preflight B overdue"),("P0","external sell"),("P0","rejected"),
 ("P0","unknown"),("P1","tp not sent"),("P1","trigger crossing"),
 ("P0","trigger unfilled"),("P0","trigger suspected"),("P0","invalid price"),
 ("P1","invalid entry price"),("P0","evidence incomplete"),("P0","shutdown incomplete"),
 ("P1","late tp fill"),("P0","legacy type")]

def main():
 out=[]
 with tempfile.TemporaryDirectory(prefix="v6lite_B22_cards_") as temp:
  for index,(level,kind) in enumerate(KINDS):
   rig=Rig(Path(temp)/str(index),protection=ProtectionConfig())
   rig.open_aapl(); pos=rig.store.open_positions()[0];row=dict(rig.store.protection(pos['position_id']))
   captures=[];original=rig.alerter.send
   def capture(now,level,subject,body="",**kwargs):
    if kwargs.get('card'): captures.append(asdict(kwargs['card']))
    return original(now,level,subject,body,**kwargs)
   rig.alerter.send=capture
   if kind=='rejected':row.update(state='REJECTED',confirmed_remaining=0,pending_max_remaining=0)
   if kind=='legacy type':row.update(order_type='STP LMT',limit_price=105.21)
   rig.engine._protection_page(rig.clock.now(),level,kind,row,"FakeBroker 合成渲染样例；不是实际券商事件")
   assert captures,(kind,rig.channel.messages)
   out.append(dict(kind=kind,level=level,card=captures[-1]))
   rig.alerter.close();rig.store.close();rig.broker.disconnect()
  rig=Rig(Path(temp)/'slippage',protection=ProtectionConfig());rig.open_aapl()
  row=dict(rig.store.protection(rig.store.open_positions()[0]['position_id']))
  for qty,offset in [(1,-1.25),(2,.50)]:
   fill=rig.broker.fill(row['intent_id'],qty=qty,price=row['trigger_price']+offset,emit=False)
   rig.engine._ingest_fill(rig.clock.now(),fill)
  report=daily_report(rig.store,rig.cfg,date(2026,10,6))
  events=[json.loads(r['payload']) for r in rig.store.conn.execute("SELECT payload FROM audit WHERE kind='PROTECT_FILL' ORDER BY id")]
  rig.alerter.close();rig.store.close();rig.broker.disconnect()
 (ROOT/'docs/batch22_evidence/cards.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 text=['# FakeBroker 合成保护卡片（只渲染，非实际事件）','']
 for item in out:
  text.extend(['## '+item['kind']+' / '+item['level'],'',item['card']['title'],''])
  for name,value in item['card']['fields']:text.extend([name+'：'+value,''])
 (ROOT/'docs/batch22_evidence/cards.md').write_text('\n'.join(text),encoding='utf-8')
 (ROOT/'docs/batch22_evidence/slippage_sample.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
 (ROOT/'docs/batch22_evidence/daily_sample.md').write_text(report,encoding='utf-8')
 print('FakeBroker cards='+str(len(out))+' protection executions='+str(len(events)))
if __name__=='__main__': main()

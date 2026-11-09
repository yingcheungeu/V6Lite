"""Render actual Batch20 cards through FakeBroker; all state is disposable TEMP."""
from pathlib import Path
import json, sys, tempfile
sys.path.insert(0,str(Path.cwd()))
from v6lite.broker.fake import FakeBroker
from v6lite.config import ProtectionConfig
from v6lite.models import BrokerAck, Purpose
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch20_protection_incident import _wire, _deliver_wire, REJECT_ZH, _page_unknown
from v6lite.tests.test_batch20_protection_equivalents import _retired_gtc_pair, _retired_native_fill
from v6lite.version import code_sha
E=Path("docs/batch20_evidence"); before=code_sha(); samples=[]; rigs=[]
def three(path,pending=False):
    rig=Rig(path,protection=ProtectionConfig(enabled=True));rigs.append(rig)
    if pending:
        rig.broker.protection_auto_status=False;submit=rig.broker.submit_stop_limit
        def early(req): return BrokerAck(submit(req).broker_order_id,None)
        rig.broker.submit_stop_limit=early
    rig.start();rig.at(et(10,0,30));rig.tick()
    entry=next(r for r in rig.broker.sent if r.purpose==Purpose.ENTRY)
    rig.engine._on_urgent_broker_event(rig.broker.fill(entry.intent_id,qty=3,emit=False))
    if not pending: rig.advance(seconds=1);rig.tick()
    [pos]=rig.store.open_positions();assert pos["qty_open"]==3
    rig.channel.messages.clear();return rig,pos["position_id"]
def plaintext(title,body): return body if body.startswith(title) else title+"\n"+body
def capture(rig,label,predicate=None):
    title,body=next((t,b) for t,b in reversed(rig.channel.messages) if predicate is None or predicate(t,b));samples.append(dict(scenario=label,text=plaintext(title,body)))
with tempfile.TemporaryDirectory(prefix="v6lite_B20_cards_") as tmp:
    root=Path(tmp);rig,pid=three(root/"coverage")
    rig.store.update_protection(rig.clock.now(),pid,state="UNKNOWN")
    _page_unknown(rig,pid,"GTC absent from complete working snapshot; no duplicate submission");capture(rig,"同一保护事件首次 P0")
    rig.advance(minutes=30);_page_unknown(rig,pid,"same condition, unchanged");capture(rig,"30分钟仍未解决 P1")
    row=rig.store.protection(pid)
    rig.store.update_protection(rig.clock.now(),pid,state="MODIFY_PENDING",pending_qty=row["requested_qty"],pending_trigger=row["trigger_price"],pending_limit=row["limit_price"],status_seen_generation=row["modify_generation"])
    rig.engine._protection_progress(rig.clock.now());assert rig.store.protection(pid)["state"]=="WORKING"
    recovered=rig.channel.messages[-1];assert "解除" in recovered[0]
    rejection,rpid=three(root/"rejection",True)
    rejection.engine._working={o.intent_id:o for o in rejection.broker.open_orders()};rejection.engine._working_at=rejection.clock.now()
    adapter,trade=_wire(rejection,rpid,raw="Cancelled",message=REJECT_ZH,code=202);_deliver_wire(rejection,adapter,trade)
    assert rejection.store.protection(rpid)["state"]=="REJECTED"
    capture(rejection,"完整 native 202 明确拒单 P0")
    samples.append(dict(scenario="新鲜三项证据确认恢复 INFO",text=plaintext(*recovered)))
    retired,pos,old,new=_retired_gtc_pair(root/"retired");rigs.append(retired);retired.channel.messages.clear()
    retired.engine._ingest_fill(retired.clock.now(),_retired_native_fill(retired,old,5));capture(retired,"旧 GTC 迟到成交，替代保护待确认 P0",lambda title,body: "P0" in title and "旧 GTC" in body)
    for r in rigs:r.store.close()
(E/"cards_samples.json").write_text(json.dumps(samples,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
md="\n".join("### "+s["scenario"]+"\n\n```text\n"+s["text"]+"\n```\n" for s in samples)
(E/"cards_samples.md").write_text(md,encoding="utf-8",newline="\n")
assert before==code_sha();print(json.dumps(dict(samples=len(samples),code_sha=before),ensure_ascii=False))

"""TEMP-only signal/shutdown child. Never creates an IBKR broker."""
import json, os, signal, sys, time
from pathlib import Path
root,work,mode=map(str,sys.argv[1:4]);sys.path.insert(0,root)
from v6lite.approval import AccountLock
from v6lite.config import ProtectionConfig
from v6lite.models import IntentStatus, OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig
from v6lite.__main__ import run_engine
work=Path(work);events=[]
def event(name,**data):
    events.append(dict(name=name,at=time.time(),**data))
    (work/'events.json').write_text(json.dumps(events,indent=2),encoding='utf-8')
lock=AccountLock('synthetic-console-only',lock_dir=work/'locks');lock.acquire()
rig=Rig(work/'rig',dry_run=mode=='dry',protection=ProtectionConfig(enabled=True))
if mode=='dry':rig.start()
else:
    rig.open_aapl()
    req=OrderRequest('v6l-console-day','TSLA',Side.BUY,1,250,Purpose.ENTRY,'pos-console-day')
    rig.store.create_intent(rig.clock.now(),req,atr=2,count_entry_day=None)
    ack=rig.broker.submit_limit(req)
    rig.store.update_intent(rig.clock.now(),req.intent_id,status=IntentStatus.SENT,broker_order_id=ack.broker_order_id,perm_id=ack.perm_id)
stop={'flag':False,'count':0}
def request_stop(*_):stop['flag']=True;stop['count']+=1;event('SIGNAL',count=stop['count'])
signal.signal(signal.SIGINT,request_stop)
if hasattr(signal,'SIGBREAK'):signal.signal(signal.SIGBREAK,request_stop)
pump=rig.broker.pump
def simulated_transport_pump(seconds):
    time.sleep(.03);rig.clock.advance(seconds=seconds)
    for ref,order in list(rig.broker.orders.items()):
        if ref in rig.broker.cancels and order.status not in {'Cancelled','ApiCancelled','Inactive'}:rig.broker.confirm_cancel(ref)
    return pump(seconds)
rig.broker.pump=simulated_transport_pump
shutdown=rig.engine.shutdown
original_disconnect=rig.broker.disconnect
def disconnect():
    row=rig.store.conn.execute("SELECT payload FROM audit WHERE kind='SHUTDOWN' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None;event('SHUTDOWN_AUDIT_WRITTEN',payload=json.loads(row['payload']))
    original_disconnect();event('DISCONNECT_DONE')
rig.broker.disconnect=disconnect
def delayed_shutdown(*a,**kw):
    event('SHUTDOWN_BEGIN');time.sleep(1.5);shutdown(*a,**kw)
    row=rig.store.conn.execute("SELECT payload FROM audit WHERE kind='SHUTDOWN' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    audit=json.loads(row['payload']);event('SHUTDOWN_AUDIT_CHECKED',payload=audit)
    if mode=='dry':
        assert not rig.broker.sent and not rig.broker.sent_protections and not rig.broker.cancels
    else:
        assert 'v6l-console-day' in rig.broker.cancels
        ids={r['intent_id'] for r in rig.store.protections()}
        assert ids and not ids.intersection(rig.broker.cancels)
        assert all(rig.broker.orders[i].remaining==20 for i in ids)
        assert audit['unresolved']==[] and audit['broker_working']==[]
        event('DAY_CANCELLED_GTC_RETAINED',gtc_remaining=20)
rig.engine.shutdown=delayed_shutdown
(work/'ready.json').write_text(json.dumps({'pid':os.getpid(),'mode':mode,'unbuffered_is_one':os.environ.get('PYTHONUNBUFFERED')=='1'}),encoding='utf-8');event('READY',pid=os.getpid())
try:
    run_engine(rig.engine,rig.broker,should_stop=lambda:stop['flag'])
finally:
    rig.alerter.close();event('ALERTER_CLOSED')
    rig.store.close();event('STORE_CLOSED')
    lock.release();assert not lock.held;event('LOCK_RELEASED')
assert stop['count']==1
with (work/'rig/state/v6lite.sqlite3').open('rb'):pass
(work/'child_done.json').write_text(json.dumps({'pid':os.getpid(),'signals':stop['count'],'mode':mode}),encoding='utf-8')
event('CHILD_FINAL');print('SYNTHETIC_SHUTDOWN_COMPLETE',flush=True)

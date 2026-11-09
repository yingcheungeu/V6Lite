"""10/29 offline TWS cancel, real restart and legacy native completed decoder.

Business sequence and known 2890 / 753795445 identities match the Addendum.
Account/orderRef/exec IDs and completed wire time are explicit test aliases;
no real Paper completed wire export was supplied. Native orderId stays 0.
"""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import pytest
from v6lite.broker.fake import FakeBroker
from v6lite.models import BrokerAck, IntentStatus, OrderRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import et
from v6lite.tests.test_batch32_open_orders import oct29, _pending_BE
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _restart
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _audits
from v6lite.tests.test_batch16_f6_release import _request_release


@pytest.fixture
def historical_perm_binding(monkeypatch):
    # Only the offline broker's deterministic ID allocation changes. Identity
    # must still be learned through normal submit and strong native callbacks.
    snapshot=FakeBroker._open_order_snapshot
    def mapped(self,ref,order):
        value=snapshot(self,ref,order)
        return replace(value,perm_id=753795445) if value.broker_order_id==2890 else value
    monkeypatch.setattr(FakeBroker,'_open_order_snapshot',mapped)
    status=FakeBroker.status
    def native_status(self,*args,**kwargs):
        result=status(self,*args,**kwargs)
        self.events=[replace(e,perm_id=753795445) if getattr(e,'broker_order_id',None)==2890 else e for e in self.events]
        return result
    monkeypatch.setattr(FakeBroker,'status',native_status)
    fill=FakeBroker.fill
    def native_fill(self,*args,**kwargs):
        event=fill(self,*args,**kwargs)
        if event.broker_order_id==2890:
            event=replace(event,perm_id=753795445)
            self.orders[event.intent_id].fills[-1]=event
        return event
    monkeypatch.setattr(FakeBroker,'fill',native_fill)
    for name in ('submit_stop','modify_protection'):
        method=getattr(FakeBroker,name)
        def send(self,*args,_method=method,**kwargs):
            ack=_method(self,*args,**kwargs)
            return BrokerAck(ack.broker_order_id,753795445) if ack.broker_order_id==2890 else ack
        monkeypatch.setattr(FakeBroker,name,send)


@pytest.fixture
def offline_oct29(historical_perm_binding,oct29,tmp_path,monkeypatch,request):
    rig,pid,wire=oct29
    pending=getattr(request,'param',None)=='pending'
    _pending_BE(rig,pid,wire,monkeypatch,(lambda row:None) if pending else (lambda row:wire.ack()))
    protection=dict(rig.store.protection(pid));ref=protection['intent_id']
    assert protection['state']==('PRICE_PENDING' if pending else 'WORKING') and protection['requested_qty']==2
    assert protection['trigger_price']==(340.58 if pending else 344.57) and protection['broker_order_id']==2890
    assert protection['perm_id']==753795445 and protection['confirmed_at']
    assert rig.store.intent(ref)['status']==IntentStatus.SENT
    rig.at(et(11,49,9,day=(2026,10,29)));rig.px('AAPL',345.)
    rig.engine.shutdown(grace_seconds=0,source='SIGINT')
    assert _audits(rig,'SHUTDOWN')[-1]['source']=='SIGINT'
    # An already-sent modification can reach TWS after shutdown with no ACK.
    if pending:wire.apply_pending()
    # Offline TWS cancel: discard every event before a new engine exists.
    rig.broker.confirm_cancel(ref);rig.broker.drain_events()
    rig.at(et(11,50,day=(2026,10,29)))
    external=[]
    for name,side,qty in [('external-sell100',Side.SELL,100),('external-buy98',Side.BUY,98)]:
        req=OrderRequest(name,'AAPL',side,qty,345.,Purpose.TP1 if side is Side.SELL else Purpose.ENTRY)
        rig.broker._register(req);external.append(rig.broker.fill(name,price=345.,emit=False))
        rig.advance(seconds=1)
    assert not FakeBroker.positions_fresh(rig.broker)
    # Manual TWS executions are client 0: visible to the account query, never
    # injected into Lite's client-scoped query. Preserve actual wire filters.
    harness=wire.harness
    original_rows=harness.rows
    def scoped_rows():
        rows=original_rows()
        filt=harness.filters[-1][1] if harness.filters else None
        return [f for f in rows if f.intent_id.startswith('v6l-')] if filt and filt.clientId else rows
    harness.rows=scoped_rows
    original_decode=harness.decode
    def decode(fill,*args,**kwargs):
        saved=harness.cfg
        if not fill.intent_id.startswith('v6l-'):harness.cfg=replace(saved,client_id=0)
        try:return original_decode(fill,*args,**kwargs)
        finally:harness.cfg=saved
    monkeypatch.setattr(harness,'decode',decode)
    path,levels=rig.store.path,rig.store._levels_fn
    rig.alerter.close();rig.store.close();rig.store=Store(path,levels)
    rig.at(et(12,14,4,day=(2026,10,29)))
    _restart(rig,tmp_path,monkeypatch)
    assert rig.store.position(pid)['status']=='OPEN' and rig.store.position(pid)['qty_open']==2
    assert rig.store.intent(ref)['status']==IntentStatus.SENT
    rig.store.set_control(rig.clock.now(),'frozen:AAPL','manual_flattened_in_TWS')
    data=json.loads(Path(__file__).with_name('fixtures').joinpath('b32_completed_native_fields.json').read_text())
    assert 'o.orderId' not in data['field_names'] and 'o.clientId' not in data['field_names']
    fields=list(data['fields']);fields[0]='101'
    values={'c.conId':str(protection['con_id']),'c.symbol':'AAPL','c.secType':'STK','c.currency':'USD',
        'o.account':rig.cfg.account,'o.orderRef':ref,'o.permId':'753795445','o.action':'SELL',
        'o.orderType':'STP','o.tif':'GTC','o.totalQuantity':'2','o.auxPrice':'344.57',
        'o.filledQuantity':'0','st.status':'Cancelled','st.completedTime':'20261029 15:50:00 UTC'}
    for key,value in values.items():fields[data['field_names'].index(key)]=value
    ib=wire.ib;ib.client.decoder.serverVersion=data['server_version']
    responses=[fields]
    def completed_query(apiOnly):
        assert apiOnly is False
        future=ib.wrapper.startReq('completedOrders')
        ib.client.reqCompletedOrders(apiOnly);wire.harness.loop.drain()
        for raw in responses:ib.client.decoder.interpret(list(raw))
        ib.wrapper.completedOrdersEnd();assert future.done()
        return future.result()
    monkeypatch.setattr(ib,'reqCompletedOrders',completed_query)
    def completed():
        result=wire.adapter.completed_orders()
        rig.broker.query_generation=wire.adapter.query_generation
        return result
    monkeypatch.setattr(rig.broker,'completed_orders',completed,raising=False)
    return rig,pid,wire,ref,external,responses,data


def test_B32A_real_offline_cancel_release_accepts_native_perm_and_persisted_order_binding(offline_oct29,record_property):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    before=tuple((r.intent_id,r.purpose) for r in rig.broker.sent)
    _request_release(rig)
    refusals=_audits(rig,'RELEASE_REFUSED')
    assert rig.store.position(pid)['status']=='RELEASED',refusals
    assert rig.store.control('frozen:AAPL') is None
    assert rig.store.risk_day('2026-10-29')['daily_stop_latched']==1
    assert not any(rig.store.conn.execute('SELECT 1 FROM fills WHERE exec_id=?',(e.exec_id,)).fetchone() for e in external)
    [proof]=_audits(rig,'PROTECT_CANCEL_PROVEN_COMPLETED')
    assert proof['native_order_id_absent'] is True
    assert proof['persisted_order_id']==2890 and proof['persisted_perm_id']==753795445
    assert proof['native_order_id'] is None and proof['native_client_id_absent'] is True
    assert proof['filled']==0 and proof['status']=='Cancelled'
    assert rig.store.intent(ref)['cancel_seen_at'] is None,'no synthesized online cancel callback'
    assert tuple((r.intent_id,r.purpose) for r in rig.broker.sent)==before
    assert ref not in rig.broker.cancels
    record_property('offline_cancel_proof',json.dumps(proof,ensure_ascii=False))

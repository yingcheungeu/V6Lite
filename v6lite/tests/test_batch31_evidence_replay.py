"""Known 10/27 tape: zoned TP time and query-bound 107.325ms raw ACK.

Unlogged native fields are explicit normal-working assumptions, not findings.
No proof or position is seeded; actual prior-day entry, Store reopen and
restoration precede TP1 and the real installed SDK's modification/query End.
"""
from dataclasses import replace
from datetime import timedelta
import pytest
from ib_insync import IB,Order,OrderStatus
from v6lite.broker.fake import FakeBroker
from v6lite.models import PriceObservationEvent
from v6lite.tests.helpers import et,Rig
from v6lite.config import TestConfig as LiteTestConfig
from v6lite.tests import test_batch28b_overnight_refresh as preparation
from v6lite.tests.test_batch26_overnight_evidence import _no_network,_safety,_tp_requests
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run,_audits,_tick_until
from v6lite.tests.test_batch28c_price_confirmation import NativePriceWire
from v6lite.tests.test_batch31_overnight import pending_be,tp2

@pytest.fixture
def evidence_run(overnight_run,monkeypatch):
    def paper_test_rig(directory,**kwargs):
        kwargs.update(client_id=132,state_dir=str(directory/'paper-test'),test=LiteTestConfig(enabled=True))
        return Rig(directory,**kwargs)
    monkeypatch.setattr(preparation,'Rig',paper_test_rig)
    rig,pid,harness=overnight_run(entry_day=(2026,10,26),tp_day=(2026,10,27),
        entry_price=343.47,stop=339.62,tp1=344.75,tp2=345.71,tp3=346.68,
        first_id=2799,entry_commission=1.003719,client_id=132,con_id=265598)
    wire=NativePriceWire(rig,pid,harness,monkeypatch)
    native=wire.native
    def generic(value):
        contract,order,state=native(value)
        if value.order_type=='STP':
            order=Order(orderId=value.broker_order_id,permId=value.perm_id,
                clientId=value.client_id,account=value.account,orderRef=value.intent_id,
                action='SELL',totalQuantity=float(value.total_quantity),orderType='STP',
                lmtPrice=0.,auxPrice=value.aux_price,tif='GTC',outsideRth=False,
                trailStopPrice=339.62,ocaType=3,displaySize=2147483647,clearingIntent='IB')
            contract.right='?'
        return contract,order,state
    monkeypatch.setattr(wire,'native',generic)
    # Rebuild the SDK stop through real connection-snapshot callbacks, not by
    # manufacturing a complete Trade/OrderStatus in the cache.
    row=rig.store.protection(pid)
    original=rig.broker._open_order_snapshot(row['intent_id'],rig.broker.orders[row['intent_id']])
    key=wire.ib.wrapper.orderKey(original.client_id,original.broker_order_id,original.perm_id)
    wire.ib.wrapper.trades.pop(key,None);wire.ib.wrapper.permId2Trade.pop(original.perm_id,None)
    wire.adapter._trades.pop(original.intent_id,None)
    wire.adapter._connection_syncing=True
    try:
        wire.open_order(original)
        recovered=wire.ib.wrapper.trades[key]
        assert type(recovered.order) is Order
        assert recovered.orderStatus.permId==0 and recovered.orderStatus.clientId==0
        wire.ib.wrapper.orderStatus(original.broker_order_id,'PreSubmitted',0.,3.,0.,
            original.perm_id,0,0.,132,'trigger',0.)
    finally:wire.adapter._connection_syncing=False
    rig.engine._drain(rig.clock.now())
    assert recovered.orderStatus.remaining==3. and recovered.orderStatus.clientId==132
    wire.ib.wrapper.clientId=132;wire.ib.client.clientId=132
    monkeypatch.setattr(wire.ib.client,'placeOrder',lambda *args:None)
    monkeypatch.setattr(wire.ib,'placeOrder',IB.placeOrder.__get__(wire.ib,IB))
    # Actual callback is unverified hyphen form; own execution downloads are
    # zoned as in audit #132138 before the BE request, never naive-only.
    harness.raw=lambda f:f.time.strftime('%Y%m%d %H:%M:%S UTC')
    original_fill=rig._b28b_callback_fill
    def callback(*args,**kwargs):
        query_raw=harness.raw;harness.raw=lambda f:f.time.strftime('%Y%m%d-%H:%M:%S')
        try:return original_fill(*args,**kwargs)
        finally:harness.raw=query_raw
    monkeypatch.setattr(rig,'_b28b_callback_fill',callback)
    rig.at(et(9,33,19,day=(2026,10,27)))
    rig.broker.set_quote('AAPL',bid=344.71,ask=344.85,last=344.91,ts=rig.clock.now())
    assert rig.store.control('overnight_tp_restored:'+pid)
    return rig,pid,wire


def test_B31_evidence_query_bound_ACK_confirms_only_at_own_openOrders_End(evidence_run,monkeypatch,record_property):
    rig,pid,wire=evidence_run;observed=[];states=[];projection=[]
    deliver=wire.deliver
    def capture(event):
        if isinstance(event,PriceObservationEvent):observed.append(event)
        return deliver(event)
    wire.deliver=capture;wire.adapter.set_urgent_handler(capture)
    def response(row):
        assert rig.store.control('execution_time_unverified:b31-tp1.01.01') is None
        req=rig.engine._protection_price_requests[pid][1]
        status_count=len(wire.status_callbacks)
        first_observation=len(observed)
        query_impl=wire.ib.reqAllOpenOrders
        def query():
            future=wire.ib.wrapper.startReq('openOrders')
            wire.ib.client.reqAllOpenOrders();wire.harness.loop.drain()
            arrival=req+timedelta(microseconds=107325)
            assert rig.clock.now()<=arrival,'pacing cannot move the scripted ACK into the past'
            rig.at(arrival)
            wire.apply_pending()
            before=len(observed)
            for value in FakeBroker.open_orders(rig.broker):wire.open_order(value)
            raw=[e for e in observed[before:] if e.source=='openOrder']
            assert raw and all(e.query_snapshot and not e.connection_snapshot for e in raw)
            assert rig.store.protection(pid)['state']=='PRICE_PENDING','a partial query row has no complete-snapshot authority'
            rig.advance(microseconds=2000);wire.ib.wrapper.openOrderEnd()
            assert future.done()
            wire.ends.append(('openOrders',rig.clock.now(),wire.adapter.query_generation))
            return future.result()
        monkeypatch.setattr(wire.ib,'reqAllOpenOrders',query)
        rig.advance(microseconds=1)  # own request starts strictly after the BE write
        try:
            own=rig.broker.open_orders()
            states.append(rig.store.protection(pid)['state'])
            projection.extend((e.source,e.mkt_cap_price_raw) for e in observed[first_observation:] if
                e.identity.intent_id==row['intent_id'] and getattr(e,'owned_open_orders',None) is None)
            assert len(own)==1 and own[0].total_quantity==2 and own[0].aux_price==343.64
        finally:monkeypatch.setattr(wire.ib,'reqAllOpenOrders',query_impl)
        assert len(wire.status_callbacks)==status_count,'BE has no orderStatus'
    pending_be(rig,pid,wire,at_request=response)
    assert states==['WORKING'],'an owned complete openOrders snapshot must acknowledge the already-issued BE without TP permission'
    assert projection[:2]==[('openOrder',''),('openOrders','0.0')]
    confirms=_audits(rig,'PROTECT_PRICE_CONFIRMED')
    assert any(a['generation']==2 and a['source']=='own complete openOrders snapshot' for a in confirms)
    assert not _audits(rig,'OVERNIGHT_TP_FROZEN')
    assert not [(l,t) for l,t in rig._b26_alerts if l in {'P0','P1'}]
    record_property('b31_evidence_callback_projection',str(projection[:2]))
    record_property('b31_evidence_known_vs_unlogged','Known: query provenance, prices, strong identity, requested quantity, verified TP time. Raw state/count/duplicate rows not recorded; fixture normal-working values are assumptions.')
    req=tp2(rig,pid,wire)
    rig._b28b_callback_fill(req.intent_id,quantity=1,price=345.72,exec_id='b31-evidence-tp2.01.01')
    _tick_until(rig,lambda: rig.store.position(pid)['tp2_done'] and rig.store.protection(pid)['state']=='WORKING')
    ref=rig.store.protection(pid)['intent_id']
    rig._b28b_callback_fill(ref,quantity=1,price=343.56,exec_id='b31-evidence-stop.01.01')
    assert rig.store.position(pid)['qty_open']==0
    _safety(rig)

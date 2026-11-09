"""B32: native duplicate callbacks after true overnight entry/restore.

The first TP timeout/restore is executed to reach generation 4, never seeded.
Prices/dates/orderId match the export; permId/refs are stable test aliases.
TP1 trigger is a synthetic level because the export omits the positions row.
"""
from dataclasses import replace
from datetime import timedelta
import pytest
from ib_insync import IB
from v6lite.broker.fake import FakeBroker
from v6lite.models import PriceObservationEvent,Purpose
from v6lite.market_calendar import ET
from v6lite.config import TestConfig as LiteTestConfig
from v6lite.tests.helpers import Rig,et
from v6lite.tests import test_batch28b_overnight_refresh as preparation
from v6lite.tests.test_batch26_overnight_evidence import _no_network,_safety,_tp_requests,_writes
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run,_audits,_tick_until
from v6lite.tests.test_batch28c_price_confirmation import NativePriceWire

@pytest.fixture
def oct29(overnight_run,monkeypatch):
    def paper_test_rig(directory,**kwargs):
        kwargs.update(client_id=132,state_dir=str(directory/'paper-test'),test=LiteTestConfig(enabled=True))
        return Rig(directory,**kwargs)
    monkeypatch.setattr(preparation,'Rig',paper_test_rig)
    rig,pid,harness=overnight_run(entry_day=(2026,10,28),tp_day=(2026,10,29),
        entry_price=344.40,stop=340.58,tp1=345.68,tp2=346.63,tp3=347.58,
        first_id=2888,client_id=132,con_id=265598)
    wire=NativePriceWire(rig,pid,harness,monkeypatch)
    native=wire.native
    def native_echo(value):
        contract,order,state=native(value)
        if value.order_type=='STP':order.lmtPrice=0.  # recorded broker_echo_lmt
        return contract,order,state
    monkeypatch.setattr(wire,'native',native_echo)
    wire.ib.wrapper.clientId=132;wire.ib.client.clientId=132
    monkeypatch.setattr(wire.ib.client,'placeOrder',lambda *args:None)
    monkeypatch.setattr(wire.ib,'placeOrder',IB.placeOrder.__get__(wire.ib,IB))
    harness.raw=lambda f:f.time.astimezone(ET).strftime('%Y%m%d %H:%M:%S US/Eastern')
    original_fill=rig._b28b_callback_fill
    def callback(*args,**kwargs):
        query_raw=harness.raw;harness.raw=lambda f:f.time.strftime('%Y%m%d-%H:%M:%S')
        try:return original_fill(*args,**kwargs)
        finally:harness.raw=query_raw
    monkeypatch.setattr(rig,'_b28b_callback_fill',callback)
    rig.at(et(9,30,day=(2026,10,29)))
    rig.broker.set_quote('AAPL',bid=347.54,ask=347.82,last=347.70,ts=rig.clock.now())
    assert rig.store.control('overnight_tp_restored:'+pid)
    return rig,pid,wire


def _second_TP1(rig,pid,wire):
    _tick_until(rig,lambda:rig.store.protection(pid)['state']=='REDUCE_PENDING')
    wire.ack();_tick_until(rig,lambda:len(_tp_requests(rig))==1)
    first=_tp_requests(rig)[0]
    assert first.limit_price==346.85 and rig.store.protection(pid)['modify_generation']==1
    rig.advance(seconds=10);rig.tick()
    _tick_until(rig,lambda:first.intent_id in rig.broker.cancels)
    rig.broker.confirm_cancel(first.intent_id);rig.engine._drain(rig.clock.now())
    _tick_until(rig,lambda:rig.store.protection(pid)['state']=='RESTORE_PENDING')
    assert rig.store.protection(pid)['modify_generation']==2
    wire.ack()
    _tick_until(rig,lambda:rig.store.protection(pid)['state'] in {'WORKING','REDUCE_PENDING'})
    if rig.clock.now()<et(9,30,40,day=(2026,10,29)):rig.at(et(9,30,40,day=(2026,10,29)))
    rig.broker.set_quote('AAPL',bid=349.47,ask=349.64,last=349.55,ts=rig.clock.now())
    _tick_until(rig,lambda:rig.store.protection(pid)['state']=='REDUCE_PENDING')
    assert rig.store.protection(pid)['modify_generation']==3
    wire.ack();_tick_until(rig,lambda:len(_tp_requests(rig))==2)
    second=_tp_requests(rig)[-1]
    assert second.purpose is Purpose.TP1 and second.limit_price==348.78
    return second


def _pending_BE(rig,pid,wire,monkeypatch,respond):
    second=_second_TP1(rig,pid,wire);captured=[]
    physical=rig.broker.modify_protection
    def modify(req):
        ack=physical(req);row=rig.store.protection(pid)
        if not captured and row['state']=='PRICE_PENDING':
            captured.append(dict(row));respond(dict(row))
        return ack
    monkeypatch.setattr(rig.broker,'modify_protection',modify)
    rig._b28b_callback_fill(second.intent_id,quantity=1,price=349.46,exec_id='b32-tp1.01.01')
    _tick_until(rig,lambda:bool(captured))
    [row]=captured
    assert row['modify_generation']==4 and row['pending_trigger']==344.57
    assert row['pending_qty']==2 and row['broker_order_id']==2890
    return row


def test_B32_three_native_identical_rows_confirm_gen4_and_TP2(oct29,monkeypatch,record_property):
    rig,pid,wire=oct29;seen=[];sdk_counts=[];snapshots=[]
    deliver=wire.deliver
    def capture(event):
        if isinstance(event,PriceObservationEvent) and event.owned_open_orders is not None:snapshots.append(event)
        deliver(event)
    wire.deliver=capture;wire.adapter.set_urgent_handler(capture)
    def response(row):
        req=rig.engine._protection_price_requests[pid][1]
        value=wire.apply_pending()
        def query():
            future=wire.ib.wrapper.startReq('openOrders')
            wire.ib.client.reqAllOpenOrders();wire.harness.loop.drain()
            for offset in [97366,101366,105366]:
                arrival=req+timedelta(microseconds=offset)
                if rig.clock.now()<arrival:rig.at(arrival)
                wire.open_order(value)
            sdk_counts.append(len(wire.ib.wrapper._results['openOrders']))
            rig.advance(microseconds=320)  # source=openOrders receipt at +105.686ms
            wire.ib.wrapper.openOrderEnd();assert future.done()
            result=future.result();assert len(result)==3 and len({id(t) for t in result})==1
            return result
        monkeypatch.setattr(wire.ib,'reqAllOpenOrders',query)
        rig.advance(microseconds=1)
        own=rig.broker.open_orders()
        seen.append((len(own),rig.store.protection(pid)['state']))
        # Later complete rounds also contain three native observations.
        def repeated_query():
            future=wire.ib.wrapper.startReq('openOrders')
            wire.ib.client.reqAllOpenOrders();wire.harness.loop.drain()
            for current in FakeBroker.open_orders(rig.broker):
                for _ in range(3):wire.open_order(current)
            wire.ib.wrapper.openOrderEnd();assert future.done();return future.result()
        monkeypatch.setattr(wire.ib,'reqAllOpenOrders',repeated_query)
    _pending_BE(rig,pid,wire,monkeypatch,response)
    assert sdk_counts==[3]
    assert seen==[(1,'WORKING')], 'the adapter must return one order and ACK the complete duplicate snapshot'
    assert snapshots and len(snapshots[0].owned_open_orders)==1
    assert any(a['generation']==4 and a['source']=='own complete openOrders snapshot' for a in _audits(rig,'PROTECT_PRICE_CONFIRMED'))
    assert not _audits(rig,'OVERNIGHT_TP_FROZEN')
    assert not [(l,t) for l,t in rig._b26_alerts if l in {'P0','P1'}]
    _tick_until(rig,lambda:rig.store.protection(pid)['state']=='REDUCE_PENDING')
    assert rig.store.protection(pid)['modify_generation']==5
    wire.ack();_tick_until(rig,lambda:len(_tp_requests(rig))==3)
    req=_tp_requests(rig)[-1];assert req.purpose is Purpose.TP2 and req.quantity==1
    facts=_audits(rig,'TP_EVIDENCE')
    assert any(a.get('P')==2 and a.get('S')==2 for a in facts)
    assert all(a.get('S')!=6 for a in facts)
    record_property('b32_native_rows','three callbacks -> same SDK Trade referenced three times -> one native normalized row')
    _safety(rig)

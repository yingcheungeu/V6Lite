"""Batch31: actual prior-day entry, durable restart and native SDK callbacks.

No position, restoration marker or authority cache is seeded. The native SDK
placeOrder reuses its recovered Trade; only the transport is offline.
"""
from dataclasses import replace
from datetime import timedelta
import json
import pytest
from v6lite.models import Purpose
from v6lite.tests.helpers import et
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _safety, _tp_requests, _writes, _restart
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _tick_until, _audits
from v6lite.tests.test_batch28c_price_confirmation import NativePriceWire

@pytest.fixture
def oct27(overnight_run, monkeypatch):
    def make():
        rig,pid,harness=overnight_run(entry_day=(2026,10,26),tp_day=(2026,10,27),
            entry_price=343.47,stop=339.62,tp1=344.75,tp2=345.72,tp3=347.00,first_id=2799,entry_commission=1.01)
        wire=NativePriceWire(rig,pid,harness,monkeypatch)
        from ib_insync import IB
        # Exercise the actual installed SDK's existing-Trade modification.
        wire.ib.wrapper.clientId=rig.cfg.client_id
        wire.ib.client.clientId=rig.cfg.client_id
        monkeypatch.setattr(wire.ib.client,'placeOrder',lambda *args: None)
        monkeypatch.setattr(wire.ib,'placeOrder',IB.placeOrder.__get__(wire.ib,IB))
        rig.at(et(9,33,19,day=(2026,10,27)));rig.px('AAPL',344.91)
        assert rig.store.control('overnight_tp_restored:'+pid)
        assert rig.store.position(pid)['opened_at'].startswith('2026-10-26')
        return rig,pid,wire
    return make

def pending_be(rig,pid,wire,*,at_request=None):
    _tick_until(rig,lambda: rig.store.protection(pid)['state']=='REDUCE_PENDING')
    wire.ack()
    _tick_until(rig,lambda: len(_tp_requests(rig))==1)
    tp=_tp_requests(rig)[0]
    captured=[]
    if at_request is not None:
        physical_modify=rig.broker.modify_protection
        def modify(request):
            ack=physical_modify(request)
            if not captured and rig.store.protection(pid)['state']=='PRICE_PENDING':
                captured.append(dict(rig.store.protection(pid)))
                at_request(captured[-1])
            return ack
        rig.broker.modify_protection=modify
    rig._b28b_callback_fill(tp.intent_id,quantity=1,price=344.71,exec_id='b31-tp1.01.01')
    _tick_until(rig,lambda: bool(captured) if at_request is not None else
        rig.store.protection(pid)['state']=='PRICE_PENDING')
    row=captured[0] if captured else dict(rig.store.protection(pid))
    assert row['modify_generation']==2 and row['status_seen_generation']==-1
    assert row['pending_trigger']==343.64 and row['pending_qty']==2
    assert row['trigger_price']==339.62
    return row

def clean(rig):
    assert not _audits(rig,'OVERNIGHT_TP_FROZEN')
    assert not [(level,text) for level,text in rig._b26_alerts if level in {'P0','P1'}]
    assert rig.store.control('frozen:AAPL') is None
    _safety(rig)

def tp2(rig,pid,wire):
    rig.px('AAPL',345.72)
    _tick_until(rig,lambda: rig.store.protection(pid)['state']=='REDUCE_PENDING')
    wire.ack()
    _tick_until(rig,lambda: len(_tp_requests(rig))==2)
    req=_tp_requests(rig)[-1]
    assert req.purpose is Purpose.TP2 and req.quantity==1
    return req

@pytest.mark.parametrize('source',['openOrder','own-round'])
def test_B31_oct27_actual_restoration_pending_ack_and_TP2_close(oct27,source,record_property):
    rig,pid,wire=oct27()
    seen=[]
    def native_response(row):
        value=wire.apply_pending()
        statuses=len(wire.status_callbacks)
        requested=rig.engine._protection_price_requests[pid][1]
        rig.advance(microseconds=107000);wire.open_order(value)
        seen.append(rig.clock.now())
        assert (seen[-1]-requested).total_seconds()==.107
        assert len(wire.status_callbacks)==statuses
    pending=pending_be(rig,pid,wire,at_request=native_response if source=='openOrder' else None)
    before_status=len(wire.status_callbacks)
    if source=='openOrder':
        assert rig.store.protection(pid)['state']=='WORKING'
        [ack]=_audits(rig,'PROTECT_PRICE_CONFIRMED')
        assert ack['generation']==2 and ack['source']=='live openOrder'
        rig.engine._protection_priority_due=True;rig.tick()
    else:
        wire.apply_pending()
        _tick_until(rig,lambda: rig.store.protection(pid)['state']=='WORKING')
    assert len(wire.status_callbacks)==before_status
    clean(rig)
    record_property('b31_ack_observed_at',seen[0].isoformat() if seen else 'own complete round')
    req=tp2(rig,pid,wire)
    rig._b28b_callback_fill(req.intent_id,quantity=1,price=345.72,exec_id='b31-tp2.01.01')
    _tick_until(rig,lambda: rig.store.protection(pid)['state']=='WORKING' and
        rig.store.position(pid)['tp2_done']==1)
    ref=rig.store.protection(pid)['intent_id']
    rig._b28b_callback_fill(ref,quantity=1,price=343.56,exec_id='b31-stop.01.01')
    assert rig.store.position(pid)['qty_open']==0
    clean(rig)

@pytest.mark.parametrize('seconds',[20,40])
def test_B31_old_confirmed_price_waits_without_freeze_or_P0(oct27,seconds):
    rig,pid,wire=oct27();pending_be(rig,pid,wire)
    initial=_writes(rig)
    rig.px('AAPL',345.72)
    start=rig.clock.now()
    while (rig.clock.now()-start).total_seconds()<seconds:
        rig.advance(seconds=1);rig.tick();_safety(rig)
    assert rig.store.protection(pid)['state']=='PRICE_PENDING'
    assert not _audits(rig,'OVERNIGHT_TP_FROZEN') and rig.store.control('frozen:AAPL') is None
    assert not [(l,t) for l,t in rig._b26_alerts if l=='P0']
    p1=[t for l,t in rig._b26_alerts if l=='P1']
    assert bool(p1)==(seconds==40)
    if p1: assert all('修改' in text for text in p1)
    assert _writes(rig)==initial
    wire.ack(only_open_order=True)
    assert rig.store.protection(pid)['state']=='WORKING'
    tp2(rig,pid,wire)

@pytest.mark.parametrize('wrong',['aux','quantity','permId'])
def test_B31_real_contradiction_retains_STP_and_freezes(oct27,wrong):
    rig,pid,wire=oct27();pending_be(rig,pid,wire)
    value=wire.apply_pending()
    bad=replace(value,**({'aux_price':341.} if wrong=='aux' else
        {'total_quantity':3,'remaining':3.} if wrong=='quantity' else {'perm_id':value.perm_id+1}))
    wire.transform=lambda o: bad if o.intent_id==value.intent_id else o
    before=_writes(rig)
    rig.advance(microseconds=107000);wire.open_order(bad)
    rig.px('AAPL',345.72)
    _tick_until(rig,lambda: any(l=='P0' for l,t in rig._b26_alerts))
    assert rig.store.control('frozen:AAPL') is not None or _audits(rig,'OVERNIGHT_TP_FROZEN')
    assert any(l=='P1' for l,t in rig._b26_alerts)
    assert rig.store.protection(pid)['trigger_price']==339.62
    assert _writes(rig)==before and len(_tp_requests(rig))==1
    assert rig.broker.orders[value.intent_id].remaining==2
    _safety(rig)

def test_B31_restart_pending_uses_owned_complete_round_then_TP2(oct27,tmp_path,monkeypatch):
    rig,pid,wire=oct27();pending_be(rig,pid,wire);wire.apply_pending()
    rig.engine.shutdown(grace_seconds=0)
    _restart(rig,tmp_path,monkeypatch,reopen=True)
    _tick_until(rig,lambda: rig.store.protection(pid)['state']=='WORKING')
    assert any(a['source']=='own complete three-source round' for a in _audits(rig,'PROTECT_PRICE_CONFIRMED'))
    tp2(rig,pid,wire);clean(rig)


def test_B31_live_ACK_during_normal_owned_round_does_not_depend_on_receipt_proof(oct27):
    rig,pid,wire=oct27();pending_be(rig,pid,wire)
    assert rig.store.control('execution_time_unverified:b31-tp1.01.01') is not None
    seen=[]
    def after_source(name,result):
        if name!='open_orders' or seen or rig.engine._protection_query_position!=pid:
            return
        # End arrived, but the engine's new three-source round is unfinished.
        assert wire.adapter._req_sent['openOrders']==wire.adapter._req_ended['openOrders']
        assert pid not in rig.engine._protection_notice_evidence
        value=wire.apply_pending()
        rig.advance(microseconds=107000)
        statuses=len(wire.status_callbacks)
        wire.open_order(value)
        assert len(wire.status_callbacks)==statuses
        seen.append(rig.store.protection(pid)['state'])
    wire.after_source=after_source
    rig.engine._protection_priority_due=True;rig.tick()
    assert seen==['WORKING'], 'live ACK must not depend on an unfinished unrelated receipt-proof round'
    wire.after_source=lambda name,result:None
    # Discard the mixed old round, then acquire fresh complete a-d authority.
    tp2(rig,pid,wire);clean(rig)

@pytest.mark.parametrize('reason',['保本修改待确认','真实保护身份冲突'])
def test_B31_only_explicit_confirmation_freeze_clears_after_new_a_d(oct27,reason):
    rig,pid,wire=oct27();pending_be(rig,pid,wire)
    row=rig.store.protection(pid)
    # Reproduce the legacy freeze operations with a known, explicit cause;
    # ambiguous old markers cannot be silently promoted to this provenance.
    rig.engine._overnight_failure(rig.clock.now(),row,reason)
    rig.store.set_control(rig.clock.now(),'frozen:AAPL','pending BE broker evidence mismatch')
    rig.engine._overnight_record_frozen(rig.clock.now(),row,Purpose.TP2)
    before=_writes(rig)
    wire.ack(only_open_order=True)
    assert rig.store.protection(pid)['state']=='WORKING'
    assert rig.store.control('frozen:AAPL') is not None, 'ACK alone cannot release TP'
    [freeze] = _audits(rig,'OVERNIGHT_TP_FROZEN')
    assert freeze['reason'] == ('修改待确认' if reason=='保本修改待确认' else reason)
    assert freeze['reason_codes'] == (['price_confirmation_pending'] if reason=='保本修改待确认' else ['real_failure'])
    if reason=='保本修改待确认':
        _tick_until(rig,lambda: rig.store.control('frozen:AAPL') is None)
        assert _audits(rig,'OVERNIGHT_TP_FREEZE_CLEARED')
        assert any(l=='INFO' and '冻结已解除' in t for l,t in rig._b26_alerts)
        tp2(rig,pid,wire)
    else:
        for _ in range(4): rig.advance(seconds=16);rig.tick();_safety(rig)
        assert rig.store.control('frozen:AAPL') is not None
        assert not _audits(rig,'OVERNIGHT_TP_FREEZE_CLEARED')
        assert _writes(rig)==before


def test_B31_ambiguous_legacy_freeze_cannot_be_mistaken_for_pending_only(oct27):
    rig,pid,wire=oct27();pending_be(rig,pid,wire)
    row=rig.store.protection(pid)
    # This is precisely the information persisted by the generic b30 branch.
    rig.store.set_control(rig.clock.now(),'frozen:AAPL','pending BE broker evidence mismatch')
    rig.engine._overnight_record_frozen(rig.clock.now(),row,Purpose.TP2)
    wire.ack(only_open_order=True)
    for _ in range(3):rig.advance(seconds=16);rig.tick();_safety(rig)
    assert rig.store.control('frozen:AAPL') is not None
    assert not _audits(rig,'OVERNIGHT_TP_FREEZE_CLEARED')


def test_B31_oct27_native_nine_ms_commission_close_card_has_final_minus_1_60(oct27,record_property):
    from ib_insync import CommissionReport
    rig,pid,wire=oct27();pending_be(rig,pid,wire);wire.ack(only_open_order=True)
    ref=rig.store.protection(pid)['intent_id']
    rig.channel.messages.clear()
    fill=rig._b28b_callback_fill(ref,quantity=2,price=343.56,exec_id='b31-oct27-stop.01.01',commission=None)
    assert rig.store.position(pid)['qty_open']==0
    assert not [b for t,b in rig.channel.messages if '持仓已关闭' in t]
    wire.ib.commissionReportEvent+=wire.adapter._on_commission
    rig.advance(microseconds=9000)
    wire.ib.wrapper.commissionReport(CommissionReport(execId=fill.exec_id,commission=1.01,currency='USD'))
    [card]=[b for t,b in rig.channel.messages if '持仓已关闭' in t]
    assert '毛盈亏 1.42' in card and '佣金 3.02' in card and '最终净值 -1.60' in card
    assert '未回报' not in card
    record_property('b31_oct27_final_close_card',card)
    _safety(rig)


@pytest.mark.parametrize('extra',['real-after-pending','wrong-generation'])
def test_B31_freeze_provenance_cannot_release_real_failure_or_other_generation(oct27,extra):
    rig,pid,wire=oct27();pending_be(rig,pid,wire);row=rig.store.protection(pid)
    rig.engine._overnight_failure(rig.clock.now(),row,'保本修改待确认')
    rig.store.set_control(rig.clock.now(),'frozen:AAPL','pending BE broker evidence mismatch')
    rig.engine._overnight_record_frozen(rig.clock.now(),row,Purpose.TP2)
    if extra=='real-after-pending':
        rig.engine._overnight_failure(rig.clock.now(),row,'真实工作单数量不一致')
    else:
        key='overnight_tp_freeze_causes:'+pid+':2026-10-27'
        value=json.loads(rig.store.control(key));value['binding']['modify_generation']-=1
        rig.store.set_control(rig.clock.now(),key,json.dumps(value))
    wire.ack(only_open_order=True)
    for _ in range(3):rig.advance(seconds=16);rig.tick();_safety(rig)
    assert rig.store.control('frozen:AAPL') is not None
    assert not _audits(rig,'OVERNIGHT_TP_FREEZE_CLEARED')


def test_B31_old_price_wait_cannot_hide_unknown_visible_execution(oct27):
    from v6lite.models import Side
    rig,pid,wire=oct27();pending_be(rig,pid,wire)
    unknown=replace(rig._b26_entry_fill,exec_id='b31-unknown.01.01',intent_id='',
        side=Side.BUY,quantity=1,broker_order_id=9901,perm_id=9902,time=rig.clock.now())
    wire.harness.transform=lambda rows:list(rows)+[unknown]
    before=_writes(rig)
    for _ in range(3):rig.advance(seconds=16);rig.tick();_safety(rig)
    assert any(l=='P1' and ('可见成交' in t or '证据' in t) for l,t in rig._b26_alerts)
    assert len(_tp_requests(rig))==1 and _writes(rig)==before
    assert not _audits(rig,'PROTECT_PRICE_CONFIRMED')

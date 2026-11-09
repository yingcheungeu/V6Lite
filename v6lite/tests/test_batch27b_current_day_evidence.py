"""27b independent E2E: naive query time never supplies exact-time authority.

10/19 ENTRY and 10/20 TP1 are real unverified native-decoder callbacks.
Economic truth/orders use FakeBroker, native execution request/filter/End use
the installed SDK adapter. All files/identifiers/accounts are synthetic.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.alerts import Alerter
from v6lite.broker.fake import FakeBroker
from v6lite.models import FillEvent, Purpose, Side
from v6lite.report import daily_report
from v6lite.market_calendar import trading_day
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch26_overnight_evidence import (
    _activate, _no_network, _protection_config, _restart, _safety, _settle,
    _tp_requests, _watch_boundaries, _writes,
)
from v6lite.tests.test_batch27b_raw_execution_time import NativeExecutionHarness

ENTRY_DAY=(2026,10,19)
TP_DAY=(2026,10,20)
TODAY_NOTE='当日成交时间按当日成交查询判定为今天（精确时间未核实）'


def _fills(rig):
    return [dict(row) for row in rig.store.conn.execute('SELECT * FROM fills ORDER BY rowid')]


def _markers(rig):
    return dict(rig.store.controls_with_prefix('execution_time_unverified:'))


def _verified(rig):
    return rig.store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind='EXECUTION_TIME_VERIFIED'").fetchone()[0]


def _p1(rig):
    return [body for title,body in rig.channel.messages if 'P1' in title and '过夜' in title]


def _at_tp2(rig,pid):
    rig.advance(seconds=31)
    rig.px('AAPL',float(rig.store.position(pid)['tp2'])+.20)
    rig.engine._protection_progress(rig.clock.now())
    rig.engine._evaluate_exits(rig.clock.now());rig.engine._run_queue();rig.engine._drain(rig.clock.now())


@pytest.fixture
def overnight_native(tmp_path,monkeypatch,request):
    made=[]
    def make(*,visibility='default-midnight'):
        rig=Rig(tmp_path/('native-overnight-'+str(len(made))),
            start=et(9,50,day=ENTRY_DAY),protection=_protection_config(True),risk_per_trade_pct=.0015)
        made.append(rig)
        harness=NativeExecutionHarness(rig.cfg,rig.clock,monkeypatch,request)
        harness.visibility=visibility
        # Model the independent market-data stream while genuine native query
        # pacing blocks: quotes keep their prices but receive fresh timestamps.
        # The production 3-second TP quote limit and pacing are unchanged.
        native_sleep=harness.ib.sleep
        def streaming_sleep(seconds):
            native_sleep(seconds)
            rig.refresh_quotes()
        monkeypatch.setattr(harness.ib,'sleep',streaming_sleep)
        harness.rows=lambda:FakeBroker.executions_today(rig.broker)
        rig.broker.query_generation=harness.adapter.query_generation
        def client_query():
            try:return harness.client_query()
            finally:rig.broker.query_generation=harness.adapter.query_generation
        def account_query():
            try:return harness.account_query()
            finally:rig.broker.query_generation=harness.adapter.query_generation
        def scoped_query():
            try:return harness.scoped_query()
            finally:rig.broker.query_generation=harness.adapter.query_generation
        monkeypatch.setattr(rig.broker,'executions_today',client_query)
        monkeypatch.setattr(rig.broker,'account_executions_today',account_query)
        monkeypatch.setattr(rig.broker,'account_executions_today_scoped',scoped_query,raising=False)
        old_drain=rig.broker.drain_events
        monkeypatch.setattr(rig.broker,'drain_events',lambda:old_drain()+harness.adapter.drain_events())
        def callback_fill(intent_id,*,qty,price,exec_id):
            native=rig.broker.fill(intent_id,qty=qty,price=price,exec_id=exec_id,emit=False)
            events=harness.callback(native)
            [event]=[item for item in events if isinstance(item,FillEvent)]
            assert event.time_source=='callback' and event.time_uncertain
            # Fake economics and the genuine decoder event share one execution;
            # the adapter provides time provenance, never a second execution.
            event=replace(event,commission=native.commission)
            rig.broker.orders[intent_id].fills[-1]=event
            rig.broker.events.extend([event if item is not None and isinstance(item,FillEvent) else item
                                      for item in events])
            rig.engine._drain(rig.clock.now())
            return event
        rig._b27b_callback_fill=callback_fill;rig._b27b_native=harness
        rig.start();rig.at(et(10,0,30,day=ENTRY_DAY));rig.tick()
        [entry]=[req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
        assert entry.quantity==3
        entry_fill=callback_fill(entry.intent_id,qty=3,price=113.13,exec_id='b27b-1019-entry.01.01')
        rig.advance(seconds=1);rig.tick()
        [pos]=rig.store.open_positions();pid=pos['position_id']
        assert rig.store.protection(pid)['state']=='WORKING'
        assert rig.store.control('execution_time_unverified:'+entry_fill.exec_id) is not None
        rig.engine.shutdown(grace_seconds=0)
        [shutdown]=[json.loads(row['payload']) for row in rig.store.conn.execute(
            "SELECT payload FROM audit WHERE kind='SHUTDOWN' ORDER BY id DESC LIMIT 1")]
        assert shutdown['unresolved']==[] and shutdown['broker_working']==[]
        rig.alerter.close();path,levels=rig.store.path,rig.store._levels_fn
        rig.store.close();rig.store=Store(path,levels)
        rig.at(et(10,1,day=TP_DAY))
        rig._b26_alerts=[];rig._b26_timeline=[];rig._b26_market_calls=[]
        _watch_boundaries(rig,monkeypatch)
        rig.channel.messages.clear()
        rig._b27b_entry_fill=entry_fill
        _restart(rig,tmp_path,monkeypatch)
        assert rig.store.position(pid)['qty_open']==3 and rig.broker.orders[rig.store.protection(pid)['intent_id']].remaining==3
        return rig,pid,harness
    yield make
    for rig in made:
        rig.alerter.close();rig.store.close()


def _filled_tp1(rig,pid):
    tp,ref=_activate(rig,pid)
    fill=rig._b27b_callback_fill(tp.intent_id,qty=1,price=rig.broker.quote('AAPL').bid,
                              exec_id='b27b-1020-tp1.01.01')
    _safety(rig)
    assert rig.store.position(pid)['qty_open']==2 and rig.store.position(pid)['tp1_done']==1
    assert rig.store.control('execution_time_unverified:'+fill.exec_id) is not None
    assert rig.broker.orders[ref].remaining==2
    return tp,ref,fill


@pytest.mark.parametrize('visibility',['default-midnight','trade-log-7day'])
def test_B27b_naive_1020_TP1_time_allows_BE_ACK_then_TP2_with_unchanged_unverified_ledger(overnight_native,visibility):
    rig,pid,harness=overnight_native(visibility=visibility)
    tp1,ref,fill=_filled_tp1(rig,pid)
    before_fills,before_markers,before_verified=_fills(rig),_markers(rig),_verified(rig)
    _settle(rig);_safety(rig)
    pending=rig.broker.protection_pending_modifications.get(ref)
    assert pending is not None,'complete current-day membership must permit the real BE modification'
    assert pending.quantity==2 and pending.trigger_price==pytest.approx(rig.store.position(pid)['breakeven_stop'])
    rig.broker.confirm_protection_modify(ref);_settle(rig);_safety(rig)
    row=rig.store.protection(pid)
    assert row['state']=='WORKING' and row['trigger_price']==pytest.approx(rig.store.position(pid)['breakeven_stop'])
    _at_tp2(rig,pid)
    row=rig.store.protection(pid)
    assert row['state']=='REDUCE_PENDING' and row['pending_qty']==1
    assert [request.purpose for request in _tp_requests(rig)]==[Purpose.TP1]
    _safety(rig)
    rig.broker.confirm_protection_modify(ref);_settle(rig);_safety(rig)
    assert [request.purpose for request in _tp_requests(rig)]==[Purpose.TP1,Purpose.TP2]
    assert _tp_requests(rig)[1].quantity==1 and rig.broker.orders[ref].remaining==1
    assert _fills(rig)==before_fills and _markers(rig)==before_markers and _verified(rig)==before_verified
    assert TODAY_NOTE in rig.messages(),'the actual delivered card must disclose day-only evidence'
    report=daily_report(rig.store,rig.cfg,trading_day(rig.clock.now()))
    assert TODAY_NOTE in report
    assert harness.ib.TimezoneTWS==''
    assert all(' UTC' not in raw and 'America/' not in raw for _,_,raw in harness.decoded)
    assert len([packet for packet in harness.wire if packet.split('\0',1)[0]=='7'])>0


@pytest.mark.parametrize('failure',['missing-exec','price-mismatch','permId-mismatch','provisional-other-day','incomplete-query'])
def test_B27b_each_current_day_time_proof_failure_freezes_BE_TP2_and_preserves_two_share_stop(
        overnight_native,failure):
    rig,pid,harness=overnight_native()
    _,ref,fill=_filled_tp1(rig,pid)
    if failure=='missing-exec':harness.transform=lambda rows:[row for row in rows if row.exec_id!=fill.exec_id]
    elif failure=='price-mismatch':harness.transform=lambda rows:[replace(row,price=row.price+.01) for row in rows]
    elif failure=='permId-mismatch':harness.transform=lambda rows:[replace(row,perm_id=row.perm_id+1) for row in rows]
    elif failure=='provisional-other-day':
        marker=json.loads(rig.store.control('execution_time_unverified:'+fill.exec_id))
        other_day=fill.time-timedelta(days=1)
        marker['provisional_time']=other_day.isoformat()
        rig.store.conn.execute('UPDATE fills SET time=? WHERE exec_id=?',(other_day.isoformat(),fill.exec_id))
        rig.store.set_control(rig.clock.now(),'execution_time_unverified:'+fill.exec_id,json.dumps(marker))
    else:harness.scoped_mode='raise-after-row'
    before=(_fills(rig),_markers(rig),_writes(rig),rig.store.protection(pid)['trigger_price'])
    _settle(rig);_at_tp2(rig,pid)
    assert _p1(rig),'the actual phone card must explain the failed current-day proof'
    assert not [body for title,body in rig.channel.messages if 'P0' in title], (
        'a failed narrow d scope cannot masquerade as failed core stop evidence')
    assert [request.purpose for request in _tp_requests(rig)]==[Purpose.TP1]
    assert _fills(rig)==before[0] and _markers(rig)==before[1] and _writes(rig)==before[2]
    row=rig.store.protection(pid)
    assert row['trigger_price']==before[3] and row['confirmed_remaining']==2 and row['state']=='WORKING'
    assert rig.broker.orders[ref].remaining==2 and ref not in rig.broker.protection_pending_modifications
    assert TODAY_NOTE not in '\n'.join(_p1(rig))
    _safety(rig)


@pytest.mark.parametrize('mode',['list-only','missing-End','historical-filter','unscoped-filter','generation-reset','cross-midnight'])
def test_B27b_current_day_exception_requires_real_complete_current_scope_not_just_visible_execId(overnight_native,mode):
    rig,pid,harness=overnight_native()
    _,ref,_=_filled_tp1(rig,pid)
    harness.scoped_mode=mode
    before=_writes(rig),_fills(rig),_markers(rig)
    _settle(rig)
    assert _writes(rig)==before[0]
    if mode in {'generation-reset','cross-midnight'}:
        # B28b: wrong generation/day revokes authority and awaits a new round.
        # The successful-query scope contradictions below remain actual failures.
        assert not _p1(rig)
        assert not rig.engine._overnight_proof_fresh(rig.clock.now(),rig.store.protection(pid))
        assert not rig.store.controls_with_prefix('overnight_tp_frozen_at_price:' + pid)
    else:
        assert _p1(rig)
    assert not [body for title,body in rig.channel.messages if 'P0' in title], (
        'ordinary core queries stay healthy; only dedicated today authority is withheld')
    assert _fills(rig)==before[1] and _markers(rig)==before[2]
    assert rig.store.protection(pid)['trigger_price']!=rig.store.position(pid)['breakeven_stop']
    assert rig.broker.orders[ref].remaining==2 and ref not in rig.broker.protection_pending_modifications
    assert len(_tp_requests(rig))==1
    if mode in {'list-only','missing-End','unscoped-filter','historical-filter'}:
        report=daily_report(rig.store,rig.cfg,trading_day(rig.clock.now()))
        assert TODAY_NOTE not in report,'a marker cannot stand in for a successful actual today-scoped query'
    _safety(rig)


@pytest.mark.parametrize('corruption',['bool-quantity','float-orderId','wrong-conId','future-provisional','raw-time-not-equal','missing-pin'])
def test_B27b_new_today_exception_retains_strict_marker_identity_contract_and_time_boundaries(
        overnight_native,corruption):
    rig,pid,harness=overnight_native()
    _,ref,fill=_filled_tp1(rig,pid)
    key='execution_time_unverified:'+fill.exec_id
    marker=json.loads(rig.store.control(key))
    if corruption=='bool-quantity':marker['identity'][4]=True  # True equals this TP's 1 only under unsafe ==.
    elif corruption=='float-orderId':marker['identity'][6]=float(marker['identity'][6])
    elif corruption=='wrong-conId':marker['identity'][8]+=1
    elif corruption=='future-provisional':
        future=rig.clock.now()+timedelta(hours=1)
        marker['provisional_time']=future.isoformat()
        # Match the raw strings to isolate the independent no-future bound;
        # production must not rewrite this deliberately invalid synthetic row.
        rig.store.conn.execute('UPDATE fills SET time=? WHERE exec_id=?',(future.isoformat(),fill.exec_id))
    elif corruption=='raw-time-not-equal':marker['provisional_time']=fill.time.isoformat().replace('+00:00','Z')
    else:rig.store.delete_control('conid:AAPL')
    rig.store.set_control(rig.clock.now(),key,json.dumps(marker))
    before=_writes(rig),_fills(rig),_markers(rig)
    _settle(rig)
    assert _p1(rig) and _writes(rig)==before[0]
    assert _fills(rig)==before[1] and _markers(rig)==before[2]
    assert rig.broker.orders[ref].remaining==2 and ref not in rig.broker.protection_pending_modifications
    _safety(rig)


def test_B27b_failed_a_card_has_one_unverified_time_prefix(overnight_native):
    rig,pid,harness=overnight_native()
    entry=rig._b27b_entry_fill
    received=et(23,30,day=ENTRY_DAY)
    rig.store.conn.execute('UPDATE fills SET time=?,trading_day=? WHERE exec_id=?',
        (received.isoformat(),trading_day(received).isoformat(),entry.exec_id))
    key='execution_time_unverified:'+entry.exec_id
    marker=json.loads(rig.store.control(key));marker['provisional_time']=received.isoformat()
    rig.store.set_control(rig.clock.now(),key,json.dumps(marker))
    rig.engine._protection_progress(rig.clock.now())
    pages=_p1(rig)
    assert len(pages)==1 and pages[0].count('账本成交时间尚未核实')==1,pages
    assert _tp_requests(rig)==[]


def test_B27b_failed_d_card_shows_actual_complete_query_count(overnight_native):
    rig,pid,harness=overnight_native()
    # Isolate d from the new today-TP1 exception. No TP has occurred; the
    # explicit default-midnight server view leaves the old ENTRY in history,
    # where baseline 27a already accepts its strict receive-before-day bound.
    entry=rig._b27b_entry_fill
    assert harness.visibility=='default-midnight'
    assert '过夜持仓自动止盈恢复' in rig.messages(),'a must already have naturally passed before injecting d faults'
    assert rig.store.control('execution_time_unverified:'+entry.exec_id) is not None
    ref=rig.store.protection(pid)['intent_id']
    unknown=[replace(entry,exec_id=f'b27b-unknown-{index}.01.01',intent_id='',
                     side=Side.BUY,quantity=1,perm_id=90901+index,time=rig.clock.now())
             for index in range(3)]
    harness.transform=lambda rows:rows+unknown
    before=_fills(rig),_writes(rig)
    _settle(rig)
    pages=_p1(rig)
    assert pages and any('当日可见成交无法归属' in page for page in pages),pages
    assert all('账本成交时间尚未核实' not in page for page in pages),pages
    assert any(re.search(r'当日可见成交[=：]\s*3\s*条',page) for page in pages),pages
    assert not any('当日可见成交=未完成 条' in page for page in pages)
    assert _fills(rig)==before[0] and _writes(rig)==before[1]
    assert rig.broker.orders[ref].remaining==3 and _tp_requests(rig)==[]
    _safety(rig)

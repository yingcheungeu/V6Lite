"""27b: an explicitly selected scoped query cannot downgrade incomplete proof.

Before first TP, all ledger fills belong to yesterday. No today-time marker
can accidentally serve as the only completeness guard. Legacy brokers without
the optional scoped capability retain their reviewed historical compatibility.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from v6lite.market_calendar import trading_day
from v6lite.models import FillEvent,Side
from v6lite.tests.test_batch26_overnight_evidence import (
    _activate,_attempt_tp,_no_network,_safety,_tp_requests,_writes,
)
from v6lite.tests.test_batch27b_current_day_evidence import (
    _fills,_markers,_p1,overnight_native,
)


def _observe_queries(harness,monkeypatch):
    scoped=[];ordinary=[]
    original_scope=harness.scoped_query
    original_client=harness.adapter.executions_today
    def scope_query():
        result=original_scope();scoped.append(result);return result
    def client_query():
        result=original_client();ordinary.append(result);return result
    monkeypatch.setattr(harness,'scoped_query',scope_query)
    monkeypatch.setattr(harness.adapter,'executions_today',client_query)
    return scoped,ordinary


def _historical_only_before_first_TP(rig):
    assert _tp_requests(rig)==[]
    today=trading_day(rig.clock.now()).isoformat()
    assert _fills(rig) and all(fill['trading_day']<today for fill in _fills(rig))
    assert _markers(rig),'the genuine old callback marker remains, without a today marker'


def _assert_stop_only_unchanged(rig,pid,ref,before):
    assert _p1(rig),'explicitly incomplete dedicated evidence must freeze before the first reduction'
    assert not [body for title,body in rig.channel.messages if 'P0' in title]
    assert _writes(rig)==before[0] and _fills(rig)==before[1] and _markers(rig)==before[2]
    assert _tp_requests(rig)==[] and rig.store.position(pid)['qty_open']==3
    row=rig.store.protection(pid)
    assert row['state']=='WORKING' and row['confirmed_remaining']==row['pending_max_remaining']==3
    assert row['tp_stage'] is None and row['pending_qty'] is None
    assert rig.broker.orders[ref].remaining==3 and ref not in rig.broker.protection_pending_modifications
    _safety(rig)


@pytest.mark.parametrize('fault',['missing-End','list-only'])
def test_B27b_first_TP_refuses_an_explicitly_selected_but_incomplete_or_unscoped_d_result(
        overnight_native,monkeypatch,fault):
    rig,pid,harness=overnight_native(visibility='default-midnight')
    _historical_only_before_first_TP(rig)
    scoped,ordinary=_observe_queries(harness,monkeypatch)
    harness.scoped_mode=fault  # Core ordinary requests stay healthy.
    ref=rig.store.protection(pid)['intent_id']
    before=_writes(rig),_fills(rig),_markers(rig)
    _attempt_tp(rig,pid)
    assert ordinary and all(list(result)==[] for result in ordinary)
    assert scoped and all(not getattr(result,'scope',None) or result.scope.get('complete') is not True
                          for result in scoped)
    assert any(filt.time for _,filt in harness.filters),'the dedicated query is actually selected and transmitted'
    _assert_stop_only_unchanged(rig,pid,ref,before)


def test_B27b_first_TP_refuses_completed_End_when_other_client_row_has_undecodable_native_time(
        overnight_native,monkeypatch):
    rig,pid,harness=overnight_native(visibility='default-midnight')
    _historical_only_before_first_TP(rig)
    entry=rig._b27b_entry_fill
    unknown=FillEvent('b27b-other-client-bad-time.01.01','','AAPL',Side.BUY,1,113.13,
        rig.clock.now(),broker_order_id=907001,perm_id=907002,con_id=entry.con_id)
    original_transform=harness.transform
    def only_d(rows):
        rows=original_transform(rows)
        return rows+[unknown] if harness.filters[-1][1].time else rows
    harness.transform=only_d
    original_raw=harness.raw
    harness.raw=lambda fill:'bad-native' if fill.exec_id==unknown.exec_id else original_raw(fill)
    actual=[]
    original_interpret=harness.ib.client.decoder.interpret
    def native_other_client(fields):
        if fields and fields[0]=='11' and fields[14]==unknown.exec_id:
            fields=list(fields)
            fields[22]=str(rig.cfg.client_id+17)  # Actual raw native clientId, before SDK decoding.
            actual.append((int(fields[1]),fields[14],fields[15],int(fields[22])))
        return original_interpret(fields)
    monkeypatch.setattr(harness.ib.client.decoder,'interpret',native_other_client)
    scoped,ordinary=_observe_queries(harness,monkeypatch)
    ref=rig.store.protection(pid)['intent_id']
    before=_writes(rig),_fills(rig),_markers(rig)
    _attempt_tp(rig,pid)
    assert actual and all(raw=='bad-native' and client_id!=rig.cfg.client_id
                          for _,_,raw,client_id in actual)
    ended={req_id for req_id,_ in harness.ends}
    assert all(req_id in ended for req_id,_,_,_ in actual),'the native End really completed each malformed-row request'
    assert ordinary and all(list(result)==[] for result in ordinary),'ordinary core evidence remains healthy'
    assert scoped and all(isinstance(getattr(result,'scope',None),dict) and result.scope.get('complete') is False
                          for result in scoped)
    assert all(list(result)==[] for result in scoped),'invalid row is diagnostic-only, never fabricated as an economic fill'
    _assert_stop_only_unchanged(rig,pid,ref,before)


def test_B27b_legacy_broker_without_scoped_capability_keeps_historical_stop_TP_compatibility(
        overnight_native,monkeypatch):
    rig,pid,harness=overnight_native(visibility='default-midnight')
    _historical_only_before_first_TP(rig)
    monkeypatch.delattr(rig.broker,'account_executions_today_scoped')
    before_fills,before_markers=_fills(rig),_markers(rig)
    tp,ref=_activate(rig,pid)
    assert tp.quantity==1 and rig.broker.orders[ref].remaining==2
    assert rig.store.protection(pid)['state']=='TP_ACTIVE'
    assert _fills(rig)==before_fills and _markers(rig)==before_markers
    assert _p1(rig)==[]
    _safety(rig)

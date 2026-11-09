"""27b independent review: explicit observed identity conflicts are never ignored.

Actual native decoder results prove each conflicting legacy client field.
The dedicated scoped account query is observed independently and stays healthy.
Missing optional query conId is NOT made a new rejection rule: only nonempty
conflicting refs and different positive integer conIds are used here.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from v6lite.tests.test_batch26_overnight_evidence import (
    _attempt_tp, _no_network, _safety, _settle, _tp_requests, _writes,
)
from v6lite.tests.test_batch27b_current_day_evidence import (
    _at_tp2, _filled_tp1, _fills, _markers, _p1, overnight_native,
)


def _conflict_only_legacy_client(rig,harness,monkeypatch,exec_id,**change):
    """Change native query rows, then retain the actual converted FillEvents."""
    native_original_transform=harness.transform
    def native_transform(rows):
        rows=native_original_transform(rows)
        # The harness records the exact request already sent by the SDK.
        # Preserve account/scoped rows; never forge their evidence metadata.
        _,filt=harness.filters[-1]
        if filt.clientId==rig.cfg.client_id and filt.time=='':
            return [replace(fill,**change) if fill.exec_id==exec_id else fill for fill in rows]
        return rows
    harness.transform=native_transform
    original_client_query=harness.adapter.executions_today
    converted=[]
    def observe_client():
        result=original_client_query()
        converted.extend(result)  # Real adapter conversion, not the scripted source row.
        return result
    monkeypatch.setattr(harness.adapter,'executions_today',observe_client)
    original_scoped=harness.scoped_query
    scoped=[]
    def observe_scoped():
        result=original_scoped()
        scoped.append(result)
        return result
    monkeypatch.setattr(harness,'scoped_query',observe_scoped)
    return converted,scoped


def _assert_actual_conflict_and_healthy_scope(rig,harness,converted,scoped,exec_id,field,value):
    assert any(fill.exec_id==exec_id and getattr(fill,field)==value for fill in converted), (
        'actual native adapter result must carry the tested conflict',exec_id,field)
    packets=[message.split('\0') for message in harness.wire if message.split('\0',1)[0]=='7']
    assert any(packet[3]==str(rig.cfg.client_id) and packet[5]=='' for packet in packets)
    assert any(packet[5] for packet in packets),'dedicated scoped query must actually be transmitted'
    assert scoped,'observe the untouched dedicated account result, rather than assuming it is healthy'
    assert all(isinstance(getattr(result,'scope',None),dict) and result.scope.get('complete') is True
               for result in scoped)


@pytest.mark.parametrize('field',['intent_id','con_id'],ids=['historical-nonempty-intent-conflict','historical-positive-conId-conflict'])
def test_B27b_review_wide_historical_time_fallback_never_waives_explicit_client_identity_conflict(
        overnight_native,monkeypatch,field):
    rig,pid,harness=overnight_native(visibility='trade-log-7day')
    entry=rig._b27b_entry_fill
    if field=='intent_id':
        value='v6l-b27b-wrong-history-intent'
        assert value and value!=entry.intent_id
    else:
        value=entry.con_id+1
        assert type(value) is int and value>0
    converted,scoped=_conflict_only_legacy_client(rig,harness,monkeypatch,entry.exec_id,**{field:value})
    before=_fills(rig),_markers(rig),_writes(rig)
    ref=rig.store.protection(pid)['intent_id']
    _attempt_tp(rig,pid)
    _assert_actual_conflict_and_healthy_scope(rig,harness,converted,scoped,entry.exec_id,field,value)
    assert all(entry.exec_id not in {fill.exec_id for fill in result} for result in scoped), (
        'historical row belongs only to the wide legacy query; dedicated today query must not include it')
    assert _p1(rig),'historical receipt proof cannot waive a contradictory nonempty native identity'
    assert _fills(rig)==before[0] and _markers(rig)==before[1]
    assert _writes(rig)==before[2] and _tp_requests(rig)==[]
    row=rig.store.protection(pid)
    assert row['state']=='WORKING' and row['confirmed_remaining']==row['pending_max_remaining']==3
    assert rig.broker.orders[ref].remaining==3 and ref not in rig.broker.protection_pending_modifications
    assert not [body for title,body in rig.channel.messages if 'P0' in title]
    _safety(rig)


def test_B27b_review_correct_today_account_row_cannot_hide_client_same_exec_positive_conId_conflict(
        overnight_native,monkeypatch):
    rig,pid,harness=overnight_native(visibility='trade-log-7day')
    _,ref,tp1_fill=_filled_tp1(rig,pid)
    bad_con_id=tp1_fill.con_id+1
    assert type(bad_con_id) is int and bad_con_id>0
    converted,scoped=_conflict_only_legacy_client(rig,harness,monkeypatch,tp1_fill.exec_id,con_id=bad_con_id)
    before=_fills(rig),_markers(rig),_writes(rig),rig.store.protection(pid)['trigger_price']
    _settle(rig);_at_tp2(rig,pid)
    _assert_actual_conflict_and_healthy_scope(rig,harness,converted,scoped,tp1_fill.exec_id,'con_id',bad_con_id)
    correct=[fill for result in scoped for fill in result if fill.exec_id==tp1_fill.exec_id]
    assert correct and all(fill.con_id==tp1_fill.con_id for fill in correct), (
        'the dedicated scoped account source must preserve the genuine conId')
    assert _p1(rig),'matching scoped account row cannot erase a conflicting visible client row'
    assert _fills(rig)==before[0] and _markers(rig)==before[1] and _writes(rig)==before[2]
    row=rig.store.protection(pid)
    assert row['state']=='WORKING' and row['confirmed_remaining']==row['pending_max_remaining']==2
    assert row['trigger_price']==before[3] and ref not in rig.broker.protection_pending_modifications
    assert rig.broker.orders[ref].remaining==2 and len(_tp_requests(rig))==1
    assert not [body for title,body in rig.channel.messages if 'P0' in title]
    _safety(rig)

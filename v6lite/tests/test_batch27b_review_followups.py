"""27b follow-up: complete scoped d counts remain visible on identity failure.

These are presentation guards over real native account-query results. Scope
and counts are never manufactured from markers, missing/failed queries or the
wide legacy client source. Existing trading/identity assertions stay intact.
"""
from __future__ import annotations

import re

import pytest

from v6lite.tests.test_batch26_overnight_evidence import (
    _attempt_tp, _no_network, _safety, _settle, _tp_requests, _writes,
)
from v6lite.tests.test_batch27b_current_day_evidence import (
    _at_tp2, _filled_tp1, _fills, _markers, _p1, overnight_native,
)
from v6lite.tests.test_batch27b_review_edges import (
    _assert_actual_conflict_and_healthy_scope, _conflict_only_legacy_client,
)


@pytest.mark.parametrize('conflict,expected_count',[
    ('historical-intent',0),('historical-conId',0),('today-conId',1),
])
def test_B27b_complete_scoped_count_is_shown_even_when_explicit_identity_conflict_rejects_d(
        overnight_native,monkeypatch,conflict,expected_count):
    rig,pid,harness=overnight_native(visibility='trade-log-7day')
    if conflict=='today-conId':
        _,ref,target=_filled_tp1(rig,pid)
        protected=2
    else:
        target=rig._b27b_entry_fill
        ref=rig.store.protection(pid)['intent_id']
        protected=3
    field='intent_id' if conflict=='historical-intent' else 'con_id'
    value='v6l-b27b-count-wrong-history' if field=='intent_id' else target.con_id+1
    converted,scoped=_conflict_only_legacy_client(
        rig,harness,monkeypatch,target.exec_id,**{field:value})
    before=_fills(rig),_markers(rig),_writes(rig),rig.store.protection(pid)['trigger_price']
    if conflict=='today-conId':
        _settle(rig);_at_tp2(rig,pid)
    else:
        _attempt_tp(rig,pid)
    _assert_actual_conflict_and_healthy_scope(
        rig,harness,converted,scoped,target.exec_id,field,value)
    # Count only actual complete dedicated account responses. A historical
    # legacy row whose fallback timestamp happens to be today is not counted.
    actual_counts={len({fill.exec_id for fill in result if fill.symbol=='AAPL'}) for result in scoped}
    assert actual_counts=={expected_count},actual_counts
    pages=_p1(rig)
    assert pages and any('身份冲突' in page for page in pages),pages
    assert not [body for title,body in rig.channel.messages if 'P0' in title]
    assert _fills(rig)==before[0] and _markers(rig)==before[1] and _writes(rig)==before[2]
    row=rig.store.protection(pid)
    assert row['state']=='WORKING' and row['confirmed_remaining']==row['pending_max_remaining']==protected
    assert row['trigger_price']==before[3] and rig.broker.orders[ref].remaining==protected
    assert ref not in rig.broker.protection_pending_modifications
    assert len(_tp_requests(rig))==(1 if conflict=='today-conId' else 0)
    _safety(rig)
    assert all('当日可见成交=未完成' not in page for page in pages),pages
    pattern=r'当日可见成交[=：]\s*'+str(expected_count)+r'\s*条'
    assert any(re.search(pattern,page) for page in pages),pages

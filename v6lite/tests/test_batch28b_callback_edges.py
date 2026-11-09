"""B28b callback races in the queued stale-proof refresh, offline only.

The old P=3 value is a genuine snapshot taken before a real native decoded
original-STP execution reaches the ledger. Tests never edit SQLite, install
proof, override an authority decision, or change the original frozen specs.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from v6lite.models import Side
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _overnight_p1, _safety, _tp_requests, _writes,
)
from v6lite.tests.test_batch28b_overnight_refresh import (
    HIT, overnight_run, _audits, _assert_no_false_freeze,
)


def _stale_positions_with_stop_callback(rig, pid, monkeypatch, *, quantity):
    ref = rig.store.protection(pid)['intent_id']
    original = rig.broker.positions_fresh
    chronology = []
    def snapshot_then_fill():
        snapshot = original()
        if rig.engine._protection_query_position == pid and not chronology:
            assert next(pos.quantity for pos in snapshot if pos.symbol == 'AAPL') == 3
            chronology.append(('P3-snapshot', rig.clock.now()))
            rig.clock.advance(microseconds=250000)
            fill = rig._b28b_callback_fill(ref, quantity=quantity, price=338.62,
                                           exec_id=f'b28b-race-stop-{quantity}.01.01')
            assert fill.intent_id == ref and fill.side is Side.SELL
            assert fill.time_source == 'callback' and fill.time_uncertain
            assert rig.store.intent_filled_qty(ref) == quantity
            assert rig.store.position(pid)['qty_open'] == 3 - quantity
            chronology.append(('native-callback-booked', rig.clock.now()))
            _safety(rig)
            rig.clock.advance(microseconds=250000)
            chronology.append(('return-old-P3', rig.clock.now()))
        return snapshot
    monkeypatch.setattr(rig.broker, 'positions_fresh', snapshot_then_fill)
    return ref, chronology


def _start_at_price_refresh(rig):
    rig.at(HIT)
    rig.px('AAPL', 344.05)
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())


def test_B28b_review_original_stop_closes_during_queued_P_snapshot_without_false_freeze(
        overnight_run, monkeypatch):
    rig, pid, _ = overnight_run()
    row = rig.store.protection(pid)
    earlier = HIT - timedelta(minutes=5)
    # Create and later resolve an incident through the same real alert path;
    # this does not create broker evidence or alter the order/position state.
    rig.engine._protection_page(earlier, 'P0', 'evidence incomplete', row,
                                'synthetic evidence incident preceding the stop execution')
    key = 'protect_incident:' + pid + ':evidence incomplete'
    assert rig.store.control(key) is not None
    rig._b26_alerts.clear()
    rig.channel.messages.clear()
    before = _writes(rig)
    ref, chronology = _stale_positions_with_stop_callback(rig, pid, monkeypatch, quantity=3)
    _start_at_price_refresh(rig)
    assert [item[0] for item in chronology] == [
        'P3-snapshot', 'native-callback-booked', 'return-old-P3']
    assert rig.store.position(pid)['status'] == 'CLOSED'
    assert rig.store.position(pid)['qty_open'] == 0
    final = rig.store.protection(pid)
    assert final['state'] == 'FILLED'
    assert final['confirmed_remaining'] == final['pending_max_remaining'] == 0
    assert rig.store.intent(ref)['status'] == 'FILLED'
    assert rig.store.control(key) is None
    assert rig.store.control('protect_evidence_failure:' + pid) is None
    assert any(item.get('position_id') == pid
               for item in _audits(rig, 'PROTECT_QUERY_SUPERSEDED_BY_FILL'))
    assert _audits(rig, 'PROTECTION_INCIDENT_RESOLVED')
    _assert_no_false_freeze(rig)
    assert not [text for level, text in rig._b26_alerts
                if level == 'P0' and ('evidence' in text.lower() or '三项' in text)]
    assert _writes(rig) == before and _tp_requests(rig) == []
    # Subsequent normal ticks cannot resurrect the stale holding or incident.
    for _ in range(3):
        rig.advance(seconds=1)
        rig.tick()
    assert rig.store.position(pid)['status'] == 'CLOSED'
    assert _writes(rig) == before
    _assert_no_false_freeze(rig)
    _safety(rig)


def test_B28b_review_partial_stop_supersedes_old_round_but_new_contradiction_still_freezes(
        overnight_run, monkeypatch):
    rig, pid, harness = overnight_run()
    before = _writes(rig)
    ref, chronology = _stale_positions_with_stop_callback(rig, pid, monkeypatch, quantity=1)
    _start_at_price_refresh(rig)
    assert len(chronology) == 3
    assert rig.store.position(pid)['status'] == 'OPEN'
    assert rig.store.position(pid)['qty_open'] == 2
    assert rig.broker.orders[ref].remaining == 2
    assert _tp_requests(rig) == []
    assert rig.store.protection(pid)['tp_stage'] is None
    _assert_no_false_freeze(rig)
    assert len(rig.broker.sent) == before[0]
    assert len(rig.broker.sent_protections) == before[1]
    _safety(rig)
    # Only after the old round has returned do we introduce a real new d
    # contradiction. It must be observed in another complete scoped round.
    unknown = replace(rig._b26_entry_fill, exec_id='b28b-after-partial-unknown.01.01',
                      intent_id='', side=Side.BUY, quantity=1,
                      broker_order_id=9911, perm_id=9912, time=rig.clock.now())
    harness.transform = lambda rows: list(rows) + [unknown]
    completed = []
    for name in ('open_orders', 'positions_fresh', 'executions_today',
                 'account_executions_today_scoped'):
        original = getattr(rig.broker, name)
        def observe(*args, _name=name, _original=original, **kwargs):
            result = _original(*args, **kwargs)
            if rig.engine._protection_query_position == pid:
                completed.append((_name, rig.clock.now(), result))
            return result
        monkeypatch.setattr(rig.broker, name, observe)
    deadline = rig.clock.now() + timedelta(seconds=90)
    while rig.clock.now() < deadline and not _overnight_p1(rig):
        rig.advance(seconds=1)
        rig.tick()
        _safety(rig)
    assert {'open_orders', 'positions_fresh', 'executions_today',
            'account_executions_today_scoped'} <= {name for name, _, _ in completed}
    assert any(any(pos.symbol == 'AAPL' and pos.quantity == 2 for pos in result)
               for name, _, result in completed if name == 'positions_fresh')
    scopes = [result for name, _, result in completed if name == 'account_executions_today_scoped']
    ended = {request_id for request_id, _ in harness.ends}
    assert any(result.scope.get('complete') is True and
               result.scope.get('request_id') in ended and
               any(fill.exec_id == unknown.exec_id for fill in result) for result in scopes)
    pages = _overnight_p1(rig)
    assert pages and any('归属' in page or '未知' in page for page in pages), pages
    assert _audits(rig, 'OVERNIGHT_TP_FROZEN')
    assert _tp_requests(rig) == [] and rig.store.protection(pid)['tp_stage'] is None
    assert rig.store.position(pid)['qty_open'] == 2
    assert len(rig.broker.sent) == before[0]
    assert len(rig.broker.sent_protections) == before[1]
    for modification in rig.broker.modified_protections[before[2]:]:
        assert modification.intent_id == ref and modification.quantity <= 3
        assert modification.trigger_price == 338.62
    _safety(rig)

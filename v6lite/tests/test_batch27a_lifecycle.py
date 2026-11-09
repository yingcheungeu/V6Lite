"""27a independent 10/16 lifecycle/query/notification regressions.

Only dates, ordering and explicitly disclosed IDs originate in the request.
Prices in generic rigs, conIds, permIds and transport fixtures are synthetic.
All callbacks, queries, SQLite writes and renders use real production methods.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.fake import FakeBroker
from v6lite.models import Purpose, Side, StatusEvent
from v6lite.protection_engine import ProtectionEvidenceQueryError
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch24_pacing import _opcode
from v6lite.tests.test_batch25_restart import _native_server
from v6lite.tests.test_batch26_overnight_evidence import (
    _attempt_tp, _no_network, _overnight_p1, _protection_config,
    _restart, _safety, _tp_requests, _writes,
)
from v6lite.tests.test_batch27a_execution_time import (
    _audits, _fill_row, _time_key, callback_rig, replay_history,
    seed_overnight,
)


def _evidence_p0(rig):
    return [text for level, text in rig._b26_alerts
            if level == 'P0' and any(word in text for word in ('evidence incomplete', '三项证据', '证据不完整'))]


def _old_evidence_episode(rig, pid):
    row = rig.store.protection(pid)
    earlier = et(9, 20, 54, day=(2026, 10, 14))
    rig.engine._protection_page(earlier, 'P0', 'evidence incomplete', row,
                               'synthetic old evidence incident, reproducing disclosed first_at')
    key = 'protect_incident:' + pid + ':evidence incomplete'
    episode = json.loads(rig.store.control(key))
    assert episode['first_at'] == earlier.isoformat() and episode['p0_sent'] is True
    return key


@pytest.mark.parametrize('callback', [False, True], ids=['verified-history', 'callback-history'])
def test_B27a_fresh_healthy_overnight_stop_ends_old_evidence_incident_without_granting_full_history(
        replay_history, tmp_path, monkeypatch, callback):
    rig, pid = replay_history(callback=callback)
    _restart(rig, tmp_path, monkeypatch)
    key = _old_evidence_episode(rig, pid)
    rig._b26_alerts.clear()
    rig.engine._protection_progress(rig.clock.now())
    assert rig.store.control(key) is None, 'fresh unchanged covered stop must end its old evidence episode'
    assert _audits(rig, 'PROTECTION_INCIDENT_RESOLVED')
    row = rig.store.protection(pid)
    proof = rig.engine._protection_notice_evidence[pid]
    assert proof['full_exec'] is False, 'closing a notification episode cannot invent prior-day execution coverage'
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
    assert _evidence_p0(rig) == []
    # A newly failed *request* uses a new F4 clock, not October 14's episode.
    rig.advance(seconds=16); rig.broker.fail_open_orders = True
    start = rig.clock.now()
    rig.engine._protection_progress(start)
    failure = json.loads(rig.store.control('protect_evidence_failure:' + pid))
    assert failure['since'] == start.isoformat() and failure['paged'] is False
    assert _evidence_p0(rig) == []
    rig.advance(seconds=rig.cfg.protection.evidence_fail_p0_seconds)
    rig.engine._protection_evidence_deadline(rig.clock.now(), rig.store.protection(pid))
    assert len(_evidence_p0(rig)) == 1, 'real ongoing new failures must still escalate at the unchanged F4 deadline'
    _safety(rig)


def test_B27a_1016_Submitted_then_STP_fill_inside_query_closes_ledger_without_false_P0(
        replay_history, tmp_path, monkeypatch):
    rig, pid = replay_history(callback=True)
    rig.at(et(14, 55, 44, day=(2026, 10, 16)) + timedelta(microseconds=153000))
    _restart(rig, tmp_path, monkeypatch)
    key = _old_evidence_episode(rig, pid)
    ref = rig.store.protection(pid)['intent_id']
    # .153 Submitted; active three-source query captures S=3 and P=3.
    rig.broker.status(ref, 'Submitted'); rig.engine._drain(rig.clock.now())
    rig._b26_alerts.clear(); rig.channel.messages.clear()
    original = rig.broker.executions_today
    sequence = []
    def query_with_urgent_fill():
        sequence.append(('executions-enter', rig.clock.now()))
        rig.clock.advance(microseconds=513000)  # .666: actual fill arrives during request.
        raw = rig.broker.fill(ref, qty=3, price=113.0, commission=None,
                              exec_id='0000e0d5.6ad25378.01.01', emit=False)
        callback = replace(raw, time_source='callback', time_uncertain=True)
        query = replace(raw, time_source='reqExecutions', time_uncertain=False)
        rig.broker.orders[ref].fills[-1] = query
        rig.engine._on_urgent_broker_event(callback)
        sequence.append(('ledger-closed', rig.clock.now()))
        assert rig.store.position(pid)['status'] == 'CLOSED'
        assert rig.store.position(pid)['qty_open'] == 0
        assert rig.store.intent(ref)['status'] == 'FILLED'
        rig.clock.advance(microseconds=274000)  # .940: query resumes with its stale P=3 snapshot.
        return original()
    monkeypatch.setattr(rig.broker, 'executions_today', query_with_urgent_fill)
    before = _writes(rig)
    rig.engine._protection_progress(rig.clock.now())
    assert [name for name, _ in sequence] == ['executions-enter', 'ledger-closed']
    assert _evidence_p0(rig) == [], 'a proved own STOP close plus one in-flight stale query is audit-only'
    assert rig.store.control(key) is None
    assert rig.store.control('protect_evidence_failure:' + pid) is None
    assert rig.store.position(pid)['status'] == 'CLOSED' and rig.store.position(pid)['qty_open'] == 0
    row = rig.store.protection(pid)
    assert row['state'] == 'FILLED' and row['confirmed_remaining'] == row['pending_max_remaining'] == 0
    assert _writes(rig) == before and _tp_requests(rig) == []
    assert _audits(rig, 'PROTECT_VERIFICATION_BACKOFF') or _audits(rig, 'PROTECTION_INCIDENT_RESOLVED')
    _safety(rig)


@pytest.mark.parametrize('failure', ['short-position', 'external-sell-working'])
def test_B27a_real_short_or_external_sell_is_never_excused_as_a_closed_fill_query_race(
        replay_history, tmp_path, monkeypatch, failure):
    rig, pid = replay_history(callback=True)
    _restart(rig, tmp_path, monkeypatch)
    rig._b26_alerts.clear()
    if failure == 'short-position':
        rig.broker.external_positions['AAPL'] = (-4.0, 113.13)
    else:
        from v6lite.models import OrderRequest
        req = OrderRequest('synthetic-manual-sell', 'AAPL', Side.SELL, 1, 113.13,
                           Purpose.TP1, pid)
        # TWS is an external actor, not a Lite order-boundary call. Its native
        # injection deliberately breaks S+X<=P; this is NOT a certified-safe
        # initial Lite state. Preserve insertion oracles on all Lite new orders.
        rig.broker._register(req)
        containment = []
        ref = rig.store.protection(pid)['intent_id']
        def reduce_existing(request, **kwargs):
            assert request.intent_id == ref
            native = rig.broker.orders[ref]
            assert request.quantity <= native.req.quantity
            external = rig.broker.orders[req.intent_id].remaining
            held = next(p.quantity for p in FakeBroker.positions_fresh(rig.broker) if p.symbol == 'AAPL')
            assert max(0, request.quantity - native.filled) + external <= held
            containment.append(('reduce', request.quantity))
            return FakeBroker.modify_protection(rig.broker, request, **kwargs)
        def cancel_existing(intent_id, **kwargs):
            assert intent_id == ref
            containment.append(('cancel', intent_id))
            return FakeBroker.cancel(rig.broker, intent_id, **kwargs)
        # A pre-existing external violation cannot satisfy the unchanged global
        # oracle *before* containment. Only this branch's reduce/cancel adapters
        # use monotonic containment assertions; submit guards remain untouched.
        monkeypatch.setattr(rig.broker, 'modify_protection', reduce_existing)
        monkeypatch.setattr(rig.broker, 'cancel', cancel_existing)
        original = rig.broker.open_orders
        monkeypatch.setattr(rig.broker, 'open_orders', lambda: [
            replace(order, ours=False) if order.intent_id == req.intent_id else order for order in original()])
    rig.engine._protection_progress(rig.clock.now())
    assert any(level == 'P0' for level, _ in rig._b26_alerts), rig._b26_alerts
    assert rig.store.position(pid)['qty_open'] == 3 and _tp_requests(rig) == []
    if failure == 'external-sell-working':
        from v6lite.tests.test_batch26_overnight_evidence import _settle
        _settle(rig)
        assert containment and rig.store.control('frozen:AAPL') == 'external_sell'
        assert len(rig.broker.sent_protections) == 1
        _safety(rig)  # Unchanged strict capacity oracle resumes after native ACK.


@pytest.mark.parametrize('failure', ['freeze-policy', 'callback-too-recent', 'unknown-today-execution'])
def test_B27a_quoted_TP_while_frozen_never_starts_stage_and_keeps_15_second_query_cadence(
        replay_history, tmp_path, monkeypatch, caplog, failure):
    rig, pid = replay_history(enabled=failure != 'freeze-policy')
    if failure == 'callback-too-recent':
        # Only the receipt provenance is damaged; native STP remains healthy.
        marker = json.loads(rig.store.control(_time_key()))
        marker['identity'][8] += 1
        rig.store.set_control(rig.clock.now(), _time_key(), json.dumps(marker))
    elif failure == 'unknown-today-execution':
        from v6lite.models import FillEvent
        rig._b26_visible_extra.append(FillEvent(
            'b27a-d-unattributed.01.01', '', 'AAPL', Side.BUY, 1, 113.13,
            rig.clock.now(), con_id=rig.store.protection(pid)['con_id']))
    # Fake positions() normally calls its fresh API. Real IB's method is a
    # streamed cache; this adapter-only delegation avoids counting cache reads
    # as new requests while preserving real active evidence methods.
    monkeypatch.setattr(rig.broker, 'positions', lambda: FakeBroker.positions_fresh(rig.broker))
    rig.cfg = replace(rig.cfg, reconcile_interval_seconds=3600)
    _restart(rig, tmp_path, monkeypatch)
    rig.tick()
    ref = rig.store.protection(pid)['intent_id']
    before = _writes(rig)
    stages_before = {kind: len(_audits(rig, kind)) for kind in ('STAGE_BEGIN', 'STAGE_ABANDONED')}
    calls = []
    channels = ('open_orders', 'positions_fresh', 'executions_today', 'account_executions_today')
    for name in channels:
        original = getattr(rig.broker, name)
        def query(*args, _name=name, _original=original, **kwargs):
            calls.append((_name, rig.clock.now())); return _original(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, query)
    start = rig.clock.now()
    caplog.set_level(logging.INFO)
    for index in range(61):
        rig.advance(seconds=1)
        # Cross repeatedly above and below TP1; quote churn must not create
        # STAGE_BEGIN/STAGE_ABANDONED for already denied overnight authority.
        pos = rig.store.position(pid)
        rig.px('AAPL', float(pos['tp1']) + .20 if index % 2 == 0 else 113.13)
        rig.tick()
        _safety(rig)
    for channel in channels:
        actual = [(at-start).total_seconds() for name, at in calls if name == channel]
        expected = [] if failure == 'freeze-policy' and channel == 'account_executions_today' else [15, 30, 45, 60]
        assert actual == expected, (channel, actual)
    assert _writes(rig) == before and _tp_requests(rig) == []
    assert {kind: len(_audits(rig, kind)) for kind in stages_before} == stages_before
    notes = [item for item in _audits(rig, 'OVERNIGHT_TP_FROZEN') if item['position_id'] == pid]
    assert len(notes) == 1, 'at most one reached-TP-but-frozen record for this holding and day'
    assert not [record for record in caplog.records if '[EXIT_TRIGGER]' in record.message]
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.broker.orders[ref].remaining == 3 and len(_overnight_p1(rig)) == 1
    # Restarting does not reset the one-per-day audit rule.
    _restart(rig, tmp_path, monkeypatch, reopen=True)
    _attempt_tp(rig, pid)
    assert len([item for item in _audits(rig, 'OVERNIGHT_TP_FROZEN') if item['position_id'] == pid]) == 1


def test_B27a_zero_duration_query_still_sends_actual_native_requests(
        tmp_path, monkeypatch, request):
    adapter, clock, trace, wire, _, _, _ = _native_server(tmp_path, monkeypatch, request)
    adapter.connect()
    before = len(wire)
    adapter.open_orders(); adapter.positions_fresh(); adapter.executions_today()
    packets = [message for _, message in wire[before:]]
    assert Counter(_opcode(message) for message in packets) == Counter({'16': 1, '61': 1, '7': 1}), packets
    assert all(('fresh', label + '-request') in trace for label in ('openOrders', 'positions', 'executions'))
    assert not [message for message in packets if _opcode(message) in {'3', '4'}]


def test_B27a_close_notice_never_presents_sizing_reserve_as_observed_commission(tmp_path):
    rig = Rig(tmp_path, start=et(9, 50, day=(2026, 10, 16)),
              protection=_protection_config(True), risk_per_trade_pct=.0015)
    try:
        rig.start(); rig.at(et(10, 0, 30, day=(2026, 10, 16))); rig.tick()
        [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        rig.broker.fill(entry.intent_id, qty=3, price=342.53, commission=1.0)
        rig.px('AAPL', 342.53)  # Synthetic quote agrees with the booked cost.
        rig.advance(seconds=1); rig.tick()
        [pos] = rig.store.open_positions(); pid = pos['position_id']
        ref = rig.store.protection(pid)['intent_id']
        rig.channel.messages.clear()
        rig.broker.fill(ref, qty=3, price=340.50, commission=None)
        rig.engine._drain(rig.clock.now())
        [trade] = rig.store.closed_trades()
        assert trade['gross'] == pytest.approx(-6.09) and trade['fees'] == 1.0 and len(trade['missing']) == 1
        rig.advance(seconds=3); rig.engine._drain(rig.clock.now())
        notices = [body for title, body in rig.channel.messages if title == '🟢 持仓已关闭']
        assert len(notices) == 1
        text = notices[0]
        assert '-9.09' not in text, 'the $2 sizing/funds reserve is not a known same-class commission'
        if '未计' in text or 'not included' in text.lower() or 'excluded' in text.lower():
            assert '-7.09' in text, text  # gross minus known $1 only, explicitly incomplete.
        else:
            assert '-8.09' in text and ('估' in text or 'estimated' in text.lower()), text
        # Late actual $1.02 is booked once; quantities/gross never change.
        fill = rig.broker.orders[ref].fills[-1]
        rig.store.record_commission(rig.clock.now(), fill.exec_id, 1.02)
        assert rig.store.position_net_pnl(pid) == pytest.approx(-8.11)
        assert rig.store.position(pid)['qty_open'] == 0 and rig.store.position(pid)['realized_pnl'] == pytest.approx(-6.09)
    finally:
        rig.alerter.close(); rig.store.close()



def test_B27a_prior_close_unavailable_retries_each_five_minutes_and_only_actual_close_clears_entry_block(
        replay_history, tmp_path, monkeypatch):
    rig, pid = replay_history(callback=False, enabled=False)
    rig.at(et(7, 21, 56, day=(2026, 10, 19)))  # 04:21:56 PT, premarket.
    rig.cfg = replace(rig.cfg, reconcile_interval_seconds=3600)
    rig.broker.prev_close['AAPL'] = None
    calls = []
    original = rig.broker.previous_close
    def previous_close(symbol):
        calls.append((symbol, rig.clock.now())); return original(symbol)
    monkeypatch.setattr(rig.broker, 'previous_close', previous_close)
    _restart(rig, tmp_path, monkeypatch)
    rig.tick()
    assert len(calls) == 1 and calls[0][0] == 'AAPL'
    attempted = calls[0][1]
    assert rig.engine.flags.day_risk_error == 'prior_close:AAPL'
    summary = rig.engine.status_snapshot(rig.clock.now())
    assert any('baseline' in block and 'AAPL' in block for block in summary['entry_blocks'])
    assert summary['entries_allowed'] is False
    card = rig.engine._startup_card(rig.clock.now(), summary)
    visible = card.as_plain_text()
    assert '前收盘价' in visible and '5 分钟' in visible and '重试' in visible, visible
    assert '开盘自动恢复' not in visible, 'recovery depends on a real valid history reply, not the wall clock'
    before = _writes(rig)
    rig.advance(seconds=299); rig.tick()
    assert calls == [('AAPL', attempted)], calls
    assert rig.engine.flags.day_risk_error == 'prior_close:AAPL'
    assert rig.engine.status_snapshot(rig.clock.now())['entries_allowed'] is False
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
    assert rig.broker.orders[row['intent_id']].remaining == 3
    # Only a successful native history reply at the next permitted attempt can
    # clear the baseline flag; no assertion equates normality with lack of data.
    rig.broker.prev_close['AAPL'] = 113.13
    rig.advance(seconds=1); rig.tick()
    assert [int((at-attempted).total_seconds()) for _, at in calls] == [0, 300]
    assert rig.engine.flags.day_risk_error is None
    assert not [block for block in rig.engine.status_snapshot(rig.clock.now())['entry_blocks'] if 'baseline' in block]
    assert rig.store.control('prior_close:2026-10-19:AAPL') == repr(113.13)
    assert _writes(rig) == before and _tp_requests(rig) == []
    _safety(rig)

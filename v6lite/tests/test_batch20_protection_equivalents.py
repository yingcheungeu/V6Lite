"""Batch 20: 27 legacy contracts with protection enabled and FakeBroker.

STOP is carried by the existing GTC, not a second local DAY STOP. The former
three local retries become a durable 60s unchanged-quantity timer and P0.
Release uses actual broker fills, exact GTC cancellation and fresh evidence.
"""
from __future__ import annotations
import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine, StartupError
from v6lite.models import CommissionEvent, FillEvent, IntentStatus, OrderRequest, Purpose, Side
from v6lite.risk import cooldown_until, loss_state, trade_net
from v6lite.tests.helpers import Rig, buy_bars, et, seed_closed_trade
DAY2 = (2026, 10, 7)

EQUIVALENT_MAP = {
    'test_engine::test_breakeven_stop_after_tp1_sells_remaining': 'test_breakeven_stop_after_tp1_sells_remaining_protection_on',
    'test_engine::test_exit_retries_then_escalates': 'test_exit_retries_then_escalates_protection_on',
    'test_review_fixes::test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions': 'test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions_protection_on',
    'test_review_round2::test_C2_prior_day_held_sell_never_blocks_the_stop': 'test_C2_prior_day_held_sell_never_blocks_the_stop_protection_on',
    'test_review_round2::test_C5_exit_orders_are_stamped_with_the_real_send_time': 'test_C5_exit_orders_are_stamped_with_the_real_send_time_protection_on',
    'test_review_round2::test_R8_commission_arriving_during_scan_can_latch': 'test_R8_commission_arriving_during_scan_can_latch_protection_on',
    'test_review_round2::test_race_late_fill_of_cancelled_stop_cancels_the_retry': 'test_race_late_fill_of_cancelled_stop_cancels_the_retry_protection_on',
    'test_review_round3::test_Q7_queued_exit_work_is_inside_the_protection_budget': 'test_Q7_queued_exit_work_is_inside_the_protection_budget_protection_on',
    'test_review_round3::test_SR3_blocked_exit_cannot_livelock_the_tick': 'test_SR3_blocked_exit_cannot_livelock_the_tick_protection_on',
    'test_review_round3::test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit': 'test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit_protection_on',
    'test_review_round4_selfreview::test_a_stop_blocked_by_missing_evidence_pages_p0': 'test_a_stop_blocked_by_missing_evidence_pages_p0_protection_on',
    'test_review_round4_selfreview::test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once': 'test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once_protection_on',
    'test_review_round4::test_F1_failed_fresh_order_query_refuses_the_sell': 'test_F1_failed_fresh_order_query_refuses_the_sell_protection_on',
    'test_review_round4::test_F7_exit_is_re_decided_with_the_latest_quote_before_sending': 'test_F7_exit_is_re_decided_with_the_latest_quote_before_sending_protection_on',
    'test_review_round4::test_F8_back_off_is_honoured_when_the_queued_item_runs': 'test_F8_back_off_is_honoured_when_the_queued_item_runs_protection_on',
    'test_review_round4::test_missed_buy_freezes_then_flatten_and_release': 'test_missed_buy_freezes_then_flatten_and_release_protection_on',
    'test_review_round4::test_price_only_correction_blocks_entries_while_exits_continue': 'test_price_only_correction_blocks_entries_while_exits_continue_protection_on',
    'test_review_round4::test_prior_day_partial_entry_is_frozen_then_released': 'test_prior_day_partial_entry_is_frozen_then_released_protection_on',
    'test_review_round5_selfreview::test_late_buy_after_release_of_a_recovery_position_is_evidence_only': 'test_late_buy_after_release_of_a_recovery_position_is_evidence_only_protection_on',
    'test_review_round5_selfreview::test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger': 'test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger_protection_on',
    'test_review_round5::test_R4F1_release_needs_the_position_stream_to_agree': 'test_R4F1_release_needs_the_position_stream_to_agree_protection_on',
    'test_review_round5::test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released': 'test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released_protection_on',
    'test_review_round5::test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0': 'test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0_protection_on',
    'test_review_round6::test_R5F2_old_order_without_a_position_row_never_books_after_flat': 'test_R5F2_old_order_without_a_position_row_never_books_after_flat_protection_on',
    'test_review_round6::test_R5F3_batch4_state_release_record_alone_is_a_flat_point': 'test_R5F3_batch4_state_release_record_alone_is_a_flat_point_protection_on',
    'test_review_round6::test_SR6_A2_stale_position_stream_never_silences_a_stop': 'test_SR6_A2_stale_position_stream_never_silences_a_stop_protection_on',
    'test_review_round7::test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state': 'test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state_protection_on',
}

def _rig(tmp_path, **kw):
    return Rig(tmp_path, protection=ProtectionConfig(enabled=True), **kw)

def _entries(rig):
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]

def _tps(rig):
    return [r for r in rig.broker.sent if r.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}]

def _no_local_stop(rig):
    assert not [r for r in rig.broker.sent if r.purpose == Purpose.STOP and r.intent_id.startswith('v6l-')]

def _working(rig, pid, qty):
    row = rig.store.protection(pid)
    assert row is not None and row['state'] == 'WORKING'
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == qty
    order = rig.broker.orders[row['intent_id']]
    assert order.remaining == qty and order.status == 'Submitted'
    assert order.broker_order_id == row['broker_order_id']
    assert row['perm_id'] == order.broker_order_id + 50_000
    assert rig.store.intent(row['intent_id'])['purpose'] == Purpose.PROTECT
    _no_local_stop(rig)
    return row

def _open(rig):
    entry = rig.open_aapl()
    [pos] = rig.store.open_positions()
    assert pos['qty_open'] == 20 and pos['entry_price'] == pytest.approx(113.47)
    return entry, pos, _working(rig, pos['position_id'], 20)

def _two_open(rig):
    rig.broker.bars['SPY'] = buy_bars(); rig.px('SPY', 113.13)
    rig.start(); rig.at(et(10, 0, 30)); rig.tick()
    for entry in _entries(rig):
        rig.broker.fill(entry.intent_id)
    rig.advance(seconds=1); rig.tick()
    positions = rig.store.open_positions()
    assert sorted(p['symbol'] for p in positions) == ['AAPL', 'SPY']
    for pos in positions:
        _working(rig, pos['position_id'], pos['qty_open'])
    return positions

def _at_price(rig, px, seconds=31):
    rig.advance(seconds=seconds); rig.px('AAPL', px); rig.tick()

def _settle(rig):
    rig.engine._drain(rig.clock.now()); rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now()); rig.engine.process_protection_priority()

def _close_gtc(rig, pos, row, price=107.0):
    rig.broker.fill(row['intent_id'], price=price)
    rig.advance(seconds=1); rig.tick()
    assert rig.store.position(pos['position_id'])['status'] == 'CLOSED'
    assert rig.store.position(pos['position_id'])['qty_open'] == 0
    assert rig.store.protection(pos['position_id'])['state'] == 'FILLED'
    _no_local_stop(rig)

def _manual_flatten(rig, row, quantity):
    """Operator cancels exact GTC before the genuine external flatten fill."""
    req = OrderRequest('tws-flat-' + row['position_id'], 'AAPL', Side.SELL, quantity, 100.0, Purpose.STOP)
    rig.broker._register(req)
    fill = rig.broker.fill(req.intent_id, price=100.0)
    rig.broker.drain_events()  # external fill remains authoritative account evidence
    rig.broker.confirm_cancel(row['intent_id'])
    rig.engine._drain(rig.clock.now())  # keep exact CANCEL_SEEN callback and identity
    assert not rig.broker.positions_fresh()
    return fill

def _request_release(rig):
    rig.store.set_control(rig.clock.now(), 'release:AAPL', json.dumps({
        'operator': 'synthetic-operator', 'reason': 'verified flat with exact cancelled GTC'}))
    return rig.engine._apply_releases(rig.clock.now())

def _release_verified(rig, pos, row, monkeypatch=None):
    rig.advance(seconds=61)
    counts = {'open_orders': 0, 'positions_fresh': 0, 'account_executions_today': 0}
    if monkeypatch is not None:
        for method in counts:
            original = getattr(rig.broker, method)
            def counted(original=original, method=method):
                counts[method] += 1
                return original()
            monkeypatch.setattr(rig.broker, method, counted)
    writes = tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections,
                                   rig.broker.modified_protections, rig.broker.cancels))
    _request_release(rig)
    assert rig.store.position(pos['position_id'])['status'] == 'RELEASED', rig.messages()
    assert rig.store.open_positions() == [] and rig.store.control('frozen:AAPL') is None
    assert rig.store.intent(row['intent_id'])['status'] == IntentStatus.CANCELLED
    assert rig.store.release_times()
    assert tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections,
                                  rig.broker.modified_protections, rig.broker.cancels)) == writes
    if monkeypatch is not None:
        assert all(n >= 2 for n in counts.values()), counts
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='RELEASE_GTC_VERIFIED'").fetchone()
    _no_local_stop(rig)

def _released_rig(tmp_path, monkeypatch=None):
    rig = _rig(tmp_path)
    entry, pos, row = _open(rig); _manual_flatten(rig, row, 20)
    _release_verified(rig, pos, row, monkeypatch)
    return rig, entry, pos, row

def test_breakeven_stop_after_tp1_sells_remaining_protection_on(tmp_path):
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    identity = (row['intent_id'], row['broker_order_id'], row['perm_id'])
    _at_price(rig, 120.0); [tp] = _tps(rig)
    assert tp.purpose == Purpose.TP1 and tp.quantity == 6
    assert rig.broker.orders[row['intent_id']].remaining + tp.quantity == 20
    rig.broker.fill(tp.intent_id); _at_price(rig, 118.0); _settle(rig)
    latest = _working(rig, pos['position_id'], 14)
    assert (latest['intent_id'], latest['broker_order_id'], latest['perm_id']) == identity
    assert latest['trigger_price'] == pytest.approx(113.53)
    assert rig.store.position(pos['position_id'])['breakeven_stop'] == pytest.approx(113.53)
    _at_price(rig, 113.50)
    episode = json.loads(rig.store.control('protect_trigger:' + pos['position_id']))
    assert episode['remaining'] == 14 and episode['p1_sent']
    _close_gtc(rig, pos, latest, 113.50)
    assert len(rig.broker.sent_protections) == 1

def test_exit_retries_then_escalates_protection_on(tmp_path, caplog):
    """GTC remains authoritative; durable 60s unfilled timer replaces local retries."""
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    _at_price(rig, row['trigger_price'] - 0.01)
    key = 'protect_trigger:' + pos['position_id']; first = json.loads(rig.store.control(key))
    assert first['remaining'] == 20 and first['p1_sent'] and not first['p0_sent']
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds - 1); rig.tick()
    assert not json.loads(rig.store.control(key))['p0_sent']
    rig.advance(seconds=1); rig.tick(); final = json.loads(rig.store.control(key))
    assert final['started_at'] == first['started_at'] and final['p0_sent']
    assert any('P0' in s and 'AAPL' in b for s, b in rig.channel.messages)
    assert 'trigger unfilled' in caplog.text
    assert len(rig.broker.sent_protections) == 1 and rig.broker.cancels == []
    _working(rig, pos['position_id'], 20); _close_gtc(rig, pos, row)

def test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions_protection_on(tmp_path):
    rig = _rig(tmp_path); rig.start()
    seed_closed_trade(rig.store, 'pos-prior', -10.0, et(9, 40))
    rig.at(et(10, 0, 30)); rig.tick(); [entry] = _entries(rig)
    rig.broker.fill(entry.intent_id, price=113.47, commission=0.0)
    rig.advance(seconds=1); rig.tick(); [pos] = rig.store.open_positions()
    rig.store.conn.execute("UPDATE positions SET tp1=113.49,tp2=113.6,tp3=113.7 WHERE status='OPEN'")
    for stage in (Purpose.TP1, Purpose.TP2, Purpose.TP3):
        _at_price(rig, 113.8); row = rig.store.protection(pos['position_id'])
        if row['state'] == 'PROTECT_CANCEL_PENDING':
            rig.broker.confirm_cancel(row['intent_id']); _settle(rig)
        req = next(r for r in reversed(_tps(rig)) if r.purpose == stage)
        rig.broker.fill(req.intent_id, price=113.495, commission=None, commission_separately=True)
        rig.advance(seconds=1); rig.tick(); _settle(rig)
    assert rig.store.open_positions() == []
    trade = next(t for t in rig.store.closed_trades() if t['position_id'] == pos['position_id'])
    assert trade['gross'] == pytest.approx(0.5) and len(trade['missing']) == 3
    prior_until = cooldown_until(rig.store, rig.cfg)
    assert trade_net(trade) < 0 and prior_until is not None
    for fill in rig.store.conn.execute("SELECT exec_id FROM fills WHERE side='SELL'"):
        rig.broker.events.append(CommissionEvent(fill['exec_id'], 0.01))
    rig.advance(seconds=1); rig.tick()
    trade = next(t for t in rig.store.closed_trades() if t['position_id'] == pos['position_id'])
    assert not trade['missing'] and trade_net(trade) == pytest.approx(0.47)
    assert loss_state(rig.store, rig.cfg).derived_cooldown_until is None
    assert loss_state(rig.store, rig.cfg).cooldown_until == prior_until  # B30 keeps triggered deadline
    assert loss_state(rig.store, rig.cfg).streak == 0
    _no_local_stop(rig)

def test_C2_prior_day_held_sell_never_blocks_the_stop_protection_on(tmp_path):
    """Legacy held DAY evidence does not cancel a previously confirmed full GTC."""
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    # Historical expired DAY sell has an unprovable ledger conclusion and
    # claims no working shares. This is a recovery-state prerequisite.
    old = OrderRequest('v6l-held-old-tp', 'AAPL', Side.SELL, 6, 120.0, Purpose.TP1, pos['position_id'])
    rig.store.create_intent(et(15, 58), old, atr=None, count_entry_day=None)
    rig.store.update_intent(et(16, 2), old.intent_id, status=IntentStatus.UNKNOWN,
                           hold_reason='prior_day_unverifiable')
    rig.broker._register(old); rig.broker.confirm_cancel(old.intent_id); rig.broker.drain_events()
    rig.at(et(9, 45, day=DAY2)); rig.tick()
    assert rig.store.intent(old.intent_id)['hold_reason'] == 'prior_day_unverifiable'
    _at_price(rig, 100.0)
    assert rig.broker.orders[row['intent_id']].remaining == 20
    assert row['intent_id'] not in rig.broker.cancels
    assert rig.engine.status_snapshot(rig.clock.now())['entry_blocks']
    _close_gtc(rig, pos, row, 100.0)
    assert len(rig.broker.sent_protections) == 1

def test_C5_exit_orders_are_stamped_with_the_real_send_time_protection_on(tmp_path, monkeypatch):
    """Protected TP is timestamped after slow evidence at the actual DAY send."""
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    real = rig.broker.open_orders; timestamps = []
    def slow():
        rig.advance(seconds=17); return real()
    real_submit = rig.broker.submit_limit
    def submit(req):
        if req.side == Side.SELL:
            timestamps.append((req.intent_id, rig.clock.now().isoformat()))
        return real_submit(req)
    monkeypatch.setattr(rig.broker, 'open_orders', slow)
    monkeypatch.setattr(rig.broker, 'submit_limit', submit)
    _at_price(rig, 120.0); _settle(rig); [tp] = _tps(rig)
    assert timestamps == [(tp.intent_id, rig.store.intent(tp.intent_id)['sent_at'])]
    assert rig.store.intent(tp.intent_id)['sent_at'] > pos['opened_at']
    assert tp.quantity == 6 and rig.broker.orders[row['intent_id']].remaining == 14
    _no_local_stop(rig)

def test_R8_commission_arriving_during_scan_can_latch_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path, scan_times_et=('10:00', '11:00')); _, pos, row = _open(rig)
    rig.broker.bars['TSLA'] = buy_bars(); rig.px('TSLA', 113.13); rig.at(et(11, 0, 30))
    real = rig.broker.daily_bars; count = 0; fill = None
    def slow(symbol, days):
        nonlocal count, fill
        count += 1
        if count == 1:
            rig.px('AAPL', 107.0); rig.advance(seconds=31)
        elif count == 2:
            fill = rig.broker.fill(row['intent_id'], price=106.30, commission=None, commission_separately=True)
            rig.advance(seconds=1)
        elif count == 3:
            assert rig.store.position(pos['position_id'])['status'] == 'CLOSED'
            assert rig.store.risk_day('2026-10-06')['daily_stop_latched'] == 0
            rig.broker.events.append(CommissionEvent(fill.exec_id, 6.0)); rig.advance(seconds=1)
        return real(symbol, days)
    monkeypatch.setattr(rig.broker, 'daily_bars', slow); rig.tick()
    assert count >= 3 and rig.store.risk_day('2026-10-06')['daily_stop_latched'] == 1
    assert len(_entries(rig)) == 1
    trade = next(t for t in rig.store.closed_trades() if t['position_id'] == pos['position_id'])
    assert trade_net(trade) == pytest.approx(-150.4)
    _no_local_stop(rig)

def test_race_late_fill_of_cancelled_stop_cancels_the_retry_protection_on(tmp_path):
    """Replacement needs stable absence; old late execution pulls the new GTC."""
    rig = _rig(tmp_path); _, pos, old = _open(rig)
    rig.engine._protection_cancel(rig.clock.now(), old, 'test_verified_cancel_for_replacement')
    rig.broker.confirm_cancel(old['intent_id']); _settle(rig)
    for _ in range(3):
        rig.advance(seconds=1); rig.tick(); _settle(rig)
        if len(rig.broker.sent_protections) == 2: break
    new = rig.store.protection(pos['position_id'])
    assert new['intent_id'] != old['intent_id'] and len(rig.broker.sent_protections) == 2
    assert rig.broker.orders[new['intent_id']].remaining == 20
    rig.broker.fill(old['intent_id'], qty=20, price=107.0); rig.advance(seconds=1); rig.tick()
    assert new['intent_id'] in rig.broker.cancels
    assert rig.store.position(pos['position_id'])['qty_open'] == 0
    assert any(k.startswith('anomaly:late_exit_fill') for k in rig.store.anomalies())
    _no_local_stop(rig)

def test_Q7_queued_exit_work_is_inside_the_protection_budget_protection_on(tmp_path, monkeypatch):
    """GTC crossing observation stays <=31s apart during slow scan/TP work."""
    rig = _rig(tmp_path, scan_times_et=('10:00', '11:00')); positions = _two_open(rig)
    rig.at(et(10, 59, 20)); rig.px('AAPL', 120.0); rig.px('SPY', 120.0); rig.tick()
    checks = []; mark = rig.engine._protection_on_mark
    def record(now, row, px):
        if row['symbol'] == 'SPY': checks.append(rig.clock.now())
        return mark(now, row, px)
    monkeypatch.setattr(rig.engine, '_protection_on_mark', record)
    bars, vix, cancel = rig.broker.daily_bars, rig.broker.vix_close, rig.broker.cancel
    def slow_bars(symbol, days):
        rig.advance(seconds=10); return bars(symbol, days)
    def slow_vix():
        rig.advance(seconds=25); return vix()
    def slow_cancel(iid, **kw):
        rig.advance(seconds=15); return cancel(iid, **kw)
    monkeypatch.setattr(rig.broker, 'daily_bars', slow_bars)
    monkeypatch.setattr(rig.broker, 'vix_close', slow_vix)
    monkeypatch.setattr(rig.broker, 'cancel', slow_cancel)
    rig.at(et(11, 0, 5)); rig.px('AAPL', 107.0); rig.px('SPY', 107.0)
    checks.append(rig.clock.now()); rig.tick()
    for _ in range(60):
        rig.advance(seconds=1); rig.tick()
    gaps = [(b-a).total_seconds() for a,b in zip(checks, checks[1:])]
    assert gaps and max(gaps) <= rig.cfg.exit_check_interval_seconds + 1, gaps
    spy = next(p for p in positions if p['symbol'] == 'SPY'); row = rig.store.protection(spy['position_id'])
    assert rig.broker.orders[row['intent_id']].remaining == spy['qty_open']
    rig.broker.fill(row['intent_id'], price=107.0); rig.advance(seconds=1); rig.tick()
    assert rig.store.position(spy['position_id'])['status'] == 'CLOSED'
    _no_local_stop(rig)

def test_SR3_blocked_exit_cannot_livelock_the_tick_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path); positions = _two_open(rig)
    rig.broker._register(OrderRequest('tws-manual-1', 'AAPL', Side.SELL, 20, 150.0, Purpose.STOP))
    real = rig.broker.open_orders; calls = 0
    def slow():
        nonlocal calls
        calls += 1
        assert calls <= 8, 'blocked symbol must not livelock a tick'
        rig.advance(seconds=15); return real()
    monkeypatch.setattr(rig.broker, 'open_orders', slow)
    rig.advance(seconds=31); rig.px('AAPL', 107.0); rig.px('SPY', 107.0); rig.tick()
    assert calls <= 8
    spy = next(p for p in positions if p['symbol'] == 'SPY'); row = _working(rig, spy['position_id'], spy['qty_open'])
    rig.broker.fill(row['intent_id'], price=107.0); calls = 0; rig.advance(seconds=1); rig.tick()
    assert rig.store.position(spy['position_id'])['status'] == 'CLOSED'
    assert rig.store.control('frozen:AAPL') is not None
    _no_local_stop(rig)

def test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit_protection_on(tmp_path):
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    rig.broker.fail_next_submit = BrokerError('rejected: price', maybe_sent=False)
    _at_price(rig, 120.0); _settle(rig)
    assert rig.store.stage(pos['position_id'], Purpose.TP1)['status'] == 'PENDING'
    _at_price(rig, 107.0); _settle(rig)
    latest = _working(rig, pos['position_id'], 20)
    episode = json.loads(rig.store.control('protect_trigger:' + pos['position_id']))
    assert episode['remaining'] == 20 and episode['p1_sent']
    assert len(rig.broker.sent_protections) == 1
    _close_gtc(rig, pos, latest)

def test_a_stop_blocked_by_missing_evidence_pages_p0_protection_on(tmp_path, caplog):
    """Existing GTC keeps working; incomplete new authority must page P0."""
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    writes = (len(rig.broker.sent_protections), len(rig.broker.modified_protections))
    rig.broker.fail_open_orders = True; _at_price(rig, 107.0)
    rig.advance(seconds=61); rig.tick()
    assert any('P0' in s and 'AAPL' in b for s,b in rig.channel.messages)
    assert 'evidence incomplete' in caplog.text
    assert (len(rig.broker.sent_protections), len(rig.broker.modified_protections)) == writes
    assert _tps(rig) == [] and rig.broker.orders[row['intent_id']].remaining == 20
    _close_gtc(rig, pos, row)

def test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once_protection_on(tmp_path):
    rig = _rig(tmp_path, scan_times_et=('10:00', '10:02')); _, pos, row = _open(rig)
    # Genuine GTC partial execution: broker P18/GTC18, ledger and stream20
    # lag. This proves the immediate quantity-difference BUY gate, not
    # authority to resize protection after an unknown external SELL.
    rig.px('AAPL', 107.0)
    hidden = rig.broker.fill(row['intent_id'], qty=2, price=107.0, emit=False)
    rig.broker.invisible_execs.add(hidden.exec_id)
    rig.broker.stale_positions = {'AAPL': 20.0}
    rig.broker.bars['SPY'] = buy_bars()
    rig.at(et(10, 2, 5)); rig.px('SPY', 113.13); rig.px('AAPL', 120.0); rig.tick()
    assert not [r for r in _entries(rig) if r.symbol == 'SPY']
    assert rig.engine.status_snapshot(rig.clock.now())['entry_blocks']
    assert _tps(rig) == [] and rig.broker.orders[row['intent_id']].remaining <= 18
    _no_local_stop(rig)

def test_F1_failed_fresh_order_query_refuses_the_sell_protection_on(tmp_path):
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    rig.broker._register(OrderRequest('tws-manual', 'AAPL', Side.SELL, 20, 100.0, Purpose.STOP))
    rig.broker.fail_open_orders = True
    writes = (len(rig.broker.sent_protections), len(rig.broker.modified_protections))
    # Scheduled fresh reconciliation also sees the failed order query.
    _at_price(rig, 120.0, seconds=rig.cfg.reconcile_interval_seconds + 1)
    assert _tps(rig) == []
    assert (len(rig.broker.sent_protections), len(rig.broker.modified_protections)) == writes
    assert any('P0' in s and 'AAPL' in b for s,b in rig.channel.messages)
    assert rig.engine.status_snapshot(rig.clock.now())['entry_blocks']
    _no_local_stop(rig)

def test_F7_exit_is_re_decided_with_the_latest_quote_before_sending_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path); _, pos, row = _open(rig); real = rig.broker.open_orders
    def flip():
        rig.advance(seconds=15); rig.px('AAPL', 107.0); return real()
    monkeypatch.setattr(rig.broker, 'open_orders', flip)
    writes = len(rig.broker.modified_protections); _at_price(rig, 120.0)
    assert _tps(rig) == [] and len(rig.broker.modified_protections) == writes
    assert rig.broker.orders[row['intent_id']].remaining == 20
    assert rig.store.control('protect_trigger:' + pos['position_id']) is not None
    _close_gtc(rig, pos, row)

def test_F8_back_off_is_honoured_when_the_queued_item_runs_protection_on(tmp_path, monkeypatch):
    """Already queued protected TP respects the position/stage backoff."""
    rig = _rig(tmp_path); _, pos, row = _open(rig); rig.px('AAPL', 120.0)
    rig.store.begin_stage(rig.clock.now(), pos['position_id'], Purpose.TP1, 6); starts = []
    def failure():
        starts.append(rig.clock.now()); rig.advance(seconds=15); raise BrokerError('timeout')
    monkeypatch.setattr(rig.broker, 'open_orders', failure)
    rig.engine._submit_exit(pos['position_id'], Purpose.TP1); assert len(starts) == 1
    until = rig.engine._exit_backoff[(pos['position_id'], str(Purpose.TP1))]
    assert until == starts[0] + timedelta(seconds=45)
    for _ in range(29):
        rig.advance(seconds=1); rig.engine._submit_exit(pos['position_id'], Purpose.TP1)
    assert len(starts) == 1
    rig.advance(seconds=1); rig.engine._submit_exit(pos['position_id'], Purpose.TP1)
    assert starts[1] == until
    assert _tps(rig) == [] and len(rig.broker.modified_protections) == 0
    assert rig.broker.orders[row['intent_id']].remaining == 20

def test_missed_buy_freezes_then_flatten_and_release_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    rig.store.conn.execute('UPDATE positions SET qty_open=10,qty_initial=10')
    for _ in range(5):
        rig.advance(seconds=31); rig.tick()
    assert rig.store.control('frozen:AAPL') is not None
    assert any(k.startswith('anomaly:frozen:AAPL') for k in rig.store.anomalies())
    _manual_flatten(rig, row, 20); _release_verified(rig, pos, row, monkeypatch)
    rig.advance(seconds=1); rig.tick()
    assert not any(k.startswith('anomaly:frozen') for k in rig.store.anomalies())
    assert rig.engine.flags.reconcile_ok

def test_price_only_correction_blocks_entries_while_exits_continue_protection_on(tmp_path):
    rig = _rig(tmp_path); _, pos, row = _open(rig)
    original = rig.store.conn.execute("SELECT exec_id,intent_id FROM fills WHERE side='BUY'").fetchone()
    correction = FillEvent(original['exec_id'].rsplit('.',1)[0]+'.02', original['intent_id'],
                           'AAPL', Side.BUY, 20, 120.0, rig.clock.now(), commission=1.0)
    rig.broker.events.append(correction); rig.advance(seconds=1); rig.tick()
    assert any(k.startswith('anomaly:exec_correction') for k in rig.store.anomalies())
    assert rig.engine.status_snapshot(rig.clock.now())['entry_blocks']
    assert rig.store.position(pos['position_id'])['qty_open'] == 20
    _at_price(rig, 107.0); assert rig.broker.orders[row['intent_id']].remaining == 20
    _close_gtc(rig, pos, row); assert len(rig.broker.sent_protections) == 1

def test_prior_day_partial_entry_is_frozen_then_released_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path); rig.start(); rig.at(et(10, 0, 30)); rig.tick(); [entry] = _entries(rig)
    rig.broker.fill(entry.intent_id, qty=10); rig.advance(seconds=1); rig.tick()
    [pos] = rig.store.open_positions(); row = _working(rig, pos['position_id'], 10)
    # Missing prior-day BUY: broker 15, ledger 10. Preserve genuine GTC identity.
    rig.broker.orders[entry.intent_id].status = 'Cancelled'
    rig.broker.external_positions['AAPL'] = (5.0, 113.47); rig.at(et(9, 50, day=DAY2))
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock, report_dir=tmp_path / 'restart')
    rig.engine.startup()
    assert rig.store.intent(entry.intent_id)['hold_reason'] == 'prior_day_unverifiable'
    for _ in range(5):
        rig.advance(seconds=31); rig.tick()
    assert rig.store.control('frozen:AAPL') is not None
    _manual_flatten(rig, row, 15); rig.advance(seconds=61); _request_release(rig)
    assert rig.store.open_positions() and rig.store.control('frozen:AAPL') is not None
    assert rig.store.intent(entry.intent_id)['status'] != IntentStatus.CANCELLED
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='RELEASE_REFUSED'").fetchone()
    # Explicit operator terminal ruling, followed by the same strict release path.
    from v6lite import __main__ as operator_commands
    monkeypatch.setattr(operator_commands, '_now', rig.clock.now)
    operator_commands.cmd_resolve_intent(SimpleNamespace(
        intent_id=entry.intent_id, operator='synthetic-operator',
        reason='verified old DAY cancelled', as_status='cancelled'), rig.store)
    _release_verified(rig, pos, row)
    assert rig.store.risk_day('2026-10-07')['daily_stop_latched'] == 1
    rig.advance(seconds=1); rig.tick(); assert rig.engine.flags.reconcile_ok

def test_late_buy_after_release_of_a_recovery_position_is_evidence_only_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path); entry, pos, row = _open(rig); _close_gtc(rig, pos, row)
    first = FillEvent('0000fa4e.0000aaa1.01.01', entry, 'AAPL', Side.BUY, 5, 113.13, rig.clock.now())
    rig.broker.external_positions['AAPL'] = (5.0,113.13)
    rig.engine._ingest_fill(rig.clock.now(), first); _settle(rig); [rec] = rig.store.open_positions()
    assert rec['source'] == 'late_fill' and rec['qty_open'] == 5
    recrow = _working(rig, rec['position_id'], 5)
    _manual_flatten(rig, recrow, 5); _release_verified(rig, rec, recrow, monkeypatch)
    count = rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]; rig.advance(seconds=5)
    late = FillEvent('0000fa4e.0000aaa2.01.01', entry, 'AAPL', Side.BUY, 5, 113.13, rig.clock.now())
    for _ in range(2): rig.engine._ingest_fill(rig.clock.now(), late)
    assert rig.store.open_positions() == []
    assert rig.store.position(rec['position_id'])['status'] == 'RELEASED'
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == count
    assert not rig.store.conn.execute('SELECT 1 FROM fills WHERE exec_id=?',(late.exec_id,)).fetchone()
    # Modern release establishes flat_verified and a release record. The
    # original ENTRY predates that flat point, so the earlier guard must win.
    assert any(k.startswith('anomaly:late_fill_before_flat') for k in rig.store.anomalies())
    assert len(rig.broker.sent_protections) == 2

def test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path, risk_per_trade_pct=0.015)
    rig.start(); rig.at(et(10,0,30)); rig.tick(); [entry_req] = _entries(rig)
    assert entry_req.quantity == 30
    entry = entry_req.intent_id
    rig.broker.fill(entry,qty=20,price=113.47)
    rig.advance(seconds=61); rig.tick(); rig.broker.confirm_cancel(entry)
    for _ in range(2):
        rig.advance(seconds=6); rig.tick()
    assert rig.store.intent(entry)['status'] == IntentStatus.CANCELLED
    [pos] = rig.store.open_positions(); row = _working(rig,pos['position_id'],20)
    real = rig.broker.open_orders; armed = True; late = None
    def flip():
        nonlocal armed, late
        if armed:
            armed = False; rig.px('AAPL', 107.0)
            # Real original ENTRY has ten cancelled shares still capable of
            # a late fill. Hide only execution visibility, not broker truth.
            late = rig.broker.fill(entry,qty=10,price=113.13,emit=False)
            rig.broker.invisible_execs.add(late.exec_id)
            rig.broker.stale_positions = {'AAPL':20.0}
        return real()
    monkeypatch.setattr(rig.broker, 'open_orders', flip); _at_price(rig, 120.0)
    assert _tps(rig) == [] and rig.store.stage(pos['position_id'], Purpose.STOP) is None
    assert not rig.broker.modified_protections
    assert any('P0' in s and 'AAPL' in b for s,b in rig.channel.messages)
    rig.broker.invisible_execs.remove(late.exec_id)
    rig.engine._refresh_fills(rig.clock.now(),force=True)
    assert rig.store.position(pos['position_id'])['qty_open'] == 30
    rig.broker.stale_positions = None
    # The first actual failed proof owns a one-second retry deadline. Booking
    # the genuine late BUY at the same clock time does not create new proof.
    query_counts = {name: 0 for name in ('open_orders', 'positions_fresh', 'executions_today')}
    for name in query_counts:
        query = getattr(rig.broker, name)
        def counted(*args, _name=name, _query=query, **kwargs):
            query_counts[_name] += 1
            return _query(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, counted)
    writes = tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                           rig.broker.modified_protections, rig.broker.cancels))
    for seconds in (0, .999):
        if seconds:
            rig.advance(seconds=seconds)
        _settle(rig); _settle(rig)
        waiting = _working(rig, pos['position_id'], 20)
        assert waiting['intent_id'] == row['intent_id']
        assert rig.store.position(pos['position_id'])['qty_open'] == 30
        assert all(count == 0 for count in query_counts.values()), query_counts
        assert tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                              rig.broker.modified_protections, rig.broker.cancels)) == writes
    rig.advance(seconds=.001001)
    # The normal loop retries an open gap when its proof deadline is due;
    # immediate callback priority was consumed by the earlier failed proof.
    rig.tick()
    _settle(rig); _settle(rig)
    enlarged = _working(rig,pos['position_id'],30)
    assert enlarged['intent_id'] == row['intent_id']
    assert all(count > 0 for count in query_counts.values()), query_counts
    assert tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                          rig.broker.modified_protections, rig.broker.cancels)) == (
        writes[0], writes[1], writes[2] + 1, writes[3])
    _close_gtc(rig,pos,enlarged,106.9)
    assert rig.store.position(pos['position_id'])['qty_open'] == 0

def test_R4F1_release_needs_the_position_stream_to_agree_protection_on(tmp_path):
    rig = _rig(tmp_path); _,pos,row = _open(rig); _manual_flatten(rig,row,20)
    rig.broker.stale_positions = {'AAPL':20.0}; rig.advance(seconds=61); _request_release(rig)
    assert rig.store.position(pos['position_id'])['status'] == 'OPEN'
    [body] = [b for s,b in rig.channel.messages if 'P1' in s and '人工释放被拒' in s and 'AAPL' in b]
    assert len(body.splitlines()) == 4 and '你要做什么：' in body
    assert '20' in body and '0 股' in body and 'release AAPL' in body
    assert all(token in body for token in ('1）','2）','3）','TWS'))
    refused = rig.store.conn.execute("SELECT payload FROM audit WHERE kind='RELEASE_REFUSED' ORDER BY id DESC LIMIT 1").fetchone()
    assert refused and 'position stream still shows 20' in refused['payload']
    rig.broker.stale_positions = None; _release_verified(rig,pos,row)

def test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released_protection_on(tmp_path):
    rig = _rig(tmp_path); _,pos,row = _open(rig); _at_price(rig,107.0)
    late = rig.broker.fill(row['intent_id'],qty=20,price=107.0,emit=False)
    rig.broker.invisible_execs.add(late.exec_id)
    for _ in range(10):
        rig.advance(seconds=31); rig.tick()
        if rig.store.control('frozen:AAPL') is not None: break
    assert rig.store.control('frozen:AAPL') is not None
    rig.broker.invisible_execs.remove(late.exec_id); rig.engine._refresh_fills(rig.clock.now(),force=True)
    assert rig.store.position(pos['position_id'])['status'] == 'CLOSED'
    rig.advance(seconds=61); _request_release(rig)
    assert rig.store.control('frozen:AAPL') is None
    assert not any(k.startswith('anomaly:frozen:') for k in rig.store.anomalies())
    assert rig.store.release_times() == [] and rig.store.risk_day('2026-10-06')['daily_stop_latched'] == 1
    assert rig.store.position(pos['position_id'])['status'] == 'CLOSED'
    assert len([t for t in rig.store.closed_trades() if t['position_id'] == pos['position_id']]) == 1

def test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0_protection_on(tmp_path,monkeypatch):
    rig = _rig(tmp_path); _,pos,row = _open(rig); real=rig.broker.open_orders; armed=True
    def flip():
        nonlocal armed
        if armed:
            armed=False; rig.px('AAPL',107.0)
            rig.broker._register(OrderRequest('combo-x','*',Side.SELL,5,1.0,Purpose.TP1))
        return real()
    monkeypatch.setattr(rig.broker,'open_orders',flip); _at_price(rig,120.0)
    assert not [r for r in _tps(rig) if r.intent_id.startswith('v6l-')]
    assert any('P0' in s and 'AAPL' in b for s,b in rig.channel.messages)
    assert rig.store.control('protect_trigger:'+pos['position_id']) is not None
    assert len(rig.broker.sent_protections)==1; _no_local_stop(rig)

def test_R5F2_old_order_without_a_position_row_never_books_after_flat_protection_on(tmp_path,monkeypatch):
    rig,entry,pos,row = _released_rig(tmp_path,monkeypatch)
    old=OrderRequest('v6l-oldentry0001','AAPL',Side.BUY,10,112.0,Purpose.ENTRY,'pos-never-booked')
    rig.store.create_intent(et(9,40),old,atr=2.0,count_entry_day=None)
    rig.store.update_intent(et(9,41),old.intent_id,status=IntentStatus.CANCELLED)
    late=FillEvent('0000fa4e.0000c0de.01.01',old.intent_id,'AAPL',Side.BUY,5,112.0,et(9,40,30))
    rig.engine._ingest_fill(rig.clock.now(),late)
    assert rig.store.open_positions()==[] and rig.store.position('pos-never-booked') is None
    assert not rig.store.conn.execute('SELECT 1 FROM fills WHERE exec_id=?',(late.exec_id,)).fetchone()
    assert any(k.startswith('anomaly:late_fill_before_flat:') for k in rig.store.anomalies())

def test_R5F3_batch4_state_release_record_alone_is_a_flat_point_protection_on(tmp_path,monkeypatch):
    rig,entry,pos,row = _released_rig(tmp_path,monkeypatch); rig.store.delete_control('flat_verified:AAPL')
    late=FillEvent('0000fa4e.0000c0df.01.01',entry,'AAPL',Side.BUY,3,113.0,et(10,0,31))
    rig.engine._ingest_fill(rig.clock.now(),late)
    assert rig.store.open_positions()==[]
    assert not rig.store.conn.execute('SELECT 1 FROM fills WHERE exec_id=?',(late.exec_id,)).fetchone()
    assert rig.store.position(pos['position_id'])['status']=='RELEASED'

def test_SR6_A2_stale_position_stream_never_silences_a_stop_protection_on(tmp_path):
    """Stale zero stream cannot suppress execution of the existing 20-share GTC."""
    rig=_rig(tmp_path); _,pos,row=_open(rig); rig.broker.stale_positions={'AAPL':0.0}
    # Existing STP GTC20 is independent of the stale zero stream. A stop
    # crossing starts P1; remaining shares past the RTH timeout require P0.
    _at_price(rig,row['trigger_price'] - 0.01)
    assert any('P1' in s and '已到止损价' in s and 'AAPL' in b for s,b in rig.channel.messages)
    assert not any('P0' in s and '止损已触发但未成交' in s for s,_ in rig.channel.messages)
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds - 1); rig.tick()
    assert not any('P0' in s and '止损已触发但未成交' in s for s,_ in rig.channel.messages)
    rig.advance(seconds=1); rig.tick()
    assert any('P0' in s and 'AAPL' in b for s,b in rig.channel.messages)
    assert rig.broker.orders[row['intent_id']].remaining==20
    assert row['intent_id'] not in rig.broker.cancels and len(rig.broker.sent_protections)==1
    _close_gtc(rig,pos,row); rig.broker.stale_positions=None
    rig.advance(seconds=31); rig.tick(); assert rig.store.open_positions()==[]

def test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state_protection_on(tmp_path,monkeypatch):
    rig,entry,pos,row = _released_rig(tmp_path,monkeypatch)
    rig.store.conn.execute(
        "INSERT INTO fills(exec_id,intent_id,position_id,symbol,side,quantity,price,time,trading_day,exec_base) "
        "VALUES('0000fa4e.0000beef.01.01',?,?,'AAPL','BUY',5,112.0,?,?,'0000fa4e.0000beef')",
        (entry,pos['position_id'],rig.clock.now().isoformat(),et(10).date().isoformat()))
    rig.store.raise_anomaly(rig.clock.now(),'late_fill_after_release:0000fa4e.0000beef.01.01','batch-5 row')
    before=tuple(len(x) for x in (rig.broker.sent,rig.broker.sent_protections,rig.broker.cancels))
    with pytest.raises(StartupError,match='earlier version booked'): rig.engine.startup()
    assert tuple(len(x) for x in (rig.broker.sent,rig.broker.sent_protections,rig.broker.cancels))==before

# Independent retired-GTC identity/capacity regressions, beyond the 27-map.
def _retired_gtc_pair(tmp_path):
    rig = _rig(tmp_path)
    _, pos, old = _open(rig)
    old = dict(old)
    rig.engine._protection_cancel(rig.clock.now(), old, 'test_retired_gtc_lineage')
    rig.broker.confirm_cancel(old['intent_id'])
    _settle(rig)
    for _ in range(4):
        rig.advance(seconds=1); rig.tick(); _settle(rig)
        if rig.store.protection(pos['position_id'])['intent_id'] != old['intent_id']:
            break
    new = dict(_working(rig, pos['position_id'], 20))
    assert new['intent_id'] != old['intent_id']
    assert new['broker_order_id'] != old['broker_order_id']
    assert new['perm_id'] != old['perm_id']
    assert new['con_id'] == old['con_id']
    assert rig.store.intent(old['intent_id'])['status'] == IntentStatus.CANCELLED
    assert len(rig.broker.sent_protections) == 2
    return rig, pos, old, new


def _retired_native_fill(rig, old, qty):
    """A genuine FakeBroker execution, including native identity on old code.

    B19 FillEvent predates con_id. Preserve its actual event type while adding
    the native contract field for TEMP-baseline diagnosis; new code has the
    formal field. This avoids constructor failures masquerading as product red.
    """
    from dataclasses import replace
    fill = rig.broker.fill(old['intent_id'], qty=qty, price=107.0, emit=False)
    fill = replace(fill, broker_order_id=old['broker_order_id'], perm_id=old['perm_id'])
    object.__setattr__(fill, 'con_id', old['con_id'])
    rig.broker.orders[old['intent_id']].fills[-1] = fill
    return fill


def _retired_broker_writes(rig):
    return tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                        rig.broker.modified_protections, rig.broker.cancels))


def _finish_retired_partial_cleanup(rig, pos, old, new):
    """Broker acknowledgement, then fresh proof: P15 must regain exactly S15."""
    pid = pos['position_id']
    if new['intent_id'] in rig.broker.protection_pending_modifications:
        rig.broker.confirm_protection_modify(new['intent_id'])
    elif new['intent_id'] in rig.broker.cancels:
        rig.broker.confirm_cancel(new['intent_id'])
    else:
        pytest.fail('late old-GTC execution did not request replacement GTC capacity cleanup')
    _settle(rig)
    for _ in range(4):
        row = rig.store.protection(pid)
        if row['state'] == 'WORKING' and row['confirmed_remaining'] == 15:
            break
        rig.advance(seconds=1); _settle(rig)
    row = _working(rig, pid, 15)
    assert rig.store.position(pid)['status'] == 'OPEN'
    assert rig.store.position(pid)['qty_open'] == 15
    sells = [o for o in rig.broker.open_orders() if o.symbol == 'AAPL' and o.side in ('SELL', '')]
    assert sum(o.remaining for o in sells) == 15
    assert sum(o.remaining for o in sells) <= next(
        p.quantity for p in rig.broker.positions_fresh() if p.symbol == 'AAPL')
    assert rig.broker.orders[old['intent_id']].remaining == 15
    _no_local_stop(rig)
    return row


def test_retired_gtc_full_late_fill_cancels_replacement_and_is_flat(tmp_path):
    rig, pos, old, new = _retired_gtc_pair(tmp_path)
    fill = _retired_native_fill(rig, old, 20)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    assert rig.store.position(pos['position_id'])['status'] == 'CLOSED'
    assert rig.store.position(pos['position_id'])['qty_open'] == 0
    assert rig.broker.positions_fresh() == []
    # An old execution never pretends that the replacement itself filled.
    pending = rig.store.protection(pos['position_id'])
    assert pending['intent_id'] == new['intent_id']
    assert pending['confirmed_remaining'] == pending['pending_max_remaining'] == 20
    assert pending['state'] != 'FILLED'
    assert new['intent_id'] in rig.broker.cancels
    assert not rig.broker.modified_protections
    assert len(rig.broker.sent_protections) == 2
    rig.broker.confirm_cancel(new['intent_id']); _settle(rig)
    assert [o for o in rig.broker.open_orders() if o.symbol == 'AAPL'] == []
    assert rig.store.open_positions() == []
    assert rig.store.intent_filled_qty(old['intent_id']) == 20
    assert rig.store.intent_filled_qty(new['intent_id']) == 0
    _no_local_stop(rig)


def test_retired_gtc_partial_late_fill_keeps_possible_capacity_until_ack_then_only_reduces(tmp_path):
    rig, pos, old, new = _retired_gtc_pair(tmp_path)
    rig.broker.protection_auto_status = False
    fill = _retired_native_fill(rig, old, 5)
    writes = _retired_broker_writes(rig)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    assert rig.store.position(pos['position_id'])['qty_open'] == 15
    pending = rig.store.protection(pos['position_id'])
    assert pending['intent_id'] == new['intent_id']
    assert pending['confirmed_remaining'] == pending['pending_max_remaining'] == 20
    assert rig.store.intent_filled_qty(new['intent_id']) == 0
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert len(rig.broker.sent_protections) == writes[1]
    reductions = rig.broker.modified_protections[writes[2]:]
    assert reductions or new['intent_id'] in rig.broker.cancels[writes[3]:]
    assert all(req.intent_id == new['intent_id'] and req.quantity <= 15 for req in reductions)
    _finish_retired_partial_cleanup(rig, pos, old, new)
    assert all(req.quantity <= 15 for req in rig.broker.sent_protections[writes[1]:])
    assert all(req.quantity <= 15 for req in rig.broker.modified_protections[writes[2]:])


def test_retired_gtc_duplicate_execution_replay_never_rebooks_or_rewrites_broker(tmp_path):
    rig, pos, old, new = _retired_gtc_pair(tmp_path)
    rig.broker.protection_auto_status = False
    fill = _retired_native_fill(rig, old, 5)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    row = _finish_retired_partial_cleanup(rig, pos, old, new)
    writes = _retired_broker_writes(rig)
    before = rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
    pnl = rig.store.position(pos['position_id'])['realized_pnl']
    for _ in range(3):
        rig.engine._ingest_fill(rig.clock.now(), fill)
    assert _retired_broker_writes(rig) == writes
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == before
    assert rig.store.position(pos['position_id'])['qty_open'] == 15
    assert rig.store.position(pos['position_id'])['realized_pnl'] == pytest.approx(pnl)
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert rig.store.intent_filled_qty(row['intent_id']) == 0
    _working(rig, pos['position_id'], 15)


def test_retired_gtc_lineage_survives_store_and_engine_restart(tmp_path):
    from functools import partial
    from v6lite.exits import compute_levels
    from v6lite.store import Store
    rig, pos, old, new = _retired_gtc_pair(tmp_path)
    state_path = rig.store.path
    rig.store.close()
    rig.store = Store(state_path, partial(compute_levels, rig.cfg))
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'lineage-restart-reports')
    rig.engine.startup()
    _working(rig, pos['position_id'], 20)
    assert len(rig.broker.sent_protections) == 2
    rig.broker.protection_auto_status = False
    fill = _retired_native_fill(rig, old, 5)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    assert rig.store.position(pos['position_id'])['qty_open'] == 15
    pending = rig.store.protection(pos['position_id'])
    assert pending['confirmed_remaining'] == pending['pending_max_remaining'] == 20
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert rig.store.intent_filled_qty(new['intent_id']) == 0
    _finish_retired_partial_cleanup(rig, pos, old, new)


@pytest.mark.parametrize('identity_field', ['broker_order_id', 'perm_id', 'con_id'])
def test_retired_gtc_wrong_native_identity_is_quarantined_without_current_gtc_writes(tmp_path, identity_field):
    from dataclasses import replace
    rig, pos, old, new = _retired_gtc_pair(tmp_path)
    # Wrong native identity is an untrusted callback, not an actual FakeBroker
    # execution: broker truth remains P20. Only one identity component differs.
    fill = FillEvent('0000fa4e.0000bad1.01.01', old['intent_id'], 'AAPL', Side.SELL,
                     5, 107.0, rig.clock.now(), broker_order_id=old['broker_order_id'],
                     perm_id=old['perm_id'], commission=1.0)
    object.__setattr__(fill, 'con_id', old['con_id'])
    if identity_field == 'con_id':
        object.__setattr__(fill, 'con_id', old['con_id'] + 1)
    else:
        fill = replace(fill, **{identity_field: old[identity_field] + 1})
        object.__setattr__(fill, 'con_id', old['con_id'])
    writes = _retired_broker_writes(rig)
    before = rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
    rig.engine._ingest_fill(rig.clock.now(), fill)
    assert _retired_broker_writes(rig) == writes
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == before
    assert rig.store.position(pos['position_id'])['qty_open'] == 20
    assert rig.store.intent_filled_qty(old['intent_id']) == 0
    assert rig.store.intent_filled_qty(new['intent_id']) == 0
    row = rig.store.protection(pos['position_id'])
    assert row['intent_id'] == new['intent_id']
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 20
    assert rig.broker.orders[new['intent_id']].remaining == 20
    assert rig.store.conn.execute('SELECT 1 FROM quarantined_fills WHERE exec_id=?',
                                  (fill.exec_id,)).fetchone() is not None
    assert rig.store.anomalies()
    _no_local_stop(rig)


def test_retired_gtc_pending_reduction_waits_for_original_generation_without_rewrites(tmp_path, monkeypatch):
    """Two fresh ticks cannot resend an already safe reduction awaiting its status."""
    rig, pos, old, new = _retired_gtc_pair(tmp_path)
    rig.broker.protection_auto_status = False
    fill = _retired_native_fill(rig, old, 5)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    pid = pos['position_id']
    pending = dict(rig.store.protection(pid))
    assert rig.store.position(pid)['qty_open'] == 15
    assert pending['intent_id'] == new['intent_id']
    assert pending['state'] == 'MODIFY_PENDING' and pending['pending_qty'] == 15
    assert pending['confirmed_remaining'] == pending['pending_max_remaining'] == 20
    assert rig.broker.orders[new['intent_id']].remaining == 20
    generation = pending['modify_generation']
    writes = _retired_broker_writes(rig)
    assert rig.broker.protection_pending_modifications[new['intent_id']].quantity == 15

    queries = {'open_orders': 0, 'positions_fresh': 0, 'executions_today': 0}
    for method in queries:
        original = getattr(rig.broker, method)
        def counted(original=original, method=method):
            queries[method] += 1
            return original()
        monkeypatch.setattr(rig.broker, method, counted)

    waiting = []
    for _ in range(2):
        rig.advance(seconds=1)
        rig.px('AAPL', 113.47)
        rig.tick()
        waiting.append(dict(rig.store.protection(pid)))
    assert all(count >= 2 for count in queries.values()), queries
    assert all(row['modify_generation'] == generation for row in waiting), [
        row['modify_generation'] for row in waiting]
    assert _retired_broker_writes(rig) == writes
    assert all(row['pending_qty'] == 15 and row['state'] == 'MODIFY_PENDING' for row in waiting)
    assert all(row['confirmed_remaining'] == row['pending_max_remaining'] == 20 for row in waiting)
    assert all(row['status_seen_generation'] != generation for row in waiting)
    assert rig.store.position(pid)['qty_open'] == 15
    assert rig.broker.orders[new['intent_id']].remaining == 20

    # This is the original request's genuine broker acknowledgement, followed
    # by the exact same generation's fresh order/position/execution evidence.
    rig.broker.confirm_protection_modify(new['intent_id'])
    _settle(rig)
    confirmed = _working(rig, pid, 15)
    assert confirmed['modify_generation'] == confirmed['status_seen_generation'] == generation
    assert confirmed['pending_qty'] is None
    assert _retired_broker_writes(rig) == writes
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert rig.store.intent_filled_qty(new['intent_id']) == 0
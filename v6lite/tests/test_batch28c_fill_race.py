"""B28c independent TP-fill/query races; native execution decoding, no sockets.

The old position is captured before a real TP execution reaches the durable
ledger. No SQLite data, authorization gate, proof cache or production function
is replaced. Larger-entry coverage uses a real six-share prior-day entry so a
two-share TP1 can genuinely fill only one share without flattening the holding.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.models import FillEvent, OrderRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, buy_bars, et
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _overnight_p1, _protection_config, _restart, _restore_infos,
    _safety, _tp_requests, _watch_boundaries, _writes,
)
from v6lite.tests.test_batch27b_raw_execution_time import NativeExecutionHarness
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run

ENTRY_DAY = (2026, 10, 20)
TP_DAY = (2026, 10, 21)
TP1_AT = et(11, 15, 15, day=TP_DAY)  # 08:15:15 PT
SOURCES = {'open_orders', 'positions_fresh', 'executions_today', 'account_executions_today_scoped'}


def _tick_until(rig, predicate, *, seconds=120):
    deadline = rig.clock.now() + timedelta(seconds=seconds)
    while rig.clock.now() < deadline and not predicate():
        rig.advance(seconds=1)
        rig.tick()
        _safety(rig)
    assert predicate(), ('deadline', rig.clock.now(), [dict(r) for r in rig.store.protections()])


@pytest.fixture
def larger_overnight_run(tmp_path, monkeypatch, request):
    """B28b's proven native setup, with six real entry fills and TP1 target=2."""
    rig = Rig(tmp_path / 'b28c-six-share-overnight', start=et(9, 50, day=ENTRY_DAY),
              protection=_protection_config(True), risk_per_trade_pct=.003)
    original_levels = rig.store._levels_fn
    rig.store._levels_fn = lambda price, atr: replace(
        original_levels(price, atr), stop=338.62, tp1=343.93, tp2=344.60, tp3=345.60)
    rig.broker._next_id = 704
    harness = NativeExecutionHarness(rig.cfg, rig.clock, monkeypatch, request)
    harness.visibility = 'default-midnight'
    harness.rows = lambda: FakeBroker.executions_today(rig.broker)
    original_sleep = harness.ib.sleep
    def streaming_sleep(seconds):
        original_sleep(seconds)
        rig.refresh_quotes()
    monkeypatch.setattr(harness.ib, 'sleep', streaming_sleep)
    def query(method):
        try:
            return method()
        finally:
            rig.broker.query_generation = harness.adapter.query_generation
    rig.broker.query_generation = harness.adapter.query_generation
    monkeypatch.setattr(rig.broker, 'executions_today', lambda: query(harness.client_query))
    monkeypatch.setattr(rig.broker, 'account_executions_today', lambda: query(harness.account_query))
    monkeypatch.setattr(rig.broker, 'account_executions_today_scoped', lambda: query(harness.scoped_query), raising=False)
    monkeypatch.setattr(rig.broker, 'positions', lambda: FakeBroker.positions_fresh(rig.broker))
    original_drain = rig.broker.drain_events
    monkeypatch.setattr(rig.broker, 'drain_events', lambda: original_drain() + harness.adapter.drain_events())
    def callback_fill(ref, *, quantity, price, exec_id):
        raw = rig.broker.fill(ref, qty=quantity, price=price, exec_id=exec_id, emit=False)
        events = harness.callback(raw)
        [event] = [item for item in events if isinstance(item, FillEvent)]
        assert event.time_source == 'callback' and event.time_uncertain
        event = replace(event, commission=raw.commission)
        rig.broker.orders[ref].fills[-1] = event
        rig.broker.events.extend(event if isinstance(item, FillEvent) else item for item in events)
        rig.engine._drain(rig.clock.now())
        return event
    rig._b28b_callback_fill = callback_fill
    try:
        rig.start()
        rig.at(et(10, 0, 30, day=ENTRY_DAY))
        rig.px('AAPL', 342.60)
        rig.broker.bars['AAPL'] = buy_bars(base=329.47)
        rig.tick()
        [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
        assert entry.quantity == 6, 'true partial-TP test must start from a real larger entry'
        entry_fill = callback_fill(entry.intent_id, quantity=6, price=342.60,
                                   exec_id='b28c-six-share-entry.01.01')
        rig.advance(seconds=1)
        rig.tick()
        [position] = rig.store.open_positions()
        pid = position['position_id']
        row = dict(rig.store.protection(pid))
        assert position['qty_open'] == row['confirmed_remaining'] == 6
        assert row['state'] == 'WORKING' and row['broker_order_id'] == 706
        rig.engine.shutdown(grace_seconds=0)
        rig.alerter.close()
        path, levels = rig.store.path, rig.store._levels_fn
        rig.store.close()
        rig.store = Store(path, levels)
        rig.at(et(8, 59, 59, day=TP_DAY))
        rig._b26_alerts = []
        rig._b26_timeline = []
        rig._b26_market_calls = []
        rig._b26_entry, rig._b26_entry_fill, rig._b26_original = entry, entry_fill, row
        _watch_boundaries(rig, monkeypatch)
        _restart(rig, tmp_path, monkeypatch)
        assert len(_restore_infos(rig)) == 1
        assert rig.store.position(pid)['qty_open'] == 6
        assert rig.broker.orders[row['intent_id']].remaining == 6
        assert not _overnight_p1(rig)
        rig.broker.protection_auto_status = False
        yield rig, pid, harness
    finally:
        rig.alerter.close()
        rig.store.close()


def _activate_tp1(rig, pid):
    rig.at(TP1_AT)
    rig.px('AAPL', 343.94)
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    row = rig.store.protection(pid)
    original_quantity = int(rig.store.position(pid)['qty_initial'])
    expected_tp = original_quantity // 3
    assert row['pending_qty'] == original_quantity - expected_tp
    assert not _tp_requests(rig)
    rig.broker.confirm_protection_modify(row['intent_id'])
    _safety(rig)
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 1)
    [tp] = _tp_requests(rig)
    assert tp.purpose is Purpose.TP1 and tp.quantity == expected_tp
    row = rig.store.protection(pid)
    assert row['state'] == 'TP_ACTIVE' and row['tp_intent_id'] == tp.intent_id
    assert rig.broker.orders[row['intent_id']].remaining == original_quantity - tp.quantity
    assert rig.store.intent_filled_qty(tp.intent_id) == 0
    rig._b26_alerts.clear()
    rig.channel.messages.clear()
    return tp


def _install_native_fill_race(rig, pid, harness, tp, monkeypatch, *, fault=None):
    """Capture an actual old P, book native TP SELL, then return that old P."""
    state = {'round': 0, 'rounds': {}, 'chronology': [], 'injected': False,
             'race_round': None, 'binding_before': None, 'binding_after': None,
             'old_quantity': None, 'fill': None}
    for name in SOURCES:
        original = getattr(rig.broker, name)
        def query(*args, _name=name, _original=original, **kwargs):
            is_proof = rig.engine._protection_query_position == pid
            if is_proof and _name == 'open_orders':
                state['round'] += 1
                state['rounds'][state['round']] = {}
                if fault == 'other-SELL' and not state['injected']:
                    # An external actor's genuine extra broker SELL. This
                    # deliberately unsafe input must never get a TP exemption.
                    rig.broker._register(OrderRequest('foreign-b28c-sell', 'AAPL', Side.SELL,
                        1, 344.04, Purpose.TP2, None))
            number = state['round'] if is_proof else None
            inject = bool(is_proof and _name == 'positions_fresh' and not state['injected'])
            if inject and fault == 'real-P-mismatch':
                rig.broker.external_positions['AAPL'] = (1.0, 342.60)
            result = _original(*args, **kwargs)
            if inject:
                state['injected'] = True
                state['race_round'] = number
                [old] = [pos for pos in result if pos.symbol == 'AAPL']
                state['old_quantity'] = old.quantity
                position_before = rig.store.position(pid)
                row_before = rig.store.protection(pid)
                state['binding_before'] = rig.engine._protection_verification_binding(row_before)
                assert row_before['tp_intent_id'] == tp.intent_id
                assert rig.store.intent(tp.intent_id)['position_id'] == pid
                assert rig.store.intent(tp.intent_id)['purpose'] == 'TP1'
                state['chronology'].append('old-P-snapshot')
                rig.clock.advance(microseconds=250000)
                fill = rig._b28b_callback_fill(tp.intent_id, quantity=1, price=344.04,
                                               exec_id='b28c-race-tp1.01.01')
                state['fill'] = fill
                assert fill.side is Side.SELL and fill.intent_id == tp.intent_id
                intent = rig.store.intent(tp.intent_id)
                assert fill.broker_order_id == intent['broker_order_id']
                assert fill.perm_id == intent['perm_id']
                assert rig.store.intent_filled_qty(tp.intent_id) == 1
                assert rig.store.position(pid)['qty_open'] == position_before['qty_open'] - 1
                state['binding_after'] = rig.engine._protection_verification_binding(rig.store.protection(pid))
                state['chronology'].append('native-TP-fill-booked')
                if fault != 'other-SELL':
                    _safety(rig)
                rig.clock.advance(microseconds=250000)
                state['chronology'].append('returned-old-P')
            if is_proof:
                state['rounds'][number][_name] = result
            return result
        monkeypatch.setattr(rig.broker, name, query)
    return state


def _fresh_rounds(rig, pid, harness, state):
    required_position = rig.store.position(pid)['qty_open']
    ended = {request_id for request_id, _ in harness.ends}
    found = []
    for number, sources in state['rounds'].items():
        if number == state['race_round'] or not SOURCES <= set(sources):
            continue
        positions = [pos for pos in sources['positions_fresh'] if pos.symbol == 'AAPL']
        scope = getattr(sources['account_executions_today_scoped'], 'scope', None)
        if (len(positions) == 1 and positions[0].quantity == required_position and
                scope and scope.get('complete') is True and scope.get('request_id') in ended and
                scope.get('generation') == harness.adapter.query_generation and
                any(fill.exec_id == state['fill'].exec_id for fill in sources['executions_today']) and
                any(fill.exec_id == state['fill'].exec_id
                    for fill in sources['account_executions_today_scoped'])):
            found.append(number)
    return found


def _forbid_write_from_old_round(rig, pid, harness, state, monkeypatch):
    for name in ('submit_limit', 'submit_stop', 'modify_protection', 'cancel'):
        original = getattr(rig.broker, name)
        def write(*args, _name=name, _original=original, **kwargs):
            if state['injected']:
                assert _fresh_rounds(rig, pid, harness, state), (
                    'broker write used the superseded mixed round', _name, state['rounds'])
            return _original(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, write)


def _assert_no_false_race_alert(rig, pid):
    assert not [(level, text) for level, text in rig._b26_alerts if level == 'P0']
    assert not [(level, text) for level, text in rig._b26_alerts
                if level == 'P1' and ('券商查询已限速' in text or 'broker queries limited' in text)]
    assert not _overnight_p1(rig)
    assert rig.store.control('frozen:AAPL') is None
    assert rig.store.control('protect_evidence_failure:' + pid) is None


def _exercise_positive_race(rig, pid, harness, monkeypatch, *, true_partial):
    tp = _activate_tp1(rig, pid)
    assert (tp.quantity > 1) is true_partial
    original_stop = rig.store.protection(pid)['intent_id']
    before = _writes(rig)
    state = _install_native_fill_race(rig, pid, harness, tp, monkeypatch)
    _forbid_write_from_old_round(rig, pid, harness, state, monkeypatch)
    rig.tick()
    assert state['chronology'] == ['old-P-snapshot', 'native-TP-fill-booked', 'returned-old-P']
    assert state['old_quantity'] == (6 if true_partial else 3)
    assert rig.store.position(pid)['status'] == 'OPEN'
    assert rig.store.position(pid)['qty_open'] == (5 if true_partial else 2)
    assert (state['binding_before'] == state['binding_after']) is true_partial
    old_orders = state['rounds'][state['race_round']]['open_orders']
    [stale_tp] = [order for order in old_orders if order.intent_id == tp.intent_id]
    assert stale_tp.ours and stale_tp.remaining == tp.quantity
    # A raw positive old TP remaining cannot be mistaken for a foreign SELL.
    assert rig.store.intent_filled_qty(tp.intent_id) == 1
    _assert_no_false_race_alert(rig, pid)
    _safety(rig)
    _tick_until(rig, lambda: bool(_fresh_rounds(rig, pid, harness, state)), seconds=60)
    _assert_no_false_race_alert(rig, pid)
    assert rig.store.protection(pid)['intent_id'] == original_stop
    assert rig.store.protection(pid)['broker_order_id'] == 706
    assert len(rig.broker.sent_protections) == before[1] == 1
    assert len(rig.broker.sent) == before[0], 'the race cannot duplicate TP1'
    assert rig.store.intent_filled_qty(tp.intent_id) == 1
    if true_partial:
        assert rig.store.intent(tp.intent_id)['status'] == 'PARTIAL'
        assert rig.broker.orders[tp.intent_id].remaining == tp.quantity - 1
    else:
        assert rig.store.intent(tp.intent_id)['status'] == 'FILLED'
    _safety(rig)


def test_B28c_native_TP1_full_intent_mid_query_reduces_overnight_position_3_to_2_without_false_P0(
        overnight_run, monkeypatch):
    rig, pid, harness = overnight_run()
    _exercise_positive_race(rig, pid, harness, monkeypatch, true_partial=False)


def test_B28c_native_true_partial_TP1_mid_query_invalidates_old_round_even_when_GTC_binding_unchanged(
        larger_overnight_run, monkeypatch):
    rig, pid, harness = larger_overnight_run
    _exercise_positive_race(rig, pid, harness, monkeypatch, true_partial=True)


@pytest.mark.parametrize('fault', ['real-P-mismatch', 'other-SELL'])
def test_B28c_own_TP_fill_cannot_excuse_real_quantity_difference_or_another_SELL(
        overnight_run, monkeypatch, fault):
    rig, pid, harness = overnight_run()
    tp = _activate_tp1(rig, pid)
    original = rig.store.protection(pid)
    before = _writes(rig)
    state = _install_native_fill_race(rig, pid, harness, tp, monkeypatch, fault=fault)
    # One actual evidence/progress operation is the failure boundary. The
    # foreign-SELL variant deliberately introduces external excess capacity;
    # it is not allowed to fail the fixture's capacity oracle before the
    # production diagnostic gets an opportunity to reject that broker state.
    rig.engine._protection_progress_one(rig.clock.now(), original, rig.store.position(pid))
    assert state['injected'] and state['fill'].intent_id == tp.intent_id
    assert rig.store.position(pid)['qty_open'] == 2
    assert rig.store.intent_filled_qty(tp.intent_id) == 1
    p0 = [text for level, text in rig._b26_alerts if level == 'P0']
    assert p0 and any('evidence' in text.lower() or '证据' in text or '保护' in text for text in p0)
    newly_recorded = rig.broker.sent[before[0]:]
    assert [request.intent_id for request in newly_recorded] == (
        ['foreign-b28c-sell'] if fault == 'other-SELL' else [])
    assert len(rig.broker.sent_protections) == before[1] == 1
    if fault == 'real-P-mismatch':
        assert state['old_quantity'] == 4
        assert next(pos.quantity for pos in FakeBroker.positions_fresh(rig.broker)
                    if pos.symbol == 'AAPL') == 3
        _safety(rig)
    else:
        old_orders = state['rounds'][state['race_round']]['open_orders']
        assert any(not order.ours and order.intent_id == 'foreign-b28c-sell'
                   and order.remaining == 1 for order in old_orders)
        assert rig.broker.orders['foreign-b28c-sell'].remaining == 1

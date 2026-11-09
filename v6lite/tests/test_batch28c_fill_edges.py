"""B28c independent strict-delta edges; no production/proof mutation or sockets.

Unknown executions and broken STP lists are deliberate contradictory broker
inputs. Positive cases use real native TP callbacks and complete native scoped
execution requests, including a legitimate old-fill timestamp correction.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.models import OrderRequest, Purpose, Side
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _safety, _writes
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run
from v6lite.tests.test_batch28c_fill_race import (
    SOURCES, _activate_tp1, _assert_no_false_race_alert, _forbid_write_from_old_round,
    _fresh_rounds, _install_native_fill_race, _tick_until, larger_overnight_run,
)


def _assert_race_P0(rig):
    pages = [text for level, text in rig._b26_alerts if level == 'P0']
    assert pages and any('evidence' in text.lower() or '证据' in text or '保护' in text for text in pages)


def test_B28c_own_TP_and_unknown_extra_native_SELL_same_round_cannot_get_TP_exemption(
        overnight_run, monkeypatch):
    rig, pid, harness = overnight_run()
    tp = _activate_tp1(rig, pid)
    row, pos = rig.store.protection(pid), rig.store.position(pid)
    before = _writes(rig)
    state = _install_native_fill_race(rig, pid, harness, tp, monkeypatch)
    positions = rig.broker.positions_fresh
    unknown = []
    def snapshot_then_unknown_sell():
        result = positions()  # first books the system's actual TP1 and returns old P=3
        if rig.engine._protection_query_position == pid and state['injected'] and not unknown:
            request = OrderRequest('foreign-b28c-filled-sell', 'AAPL', Side.SELL,
                                   1, 344.04, Purpose.TP2, None)
            rig.broker._register(request)
            event = rig._b28b_callback_fill(request.intent_id, quantity=1, price=344.04,
                                            exec_id='b28c-unknown-sell-same-round.01.01')
            assert event.intent_id == '' and event.side is Side.SELL
            unknown.append(event)
        return result
    monkeypatch.setattr(rig.broker, 'positions_fresh', snapshot_then_unknown_sell)
    # The external fill deliberately makes the original S exceed true P. Let
    # the actual one-round production boundary reject it before running the
    # capacity oracle on that externally injected unsafe state.
    rig.engine._protection_progress_one(rig.clock.now(), row, pos)
    assert state['injected'] and len(unknown) == 1
    assert state['old_quantity'] == 3
    assert rig.store.position(pid)['qty_open'] == 2
    assert next(p.quantity for p in FakeBroker.positions_fresh(rig.broker) if p.symbol == 'AAPL') == 1
    [unattributed] = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?',
                                          (unknown[0].exec_id,)).fetchall()
    assert unattributed['position_id'] is None and not unattributed['intent_id']
    assert rig.store.anomalies(), 'the unknown native execution must retain its real anomaly'
    _assert_race_P0(rig)
    assert [request.intent_id for request in rig.broker.sent[before[0]:]] == ['foreign-b28c-filled-sell']
    assert len(rig.broker.sent_protections) == before[1]


@pytest.mark.parametrize('defect', ['missing-STP', 'duplicate-STP'])
def test_B28c_own_TP_delta_cannot_excuse_missing_or_duplicate_original_STP(
        overnight_run, monkeypatch, defect):
    rig, pid, harness = overnight_run()
    tp = _activate_tp1(rig, pid)
    row, pos = rig.store.protection(pid), rig.store.position(pid)
    before = _writes(rig)
    orders = rig.broker.open_orders
    observed = []
    def malformed_orders():
        result = orders()
        if rig.engine._protection_query_position == pid:
            [original] = [order for order in result if order.intent_id == row['intent_id']]
            if defect == 'missing-STP':
                result = [order for order in result if order.intent_id != row['intent_id']]
            else:
                result = list(result) + [replace(original)]
            observed.append(result)
        return result
    monkeypatch.setattr(rig.broker, 'open_orders', malformed_orders)
    state = _install_native_fill_race(rig, pid, harness, tp, monkeypatch)
    rig.engine._protection_progress_one(rig.clock.now(), row, pos)
    assert state['injected'] and rig.store.position(pid)['qty_open'] == 2
    assert observed
    assert len([order for order in observed[0] if order.intent_id == row['intent_id']]) == (
        0 if defect == 'missing-STP' else 2)
    _assert_race_P0(rig)
    assert _writes(rig) == before, 'a contradictory mixed round cannot create/replace/cancel protection'
    _safety(rig)


def _install_two_fill_round(rig, pid, harness, tp, monkeypatch, *, prior_fill=None):
    """Produce old/intermediate P while the same native TP advances again."""
    state = {'round': 0, 'rounds': {}, 'injected': False, 'race_round': None,
             'fill': None, 'events': [], 'old_quantity': None}
    steps = {'first': prior_fill is not None, 'second': False}
    for name in SOURCES:
        original = getattr(rig.broker, name)
        def query(*args, _name=name, _original=original, **kwargs):
            is_proof = rig.engine._protection_query_position == pid
            if is_proof and _name == 'open_orders':
                state['round'] += 1
                state['rounds'][state['round']] = {}
            number = state['round'] if is_proof else None
            result = _original(*args, **kwargs)
            if is_proof and _name == 'open_orders' and not steps['first']:
                steps['first'] = True
                state['injected'] = True
                state['race_round'] = number
                rig.clock.advance(microseconds=200000)
                event = rig._b28b_callback_fill(tp.intent_id, quantity=1, price=344.04,
                                                exec_id='b28c-intermediate-first.01.01')
                state['events'].append(event)
                state['fill'] = event
                assert rig.store.position(pid)['qty_open'] == 5
                _safety(rig)
            if is_proof and _name == 'positions_fresh' and not steps['second']:
                steps['second'] = True
                state['injected'] = True
                state['race_round'] = number
                [position] = [position for position in result if position.symbol == 'AAPL']
                state['old_quantity'] = position.quantity
                assert position.quantity == 5
                rig.clock.advance(microseconds=200000)
                event = rig._b28b_callback_fill(tp.intent_id, quantity=1, price=344.04,
                                                exec_id='b28c-intermediate-second.01.01')
                state['events'].append(event)
                state['fill'] = event
                assert rig.store.intent_filled_qty(tp.intent_id) == 2
                assert rig.store.position(pid)['qty_open'] == 4
                _safety(rig)
                rig.clock.advance(microseconds=200000)
            if is_proof:
                state['rounds'][number][_name] = result
            return result
        monkeypatch.setattr(rig.broker, name, query)
    return state


def _finish_positive_edge(rig, pid, harness, state):
    assert state['injected'] and state['old_quantity'] == 5
    assert rig.store.position(pid)['qty_open'] == 4
    _assert_no_false_race_alert(rig, pid)
    _safety(rig)
    _tick_until(rig, lambda: bool(_fresh_rounds(rig, pid, harness, state)), seconds=60)
    _assert_no_false_race_alert(rig, pid)
    assert rig.store.position(pid)['qty_open'] == 4
    assert rig.store.protection(pid)['broker_order_id'] == 706
    assert len(rig.broker.sent_protections) == 1
    _safety(rig)


def test_B28c_old_fill_precise_time_verification_plus_new_TP_fill_is_not_economic_drift(
        larger_overnight_run, monkeypatch):
    rig, pid, harness = larger_overnight_run
    tp = _activate_tp1(rig, pid)
    assert tp.quantity == 2
    prior = rig._b28b_callback_fill(tp.intent_id, quantity=1, price=344.04,
                                    exec_id='b28c-prior-partial-tp.01.01')
    assert prior.time_uncertain and rig.store.position(pid)['qty_open'] == 5
    [before] = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (prior.exec_id,)).fetchall()
    assert rig.store.control('execution_time_unverified:' + prior.exec_id) is not None
    corrected = datetime.fromisoformat(before['time']).replace(microsecond=0) - timedelta(seconds=1)
    raw = harness.raw
    harness.raw = lambda fill: (corrected.strftime('%Y%m%d %H:%M:%S UTC')
                                if fill.exec_id == prior.exec_id else raw(fill))
    state = _install_two_fill_round(rig, pid, harness, tp, monkeypatch, prior_fill=prior)
    _forbid_write_from_old_round(rig, pid, harness, state, monkeypatch)
    rig.tick()
    assert len(state['events']) == 1
    [after] = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (prior.exec_id,)).fetchall()
    assert datetime.fromisoformat(after['time']) == corrected
    assert after['time'] != before['time'], 'native complete query must actually correct the old timestamp'
    assert rig.store.control('execution_time_unverified:' + prior.exec_id) is None
    economic = ('exec_id', 'intent_id', 'position_id', 'symbol', 'side', 'quantity', 'price',
                'broker_order_id', 'perm_id')
    assert tuple(before[name] for name in economic) == tuple(after[name] for name in economic)
    assert rig.store.intent_filled_qty(tp.intent_id) == 2
    _finish_positive_edge(rig, pid, harness, state)


def test_B28c_two_native_partial_TP_fills_allow_exact_intermediate_position_prefix(
        larger_overnight_run, monkeypatch):
    rig, pid, harness = larger_overnight_run
    tp = _activate_tp1(rig, pid)
    assert tp.quantity == 2 and rig.store.position(pid)['qty_open'] == 6
    state = _install_two_fill_round(rig, pid, harness, tp, monkeypatch)
    _forbid_write_from_old_round(rig, pid, harness, state, monkeypatch)
    rig.tick()
    assert len(state['events']) == 2
    assert all(event.intent_id == tp.intent_id and event.side is Side.SELL and event.quantity == 1
               for event in state['events'])
    assert state['old_quantity'] == 5, 'P=5 is the genuine midpoint of native 6 -> 5 -> 4'
    assert rig.store.intent_filled_qty(tp.intent_id) == 2
    _finish_positive_edge(rig, pid, harness, state)

"""B28d final-TP closure races through native callbacks and real SQLite.

The prior-day entry, TP1, BE acknowledgement, next-day restart, TP2 and owned
original-STP cancellation all use existing offline integration fixtures. Only
broker economics/callback timing are scripted. No SQL writes, authority cache,
permission gate, or production confirmation function is replaced.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.fake import FakeBroker
from v6lite.models import FillEvent, OrderRequest, Purpose, Side
from v6lite.tests.helpers import et
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _restart, _safety, _tp_requests, _writes,
)
from v6lite.tests.test_batch28b_overnight_refresh import NEXT_DAY, overnight_run, _tick_until
from v6lite.tests.test_batch28c_price_confirmation import price_run, _be_pending, _confirmed


def _final_tp_inflight(price_run, tmp_path, monkeypatch):
    rig, pid, wire = price_run()
    rig.px('AAPL', 344.07)  # existing six-cent synthetic spread: TP1 fills @344.04
    pending = _be_pending(rig, pid, wire)
    wire.ack(only_open_order=True)
    _confirmed(rig, pid, pending)
    ref = rig.store.protection(pid)['intent_id']
    assert rig.store.position(pid)['qty_open'] == 2
    rig.engine.shutdown(grace_seconds=0)
    rig.at(et(9, 29, day=NEXT_DAY))
    _restart(rig, tmp_path, monkeypatch, reopen=True)
    assert rig.store.position(pid)['qty_open'] == 2
    assert rig.store.protection(pid)['trigger_price'] == 342.77
    rig.at(et(9, 30, 20, day=NEXT_DAY) + timedelta(milliseconds=545))
    # Use the established fixture's TP2/TP3 thresholds for sequential triggers;
    # the executions retain the actual 10/22 economic prices below.
    rig.px('AAPL', float(rig.store.position(pid)['tp2']) + .20)
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    assert rig.broker.protection_pending_modifications[ref].quantity == 1
    wire.ack()
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 2)
    tp2 = _tp_requests(rig)[-1]
    assert tp2.purpose is Purpose.TP2 and tp2.quantity == 1
    rig._b28b_callback_fill(tp2.intent_id, quantity=1, price=346.33,
                           exec_id='b28d-1022-tp2.01.01')
    _tick_until(rig, lambda: rig.store.position(pid)['tp2_done'] == 1 and
                rig.store.protection(pid)['state'] == 'WORKING')
    assert rig.store.position(pid)['qty_open'] == 1
    rig.px('AAPL', 347.04)
    _tick_until(rig, lambda: ref in rig.broker.cancels)
    assert len(_tp_requests(rig)) == 2
    economic = rig.broker.orders[ref]
    economic.status = 'Cancelled'
    wire.ib.wrapper.lastTime = rig.clock.now()
    wire.ib.wrapper.orderStatus(economic.broker_order_id, 'Cancelled',
        economic.filled, economic.remaining, 0., economic.broker_order_id + 50000,
        0, 0., rig.cfg.client_id, '', 0.)
    rig.engine._drain(rig.clock.now())
    _safety(rig)
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 3)
    tp3 = _tp_requests(rig)[-1]
    assert tp3.purpose is Purpose.TP3 and tp3.quantity == 1
    original = rig.store.intent(ref)
    assert original['status'] == 'CANCELLED'
    assert original['last_raw_status'] in {'Cancelled', 'ApiCancelled'}
    assert not original['hold_reason']
    row = rig.store.protection(pid)
    assert row['state'] == 'TP_ACTIVE' and row['tp_intent_id'] == tp3.intent_id
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 0
    assert rig.broker.orders[ref].status == 'Cancelled'
    assert ref in rig.broker.cancels
    assert len(rig.broker.sent_protections) == 1
    rig._b26_alerts.clear()
    rig.channel.messages.clear()
    _safety(rig)
    return rig, pid, wire, tp3, ref


def _native_malformed_extra_buy(rig, wire, tp):
    # Model a real additional BUY whose broker metadata incorrectly names our
    # SELL intent. The native decoder/Store mismatch path must keep its anomaly
    # and the nonzero position-fill net; tests never edit those economic rows.
    request = OrderRequest('external-b28d-wrong-side', 'AAPL', Side.BUY, 1, 346.93, Purpose.ENTRY)
    FakeBroker.submit_limit(rig.broker, request)
    raw = FakeBroker.fill(rig.broker, request.intent_id, qty=1, price=346.93,
                         exec_id='b28d-wrong-side-net.01.01', emit=False)
    wire.adapter.set_urgent_handler(None)
    try:
        events = wire.harness.callback(replace(raw, intent_id=tp.intent_id))
    finally:
        wire.adapter.set_urgent_handler(wire.deliver)
    [event] = [event for event in events if isinstance(event, FillEvent)]
    assert event.intent_id == tp.intent_id and event.side is Side.BUY
    event = replace(event, commission=raw.commission)
    rig.broker.orders[request.intent_id].fills[-1] = event
    rig.broker.events.extend(event if isinstance(value, FillEvent) else value for value in events)
    rig.engine._drain(rig.clock.now())


def _closed_query_race(rig, pid, wire, tp, ref, monkeypatch, *, fault=None):
    state = {'chronology': [], 'fill': None, 'query_failed': False, 'old_orders': None,
             'cancel_callback': None}
    if fault == 'other-SELL':
        # Deliberately unsafe external broker input. A different actor's SELL
        # remains live after our final TP closes the stock position.
        FakeBroker.submit_limit(rig.broker, OrderRequest(
            'external-b28d-working-sell', 'AAPL', Side.SELL, 1, 346.93, Purpose.TP3))
    after_source = wire.after_source
    def source(name, result):
        after_source(name, result)
        if rig.engine._protection_query_position != pid:
            return
        if name == 'open_orders' and not state['chronology']:
            state['old_orders'] = list(result)
        if name != 'positions_fresh' or state['chronology']:
            return
        [old] = [position for position in result if position.symbol == 'AAPL']
        assert old.quantity == 1
        state['chronology'].append('native-positionEnd-old-P1')
        rig.clock.advance(microseconds=250000)
        if fault == 'cancel-identity-unconfirmed':
            # The original STP was durably confirmed cancelled before TP3 was
            # sent. A later same-hard-identity working report contradicts that
            # receipt; its diagnostic must survive the subsequent real close.
            economic = rig.broker.orders[ref]
            economic.status = 'PreSubmitted'
            wire.ib.wrapper.lastTime = rig.clock.now()
            callbacks_before = len(wire.status_callbacks)
            wire.ib.wrapper.orderStatus(economic.broker_order_id, 'PreSubmitted',
                economic.filled, economic.remaining, 0., economic.broker_order_id + 50000,
                0, 0., rig.cfg.client_id, 'synthetic working STP after confirmed own cancellation', 0.)
            rig.engine._drain(rig.clock.now())
            observed = wire.status_callbacks[callbacks_before:]
            assert len(observed) == 1 and observed[0][1:] == (ref, 'PreSubmitted')
            state['cancel_callback'] = observed[0]
        fill = rig._b28b_callback_fill(tp.intent_id, quantity=1, price=346.93,
                                      exec_id='b28d-1022-final-tp3.01.01')
        state['fill'] = fill
        assert fill.intent_id == tp.intent_id and fill.side is Side.SELL
        assert fill.broker_order_id == rig.store.intent(tp.intent_id)['broker_order_id']
        assert fill.perm_id == rig.store.intent(tp.intent_id)['perm_id']
        assert rig.store.position(pid)['status'] == 'CLOSED'
        assert rig.store.position(pid)['qty_open'] == 0
        state['chronology'].append('native-TP3-fill-durably-closed-P0')
        if fault in {'unknown-native-SELL', 'unknown-query-SELL'}:
            request = OrderRequest('external-b28d-extra-sell', 'AAPL', Side.SELL, 1, 346.93, Purpose.TP3)
            FakeBroker.submit_limit(rig.broker, request)
            if fault == 'unknown-native-SELL':
                extra = rig._b28b_callback_fill(request.intent_id, quantity=1, price=346.93,
                                               exec_id='b28d-unknown-native.01.01')
                assert extra.intent_id == ''
            else:
                FakeBroker.fill(rig.broker, request.intent_id, qty=1, price=346.93,
                                exec_id='b28d-unknown-query.01.01', emit=False)
        elif fault == 'ledger-nonzero':
            _native_malformed_extra_buy(rig, wire, tp)
        if fault is None or fault == 'query-unavailable':
            _safety(rig)
        rig.clock.advance(microseconds=250000)
        state['chronology'].append('returned-obsolete-P1')
    wire.after_source = source
    if fault == 'query-unavailable':
        executions = rig.broker.executions_today
        def unavailable():
            if state['chronology'] and not state['query_failed'] and rig.engine._protection_query_position == pid:
                state['query_failed'] = True
                raise BrokerError('synthetic client execution query unavailable after owned final TP closure')
            return executions()
        monkeypatch.setattr(rig.broker, 'executions_today', unavailable)
    return state


def _assert_terminal(rig, pid, ref):
    pos = rig.store.position(pid)
    row = rig.store.protection(pid)
    assert pos['status'] == 'CLOSED' and pos['qty_open'] == 0
    assert row['state'] == 'CANCELLED', 'the cancelled original STP must not remain WORKING or pretend to have FILLED'
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 0
    assert row['pending_qty'] is None
    assert row['tp_stage'] is row['tp_qty'] is row['tp_intent_id'] is None
    assert row['gap_b_started_at'] is None
    assert rig.store.intent(ref)['status'] == 'CANCELLED'
    assert rig.broker.orders[ref].status == 'Cancelled'
    assert not rig.store.intent(ref)['hold_reason']
    assert rig.store.control('protect_evidence_failure:' + pid) is None
    _safety(rig)


@pytest.mark.parametrize('fault', [None, 'query-unavailable'], ids=['complete-native-round', 'failed-query-after-close'])
def test_B28d_final_TP3_native_fill_invalidates_old_P1_without_P0_and_enters_terminal(
        price_run, tmp_path, monkeypatch, fault):
    rig, pid, wire, tp, ref = _final_tp_inflight(price_run, tmp_path, monkeypatch)
    before = _writes(rig)
    last_audit = rig.store.conn.execute('SELECT COALESCE(MAX(id),0) FROM audit').fetchone()[0]
    row, pos = rig.store.protection(pid), rig.store.position(pid)
    state = _closed_query_race(rig, pid, wire, tp, ref, monkeypatch, fault=fault)
    rig.engine._protection_progress_one(rig.clock.now(), row, pos)
    assert state['chronology'] == ['native-positionEnd-old-P1', 'native-TP3-fill-durably-closed-P0',
                                   'returned-obsolete-P1']
    assert state['query_failed'] is (fault == 'query-unavailable')
    assert not [text for level, text in rig._b26_alerts if level == 'P0']
    assert not [text for level, text in rig._b26_alerts if level == 'P1' and '券商查询已限速' in text]
    _assert_terminal(rig, pid, ref)
    assert _writes(rig) == before
    assert rig.store.intent_filled_qty(tp.intent_id) == tp.quantity == 1
    assert sum(fill['quantity'] if fill['side'] == 'BUY' else -fill['quantity']
               for fill in rig.store.position_fills(pid)) == 0
    fresh = rig.engine._fresh_held.get('AAPL')
    assert fresh is None or fresh[0] == 0, 'the obsolete P1 must not survive as fresh authority'
    audits = [(event['kind'], json.loads(event['payload'])) for event in rig.store.conn.execute(
        'SELECT kind,payload FROM audit WHERE id>? ORDER BY id', (last_audit,))]
    assert any('SUPERSEDED' in kind and payload.get('position_id') == pid for kind, payload in audits)
    for _ in range(3):
        rig.advance(seconds=1)
        rig.tick()
    assert not [text for level, text in rig._b26_alerts if level == 'P0']
    assert _writes(rig) == before
    _assert_terminal(rig, pid, ref)


def test_B28d_final_TP3_normal_callback_also_finishes_cancelled_protection(price_run, tmp_path, monkeypatch):
    rig, pid, wire, tp, ref = _final_tp_inflight(price_run, tmp_path, monkeypatch)
    before = _writes(rig)
    rig._b28b_callback_fill(tp.intent_id, quantity=1, price=346.93,
                           exec_id='b28d-ordinary-final-tp3.01.01')
    _assert_terminal(rig, pid, ref)
    assert not [text for level, text in rig._b26_alerts if level == 'P0']
    assert _writes(rig) == before


@pytest.mark.parametrize('fault', ['unknown-native-SELL', 'unknown-query-SELL', 'other-SELL',
                                  'ledger-nonzero', 'cancel-identity-unconfirmed'])
def test_B28d_final_TP_closure_never_excuses_unknown_SELL_nonzero_net_or_unconfirmed_cancel(
        price_run, tmp_path, monkeypatch, fault):
    rig, pid, wire, tp, ref = _final_tp_inflight(price_run, tmp_path, monkeypatch)
    own_order_ids = [request.intent_id for request in rig.broker.sent if request.position_id == pid]
    last_audit = rig.store.conn.execute('SELECT COALESCE(MAX(id),0) FROM audit').fetchone()[0]
    row, pos = rig.store.protection(pid), rig.store.position(pid)
    state = _closed_query_race(rig, pid, wire, tp, ref, monkeypatch, fault=fault)
    rig.engine._protection_progress_one(rig.clock.now(), row, pos)
    assert state['fill'] is not None and rig.store.position(pid)['qty_open'] == 0
    assert rig.store.intent_filled_qty(tp.intent_id) >= 1
    pages = [text for level, text in rig._b26_alerts if level == 'P0']
    assert pages, 'a known contradiction cannot inherit the legitimate final-TP close exemption'
    assert [request.intent_id for request in rig.broker.sent if request.position_id == pid] == own_order_ids
    assert len(rig.broker.sent_protections) == 1
    if fault == 'ledger-nonzero':
        net = sum(fill['quantity'] if fill['side'] == 'BUY' else -fill['quantity']
                  for fill in rig.store.position_fills(pid))
        assert net != 0 and rig.store.anomalies()
    elif fault == 'cancel-identity-unconfirmed':
        assert state['cancel_callback'] is not None
        assert state['cancel_callback'][1:] == (ref, 'PreSubmitted')
        assert rig.broker.orders[ref].status == 'PreSubmitted'
        assert not any('SUPERSEDED' in event['kind'] and
                       json.loads(event['payload']).get('position_id') == pid
                       for event in rig.store.conn.execute(
                           'SELECT kind,payload FROM audit WHERE id>? ORDER BY id', (last_audit,)))
    elif fault == 'other-SELL':
        economic = rig.broker.orders['external-b28d-working-sell']
        assert any(not order.ours and order.broker_order_id == economic.broker_order_id and
                   order.perm_id == economic.broker_order_id + 50000 and order.remaining == 1
                   for order in state['old_orders'])
        assert economic.remaining == 1
    else:
        assert next(position.quantity for position in FakeBroker.positions_fresh(rig.broker)
                    if position.symbol == 'AAPL') == -1
        unknown_id = 'b28d-unknown-native.01.01' if fault == 'unknown-native-SELL' else 'b28d-unknown-query.01.01'
        found = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (unknown_id,)).fetchone()
        if fault == 'unknown-native-SELL':
            assert found is not None and found['position_id'] is None
        else:
            assert found is None, 'the native query contradiction must be observed before any callback booking'

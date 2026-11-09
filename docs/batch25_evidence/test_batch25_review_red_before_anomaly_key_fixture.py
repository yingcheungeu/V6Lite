"""B25 independent follow-up regressions against the first frozen candidate.

Only synthetic Rig/real native adapter callbacks; no sockets, runtime state,
operator configuration, source mutation, or implementation-derived expectations.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from dataclasses import replace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.models import ConnectionEvent, PriceObservationEvent, StatusEvent, OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch23_contracts import _working, _writes, _native_status
from v6lite.tests.test_batch24_stp_echo import _no_network


def _p0_stop(rig):
    return [(title, body) for title, body in rig.channel.messages
            if 'P0' in title and '连接反复重建' in title]


def _audit(rig, kind):
    return [json.loads(row['payload']) for row in rig.store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY id', (kind,))]


@pytest.mark.parametrize('raw', ['PreSubmitted', 'Cancelled'])
@pytest.mark.parametrize('echo', [0.0, 340.19, sys.float_info.max], ids=['zero', 'finite', 'UNSET'])
def test_B25_review_creation_bound_snapshot_Status_and_Price_cannot_mutate_authority_after_sync(
        tmp_path, monkeypatch, raw, echo):
    rig, pid = _working(tmp_path)
    row = rig.store.protection(pid)
    ref = row['intent_id']
    retained = 'synthetic pre-existing operator identity hold: keep exact original evidence'
    rig.store.hold_intent(rig.clock.now(), ref, retained)
    adapter, trade = _native_status(rig, ref, raw=raw, wrong='permId', cap=119.51)
    trade.order.orderId += 17; trade.orderStatus.orderId = trade.order.orderId
    trade.order.auxPrice += .01
    trade.order.lmtPrice = echo
    adapter._connection_syncing = True
    adapter._on_status(trade)
    adapter._on_open_order(trade)
    events = adapter.drain_events()
    statuses = [event for event in events if isinstance(event, StatusEvent)]
    prices = [event for event in events if isinstance(event, PriceObservationEvent)]
    assert len(statuses) == 1 and prices, 'genuine adapter callbacks must emit both snapshot roles'
    assert all(getattr(event, 'connection_snapshot', False) for event in statuses + prices)
    # Consume only after native sync ended: creation-time provenance survives.
    adapter._connection_syncing = False
    before_intent = dict(rig.store.intent(ref))
    before_protection = dict(rig.store.protection(pid))
    before_controls = {row['key']: row['value'] for row in rig.store.conn.execute('SELECT key,value FROM controls')}
    before_prices = len(_audit(rig, 'ORDER_PRICE_OBSERVATION'))
    before = _writes(rig)
    for event in events:
        rig.engine._handle_event(rig.clock.now(), event)
    assert dict(rig.store.intent(ref)) == before_intent, (
        'snapshot last_raw_status/IDs/hold/status are hints, never trading authority')
    assert dict(rig.store.protection(pid)) == before_protection, (
        'snapshot cannot change IDs, stop, A/B, status_seen_generation or confirmed_at')
    assert {row['key']: row['value'] for row in rig.store.conn.execute('SELECT key,value FROM controls')} == before_controls
    assert len(_audit(rig, 'ORDER_PRICE_OBSERVATION')) == before_prices
    assert rig.store.intent(ref)['hold_reason'] == retained
    assert _writes(rig) == before
    status_audits = _audit(rig, 'CONNECTION_SNAPSHOT_STATUS')
    price_audits = _audit(rig, 'CONNECTION_SNAPSHOT_PRICE')
    assert any(a.get('raw_status') == raw and a.get('broker_echo_lmt') == echo and
               a.get('broker_order_id') == trade.order.orderId and a.get('perm_id') == trade.order.permId
               for a in status_audits)
    assert any(a.get('broker_echo_lmt') == echo and a.get('aux_price') == trade.order.auxPrice
               for a in price_audits), 'raw callback diagnostics remain auditable'


def _failed_connections(tmp_path, monkeypatch, reasons):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start(); rig.at(et(10, 1))
    assert rig.engine.status_snapshot(rig.clock.now())['entries_allowed'], 'start from a healthy entry window'
    attempts = []
    def failed_connect():
        attempts.append(rig.clock.now())
        raise BrokerError(reasons[min(len(attempts)-1, len(reasons)-1)])
    monkeypatch.setattr(rig.broker, 'connect', failed_connect)
    rig.broker.connected = False
    rig.engine._on_connection(rig.clock.now(), ConnectionEvent(False, 'synthetic native socket reset'))
    rig.channel.messages.clear()
    return rig, attempts, rig.clock.now()


def _reason(base, generation):
    return (base + f' [generation={generation} PID={9000+generation} reqId={700+generation} '
            f'elapsed={generation+.125:.3f}s attempt={generation}]')


def test_B25_review_incidental_generation_PID_reqId_elapsed_attempt_never_bypass_same_cause_limit(
        tmp_path, monkeypatch):
    base = 'SDK synchronization incomplete; registered End missing: openOrders'
    reasons = [_reason(base, value) for value in (1, 2, 3, 4)]
    rig, attempts, start = _failed_connections(tmp_path, monkeypatch, reasons)
    rig.engine._maintain_connection(rig.clock.now())
    rig.advance(seconds=29.999); rig.engine._maintain_connection(rig.clock.now())
    assert len(attempts) == 1
    rig.advance(seconds=.001); rig.engine._maintain_connection(rig.clock.now())
    assert [(value-start).total_seconds() for value in attempts] == [0, 30]
    rig.advance(seconds=59.999); rig.engine._maintain_connection(rig.clock.now())
    assert len(attempts) == 2 and _p0_stop(rig) == []
    rig.advance(seconds=.001); rig.engine._maintain_connection(rig.clock.now())
    assert len(attempts) == 2 and len(_p0_stop(rig)) == 1, 'incidental metadata is not a new cause'
    assert reasons[1] in _p0_stop(rig)[0][1], 'complete latest broker reason remains in the card'
    audited = _audit(rig, 'RECONNECT_ATTEMPT')
    assert [item['reason'] for item in audited] == reasons[:2]
    for delay in (210, 1, 300):
        rig.advance(seconds=delay); rig.engine._maintain_connection(rig.clock.now())
        assert len(attempts) == 2 and len(_p0_stop(rig)) == 1
    assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
    assert _writes(rig) == (0, 0, 0, 0)


def test_B25_review_normalization_preserves_truly_different_query_failure_causes(
        tmp_path, monkeypatch):
    bases = ['SDK synchronization incomplete; registered End missing: openOrders',
             'SDK synchronization incomplete; registered End missing: positions',
             'executions request refused by broker', 'managed account identity missing']
    rig, attempts, start = _failed_connections(tmp_path, monkeypatch,
                                              [_reason(base, n+1) for n, base in enumerate(bases)])
    rig.engine._maintain_connection(rig.clock.now())
    for delay, count in ((30, 2), (60, 3), (120, 4)):
        rig.advance(seconds=delay-.001); rig.engine._maintain_connection(rig.clock.now())
        assert len(attempts) == count-1
        rig.advance(seconds=.001); rig.engine._maintain_connection(rig.clock.now())
        assert len(attempts) == count
    assert [(value-start).total_seconds() for value in attempts] == [0, 30, 90, 210]
    assert _p0_stop(rig) == []


def test_B25_review_connected_socket_with_once_failed_execution_recovery_retries_after_30_and_keeps_budget(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start(); rig.at(et(10, 1))
    assert rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
    physical, execution_queries = [], []
    original_connect = rig.broker.connect
    original_exec = rig.broker.executions_today
    def connected():
        physical.append(rig.clock.now()); original_connect()
    def executions():
        execution_queries.append(rig.clock.now())
        if len(execution_queries) == 1:
            raise BrokerError('synthetic executions temporarily unavailable after reconnect')
        return original_exec()
    monkeypatch.setattr(rig.broker, 'connect', connected)
    monkeypatch.setattr(rig.broker, 'executions_today', executions)
    reason = 'synthetic repeated physical socket reset'
    rig.broker.connected = False
    rig.engine._on_connection(rig.clock.now(), ConnectionEvent(False, reason))
    start = rig.clock.now()
    rig.engine._maintain_connection(rig.clock.now())
    assert rig.broker.is_connected() and getattr(rig.broker, 'is_synchronized', rig.broker.is_connected)()
    assert len(physical) == 1 and len(execution_queries) == 1
    assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed'], (
        'positions reconcile alone cannot declare failed execution recovery healthy')
    rig.advance(seconds=29.999); rig.engine._maintain_connection(rig.clock.now())
    assert len(physical) == 1 and len(execution_queries) == 1
    rig.advance(seconds=.001); rig.engine._maintain_connection(rig.clock.now())
    assert len(physical) == 1 and len(execution_queries) == 2, 'retry recovery on the existing healthy socket'
    assert (execution_queries[-1]-start).total_seconds() == 30
    recovered = rig.engine.status_snapshot(rig.clock.now())
    assert rig.engine.flags.reconcile_ok and recovered['reconnect_next_at'] is None
    assert 'recovery_failed' in rig.store.anomalies()
    assert recovered['entries_allowed'] is False, 'successful retry cannot clear the existing operator anomaly'
    rig.store.clear_anomaly(rig.clock.now(), 'recovery_failed', operator='SyntheticTest',
                            reason='synthetic operator reviewed completed fresh recovery and reconciliation')
    assert rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
    assert len(_audit(rig, 'RECONNECT_ATTEMPT')) == 1, 'virtual recovery is not another physical reconnect'
    # The now successful recovery does not erase the original rolling budget.
    rig.advance(seconds=31)
    rig.broker.connected = False
    rig.engine._on_connection(rig.clock.now(), ConnectionEvent(False, reason))
    rig.engine._maintain_connection(rig.clock.now())
    assert len(physical) == 2
    rig.advance(seconds=31)
    rig.broker.connected = False
    rig.engine._on_connection(rig.clock.now(), ConnectionEvent(False, reason))
    rig.engine._maintain_connection(rig.clock.now())
    assert len(physical) == 2 and len(_p0_stop(rig)) == 1
    assert _writes(rig) == (0, 0, 0, 0)


def test_B25_review_P0_stop_survives_connected_event_and_normal_tick_without_reconnect_or_entry(
        tmp_path, monkeypatch):
    reason = 'synthetic persistent native channel reset'
    rig, attempts, _ = _failed_connections(tmp_path, monkeypatch, [reason])
    for delay in (0, 30, 60):
        rig.advance(seconds=delay); rig.engine._maintain_connection(rig.clock.now())
    assert len(attempts) == 2 and len(_p0_stop(rig)) == 1
    before = _writes(rig)
    # A late socket-up indication is not operator authorization to clear P0.
    rig.broker.connected = True
    rig.engine._on_connection(rig.clock.now(), ConnectionEvent(True, 'synthetic delayed restored notice'))
    rig.tick()
    snapshot = rig.engine.status_snapshot(rig.clock.now())
    assert snapshot.get('automatic_reconnect_stopped', False) is True
    assert snapshot['entries_allowed'] is False
    assert not snapshot['broker_connected'] and not snapshot['reconcile_ok']
    assert len(attempts) == 2 and _writes(rig) == before and len(_p0_stop(rig)) == 1
    rig.advance(minutes=6); rig.tick()
    assert rig.engine.status_snapshot(rig.clock.now())['entries_allowed'] is False
    assert len(attempts) == 2 and _writes(rig) == before and len(_p0_stop(rig)) == 1

@pytest.mark.parametrize('missing_today_execution', [False, True], ids=['historical-gap', 'historical-and-today-gap'])
def test_B25_review_crossday_S_less_than_P_and_missing_required_today_exec_cannot_increase_STP(
        tmp_path, monkeypatch, missing_today_execution):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=.0015)
    rig.start(); rig.at(et(10, 0, 30)); rig.tick()
    [entry] = [request for request in rig.broker.sent if request.purpose is Purpose.ENTRY]
    assert entry.quantity == 3
    rig.broker.fill(entry.intent_id, qty=1)
    rig.advance(seconds=1); rig.tick()
    [position] = rig.store.open_positions(); pid = position['position_id']
    [stop] = rig.broker.sent_protections
    assert rig.store.protection(pid)['confirmed_remaining'] == 1
    rig.at(et(10, 1, day=(2026, 10, 7)))
    fill = rig.broker.fill(entry.intent_id, qty=1, emit=False)
    original_exec = rig.broker.executions_today
    def today_exec():
        rows = [event for event in original_exec() if event.time.date() == rig.clock.now().date()]
        return [event for event in rows if not missing_today_execution or event.exec_id != fill.exec_id]
    monkeypatch.setattr(rig.broker, 'executions_today', today_exec)
    rig.engine._on_urgent_broker_event(fill)
    before = _writes(rig)
    rig.engine.process_protection_priority()
    assert rig.store.position(pid)['qty_open'] == 2
    assert rig.store.protection(pid)['confirmed_remaining'] == 1
    assert rig.broker.orders[stop.intent_id].req.quantity == 1
    assert len(rig.broker.sent_protections) == 1 and rig.broker.modified_protections == []
    assert _writes(rig) == before, 'incomplete cross-day proof cannot increase/recreate a SELL'
    assert any('P0' in title for title, body in rig.channel.messages)


@pytest.mark.parametrize('fault', ['absent-GTC', 'manual-hold', 'other-SELL'])
def test_B25_review_crossday_carry_never_authorizes_recreation_release_or_ignores_hold_and_other_sells(
        tmp_path, monkeypatch, fault):
    rig, pid = _working(tmp_path)
    row = rig.store.protection(pid); ref = row['intent_id']
    rig.at(et(10, 1, day=(2026, 10, 7)))
    monkeypatch.setattr(rig.broker, 'executions_today', lambda: [])
    held = 'synthetic retained operator identity hold'
    if fault == 'absent-GTC':
        rig.broker.orders[ref].status = 'Cancelled'  # server canceled while the old process was stopped
    elif fault == 'manual-hold':
        rig.store.hold_intent(rig.clock.now(), ref, held)
    else:
        # A synthetic already-confirmed safety reduction precedes the
        # external order. Never inject S3+X1>P3 into the strong B23 hooks.
        # Native order, durable intent and confirmed quantities all agree.
        rig.broker.orders[ref].req = replace(rig.broker.orders[ref].req, quantity=2)
        rig.store.update_protection(rig.clock.now(), pid, requested_qty=2,
                                    confirmed_remaining=2, pending_max_remaining=2)
        rig.store.update_protection_intent_quantity(rig.clock.now(), ref, 2)
        external = OrderRequest('external-b25-prior-day-seller', 'AAPL', Side.SELL, 1, 120., Purpose.TP1)
        rig.broker._register(external)
        assert rig.broker.orders[ref].remaining + rig.broker.orders[external.intent_id].remaining == 3
    submissions = len(rig.broker.sent_protections)
    rig.engine._protection_progress(rig.clock.now())
    assert len(rig.broker.sent_protections) == submissions, 'cross-day incomplete history never recreates protection'
    assert not [request for request in rig.broker.modified_protections if request.quantity > 3]
    assert not [request for request in rig.broker.sent
                if request.side is Side.SELL and request.intent_id.startswith('v6l-')]
    assert rig.store.position(pid)['status'] == 'OPEN' and rig.store.position(pid)['qty_open'] == 3
    if fault == 'manual-hold':
        assert rig.store.intent(ref)['hold_reason'] == held
    elif fault == 'other-SELL':
        assert rig.store.control('frozen:AAPL') is not None
        # A safety reduction may run; this exception never grants extra S.
        assert rig.broker.orders[ref].remaining + 1 <= 3
    else:
        assert any('P0' in title for title, body in rig.channel.messages)
    writes = _writes(rig)
    rig.store.set_control(rig.clock.now(), 'release:AAPL', json.dumps({
        'operator': 'SyntheticTest', 'reason': 'synthetic release must remain refused without complete truth'}))
    rig.engine._apply_releases(rig.clock.now())
    assert rig.store.position(pid)['status'] == 'OPEN'
    assert _audit(rig, 'RELEASE_REFUSED'), 'carryover display evidence never grants manual release'
    assert _writes(rig) == writes

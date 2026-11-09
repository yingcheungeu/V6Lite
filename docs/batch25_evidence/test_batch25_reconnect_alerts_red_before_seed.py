"""B25 independent retry budget and alert-time contracts, synthetic state only."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.models import ConnectionEvent, Purpose
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch23_contracts import _working, _writes
from v6lite.tests.test_batch24_stp_echo import _pending_rig, _no_network


def _rate_pages(rig):
    return [(title, body) for title, body in rig.channel.messages
            if 'P1' in title and '券商查询已限速' in title]


def _stop_pages(rig):
    return [(title, body) for title, body in rig.channel.messages
            if 'P0' in title and '连接反复重建' in title]


def _disconnected(tmp_path, monkeypatch, reasons):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start(); rig.channel.messages.clear()
    calls = []
    def fail():
        calls.append(rig.clock.now())
        raise BrokerError(reasons[min(len(calls)-1, len(reasons)-1)])
    monkeypatch.setattr(rig.broker, 'connect', fail)
    rig.broker.connected = False
    rig.engine._on_connection(rig.clock.now(), ConnectionEvent(False, 'synthetic socket loss'))
    rig.channel.messages.clear()
    return rig, calls, rig.clock.now()


def test_B25_same_reason_at_most_two_reconnects_in_five_minutes_then_latched_single_P0(
        tmp_path, monkeypatch):
    reason = 'openOrders: unsolicited End seen; connection renewed'
    rig, calls, start = _disconnected(tmp_path, monkeypatch, [reason])
    before = _writes(rig)
    rig.engine._maintain_connection(rig.clock.now())
    assert calls == [start], 'initial reconnect still tries immediately'
    rig.advance(seconds=29.999); rig.engine._maintain_connection(rig.clock.now())
    assert len(calls) == 1
    rig.advance(seconds=.001); rig.engine._maintain_connection(rig.clock.now())
    assert [(value-start).total_seconds() for value in calls] == [0, 30]
    rig.advance(seconds=59.999); rig.engine._maintain_connection(rig.clock.now())
    assert len(calls) == 2 and _stop_pages(rig) == []
    rig.advance(seconds=.001); rig.engine._maintain_connection(rig.clock.now())
    assert len(calls) == 2 and len(_stop_pages(rig)) == 1
    [(title, body)] = _stop_pages(rig)
    assert '已停止自动重连' in title and reason in body
    assert '1.' in body and '2.' in body and ('TWS' in body or '人工' in body)
    assert not rig.engine.flags.broker_connected and not rig.engine.flags.reconcile_ok
    assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
    # A 300-second rolling window does not silently release the operator stop.
    for seconds in (210, 1, 300, 600):
        rig.advance(seconds=seconds); rig.engine._maintain_connection(rig.clock.now())
        assert len(calls) == 2 and len(_stop_pages(rig)) == 1
    assert _writes(rig) == before


def test_B25_reason_drift_never_resets_continuous_30_60_120_retry_backoff(
        tmp_path, monkeypatch):
    reasons = ['native socket unavailable', 'openOrders proof unavailable',
               'positions proof unavailable', 'executions proof unavailable']
    rig, calls, start = _disconnected(tmp_path, monkeypatch, reasons)
    rig.engine._maintain_connection(rig.clock.now())
    for delay, expected in ((30, 2), (60, 3), (120, 4)):
        rig.advance(seconds=delay-.001)
        rig.engine._maintain_connection(rig.clock.now())
        assert len(calls) == expected-1, 'changed reasons cannot reopen the previous shorter delay'
        rig.advance(seconds=.001)
        rig.engine._maintain_connection(rig.clock.now())
        assert len(calls) == expected
    assert [(value-start).total_seconds() for value in calls] == [0, 30, 90, 210]
    assert _stop_pages(rig) == []


def test_B25_successful_reconcile_does_not_erase_same_reason_rolling_budget(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start(); rig.channel.messages.clear()
    connect = rig.broker.connect
    calls = []
    def successful_connect():
        calls.append(rig.clock.now()); connect()
    monkeypatch.setattr(rig.broker, 'connect', successful_connect)
    reason = 'synthetic repeated native channel reset'
    start = rig.clock.now()
    for index in range(3):
        rig.broker.connected = False
        rig.engine._on_connection(rig.clock.now(), ConnectionEvent(False, reason))
        rig.engine._maintain_connection(rig.clock.now())
        if index < 2:
            assert len(calls) == index+1
            assert rig.engine.flags.broker_connected and rig.engine.flags.reconcile_ok
            rig.advance(seconds=31)
    assert len(calls) == 2 and len(_stop_pages(rig)) == 1
    assert not rig.engine.flags.broker_connected
    assert _writes(rig) == (0, 0, 0, 0)


@pytest.mark.parametrize('elapsed', [0, 1, 4.999, 5.0])
def test_B25_initial_A_confirmation_wait_does_not_page_query_limit(
        tmp_path, monkeypatch, elapsed):
    rig, pid, _ = _pending_rig(tmp_path, monkeypatch)
    # A completed empty download while the first submitted STP acknowledgement
    # is still in flight is not a genuine failed identity verification.
    row = rig.store.protection(pid)
    assert row['gap_a_started_at'] is not None and row['perm_id'] is None
    writes = _writes(rig)
    rig.advance(seconds=elapsed)
    rig.engine.process_protection_priority()
    assert _rate_pages(rig) == []
    assert rig.store.protection(pid)['state'] != 'WORKING'
    assert rig.store.protection(pid)['gap_a_started_at'] is not None
    assert _writes(rig) == writes
    assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']


def test_B25_sync_pending_cannot_emit_query_limit_or_manufacture_fresh_coverage(
        tmp_path, monkeypatch):
    rig, pid = _working(tmp_path)
    row = dict(rig.store.protection(pid)); before = _writes(rig)
    # Public BrokerPort lifecycle observation; adding this fixture method on
    # B24 proves behaviour, never a missing-interface AttributeError.
    monkeypatch.setattr(rig.broker, 'is_synchronized', lambda: False, raising=False)
    queries = []
    def incomplete():
        queries.append(rig.clock.now())
        raise BrokerError('synthetic connection synchronization still in progress')
    monkeypatch.setattr(rig.broker, 'open_orders', incomplete)
    rig.advance(seconds=rig.cfg.protection.steady_check_seconds+1)
    rig.engine._protection_progress(rig.clock.now())
    assert _rate_pages(rig) == []
    current = rig.store.protection(pid)
    assert current['confirmed_at'] == row['confirmed_at']
    assert _writes(rig) == before
    assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']


def test_B25_real_post_sync_failed_verification_still_pages_once_then_keeps_retry_gate(
        tmp_path, monkeypatch):
    rig, pid = _working(tmp_path)
    monkeypatch.setattr(rig.broker, 'is_synchronized', lambda: True, raising=False)
    rig.broker.fail_open_orders = True
    before = _writes(rig)
    rig.advance(seconds=rig.cfg.protection.steady_check_seconds+1)
    for delay in (0, .5, .5, 2, 4, 8, 16, 30):
        rig.advance(seconds=delay)
        rig.engine._protection_progress(rig.clock.now())
        assert len(_rate_pages(rig)) == 1
        assert _writes(rig) == before
        assert rig.store.protection(pid)['confirmed_remaining'] == 3
    failure = json.loads(rig.store.control('protect_evidence_failure:' + pid))
    assert failure['paged'] is True, 'the independent F4 60-second deadline remains active'


def test_B25_generic_FakeBroker_connect_pushes_own_working_order_and_End_before_sync(
        tmp_path, monkeypatch):
    rig, pid = _working(tmp_path)
    row = rig.store.protection(pid)
    rig.broker.disconnect()
    observed = []
    def snapshot(stage, payload):
        observed.append((stage, payload,
                         getattr(rig.broker, 'is_synchronized', lambda: rig.broker.connected)()))
    # The hook exists as a test callback on both packages; B24 naturally fails
    # because its connect does not emit snapshots, rather than import failure.
    monkeypatch.setattr(rig.broker, 'connect_snapshot_hook', snapshot, raising=False)
    def forbidden_fresh(*args, **kwargs):
        raise AssertionError('connection snapshot must not invoke a fresh authority getter')
    for name in ('open_orders', 'positions_fresh', 'executions_today'):
        monkeypatch.setattr(rig.broker, name, forbidden_fresh)
    rig.broker.connect()
    stages = [stage for stage, payload, ready in observed]
    assert stages == ['openOrder', 'orderStatus', 'openOrderEnd', 'synchronized']
    assert [ready for stage, payload, ready in observed[:-1]] == [False, False, False]
    [order] = [payload for stage, payload, _ in observed if stage == 'openOrder']
    assert order.intent_id == row['intent_id'] and order.total_quantity == 3
    assert order.order_type == 'STP' and order.limit_price is None
    assert getattr(rig.broker, 'is_synchronized', lambda: False)()
    assert len(rig.broker.sent_protections) == 1 and rig.broker.cancels == []

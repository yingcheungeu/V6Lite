"""B23: full original broker exceptions remain durable diagnostic evidence.

Six representative real catch paths, TEMP/FakeBroker only. No production or
same-batch test-module imports. These are not claims of dynamic coverage of
all catches: open_orders, positions_fresh and TP-timeout cancel additionally
need independent static review. Cards may use the existing summary; actual
SQLite audit and the full daily report must retain the original long cause.
"""
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.market_calendar import trading_day
from v6lite.models import IntentStatus, ProtectionRequest, Purpose, Side
from v6lite.report import daily_report
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch22_protection_lifecycle import (
    _rig as _release_rig, _open as _release_open, _manual_flat,
)

TP_PURPOSES = {Purpose.TP1, Purpose.TP2, Purpose.TP3}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import asyncio
    policy = asyncio.get_event_loop_policy()
    try: loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    from ib_insync import IB
    from ib_insync.util import getLoop
    assert getLoop() is loop
    def forbidden(*args, **kwargs):
        raise AssertionError('B23 diagnostics tests cannot connect to IB or a network')
    async def forbidden_async(*args, **kwargs): forbidden(*args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _cause(source):
    return ('Synthetic broker ' + source + ' exception: ' + '完整保留原始异常🍎 details; ' * 50
            + 'END-BROKER-EXCEPTION-' + source)


def _writes(rig):
    return tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections,
                                  rig.broker.modified_protections, rig.broker.cancels))


def _rig(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=.0015)
    rig.market_calls = []
    def no_market(*args, **kwargs):
        rig.market_calls.append((args, kwargs)); raise AssertionError('MKT forbidden')
    rig.broker.submit_market = no_market
    return rig


def _working(tmp_path):
    rig = _rig(tmp_path); rig.open_aapl(); [pos] = rig.store.open_positions()
    pid = pos['position_id']
    assert pos['qty_open'] == 3 and rig.store.protection(pid)['state'] == 'WORKING'
    _capacity(rig)
    return rig, pid


def _capacity(rig):
    # Offline Fake truth, with old maximum retained through uncertain decrease.
    held = {p.symbol: int(p.quantity) for p in rig.broker.positions_fresh()}
    by_ref = {}
    for row in rig.store.protections():
        by_ref[row['intent_id']] = (row['symbol'], max(row['confirmed_remaining'], row['pending_max_remaining']))
    for ref, order in rig.broker.orders.items():
        req = order.req
        if isinstance(req, ProtectionRequest):
            assert req.order_type == 'STP' and req.limit_price is None
        else:
            assert req.limit_price > 0 and getattr(req, 'order_type', 'LMT') == 'LMT'
            if req.side is not Side.SELL: continue
        if order.status in {'Cancelled', 'ApiCancelled', 'Inactive', 'Filled'}: continue
        previous = by_ref.get(ref, (req.symbol, 0))[1]
        pending = rig.broker.protection_pending_modifications.get(ref)
        potential = max(order.remaining, pending.quantity - order.filled if pending else 0)
        by_ref[ref] = (req.symbol, max(previous, potential))
    totals = {}
    for symbol, qty in by_ref.values(): totals[symbol] = totals.get(symbol, 0) + qty
    assert all(qty <= held.get(symbol, 0) for symbol, qty in totals.items()), (totals, held)
    assert rig.market_calls == []


def _settle(rig):
    for _ in range(2):
        rig.engine._drain(rig.clock.now()); rig.engine.process_protection_priority()
    _capacity(rig)


def _queue_tp(rig, pid, stage=Purpose.TP1):
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    level = {Purpose.TP1: 'tp1', Purpose.TP2: 'tp2', Purpose.TP3: 'tp3'}[stage]
    rig.px('AAPL', float(rig.store.position(pid)[level]) + .10)
    rig.engine._evaluate_exits(rig.clock.now())
    assert rig.store.stage(pid, stage)['status'] == 'PENDING'
    rig.engine._run_queue(); _settle(rig)


def _real_first_two_profit_fills(rig, pid):
    for stage in (Purpose.TP1, Purpose.TP2):
        _queue_tp(rig, pid, stage)
        [req] = [request for request in rig.broker.sent if request.purpose is stage]
        assert req.quantity == 1 and req.limit_price > 0
        fill = rig.broker.fill(req.intent_id, emit=False)
        rig.engine._on_urgent_broker_event(fill)
        rig.advance(seconds=1); rig.tick(); _settle(rig)
        assert rig.store.intent_filled_qty(req.intent_id) == 1
    assert rig.store.position(pid)['qty_open'] == 1
    assert rig.store.protection(pid)['state'] == 'WORKING'
    _capacity(rig)


def _assert_full_diagnostic_and_report(rig, *, kind, source, reason, pid=None, round_number=None):
    events = [json.loads(row['payload']) for row in rig.store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY id', (kind,))]
    matching = [event for event in events if event.get('source') == source and event.get('full_error') == reason]
    assert matching, 'the actual catch must persist the full original broker exception: ' + source
    assert all(event.get('error_type') == 'BrokerError' and event.get('symbol') == 'AAPL' for event in matching)
    if pid is not None: assert any(event.get('position_id') == pid for event in matching)
    if round_number is not None: assert any(event.get('evidence_round') == round_number for event in matching)
    report = daily_report(rig.store, rig.cfg, trading_day(rig.clock.now()))
    assert reason in report and 'END-BROKER-EXCEPTION-' + source in report
    return matching


@pytest.mark.parametrize('operation', ['submit_stop', 'modify_protection', 'cancel_protection'])
def test_B23_protect_write_failure_keeps_original_exception_audit_without_expanding_sell_authority(
        tmp_path, monkeypatch, operation):
    reason = _cause(operation)
    attempts = []
    if operation == 'submit_stop':
        rig = _rig(tmp_path); rig.start(); rig.at(et(10, 0, 30)); rig.tick()
        [entry] = [request for request in rig.broker.sent if request.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        rig.broker.fail_next_protection = BrokerError(reason, maybe_sent=False)
        original = rig.broker.submit_stop
        def failed_submit(request):
            attempts.append(request); return original(request)
        monkeypatch.setattr(rig.broker, 'submit_stop', failed_submit)
        writes = _writes(rig)
        fill = rig.broker.fill(entry.intent_id, emit=False)
        rig.engine._on_urgent_broker_event(fill)
        [pos] = rig.store.open_positions(); pid = pos['position_id']
        row = rig.store.protection(pid)
        assert row['state'] == 'REJECTED' and row['pending_max_remaining'] == 0
        assert row['intent_id'] not in rig.broker.orders
        assert rig.store.intent_filled_qty(entry.intent_id) == pos['qty_open'] == 3
        assert _writes(rig) == writes
    else:
        rig, pid = _working(tmp_path)
        if operation == 'modify_protection':
            rig.broker.fail_next_modify = BrokerError(reason, maybe_sent=False)
            original = rig.broker.modify_protection
            def failed_modify(request):
                attempts.append(request); return original(request)
            monkeypatch.setattr(rig.broker, 'modify_protection', failed_modify)
            writes = _writes(rig)
            _queue_tp(rig, pid)
            row = rig.store.protection(pid)
            assert row['state'] == 'UNKNOWN' and row['pending_max_remaining'] == 3
            assert row['confirmed_remaining'] == 3 and row['pending_qty'] == 2
            assert not [request for request in rig.broker.sent if request.purpose in TP_PURPOSES]
            assert _writes(rig) == writes
        else:
            _real_first_two_profit_fills(rig, pid)
            row = rig.store.protection(pid)
            def failed_cancel(ref, **kwargs):
                attempts.append((ref, kwargs)); raise BrokerError(reason, maybe_sent=False)
            monkeypatch.setattr(rig.broker, 'cancel', failed_cancel)
            writes = _writes(rig)
            _queue_tp(rig, pid, Purpose.TP3)
            row = rig.store.protection(pid)
            assert row['state'] == 'UNKNOWN' and row['pending_max_remaining'] == row['confirmed_remaining'] == 1
            assert rig.broker.orders[row['intent_id']].remaining == 1
            assert not [request for request in rig.broker.sent if request.purpose is Purpose.TP3]
            assert _writes(rig) == writes
    assert len(attempts) == 1
    _capacity(rig)
    row = rig.store.protection(pid)
    events = _assert_full_diagnostic_and_report(rig, kind='PROTECT_BROKER_ERROR',
               source=operation, reason=reason, pid=pid)
    assert any(event.get('intent_id') == row['intent_id'] and event.get('maybe_sent') is False for event in events)


def test_B23_TP_three_source_execution_query_failure_keeps_original_cause_and_zero_new_SELL(tmp_path, monkeypatch):
    rig, pid = _working(tmp_path)
    position = dict(rig.store.position(pid)); before = _writes(rig)
    reason = _cause('executions_today'); calls = []
    def unavailable():
        calls.append(True); raise BrokerError(reason)
    monkeypatch.setattr(rig.broker, 'executions_today', unavailable)
    _queue_tp(rig, pid)
    assert calls and _writes(rig) == before
    assert dict(rig.store.position(pid)) == position
    row = rig.store.protection(pid)
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 3 and row['tp_stage'] is None
    assert rig.store.stage(pid, Purpose.TP1)['attempts'] == 0
    _assert_full_diagnostic_and_report(rig, kind='BROKER_QUERY_ERROR',
        source='executions_today', reason=reason, pid=pid)


@pytest.mark.parametrize('failing_round', [1, 2])
def test_B23_release_account_execution_failure_in_either_round_keeps_full_cause_without_release_permission(
        tmp_path, monkeypatch, failing_round):
    from v6lite import __main__ as cli
    # This is the existing positive strong-identity release fixture: real
    # exact-ID cancellation, external physical flatten, execution silence and
    # complete working/position evidence. No control/identity is fabricated to
    # bypass a release gate; only this account execution request fails.
    rig = _release_rig(tmp_path)
    _, pos, row = _release_open(rig)
    _manual_flat(rig, row)
    assert rig.store.intent(row['intent_id'])['status'] == IntentStatus.CANCEL_SEEN
    assert not rig.store.intent(row['intent_id'])['hold_reason']
    assert rig.broker.positions_fresh() == [] and rig.broker.open_orders() == []
    position = dict(rig.store.position(pos['position_id']))
    frozen = rig.store.control('frozen:AAPL'); assert frozen
    writes = _writes(rig)
    reason = _cause('release_account_executions_today')
    original = rig.broker.account_executions_today
    calls = []
    def failing_account_query():
        calls.append(True)
        if len(calls) == failing_round: raise BrokerError(reason)
        return original()
    monkeypatch.setattr(rig.broker, 'account_executions_today', failing_account_query)
    monkeypatch.setattr(cli, '_now', rig.clock.now)
    cli.cmd_release(SimpleNamespace(symbol='AAPL', operator='John',
                    reason='synthetic valid identity/flat proof requires both account execution rounds'), rig.store)
    rig.engine._apply_releases(rig.clock.now())
    assert len(calls) == failing_round, 'the requested real release evidence round was not reached'
    assert dict(rig.store.position(pos['position_id'])) == position
    assert rig.store.position(pos['position_id'])['status'] == 'OPEN'
    assert rig.store.flat_point('AAPL') is None and rig.store.control('frozen:AAPL') == frozen
    assert not rig.store.conn.execute('SELECT 1 FROM releases').fetchone()
    assert rig.store.intent(row['intent_id'])['status'] == IntentStatus.CANCEL_SEEN
    assert _writes(rig) == writes and rig.market_calls == []
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='RELEASE_REFUSED'").fetchone()
    _assert_full_diagnostic_and_report(rig, kind='BROKER_QUERY_ERROR',
        source='release_account_executions_today', reason=reason, round_number=failing_round)

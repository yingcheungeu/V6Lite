"""B24 real two-round workflows and operator-resume share failed-query backoff."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.models import IntentStatus, Purpose
from v6lite.tests.helpers import Rig
from v6lite.tests.test_review_round2 import _two_open_positions
from v6lite.tests.test_batch23_contracts import (
    _activate_tp, _begin_tp, _deliver_status, _native_status, _paused,
    _request_resume, _safety, _settle, _terminal_tp, _watch_boundaries, _writes,
)
from v6lite.tests.test_batch24_stp_echo import _no_network

PHASES = ('TP_CANCEL_PENDING', 'PROTECT_CANCEL_PENDING', 'TP_NEEDS_RESTORE', 'replace_after_cancel')


def _phase_rig(tmp_path, phase):
    quantity = 1 if phase in {'PROTECT_CANCEL_PENDING', 'replace_after_cancel'} else 6
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=quantity * .0005)
    rig.market_calls = []
    def no_market(*args, **kwargs):
        rig.market_calls.append((args, kwargs))
        raise AssertionError('market write is forbidden')
    rig.broker.submit_market = no_market
    _watch_boundaries(rig)
    _two_open_positions(rig)
    positions = {row['symbol']: row for row in rig.store.open_positions()}
    pid = positions['AAPL']['position_id']
    good_pid = positions['SPY']['position_id']
    assert positions['AAPL']['qty_open'] == quantity
    rig.broker.protection_auto_status = False
    if quantity == 1:
        _begin_tp(rig, pid)
        row = rig.store.protection(pid)
        assert row['state'] == 'PROTECT_CANCEL_PENDING'
        ref = row['intent_id']
        rig.broker.orders[ref].status = 'Cancelled'
        adapter, trade = _native_status(rig, ref, raw='Cancelled', code=202,
                                       reason='Order Canceled - synthetic requested last-share cancellation')
        _deliver_status(rig, adapter, trade)
        if phase == 'replace_after_cancel':
            # An Inactive callback is only CANCEL_SEEN. A definite unsent
            # broker submission failure enters replacement without forging
            # REJECTED or letting a helper settle into the next restore phase.
            rig.broker.fail_next_submit = BrokerError('synthetic definite TP rejection', maybe_sent=False)
            rig.engine._protection_progress_one(rig.clock.now(), rig.store.protection(pid), rig.store.position(pid))
            row = rig.store.protection(pid)
            assert row['state'] == 'TP_NEEDS_RESTORE'
            assert rig.store.intent(row['tp_intent_id'])['status'] == IntentStatus.REJECTED
            assert row['tp_intent_id'] not in rig.broker.orders
    else:
        req = _activate_tp(rig, pid)
        if phase == 'TP_CANCEL_PENDING':
            rig.advance(seconds=10)
            rig.engine._hard_protection_deadlines(rig.clock.now())
            assert req.intent_id in rig.broker.cancels
            assert rig.store.protection(pid)['state'] == 'TP_CANCEL_PENDING'
        _terminal_tp(rig, req, raw='Cancelled', code=202,
                     reason='Order canceled by system: synthetic terminal TP proof')
    expected = 'TP_NEEDS_RESTORE' if phase == 'replace_after_cancel' else phase
    row = rig.store.protection(pid)
    assert row['state'] == expected
    assert rig.store.protection(good_pid)['state'] == 'WORKING'
    _safety(rig)
    return rig, pid, good_pid, expected


def _assert_retry_schedule(times):
    assert 10 <= len(times) <= 15, ('actual failed-workflow attempts in five minutes', len(times))
    gaps = [b - a for a, b in zip(times, times[1:])]
    expected = [1, 2, 4, 8, 16] + [30] * max(0, len(gaps) - 5)
    assert all(gap >= delay - 1e-6 for gap, delay in zip(gaps, expected)), (gaps, expected)


@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('fault', ['executions_failed', 'executions_partial', 'positions_unstable'])
def test_B24_second_round_failure_retains_exponential_schedule_and_other_holding_health(
        tmp_path, monkeypatch, phase, fault):
    rig, pid, good_pid, expected_state = _phase_rig(tmp_path, phase)
    initial = dict(rig.store.protection(pid))
    held = rig.store.position(pid)['qty_open']
    frozen = 'synthetic operator freeze retained during incomplete second-round proof'
    rig.store.set_control(rig.clock.now(), 'frozen:AAPL', frozen)
    refs = [ref for ref in (initial['intent_id'], initial['tp_intent_id']) if ref]
    holds = {ref: rig.store.intent(ref)['hold_reason'] for ref in refs}
    required = {row['exec_id'] for row in rig.store.conn.execute(
        'SELECT exec_id FROM fills WHERE position_id=?', (pid,))}
    assert required, 'partial evidence must omit a real booked execution'
    scope = {'pid': None, 'round': 0}
    attempts, good_proofs, first_complete, second_faults = [], [], [], []
    queries = {'positions': rig.broker.positions_fresh, 'executions': rig.broker.executions_today}
    evidence = rig.engine._protection_evidence
    calls = [0]
    def observed(row):
        target = row['position_id'] == pid
        if target:
            calls[0] += 1
            round_number = 1 + ((calls[0] - 1) % 2)
            if round_number == 1:
                attempts.append(rig.clock.now().timestamp())
        else:
            round_number = 0
            if row['position_id'] == good_pid:
                good_proofs.append(rig.clock.now().timestamp())
        previous = dict(scope)
        scope.update(pid=row['position_id'], round=round_number)
        try:
            result = evidence(row)  # real three-source query/conversion and completeness checks
            if target and round_number == 1:
                assert result[0] == held and result[3] is True
                first_complete.append(rig.clock.now().timestamp())
            return result
        finally:
            scope.update(previous)
    def positions():
        rows = queries['positions']()
        if scope['pid'] == pid and scope['round'] == 2 and fault == 'positions_unstable':
            second_faults.append(rig.clock.now().timestamp())
            return [replace(row, quantity=row.quantity + 1) if row.symbol == 'AAPL' else row for row in rows]
        return rows
    def executions():
        rows = queries['executions']()
        if scope['pid'] == pid and scope['round'] == 2 and fault.startswith('executions_'):
            second_faults.append(rig.clock.now().timestamp())
            if fault == 'executions_failed':
                raise BrokerError('synthetic actual second-round reqExecutions failure')
            return [fill for fill in rows if fill.exec_id not in required]
        return rows
    monkeypatch.setattr(rig.engine, '_protection_evidence', observed)
    monkeypatch.setattr(rig.broker, 'positions_fresh', positions)
    monkeypatch.setattr(rig.broker, 'executions_today', executions)
    heartbeats = []
    heartbeat = rig.broker.gateway_heartbeat_request
    def healthy_heartbeat():
        heartbeats.append(rig.clock.now().timestamp())
        return heartbeat()
    monkeypatch.setattr(rig.broker, 'gateway_heartbeat_request', healthy_heartbeat)
    writes = _writes(rig); boundary_checks = len(rig._b23_write_checks)
    start = rig.clock.now().timestamp()
    while rig.clock.now().timestamp() - start < 300:
        rig.engine._protection_progress(rig.clock.now(), steady_budget=True, phase='active')
        rig.engine._protection_progress(rig.clock.now(), steady_budget=True, phase='steady')
        rig.engine._gateway_heartbeat(rig.clock.now())
        row = rig.store.protection(pid)
        assert row['state'] == expected_state and row['intent_id'] == initial['intent_id']
        assert tuple(row[key] for key in ('confirmed_remaining', 'pending_max_remaining', 'pending_qty')) == tuple(
            initial[key] for key in ('confirmed_remaining', 'pending_max_remaining', 'pending_qty'))
        assert rig.store.position(pid)['qty_open'] == held
        assert rig.store.control('frozen:AAPL') == frozen
        assert {ref: rig.store.intent(ref)['hold_reason'] for ref in refs} == holds
        assert _writes(rig) == writes and len(rig._b23_write_checks) == boundary_checks
        _safety(rig)
        rig.advance(seconds=.1)
    assert len(first_complete) == len(second_faults) == len(attempts), 'each attempt must reach its real second proof'
    _assert_retry_schedule(attempts)
    assert len(good_proofs) >= 10 and max(b-a for a,b in zip(good_proofs,good_proofs[1:])) <= 32
    assert rig.store.protection(good_pid)['state'] == 'WORKING'
    assert (rig.clock.now()-__import__('datetime').datetime.fromisoformat(
        rig.store.protection(good_pid)['confirmed_at'])).total_seconds() <= 32
    assert len(heartbeats) >= 4


@pytest.mark.parametrize('fault', ['executions_failed', 'executions_partial'])
def test_B24_pending_operator_resume_cannot_bypass_failed_position_retry(tmp_path, monkeypatch, fault):
    rig, pid, req, pause = _paused(tmp_path)
    _request_resume(rig, pid, pause)
    raw_pause = rig.store.control(f'tp_pause:{pid}')
    raw_request = rig.store.control(f'resume_tp:{pid}')
    initial = dict(rig.store.protection(pid))
    assert initial['state'] == 'WORKING' and not rig.store.control('frozen:AAPL')
    assert not rig.store.intent(initial['intent_id'])['hold_reason']
    required = {row['exec_id'] for row in rig.store.conn.execute(
        'SELECT exec_id FROM fills WHERE position_id=?', (pid,))}
    attempts = []; query_trace = []
    scope = {'resume': False}
    progress_complete = []
    evidence = rig.engine._protection_evidence
    def observe_progress(row):
        result = evidence(row)
        if not scope['resume'] and row['position_id'] == pid:
            assert result[0] == 3 and result[3] is True
            progress_complete.append(rig.clock.now().timestamp())
        return result
    monkeypatch.setattr(rig.engine, '_protection_evidence', observe_progress)
    for method in ('open_orders', 'positions_fresh', 'executions_today'):
        original = getattr(rig.broker, method)
        def query(*args, _method=method, _original=original, **kwargs):
            if scope['resume']:
                query_trace.append((_method, rig.clock.now().timestamp()))
                if _method == 'open_orders':
                    attempts.append(rig.clock.now().timestamp())
            result = _original(*args, **kwargs)
            if scope['resume'] and _method == 'executions_today':
                if fault == 'executions_failed':
                    raise BrokerError('synthetic resume reqExecutions failed')
                return [fill for fill in result if fill.exec_id not in required]
            return result
        monkeypatch.setattr(rig.broker, method, query)
    writes = _writes(rig); checks = len(rig._b23_write_checks)
    start = rig.clock.now().timestamp()
    while rig.clock.now().timestamp()-start < 300:
        # Successful same-position ordinary proofs cannot clear the separate
        # pending operator-resume failure. Both paths share real retry time.
        rig.engine._protection_progress(rig.clock.now(), steady_budget=True, phase='active')
        rig.engine._protection_progress(rig.clock.now(), steady_budget=True, phase='steady')
        scope['resume'] = True
        try:
            rig.engine._tp_consume_resume(rig.clock.now())
        finally:
            scope['resume'] = False
        assert rig.store.control(f'tp_pause:{pid}') == raw_pause
        assert rig.store.control(f'resume_tp:{pid}') == raw_request
        row = rig.store.protection(pid)
        assert row['state'] == 'WORKING' and row['intent_id'] == initial['intent_id']
        assert row['confirmed_remaining'] == initial['confirmed_remaining'] == 3
        assert not rig.store.control('frozen:AAPL') and not rig.store.intent(row['intent_id'])['hold_reason']
        assert _writes(rig) == writes and len(rig._b23_write_checks) == checks
        _safety(rig)
        rig.advance(seconds=.1)
    assert len(progress_complete) >= 5, 'ordinary same-position fresh proofs must really succeed'
    _assert_retry_schedule(attempts)
    for method in ('open_orders', 'positions_fresh', 'executions_today'):
        times = [at for name,at in query_trace if name == method]
        assert len(times) == len(attempts) and all(b-a >= 1-1e-6 for a,b in zip(times,times[1:]))

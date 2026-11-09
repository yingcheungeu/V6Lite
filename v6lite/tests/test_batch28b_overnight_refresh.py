"""B28b behavioral contracts: real tick ordering and native scoped executions.

Only temporary SQLite, FakeBroker economics and the installed SDK's offline
execution request/filter/End path are used.  No test grants proof, edits the
authority cache, replaces a gate, or bypasses the steady evidence budget.
All entrypoints used below already exist on v6lite-batch28.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.exits import breakeven_stop
from v6lite.models import FillEvent, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, buy_bars, et
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _overnight_p1, _protection_config, _restart, _restore_infos,
    _safety, _tp_requests, _watch_boundaries, _writes,
)
from v6lite.tests.test_batch27b_raw_execution_time import NativeExecutionHarness


ENTRY_DAY = (2026, 10, 20)
TP_DAY = (2026, 10, 21)
NEXT_DAY = (2026, 10, 22)
OPEN = et(8, 59, 59, day=TP_DAY)  # 05:59:59 PT, before RTH.
HIT = et(11, 3, 9, day=TP_DAY)   # 08:03:09 PT.


def _audits(rig, kind):
    return [json.loads(row['payload']) for row in rig.store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY id', (kind,))]


def _tick_until(rig, predicate, *, seconds=120):
    end = rig.clock.now() + timedelta(seconds=seconds)
    while rig.clock.now() < end and not predicate():
        rig.advance(seconds=1)
        rig.tick()
        _safety(rig)
    assert predicate(), ('deadline', rig.clock.now(),
                         [dict(row) for row in rig.store.protections()])


@pytest.fixture
def overnight_run(tmp_path, monkeypatch, request):
    """A real prior-day entry/stop, shutdown, SQLite reopen and preopen startup."""
    made = []
    def make(*, entry_day=ENTRY_DAY, tp_day=TP_DAY, entry_price=342.60,
             stop=338.62, tp1=343.93, tp2=344.60, tp3=345.60, first_id=704, entry_commission=1., client_id=131, con_id=None):
        rig = Rig(tmp_path / ('b28b-' + str(len(made))),
                  start=et(9, 50, day=entry_day),
                  protection=_protection_config(True), risk_per_trade_pct=.0015, client_id=client_id)
        made.append(rig)
        original_levels = rig.store._levels_fn
        rig.store._levels_fn = lambda price, atr: replace(
            original_levels(price, atr), stop=stop, tp1=tp1,
            tp2=tp2, tp3=tp3)
        rig.broker._next_id = first_id
        if con_id is not None: rig.broker.con_ids["AAPL"] = con_id
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
        monkeypatch.setattr(rig.broker, 'account_executions_today_scoped',
                            lambda: query(harness.scoped_query), raising=False)
        # A streamed cache read must not masquerade as a new reqPositions.
        monkeypatch.setattr(rig.broker, 'positions', lambda: FakeBroker.positions_fresh(rig.broker))
        original_drain = rig.broker.drain_events
        monkeypatch.setattr(rig.broker, 'drain_events',
                            lambda: original_drain() + harness.adapter.drain_events())
        def callback_fill(ref, *, quantity, price, exec_id, commission=1.):
            raw = rig.broker.fill(ref, qty=quantity, price=price, exec_id=exec_id, emit=False, commission=commission)
            events = harness.callback(raw)
            [event] = [item for item in events if isinstance(item, FillEvent)]
            assert event.time_source == 'callback' and event.time_uncertain
            event = replace(event, commission=raw.commission)
            rig.broker.orders[ref].fills[-1] = event
            rig.broker.events.extend(event if isinstance(item, FillEvent) else item for item in events)
            rig.engine._drain(rig.clock.now())
            return event
        rig._b28b_callback_fill = callback_fill
        rig.start()
        rig.at(et(10, 0, 30, day=entry_day))
        rig.px('AAPL', entry_price)
        rig.broker.bars['AAPL'] = buy_bars(base=329.47)
        rig.tick()
        [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        entry_fill = callback_fill(entry.intent_id, quantity=3, price=entry_price,
                                   exec_id='b28b-1020-entry.01.01', commission=entry_commission)
        rig.advance(seconds=1)
        rig.tick()
        [position] = rig.store.open_positions()
        pid = position['position_id']
        row = dict(rig.store.protection(pid))
        assert row['state'] == 'WORKING' and row['broker_order_id'] == first_id + 2
        assert row['trigger_price'] == stop and position['qty_open'] == 3
        rig.engine.shutdown(grace_seconds=0)
        rig.alerter.close()
        path, levels = rig.store.path, rig.store._levels_fn
        rig.store.close()
        rig.store = Store(path, levels)
        rig.at(et(8, 59, 59, day=tp_day))
        rig._b26_alerts = []
        rig._b26_timeline = []
        rig._b26_market_calls = []
        rig._b26_entry, rig._b26_entry_fill, rig._b26_original = entry, entry_fill, row
        _watch_boundaries(rig, monkeypatch)
        _restart(rig, tmp_path, monkeypatch)
        assert len(_restore_infos(rig)) == 1
        assert rig.store.control('overnight_tp_restored:' + pid) is not None
        assert rig.store.position(pid)['qty_open'] == 3
        assert rig.broker.orders[row['intent_id']].remaining == 3
        assert not _overnight_p1(rig)
        rig.broker.protection_auto_status = False
        return rig, pid, harness
    yield make
    for rig in made:
        rig.alerter.close()
        rig.store.close()


def _observe_rounds(rig, pid, monkeypatch, *, hook=None):
    """Read requests and outcomes, without granting or modifying proof."""
    rounds, calls, checkpoints = [], [], []
    for name in ('open_orders', 'positions_fresh', 'executions_today',
                 'account_executions_today_scoped'):
        original = getattr(rig.broker, name)
        def tracked(*args, _name=name, _original=original, **kwargs):
            is_proof = rig.engine._protection_query_position == pid
            if is_proof:
                calls.append((_name, rig.clock.now()))
                checkpoints.append((_name, len(_overnight_p1(rig)),
                                    len(_audits(rig, 'OVERNIGHT_TP_FROZEN'))))
                if _name == 'open_orders':
                    rounds.append(rig.clock.now())
            result = _original(*args, **kwargs)
            if is_proof and _name == 'open_orders' and hook is not None:
                hook()
            return result
        monkeypatch.setattr(rig.broker, name, tracked)
    return rounds, calls, checkpoints


def _assert_no_false_freeze(rig):
    assert not _overnight_p1(rig), _overnight_p1(rig)
    assert not _audits(rig, 'OVERNIGHT_TP_FROZEN')


def _complete_tp1(rig, pid):
    """Drive only native acknowledgements and actual ticks through b23's flow."""
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    row = rig.store.protection(pid)
    ref = row['intent_id']
    assert row['pending_qty'] == 2 and rig.broker.orders[ref].remaining == 3
    assert not _tp_requests(rig), 'no TP before the native same-STP decrease ACK'
    assert rig.broker.protection_pending_modifications[ref].quantity == 2
    _safety(rig)
    rig.broker.confirm_protection_modify(ref)
    _safety(rig)
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 1)
    [tp] = _tp_requests(rig)
    assert tp.purpose is Purpose.TP1 and tp.quantity == 1
    assert rig.broker.orders[ref].remaining == 2
    assert rig.store.protection(pid)['state'] == 'TP_ACTIVE'
    rig._b28b_callback_fill(tp.intent_id, quantity=1,
                            price=rig.broker.quote('AAPL').bid,
                            exec_id='b28b-tp1.01.01')
    _safety(rig)
    _tick_until(rig, lambda: ref in rig.broker.protection_pending_modifications)
    pending = rig.broker.protection_pending_modifications[ref]
    assert pending.quantity == 2
    assert pending.trigger_price == pytest.approx(breakeven_stop(rig.cfg, 342.60))
    rig.broker.confirm_protection_modify(ref)
    _safety(rig)
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'WORKING' and
                rig.store.protection(pid)['trigger_price'] == pending.trigger_price)
    pos = rig.store.position(pid)
    assert pos['qty_open'] == 2 and pos['tp1_done'] == 1
    final = rig.store.protection(pid)
    assert final['confirmed_remaining'] == final['pending_max_remaining'] == 2
    assert final['broker_order_id'] == 706 and final['intent_id'] == ref
    assert len(rig.broker.sent_protections) == 1
    _assert_no_false_freeze(rig)
    return tp, ref


def test_B28b_real_preopen_startup_two_hours_steady_then_inflight_TP1_is_not_frozen(
        overnight_run, monkeypatch, record_property):
    rig, pid, harness = overnight_run()
    crossed = []
    def cross_inside_query():
        if not crossed and rig.clock.now() >= HIT:
            assert rig.engine._protection_steady_evidence_budget is True
            crossed.append(rig.clock.now())
            rig.px('AAPL', 344.05)
    rounds, calls, checkpoints = _observe_rounds(rig, pid, monkeypatch, hook=cross_inside_query)
    before = _writes(rig)
    # Real ticks preserve active/steady phase order and all _protect calls.
    # Native pacing advances the same clock; never rewind it between ticks.
    while rig.clock.now() < HIT:
        rig.advance(seconds=1)
        rig.tick()
        _safety(rig)
        if not crossed:
            assert rig.broker.quote('AAPL').last == 342.60
            assert _writes(rig) == before
    assert (rig.clock.now() - OPEN).total_seconds() >= 2 * 3600
    assert len(rounds) >= 300, 'two hours must actually exercise repeated steady evidence'
    assert len(harness.filters) > 300 and harness.ends
    _tick_until(rig, lambda: bool(crossed), seconds=45)
    assert crossed[0] >= HIT
    # The critical checkpoints occur after old proof was invalidated but
    # before this round has returned all three sources/account-visible End.
    assert all(p1 == 0 and frozen == 0 for _, p1, frozen in checkpoints), checkpoints[-12:]
    _assert_no_false_freeze(rig)
    _complete_tp1(rig, pid)
    scoped = [(at, filt) for at, filt in harness.filters if filt.time]
    assert scoped and all(filt.time == '20261021-04:00:00' for _, filt in scoped)
    record_property('b28b_steady_rounds', len(rounds))
    record_property('b28b_crossed_at', crossed[0].isoformat())
    record_property('b28b_native_scoped_requests', len(scoped))


def test_B28b_expired_cache_at_price_requests_real_new_sources_without_manual_progress(
        overnight_run, monkeypatch):
    rig, pid, harness = overnight_run()
    rounds, calls, _ = _observe_rounds(rig, pid, monkeypatch)
    native_before = len(harness.filters)
    rig.at(HIT)
    rig.px('AAPL', 344.05)
    assert (rig.clock.now() - rig.engine._protection_notice_evidence[pid]['at']).total_seconds() > 60
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    _assert_no_false_freeze(rig)
    _tick_until(rig, lambda: bool(rounds))
    assert {'open_orders', 'positions_fresh', 'executions_today',
            'account_executions_today_scoped'} <= {name for name, _ in calls}
    assert len(harness.filters) > native_before
    _complete_tp1(rig, pid)


@pytest.mark.parametrize('failure', ['P-mismatch', 'unknown-execution', 'nonworking-STP'])
def test_B28b_fresh_contradiction_at_price_freezes_without_repairing_original_STP(
        overnight_run, monkeypatch, failure):
    rig, pid, harness = overnight_run()
    ref = rig.store.protection(pid)['intent_id']
    rig.at(HIT)
    query_start = rig.clock.now()
    completed = []
    round_number = {'value': 0}
    for name in ('open_orders', 'positions_fresh', 'executions_today',
                 'account_executions_today_scoped'):
        query = getattr(rig.broker, name)
        def observe_completion(*args, _name=name, _query=query, **kwargs):
            is_proof = rig.engine._protection_query_position == pid
            if is_proof and _name == 'open_orders':
                round_number['value'] += 1
            current_round = round_number['value']
            result = _query(*args, **kwargs)
            if is_proof:
                completed.append((current_round, _name, rig.clock.now(), result))
            return result
        monkeypatch.setattr(rig.broker, name, observe_completion)
    if failure == 'P-mismatch':
        # P=4, ledger=3 keeps the pre-existing STP within capacity while
        # testing an actual complete fresh position contradiction.
        rig.broker.external_positions['AAPL'] = (1., 342.60)
    elif failure == 'unknown-execution':
        unknown = replace(rig._b26_entry_fill, exec_id='b28b-unattributed.01.01',
                          intent_id='', side=Side.BUY, quantity=1,
                          broker_order_id=9901, perm_id=9902, time=rig.clock.now())
        harness.transform = lambda rows: list(rows) + [unknown]
    else:
        # A native cancellation-in-flight is returned by fresh openOrders;
        # it cannot masquerade as a working Submitted/PreSubmitted stop.
        rig.broker.orders[ref].status = 'PendingCancel'
    original = (rig.broker.orders[ref].req, rig.broker.orders[ref].status)
    before = _writes(rig)
    rig.px('AAPL', 344.05)
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    end = rig.clock.now() + timedelta(seconds=35)
    while rig.clock.now() < end and not _overnight_p1(rig):
        rig.advance(seconds=1)
        rig.tick()
    pages = _overnight_p1(rig)
    assert pages, failure
    # A stale-cache P1 is not a valid negative result: the contradiction
    # must have arrived in a newly completed actual query round first.
    required = {'open_orders', 'positions_fresh'}
    if failure != 'P-mismatch':
        required |= {'executions_today', 'account_executions_today_scoped'}
    fresh_rounds = {number for number, _, at, _ in completed if at >= query_start}
    assert any(required <= {name for number, name, at, _ in completed
                            if number == candidate and at >= query_start}
               for candidate in fresh_rounds), (failure, completed)
    assert not any('当日新鲜三项核验已过期' in page for page in pages), pages
    if failure == 'P-mismatch':
        positions = [result for _, name, _, result in completed if name == 'positions_fresh']
        assert any(any(pos.symbol == 'AAPL' and pos.quantity == 4 for pos in result)
                   for result in positions)
        assert any(('position' in page.lower() or '持仓' in page or 'qty' in page.lower()) and
                   ('不一致' in page or 'does not match' in page.lower() or 'mismatch' in page.lower())
                   for page in pages), pages
    else:
        scoped = [result for _, name, _, result in completed
                  if name == 'account_executions_today_scoped']
        ended = {request_id for request_id, _ in harness.ends}
        assert scoped and any(
            result.scope.get('complete') is True and
            result.scope.get('request_id') in ended and
            result.scope.get('day') == '2026-10-21' and
            result.scope.get('time_filter') == '20261021-04:00:00' and
            result.scope.get('generation') == harness.adapter.query_generation
            for result in scoped), (failure, [getattr(result, 'scope', None) for result in scoped])
        if failure == 'unknown-execution':
            assert any(any(fill.exec_id == unknown.exec_id for fill in result) for result in scoped)
            assert any(('归属' in page or '未知' in page or 'unattributed' in page.lower()) and
                       ('成交' in page or 'execution' in page.lower()) for page in pages), pages
        else:
            orders = [result for _, name, _, result in completed if name == 'open_orders']
            assert any(any(order.intent_id == ref and order.status == 'PendingCancel'
                           for order in result) for result in orders)
            assert any(('STP' in page or 'GTC' in page or '保护' in page) and
                       ('工作状态' in page or '工作中' in page or 'working' in page.lower() or
                        '工作单' in page or '保护数量' in page) for page in pages), pages
    assert _audits(rig, 'OVERNIGHT_TP_FROZEN'), failure
    assert _tp_requests(rig) == [] and rig.store.protection(pid)['tp_stage'] is None
    assert _writes(rig) == before
    assert (rig.broker.orders[ref].req, rig.broker.orders[ref].status) == original
    assert rig.store.position(pid)['qty_open'] == 3
    _safety(rig)


@pytest.mark.parametrize('persistent', [False, True], ids=['temporary', 'F4-escalation'])
def test_B28b_timeout_waits_and_retries_without_false_overnight_failure_card(
        overnight_run, monkeypatch, persistent):
    rig, pid, _ = overnight_run()
    rig.at(HIT)
    rig.px('AAPL', 344.05)
    original = rig.broker.open_orders
    attempts = []
    fault = {'on': True}
    def delayed():
        if rig.engine._protection_query_position == pid:
            attempts.append(rig.clock.now())
            if fault['on']:
                raise TimeoutError('synthetic openOrders End timeout')
        return original()
    monkeypatch.setattr(rig.broker, 'open_orders', delayed)
    before = _writes(rig)
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue()
    end = rig.clock.now() + timedelta(seconds=rig.cfg.protection.evidence_fail_p0_seconds + 20
                                     if persistent else 8)
    while rig.clock.now() < end:
        rig.advance(seconds=1)
        rig.tick()
        _safety(rig)
        _assert_no_false_freeze(rig)
        assert _writes(rig) == before and _tp_requests(rig) == []
    assert attempts
    assert all((right - left).total_seconds() >= 15
               for left, right in zip(attempts, attempts[1:])), attempts
    if persistent:
        assert any(level == 'P0' and ('三项' in text or 'evidence' in text)
                   for level, text in rig._b26_alerts)
    else:
        assert not [text for level, text in rig._b26_alerts if level == 'P0']
        fault['on'] = False
        _complete_tp1(rig, pid)


def test_B28b_previous_day_dedupe_controls_survive_reopen_without_blocking_next_day_TP(
        overnight_run, tmp_path, monkeypatch):
    rig, pid, _ = overnight_run()
    rig.at(HIT)
    failure_key = 'overnight_tp_failure:' + pid
    frozen_key = 'overnight_tp_frozen_at_price:' + pid + ':2026-10-21'
    rig.store.set_control(rig.clock.now(), failure_key, 'synthetic-1021-stale-proof-fingerprint')
    rig.store.set_control(rig.clock.now(), frozen_key, rig.clock.now().isoformat())
    rig.store.audit(rig.clock.now(), 'OVERNIGHT_TP_FROZEN', position_id=pid,
                    symbol='AAPL', stage='TP1', day='2026-10-21',
                    reason='synthetic reproduction of 10/21 stale-proof false freeze')
    rig.engine.shutdown(grace_seconds=0)
    rig.at(et(8, 59, 59, day=NEXT_DAY))
    _restart(rig, tmp_path, monkeypatch, reopen=True)
    assert rig.store.control(failure_key) == 'synthetic-1021-stale-proof-fingerprint'
    assert rig.store.control(frozen_key) is not None
    rig.at(et(11, 3, 9, day=NEXT_DAY))
    rig.px('AAPL', 344.05)
    prior_frozen = list(_audits(rig, 'OVERNIGHT_TP_FROZEN'))
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue()
    # The historical frozen audit is retained; no new 10/22 frozen fact may
    # be written merely because this process's startup proof is old.
    assert not _overnight_p1(rig)
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    assert _audits(rig, 'OVERNIGHT_TP_FROZEN') == prior_frozen
    assert rig.store.control('overnight_tp_frozen_at_price:' + pid + ':2026-10-22') is None
    # _complete_tp1's no-false-freeze assertion concerns the new day only.
    old_audits = _audits(rig, 'OVERNIGHT_TP_FROZEN')
    row = rig.store.protection(pid)
    ref = row['intent_id']
    assert row['pending_qty'] == 2 and _tp_requests(rig) == []
    rig.broker.confirm_protection_modify(ref)
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 1)
    [tp] = _tp_requests(rig)
    rig._b28b_callback_fill(tp.intent_id, quantity=1,
                            price=rig.broker.quote('AAPL').bid,
                            exec_id='b28b-1022-tp1.01.01')
    _tick_until(rig, lambda: ref in rig.broker.protection_pending_modifications)
    rig.broker.confirm_protection_modify(ref)
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'WORKING' and
                rig.store.protection(pid)['trigger_price'] == breakeven_stop(rig.cfg, 342.60))
    assert rig.store.position(pid)['qty_open'] == 2
    assert _audits(rig, 'OVERNIGHT_TP_FROZEN') == old_audits
    assert not _overnight_p1(rig)
    _safety(rig)

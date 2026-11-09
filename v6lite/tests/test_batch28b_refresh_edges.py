"""Independent B28b review edges; original eight frozen specs remain untouched.

The observers always execute the original broker/engine methods.  Synthetic
faults affect native results or native completion/reset callbacks, never the
proof cache or a safety gate.  No sockets or persistent operator state.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from v6lite.models import ExecutionQueryResult
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _overnight_p1, _safety, _tp_requests, _writes,
)
from v6lite.tests.test_batch28b_overnight_refresh import (
    HIT, overnight_run, _audits, _assert_no_false_freeze, _complete_tp1,
)


SOURCES = ('open_orders', 'positions_fresh', 'executions_today',
           'account_executions_today_scoped')


def _at_price(rig):
    rig.at(HIT)
    rig.px('AAPL', 344.05)


def _decision_and_queue(rig):
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())


def _observe_starts(rig, pid, monkeypatch):
    starts = []
    original = rig.broker.open_orders
    def observed():
        if rig.engine._protection_query_position == pid:
            starts.append((rig.clock.now(), rig.broker.query_generation))
        return original()
    monkeypatch.setattr(rig.broker, 'open_orders', observed)
    return starts


def test_B28b_review_queued_stale_refresh_services_protection_before_every_source(
        overnight_run, monkeypatch):
    rig, pid, _ = overnight_run()
    _at_price(rig)
    timeline = []
    protect = rig.engine._protect
    service = rig.engine._service_protection
    def protect_observer(kind=None, **kwargs):
        if rig.engine._protection_query_position == pid:
            timeline.append(('protect', kind))
        return protect(kind, **kwargs)
    def service_observer(now):
        if rig.engine._protection_query_position == pid:
            timeline.append(('service', now))
        return service(now)
    monkeypatch.setattr(rig.engine, '_protect', protect_observer)
    monkeypatch.setattr(rig.engine, '_service_protection', service_observer)
    checkpoints = []
    for name in SOURCES:
        original = getattr(rig.broker, name)
        def source(*args, _name=name, _original=original, **kwargs):
            if rig.engine._protection_query_position == pid:
                checkpoints.append((_name, list(timeline)))
                timeline.append(('source', _name))
            return _original(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, source)
    rig.engine._evaluate_exits(rig.clock.now())
    assert checkpoints == [], 'the fast decision pass must only queue broker work'
    rig.engine._run_queue()
    assert [name for name, _ in checkpoints[:4]] == list(SOURCES)
    previous = 0
    for name, before in checkpoints[:4]:
        since_prior_source = before[previous:]
        assert ('protect', 'query') in since_prior_source, (name, timeline)
        assert any(kind == 'service' for kind, _ in since_prior_source), (name, timeline)
        # Include the source marker so the next stage needs a new budget pass.
        previous = len(before) + 1
    _assert_no_false_freeze(rig)
    _safety(rig)


def test_B28b_review_incomplete_scope_denial_does_not_repeat_inside_fifteen_seconds(
        overnight_run, monkeypatch):
    rig, pid, harness = overnight_run()
    _at_price(rig)
    starts = _observe_starts(rig, pid, monkeypatch)
    native_scope = harness.scoped_query
    results = []
    def incomplete_after_actual_end():
        result = native_scope()
        assert result.scope['complete'] is True
        assert result.scope['request_id'] in {request_id for request_id, _ in harness.ends}
        # Only this completed response's declared completeness is damaged.
        damaged = ExecutionQueryResult(result, {**result.scope, 'complete': False})
        results.append(damaged)
        return damaged
    monkeypatch.setattr(harness, 'scoped_query', incomplete_after_actual_end)
    before = _writes(rig)
    _decision_and_queue(rig)
    assert len(starts) == 1 and len(results) == 1
    completed_at = rig.clock.now()
    assert _overnight_p1(rig) and _audits(rig, 'OVERNIGHT_TP_FROZEN')
    assert _writes(rig) == before and _tp_requests(rig) == []
    rig.tick()
    assert len(starts) == 1 and len(results) == 1, 'no second same-tick denial query'
    rig.at(completed_at + timedelta(seconds=14, microseconds=999000))
    rig.tick()
    # Real tick work may itself spend time inside a paced native request.
    # Judge the actual request timestamps, not the tick's earlier entry time.
    assert all((at - starts[0][0]).total_seconds() >= 15 for at, _ in starts[1:])
    rig.at(max(rig.clock.now(), completed_at + timedelta(seconds=15)))
    rig.tick()
    assert len(starts) == 2 and len(results) == 2, 'normal next steady round must remain eligible'
    assert (starts[1][0] - starts[0][0]).total_seconds() >= 15
    assert _writes(rig) == before and _tp_requests(rig) == []
    assert rig.store.protection(pid)['confirmed_remaining'] == 3
    _safety(rig)


@pytest.mark.parametrize('persistent', [False, True], ids=['temporary', 'persistent-then-recovery'])
def test_B28b_review_scoped_native_End_timeout_is_pending_then_can_recover(
        overnight_run, monkeypatch, persistent):
    rig, pid, harness = overnight_run()
    _at_price(rig)
    starts = _observe_starts(rig, pid, monkeypatch)
    execute = harness.ib.reqExecutions
    fault = {'on': True}
    timed_out_ids = []
    def missing_scoped_end(execution_filter=None):
        if execution_filter is not None and execution_filter.time and fault['on']:
            prior_mode = harness.mode
            harness.mode = 'missing-End'
            try:
                execute(execution_filter)
                # The offline server really transmitted this scoped request
                # and withheld its native End; model the subsequent timeout.
                request = [packet.split('\0') for packet in harness.wire
                           if packet.split('\0', 1)[0] == '7'][-1]
                timed_out_ids.append(int(request[2]))
                raise TimeoutError('synthetic account_visible_utc execDetailsEnd timeout')
            finally:
                harness.mode = prior_mode
        return execute(execution_filter)
    monkeypatch.setattr(harness.ib, 'reqExecutions', missing_scoped_end)
    before = _writes(rig)
    _decision_and_queue(rig)
    assert timed_out_ids
    deadline = rig.clock.now() + timedelta(
        seconds=rig.cfg.protection.evidence_fail_p0_seconds + 20 if persistent else 8)
    while rig.clock.now() < deadline:
        rig.advance(seconds=1)
        rig.tick()
        _assert_no_false_freeze(rig)
        assert _tp_requests(rig) == [] and _writes(rig) == before
        _safety(rig)
    ended = {request_id for request_id, _ in harness.ends}
    assert not ended.intersection(timed_out_ids)
    assert all((right[0] - left[0]).total_seconds() >= 15
               for left, right in zip(starts, starts[1:])), starts
    pages = [text for level, text in rig._b26_alerts if level == 'P0']
    if persistent:
        assert any('evidence' in text.lower() or '三项' in text for text in pages)
    else:
        assert pages == []
    fault['on'] = False
    _complete_tp1(rig, pid)
    assert any(filt.time for _, filt in harness.filters)
    assert rig.store.control('protect_evidence_failure:' + pid) is None


def test_B28b_review_native_End_callback_generation_reset_requires_a_new_proof_round(
        overnight_run, monkeypatch):
    rig, pid, harness = overnight_run()
    _at_price(rig)
    starts = _observe_starts(rig, pid, monkeypatch)
    end = harness.wrapper.execDetailsEnd
    resets = []
    def end_then_native_reset(request_id):
        result = end(request_id)
        if not resets and harness.filters and harness.filters[-1][1].time:
            old = harness.adapter.query_generation
            harness.adapter._reset_channels()
            resets.append((request_id, old, harness.adapter.query_generation))
        return result
    monkeypatch.setattr(harness.wrapper, 'execDetailsEnd', end_then_native_reset)
    before = _writes(rig)
    _decision_and_queue(rig)
    assert len(resets) == 1 and len(starts) == 1
    request_id, old_generation, new_generation = resets[0]
    assert new_generation != old_generation
    assert request_id in {value for value, _ in harness.ends}
    assert rig.broker.query_generation == new_generation
    assert rig.store.protection(pid)['tp_stage'] is None
    assert _writes(rig) == before and _tp_requests(rig) == []
    _assert_no_false_freeze(rig)
    # A native response from the obsolete generation did not authorize any
    # economic action. Only a subsequent real same-generation full round may.
    _complete_tp1(rig, pid)
    assert len(starts) >= 2 and starts[0][1] == old_generation
    assert any(generation == new_generation for _, generation in starts[1:])
    assert len(resets) == 1

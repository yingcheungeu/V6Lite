"""27a independent review guards; no production authority is mocked.

These cases include candidate-regression controls. A static-review wording
repair and closed-fill negative controls need not be natural-red on B26.
Only the repeated-WORKING contradictory-payload case is a new shortcut guard.
All prices, orderRefs and unspecified native identifiers are synthetic.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.config import ProtectionConfig
from v6lite.models import OrderRequest, Purpose, Side, StatusEvent
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _protection_config, _restart, _safety, _tp_requests,
    _watch_boundaries, _writes,
)
from v6lite.tests.test_batch27a_execution_time import callback_rig, replay_history, seed_overnight


@pytest.fixture
def working_rig(tmp_path, monkeypatch):
    made = []
    def make(*, protection=True, pending_ack=False):
        rig = Rig(tmp_path / ('review-' + str(len(made))),
                  start=et(9, 50, day=(2026, 10, 16)),
                  protection=ProtectionConfig(enabled=protection),
                  risk_per_trade_pct=.0015, reconcile_interval_seconds=3600)
        made.append(rig)
        rig.broker.protection_auto_status = not pending_ack
        rig.start(); rig.at(et(10, 0, 30, day=(2026, 10, 16))); rig.tick()
        [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        rig.broker.fill(entry.intent_id, qty=3, price=113.13)
        rig.engine._drain(rig.clock.now())
        if not pending_ack:
            rig.advance(seconds=1); rig.tick()
        [pos] = rig.store.open_positions()
        return rig, pos['position_id']
    yield make
    for rig in made:
        rig.alerter.close(); rig.store.close()


@pytest.mark.parametrize('state', ['disabled', 'UNKNOWN', 'native-GTC-missing'])
def test_B27a_review_prior_close_card_never_promises_a_stop_that_was_not_independently_verified(
        working_rig, state):
    rig, pid = working_rig(protection=state != 'disabled')
    if state == 'UNKNOWN':
        rig.store.update_protection(rig.clock.now(), pid, state='UNKNOWN')
    elif state == 'native-GTC-missing':
        ref = rig.store.protection(pid)['intent_id']
        rig.broker.orders[ref].status = 'Cancelled'
    rig.at(et(7, 21, day=(2026, 10, 19)))
    rig.broker.prev_close['AAPL'] = None
    # Real day-risk calculation creates the actual pending-baseline entry
    # block. The renderer receives the real status, not invented authority.
    rig.engine._daily_risk(rig.clock.now(), fetch=True)
    assert rig.engine.flags.day_risk_error == 'prior_close:AAPL'
    status = rig.engine.status_snapshot(rig.clock.now())
    assert any('daily_loss_baseline_unknown:prior_close:AAPL' == value
               for value in status['entry_blocks'])
    card = rig.engine._startup_card(rig.clock.now(), status)
    visible = card.as_plain_text()
    assert '原 STP 继续有效' not in visible and '原STP继续有效' not in visible, visible
    assert '保护状态仍须独立核验' in visible or '保护状态需独立核验' in visible, visible
    assert '5 分钟' in visible and '重试' in visible
    assert rig.store.position(pid)['qty_open'] == 3


@pytest.mark.parametrize('known_danger', ['short-position', 'extra-working-SELL'])
def test_B27a_review_closed_own_fill_exception_cannot_hide_already_visible_short_or_other_SELL(
        replay_history, tmp_path, monkeypatch, known_danger):
    rig, pid = replay_history(callback=True)
    rig.at(et(14, 55, 44, day=(2026, 10, 16)) + timedelta(microseconds=153000))
    _restart(rig, tmp_path, monkeypatch)
    ref = rig.store.protection(pid)['intent_id']
    if known_danger == 'short-position':
        # External actor has already violated broker P before the query.
        # The real positions response is negative, so the production evidence
        # validator itself must set the emergency flag; we never set that flag.
        rig.broker.external_positions['AAPL'] = (-4.0, 113.13)
    else:
        external = OrderRequest('manual-review-SELL', 'AAPL', Side.SELL, 1,
                                113.13, Purpose.STOP)
        # TWS actor, not a Lite submit call. S+X>P is an unsafe *external*
        # preparation state. No test claims it satisfies Lite's capacity rule.
        rig.broker._register(external)
    original_query = rig.broker.executions_today
    calls = []
    def executions_with_close():
        calls.append(rig.clock.now())
        rig.clock.advance(microseconds=513000)
        raw = rig.broker.fill(ref, qty=3, price=113.0, commission=None,
                              exec_id='b27a-known-danger-stop.01.01', emit=False)
        callback = replace(raw, time_source='callback', time_uncertain=True)
        rig.broker.orders[ref].fills[-1] = replace(raw, time_source='reqExecutions')
        rig.engine._on_urgent_broker_event(callback)
        assert rig.store.position(pid)['status'] == 'CLOSED'
        rig.clock.advance(microseconds=274000)
        return original_query()
    monkeypatch.setattr(rig.broker, 'executions_today', executions_with_close)
    before = _writes(rig)
    rig._b26_alerts.clear()
    rig.engine._protection_progress(rig.clock.now())
    assert len(calls) == 1
    assert rig.store.position(pid)['status'] == 'CLOSED' and rig.store.position(pid)['qty_open'] == 0
    assert rig.store.intent(ref)['status'] == 'FILLED'
    assert any(level == 'P0' for level, _ in rig._b26_alerts), (
        'known danger may not be swallowed by the otherwise valid own-STOP close exception', rig._b26_alerts)
    assert _writes(rig) == before and _tp_requests(rig) == []
    if known_danger == 'short-position':
        assert rig.engine._protection_emergency is True
        assert FakeBroker.positions_fresh(rig.broker)[0].quantity < 0
    else:
        assert any(order.intent_id == external.intent_id and order.remaining == 1
                   for order in rig.broker.open_orders())


@pytest.mark.parametrize('filled,remaining', [(1, 2), (0, 0), (.5, 2.5), (float('nan'), 3)],
                         ids=['unbooked-fill', 'different-remaining', 'fractional', 'NaN'])
def test_B27a_review_repeated_WORKING_does_not_skip_urgent_when_payload_disagrees_with_identity(
        working_rig, filled, remaining):
    rig, pid = working_rig()
    row = rig.store.protection(pid); ref = row['intent_id']
    rig.engine._protection_progress(rig.clock.now())
    assert rig.engine._protection_priority_due is False
    [identity] = [order for order in rig.broker.open_orders() if order.intent_id == ref]
    assert identity.status == 'Submitted' and identity.filled == 0 and identity.remaining == 3
    before = _writes(rig)
    event = StatusEvent(ref, 'Submitted', filled, remaining,
                        row['broker_order_id'], row['perm_id'],
                        order_identity=identity, identity_checked=True)
    rig.engine._on_urgent_broker_event(event)
    current, intent = rig.store.protection(pid), rig.store.intent(ref)
    assert (rig.engine._protection_priority_due or current['state'] == 'UNKNOWN' or intent['hold_reason']), (
        'contradictory quantities are not a consistent repeated-WORKING observation', filled, remaining)
    assert rig.store.position(pid)['qty_open'] == 3 and rig.store.intent_filled_qty(ref) == 0
    assert _writes(rig) == before


def test_B27a_review_true_repeated_WORKING_preserves_fifteen_second_steady_queries(working_rig, monkeypatch):
    rig, pid = working_rig()
    row = rig.store.protection(pid); ref = row['intent_id']
    monkeypatch.setattr(rig.broker, 'positions', lambda: FakeBroker.positions_fresh(rig.broker))
    rig.tick()
    calls = []
    original = rig.broker.open_orders
    def open_orders():
        calls.append(rig.clock.now()); return original()
    monkeypatch.setattr(rig.broker, 'open_orders', open_orders)
    # The seed has already advanced one second after its real confirmation.
    # Anchor cadence to that persisted confirmation, never rewrite it merely
    # to align the test's count-start with the engine's evidence clock.
    from datetime import datetime
    confirmed = datetime.fromisoformat(rig.store.protection(pid)['confirmed_at'])
    for _ in range(31):
        rig.advance(seconds=1)
        # A full strong tuple agrees with every status payload quantity.
        [identity] = [order for order in FakeBroker.open_orders(rig.broker) if order.intent_id == ref]
        event = StatusEvent(ref, identity.status, identity.filled, identity.remaining,
                            identity.broker_order_id, identity.perm_id,
                            order_identity=identity, identity_checked=True)
        rig.engine._on_urgent_broker_event(event)
        assert rig.engine._protection_priority_due is False
        rig.tick()
    assert [(at-confirmed).total_seconds() for at in calls] == [15, 30], calls
    assert (calls[1]-calls[0]).total_seconds() == 15
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.broker.orders[ref].remaining == 3


def test_B27a_review_first_A_ack_is_urgent_not_a_suppressed_repeated_status(working_rig):
    rig, pid = working_rig(pending_ack=True)
    row = rig.store.protection(pid); ref = row['intent_id']
    assert row['gap_a_started_at'] is not None and row['confirmed_remaining'] == 0
    rig.broker.orders[ref].status = 'Submitted'
    [identity] = [order for order in FakeBroker.open_orders(rig.broker) if order.intent_id == ref]
    rig.engine._on_urgent_broker_event(StatusEvent(
        ref, 'Submitted', 0, 3, identity.broker_order_id, identity.perm_id,
        order_identity=identity, identity_checked=True))
    assert rig.engine._protection_priority_due is True
    rig.engine.process_protection_priority()
    current = rig.store.protection(pid)
    assert current['state'] == 'WORKING' and current['confirmed_remaining'] == 3
    assert current['gap_a_started_at'] is None


def test_B27a_review_reduce_ack_is_urgent_and_TP_waits_for_native_ack(working_rig, monkeypatch):
    rig, pid = working_rig()
    rig._b26_market_calls = []; rig._b26_timeline = []
    _watch_boundaries(rig, monkeypatch)
    row = rig.store.protection(pid); ref = row['intent_id']
    rig.broker.protection_auto_status = False
    rig.advance(seconds=31)
    rig.px('AAPL', float(rig.store.position(pid)['tp1']) + .20)
    rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue(); rig.engine._drain(rig.clock.now())
    pending = rig.store.protection(pid)
    assert pending['state'] == 'REDUCE_PENDING' and pending['pending_qty'] == 2
    assert _tp_requests(rig) == [] and rig.broker.orders[ref].remaining == 3
    _safety(rig)
    rig.broker.confirm_protection_modify(ref)
    native_events = rig.broker.drain_events()
    assert len(native_events) == 1 and isinstance(native_events[0], StatusEvent)
    [identity] = [order for order in FakeBroker.open_orders(rig.broker) if order.intent_id == ref]
    rig.engine._on_urgent_broker_event(replace(native_events[0], order_identity=identity,
                                              identity_checked=True))
    assert rig.engine._protection_priority_due is True
    assert rig.store.protection(pid)['gap_b_started_at'] is not None
    rig.engine.process_protection_priority()
    [tp] = _tp_requests(rig)
    assert tp.quantity == 1 and rig.broker.orders[ref].remaining == 2
    _safety(rig)



def _pending_resume_evidence_episode(rig, pid):
    import json
    from v6lite.tests.test_batch27a_lifecycle import _old_evidence_episode
    key = _old_evidence_episode(rig, pid)
    row = rig.store.protection(pid)
    # Real failure bookkeeping records the independent unresolved resume
    # workflow; no fresh proof or authorization flag is mocked successful.
    rig.engine._protection_verification_failure(rig.clock.now(), row, workflow='resume', notify=False)
    assert 'resume' in rig.engine._protection_verification_retry[pid]['pending_workflows']
    f4 = 'protect_evidence_failure:' + pid
    rig.store.set_control(rig.clock.now(), f4, json.dumps({
        'since': et(9, 20, 54, day=(2026, 10, 14)).isoformat(), 'paged': True}))
    return key, f4


def test_B27a_review_confirmed_own_STOP_close_ends_evidence_episode_even_with_pending_resume(working_rig):
    rig, pid = working_rig()
    row = rig.store.protection(pid); ref = row['intent_id']
    key, f4 = _pending_resume_evidence_episode(rig, pid)
    rig.broker.fill(ref, qty=3, price=113.0)
    rig.engine._drain(rig.clock.now())
    assert rig.store.position(pid)['status'] == 'CLOSED' and rig.store.position(pid)['qty_open'] == 0
    current = rig.store.protection(pid)
    assert current['state'] == 'FILLED' and current['confirmed_remaining'] == current['pending_max_remaining'] == 0
    assert rig.store.intent(ref)['status'] == 'FILLED' and rig.store.intent_filled_qty(ref) == 3
    assert FakeBroker.open_orders(rig.broker) == []
    assert rig.store.control(key) is None, 'resume bookkeeping cannot keep an already-flat evidence incident alive'
    assert rig.store.control(f4) is None and pid not in rig.engine._protection_verification_retry
    assert _tp_requests(rig) == []


def test_B27a_review_healthy_OPEN_stop_cannot_clear_an_unfinished_resume_proof(working_rig):
    rig, pid = working_rig()
    key, f4 = _pending_resume_evidence_episode(rig, pid)
    rig.advance(seconds=2)
    rig.engine._protection_progress(rig.clock.now())
    assert rig.store.position(pid)['status'] == 'OPEN' and rig.store.position(pid)['qty_open'] == 3
    assert 'resume' in rig.engine._protection_verification_retry[pid]['pending_workflows']
    assert rig.store.control(key) is not None and rig.store.control(f4) is not None
    assert _tp_requests(rig) == []


@pytest.mark.parametrize('identity_index', [4, 6, 7, 8], ids=['quantity-float', 'orderId-float', 'permId-float', 'conId-float'])
def test_B27a_review_same_day_time_verifier_rejects_equal_valued_float_identity_fields(
        callback_rig, identity_index):
    import json
    from v6lite.tests.test_batch27a_execution_time import _audits, _fill_row, _time_key
    rig, callback, _ = callback_rig()
    marker = json.loads(rig.store.control(_time_key(callback.exec_id)))
    original = marker['identity'][identity_index]
    assert type(original) is int
    marker['identity'][identity_index] = float(original)
    value = json.dumps(marker)
    rig.store.set_control(rig.clock.now(), _time_key(callback.exec_id), value)
    before = _fill_row(rig, callback.exec_id)
    # Real complete-protection-query duplicate path, never a direct invocation
    # of the private time verifier or replacement of broker authority.
    rig.engine._protection_evidence(rig.store.protection(before['position_id']))
    assert rig.store.control(_time_key(callback.exec_id)) == value
    assert _fill_row(rig, callback.exec_id) == before
    assert not [item for item in _audits(rig, 'EXECUTION_TIME_VERIFIED') if item['exec_id'] == callback.exec_id]
    assert any(item['exec_id'] == callback.exec_id for item in _audits(rig, 'EXECUTION_TIME_REVIEW_REQUIRED'))

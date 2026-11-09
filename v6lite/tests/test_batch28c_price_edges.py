"""28c independent price-confirmation edges; isolated SQLite/native SDK only.

The first spec stays byte-frozen. Only token-corruption cases deliberately
change their one persisted token after a real TP1 and authorized BE request.
No proof, status_seen_generation, order identity or safety gate is replaced.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import pytest

from v6lite.models import Purpose
from v6lite.tests.helpers import et
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _restart, _safety, _tp_requests, _writes,
)
from v6lite.tests.test_batch28b_overnight_refresh import NEXT_DAY, overnight_run, _tick_until
from v6lite.tests.test_batch28c_price_confirmation import (
    price_run, _be_pending, _confirmed, _tp1_inflight,
)


def _price_pages(rig):
    return [text for level, text in rig._b26_alerts if level == 'P1' and
            any(fragment in text for fragment in ('保本', '价格', '待确认', '修改', 'pending', 'binding'))]


def test_B28c_restart_complete_round_with_old_confirmed_price_waits_without_STP_write(
        price_run, tmp_path, monkeypatch):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    ref = pending['intent_id']
    original = rig.broker.orders[ref].req
    assert original.trigger_price == pending['trigger_price'] == 338.62
    assert original.trigger_price != pending['pending_trigger']
    # The original process sent BE but the server never applied it. On a new
    # process the durable request still supports a bounded pending modification.
    rig.broker.protection_pending_modifications.pop(ref)
    rig.engine.shutdown(grace_seconds=0)
    rig.at(et(9, 31, day=NEXT_DAY))
    rig._b26_alerts.clear()
    before, sources = _writes(rig), len(wire.sources)
    _restart(rig, tmp_path, monkeypatch, reopen=True)
    _tick_until(rig, lambda: bool(_price_pages(rig)))
    assert rig.store.control('frozen:AAPL') is None
    assert not [t for l,t in rig._b26_alerts if l == 'P0']
    row = rig.store.protection(pid)
    assert row['state'] != 'WORKING' and row['pending_qty'] == 2
    assert row['trigger_price'] == 338.62 and row['pending_trigger'] == pending['pending_trigger']
    fresh = wire.sources[sources:]
    assert {'open_orders', 'positions_fresh', 'executions_today',
            'account_executions_today_scoped'} <= {name for name, _, _, proof in fresh if proof}
    assert any(any(order.intent_id == ref and order.aux_price == 338.62 and order.remaining == 2
                   for order in result) for name, _, result, proof in fresh
               if proof and name == 'open_orders')
    assert _writes(rig) == before and rig.broker.orders[ref].req == original
    rig.px('AAPL', float(rig.store.position(pid)['tp2']) + .20)
    rig.advance(seconds=31); rig.engine._evaluate_exits(rig.clock.now())
    assert len(_tp_requests(rig)) == 1 and _writes(rig) == before
    assert not [stage for stage in rig.store.pending_stages(pid) if stage['stage'] == str(Purpose.TP2)]
    _safety(rig)


def test_B28c_native_pre_send_openOrder_at_exact_request_time_cannot_confirm_BE(
        price_run, monkeypatch):
    rig, pid, wire = price_run()
    tp = _tp1_inflight(rig, pid, wire)
    physical_modify = rig.broker.modify_protection
    captured = []
    def callback_before_send(request):
        row = rig.store.protection(pid)
        if row['state'] == 'PRICE_PENDING':
            # This callback predates the physical BE request. Its fields may
            # coincidentally match, and its receive clock equals the pending
            # write's clock exactly. Equality must remain conservative.
            at = rig.clock.now()
            assert datetime.fromisoformat(row['updated_at']) == at
            current = rig.broker._open_order_snapshot(row['intent_id'],
                        rig.broker.orders[row['intent_id']])
            events = wire.queued(replace(current, aux_price=request.trigger_price))
            for event in events:
                observed_at = getattr(event, 'observed_at', None)
                if observed_at is not None:
                    assert observed_at == at
                rig.engine._handle_event(at, event)
            assert rig.store.protection(pid)['state'] == 'PRICE_PENDING', (
                'an equal-time pre-send event cannot acknowledge the request that has not reached the server')
            assert rig.store.protection(pid)['trigger_price'] == row['trigger_price']
            captured.append(at)
        return physical_modify(request)
    monkeypatch.setattr(rig.broker, 'modify_protection', callback_before_send)
    pending = _be_pending(rig, pid, wire, tp=tp)
    assert len(captured) == 1
    before = _writes(rig)
    # A genuinely later callback is still accepted, with no new broker write.
    wire.ack(only_open_order=True)
    _confirmed(rig, pid, pending)
    assert _writes(rig) == before and len(_tp_requests(rig)) == 1


@pytest.mark.parametrize('corruption', [
    'missing-token', 'malformed-json', 'empty-binding',
    'missing-modify-generation', 'wrong-history-digest',
])
def test_B28c_missing_or_corrupt_BE_token_cannot_be_promoted_by_live_or_complete_query(
        price_run, corruption):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    key = 'overnight_be_flow:' + pid
    raw = rig.store.control(key)
    assert raw is not None
    token = json.loads(raw)
    if corruption == 'missing-token':
        rig.store.delete_control(key)
    elif corruption == 'malformed-json':
        rig.store.set_control(rig.clock.now(), key, '{broken-synthetic-json')
    else:
        if corruption == 'empty-binding':
            token['binding'] = {}
        elif corruption == 'missing-modify-generation':
            del token['binding']['modify_generation']
        else:
            token['historical_digest'] = '0' * 64
        rig.store.set_control(rig.clock.now(), key, json.dumps(token, sort_keys=True))
    corrupted = rig.store.control(key)
    before = _writes(rig)
    wire.ack(only_open_order=True)
    assert rig.store.protection(pid)['state'] != 'WORKING'
    assert rig.store.protection(pid)['trigger_price'] == pending['trigger_price']
    assert rig.store.protection(pid)['pending_trigger'] == pending['pending_trigger']
    # The correct current STP cannot turn a damaged authorization token into
    # a new request fact, including after a complete native own-End round.
    sources = len(wire.sources)
    deadline = rig.clock.now()
    while (rig.clock.now() - deadline).total_seconds() < 45:
        rig.advance(seconds=1); rig.tick(); _safety(rig)
        completed = {name for name, _, _, proof in wire.sources[sources:] if proof}
        if {'open_orders', 'positions_fresh', 'executions_today',
                'account_executions_today_scoped'} <= completed:
            break
    else:
        pytest.fail('the negative contract must observe an actual complete fresh three-source round')
    row = rig.store.protection(pid)
    assert row['state'] != 'WORKING' and row['pending_qty'] is not None
    assert row['trigger_price'] == pending['trigger_price']
    assert rig.store.control(key) == corrupted, 'confirmation must not repair or replace a corrupt/missing token'
    assert _writes(rig) == before and len(_tp_requests(rig)) == 1
    assert rig.broker.orders[pending['intent_id']].req.trigger_price == pending['pending_trigger']
    _safety(rig)

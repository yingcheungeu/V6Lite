"""Unconfirmed auxPrice remains uncertain through UNKNOWN and retirement.

Independent Batch22 regression file: TEMP state, actual FakeBroker fills,
existing B20 constructors/methods, and no invocation of the real CLI/broker.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.models import IntentStatus, StatusEvent
from v6lite.tests.helpers import Rig


def _open(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.open_aapl()
    [position] = rig.store.open_positions()
    assert position['qty_open'] == 20
    row = dict(rig.store.protection(position['position_id']))
    assert row['state'] == 'WORKING'
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 20
    [native] = [order for order in rig.broker.open_orders() if order.intent_id == row['intent_id']]
    return rig, dict(position), row, native


def _writes(rig):
    return tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                        rig.broker.modified_protections, rig.broker.cancels))


def _request_price(rig, row, native, new_stop):
    return rig.engine._protection_modify(rig.clock.now(), row, native, 20,
        increase=False, state='PRICE_PENDING', trigger=new_stop)


def _settle(rig):
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()


def _unknown_audit(rig, fill, old_stop, new_stop, *, retired=False):
    rows = [json.loads(row['payload']) for row in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='PROTECT_FILL' ORDER BY id")]
    rows = [row for row in rows if row.get('exec_id') == fill.exec_id]
    assert len(rows) == 1, rows
    row = rows[0]
    for field in ('stop_price', 'slippage_usd', 'slippage_bps', 'bps'):
        assert field in row and row[field] is None, row
    assert row.get('slippage_status') in {'unknown', 'candidates'}, row
    assert set(row.get('stop_price_candidates', [])) == {old_stop, new_stop}, row
    assert row.get('fill_price') == pytest.approx(fill.price)
    assert row.get('avg_fill_price') == pytest.approx(fill.price)
    assert row.get('quantity') == fill.quantity
    if retired:
        assert row.get('retired') is True
        compatible = [json.loads(item['payload']) for item in rig.store.conn.execute(
            "SELECT payload FROM audit WHERE kind='PROTECT_RETIRED_FILL'")]
        assert len([item for item in compatible if item.get('exec_id') == fill.exec_id]) == 1
    return row


def _assert_replay_keeps_audit_and_accounting(rig, fill, payload, position_id):
    before = dict(rig.store.position(position_id))
    count = rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
    writes = _writes(rig)
    for _ in range(3):
        rig.engine._ingest_fill(rig.clock.now(), fill)
    rows = [json.loads(row['payload']) for row in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='PROTECT_FILL'")]
    assert [row for row in rows if row.get('exec_id') == fill.exec_id] == [payload]
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == count
    assert dict(rig.store.position(position_id)) == before
    assert _writes(rig) == writes


def test_B22_maybe_sent_price_modify_unknown_fill_keeps_both_stop_candidates_without_rehang(tmp_path):
    rig, pos, old, native = _open(tmp_path)
    new_stop = old['trigger_price'] + 1.0
    rig.broker.fail_next_modify = BrokerError('synthetic auxPrice outcome uncertain', maybe_sent=True)
    assert not _request_price(rig, old, native, new_stop)
    unknown = rig.store.protection(pos['position_id'])
    assert unknown['state'] == 'UNKNOWN'
    assert unknown['trigger_price'] == old['trigger_price']
    assert unknown['pending_trigger'] == new_stop and unknown['pending_qty'] == 20
    writes = _writes(rig)
    fill = rig.broker.fill(old['intent_id'], qty=5, price=old['trigger_price'] - 0.5, emit=False)
    assert (fill.broker_order_id, fill.perm_id, fill.con_id) == (
        old['broker_order_id'], old['perm_id'], old['con_id'])
    rig.engine._ingest_fill(rig.clock.now(), fill)
    payload = _unknown_audit(rig, fill, old['trigger_price'], new_stop)
    assert rig.store.position(pos['position_id'])['qty_open'] == 15
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert rig.store.protection(pos['position_id'])['intent_id'] == old['intent_id']
    assert len(rig.broker.sent_protections) == 1 and _writes(rig) == writes
    assert rig.store.protection(pos['position_id'])['pending_max_remaining'] == 15
    assert rig.broker.orders[old['intent_id']].remaining == 15
    _assert_replay_keeps_audit_and_accounting(rig, fill, payload, pos['position_id'])


def test_B22_native_status_identity_unknown_keeps_pending_stop_candidates_for_real_same_id_fill(tmp_path):
    rig, pos, old, native = _open(tmp_path)
    new_stop = old['trigger_price'] + 1.0
    rig.broker.protection_auto_status = False
    assert _request_price(rig, old, native, new_stop)
    assert rig.store.protection(pos['position_id'])['state'] == 'PRICE_PENDING'
    # Only this untrusted status has a wrong native contract. The later fill
    # comes from the real old FakeBroker order with its complete exact tuple.
    wrong = replace(native, con_id=native.con_id + 1, total_quantity=20)
    event = StatusEvent(old['intent_id'], native.status, native.filled, native.remaining,
        old['broker_order_id'], old['perm_id'], order_identity=wrong, identity_checked=True)
    rig.engine._on_status(rig.clock.now(), event)
    unknown = rig.store.protection(pos['position_id'])
    assert unknown['state'] == 'UNKNOWN'
    assert rig.store.intent(old['intent_id'])['hold_reason']
    assert unknown['pending_trigger'] == new_stop and unknown['trigger_price'] == old['trigger_price']
    writes = _writes(rig)
    fill = rig.broker.fill(old['intent_id'], qty=5, price=old['trigger_price'] - 0.5, emit=False)
    assert (fill.broker_order_id, fill.perm_id, fill.con_id) == (
        old['broker_order_id'], old['perm_id'], old['con_id'])
    rig.engine._ingest_fill(rig.clock.now(), fill)
    payload = _unknown_audit(rig, fill, old['trigger_price'], new_stop)
    assert rig.store.intent(old['intent_id'])['hold_reason']
    assert rig.store.position(pos['position_id'])['qty_open'] == 15
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert len(rig.broker.sent_protections) == 1 and _writes(rig) == writes
    assert rig.store.protection(pos['position_id'])['pending_max_remaining'] == 15
    assert rig.broker.orders[old['intent_id']].remaining == 15
    _assert_replay_keeps_audit_and_accounting(rig, fill, payload, pos['position_id'])


def test_B22_unconfirmed_price_cancel_replacement_retired_fill_keeps_durable_candidates(tmp_path):
    rig, pos, old, native = _open(tmp_path)
    new_stop = old['trigger_price'] + 1.0
    rig.broker.protection_auto_status = False
    assert _request_price(rig, old, native, new_stop)
    pending = dict(rig.store.protection(pos['position_id']))
    assert pending['state'] == 'PRICE_PENDING' and pending['pending_trigger'] == new_stop
    rig.engine._protection_cancel(rig.clock.now(), pending, 'synthetic cancel before auxPrice acknowledgement')
    assert rig.store.protection(pos['position_id'])['state'] == 'CANCEL_PENDING'
    rig.broker.confirm_cancel(old['intent_id'])
    rig.broker.protection_auto_status = True  # only the genuine replacement is acknowledged
    _settle(rig)
    for _ in range(4):
        rig.advance(seconds=1)
        rig.tick()
        _settle(rig)
        if rig.store.protection(pos['position_id'])['intent_id'] != old['intent_id']:
            break
    new = dict(rig.store.protection(pos['position_id']))
    assert new['intent_id'] != old['intent_id'] and new['state'] == 'WORKING'
    assert new['broker_order_id'] != old['broker_order_id'] and new['perm_id'] != old['perm_id']
    assert new['con_id'] == old['con_id']
    assert new['confirmed_remaining'] == new['pending_max_remaining'] == 20
    assert rig.store.intent(old['intent_id'])['status'] == IntentStatus.CANCELLED
    identity = json.loads(rig.store.control('retired_protection:' + old['intent_id']))
    assert identity.get('stop_price_uncertain') is True, identity
    assert set(identity.get('stop_price_candidates', [])) == {old['trigger_price'], new_stop}, identity
    for field in ('intent_id', 'position_id', 'symbol', 'account', 'client_id', 'con_id', 'broker_order_id', 'perm_id'):
        assert identity[field] == old[field]
    fill = rig.broker.fill(old['intent_id'], qty=5, price=old['trigger_price'] - 0.5, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    payload = _unknown_audit(rig, fill, old['trigger_price'], new_stop, retired=True)
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert rig.store.intent_filled_qty(new['intent_id']) == 0
    assert rig.store.position(pos['position_id'])['qty_open'] == 15
    _settle(rig)
    current = rig.store.protection(pos['position_id'])
    assert current['intent_id'] == new['intent_id'] and current['state'] == 'WORKING'
    assert current['confirmed_remaining'] == current['pending_max_remaining'] == 15
    sells = [order for order in rig.broker.open_orders() if order.symbol == 'AAPL' and order.side in ('SELL', '')]
    assert sum(order.remaining for order in sells) == 15
    assert sum(order.remaining for order in sells) <= next(
        position.quantity for position in rig.broker.positions_fresh() if position.symbol == 'AAPL')
    assert len(rig.broker.sent_protections) == 2
    _assert_replay_keeps_audit_and_accounting(rig, fill, payload, pos['position_id'])

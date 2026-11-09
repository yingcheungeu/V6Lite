"""Batch 24 presentation contracts through real offline callback/card paths.

No broker socket, operator config, runtime database or main loop is used.
Old fixtures supply scenario construction; assertions inspect visible cards,
full raw audit evidence, and the existing fail-closed order state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
from datetime import timedelta

import pytest

from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch17_heartbeat_account import _TimeReplies
from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
from v6lite.tests.test_batch23_tp_evidence import (
    _activate_tp, _deliver_status, _native_status, _terminal_tp, _working, _writes,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # eventkit needs its local Windows socketpair before external connects fail.
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    if loop.is_closed():
        policy.set_event_loop(asyncio.new_event_loop())
    from ib_insync import IB

    def forbidden(*args, **kwargs):
        raise AssertionError('B24 presentation tests must never connect to IB or a network')

    async def forbidden_async(*args, **kwargs):
        forbidden(*args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _cards(channel, fragment):
    return [card for card in channel.accepted if fragment in card['title']]


def _card_text(card):
    return json.dumps(card, ensure_ascii=False)


def _audit_payloads(rig, kind=None):
    query = 'SELECT kind,payload FROM audit ORDER BY id'
    return [(row['kind'], json.loads(row['payload']))
            for row in rig.store.conn.execute(query)
            if kind is None or row['kind'] == kind]


def _contains_value(value, expected):
    if isinstance(value, dict):
        return any(_contains_value(item, expected) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_value(item, expected) for item in value)
    return value == expected


def test_cap_is_two_decimals_in_real_price_drift_card_and_raw_in_audit(tmp_path):
    rig, pid = _working(tmp_path)
    request = _activate_tp(rig, pid)
    channel = _card_channel(rig)
    raw_cap = 119.50999999999999
    original_limit = rig.store.intent(request.intent_id)['limit_price']
    before = _writes(rig)
    adapter, trade = _native_status(rig, request.intent_id, cap=raw_cap)
    _deliver_status(rig, adapter, trade)
    [card] = _cards(channel, '券商工作价格不一致')
    text = _card_text(card)
    assert 'cap=119.51' in text, text
    assert '119.50999999999999' not in text, text
    assert any(_contains_value(payload, raw_cap)
               for _, payload in _audit_payloads(rig, 'ORDER_PRICE_OBSERVATION'))
    assert rig.store.intent(request.intent_id)['limit_price'] == original_limit
    assert _writes(rig) == before


def test_tp_rejection_p0_has_definite_p_s_unprotected_from_full_evidence(tmp_path):
    rig, pid = _working(tmp_path, qty=6)
    request = _activate_tp(rig, pid)
    assert request.quantity == 2
    row = rig.store.protection(pid)
    assert row['confirmed_remaining'] == 4
    # Complete all three sources after the TP becomes terminal and before the
    # callback; its working capacity must not obscure confirmed STP quantity.
    rig.broker.orders[request.intent_id].status = 'Inactive'
    held, own, others, full_exec = rig.engine._protection_evidence(row)
    assert held == 6 and full_exec and len(own) == 1 and not others
    assert own[0].remaining == 4
    channel = _card_channel(rig)
    before = _writes(rig)
    _terminal_tp(rig, request, reason='Order price is outside the allowed price range; B24 synthetic reason')
    [card] = _cards(channel, '止盈价格被券商拒绝')
    text = _card_text(card)
    assert re.search(r'(?:持仓(?:数量|\s*P)?|\bP)\s*[=:：]\s*6\s*股', text), text
    assert re.search(r'(?:已保护(?:\s*S)?|保护单已确认|\bS)\s*[=:：]\s*4\s*股', text), text
    assert re.search(r'未保护(?:量|数量)?\s*[=:：]\s*2\s*股', text), text
    assert '待核实' not in text, text
    assert rig.store.control('tp_pause:' + pid)
    assert _writes(rig) == before


def test_tws_7497_startup_and_heartbeat_cards_use_tws_or_ibkr(tmp_path):
    rig = Rig(tmp_path, start=et(9), port=7497,
              protection=ProtectionConfig(enabled=True))
    replies = _TimeReplies(rig)
    channel = _card_channel(rig)
    rig.start()
    assert len(replies.requests) == 1 and not rig.broker.sent
    startup = [card for card in channel.accepted if '启动' in card['title']]
    assert startup, 'startup must send its actual operator card'
    for card in startup:
        text = _card_text(card)
        assert 'Gateway' not in text, text
        assert 'TWS' in text or 'IBKR' in text, text
    channel.accepted.clear()
    rig.advance(seconds=31)
    rig.engine._gateway_heartbeat(rig.clock.now())
    rig.advance(seconds=29)
    rig.engine._gateway_heartbeat(rig.clock.now())
    rig.advance(seconds=31)
    rig.engine._gateway_heartbeat(rig.clock.now())
    assert rig.engine.gateway_heartbeat_stats()['not_responding']
    [outage] = _cards(channel, '心跳无应答')
    # A current request with a precise local callback proves recovery.
    rig.at(replies.requests[-1] + timedelta(seconds=60))
    rig.engine._gateway_heartbeat(rig.clock.now())
    replies.answer()
    rig.engine._gateway_heartbeat(rig.clock.now())
    assert not rig.engine.gateway_heartbeat_stats()['not_responding']
    [recovery] = _cards(channel, '已恢复应答')
    for card in (outage, recovery):
        text = _card_text(card)
        assert 'Gateway' not in text, text
        assert 'TWS' in text or 'IBKR' in text, text
    assert not rig.broker.sent and not rig.broker.sent_protections


@pytest.mark.parametrize('raw, displayed', [
    (r'\u5e02\u573a\u6570\u636e\u8fde\u63a5\u6682\u65f6\u4e0d\u6d3b\u8dc3', '市场数据连接暂时不活跃'),
    ('市场数据连接暂时不活跃', '市场数据连接暂时不活跃'),
    (r'市场数据 farm: \u6682\u65f6\u4e0d\u6d3b\u8dc3', '市场数据 farm: 暂时不活跃'),
])
def test_native_wrapper_2108_one_readable_log_and_original_audit(tmp_path, caplog, raw, displayed):
    rig = Rig(tmp_path)
    adapter = IbkrBroker(rig.cfg)
    adapter._ensure_channel_hooks()
    adapter.ib.errorEvent += adapter._on_error
    adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
    caplog.set_level(logging.INFO)
    caplog.clear()
    before = _writes(rig)
    before_connected = rig.engine.flags.broker_connected
    # This is the real ib_insync warning path, including its own logger and
    # subsequent native errorEvent emission; no adapter-only shortcut.
    adapter.ib.wrapper.error(-1, 2108, raw, '')
    for event in adapter.drain_events():
        rig.engine._handle_event(rig.clock.now(), event)
    records = [record for record in caplog.records
               if record.name in {'ib_insync.wrapper', 'v6lite.ibkr'}
               and '2108' in record.getMessage()]
    assert len(records) == 1, [record.getMessage() for record in records]
    assert displayed in records[0].getMessage()
    assert r'\u5e02' not in records[0].getMessage()
    assert r'\u6682' not in records[0].getMessage()
    assert any(_contains_value(payload, raw) for _, payload in _audit_payloads(rig)), \
        'the exact raw warning must remain available in audit'
    assert rig.engine.flags.broker_connected == before_connected
    assert _writes(rig) == before
    assert not adapter.ib.isConnected()


def test_protection_unknown_p0_suppresses_same_order_held_p1(tmp_path):
    rig, pid = _working(tmp_path)
    channel = _card_channel(rig)
    row = rig.store.protection(pid)
    ref = row['intent_id']
    before = _writes(rig)
    adapter, trade = _native_status(rig, ref, working_price=row['trigger_price'] + .01)
    _deliver_status(rig, adapter, trade)
    assert rig.store.protection(pid)['state'] == 'UNKNOWN'
    assert rig.store.intent(ref)['hold_reason']
    assert rig.store.control('frozen:AAPL')
    assert len([card for card in channel.accepted if 'P0' in card['title']]) == 1
    for _ in range(2):
        rig.engine._note_working(rig.clock.now(), rig.broker.open_orders())
    held_p1 = [(title, body) for title, body in channel.messages
               if 'P1' in title and (ref in body or 'held order is working' in title)]
    assert not held_p1, held_p1
    assert rig.store.intent(ref)['hold_reason']
    assert rig.store.protection(pid)['state'] == 'UNKNOWN'
    assert _writes(rig) == before


def test_ordinary_day_held_working_still_pages_p1_once(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    request = rig.broker.sent[-1]
    assert any(order.intent_id == request.intent_id and order.tif == 'DAY'
               for order in rig.broker.open_orders())
    channel = _card_channel(rig)
    before = _writes(rig)
    rig.store.hold_intent(rig.clock.now(), request.intent_id, 'synthetic unverifiable DAY order')
    for _ in range(2):
        rig.engine._note_working(rig.clock.now(), rig.broker.open_orders())
    held_p1 = [(title, body) for title, body in channel.messages
               if 'P1' in title and request.intent_id in body]
    assert len(held_p1) == 1, held_p1
    assert rig.store.intent(request.intent_id)['hold_reason']
    assert _writes(rig) == before

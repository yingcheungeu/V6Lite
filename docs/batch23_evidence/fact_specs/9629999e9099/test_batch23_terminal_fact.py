"""Inactive plus a native 202 cancellation is still a cancellation fact.

Stable batch22a helpers; TEMP/FakeBroker and full native hard identity only.
This display guard cannot grant cancel/release authority or rehang protection.
"""
from __future__ import annotations

import socket

import pytest

from v6lite.tests.test_batch22_item9_broker_cancel import (
    _pending_three, _wire, _drive, _writes, _pages,
)


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
        raise AssertionError('terminal fact guard cannot connect to IB or a network')
    async def forbidden_async(*args, **kwargs): forbidden(*args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def test_B23_native_Inactive_202_explicit_cancelled_reason_uses_cancel_fact_and_never_rehangs(tmp_path):
    rig, pid = _pending_three(tmp_path)
    row = rig.store.protection(pid); ref = row['intent_id']
    original = dict(rig.store.position(pid)); before = _writes(rig)
    reason = 'Order canceled by system: synthetic protective order cancellation. END-INACTIVE-202-ORIGINAL'
    assert rig.store.intent(ref)['cancel_requested_at'] is None
    rig.broker.orders[ref].status = 'Inactive'
    adapter, trade = _wire(rig, pid, raw='Inactive', code=202, reason=reason)
    adapter._on_status(trade)
    events = adapter.drain_events(); assert events
    for event in events: rig.engine._on_urgent_broker_event(event)
    _drive(rig)
    cards = _pages(rig, 'P0')
    assert any('券商已取消保护单' in title for title, _ in cards), cards
    assert not any('已拒绝' in title for title, _ in cards)
    assert any(reason in body for _, body in cards), 'native broker cause must remain untranslated and complete'
    assert all('券商已明确拒绝 STP' not in body and '券商已明确拒绝该保护请求' not in body
               for _, body in cards)
    assert any('已回报取消' in body or '已取消' in body for _, body in cards)
    assert any(all(text in body for text in (
        '持仓数量=3 股', '保护单已确认=0 股', '保护单可能=0 股', '未保护=3 股'))
        for _, body in cards), cards
    latest = rig.store.protection(pid)
    assert latest['intent_id'] == ref and latest['state'] == 'UNKNOWN'
    assert latest['confirmed_remaining'] == latest['pending_max_remaining'] == 0
    assert not rig.store.intent(ref)['hold_reason'], 'the full exact native identity is not a foreign tuple'
    assert rig.store.control('frozen:AAPL')
    assert dict(rig.store.position(pid)) == original and _writes(rig) == before
    assert len(rig.broker.sent_protections) == 1
    rig.advance(minutes=31); rig.tick(); _drive(rig)
    assert rig.store.control('frozen:AAPL') and dict(rig.store.position(pid)) == original
    assert rig.store.protection(pid)['intent_id'] == ref and _writes(rig) == before
    assert len(rig.broker.sent_protections) == 1 and rig.broker.cancels == []

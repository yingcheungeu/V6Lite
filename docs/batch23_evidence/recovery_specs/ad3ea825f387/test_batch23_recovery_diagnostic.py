"""B23: a real recovery query failure keeps its full diagnostic in the daily report.

Stable batch22a interfaces, TEMP Store/FakeBroker only. The ANOMALY is produced
by Engine._recover, never inserted by this test. A real operator hold remains
fail-closed across another failed recovery; diagnostics grant no order rights.
"""
from __future__ import annotations

import json
import socket

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.market_calendar import trading_day
from v6lite.models import IntentStatus, Purpose, Side
from v6lite.report import daily_report
from v6lite.tests.helpers import Rig, et


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
        raise AssertionError('recovery diagnostic guard cannot connect to IB or a network')
    async def forbidden_async(*args, **kwargs): forbidden(*args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _writes(rig):
    return tuple(len(items) for items in (
        rig.broker.sent, rig.broker.sent_protections,
        rig.broker.modified_protections, rig.broker.cancels))


def test_B23_real_recovery_query_failure_keeps_full_ANOMALY_in_daily_and_retains_UNKNOWN_hold(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=.0015)
    market_calls = []
    def no_market(*args, **kwargs):
        market_calls.append((args, kwargs)); raise AssertionError('MKT forbidden')
    monkeypatch.setattr(rig.broker, 'submit_market', no_market, raising=False)
    rig.start(); rig.at(et(10, 0, 30)); rig.tick()
    [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
    assert entry.side is Side.BUY and entry.quantity == 3 and entry.limit_price > 0
    original = dict(rig.store.intent(entry.intent_id))
    assert original['status'] == IntentStatus.SENT and original['hold_reason'] is None
    assert rig.store.intent_filled_qty(entry.intent_id) == 0 and rig.store.open_positions() == []
    before = _writes(rig)
    # Entry submission legitimately creates a PENDING plan before any fill.
    # Failed recovery must preserve that existing plan, never submit it.
    original_plans = [dict(row) for row in rig.store.protections()]
    assert rig.broker.sent_protections == []
    cause = ('Synthetic recovery open_orders BrokerError: ' + 'original diagnostic detail; ' * 55
             + 'END-RECOVERY-ORIGINAL-CAUSE-DO-NOT-TRUNCATE')
    calls = []
    def query_failure():
        calls.append('open_orders'); raise BrokerError(cause)
    monkeypatch.setattr(rig.broker, 'open_orders', query_failure)

    # First actual catch proves that unavailable order truth does not finalize it.
    rig.engine._recover(rig.clock.now())
    unknown = rig.store.intent(entry.intent_id)
    assert unknown['status'] == IntentStatus.UNKNOWN and unknown['hold_reason'] is None
    assert unknown['note'] == 'recovery_evidence_unavailable'
    assert rig.store.control('anomaly:recovery_failed') == cause

    # This is the existing public operator hold interface, not a forged control
    # or an inserted ANOMALY. A further failed recovery cannot grant permission.
    hold = 'synthetic operator review: broker query evidence is unavailable'
    assert rig.store.hold_intent(rig.clock.now(), entry.intent_id, hold)
    rig.advance(seconds=1)
    rig.engine._recover(rig.clock.now())
    latest = rig.store.intent(entry.intent_id)
    assert latest['status'] == IntentStatus.UNKNOWN and latest['hold_reason'] == hold
    for field in ('intent_id', 'symbol', 'side', 'purpose', 'position_id', 'quantity',
                  'limit_price', 'broker_order_id', 'perm_id', 'sent_at', 'cancel_requested_at', 'cancel_seen_at'):
        assert latest[field] == original[field], field
    assert calls == ['open_orders', 'open_orders']
    assert rig.store.intent_filled_qty(entry.intent_id) == 0
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == 0
    assert rig.store.open_positions() == []
    assert [dict(row) for row in rig.store.protections()] == original_plans
    assert rig.broker.sent_protections == []
    assert _writes(rig) == before == (1, 0, 0, 0) and market_calls == []
    assert rig.broker.orders[entry.intent_id].status not in {'Cancelled', 'ApiCancelled', 'Inactive', 'Filled'}

    anomalies = [json.loads(row['payload']) for row in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='ANOMALY' ORDER BY id")]
    recovery = [payload for payload in anomalies if payload.get('code') == 'recovery_failed']
    assert len(recovery) == 2 and all(payload.get('detail') == cause for payload in recovery)
    report = daily_report(rig.store, rig.cfg, trading_day(rig.clock.now()))
    assert cause in report and 'END-RECOVERY-ORIGINAL-CAUSE-DO-NOT-TRUNCATE' in report, (
        'the full original exception saved by the real recovery catch must survive the actual daily report')

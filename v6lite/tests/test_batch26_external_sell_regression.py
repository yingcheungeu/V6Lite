"""B26 candidate-regression control: retain B25 external-SELL freeze and P0.

The external actor can create S+X>P before Lite observes it. This test does
not certify that state as safe or suppress legitimate existing protection
reductions/cancels. It only checks the persistent original freeze/P0, and
that cancellation of the external order alone never grants automatic TP.
This is expected green on B25, not a new-feature B25 natural-red claim.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from v6lite.alerts import Alerter
from v6lite.engine import Engine
from v6lite.market_calendar import trading_day
from v6lite.models import OrderRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _protection_config


@pytest.mark.parametrize('reopen', [False, True], ids=['same-engine', 'SQLite-reopen'])
def test_B26_regression_visible_external_SELL_latches_original_P0_and_freeze_until_operator_release(
        tmp_path, reopen):
    protection = _protection_config(True)
    rig = Rig(tmp_path, start=et(9, 50, day=(2026, 10, 13)),
              protection=protection, risk_per_trade_pct=.0015)
    try:
        rig.start(); rig.at(et(10, 0, 30, day=(2026, 10, 13))); rig.tick()
        [entry] = [request for request in rig.broker.sent if request.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        rig.broker.fill(entry.intent_id, qty=3, price=113.13)
        rig.advance(seconds=1); rig.tick()
        [pos] = rig.store.open_positions(); pid = pos['position_id']
        assert pos['qty_open'] == 3 and rig.store.protection(pid)['state'] == 'WORKING'
        rig.engine.shutdown(grace_seconds=0)
        [shutdown] = [json.loads(row['payload']) for row in rig.store.conn.execute(
            "SELECT payload FROM audit WHERE kind='SHUTDOWN'")]
        assert shutdown['unresolved'] == [] and shutdown['broker_working'] == []
        state_path, levels = rig.store.path, rig.store._levels_fn
        rig.alerter.close(); rig.store.close(); rig.store = Store(state_path, levels)
        rig.at(et(10, 1, day=(2026, 10, 14)))
        original_execs = rig.broker.executions_today

        def executions_today():
            day = trading_day(rig.clock.now())
            return [fill for fill in original_execs() if trading_day(fill.time) == day]

        rig.broker.executions_today = executions_today
        rig.broker.account_executions_today = lambda: [replace(fill, intent_id='') for fill in executions_today()]
        assert executions_today() == []
        external = OrderRequest('external-b26-freeze', 'AAPL', Side.SELL, 1, 113.0, Purpose.STOP)
        rig.broker._register(external)  # The TWS actor submitted while Lite was stopped.
        rig.channel.messages.clear()
        rig.alerter = Alerter([rig.channel], synchronous=True)
        rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                            report_dir=tmp_path / 'today-reports')
        rig.engine.startup(); rig.engine._drain(rig.clock.now())
        rig.engine._protection_progress(rig.clock.now())
        assert rig.store.control('frozen:AAPL') == 'external_sell', (
            'failed opt-in evidence must not bypass the original persistent external-SELL freeze')
        assert any('P0' in subject and '外部卖单' in subject and 'AAPL' in body
                   for subject, body in rig.channel.messages), (
            'the original external-SELL P0 must remain; a new overnight P1 cannot replace it')
        assert not [request for request in rig.broker.sent if request.purpose in {
            Purpose.TP1, Purpose.TP2, Purpose.TP3} and request.intent_id.startswith('v6l-')]
        # Broker confirms the external order ended without any execution.
        rig.broker.confirm_cancel(external.intent_id)
        rig.broker.events.clear()  # Unknown-order callback is not used as a ledger authority.
        assert rig.broker.orders[external.intent_id].filled == 0 and executions_today() == []
        if reopen:
            rig.alerter.close(); rig.store.close(); rig.store = Store(state_path, levels)
            rig.alerter = Alerter([rig.channel], synchronous=True)
            rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                                report_dir=tmp_path / 'reopened-reports')
            rig.engine.startup(); rig.engine._drain(rig.clock.now())
        for _ in range(2):
            rig.advance(seconds=31)
            rig.px('AAPL', float(rig.store.position(pid)['tp1']) + .20)
            rig.engine._protection_progress(rig.clock.now())
            rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue()
            rig.engine._drain(rig.clock.now()); rig.engine.process_protection_priority()
        assert rig.store.control('frozen:AAPL') == 'external_sell', (
            'an absent external SELL is not an operator release of its durable freeze')
        assert rig.store.position(pid)['status'] == 'OPEN' and rig.store.position(pid)['qty_open'] == 3
        assert not [request for request in rig.broker.sent if request.purpose in {
            Purpose.TP1, Purpose.TP2, Purpose.TP3} and request.intent_id.startswith('v6l-')]
        assert rig.store.conn.execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 0
    finally:
        rig.alerter.close()
        rig.store.close()

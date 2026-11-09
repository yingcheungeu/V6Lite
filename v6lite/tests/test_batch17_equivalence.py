"""Batch 17: five old risk/accounting contracts with broker protection enabled."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from v6lite.config import ProtectionConfig
from v6lite.models import CommissionEvent, IntentStatus, OrderRequest, Purpose, Side
from v6lite.risk import loss_state, trade_net
from v6lite.tests.helpers import Rig, buy_bars, et, seed_closed_trade


def _rig(tmp_path, **overrides):
    return Rig(tmp_path, protection=ProtectionConfig(enabled=True), **overrides)


def _open(rig):
    entry_id = rig.open_aapl()
    pos = rig.store.open_positions()[0]
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "WORKING"
    assert rig.store.intent(row["intent_id"])["purpose"] == Purpose.PROTECT
    assert rig.broker.orders[row["intent_id"]].remaining == pos["qty_open"]
    return entry_id, pos, row


def _entries(rig):
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


def test_R8_stop_fill_during_scan_latches_before_the_next_entry_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path, scan_times_et=("10:00", "11:00"))
    _, pos, protect = _open(rig)
    rig.broker.bars["TSLA"] = buy_bars()
    rig.px("TSLA", 113.13)
    rig.at(et(11, 0, 30))
    original = rig.broker.daily_bars
    seen = []

    def slow(symbol, days):
        seen.append(symbol)
        if len(seen) == 1:
            # The GTC already exists at the broker. It fills while the scan's
            # blocking history call runs, before a later TSLA buy can send.
            rig.broker.fill(protect["intent_id"], price=105.91)
            rig.clock.advance(seconds=1)
            rig.refresh_quotes()
        return original(symbol, days)

    monkeypatch.setattr(rig.broker, "daily_bars", slow)
    rig.tick()
    assert seen, "exercise the scan's broker query"
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    assert rig.store.risk_day("2026-10-06")["daily_stop_latched"] == 1
    assert len(_entries(rig)) == 1
    assert not [r for r in rig.broker.sent if r.purpose == Purpose.STOP]
    assert any(r["kind"] == "TRADE_CLOSED" for r in rig.store.conn.execute("SELECT kind FROM audit"))


def test_release_counts_as_a_loss_for_the_streak_protection_on(tmp_path, monkeypatch):
    rig = _rig(tmp_path)
    _, pos, protect = _open(rig)
    seed_closed_trade(rig.store, "pos-prior", -3.0, et(9, 40))
    manual = OrderRequest("tws-manual-flatten", "AAPL", Side.SELL,
                          pos["qty_open"], 100.0, Purpose.TP1)
    rig.broker._register(manual)
    manual_fill = rig.broker.fill(manual.intent_id)
    rig.broker.drain_events()
    rig.store.set_control(rig.clock.now(), "frozen:AAPL", "manual_flattened_in_TWS")
    rig.broker.confirm_cancel(protect["intent_id"])
    rig.engine._drain(rig.clock.now())
    assert rig.store.intent(protect["intent_id"])["status"] == IntentStatus.CANCEL_SEEN
    rig.advance(seconds=61)
    assert rig.broker.positions_fresh() == []

    counts = {"open_orders": 0, "positions_fresh": 0, "account_executions_today": 0}
    for method in counts:
        original = getattr(rig.broker, method)
        def counted(original=original, method=method):
            counts[method] += 1
            return original()
        monkeypatch.setattr(rig.broker, method, counted)
    writes = (len(rig.broker.sent), len(rig.broker.cancels))
    rig.store.set_control(rig.clock.now(), "release:AAPL", json.dumps({
        "operator": "John", "reason": "flat in TWS with exact cancelled GTC"}))
    rig.engine._apply_releases(rig.clock.now())
    assert all(count >= 2 for count in counts.values()), counts
    assert rig.store.position(pos["position_id"])["status"] == "RELEASED"
    assert rig.store.intent(protect["intent_id"])["status"] == IntentStatus.CANCELLED
    assert rig.store.control("frozen:AAPL") is None
    assert rig.store.release_times()
    assert (len(rig.broker.sent), len(rig.broker.cancels)) == writes
    assert manual_fill.exec_id in [e.exec_id for e in rig.broker.account_executions_today()]
    assert loss_state(rig.store, rig.cfg).cooldown_until > rig.clock.now()
    rig.advance(seconds=1)
    rig.tick()
    blocks = rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    assert "daily_loss_stop_latched" in blocks
    assert any(b.startswith("loss_cooldown_until") for b in blocks)


def test_R7_late_commission_after_close_still_counts_the_loss_protection_on(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    seed_closed_trade(rig.store, "pos-prior", -3.0, et(9, 40))
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = _entries(rig)[0]
    rig.broker.fill(entry.intent_id, price=113.47, commission=0.0)
    rig.advance(seconds=1)
    rig.tick()
    pos = rig.store.open_positions()[0]
    protect = rig.store.protection(pos["position_id"])
    # A triggered stop-limit may execute on a rebound at a better price. Its
    # small gross profit initially exceeds the conservative missing-fee estimate.
    rig.px("AAPL", protect["trigger_price"] - 0.01)
    rig.advance(seconds=1)
    rig.tick()
    fill = rig.broker.fill(protect["intent_id"], price=113.62,
                           commission=None, commission_separately=True)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    trade = next(t for t in rig.store.closed_trades() if t["position_id"] == pos["position_id"])
    assert trade["gross"] == pytest.approx(pos["qty_open"] * 0.15)
    assert trade_net(trade) > 0 and len(trade["missing"]) == 1
    assert loss_state(rig.store, rig.cfg).cooldown_until is None
    rig.at(et(16, 6))
    rig.tick()
    rig.broker.events.append(CommissionEvent(fill.exec_id, 4.0))
    rig.advance(seconds=1)
    rig.tick()
    trade = next(t for t in rig.store.closed_trades() if t["position_id"] == pos["position_id"])
    assert not trade["missing"] and trade["fees"] == pytest.approx(4.0)
    assert trade_net(trade) == pytest.approx(pos["qty_open"] * 0.15 - 4.0)
    assert loss_state(rig.store, rig.cfg).cooldown_until is not None
    assert rig.store.control("cooldown_until") is not None
    assert not rig.store.open_positions()


def test_L3_late_entry_fill_after_close_opens_a_managed_position_protection_on(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = _entries(rig)[0]
    rig.broker.fill(entry.intent_id, qty=10)
    rig.advance(seconds=61)
    rig.tick()
    rig.broker.confirm_cancel(entry.intent_id)
    rig.advance(seconds=6)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()
    assert rig.store.intent(entry.intent_id)["status"] == IntentStatus.CANCELLED
    pos = rig.store.open_positions()[0]
    protect = rig.store.protection(pos["position_id"])
    assert rig.broker.orders[protect["intent_id"]].remaining == 10
    rig.broker.fill(protect["intent_id"], price=107.0)
    rig.advance(seconds=1)
    rig.tick()
    assert not rig.store.open_positions()
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    protection_count = len(rig.broker.sent_protections)
    rig.broker.fill(entry.intent_id, qty=5, price=entry.limit_price)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.conn.execute(
        "SELECT COUNT(*) FROM positions WHERE status='CLOSED' AND qty_open>0").fetchone()[0] == 0
    opened = rig.store.open_positions()
    assert len(opened) == 1 and opened[0]["qty_open"] == 5
    assert opened[0]["position_id"] != pos["position_id"]
    new_protect = rig.store.protection(opened[0]["position_id"])
    assert new_protect is not None and new_protect["state"] == "WORKING"
    assert new_protect["intent_id"] != protect["intent_id"]
    assert rig.broker.orders[new_protect["intent_id"]].remaining == 5
    assert len(rig.broker.sent_protections) == protection_count + 1
    assert any(k.startswith("anomaly:late_entry_fill") for k in rig.store.anomalies())
    assert rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    assert not [r for r in rig.broker.sent if r.purpose == Purpose.STOP]


def test_kill_blocks_entries_but_exits_still_run_protection_on(tmp_path):
    rig = _rig(tmp_path, scan_times_et=("10:00", "11:00"))
    _, pos, protect = _open(rig)
    rig.store.kill(rig.clock.now(), "operator hold with GTC protection")
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(r for r in rig.broker.sent if r.purpose == Purpose.TP1)
    assert tp.side == Side.SELL and tp.quantity > 0
    assert rig.broker.orders[protect["intent_id"]].remaining + tp.quantity == pos["qty_open"]
    rig.broker.fill(tp.intent_id, price=tp.limit_price)
    rig.advance(seconds=1)
    rig.px("AAPL", pos["entry_price"])
    rig.tick()
    remaining = rig.store.position(pos["position_id"])["qty_open"]
    assert remaining == pos["qty_open"] - tp.quantity
    assert rig.broker.orders[protect["intent_id"]].remaining == remaining
    rig.broker.fill(protect["intent_id"], price=100.0)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    rig.broker.bars["TSLA"] = buy_bars()
    rig.px("TSLA", 113.13)
    rig.at(et(11, 0, 30))
    rig.tick()
    snap = rig.engine.status_snapshot(rig.clock.now())
    assert not snap["entries_allowed"]
    assert any(b.startswith("kill_switch") for b in snap["entry_blocks"])
    assert len(_entries(rig)) == 1
    assert not [r for r in rig.broker.sent if r.purpose == Purpose.STOP]
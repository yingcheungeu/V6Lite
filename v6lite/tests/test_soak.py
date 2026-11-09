"""Randomised multi-day soak against a hostile fake broker (fixed seeds).

The broker randomly fills, partially fills, duplicates callbacks, hides
executions from callbacks (visible only via reqExecutions), races cancels
against fills and drops the connection. After every tick the invariants that
V6-Lite exists to protect must hold.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from v6lite.config import ProtectionConfig
from v6lite.market_calendar import is_rth
from v6lite.models import ProtectionRequest, Purpose, Side
from v6lite.tests.helpers import Rig, buy_bars, et

WORKING = {"Submitted", "PreSubmitted"}


def _hostile_broker_step(rig: Rig, rng: random.Random, prices: dict[str, float]) -> None:
    broker = rig.broker
    for intent_id, order in list(broker.orders.items()):
        if order.status not in WORKING or order.remaining <= 0:
            continue
        protection = isinstance(order.req, ProtectionRequest)
        # outsideRth=False applies to simulated executions as well as triggers.
        can_fill = not protection or is_rth(rig.clock.now())
        fill_price = prices[order.req.symbol] if protection else order.req.limit_price
        cancelled = intent_id in broker.cancels
        roll = rng.random()
        if cancelled and roll < 0.7:
            if rng.random() < 0.25 and can_fill:  # race: fills right before the cancel lands
                broker.fill(intent_id, price=fill_price, emit=rng.random() < 0.5)
            else:
                broker.confirm_cancel(intent_id)
            continue
        if protection:
            # Once the stop crosses, every remaining share is a market SELL:
            # no limit floor, and a rebound cannot untrigger a partial order.
            if can_fill and prices[order.req.symbol] <= order.req.trigger_price:
                order._soak_stp_triggered = True
            marketable = can_fill and getattr(order, "_soak_stp_triggered", False)
        else:
            marketable = (order.req.side is Side.BUY and prices[order.req.symbol] <= order.req.limit_price) or \
                         (order.req.side is Side.SELL and prices[order.req.symbol] >= order.req.limit_price)
        if not marketable or roll > 0.6:
            if rng.random() < 0.05:
                broker.status(intent_id, "Submitted")  # noise
            continue
        qty = order.remaining if rng.random() < 0.7 else max(1, order.remaining // 2)
        broker.fill(intent_id, qty, price=fill_price, duplicate=rng.random() < 0.15,
                    emit=rng.random() < 0.85, commission_separately=rng.random() < 0.3)


def _check_invariants(rig: Rig) -> None:
    store, broker = rig.store, rig.broker
    broker_qty = {p.symbol: p.quantity for p in broker.positions()}
    assert all(q >= 0 for q in broker_qty.values()), f"accidental short: {broker_qty}"
    for row in store.open_positions():
        assert row["qty_open"] >= 0
        assert row["stop"] < row["entry_price"] < row["tp1"] < row["tp2"] < row["tp3"]
        active = store.active_intents_for_position(row["position_id"])
        # L7 deliberately has one GTC protection and may have one DAY TP after
        # reducing the GTC first. Count the non-protection exits separately.
        assert sum(intent["side"] == Side.SELL and intent["purpose"] != Purpose.PROTECT
                   for intent in active) <= 1, "two concurrent DAY exits on one position"
        gross = sum(order.remaining for order in broker.orders.values()
                    if order.req.symbol == row["symbol"] and order.remaining > 0 and
                    order.status not in {"Cancelled", "ApiCancelled", "Inactive", "Filled"} and
                    (isinstance(order.req, ProtectionRequest) or order.req.side is Side.SELL))
        assert gross <= broker_qty.get(row["symbol"], 0), (
            f"working broker SELL gross {gross} exceeds shares {broker_qty.get(row['symbol'], 0)}")
    assert not [order for order in broker.sent if getattr(order, "order_type", "") == "MKT"]
    symbols = [r["symbol"] for r in store.open_positions()]
    assert len(symbols) == len(set(symbols))
    assert not store.anomalies() or rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    # every executed share is accounted exactly once
    fills = store.conn.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT exec_id) AS d FROM fills").fetchone()
    assert fills["n"] == fills["d"]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_soak_three_sessions_hostile_broker(tmp_path, seed):
    rng = random.Random(seed)
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True),
              scan_times_et=("10:00", "13:30"), max_daily_entries=6)
    rig.broker.bars["SPY"] = buy_bars(base=440.0)
    prices = {"AAPL": 113.13, "SPY": rig.broker.bars["SPY"][-1].close, "TSLA": 250.0}
    for p in prices:
        rig.px(p, prices[p])
    rig.engine.startup()
    for day in ((2026, 10, 6), (2026, 10, 7), (2026, 10, 8)):
        rig.at(et(9, 45, day=day))
        t = et(9, 45, day=day)
        end = et(16, 10, day=day)
        while t < end:
            for sym in prices:
                prices[sym] = max(5.0, prices[sym] * (1 + rng.gauss(0, 0.004)))
                rig.px(sym, round(prices[sym], 2))
            _hostile_broker_step(rig, rng, prices)
            if rng.random() < 0.01:
                rig.broker.drop_connection()
            rig.tick()
            _check_invariants(rig)
            step = timedelta(seconds=rng.choice((5, 10, 30, 60)))
            t += step
            rig.clock.set(t)
        # overnight: bars roll forward so scans keep producing signals
        for sym in ("AAPL", "SPY"):
            bars = rig.broker.bars[sym]
            rig.broker.bars[sym] = bars[1:] + [bars[-1].__class__(
                "n", prices[sym], prices[sym] * 1.01, prices[sym] * 0.99, prices[sym], 1_000_000)]
    rig.engine.shutdown(grace_seconds=0)
    _check_invariants(rig)
    traded = rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert traded > 0, "soak produced no trading activity; the scenario is not exercising the engine"

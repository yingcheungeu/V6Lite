"""Independent behavior tests for the BT-1 offline backtest adapter."""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from research.bt1 import backtest as bt
from v6lite.config import load_config
from v6lite.exits import breakeven_stop, compute_levels
from v6lite.models import Bar
from v6lite.sizing import estimate_fee

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def cfg():
    return load_config(ROOT / "v6lite" / "examples" / "paper.toml")


def trade(cfg, qty=5):
    entry = 104.61
    return bt.Trade(
        trade_id="TEST-ABT", symbol="ABT", signal_day="2026-08-04",
        entry_day="2026-08-05", entry_price=entry, atr=3.02,
        qty_initial=qty, score=70, vix=20.0,
        levels=compute_levels(cfg, entry, 3.02),
        entry_fee=bt.fee_for(qty, entry, "conservative"),
    )


def bar(day, open_, high, low, close):
    return Bar(day, open_, high, low, close, 1_000_000.0)


def test_abt_ledger_fixed_point(cfg):
    levels = compute_levels(cfg, 104.61, 3.02)
    assert (levels.stop, levels.tp1, levels.tp2, levels.tp3) == (
        97.06, 113.67, 119.71, 128.77,
    )


def test_gap_below_stop_fills_at_open(cfg):
    position = trade(cfg)
    legs = bt.process_daily_bar(
        cfg, position, bar("2026-08-06", 95.25, 98.0, 94.0, 97.0),
    )
    assert len(legs) == 1
    assert legs[0]["price"] == 95.25
    assert legs[0]["qty"] == 5
    assert "STOP" in str(legs[0]["stage"]).upper()
    assert position.qty_open == 0


def test_same_bar_stop_and_profit_uses_stop_first(cfg):
    position = trade(cfg)
    legs = bt.process_daily_bar(
        cfg, position, bar("2026-08-06", 104.0, 125.0, 96.0, 112.0),
    )
    assert len(legs) == 1
    assert legs[0]["price"] == 97.06
    assert legs[0]["qty"] == 5
    assert "STOP" in str(legs[0]["stage"]).upper()
    assert position.qty_open == 0


def test_tp1_then_stop_moves_to_breakeven(cfg):
    position = trade(cfg)
    first = bt.process_daily_bar(
        cfg, position, bar("2026-08-06", 105.0, 114.0, 105.0, 112.0),
    )
    assert len(first) == 1
    assert first[0]["qty"] == 1
    assert "TP1" in str(first[0]["stage"]).upper()
    assert position.tp1_done and position.qty_open == 4
    second = bt.process_daily_bar(
        cfg, position, bar("2026-08-07", 106.0, 108.0, 104.0, 105.0),
    )
    assert len(second) == 1
    assert second[0]["qty"] == 4
    assert second[0]["price"] == breakeven_stop(cfg, 104.61)
    assert "STOP" in str(second[0]["stage"]).upper()


def test_thirds_keep_final_remainder(cfg):
    position = trade(cfg, qty=10)
    legs = []
    legs += bt.process_daily_bar(cfg, position, bar("2026-08-06", 105, 114, 105, 112))
    legs += bt.process_daily_bar(cfg, position, bar("2026-08-07", 115, 120, 110, 119))
    legs += bt.process_daily_bar(cfg, position, bar("2026-08-10", 121, 130, 118, 129))
    assert [x["qty"] for x in legs] == [3, 3, 4]
    assert [str(x["stage"]).upper().split(".")[-1] for x in legs] == ["TP1", "TP2", "TP3"]
    assert position.qty_open == 0


def test_each_fill_uses_selected_commission_model():
    qty, price = 10, 104.61
    assert bt.fee_for(qty, price, "conservative") == estimate_fee(qty, price)
    assert bt.fee_for(qty, price, "paper") == 1.0
    assert bt.fee_for(qty, price, "zero") == 0.0
    with pytest.raises(ValueError):
        bt.fee_for(qty, price, "unknown")


def test_signal_decision_ignores_day_and_future_bars(cfg):
    days = []
    current = date(2025, 1, 2)
    while len(days) < 110:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    prices = [100 + 0.4 * i + 2 * math.sin(i / 4) for i in range(110)]
    stock = [bar(day, p, p + 1, p - 1, p + 0.2) for day, p in zip(days, prices)]
    vix = [bar(day, 20, 21, 19, 20) for day in days]
    target = days[90]
    original = bt.signal_for_day(cfg, {"ABT": stock, "^VIX": vix}, target)
    changed_stock = stock[:90] + [bar(day, 900, 1100, 800, 1000) for day in days[90:]]
    changed_vix = vix[:90] + [bar(day, 100, 101, 99, 100) for day in days[90:]]
    changed = bt.signal_for_day(cfg, {"ABT": changed_stock, "^VIX": changed_vix}, target)
    assert original == changed


def test_price_drift_gate():
    assert bt.entry_allowed_by_drift(100.0, 102.9)
    assert not bt.entry_allowed_by_drift(100.0, 103.1)


def test_two_opening_losses_start_same_day_cooldown(cfg):
    """The daily approximation must block a new entry after two gap losses."""
    from dataclasses import replace
    from v6lite.models import Signal

    loose_daily_stop = replace(
        cfg, daily_loss_limit_usd=10_000.0, daily_loss_limit_pct=0.99,
    )
    monday, tuesday = date(2026, 8, 3), date(2026, 8, 4)
    day1, day2 = monday.isoformat(), tuesday.isoformat()
    series = {
        "AAPL": [bar(day1, 100, 101, 99, 100), bar(day2, 94, 95, 93, 94)],
        "TSLA": [bar(day1, 100, 101, 99, 100), bar(day2, 94, 95, 93, 94)],
        "ABT": [bar(day1, 100, 101, 99, 100), bar(day2, 100, 101, 99, 100)],
    }
    cache = {"A": {
        day1: ([Signal("AAPL", 70, 100, 2), Signal("TSLA", 70, 100, 2)], [], 20.0),
        day2: ([Signal("ABT", 70, 100, 2)], [], 20.0),
    }}
    trades, _, _ = bt.run_scenario(
        loose_daily_stop, series, scenario="A", start_day=monday, end_day=tuesday,
        scan_cache=cache,
    )
    assert [t.symbol for t in trades] == ["AAPL", "TSLA"]
    assert all(t.net_pnl < 0 for t in trades)



def test_same_day_tp1_then_new_breakeven_stop(cfg):
    position = trade(cfg)
    legs = bt.process_daily_bar(
        cfg, position, bar("2026-08-06", 105.0, 114.0, 100.0, 112.0),
    )
    assert [(leg["stage"], leg["qty"]) for leg in legs] == [("TP1", 1), ("STOP", 4)]
    assert legs[1]["price"] == breakeven_stop(cfg, 104.61)
    assert position.qty_open == 0


def test_flat_trade_neither_increments_nor_clears_loss_streak():
    streak = 0
    streak = bt._next_loss_streak(streak, -10.0)
    assert streak == 1
    streak = bt._next_loss_streak(streak, 0.0)
    assert streak == 1
    streak = bt._next_loss_streak(streak, -2.0)
    assert streak == 2
    streak = bt._next_loss_streak(streak, 3.0)
    assert streak == 0

"""Independent five-trade audit from the immutable source DB and A CSV.

Does not import research.bt1.backtest or any strategy/exit/sizing module.
"""
from __future__ import annotations

import csv
import json
import random
import sqlite3
import tomllib
from decimal import Decimal, ROUND_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CSV = ROOT / "research" / "bt1" / "out" / "A_trades.csv"
DB = Path(r"C:\Dev\TradingV6\data\historical\prices.sqlite")
CONFIG = ROOT / "v6lite" / "examples" / "paper.toml"
SEED = 20261004
N = 5


def nearly(a, b, tolerance=0.000101):
    assert abs(float(a) - float(b)) <= tolerance, (a, b)


def fee(qty, price):
    notion = qty * price
    commission = min(max(1.0, 0.005 * qty), 0.01 * notion) if qty else 0.0
    regulatory = 0.0000206 * notion + 0.000195 * qty
    return max(2.0, (commission + regulatory) * 1.5)


def discounted_sell(level, band):
    x = Decimal(str(level)) * (Decimal(1) - Decimal(str(band)))
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_UP))


def atr_from_raw(prior_bars):
    trs = []
    for prev, cur in zip(prior_bars, prior_bars[1:]):
        prev_close = prev[3]
        high, low = cur[1], cur[2]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    assert len(trs) >= 14
    value = sum(trs[:14]) / 14
    for tr in trs[14:]:
        value = (value * 13 + tr) / 14
    return round(value, 2)


def source_bars(db, symbol, entry_day, exit_day):
    prior = list(db.execute(
        "SELECT trading_day,high,low,close FROM historical_bars "
        "WHERE symbol=? AND timeframe='1d' AND source='ibkr' AND trading_day<? "
        "ORDER BY trading_day DESC LIMIT 90", (symbol, entry_day)))
    prior.reverse()
    during = list(db.execute(
        "SELECT trading_day,open,high,low,close FROM historical_bars "
        "WHERE symbol=? AND timeframe='1d' AND source='ibkr' "
        "AND trading_day>=? AND trading_day<=? ORDER BY trading_day",
        (symbol, entry_day, exit_day)))
    assert prior and during and during[0][0] == entry_day and during[-1][0] == exit_day
    return prior, during


def replay_one(row, db, config):
    strategy = config["strategy"]
    exit_band = config["execution"]["exit_limit_band_pct"]
    symbol, entry_day, exit_day = row["symbol"], row["entry_day"], row["exit_day"]
    prior, during = source_bars(db, symbol, entry_day, exit_day)
    atr = atr_from_raw(prior)
    nearly(atr, row["atr"], 0.001)
    entry = round(during[0][1] * 1.0005, 4)
    nearly(entry, row["entry_price"])
    qty_start = int(row["qty"])
    levels = {
        "stop": round(entry - strategy["atr_stop_mult"] * atr, 2),
        "TP1": round(entry + strategy["tp1_mult"] * atr, 2),
        "TP2": round(entry + strategy["tp2_mult"] * atr, 2),
        "TP3": round(entry + strategy["tp3_mult"] * atr, 2),
    }
    for key, value in levels.items():
        nearly(value, row[key.lower()])
    entry_fee = fee(qty_start, entry)
    nearly(entry_fee, row["entry_fee"], 0.000001)
    available = qty_start
    tp1_done = tp2_done = False
    planned = []

    def next_stage():
        if not tp1_done:
            return "TP1"
        if not tp2_done:
            return "TP2"
        return "TP3"

    def stop_level():
        return round(entry * (1 + strategy["breakeven_offset_pct"]), 2) if tp1_done else levels["stop"]

    def fill(day, stage, price):
        nonlocal available, tp1_done, tp2_done
        count = available if stage in ("STOP", "TP3") else min(max(1, qty_start // 3), available)
        planned.append({
            "day": day, "stage": stage, "qty": count, "price": price,
            "fee": fee(count, price), "gross_pnl": (price - entry) * count,
        })
        available -= count
        if stage == "TP1":
            tp1_done = True
        elif stage == "TP2":
            tp2_done = True

    for day, opening, high, low, _close in during:
        if available == 0:
            break
        # Existing positions have an opening check. On entry day the open is
        # the fill reference and cannot trigger an exit before that fill.
        if day != entry_day:
            if opening <= stop_level():
                fill(day, "STOP", opening)
            elif opening >= levels[next_stage()]:
                stage = next_stage()
                fill(day, stage, discounted_sell(levels[stage], exit_band))
        if available == 0:
            continue
        stage = next_stage()
        if low <= stop_level():
            fill(day, "STOP", stop_level())
            continue
        while available > 0 and high >= levels[next_stage()]:
            stage = next_stage()
            fill(day, stage, discounted_sell(levels[stage], exit_band))
            # After TP1, prioritize the newly raised breakeven stop over
            # any further same-day TP touched by the daily high.
            if stage == "TP1" and available > 0 and low <= stop_level():
                fill(day, "STOP", stop_level())
                break
    assert available == 0, (symbol, entry_day, exit_day, available)
    reported = json.loads(row["exit_legs"])
    assert len(planned) == len(reported), (len(planned), len(reported))
    for manual, output in zip(planned, reported):
        assert (manual["day"], manual["stage"], manual["qty"]) == (
            output["day"], output["stage"], output["qty"])
        nearly(manual["price"], output["price"])
        nearly(manual["fee"], output["fee"], 0.000001)
        nearly(manual["gross_pnl"], output["gross_pnl"], 0.000001)
    gross = sum(leg["gross_pnl"] for leg in planned)
    exit_fees = sum(leg["fee"] for leg in planned)
    net = gross - entry_fee - exit_fees
    risk = qty_start * strategy["atr_stop_mult"] * atr
    nearly(gross, row["gross_pnl"], 0.000001)
    nearly(exit_fees, row["exit_fees"], 0.000001)
    nearly(net, row["net_pnl"], 0.000001)
    nearly(risk, row["risk_usd"], 0.000001)
    nearly(net / risk, row["r_multiple"], 0.000001)
    return {
        "trade_id": row["trade_id"], "symbol": symbol, "entry_day": entry_day,
        "exit_day": exit_day, "entry": entry, "atr": atr, "legs": len(planned),
        "gross": round(gross, 6), "fees": round(entry_fee + exit_fees, 6),
        "net": round(net, 6), "r": round(net / risk, 6),
    }


def main():
    with CSV.open("r", encoding="utf-8", newline="") as file:
        rows = [(line, row) for line, row in enumerate(csv.DictReader(file), 2)
                if row["exit_day"] and not row["censor_day"]]
    assert len(rows) >= N, len(rows)
    chosen = random.Random(SEED).sample(rows, N)
    with CONFIG.open("rb") as file:
        config = tomllib.load(file)
    uri = DB.resolve().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        for line, row in chosen:
            result = replay_one(row, db, config)
            print(json.dumps({"csv_line": line, **result}, sort_keys=True))
    print(f"PASS {N}/{N} seed={SEED} eligible={len(rows)}")


if __name__ == "__main__":
    main()



"""BT-1: deterministic, offline daily-bar study of the unchanged V6-Lite code.

This module never imports a broker adapter.  The only database connection is
SQLite's read-only immutable URI.  An OHLC bar cannot reconstruct the order
of intraday events; `stop_first` makes that uncertainty explicit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from v6lite.config import LiteConfig, load_config
from v6lite.engine import MAX_PRICE_DRIFT_PCT
from v6lite.exits import (
    Levels, PositionView, breakeven_stop, buy_limit, compute_levels,
    decide_exit, sell_limit,
)
from v6lite.market_calendar import is_trading_day
from v6lite.models import Bar, Purpose, Signal
from v6lite.risk import daily_loss_limit
from v6lite.sizing import estimate_fee, size_entry
from v6lite.strategy import effective_min_score, generate_signals

SOURCE_DB = Path(r"C:\Dev\TradingV6\data\historical\prices.sqlite")
CONFIG = Path(__file__).resolve().parents[2] / "v6lite" / "examples" / "paper.toml"
OUTPUT = Path(__file__).resolve().parent / "out"
START_DAY = date(2021, 7, 1)
END_DAY = date(2026, 8, 6)
INITIAL_CAPITAL = 10_000.0
SCENARIOS = {"A": ("bars", 60), "B": ("calendar", 60),
             "C": ("bars", 50), "D": ("bars", 70)}


def _day(s: str | date) -> date:
    return s if isinstance(s, date) else date.fromisoformat(s)


def _days(start: date, end: date):
    d = start
    while d <= end:
        if is_trading_day(d):
            yield d
        d += timedelta(days=1)


def _prior_trading_day(day: date) -> date:
    d = day - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def _valid_bar(b: Bar) -> bool:
    values = (b.open, b.high, b.low, b.close)
    return all(v is not None and math.isfinite(v) and v > 0 for v in values) and \
        b.volume is not None and math.isfinite(b.volume) and b.volume >= 0


def load_bars(db: Path = SOURCE_DB, symbols: tuple[str, ...] | None = None
              ) -> dict[str, list[Bar]]:
    """Read only requested IBKR daily series; never open a writable handle."""
    if symbols is None:
        symbols = load_config(CONFIG).tradable_symbols + ("^VIX",)
    uri = db.resolve().as_uri() + "?mode=ro&immutable=1"
    result: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
    with sqlite3.connect(uri, uri=True) as conn:
        for symbol in symbols:
            rows = conn.execute(
                "SELECT trading_day,open,high,low,close,volume "
                "FROM historical_bars WHERE symbol=? AND timeframe='1d' AND source='ibkr' "
                "ORDER BY trading_day", (symbol,))
            result[symbol] = [Bar(*row) for row in rows]
    return result


def audit_data(bars: dict[str, list[Bar]], db: Path = SOURCE_DB) -> dict:
    """Return every calendar gap/invalid row/jump without concealing data flaws."""
    report: dict = {"db_sha256": hashlib.sha256(db.read_bytes()).hexdigest(), "symbols": {}}
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        report["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        for symbol, rows in bars.items():
            dates = [_day(b.day) for b in rows]
            observed = set(dates)
            missing = [str(d) for d in _days(date(2021, 6, 14), END_DAY) if d not in observed]
            bad = [{"day": b.day, "ohlcv": [b.open, b.high, b.low, b.close, b.volume]}
                   for b in rows if not _valid_bar(b)]
            jumps = []
            close_jumps = []
            for previous, current in zip(rows, rows[1:]):
                if _valid_bar(previous) and _valid_bar(current):
                    change = current.open / previous.close - 1
                    if abs(change) > .25:
                        jumps.append({"previous_day": previous.day, "day": current.day,
                                      "previous_close": previous.close, "open": current.open,
                                      "change_pct": round(change * 100, 4)})
                    close_change = current.close / previous.close - 1
                    if abs(close_change) > .25:
                        close_jumps.append({"previous_day": previous.day, "day": current.day,
                                            "change_pct": round(close_change * 100, 4)})
            adjusted = conn.execute(
                "SELECT COUNT(*) FROM historical_bars WHERE symbol=? AND timeframe='1d' "
                "AND source='ibkr' AND adjusted_close IS NOT NULL", (symbol,)).fetchone()[0]
            report["symbols"][symbol] = {
                "rows": len(rows), "start": rows[0].day if rows else "",
                "end": rows[-1].day if rows else "", "missing_days": missing,
                "invalid_rows": bad, "overnight_jumps": jumps,
                "close_to_close_jumps": close_jumps,
                "adjusted_close_nonnull": adjusted,
                "duplicate_days": len(rows) - len(observed),
            }
    return report


def signal_for_day(cfg: LiteConfig, bars_by_symbol: dict[str, list[Bar]],
                   day: str | date, *, lookback: str = "bars",
                   min_score: int | None = None
                   ) -> tuple[list[Signal], list, float | None]:
    """Compute D's signals from bars strictly before D and VIX at D-1 only."""
    target = _day(day)
    if lookback not in {"bars", "calendar"}:
        raise ValueError(lookback)
    prior = _prior_trading_day(target).isoformat()
    vix_rows = [b for b in bars_by_symbol.get("^VIX", ()) if b.day == prior and _valid_bar(b)]
    vix = vix_rows[0].close if len(vix_rows) == 1 else None
    threshold = effective_min_score(
        vix, min_score=cfg.min_score if min_score is None else min_score,
        high_vix_threshold=cfg.high_vix_threshold,
        high_vix_min_score=cfg.high_vix_min_score)
    inputs: dict[str, list[Bar]] = {}
    for symbol in cfg.scan_symbols:
        history = [b for b in bars_by_symbol.get(symbol, ()) if _day(b.day) < target]
        if lookback == "bars":
            history = history[-cfg.bars_lookback_days:]
        else:
            floor = target - timedelta(days=60)
            history = [b for b in history if _day(b.day) >= floor]
        # A missing D-1 bar is not silently forward-filled.  This especially
        # matters at the incomplete right edge of several source series.
        inputs[symbol] = history if history and history[-1].day == prior else []
    signals, results = generate_signals(inputs, etf_symbols=cfg.etf_symbols, min_score=threshold)
    return signals, results, vix


def entry_allowed_by_drift(signal_price: float, open_price: float) -> bool:
    return (math.isfinite(signal_price) and signal_price > 0 and
            math.isfinite(open_price) and open_price > 0 and
            abs(open_price - signal_price) / signal_price <= MAX_PRICE_DRIFT_PCT)


def fee_for(qty: int, price: float, mode: str) -> float:
    if mode == "conservative":
        return estimate_fee(qty, price)
    if mode == "zero":
        return 0.0
    if mode == "paper":
        return max(1.0, 0.005 * qty)
    raise ValueError(f"unknown fee mode: {mode}")


def _next_loss_streak(streak: int, net_pnl: float) -> int:
    """Match risk.loss_state: a flat trade neither adds nor clears a streak."""
    if net_pnl < 0:
        return streak + 1
    if net_pnl > 0:
        return 0
    return streak


@dataclass
class Trade:
    trade_id: str
    symbol: str
    signal_day: str
    entry_day: str
    entry_price: float
    atr: float
    qty_initial: int
    score: int
    vix: float | None
    levels: Levels
    entry_fee: float
    atr_stop_mult: float = 2.5
    qty_open: int = -1
    tp1_done: bool = False
    tp2_done: bool = False
    breakeven_stop: float | None = None
    exit_day: str = ""
    legs: list[dict] = field(default_factory=list)
    gross_pnl: float = 0.0
    exit_fees: float = 0.0
    ambiguous_days: int = 0
    censor_day: str = ""
    censor_mark: float | None = None
    censor_qty: int = 0

    def __post_init__(self):
        if self.qty_open < 0:
            self.qty_open = self.qty_initial

    @property
    def risk_usd(self) -> float:
        return self.qty_initial * self.atr * self.atr_stop_mult

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.entry_fee - self.exit_fees

    def view(self) -> PositionView:
        return PositionView(self.trade_id, self.symbol, self.entry_price,
                            self.qty_initial, self.qty_open, self.levels.stop,
                            self.levels.tp1, self.levels.tp2, self.levels.tp3,
                            self.tp1_done, self.tp2_done, self.breakeven_stop)


def _fill_exit(cfg: LiteConfig, trade: Trade, decision, bar: Bar, price: float,
               fee_mode: str) -> dict:
    quantity = decision.quantity
    gross = (price - trade.entry_price) * quantity
    fee = fee_for(quantity, price, fee_mode)
    leg = {"day": bar.day, "stage": str(decision.stage), "qty": quantity,
           "price": round(price, 4), "fee": round(fee, 6),
           "gross_pnl": round(gross, 6)}
    trade.qty_open -= quantity
    trade.gross_pnl += gross
    trade.exit_fees += fee
    trade.legs.append(leg)
    if decision.stage == Purpose.TP1:
        trade.tp1_done = True
        trade.breakeven_stop = breakeven_stop(cfg, trade.entry_price)
    elif decision.stage == Purpose.TP2:
        trade.tp2_done = True
    if trade.qty_open == 0:
        trade.exit_day = bar.day
    return leg


def process_daily_bar(cfg: LiteConfig, trade: Trade, bar: Bar, *,
                      stop_first: bool = True, fee_mode: str = "conservative",
                      phase: str = "full") -> list[dict]:
    """Apply a day's open then range; exit *decisions* always call decide_exit.

    `phase=open` followed by `phase=range` permits entry at the open without
    using the day's high/low to decide whether to enter.  A stop/TP collision
    is recorded once per trade-day and stop-first is the baseline.
    """
    if phase not in {"full", "open", "range"}:
        raise ValueError(phase)
    if not _valid_bar(bar) or trade.qty_open <= 0:
        return []
    out: list[dict] = []
    if phase in {"full", "open"}:
        opening = decide_exit(trade.view(), bar.open)
        if opening:
            price = bar.open if opening.stage == Purpose.STOP else sell_limit(
                opening.trigger_level, cfg.exit_limit_band_pct)
            out.append(_fill_exit(cfg, trade, opening, bar, price, fee_mode))
    if phase == "open" or trade.qty_open <= 0:
        return out
    stop = decide_exit(trade.view(), bar.low)
    # A high price can cross multiple TP levels. Check it against the state
    # before processing the low so the collision count is independent of order.
    high = decide_exit(trade.view(), bar.high)
    has_stop = stop is not None and stop.stage == Purpose.STOP
    has_tp = high is not None and high.stage in {Purpose.TP1, Purpose.TP2, Purpose.TP3}
    if has_stop and has_tp:
        trade.ambiguous_days += 1
    if has_stop and (stop_first or not has_tp):
        out.append(_fill_exit(cfg, trade, stop, bar, stop.trigger_level, fee_mode))
        return out
    # One daily high may have crossed multiple TP levels. After TP1, a low
    # between the old stop and new breakeven stop is assumed to happen after
    # TP1 in the conservative path; only then may TP2/TP3 be considered.
    while trade.qty_open > 0:
        decision = decide_exit(trade.view(), bar.high)
        if decision is None or decision.stage == Purpose.STOP:
            break
        price = sell_limit(decision.trigger_level, cfg.exit_limit_band_pct)
        out.append(_fill_exit(cfg, trade, decision, bar, price, fee_mode))
        if stop_first and trade.qty_open > 0 and decision.stage == Purpose.TP1:
            revised_stop = decide_exit(trade.view(), bar.low)
            if revised_stop and revised_stop.stage == Purpose.STOP:
                out.append(_fill_exit(cfg, trade, revised_stop, bar,
                                      revised_stop.trigger_level, fee_mode))
                return out
    if trade.qty_open > 0 and not stop_first:
        decision = decide_exit(trade.view(), bar.low)
        if decision and decision.stage == Purpose.STOP:
            out.append(_fill_exit(cfg, trade, decision, bar, decision.trigger_level, fee_mode))
    return out


TRADE_COLUMNS = (
    "trade_id", "symbol", "signal_day", "entry_day", "exit_day", "score", "vix",
    "entry_price", "atr", "qty", "risk_usd", "stop", "tp1", "tp2", "tp3",
    "entry_fee", "exit_fees", "gross_pnl", "net_pnl", "r_multiple",
    "holding_days", "exit_stages", "exit_legs", "ambiguous_days", "unrealized_pnl",
    "censor_day", "censor_mark", "censor_qty",
)
EQUITY_COLUMNS = ("day", "cash", "market_value", "equity", "drawdown_usd",
                  "open_positions", "signals", "entries")


def _money(n: float) -> float:
    return round(n, 6)


def _vix_group(vix: float | None) -> str:
    if vix is None:
        return "missing"
    if vix < 20:
        return "<20"
    if vix <= 30:
        return "20-30"
    return ">30"


def trade_row(trade: Trade, last_mark: float | None = None) -> dict:
    risk = trade.risk_usd
    unrealized = ((last_mark - trade.entry_price) * trade.qty_open if last_mark is not None else 0.0)
    return {
        "trade_id": trade.trade_id, "symbol": trade.symbol,
        "signal_day": trade.signal_day, "entry_day": trade.entry_day,
        "exit_day": trade.exit_day, "score": trade.score,
        "vix": "" if trade.vix is None else _money(trade.vix),
        "entry_price": _money(trade.entry_price), "atr": _money(trade.atr),
        "qty": trade.qty_initial, "risk_usd": _money(risk),
        "stop": trade.levels.stop, "tp1": trade.levels.tp1,
        "tp2": trade.levels.tp2, "tp3": trade.levels.tp3,
        "entry_fee": _money(trade.entry_fee), "exit_fees": _money(trade.exit_fees),
        "gross_pnl": _money(trade.gross_pnl), "net_pnl": _money(trade.net_pnl),
        "r_multiple": "" if not trade.exit_day else _money(trade.net_pnl / risk),
        "holding_days": "" if not trade.exit_day else
            (_day(trade.exit_day) - _day(trade.entry_day)).days,
        "exit_stages": ";".join(leg["stage"] for leg in trade.legs),
        "exit_legs": json.dumps(trade.legs, separators=(",", ":"), sort_keys=True),
        "ambiguous_days": trade.ambiguous_days,
        "unrealized_pnl": _money(unrealized),
        "censor_day": trade.censor_day,
        "censor_mark": "" if trade.censor_mark is None else _money(trade.censor_mark),
        "censor_qty": trade.censor_qty,
    }


def _summary_group(trades: list[Trade]) -> dict:
    closed = [t for t in trades if t.exit_day]
    winners = [t for t in closed if t.net_pnl > 0]
    losers = [t for t in closed if t.net_pnl < 0]
    gains = sum(t.net_pnl for t in winners)
    losses = -sum(t.net_pnl for t in losers)
    return {
        "trades": len(closed),
        "censored_data_gap": sum(bool(t.censor_day) for t in trades),
        "open_at_end": sum(not t.exit_day and not t.censor_day for t in trades),
        "win_rate_pct": _money(100 * len(winners) / len(closed)) if closed else None,
        "avg_r": _money(sum(t.net_pnl / t.risk_usd for t in closed) / len(closed)) if closed else None,
        "profit_factor": _money(gains / losses) if losses else None,
        "net_pnl_usd": _money(sum(t.net_pnl for t in closed)),
    }


def summarize(trades: list[Trade], curve: list[dict], signals_total: int,
              entries_total: int) -> dict:
    closed = [t for t in trades if t.exit_day]
    main = _summary_group(trades)
    count = len(closed)
    first = _day(curve[0]["day"])
    last = _day(curve[-1]["day"])
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    grouped = {}
    for name, key in (("symbol", lambda t: t.symbol),
                      ("year", lambda t: t.entry_day[:4]),
                      ("vix", lambda t: _vix_group(t.vix)),
                      ("period", lambda t: "2021-07..2024-06" if t.entry_day < "2024-07-01"
                       else "2024-07..2026-08")):
        keys = sorted({key(t) for t in trades})
        grouped[name] = {k: _summary_group([t for t in trades if key(t) == k]) for k in keys}
    reaches = Counter(stage for t in closed for stage in {leg["stage"] for leg in t.legs})
    for t in closed:
        if any(leg["stage"] == "STOP" for leg in t.legs):
            if t.tp1_done:
                reaches["BREAKEVEN"] += 1
            else:
                reaches["ATR_STOP"] += 1
    fees = sum(t.entry_fee + t.exit_fees for t in trades)
    fees_closed = sum(t.entry_fee + t.exit_fees for t in closed)
    gross_profit = sum(max(t.gross_pnl, 0) for t in closed)
    max_dd = max(row["drawdown_usd"] for row in curve)
    censored_proxy = sum(t.net_pnl + (t.censor_mark - t.entry_price) * t.censor_qty
                         for t in trades if t.censor_day)
    open_mtm = sum(t.net_pnl +
                   (curve[-1]["marks"].get(t.symbol, t.entry_price) - t.entry_price) * t.qty_open
                   for t in trades if t.qty_open > 0)
    main.update({
        "signals_total": signals_total, "entries_total": entries_total,
        "months": months, "signals_per_month": _money(signals_total / months),
        "entries_per_month": _money(entries_total / months),
        "expectancy_r_per_trade": main["avg_r"],
        "total_pnl_usd_including_open_mtm": _money(curve[-1]["equity"] - INITIAL_CAPITAL),
        "censored_last_mark_proxy_pnl_usd": _money(censored_proxy),
        "open_mtm_and_partial_realized_pnl_usd": _money(open_mtm),
        "open_unrealized_pnl_usd": _money(sum(
            (curve[-1]["marks"].get(t.symbol, t.entry_price) - t.entry_price) * t.qty_open
            for t in trades if t.qty_open > 0)),
        "max_drawdown_usd": _money(max_dd),
        "max_drawdown_pct_of_10000": _money(max_dd / INITIAL_CAPITAL * 100),
        "avg_holding_calendar_days": _money(sum(
            (_day(t.exit_day) - _day(t.entry_day)).days for t in closed) / count) if count else None,
        "capital_occupancy_pct": _money(sum(row["market_value"] for row in curve) /
                                        (len(curve) * INITIAL_CAPITAL) * 100),
        "exit_reach_pct": {stage: _money(reaches[stage] / count * 100) if count else None
                           for stage in ("ATR_STOP", "BREAKEVEN", "TP1", "TP2", "TP3")},
        "fees_usd": _money(fees),
        "closed_trade_fees_usd": _money(fees_closed),
        "fees_pct_of_gross_profit": _money(fees_closed / gross_profit * 100)
        if gross_profit else None,
        "ambiguous_trade_days": sum(t.ambiguous_days for t in trades),
        "groups": grouped,
    })
    return main


def run_scenario(cfg: LiteConfig, bars: dict[str, list[Bar]], *,
                 scenario: str = "A", slippage_bps: int = 5,
                 fee_mode: str = "conservative", stop_first: bool = True,
                 start_day: date = START_DAY, end_day: date = END_DAY,
                 scan_cache: dict | None = None) -> tuple[list[Trade], list[dict], dict]:
    """One portfolio path, daily decisions at D open, range exits afterward."""
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    lookback, min_score = SCENARIOS[scenario]
    by_day = {s: {b.day: b for b in rows if _valid_bar(b)} for s, rows in bars.items()}
    all_trades: list[Trade] = []
    open_trades: dict[str, Trade] = {}
    marks: dict[str, float] = {}
    cash = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    curve: list[dict] = []
    signals_total = entries_total = 0
    loss_streak = 0

    def book(legs: list[dict]) -> None:
        nonlocal cash, loss_streak
        for leg in legs:
            cash += leg["qty"] * leg["price"] - leg["fee"]

    def equity_with(day_bars: dict[str, Bar], field_name: str) -> float:
        return cash + sum(t.qty_open * getattr(day_bars[t.symbol], field_name)
                          if t.symbol in day_bars else t.qty_open * marks.get(t.symbol, t.entry_price)
                          for t in open_trades.values())

    for day in _days(start_day, end_day):
        day_s = day.isoformat()
        today = {s: series[day_s] for s, series in by_day.items() if day_s in series}
        sod_equity = curve[-1]["equity"] if curve else INITIAL_CAPITAL
        # Existing open positions may gap through a stop before the day's
        # entry decision; no intraday high/low is inspected until after entry.
        cooldown_today = False
        for t in list(open_trades.values()):
            bar = today.get(t.symbol)
            if bar is None and any(s in today for s in cfg.tradable_symbols):
                # A symbol-specific gap makes this position's subsequent stop
                # path unknowable.  Censor it; carry its last valid close only
                # as an accounting mark, never count it as a strategy exit.
                mark = marks.get(t.symbol, t.entry_price)
                cash += t.qty_open * mark
                t.censor_day = day_s
                t.censor_mark = mark
                t.censor_qty = t.qty_open
                t.qty_open = 0
                del open_trades[t.symbol]
                continue
            if bar:
                book(process_daily_bar(cfg, t, bar, fee_mode=fee_mode, phase="open"))
            if t.qty_open == 0:
                del open_trades[t.symbol]
                loss_streak = _next_loss_streak(loss_streak, t.net_pnl)
                if loss_streak >= cfg.max_consecutive_losses:
                    cooldown_today = True
                    loss_streak = 0
        open_equity = equity_with(today, "open")
        daily_stop = sod_equity - open_equity >= daily_loss_limit(cfg, sod_equity)
        if scan_cache is not None:
            signals, _results, vix = scan_cache[scenario][day_s]
        else:
            signals, _results, vix = signal_for_day(
                cfg, bars, day, lookback=lookback, min_score=min_score)
        signals_total += len(signals)
        day_entries = 0
        # One daily decision is an explicit simplification of the two live
        # scans. There can be no later same-day entry after a loss/cooldown.
        for signal in signals:
            if daily_stop or cooldown_today or day_entries >= cfg.max_daily_entries:
                break
            if signal.symbol not in cfg.tradable_symbols or signal.symbol in cfg.protected_symbols:
                continue
            bar = today.get(signal.symbol)
            if bar is None or signal.symbol in open_trades:
                continue
            if len(open_trades) >= cfg.max_positions:
                continue
            if sum(cfg.sector_of(t.symbol) == cfg.sector_of(signal.symbol)
                   for t in open_trades.values()) >= cfg.max_per_sector:
                continue
            if not entry_allowed_by_drift(signal.price, bar.open):
                continue
            entry_price = round(bar.open * (1 + slippage_bps / 10000), 4)
            limit = buy_limit(bar.open, cfg.entry_limit_band_pct)
            if limit is None or entry_price > limit:
                continue
            deployed = sum(t.qty_open * t.entry_price for t in open_trades.values())
            equity_for_size = min(open_equity, cfg.max_capital_usd)
            size = size_entry(cfg, equity=equity_for_size, price=limit,
                              atr=signal.atr, deployed_notional=deployed)
            qty = size.quantity
            while qty > 0 and qty * entry_price + fee_for(qty, entry_price, fee_mode) > cash:
                qty -= 1
            if qty < 1:
                continue
            try:
                levels = compute_levels(cfg, entry_price, signal.atr)
            except ValueError:
                continue
            fee = fee_for(qty, entry_price, fee_mode)
            trade = Trade(f"{scenario}-{len(all_trades)+1:05d}", signal.symbol,
                          _prior_trading_day(day).isoformat(), day_s, entry_price,
                          signal.atr, qty, signal.score, vix, levels, fee,
                          atr_stop_mult=cfg.atr_stop_mult)
            all_trades.append(trade)
            open_trades[signal.symbol] = trade
            cash -= qty * entry_price + fee
            day_entries += 1
            entries_total += 1
            open_equity -= fee
        for t in list(open_trades.values()):
            bar = today.get(t.symbol)
            if bar:
                book(process_daily_bar(cfg, t, bar, stop_first=stop_first,
                                       fee_mode=fee_mode, phase="range"))
            if t.qty_open == 0:
                del open_trades[t.symbol]
                loss_streak = _next_loss_streak(loss_streak, t.net_pnl)
                if loss_streak >= cfg.max_consecutive_losses:
                    loss_streak = 0
        for symbol, bar in today.items():
            marks[symbol] = bar.close
        market_value = sum(t.qty_open * marks.get(t.symbol, t.entry_price)
                           for t in open_trades.values())
        equity = cash + market_value
        peak = max(peak, equity)
        curve.append({"day": day_s, "cash": _money(cash),
                      "market_value": _money(market_value), "equity": _money(equity),
                      "drawdown_usd": _money(peak - equity),
                      "open_positions": len(open_trades), "signals": len(signals),
                      "entries": day_entries, "marks": marks.copy()})
    return all_trades, curve, summarize(all_trades, curve, signals_total, entries_total)


def build_scan_cache(cfg: LiteConfig, bars: dict[str, list[Bar]], *,
                     start_day: date = START_DAY, end_day: date = END_DAY) -> dict:
    cache = {s: {} for s in SCENARIOS}
    for day in _days(start_day, end_day):
        for scenario, (lookback, min_score) in SCENARIOS.items():
            cache[scenario][day.isoformat()] = signal_for_day(
                cfg, bars, day, lookback=lookback, min_score=min_score)
    return cache


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline BT-1; DB read-only immutable")
    ap.add_argument("--database", type=Path, default=SOURCE_DB)
    ap.add_argument("--output", type=Path, default=OUTPUT)
    args = ap.parse_args()
    cfg = load_config(CONFIG)
    if cfg.max_capital_usd != INITIAL_CAPITAL:
        raise ValueError("BT-1 capital mismatch with paper.toml")
    bars = load_bars(args.database, cfg.tradable_symbols + ("^VIX",))
    audit = audit_data(bars, args.database)
    cache = build_scan_cache(cfg, bars)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "data_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    summaries = {}
    for scenario in SCENARIOS:
        trades, curve, summary = run_scenario(cfg, bars, scenario=scenario, scan_cache=cache)
        last_marks = curve[-1]["marks"]
        write_csv(args.output / f"{scenario}_trades.csv", TRADE_COLUMNS,
                  [trade_row(t, last_marks.get(t.symbol)) for t in trades])
        write_csv(args.output / f"{scenario}_equity.csv", EQUITY_COLUMNS, curve)
        summaries[scenario] = summary
    write_csv(args.output / "A_signals.csv",
              ("day", "symbol", "score", "signal_price", "atr", "vix"),
              [{"day": day, "symbol": signal.symbol, "score": signal.score,
                "signal_price": signal.price, "atr": signal.atr,
                "vix": "" if vix is None else vix}
               for day, (signals, _results, vix) in cache["A"].items()
               for signal in signals])
    # Explicit counterfactuals for the baseline.  Their full paths are
    # retained as JSON summaries; only A-D are required as detailed CSVs.
    sensitivity = {}
    for label, overrides in (
            ("slip_0bps", {"slippage_bps": 0}),
            ("slip_10bps", {"slippage_bps": 10}),
            ("fee_zero", {"fee_mode": "zero"}),
            ("fee_paper", {"fee_mode": "paper"}),
            ("tp_first", {"stop_first": False})):
        _trades, _curve, summary = run_scenario(
            cfg, bars, scenario="A", scan_cache=cache, **overrides)
        sensitivity[label] = summary
    output = {"scenarios": summaries, "sensitivity": sensitivity,
              "source_db_sha256": audit["db_sha256"]}
    (args.output / "summary.json").write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({s: {k: summaries[s][k] for k in (
        "trades", "signals_total", "entries_total", "total_pnl_usd_including_open_mtm")}
                      for s in SCENARIOS}, sort_keys=True))


if __name__ == "__main__":
    main()

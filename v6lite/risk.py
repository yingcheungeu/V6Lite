"""Entry risk gate and daily-loss evaluation.

The gate only ever blocks NEW risk. Exits are governed by the engine's exit
path, which stays available under kill switch, daily stop and cooldown.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from v6lite.config import LiteConfig
from v6lite.commissions import fee_breakdown, effective_cooldown
from v6lite.market_calendar import et_time_on, session_for, trading_day
from v6lite.models import Decision, Quote
from v6lite.sizing import estimate_fee
from v6lite.store import Store


@dataclass
class RuntimeFlags:
    """Non-durable conditions the engine refreshes every tick."""
    broker_connected: bool = False
    reconcile_ok: bool = False
    # symbol -> fresh-read sequence at which the exit path saw broker !=
    # ledger; entries stay blocked until a reconcile with a NEWER read
    quantity_suspect: dict = field(default_factory=dict)
    # working combo orders with an unresolved leg (last fresh order list):
    # they block every SELL, so no new position may be opened meanwhile
    unresolved_combos: list = field(default_factory=list)
    reconcile_detail: list[str] = field(default_factory=list)
    alerts_healthy: bool = False
    unpriced_positions: list[str] = field(default_factory=list)
    equity: float | None = None
    approval_error: str | None = None     # live only: approval missing/expired/revoked
    day_risk_error: str | None = None     # daily loss cannot be computed (e.g. prior close unknown)


def daily_loss_limit(cfg: LiteConfig, sod_equity: float) -> float:
    return min(cfg.daily_loss_limit_usd, cfg.daily_loss_limit_pct * sod_equity)


@dataclass(frozen=True)
class DayPnl:
    pnl: float | None                                  # None = cannot be computed right now
    missing_prior_close: tuple[str, ...] = ()
    unpriced: tuple[str, ...] = ()


def day_pnl(store: Store, day: str, *, marks: dict[str, float | None],
            prior_close: dict[str, float | None]) -> DayPnl:
    """Today's P&L of the managed book, derived from the ledger every time.

    Per position, share lots are valued from a reference price:
      * shares held at the prior close  -> prior close
        (held = open now - bought today + sold today)
      * shares bought today             -> their fill price
    (v1 has no adopt. Released positions are excluded; a release freezes
    entries for the day.)
    Today's sells consume lots first-in-first-out; remaining lots are marked
    at the current price. Commissions of every fill booked today are
    subtracted. Nothing is stored, so later sells, late fills, repairs or
    restarts cannot freeze a wrong baseline.
    """
    total = 0.0
    missing: list[str] = []
    unpriced: list[str] = []
    for pos in store.positions_for_day(day):
        fills = store.position_fills(pos["position_id"])
        today = [f for f in fills if f["trading_day"] == day]
        bought_today = sum(f["quantity"] for f in today if f["side"] == "BUY")
        sold_today = sum(f["quantity"] for f in today if f["side"] == "SELL")
        carried = max(0, pos["qty_open"] - bought_today + sold_today)
        lots: list[list[float]] = []
        if carried:
            close = prior_close.get(pos["symbol"])
            if close is None:
                missing.append(pos["symbol"])
                continue
            lots.append([carried, close])
        pnl = 0.0
        for f in today:
            if f["side"] == "BUY":
                lots.append([f["quantity"], f["price"]])
                continue
            left = f["quantity"]
            while left > 0 and lots:
                take = min(left, lots[0][0])
                pnl += (f["price"] - lots[0][1]) * take
                lots[0][0] -= take
                left -= take
                if lots[0][0] <= 0:
                    lots.pop(0)
            if left > 0:  # sold more than the lots explain: value against cost
                pnl += (f["price"] - pos["entry_price"]) * left
        still_open = sum(q for q, _ in lots)
        if still_open > 0:
            mark = marks.get(pos["symbol"])
            if mark is None:
                unpriced.append(pos["symbol"])
                continue
            pnl += sum((mark - ref) * q for q, ref in lots)
        total += pnl
    if missing or unpriced:
        return DayPnl(None, tuple(sorted(set(missing))), tuple(sorted(set(unpriced))))
    # reported commissions, plus the conservative estimate for any not reported yet
    estimated = sum(estimate_fee(q, p) for q, p in store.fills_missing_commission(day))
    return DayPnl(total - store.fees_for_day(day) - estimated)


@dataclass(frozen=True)
class LossState:
    streak: int
    cooldown_until: datetime | None
    trades: int
    trigger_trades: tuple[dict, ...] = ()
    derived_cooldown_until: datetime | None = None


def trade_net(trade: dict, store: Store | None = None, cfg: LiteConfig | None = None) -> float:
    """Reported net less frozen/history-based missing commissions; not sizing headroom."""
    return fee_breakdown(trade, store, cfg)["estimated_net"]


def loss_state(store: Store, cfg: LiteConfig) -> LossState:
    """Execution-time ordered loss rule; triggered deadlines survive fee corrections.

    Final fees still determine the derived loss streak, including a newly
    proven loss. A prior triggered cooldown is never automatically released.
    """
    streak = 0
    until = None
    trigger = ()
    losses = []
    trades = store.closed_trades()
    events = []
    for trade in trades:
        fees = fee_breakdown(trade, store, cfg)
        events.append((trade["closed_at"],fees["estimated_net"],{
            "position_id":trade["position_id"],"net":fees["estimated_net"],
            "estimated":bool(fees["missing_count"]),"estimated_fees":fees["estimated_fees"]}))
    events += [(at,-1.0,{"position_id":"operator-release@"+at.isoformat(),"net":-1.0,
                        "estimated":False,"source":"released position counts as loss"})
               for at in store.release_times()]
    events.sort(key=lambda e:e[0])
    for at,net,basis in events:
        if net < 0:
            streak += 1
            losses.append(basis)
            if streak >= cfg.max_consecutive_losses:
                until = at + timedelta(minutes=cfg.loss_cooldown_minutes)
                trigger = tuple(losses)
                streak = 0
                losses = []
        elif net > 0:
            streak = 0
            losses = []
    return LossState(streak,effective_cooldown(store,until),len(trades),trigger,until)


def cooldown_until(store: Store, cfg: LiteConfig) -> datetime | None:
    return loss_state(store, cfg).cooldown_until


def entry_window_open(cfg: LiteConfig, now: datetime) -> bool:
    session = session_for(trading_day(now))
    if session is None:
        return False
    start = max(session.open_utc, et_time_on(session.day, cfg.entry_window_start_et))
    end = session.close_utc - timedelta(minutes=cfg.entry_cutoff_minutes_before_close)
    return start <= now < end


def global_entry_blocks(cfg: LiteConfig, store: Store, flags: RuntimeFlags, now: datetime) -> list[str]:
    """Reasons that block ALL new entries right now (empty list = clear)."""
    reasons: list[str] = []
    kill = store.kill_state()
    if kill:
        reasons.append(f"kill_switch:{kill.get('reason', '')}")
    day = trading_day(now).isoformat()
    row = store.risk_day(day)
    if row is None:
        reasons.append("risk_day_not_initialized")
    else:
        if row["daily_stop_latched"]:
            reasons.append("daily_loss_stop_latched")
        if row["entries_submitted"] >= cfg.max_daily_entries:
            reasons.append("max_daily_entries")
    if flags.day_risk_error:
        reasons.append(f"daily_loss_baseline_unknown:{flags.day_risk_error}")
    until = cooldown_until(store, cfg)
    if until and now < until:
        reasons.append(f"loss_cooldown_until:{until.isoformat()}")
    for key, detail in store.anomalies().items():
        reasons.append(f"{key}:{detail}")
    frozen = sorted(k.split(":", 1)[1] for k in store.controls_with_prefix("frozen:"))
    if frozen:
        reasons.append("frozen_symbols:" + ",".join(frozen))    # cleared only by `release`
    if flags.unresolved_combos:
        reasons.append("unresolved_combo_order:" + ",".join(flags.unresolved_combos))
    if flags.quantity_suspect:
        reasons.append("quantity_difference:" + ",".join(sorted(flags.quantity_suspect)))
    unknown = [r["intent_id"] for r in store.active_intents() if r["status"] == "UNKNOWN" or r["hold_reason"]]
    if unknown:
        reasons.append(f"unknown_order_state:{','.join(unknown)}")
    if not flags.broker_connected:
        reasons.append("broker_disconnected")
    if not flags.reconcile_ok:
        reasons.append("reconcile:" + ";".join(flags.reconcile_detail or ["not_run"]))
    if cfg.is_live and not flags.alerts_healthy:
        reasons.append("alerts_unavailable")          # not bypassable in live
    if cfg.is_live and flags.approval_error:
        reasons.append(f"live_approval:{flags.approval_error}")
    if flags.unpriced_positions:
        reasons.append("unpriced_positions:" + ",".join(flags.unpriced_positions))
    if flags.equity is None:
        reasons.append("equity_unavailable")
    if not entry_window_open(cfg, now):
        reasons.append("outside_entry_window")
    return reasons


def check_symbol_entry(cfg: LiteConfig, store: Store, *, symbol: str, sector_counts: dict[str, int],
                       open_count: int, ignored: set[str]) -> Decision:
    decision = Decision(True)
    sym = symbol.upper()
    if sym not in cfg.tradable_symbols:
        decision.block("not_tradable")
    if sym in cfg.protected_symbols or sym in ignored:
        decision.block("protected_or_ignored")
    if any(r["symbol"] == sym for r in store.open_positions()):
        decision.block("already_open")
    if any(r["symbol"] == sym for r in store.active_intents()):
        decision.block("order_in_flight")
    if open_count >= cfg.max_positions:
        decision.block("max_positions")
    if sector_counts.get(cfg.sector_of(sym), 0) >= cfg.max_per_sector:
        decision.block("sector_cap")
    return decision


EXIT_QUOTE_AGE_MULTIPLIER = 8  # exits tolerate quieter quotes (15s -> 120s) rather than skip a stop


def check_quote(cfg: LiteConfig, quote: Quote | None, now: datetime, *, require_bid_ask: bool,
                max_age_seconds: float | None = None) -> Decision:
    decision = Decision(True)
    if quote is None:
        return decision.block("no_quote")
    # entries price from the book (bid/ask); exits from mark(): judge the age
    # of exactly the data the decision uses
    age = quote.age_seconds(now) if require_bid_ask else quote.mark_age_seconds(now)
    limit = cfg.max_quote_age_seconds if max_age_seconds is None else max_age_seconds
    if age is None or age > limit:
        decision.block("stale_quote")
    if require_bid_ask:
        if not quote.valid_bid_ask():
            decision.block("invalid_bid_ask")
        else:
            spread = quote.spread_pct()
            if spread is None or not math.isfinite(spread) or spread > cfg.max_spread_pct:
                decision.block("spread_too_wide")
    elif quote.mark() is None:
        decision.block("no_price")
    return decision


def describe(reasons: list[str]) -> str:
    return json.dumps(reasons, ensure_ascii=False)

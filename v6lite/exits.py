"""Exit plan (ATR stop + three take-profit stages), ported from V6 TPSL.

Rules (unchanged from trading_bot_v6/tpsl):
  STOP = entry - 2.5*ATR  (after TP1: breakeven stop = entry * 1.0005)
  TP1  = entry + 3*ATR -> sell 1/3 of initial quantity
  TP2  = entry + 5*ATR -> sell 1/3 of initial quantity
  TP3  = entry + 8*ATR -> sell everything left

Persisted-plan priority (V6 ruling 2026-07-27): levels are computed once from
the entry fill and stored; they are never recomputed from fresh market data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal

from v6lite.config import LiteConfig
from v6lite.models import Purpose


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Levels:
    stop: float
    tp1: float
    tp2: float
    tp3: float


def compute_levels(cfg: LiteConfig, entry: float, atr: float) -> Levels:
    if not (math.isfinite(entry) and math.isfinite(atr) and entry > 0 and atr > 0):
        raise PlanError("non_finite_or_non_positive_entry_or_atr")
    stop, tp1, tp2, tp3 = ((cfg.test.stop_mult, cfg.test.tp1_mult, cfg.test.tp2_mult,
                            cfg.test.tp3_mult) if cfg.test.enabled else
                           (cfg.atr_stop_mult, cfg.tp1_mult, cfg.tp2_mult, cfg.tp3_mult))
    levels = Levels(
        stop=round(entry - stop * atr, 2),
        tp1=round(entry + tp1 * atr, 2),
        tp2=round(entry + tp2 * atr, 2),
        tp3=round(entry + tp3 * atr, 2),
    )
    if not (0 < levels.stop < entry < levels.tp1 < levels.tp2 < levels.tp3):
        raise PlanError("invalid_level_order")
    return levels


def breakeven_stop(cfg: LiteConfig, entry: float) -> float:
    return round(entry * (1 + cfg.breakeven_offset_pct), 2)


@dataclass(frozen=True)
class PositionView:
    position_id: str
    symbol: str
    entry_price: float
    qty_initial: int
    qty_open: int
    stop: float
    tp1: float
    tp2: float
    tp3: float
    tp1_done: bool
    tp2_done: bool
    breakeven_stop: float | None


@dataclass(frozen=True)
class ExitDecision:
    stage: Purpose
    quantity: int
    trigger_level: float
    reason: str


def effective_stop(pos: PositionView) -> float:
    return pos.breakeven_stop if (pos.tp1_done and pos.breakeven_stop) else pos.stop


def third(pos: PositionView) -> int:
    return max(1, pos.qty_initial // 3)


def decide_exit(pos: PositionView, price: float) -> ExitDecision | None:
    if pos.qty_open <= 0 or not math.isfinite(price) or price <= 0:
        return None
    stop_level = effective_stop(pos)
    if price <= stop_level:
        reason = "breakeven_stop" if pos.tp1_done else "atr_stop"
        return ExitDecision(Purpose.STOP, pos.qty_open, stop_level, reason)
    if pos.tp1_done and pos.tp2_done and price >= pos.tp3:
        return ExitDecision(Purpose.TP3, pos.qty_open, pos.tp3, "tp3")
    if pos.tp1_done and not pos.tp2_done and price >= pos.tp2:
        return ExitDecision(Purpose.TP2, min(third(pos), pos.qty_open), pos.tp2, "tp2")
    if not pos.tp1_done and price >= pos.tp1:
        return ExitDecision(Purpose.TP1, min(third(pos), pos.qty_open), pos.tp1, "tp1")
    return None


def sell_limit(reference_price: float, band: float) -> float | None:
    """Protected SELL limit = reference * (1 - band), rounded UP to the cent
    (same rule as trading_bot_v6.tpsl.limit_exit.derive_tpsl_sell_limit)."""
    if not math.isfinite(reference_price) or reference_price <= 0 or not 0 < band < 1:
        return None
    value = (Decimal(str(reference_price)) * (Decimal(1) - Decimal(str(band)))).quantize(
        Decimal("0.01"), rounding=ROUND_UP)
    return float(value) if value > 0 else None


def buy_limit(reference_price: float, band: float) -> float | None:
    if not math.isfinite(reference_price) or reference_price <= 0 or not 0 < band < 1:
        return None
    return round(reference_price * (1 + band), 2)

"""Risk-based position sizing. Fails closed: never rounds a zero size up to 1.

(V6 review finding: a silent 1-share floor let undersized signals trade.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from v6lite.config import LiteConfig


FEE_RESERVE_USD = 2.0          # floor of the per-order fee estimate
FEE_SAFETY_FACTOR = 1.5        # head-room for exchange / pass-through fees


def estimate_fee(quantity: int, price: float) -> float:
    """Conservative per-order commission estimate (USD).

    IBKR Pro Fixed (US stocks): USD 0.005/share, min USD 1.00, max 1% of trade
    value, plus regulatory fees on sales (SEC 0.0000206 x value, FINRA TAF
    0.000195 x shares). Multiplied by FEE_SAFETY_FACTOR, never below
    FEE_RESERVE_USD. Used for the funds check, sizing head-room and for
    commissions that have not been reported yet. To be checked against real
    commission reports (G2/G3); it is an estimate, not a guarantee.
    """
    qty = max(0, int(quantity))
    notional = max(0.0, float(price)) * qty
    commission = min(max(1.0, 0.005 * qty), 0.01 * notional) if qty else 0.0
    regulatory = 0.0000206 * notional + 0.000195 * qty
    return max(FEE_RESERVE_USD, (commission + regulatory) * FEE_SAFETY_FACTOR)


@dataclass(frozen=True)
class SizeResult:
    quantity: int
    reason: str
    risk_usd: float = 0.0
    notional_usd: float = 0.0


def sizing_equity(cfg: LiteConfig, net_liquidation: float | None) -> float | None:
    """Capital used for sizing = min(NLV, max_capital_usd). None if NLV unusable."""
    if net_liquidation is None or not math.isfinite(net_liquidation) or net_liquidation <= 0:
        return None
    return min(float(net_liquidation), float(cfg.max_capital_usd))


def size_entry(cfg: LiteConfig, *, equity: float, price: float, atr: float,
               deployed_notional: float) -> SizeResult:
    for name, value in (("equity", equity), ("price", price), ("atr", atr)):
        if not math.isfinite(value) or value <= 0:
            return SizeResult(0, f"invalid_{name}")
    per_share_risk = atr * (cfg.test.stop_mult if cfg.test.enabled else cfg.atr_stop_mult)
    risk_budget = equity * cfg.risk_per_trade_pct
    qty_risk = math.floor(risk_budget / per_share_risk)
    qty_position = math.floor(equity * cfg.max_position_pct / price)
    qty_order = math.floor(cfg.max_order_notional_usd / price)
    # capital left = sizing equity (already min(NLV, cap)) minus deployed and
    # in-flight notional, minus the fee estimate; never the static cap alone
    remaining_capital = equity - max(0.0, deployed_notional)
    qty_capital = math.floor(max(0.0, remaining_capital - FEE_RESERVE_USD) / price)
    while qty_capital > 0 and qty_capital * price + estimate_fee(qty_capital, price) > remaining_capital:
        qty_capital -= 1
    qty = min(qty_risk, qty_position, qty_order, qty_capital)
    if qty < 1:
        limiting = min(
            (("risk_budget", qty_risk), ("position_cap", qty_position),
             ("order_notional_cap", qty_order), ("capital_cap", qty_capital)),
            key=lambda item: item[1],
        )[0]
        return SizeResult(0, f"size_below_one_share:{limiting}")
    return SizeResult(qty, "ok", risk_usd=qty * per_share_risk, notional_usd=qty * price)

"""Cent-tick broker STP price; acknowledgement is not execution evidence."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

def protection_prices(trigger_price: float) -> tuple[float, None]:
    """Round SELL stop down to cents; STP has no limit price or price floor."""
    trigger = Decimal(str(trigger_price))
    if not trigger.is_finite() or trigger < 1:
        raise ValueError("broker protection requires stock price >= $1")
    return float(trigger.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)), None


def same_cent_price(actual, expected) -> bool:
    """Compare positive prices at cent precision without copying broker tails."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    try:
        left, right = Decimal(str(actual)), Decimal(str(expected))
        return bool(left.is_finite() and right.is_finite() and left > 0 and right > 0 and
                    left.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) ==
                    right.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return False

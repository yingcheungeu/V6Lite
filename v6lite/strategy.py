"""Technical scanner strategy (faithful port of market_scanner.scan_ticker).

The indicator math and scoring rules are copied unchanged from
market_scanner.py so V6-Lite trades the same edge that V6 paper-traded.
Differences are deliberate and limited to data handling:

* bars are injected (IBKR daily bars) instead of fetched from yfinance inside
  the function, so the strategy is pure and testable;
* non-finite OHLCV rows fail closed (bounded drop of <= 3 rows, as in
  tpsl/atr_data_quality.py), a lesson from incident ATR-NAN-1;
* a missing VIX reading uses the elevated (stricter) score threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from v6lite.models import Bar, Signal

MIN_BARS = 30
MAX_DROPPED_ROWS = 3


# ---------------- indicator math: copied verbatim from market_scanner.py ----
def _ema(data: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    result = [data[0]]
    for v in data[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _atr(high: list, low: list, close: list, period: int = 14) -> float:
    tr = []
    for i in range(1, len(close)):
        tr.append(max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1]),
        ))
    if len(tr) < period:
        return 0.0
    atr_vals = [sum(tr[:period]) / period]
    for t in tr[period:]:
        atr_vals.append((atr_vals[-1] * (period - 1) + t) / period)
    return atr_vals[-1]


def _adx(high: list, low: list, close: list, period: int = 14) -> float:
    if len(close) < period * 2:
        return 0.0
    dm_plus, dm_minus, tr_list = [], [], []
    for i in range(1, len(close)):
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        dm_plus.append(up if up > down and up > 0 else 0)
        dm_minus.append(down if down > up and down > 0 else 0)
        tr_list.append(max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1]),
        ))

    def smooth(data):
        s = [sum(data[:period])]
        for v in data[period:]:
            s.append(s[-1] - s[-1] / period + v)
        return s
    str_ = smooth(tr_list)
    sdp = smooth(dm_plus)
    sdm = smooth(dm_minus)
    di_p = [100 * p / t if t else 0 for p, t in zip(sdp, str_)]
    di_m = [100 * m / t if t else 0 for m, t in zip(sdm, str_)]
    dx = [abs(p - m) / (p + m) * 100 if (p + m) else 0 for p, m in zip(di_p, di_m)]
    if len(dx) < period:
        return 0.0
    adx_vals = [sum(dx[:period]) / period]
    for v in dx[period:]:
        adx_vals.append((adx_vals[-1] * (period - 1) + v) / period)
    return adx_vals[-1]


def _rsi(close: list, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    deltas = [close[i] - close[i-1] for i in range(1, len(close))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
    return 100 - 100 / (1 + ag / al) if al else 100.0


# ---------------- scoring -------------------------------------------------
@dataclass
class ScanResult:
    symbol: str
    price: float = 0.0
    ema9: float = 0.0
    ema21: float = 0.0
    ema50: float = 0.0
    adx: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    volume_ratio: float = 0.0
    score: int = 0
    signal: str = "NEUTRAL"
    reasons: list = field(default_factory=list)
    error: str = ""


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def clean_bars(bars: list[Bar]) -> tuple[list[Bar], str]:
    """Drop at most MAX_DROPPED_ROWS non-finite rows; otherwise fail closed."""
    bad = [i for i, b in enumerate(bars)
           if not all(_finite(v) for v in (b.high, b.low, b.close, b.volume))]
    if len(bad) > MAX_DROPPED_ROWS:
        return [], f"non_finite_rows:{len(bad)}"
    kept = [b for i, b in enumerate(bars) if i not in set(bad)]
    if len(kept) < MIN_BARS:
        return [], f"insufficient_bars:{len(kept)}"
    return kept, "ok" if not bad else f"dropped_rows:{len(bad)}"


def score_symbol(symbol: str, bars: list[Bar], *, is_etf: bool) -> ScanResult:
    """Port of market_scanner.scan_ticker scoring (non-VIX branch)."""
    result = ScanResult(symbol=symbol)
    kept, status = clean_bars(bars)
    if not kept:
        result.error = status
        return result

    close = [b.close for b in kept]
    high = [b.high for b in kept]
    low = [b.low for b in kept]
    volume = [b.volume for b in kept]

    ema9_arr = _ema(close, 9)
    ema21_arr = _ema(close, 21)
    ema50_arr = _ema(close, 50)

    result.price = round(close[-1], 2)
    result.ema9 = round(ema9_arr[-1], 2)
    result.ema21 = round(ema21_arr[-1], 2)
    result.ema50 = round(ema50_arr[-1], 2)
    result.atr = round(_atr(high, low, close), 2)
    result.adx = round(_adx(high, low, close), 1)
    result.rsi = round(_rsi(close), 1)

    if len(volume) >= 21:
        avg_vol_20d = sum(volume[-21:-1]) / 20
        prev_day_vol = volume[-2]
        result.volume_ratio = round(prev_day_vol / avg_vol_20d, 2) if avg_vol_20d else 1.0
    else:
        avg_vol = sum(volume[-20:]) / min(len(volume), 20)
        result.volume_ratio = round(volume[-1] / avg_vol, 2) if avg_vol else 1.0

    score = 0
    reasons: list[str] = []
    ema_golden = result.ema9 > result.ema21
    ema_trend = result.ema21 > result.ema50
    price_above = result.price > result.ema50

    if ema_golden:
        score += 20
        reasons.append("ema9>ema21")
    else:
        if is_etf:
            score += 5
            reasons.append("ema_death_cross_etf_tolerated")
        else:
            reasons.append("ema_death_cross")
    if ema_trend:
        score += 10
        reasons.append("ema21>ema50")
    if price_above:
        score += 10
        reasons.append("price>ema50")

    if result.adx >= 35:
        score += 25
    elif result.adx >= 25:
        score += 18
    elif result.adx >= 20:
        score += 10
    reasons.append(f"adx={result.adx}")

    if 50 <= result.rsi < 65:
        score += 20
    elif 65 <= result.rsi < 70:
        score += 12
    elif 40 <= result.rsi < 50:
        score += 8
    elif 70 <= result.rsi < 75:
        score += 5
    reasons.append(f"rsi={result.rsi}")

    if result.volume_ratio >= 2.0:
        score += 10
    elif result.volume_ratio >= 1.5:
        score += 5
    reasons.append(f"vol_ratio={result.volume_ratio}")

    result.score = min(score, 100)
    result.reasons = reasons

    hard_conditions = result.adx >= 20 and 40 <= result.rsi <= 75
    if not is_etf:
        hard_conditions = hard_conditions and ema_golden

    if result.score >= 60 and hard_conditions:
        result.signal = "BUY"
    elif result.rsi > 75 or (result.adx < 15 and not is_etf):
        result.signal = "AVOID"
    else:
        result.signal = "NEUTRAL"
    return result


def effective_min_score(vix_close: float | None, *, min_score: int,
                        high_vix_threshold: float, high_vix_min_score: int) -> int:
    """VIX regime gate (the only regime logic live in V6). Unknown VIX -> strict."""
    if vix_close is None or not _finite(vix_close):
        return max(min_score, high_vix_min_score)
    if vix_close > high_vix_threshold:
        return max(min_score, high_vix_min_score)
    return min_score


def generate_signals(bars_by_symbol: dict[str, list[Bar]], *, etf_symbols, min_score: int
                     ) -> tuple[list[Signal], list[ScanResult]]:
    results: list[ScanResult] = []
    signals: list[Signal] = []
    etfs = set(etf_symbols)
    for symbol, bars in bars_by_symbol.items():
        res = score_symbol(symbol, bars, is_etf=symbol in etfs)
        results.append(res)
        if res.signal == "BUY" and res.score >= min_score and res.atr > 0 and res.price > 0:
            signals.append(Signal(symbol, res.score, res.price, res.atr, tuple(res.reasons)))
    signals.sort(key=lambda s: (-s.score, s.symbol))
    return signals, results

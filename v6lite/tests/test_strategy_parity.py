"""Reproducible parity check: V6-Lite scoring vs legacy market_scanner.scan_ticker.

The legacy function is extracted from ../market_scanner.py with `ast` (no
Discord / yfinance / trading_bot_v6 imports are executed) and fed the SAME
bars through a stub `yfinance.Ticker`. Fixed seed -> identical cases on every
machine. Skipped when the legacy file is not present.

This proves algorithm parity for identical input only. It does not prove that
yfinance and IBKR daily bars are identical (session dates, adjustments,
partial current-day bar, volume units) -- that is checked in the read-only
Gateway phase (G2).
"""

from __future__ import annotations

import ast
import logging
import os
import random
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from v6lite.models import Bar
from v6lite.strategy import score_symbol

LEGACY = Path(os.environ.get("V6LITE_LEGACY_SCANNER", Path(__file__).resolve().parents[2] / "market_scanner.py"))
ETFS = {"SPY", "IWM", "IBB"}
CASES = 1000
SEED = 20260930


class _Column:
    def __init__(self, values):
        self.values = self
        self._values = values

    def tolist(self):
        return list(self._values)


class _Frame:
    def __init__(self, bars):
        self._bars = bars

    def __len__(self):
        return len(self._bars)

    def __getitem__(self, name):
        attr = {"Close": "close", "High": "high", "Low": "low", "Volume": "volume"}[name]
        return _Column([getattr(b, attr) for b in self._bars])


def _load_legacy_scan_ticker(current: dict):
    tree = ast.parse(LEGACY.read_text(encoding="utf-8"))
    wanted = {"_ema", "_atr", "_adx", "_rsi", "scan_ticker", "ScanResult"}
    module = ast.Module(body=[n for n in tree.body if getattr(n, "name", None) in wanted], type_ignores=[])
    namespace = {
        "dataclass": dataclass, "field": field, "logger": logging.getLogger("legacy"),
        "get_source_pool": lambda s: "X", "is_observation_only_symbol": lambda s: False,
        "ETF_TICKERS": ETFS, "Optional": object,
    }
    exec(compile("from __future__ import annotations", "legacy", "exec"), namespace)
    exec(compile(module, str(LEGACY), "exec"), namespace)

    class Ticker:
        def __init__(self, symbol):
            pass

        def history(self, period, interval):
            return _Frame(current["bars"])

    return namespace["scan_ticker"], types.SimpleNamespace(Ticker=Ticker)


@pytest.mark.skipif(not LEGACY.exists(), reason="legacy market_scanner.py not present")
def test_scoring_matches_legacy_scanner_on_identical_bars(monkeypatch):
    current: dict = {}
    scan_ticker, fake_yf = _load_legacy_scan_ticker(current)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    rng = random.Random(SEED)
    buys = mismatches = 0
    first_mismatch = None
    for _ in range(CASES):
        n = rng.randint(30, 90)
        base = rng.uniform(20, 500)
        drift = rng.uniform(-0.8, 0.8)
        amp = rng.uniform(0.2, 4)
        price, bars = base, []
        for i in range(n):
            price = max(1.0, price + drift * base / 100 + rng.gauss(0, amp * base / 100))
            bars.append(Bar(str(i), price, price * (1 + rng.uniform(0, 0.02)),
                            price * (1 - rng.uniform(0, 0.02)), price, rng.uniform(5e5, 5e6)))
        symbol = rng.choice(["AAPL", "SPY", "TSLA", "IBB"])
        current["bars"] = bars
        legacy = scan_ticker(symbol, "x")
        lite = score_symbol(symbol, bars, is_etf=symbol in ETFS)
        fields = ("signal", "score", "atr", "adx", "rsi", "volume_ratio", "price")
        a = tuple(getattr(legacy, f) for f in fields)
        b = tuple(getattr(lite, f) for f in fields)
        buys += legacy.signal == "BUY"
        if a != b:
            mismatches += 1
            first_mismatch = first_mismatch or (symbol, a, b)
    assert mismatches == 0, f"{mismatches}/{CASES} mismatches, first: {first_mismatch}"
    assert buys > CASES // 20, "sample does not exercise BUY signals"

"""Shared test fixtures: deterministic clock, fake broker, temp state."""

from __future__ import annotations

import os

import math
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.broker.fake import FakeBroker
from v6lite.clock import FixedClock
from v6lite.config import AlertConfig, LiteConfig, ProtectionConfig, validate
from v6lite.engine import Engine
from v6lite.exits import compute_levels
from v6lite.models import Bar
from v6lite.store import Store

ET = ZoneInfo("America/New_York")
DAY = (2026, 10, 6)  # a regular Tuesday session


def et(hour: int, minute: int = 0, second: int = 0, day=DAY) -> datetime:
    return datetime(*day, hour, minute, second, tzinfo=ET).astimezone(timezone.utc)


def make_cfg(tmp_path: Path, **overrides) -> LiteConfig:
    # Existing Paper scenarios exercise their batch-14 order path.  Synthetic
    # Live fixtures must preserve the production fail-closed L7 requirement.
    # Dedicated L7 Paper tests opt in explicitly.
    overrides.setdefault("protection", ProtectionConfig(
        enabled=overrides.get("mode") == "live" or os.environ.get("V6LITE_TEST_PROTECTION") == "1"))
    tradable = ("AAPL", "SPY", "TSLA")
    base = LiteConfig(
        mode="paper", account="DU0000000", port=4002, client_id=131,
        scan_symbols=tradable, tradable_symbols=tradable, etf_symbols=("SPY",),
        protected_symbols=("MSTR",), sectors=(("AAPL", "tech"), ("SPY", "index"), ("TSLA", "consumer")),
        scan_times_et=("10:00",), state_dir=str(tmp_path / "state"),
        alerts=AlertConfig(email_to="ops@example.invalid", smtp_host="smtp.example.invalid"),
    )
    return validate(replace(base, **overrides))


def buy_bars(n: int = 70, base: float = 100.0, drift: float = 0.2, amp: float = 1.0) -> list[Bar]:
    """Synthetic uptrend that scores BUY 78 (price 113.13, ATR 2.0) with the ported scanner."""
    bars = []
    for i in range(n):
        c = base + drift * i + amp * math.sin(i * 0.9)
        bars.append(Bar(f"d{i}", c - 0.3, c + 1.0, c - 1.0, c, 1_000_000))
    return bars


def down_bars(n: int = 70) -> list[Bar]:
    return buy_bars(n=n, base=150.0, drift=-0.4, amp=1.0)


class Rig:
    def __init__(self, tmp_path: Path, *, start=None, dry_run=False, alerts_fail=False, approval_checker=None,
                 **cfg_overrides):
        self.cfg = make_cfg(tmp_path, **cfg_overrides)
        self.clock = FixedClock(start or et(9, 50))
        self.broker = FakeBroker(account=self.cfg.account, clock=self.clock, client_id=self.cfg.client_id)
        self.store = Store(self.cfg.resolved_state_dir() / "v6lite.sqlite3", partial(compute_levels, self.cfg))
        self.channel = MemoryChannel(fail=alerts_fail)
        self.alerter = Alerter([self.channel], synchronous=True)
        if dry_run:
            self.store.bind_identity(self.cfg.account, self.cfg.mode, role="dryrun",
                                     client_id=self.cfg.client_id)
        self.engine = Engine(self.cfg, self.broker, self.store, self.alerter, self.clock, dry_run=dry_run,
                             report_dir=tmp_path / "reports", approval_checker=approval_checker)
        self.broker.bars["AAPL"] = buy_bars()
        self.broker.bars["SPY"] = down_bars()
        self.broker.bars["TSLA"] = down_bars()
        self.px("AAPL", 113.13)
        self.px("SPY", 500.0)
        self.px("TSLA", 250.0)

    def px(self, symbol: str, price: float, spread: float = 0.06) -> None:
        self.broker.set_quote(symbol, bid=round(price - spread / 2, 2), ask=round(price + spread / 2, 2),
                              last=price, ts=self.clock.now())

    def refresh_quotes(self) -> None:
        for sym, q in list(self.broker.quotes.items()):
            self.broker.set_quote(sym, bid=q.bid, ask=q.ask, last=q.last, ts=self.clock.now())

    def at(self, when: datetime) -> None:
        self.clock.set(when)
        self.refresh_quotes()

    def advance(self, **kw) -> None:
        self.clock.advance(**kw)
        self.refresh_quotes()

    def tick(self) -> None:
        self.engine.tick()

    def start(self) -> None:
        self.engine.startup()
        self.tick()  # creates risk day + marks start-of-day unrealized

    def open_aapl(self) -> str:
        """Start, scan at 10:00 ET, fully fill the AAPL entry. Returns intent id."""
        self.start()
        self.at(et(10, 0, 30))
        self.tick()
        entry = self.broker.sent[-1]
        self.broker.fill(entry.intent_id)
        self.advance(seconds=1)
        self.tick()
        return entry.intent_id

    def messages(self) -> str:
        return "\n".join(s + " " + b for s, b in self.channel.messages)


def seed_closed_trade(store: Store, position_id: str, realized: float, closed_at: datetime,
                      symbol: str = "SPY") -> None:
    """A finished trade with no fills (all fees known = 0) -- history for the
    derived loss streak."""
    t = closed_at.isoformat()
    store.conn.execute(
        "INSERT INTO positions(position_id, symbol, status, source, entry_price, atr, qty_initial, qty_open, "
        "stop, tp1, tp2, tp3, realized_pnl, opened_at, closed_at) VALUES "
        "(?,?,'CLOSED','strategy',100,1,1,0,97.5,103,105,108,?,?,?)",
        (position_id, symbol, realized, t, t))

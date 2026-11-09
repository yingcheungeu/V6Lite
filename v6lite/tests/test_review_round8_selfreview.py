"""Batch-9 adversarial self-review: counterexamples found against the first
draft of batch 9, kept as regression tests asserting the SAFE behaviour.

A  a side that vanishes (IB price -1 / 0: halt, LULD, empty book) stops
   pricing at once; a size tick never re-certifies a vanished level
B  a fresh trade price is judged by its own receive time even when one book
   side is quiet; a SELL is then priced from that trade, not the stale bid
C  a refusal is a permanent floor: fields received before it never price a
   quote again, also after the status display cleared the refusal
D  a signal that cannot be entered anyway never costs the blocking
   working-order query; eligibility is checked again after the query
E  ticks of unknown request ids are ignored
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from v6lite.models import OrderRequest, Purpose, Quote, Side
from v6lite.risk import EXIT_QUOTE_AGE_MULTIPLIER, check_quote
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_review_round8 import _Clock, _book, adapter  # noqa: F401  (fixture)


def _exit_ok(broker, quote):
    age = broker.cfg.max_quote_age_seconds * EXIT_QUOTE_AGE_MULTIPLIER
    return check_quote(broker.cfg, quote, _Clock.t, require_bid_ask=False, max_age_seconds=age).allowed


def _entry_ok(broker, quote):
    return check_quote(broker.cfg, quote, _Clock.t, require_bid_ask=True).allowed


# ======================================================================= A
def test_A1_vanished_ask_never_prices_an_entry(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05)
    _Clock.t += timedelta(seconds=5)
    ib.wrapper.priceSizeTick(14, 2, -1.0, 0.0)          # IB: no offers any more
    q = broker.quote("AAPL")
    assert q is None or q.ask is None
    assert not _entry_ok(broker, q)


def test_A2_size_tick_never_recertifies_a_vanished_level(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05)
    _Clock.t += timedelta(seconds=60)
    ib.wrapper.priceSizeTick(14, 2, -1.0, 0.0)          # ask gone
    ib.wrapper.priceSizeTick(14, 1, 99.90, 100.0)
    ib.wrapper.tickSize(14, 3, 0.0)                       # size 0 on the gone ask
    ib.wrapper.tickSize(14, 3, 50.0)                      # even a positive size: no price behind it
    q = broker.quote("AAPL")
    assert q is None or q.ask is None


def test_A3_vanished_bid_never_prices_a_sell(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05, 100.0)
    _Clock.t += timedelta(seconds=3)
    ib.wrapper.priceSizeTick(14, 1, -1.0, 0.0)          # bids gone (limit-down / halt)
    ib.wrapper.priceSizeTick(14, 2, 99.00, 100.0)
    q = broker.quote("AAPL")
    assert q is None or q.bid is None


# ======================================================================= B
def test_B1_fresh_trade_is_usable_although_one_book_side_is_quiet(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05)
    t0 = _Clock.t
    _Clock.t = t0 + timedelta(seconds=130)
    ib.wrapper.priceSizeTick(14, 1, 97.00, 100.0)
    ib.wrapper.priceSizeTick(14, 4, 97.00, 100.0)       # trade just now (stop territory)
    q = broker.quote("AAPL")
    assert q.mark() == 97.00 and q.ts == t0 and q.last_ts == _Clock.t
    assert _exit_ok(broker, q)                            # the stop IS evaluated
    assert not _entry_ok(broker, q)                       # an entry still needs a fresh book


def test_B2_same_last_without_book_is_fresh(adapter):
    broker, ib, req = adapter
    _Clock.t += timedelta(seconds=130)
    ib.wrapper.priceSizeTick(14, 4, 97.00, 100.0)
    assert _exit_ok(broker, broker.quote("AAPL"))


def test_B3_sell_is_priced_from_the_trade_when_the_book_is_stale(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.advance(seconds=31)
    now = rig.clock.now()
    # book 130 s old (beyond the 120 s exit age), bid still at 110; a trade at 107 just now
    rig.broker.quotes["AAPL"] = Quote("AAPL", 110.0, 110.10, 107.0, now - timedelta(seconds=130), last_ts=now)
    rig.tick()
    [stop] = [s for s in rig.broker.sent if s.purpose == Purpose.STOP]
    assert stop.limit_price <= 107.0                      # never from the stale 110 bid


def test_B4_sell_uses_the_bid_of_a_fresh_book(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    [stop] = [s for s in rig.broker.sent if s.purpose == Purpose.STOP]
    assert stop.limit_price < rig.broker.quotes["AAPL"].bid


# ======================================================================= C
def test_C1_pre_refusal_fields_never_revive_after_the_status_call(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05)                          # before the refusal
    broker._on_error(14, 10197, "No market data during competing live session", None)
    assert broker.quote("AAPL") is None
    _Clock.t += timedelta(seconds=30)
    ib.wrapper.priceSizeTick(14, 4, 101.0, 1.0)          # a price after the refusal
    broker.market_data_status()                           # heartbeat clears the displayed refusal
    ib.wrapper.tickSize(14, 0, 200.0)
    ib.wrapper.tickSize(14, 3, 300.0)
    q = broker.quote("AAPL")
    assert q is not None and (q.bid, q.ask) == (None, None) and q.last == 101.0
    assert not _entry_ok(broker, q)


# ======================================================================= D
def _sig(symbol="AAPL", price=113.13):
    return type("S", (), {"symbol": symbol, "price": price, "atr": 2.0, "score": 80})()


def _count_queries(rig):
    calls = {"n": 0}
    real = rig.broker.open_orders

    def counting():
        calls["n"] += 1
        return real()
    rig.broker.open_orders = counting
    return calls


def test_D1_no_query_for_a_symbol_that_is_not_eligible(tmp_path):
    rig = Rig(tmp_path, max_positions=1)
    rig.open_aapl()
    calls = _count_queries(rig)
    rig.at(et(10, 5))
    out = rig.engine._try_enter(rig.clock.now(), _sig())
    assert out.startswith("blocked:")
    assert calls["n"] == 0


def test_D2_no_query_without_a_tradable_quote(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 5))
    rig.broker.quotes.pop("AAPL", None)
    calls = _count_queries(rig)
    out = rig.engine._try_enter(rig.clock.now(), _sig())
    assert out.startswith("quote:") and calls["n"] == 0


def test_D3_eligibility_is_checked_again_after_the_query(tmp_path):
    rig = Rig(tmp_path, max_positions=1)
    rig.start()
    rig.at(et(10, 5))
    rig.refresh_quotes()
    real = rig.broker.open_orders

    def appear():
        other = OrderRequest("v6l-otherentry", "MSFT", Side.BUY, 5, 113.0, Purpose.ENTRY, "pos-other")
        rig.store.create_intent(rig.clock.now(), other, atr=2.0, count_entry_day=None)
        return real()
    rig.broker.open_orders = appear
    out = rig.engine._try_enter(rig.clock.now(), _sig())
    assert out.startswith("blocked:"), out
    assert not [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


# ======================================================================= E
def test_E_unknown_request_ids_are_ignored(adapter):
    broker, ib, req = adapter
    ib.wrapper.tickSize(999, 0, 5.0)
    ib.wrapper.priceSizeTick(999, 1, 10.0, 5.0)
    assert broker.quote("AAPL") is None

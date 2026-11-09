"""Regression tests for Codex's review of batch 8 (and patch 8A).

P1-a  the entry's blocking working-order query runs FIRST; quote, limit,
      size and symbol eligibility are read after it (a 17-19 s query can no
      longer leave a stale quote, an old limit or a duplicate position)
P1-b  trading quotes are built only from qualifying price fields of the
      current request, each with its own receive time: volume ticks never
      make an old price fresh, a late tick of a cancelled request or a
      delayed packet never prices a live quote
P2    the intent and its sent_at carry the time of the actual send
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v6lite.models import IntentStatus, OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, et, make_cfg


def _entries(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


def _slow_orders(rig: Rig, seconds: float, during=None):
    """reqAllOpenOrders taking `seconds` (the clock moves, quotes do NOT
    refresh unless `during` does so)."""
    real = rig.broker.open_orders
    state = {"armed": True}

    def open_orders():
        out = real()
        if state["armed"]:
            state["armed"] = False
            rig.clock.advance(seconds=seconds)
            if during:
                during()
        return out
    rig.broker.open_orders = open_orders


def _scan_now(rig: Rig):
    rig.at(et(10, 0, 30))
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False


# ======================================================================= P1-a
def test_P1a_quote_gone_stale_during_the_query_stops_the_buy(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    _scan_now(rig)
    _slow_orders(rig, 17)                       # max_quote_age_seconds = 15: the quote is now 17 s old
    rig.tick()
    assert not _entries(rig)


def test_P1a_order_for_the_same_symbol_appearing_during_the_query_stops_the_buy(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    _scan_now(rig)

    def appear():
        other = OrderRequest("v6l-otherentry", "AAPL", Side.BUY, 5, 113.0, Purpose.ENTRY, "pos-other")
        rig.store.create_intent(rig.clock.now(), other, atr=2.0, count_entry_day=None)
        rig.store.update_intent(rig.clock.now(), other.intent_id, status=IntentStatus.SENT)
        rig.refresh_quotes()
    _slow_orders(rig, 1, appear)
    rig.tick()
    assert [e.intent_id for e in _entries(rig)] == []


def test_P1a_limit_and_size_come_from_the_price_after_the_query(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    _scan_now(rig)
    _slow_orders(rig, 2, lambda: rig.px("AAPL", 113.13 * 1.20))   # +20 % during the query
    rig.tick()
    assert not _entries(rig)                    # drift > MAX_PRICE_DRIFT_PCT vs the signal: refused, not
                                                # sent at the old limit


def test_P1a_clean_attempt_still_buys_with_current_price(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    _scan_now(rig)
    _slow_orders(rig, 2, lambda: rig.px("AAPL", 113.50))
    rig.tick()
    [entry] = _entries(rig)
    assert entry.limit_price == pytest.approx(113.50 * 1.003, abs=0.02)   # limit from the price after the query


# ======================================================================= P2
def test_P2_intent_and_sent_at_carry_the_send_time(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    _scan_now(rig)
    _slow_orders(rig, 10, rig.refresh_quotes)
    rig.tick()
    [entry] = _entries(rig)
    row = rig.store.intent(entry.intent_id)
    assert row["sent_at"] == rig.clock.now().isoformat()
    assert row["created_at"] == rig.clock.now().isoformat()


# ======================================================================= P1-b
class _Clock:
    t = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    """IbkrBroker on the REAL ib_insync objects with a controllable receive clock."""
    import v6lite.broker.ibkr as ibkr
    from ib_insync import Stock

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _Clock.t
    monkeypatch.setattr(ibkr, "datetime", FakeDateTime)
    _Clock.t = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
    broker = ibkr.IbkrBroker(make_cfg(tmp_path))
    ib = broker.ib
    assert broker._ensure_channel_hooks()
    req = {"id": 14}
    broker.ib.reqMktData = lambda c, *a: ib.wrapper.startTicker(req["id"], c, "mktData")
    broker._contracts["AAPL"] = Stock("AAPL", "SMART", "USD", conId=265598)
    broker.subscribe_quotes(["AAPL"])
    return broker, ib, req


def _book(ib, req_id, bid, ask, last=None):
    ib.wrapper.priceSizeTick(req_id, 1, bid, 100)
    ib.wrapper.priceSizeTick(req_id, 2, ask, 100)
    if last:
        ib.wrapper.priceSizeTick(req_id, 4, last, 100)


def test_P1b_volume_ticks_never_make_an_old_price_fresh(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05, 100.0)
    t0 = _Clock.t
    for minute in range(1, 6):                                    # 5 minutes of volume-only updates
        _Clock.t = t0 + timedelta(minutes=minute)
        ib.wrapper.tickSize(14, 8, 1000.0 * minute)
        ib.wrapper.lastTime = _Clock.t
        ib.wrapper.tcpDataProcessed()                             # Ticker.time moves on
    quote = broker.quote("AAPL")
    assert quote.ts == t0 and quote.age_seconds(_Clock.t) == 300
    from v6lite.risk import check_quote
    assert not check_quote(broker.cfg, quote, _Clock.t, require_bid_ask=True).allowed


def test_P1b_bid_ask_size_changes_confirm_the_book(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05)
    _Clock.t += timedelta(seconds=20)
    ib.wrapper.tickSize(14, 0, 300.0)                              # bid size changed at the same price
    ib.wrapper.tickSize(14, 3, 400.0)
    assert broker.quote("AAPL").ts == _Clock.t


def test_P1b_late_tick_of_a_cancelled_request_never_prices_the_quote(adapter):
    broker, ib, req = adapter
    _book(ib, 14, 99.95, 100.05)
    req["id"] = 15
    broker._tickers.pop("AAPL")
    broker.subscribe_quotes(["AAPL"])                              # re-subscription, same Ticker object
    _book(ib, 15, 101.95, 102.05)
    ib.wrapper.priceSizeTick(14, 1, 50.0, 100)                     # late tick on the OLD request id
    quote = broker.quote("AAPL")
    assert (quote.bid, quote.ask) == (101.95, 102.05)


def test_P1b_delayed_packet_never_prices_a_live_quote(adapter):
    broker, ib, req = adapter
    ib.wrapper.priceSizeTick(14, 66, 99.0, 100)                    # delayed bid/ask/last on the current id
    ib.wrapper.priceSizeTick(14, 67, 99.1, 100)
    ib.wrapper.priceSizeTick(14, 68, 99.05, 100)
    assert broker.quote("AAPL") is None


def test_P1b_an_older_last_never_prices_a_fresher_book(adapter):
    broker, ib, req = adapter
    ib.wrapper.priceSizeTick(14, 4, 90.0, 100)                     # an old trade
    _Clock.t += timedelta(minutes=10)
    _book(ib, 14, 99.95, 100.05)
    quote = broker.quote("AAPL")
    assert quote.last is None and quote.mark() == pytest.approx(100.0)

"""Regression tests for Codex's review of batches 6 and 7.

P1-a  an unresolved combo order that appears BETWEEN reconciles must stop
      the BUY at its final boundary (fresh, budgeted working-order query;
      a failed query refuses the BUY)
P1-b  after a market-data refusal, size/volume ticks (which refresh
      Ticker.time) never revive an old price; only a PRICE tick of the
      current subscription does -- ordered by message sequence, not by
      clock time (equal timestamps are common on Windows)
P2-a  evidence-only fills booked by earlier versions for released positions
      (`late_fill_after_release`, `exit_after_close`) make the state refused;
      new ones are quarantined before booking (no fee in day P&L)
P2-b  a refusal of another request (history) on the same symbol is not a
      quote refusal; frozen data in RTH is paged; AvailableFunds freshness
      is the time of that field, not of any account field
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v6lite.models import FillEvent, OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, et, make_cfg


def _entries(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


# ======================================================================= P1-a
def test_P1a_combo_appearing_between_reconciles_stops_the_buy(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(9, 59, 50))
    rig.engine._reconcile_due = True
    rig.tick()                                                     # last reconcile before the scan: clean
    assert rig.engine.flags.reconcile_ok
    rig.broker._register(OrderRequest("combo-z", "*", Side.SELL, 5, 1.0, Purpose.TP1, None))
    rig.at(et(10, 0, 30))                                          # scan; no reconcile due in between
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False
    rig.tick()
    assert not _entries(rig)


def test_P1a_failed_working_order_query_refuses_the_buy(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False
    rig.broker.fail_open_orders = True
    rig.tick()
    assert not _entries(rig)


def test_P1a_clean_evidence_still_buys(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert _entries(rig)


# ======================================================================= P1-b
def _real_quote_broker(tmp_path):
    from ib_insync import Stock
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    ib = broker.ib
    assert broker._ensure_channel_hooks()
    contract = Stock("AAPL", "SMART", "USD", conId=265598)
    req = {"id": 14}
    broker.ib.reqMktData = lambda c, *a: ib.wrapper.startTicker(req["id"], c, "mktData")
    broker.ib.cancelMktData = lambda c: None
    broker._contracts["AAPL"] = contract
    broker.subscribe_quotes(["AAPL"])
    return broker, ib, req


FIXED = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)          # every message at the SAME time


def _batch(ib):
    """End of one socket read, as ib_insync does it: Ticker.time is set."""
    ib.wrapper.lastTime = FIXED
    ib.wrapper.tcpDataProcessed()


def test_P1b_volume_ticks_after_a_refusal_never_revive_an_old_price(tmp_path):
    broker, ib, req = _real_quote_broker(tmp_path)
    ib.wrapper.lastTime = FIXED
    for tick_type, price in ((1, 99.95), (2, 100.05), (4, 100.0)):
        ib.wrapper.priceSizeTick(14, tick_type, price, 100)
    _batch(ib)
    assert broker.quote("AAPL") is not None                         # a live price of this subscription
    broker._on_error(14, 10089, "Requested market data requires additional subscription for API.", None)
    for _ in range(3):
        ib.wrapper.tickSize(14, 8, 12345.0)                          # volume only: refreshes Ticker.time
        _batch(ib)
    assert broker._tickers["AAPL"].time == FIXED
    assert broker.quote("AAPL") is None
    assert broker.market_data_status()["no_subscription"] == ["AAPL"]
    ib.wrapper.priceSizeTick(14, 1, 99.97, 100)                      # PRICE ticks, same timestamp
    ib.wrapper.priceSizeTick(14, 2, 100.03, 100)                     # (batch 9: a quote needs bid AND ask,
    _batch(ib)                                                       #  or a last, of this request)
    assert broker.quote("AAPL") is not None
    assert broker.market_data_status()["no_subscription"] == []


def test_P1b_resubscription_does_not_inherit_the_old_price(tmp_path):
    broker, ib, req = _real_quote_broker(tmp_path)
    ib.wrapper.lastTime = FIXED
    ib.wrapper.priceSizeTick(14, 4, 100.0, 100)
    _batch(ib)
    assert broker.quote("AAPL") is not None
    req["id"] = 15
    broker._tickers.pop("AAPL")
    broker.subscribe_quotes(["AAPL"])                               # e.g. after 1101: same Ticker object
    ticker = broker._tickers["AAPL"]
    assert math.isnan(ticker.last)
    ib.wrapper.tickSize(15, 8, 999.0)
    _batch(ib)
    assert broker.quote("AAPL") is None                              # no price of the new subscription yet


def test_P1b_a_refusal_of_a_history_request_on_the_same_symbol_is_not_a_quote_refusal(tmp_path):
    broker, ib, req = _real_quote_broker(tmp_path)
    broker._on_error(77, 354, "Requested market data is not subscribed.", SimpleNamespace(symbol="AAPL"))
    assert broker.market_data_status()["no_subscription"] == []


# ======================================================================= P2-a
def test_P2a_earlier_versions_evidence_rows_of_released_positions_refuse_the_state(rig_released):
    from v6lite.engine import StartupError
    rig, entry_id, pid = rig_released
    rig.store.conn.execute(
        "INSERT INTO fills(exec_id, intent_id, position_id, symbol, side, quantity, price, time, trading_day, "
        "exec_base) VALUES ('0000fa4e.0000beef.01.01', ?, ?, 'AAPL', 'BUY', 5, 112.0, ?, ?, '0000fa4e.0000beef')",
        (entry_id, pid, rig.clock.now().isoformat(), et(10).date().isoformat()))
    rig.store.raise_anomaly(rig.clock.now(), "late_fill_after_release:0000fa4e.0000beef.01.01", "batch-5 row")
    with pytest.raises(StartupError, match="earlier version booked"):
        rig.engine.startup()


def test_P2a_new_fill_of_a_released_positions_order_is_quarantined(rig_released):
    rig, entry_id, pid = rig_released
    fees_before = rig.store.fees_for_day(et(10).date().isoformat())
    # an intent created AFTER the flat point but belonging to the released
    # position (defence in depth: the flat-point rule would not catch it)
    late = OrderRequest("v6l-afterflat001", "AAPL", Side.SELL, 5, 110.0, Purpose.STOP, pid)
    rig.store.create_intent(rig.clock.now() + timedelta(seconds=1), late, atr=None, count_entry_day=None)
    fill = FillEvent("0000fa4e.0000bef0.01.01", late.intent_id, "AAPL", Side.SELL, 5, 110.0, rig.clock.now(),
                     commission=2.0)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    assert rig.store.conn.execute("SELECT 1 FROM fills WHERE exec_id=?", (fill.exec_id,)).fetchone() is None
    assert rig.store.fees_for_day(et(10).date().isoformat()) == fees_before
    assert any(k.startswith("anomaly:late_fill_after_release:") for k in rig.store.anomalies())
    assert rig.store.legacy_evidence_fills() == []


@pytest.fixture
def rig_released(tmp_path):
    rig = Rig(tmp_path)
    entry_id = rig.open_aapl()
    pid = rig.store.open_positions()[0]["position_id"]
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)
    rig.advance(seconds=61)
    rig.store.set_control(rig.clock.now(), "release:AAPL", '{"operator": "J", "reason": "flat"}')
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.position(pid)["status"] == "RELEASED"
    return rig, entry_id, pid


# ======================================================================= P2-b
def test_P2b_frozen_data_in_rth_is_paged(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.broker.market_data_status = lambda: {"requested": "live", "delayed_symbols": [],
                                             "frozen_symbols": ["SPY"], "no_subscription": []}
    rig.start()
    rig.at(et(10, 30))
    rig.advance(seconds=400)
    rig.tick()
    assert "实时行情订阅异常" in rig.messages()
    assert "no real-time market data" in caplog.text


def test_P2b_available_funds_freshness_is_its_own_push_time(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    av = lambda tag, value: SimpleNamespace(account="DU0000000", tag=tag, value=value, currency="USD")
    broker._on_account_value(av("AvailableFunds", "5000"))
    funds_seen = broker.available_funds().seen_at
    later = [av("NetLiquidation", "10000"), av("BuyingPower", "20000")]
    for v in later:
        broker._on_account_value(v)
    assert broker.available_funds().seen_at == funds_seen            # other fields do not refresh it

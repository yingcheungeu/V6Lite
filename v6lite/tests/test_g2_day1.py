"""Regression tests for the findings of G2 day 1 (2026-10-01, real Paper
Gateway, read-only):

D1-1  AvailableFunds is pushed every ~180 s; a blocking reqAccountUpdates on
      the already subscribed account always timed out after 15 s. The
      refresh is now a non-blocking re-subscription, only after the stream
      was silent longer than one push cycle, at most once per window.
D1-2  117 identical P1 "broker/runtime mismatch" pages in one session: an
      unchanged alert is repeated at most every 30 min (P0: 10 min).
D1-3  a missing config file printed a traceback.
D1-4  no real-time API market data (10089) was only visible in the raw log:
      it is now paged, shown in every status line and in the daily report;
      with real-time data configured a delayed tick is never a quote.
D1-5  the probe compared 90 IBKR bars with ~125 yfinance bars (EMA50 / ADX
      depend on the window): sources are now compared on the same window,
      with the indicator values.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.tests.helpers import Rig, et, make_cfg


# ======================================================================= D1-1 funds
class _Client:
    def __init__(self):
        self.calls = []

    def reqAccountUpdates(self, subscribe, account):
        self.calls.append((subscribe, account))


def test_D1_1_refresh_is_a_non_blocking_resubscription_keeping_the_old_values(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    client = _Client()

    def blocking(*a, **k):
        raise AssertionError("the blocking reqAccountUpdates must not be used")
    broker.ib = SimpleNamespace(client=client, reqAccountUpdates=blocking)
    broker._on_account_value(SimpleNamespace(account="DU0000000", tag="AvailableFunds", value="5000",
                                             currency="USD"))
    before = broker.available_funds()
    broker.refresh_account()
    assert client.calls == [(False, "DU0000000"), (True, "DU0000000")]
    after = broker.available_funds()
    assert after.value == 5000.0 and after.seen_at == before.seen_at        # judged by the old time


def test_D1_1_a_normal_push_cycle_does_not_trigger_a_refresh(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 30))
    rig.broker.funds_as_of = rig.broker.funds_seen_at = et(10, 27)            # 180 s ago: one normal cycle
    rig.tick()
    assert getattr(rig.broker, "account_refreshes", 0) == 0


def test_D1_1_silent_funds_are_probed_at_most_once_per_window(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 30))
    rig.broker.funds_as_of = et(10, 20)

    rig.broker.refresh_funds = lambda: setattr(rig.broker, "funds_refreshes",
                                               getattr(rig.broker, "funds_refreshes", 0) + 1)
    rig.broker.funds_seen_at = et(10, 20)                                     # stays silent
    for _ in range(10):                                                       # 100 s of ticks
        rig.tick()
        rig.advance(seconds=10)
    assert rig.broker.funds_refreshes == 1
    rig.advance(seconds=210)
    rig.tick()
    assert rig.broker.funds_refreshes == 2
    assert getattr(rig.broker, "account_refreshes", 0) == 0


def test_D1_1_entry_gate_uses_the_measured_cycle(tmp_path):
    from v6lite.engine import ACCOUNT_PUSH_CYCLE, FUNDS_MAX_AGE, FUNDS_REFRESH_AFTER
    assert ACCOUNT_PUSH_CYCLE < FUNDS_REFRESH_AFTER < FUNDS_MAX_AGE


# ======================================================================= D1-2 alerts
def _alerter():
    channel = MemoryChannel()
    return Alerter([channel], synchronous=True), channel


def test_D1_2_unchanged_condition_is_repeated_at_most_every_30_minutes():
    alerter, channel = _alerter()
    t0 = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    for minute in range(0, 120, 5):                                           # a reconcile every 5 min
        alerter.send(t0 + timedelta(minutes=minute), "P1", "broker/runtime mismatch", "ABT: unmanaged 333")
    assert len(channel.messages) == 4                                         # 0, 30, 60, 90


def test_D1_2_a_changed_condition_still_pages_after_the_short_window():
    alerter, channel = _alerter()
    t0 = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    alerter.send(t0, "P1", "broker/runtime mismatch", "ABT: unmanaged 333")
    alerter.send(t0 + timedelta(minutes=2), "P1", "broker/runtime mismatch", "ABT: unmanaged 300")
    alerter.send(t0 + timedelta(minutes=6), "P1", "broker/runtime mismatch", "ABT: unmanaged 300")
    assert len(channel.messages) == 2                                         # t0 and the change at +6


def test_D1_2_p0_repeats_every_10_minutes():
    alerter, channel = _alerter()
    t0 = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    for minute in range(0, 30, 1):
        alerter.send(t0 + timedelta(minutes=minute), "P0", "exit blocked AAPL STOP", "combo working")
    assert len(channel.messages) == 3


# ======================================================================= D1-3 config
def test_D1_3_missing_config_is_one_clear_line(tmp_path, capsys):
    from v6lite.__main__ import main
    rc = main(["run", "--config", str(tmp_path / "v6lite.paper.toml"), "--dry-run"])
    err = capsys.readouterr().err
    assert rc == 2 and "config file not found" in err and "Traceback" not in err


# ======================================================================= D1-4 market data
def _ticker(md_type, last=100.0):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(marketDataType=md_type, bid=last - 0.05, ask=last + 0.05, last=last, time=now,
                           contract=SimpleNamespace(symbol="AAPL"))


def _real_quote_broker(tmp_path, **cfg):
    """IbkrBroker on the REAL ib_insync IB/Wrapper/Ticker objects (no socket):
    a quote subscription is registered exactly as reqMktData would."""
    from ib_insync import Stock
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path, **cfg))
    ib = broker.ib
    assert broker._ensure_channel_hooks()
    contract = Stock("AAPL", "SMART", "USD", conId=265598)
    broker.ib.reqMktData = lambda c, *a: ib.wrapper.startTicker(14, c, "mktData")
    broker._contracts["AAPL"] = contract
    broker.subscribe_quotes(["AAPL"])
    return broker, ib


def _price(ib, tick_type, price, size=100):
    ib.wrapper.lastTime = datetime.now(timezone.utc)
    ib.wrapper.priceSizeTick(14, tick_type, price, size)          # what the decoder calls


def test_D1_4_delayed_tick_is_not_a_quote_when_real_time_is_configured(tmp_path):
    broker, ib = _real_quote_broker(tmp_path)
    ticker = broker._tickers["AAPL"]
    ticker.marketDataType = 3
    _price(ib, 66, 99.95), _price(ib, 67, 100.05), _price(ib, 68, 100.0)
    ticker.time = datetime.now(timezone.utc)
    assert broker.quote("AAPL") is None
    assert broker.market_data_status()["delayed_symbols"] == ["AAPL"]
    ticker.marketDataType = 1
    _price(ib, 1, 99.96), _price(ib, 2, 100.04), _price(ib, 4, 100.01)
    assert broker.quote("AAPL") is not None


def test_D1_4_missing_subscription_is_recorded_and_cleared_by_a_real_time_price(tmp_path):
    broker, ib = _real_quote_broker(tmp_path)
    broker._on_error(14, 10089, "Requested market data requires additional subscription for API.", None)
    broker._on_error(15, 354, "history of an index", SimpleNamespace(symbol="AAPL"))   # another request id
    assert broker.market_data_status()["no_subscription"] == ["AAPL"]
    _price(ib, 1, 99.96)
    assert broker.market_data_status()["no_subscription"] == []


def test_D1_4_frozen_tick_is_not_a_quote_either(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker._tickers["AAPL"] = _ticker(2)
    assert broker.quote("AAPL") is None


def test_D1_4_engine_pages_and_reports_missing_real_time_data(tmp_path, caplog):
    from v6lite.report import daily_report
    rig = Rig(tmp_path)
    rig.broker.no_subscription = ["AAPL", "SPY"]
    rig.start()
    rig.advance(seconds=400)
    rig.tick()
    assert "实时行情订阅异常" in rig.messages()
    assert "no real-time market data" in caplog.text
    snap = rig.engine.status_snapshot(rig.clock.now())
    assert snap["market_data"]["no_subscription"] == ["AAPL", "SPY"]
    text = daily_report(rig.store, rig.cfg, et(10).date())
    assert "market data: real-time (1) | no API subscription: AAPL, SPY" in text


def test_D1_4_report_states_a_delayed_rehearsal(tmp_path):
    from v6lite.report import daily_report
    rig = Rig(tmp_path, market_data_type=3)
    rig.start()
    assert "DELAYED (3)" in daily_report(rig.store, rig.cfg, et(10).date())


# ======================================================================= D1-5 probe comparison
def test_D1_5_probe_compares_sources_on_the_same_window(tmp_path, monkeypatch):
    from datetime import date
    from v6lite.tools.g2_probe import Probe, _comparison_line
    from v6lite.tests.test_g2_probe import ProbeIB
    from v6lite.tests.helpers import buy_bars

    long = buy_bars(n=200)
    days = [date(2025, 12, 1) + timedelta(days=i) for i in range(200)]

    class Frame:                                     # the part of a DataFrame the probe uses
        def __init__(self, rows):
            self.rows = rows

        def iterrows(self):
            return iter(self.rows)

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period, interval):
            n = 42 if period == "60d" else 200
            return Frame([(datetime(d.year, d.month, d.day),
                           SimpleNamespace(Open=b.open, High=b.high, Low=b.low, Close=b.close, Volume=b.volume))
                          for d, b in zip(days[200 - n:], long[200 - n:])])
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=FakeTicker))
    ib_bars = [SimpleNamespace(date=d, open=b.open, high=b.high, low=b.low, close=b.close,
                               volume=b.volume) for d, b in zip(days[-90:], long[-90:])]
    probe = Probe(make_cfg(tmp_path), ProbeIB())
    probe.compare_sources({"AAPL": ib_bars})
    row = probe.ev.findings["source_comparison"]["AAPL"]
    assert row["window_bars"] == 90 and row["same_dates"] and row["max_close_diff_pct"] == 0.0
    assert row["score_diff"] == 0 and row["same_signal"]                     # same prices, same window
    assert row["legacy_yfinance_60d"]["bars"] == 42 and "adx" in row["ibkr"]
    assert "AAPL" in _comparison_line(probe.ev.findings["source_comparison"])

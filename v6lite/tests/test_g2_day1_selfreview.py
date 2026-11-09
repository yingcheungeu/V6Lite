"""Batch-7 self-review (adversarial pass before delivery). R7-1/2/3/6/7/9
failed on the first batch-7 version, R7-5 also on batch 6 (pre-existing);
R7-4 pins the ib_insync behaviour the non-blocking re-subscription relies
on; R7-8 now asserts that history-only refusals are not recorded at all."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.tests.helpers import Rig, et, make_cfg


# ------------------------------------------------------------------ alerts
def test_R7_1_log_only_alert_must_not_mute_a_later_remote_page_for_30_min():
    """A P1 first raised log-only (remote=False, e.g. a pre-RTH disconnect)
    records (now, None). The new 30-min repeat window then suppresses the
    same P1 when it later must page remotely (RTH). Baseline: 5 min."""
    ch = MemoryChannel()
    a = Alerter([ch], synchronous=True)
    t0 = datetime(2026, 10, 6, 13, 20, tzinfo=timezone.utc)            # 09:20 ET, pre-RTH
    a.send(t0, "P1", "IBKR disconnected", "disconnected", remote=False)
    rid = a.send(t0 + timedelta(minutes=15), "P1", "IBKR disconnected", "disconnected")  # 09:35 ET RTH
    assert rid is not None and len(ch.messages) == 1, "RTH disconnect page suppressed by a log-only entry"


def test_R7_2_engine_pre_rth_and_recurring_rth_disconnect_are_both_paged(tmp_path):
    rig = Rig(tmp_path, start=et(9, 15))
    rig.start()
    rig.broker.drop_connection()                     # B28: unexpected pre-RTH loss also pages
    rig.tick()
    count = lambda: sum("P1" in title and "IBKR 连接断开" in title
                        for title, _ in rig.channel.messages)
    assert count() == 1
    rig.advance(seconds=31)
    rig.tick()                                       # reconnects
    assert rig.engine.flags.broker_connected
    rig.at(et(9, 35))                                # new RTH loss must page again
    rig.broker.drop_connection()
    rig.tick()
    assert count() == 2, "new RTH disconnect must not be muted by the pre-RTH page"


def test_R7_3_recurring_disconnect_after_reconnect_is_not_paged(tmp_path):
    """Disconnect paged, 'reconnected' paged, then a NEW disconnect 15 min
    later is suppressed (same subject+body < 30 min): the operator's last
    message says 'reconnected' while the engine is down again."""
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0))
    rig.broker.drop_connection()
    rig.tick()
    rig.advance(seconds=31)
    rig.tick()
    assert rig.engine.flags.broker_connected
    n_disc = sum("P1" in subject and "IBKR 连接断开" in subject
                 for subject, _ in rig.channel.messages)
    assert n_disc == 1 and any("IBKR 已重新连接" in subject for subject, _ in rig.channel.messages)
    rig.at(et(10, 15))
    rig.broker.drop_connection()
    rig.tick()
    n_disc = sum("P1" in subject and "IBKR 连接断开" in subject
                 for subject, _ in rig.channel.messages)
    assert n_disc == 2, "second disconnect (after a reconnect) not paged"


# ------------------------------------------------------------------ funds / ib_insync semantics
def _real_ib_broker(tmp_path):
    from ib_insync import IB
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    ib = IB()
    sent = []
    ib.client.send = lambda *f, **k: sent.append(f)      # socket stand-in
    ib.accountValueEvent += broker._on_account_value
    ib.errorEvent += broker._on_error
    broker.ib = ib
    return broker, ib, sent


def test_R7_4_real_wrapper_handles_unsolicited_download_end_and_keeps_values(tmp_path):
    """Semantics check (expected to pass): accountDownloadEnd with no
    pending future is a no-op; unsubscribing does not clear
    wrapper.accountValues (net_liquidation keeps the pre-refresh value)."""
    broker, ib, sent = _real_ib_broker(tmp_path)
    w = ib.wrapper
    w.updateAccountValue("NetLiquidation", "100000", "USD", "DU0000000")
    w.updateAccountValue("AvailableFunds", "5000", "USD", "DU0000000")
    w.accountDownloadEnd("DU0000000")
    broker.refresh_account()
    assert [f[0] for f in sent] == [6, 6]
    w.accountDownloadEnd("DU0000000")                       # re-download end, nobody waiting
    assert broker.net_liquidation() == 100000.0
    assert broker.available_funds().value == 5000.0


def test_R7_5_after_1101_funds_are_dropped_but_net_liquidation_is_not(tmp_path):
    """refresh_subscriptions resets the adapter's AvailableFunds (entries
    blocked until IBKR speaks) but net_liquidation still serves the
    pre-'data lost' wrapper value with no age check -> sizing equity from
    stale data. (Pre-existing; asserted here as the consistent behaviour.)"""
    broker, ib, sent = _real_ib_broker(tmp_path)
    ib.wrapper.updateAccountValue("NetLiquidation", "100000", "USD", "DU0000000")
    ib.wrapper.updateAccountValue("AvailableFunds", "5000", "USD", "DU0000000")
    broker.refresh_subscriptions()
    assert broker.available_funds().value is None
    assert broker.net_liquidation() is None, "equity still sized from pre-1101 NetLiquidation"


def test_R7_6_fake_refresh_account_models_the_old_blocking_semantics(tmp_path):
    """FakeBroker.refresh_account marks the stream fresh at once; the real
    adapter keeps the old seen_at until IBKR re-sends. Engine tests using
    the fake can therefore admit entries the real adapter would refuse."""
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0))
    rig.broker.funds_as_of = rig.broker.funds_seen_at = et(9, 54)          # silent 6 min
    rig.tick()
    assert rig.broker.available_funds().seen_at < et(9, 56), \
        "fake refresh made a silent stream look fresh (real adapter would not)"


def test_R7_7_funds_unavailable_then_refresh_logs_no_unbound_error(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 30))

    def boom():
        raise RuntimeError("funds read failed")
    rig.broker.available_funds = boom
    caplog.set_level("WARNING")
    rig.tick()
    assert getattr(rig.broker, "account_refreshes", 0) == 0
    assert rig.broker.funds_refreshes == 1
    assert "refresh failed" not in caplog.text, caplog.text


# ------------------------------------------------------------------ market data
def test_R7_8_refusal_for_a_non_streamed_contract_is_not_recorded(tmp_path):
    """354/10089 on a history request (VIX index, a scan-only symbol) is not
    a quote stream: it is logged but never becomes a sticky market-data
    problem that only a streamed tick could clear."""
    from ib_insync import Index
    broker, ib, sent = _real_ib_broker(tmp_path)
    vix = Index("VIX", "CBOE")
    ib.wrapper.startReq(77, contract=vix)
    ib.wrapper.error(77, 354, "Requested market data is not subscribed.", "")
    assert broker.market_data_status()["no_subscription"] == []


def test_R7_9_stale_last_on_reused_ticker_clears_a_fresh_refusal(tmp_path):
    """ib_insync reuses the Ticker (same contract) after cancel/re-request:
    a 10089 on the new subscription must not be cleared by the OLD last.
    (Batch 8: driven through subscribe_quotes, as the adapter really does.)"""
    from ib_insync import Stock
    broker, ib, sent = _real_ib_broker(tmp_path)
    c = Stock("AAPL", "SMART", "USD")
    broker._contracts["AAPL"] = c
    req = {"id": 5}
    ib.reqMktData = lambda contract, *a: ib.wrapper.startTicker(req["id"], contract, "mktData")
    broker.subscribe_quotes(["AAPL"])
    t = broker._tickers["AAPL"]
    ib.wrapper.priceSizeTick(5, 4, 113.0, 100)             # from before (real-time, type 1)
    ib.wrapper.endTicker(t, "mktData")
    broker._tickers.pop("AAPL")
    req["id"] = 6
    broker.subscribe_quotes(["AAPL"])                      # re-subscribe after 1101 -> same object
    assert broker._tickers["AAPL"] is t
    ib.wrapper.error(6, 10089, "Requested market data requires additional subscription for API.", "")
    assert broker.market_data_status()["no_subscription"] == ["AAPL"], "refusal hidden by a stale last"
    ib.wrapper.priceSizeTick(5, 4, 113.5, 100)             # a late tick on the CANCELLED request id
    assert broker.quote("AAPL") is None

"""Batch 11 F8 acceptance tests: isolated G2-D delayed-data rehearsal.

All scans use FakeBroker, a temporary store, and a fixed clock. The CLI test
replaces its broker, account lock, clock, and engine loop before invoking main.
"""

from __future__ import annotations

import json
import logging
import tomllib
from datetime import timedelta
from pathlib import Path

import pytest

from v6lite.alerts import MemoryChannel
from v6lite.tests.helpers import Rig, et


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    from ib_insync import IB

    def forbidden(*args, **kwargs):
        raise AssertionError("F8 tests must not connect to Gateway")

    monkeypatch.setattr(IB, "connect", forbidden)
    monkeypatch.setattr(IB, "connectAsync", forbidden)


def _rig(tmp_path, *, delayed: bool, start=None):
    rig = Rig(tmp_path, start=start or et(9, 50), dry_run=True,
              market_data_type=3 if delayed else 1)
    # FakeBroker's default status always says live; represent the configured
    # public adapter mode explicitly, without changing production test helpers.
    rig.broker.delayed_symbols = []
    rig.broker.no_subscription = []
    rig.broker.market_data_status = lambda: {
        "requested": "DELAYED" if delayed else "live",
        "delayed_symbols": list(rig.broker.delayed_symbols),
        "no_subscription": list(rig.broker.no_subscription),
    }
    return rig


def _run_one_scan(rig, caplog):
    caplog.set_level(logging.INFO)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    rows = [r.getMessage() for r in caplog.records
            if "WOULD_BUY AAPL " in r.getMessage()]
    assert rows, "synthetic buy bars must exercise the real scan and entry gate"
    assert all("gate=" in row for row in rows)
    assert rig.broker.sent == []
    assert rig.broker.cancels == []
    assert rig.store.conn.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
    return rows


def _pages(rig, subject):
    title = ("延迟行情订阅异常" if "no delayed market data" in subject else
             "实时行情订阅异常" if "no real-time market data" in subject else subject)
    return [s for s, _ in rig.channel.messages
            if title in s and ("P1:" not in subject or "P1" in s)]


def test_F8_delayed_fake_scan_labels_every_would_buy_and_has_no_order_writes(tmp_path, caplog):
    rig = _rig(tmp_path, delayed=True)
    try:
        assert all("data=delayed" in row for row in _run_one_scan(rig, caplog))
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_live_fake_scan_labels_every_would_buy(tmp_path, caplog):
    rig = _rig(tmp_path, delayed=False)
    try:
        assert all("data=live" in row for row in _run_one_scan(rig, caplog))
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_delayed_cli_alert_title_is_unmistakable(tmp_path, monkeypatch):
    from v6lite import alerts
    from v6lite.tests.test_batch10 import _fake_cli

    cli, fake = _fake_cli(tmp_path, monkeypatch, md_type=3, capture={})
    channel = MemoryChannel()
    monkeypatch.setattr(alerts, "build_channels", lambda cfg: [channel])
    assert cli.main(["run", "--dry-run", "--config", str(tmp_path / "fake.toml")]) == 0
    assert not fake.sent and not fake.cancels
    assert channel.messages
    assert any("V6-Lite 已启动" in s and "延迟行情" in s for s, _ in channel.messages)
    assert any("演练" in b and "延迟行情" in b for s, b in channel.messages
               if "V6-Lite 已启动" in s)


def test_F8_delayed_startup_and_heartbeat_identify_rehearsal(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    rig = _rig(tmp_path, delayed=True, start=et(10, 5))
    try:
        rig.start()
        rig.at(et(10, 16))
        rig.tick()
        summaries = [json.loads(r.getMessage().split("[STARTUP_SUMMARY] ", 1)[1])
                     for r in caplog.records if "[STARTUP_SUMMARY] " in r.getMessage()]
        heartbeats = [json.loads(r.getMessage().split("[HEARTBEAT] ", 1)[1])
                      for r in caplog.records if "[HEARTBEAT] " in r.getMessage()]
        assert len(summaries) == 1 and heartbeats
        assert summaries[0]["data_mode"] == "DELAYED rehearsal only"
        assert all(row["data_mode"] == "DELAYED rehearsal only" for row in heartbeats)
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_expected_delayed_ticks_do_not_page_about_missing_real_time(tmp_path):
    rig = _rig(tmp_path, delayed=True, start=et(10, 5))
    rig.broker.delayed_symbols = ["AAPL", "SPY", "TSLA"]
    try:
        rig.start()
        assert not _pages(rig, "no real-time market data")
        assert not _pages(rig, "no delayed market data")
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_delayed_config_never_uses_real_time_alert_when_status_mislabels_mode(tmp_path):
    rig = _rig(tmp_path, delayed=True, start=et(10, 5))
    rig.broker.no_subscription = ["AAPL"]
    rig.broker.market_data_status = lambda: {
        "requested": "live",  # FakeBroker's default; config remains delayed.
        "delayed_symbols": [],
        "no_subscription": list(rig.broker.no_subscription),
    }
    try:
        rig.start()
        assert not _pages(rig, "no real-time market data")
        assert len(_pages(rig, "P1: no delayed market data")) == 1
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_delayed_no_subscription_pages_P1_with_F3_cadence_and_resolution(tmp_path):
    rig = _rig(tmp_path, delayed=True, start=et(9))
    rig.broker.no_subscription = ["AAPL"]
    try:
        rig.start()
        assert len(_pages(rig, "P1: no delayed market data")) == 1  # startup
        assert not _pages(rig, "no real-time market data")
        for when, expected in [(et(9, 29), 1), (et(9, 30), 2),
                               (et(10), 2), (et(11, 30), 3)]:
            rig.at(when)
            rig.engine._check_market_data(when)
            assert len(_pages(rig, "P1: no delayed market data")) == expected
        rig.broker.no_subscription = ["AAPL", "TSLA"]
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_pages(rig, "P1: no delayed market data")) == 4
        rig.broker.no_subscription = []
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        rig.broker.no_subscription = ["AAPL"]
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_pages(rig, "P1: no delayed market data")) == 5
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_delayed_quote_shortage_pages_only_after_120_seconds_via_tick(tmp_path):
    rig = _rig(tmp_path, delayed=True, start=et(10, 5))
    rig.broker.quotes.pop("AAPL")
    try:
        rig.start()
        assert not _pages(rig, "no delayed market data")
        rig.advance(seconds=119)
        rig.tick()
        assert not _pages(rig, "no delayed market data")
        rig.advance(seconds=2)
        rig.tick()
        assert len(_pages(rig, "P1: no delayed market data")) == 1
        assert not _pages(rig, "no real-time market data")
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_real_time_subscription_alert_behavior_is_preserved(tmp_path):
    rig = _rig(tmp_path, delayed=False, start=et(10, 5))
    rig.broker.no_subscription = ["AAPL"]
    try:
        rig.start()
        assert len(_pages(rig, "P1: no real-time market data")) == 1
        assert not _pages(rig, "no delayed market data")
        rig.advance(seconds=10)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_pages(rig, "P1: no real-time market data")) == 1
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_same_quote_count_but_different_missing_symbol_pages_immediately(tmp_path):
    rig = _rig(tmp_path, delayed=True, start=et(10, 5))
    try:
        rig.start()
        rig.broker.quotes.pop("AAPL")
        rig.engine._check_market_data(rig.clock.now())
        rig.advance(seconds=121)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_pages(rig, "P1: no delayed market data")) == 1

        rig.px("AAPL", 113.13)
        rig.broker.quotes.pop("SPY")
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_pages(rig, "P1: no delayed market data")) == 2
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_delayed_quote_shortage_resolves_and_reappearing_shortage_pages(tmp_path):
    rig = _rig(tmp_path, delayed=True, start=et(10, 5))
    try:
        rig.start()
        rig.broker.quotes.pop("AAPL")
        rig.engine._check_market_data(rig.clock.now())
        rig.advance(seconds=121)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_pages(rig, "P1: no delayed market data")) == 1

        rig.px("AAPL", 113.13)
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        assert "P1|no delayed market data" not in rig.alerter._recent

        rig.broker.quotes.pop("AAPL")
        rig.engine._check_market_data(rig.clock.now())
        rig.advance(seconds=121)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_pages(rig, "P1: no delayed market data")) == 2
    finally:
        rig.alerter.close()
        rig.store.close()


def test_F8_example_matches_paper_except_data_type_and_expanded_state_dir():
    from v6lite.config import load_config

    root = Path(__file__).resolve().parents[1] / "examples"
    paper = tomllib.loads((root / "paper.toml").read_text(encoding="utf-8"))
    delayed_path = root / "paper_delayed.toml"
    delayed = tomllib.loads(delayed_path.read_text(encoding="utf-8"))
    assert paper["ibkr"]["market_data_type"] == 1
    assert delayed["ibkr"]["market_data_type"] == 3
    assert delayed["paths"]["state_dir"] == "~/.v6lite/paper-delayed"
    delayed["ibkr"]["market_data_type"] = paper["ibkr"]["market_data_type"]
    delayed["paths"]["state_dir"] = paper["paths"]["state_dir"]
    assert delayed == paper
    assert load_config(delayed_path).resolved_state_dir() == Path.home() / ".v6lite" / "paper-delayed"

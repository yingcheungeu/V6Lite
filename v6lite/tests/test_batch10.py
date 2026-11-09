"""Batch 10 acceptance tests. All brokers/channels/clocks/state are isolated.

These assert the requested observable behaviour, including the no-write
boundary; they deliberately fail on the unmodified batch-9 implementation.
No CLI engine loop or real G2 probe is launched by this module.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.broker.fake import FakeBroker
from v6lite.clock import FixedClock
from v6lite.models import AnomalyEvent, OpenOrder, Signal
from v6lite.report import daily_report
from v6lite.tests.helpers import Rig, et, make_cfg
from v6lite.tests.test_g2_probe import ProbeIB


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    """Even an accidentally unmocked adapter must never open a Gateway."""
    from ib_insync import IB

    def forbidden(*args, **kwargs):
        raise AssertionError("batch10 tests may not connect to a real Gateway")

    monkeypatch.setattr(IB, "connect", forbidden)
    monkeypatch.setattr(IB, "connectAsync", forbidden)


@pytest.fixture
def dry_rig(tmp_path):
    rig = Rig(tmp_path, dry_run=True)
    rig.start()
    rig.at(et(10, 5))
    yield rig
    rig.alerter.close()
    rig.store.close()


def _attempt(rig):
    return rig.engine._try_enter(rig.clock.now(), Signal("AAPL", 85, 113.13, 2.0))


def _no_writes(rig):
    assert rig.broker.sent == [], "dry-run reached broker submit"
    assert rig.broker.cancels == [], "dry-run reached broker cancel"
    assert rig.store.conn.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
    assert rig.store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind='INTENT_CREATED'").fetchone()[0] == 0
    risk = rig.store.risk_day("2026-10-06")
    assert risk is not None and risk["entries_submitted"] == 0


def _would_buy(caplog):
    return [r.getMessage() for r in caplog.records if "WOULD_BUY AAPL " in r.getMessage()]


def test_F1_each_dry_attempt_reports_final_gate_and_never_writes(dry_rig, caplog):
    caplog.set_level(logging.INFO)
    _attempt(dry_rig)
    _attempt(dry_rig)
    _no_writes(dry_rig)
    rows = _would_buy(caplog)
    assert len(rows) == 2, "each attempt needs a result; day/symbol dedupe hides changed gates"
    assert all(" LMT " in row and "risk=" in row and "gate=ok" in row for row in rows)


@pytest.mark.parametrize("case,reason", [
    ("global_kill", "kill_switch:batch10 early refusal"),
    ("symbol_ignored", "protected_or_ignored"),
    ("no_quote", "no_quote"),
    ("price_drift", "price_drift"),
    ("size_zero", "size_below_one_share"),
])
def test_F1_early_refusal_reports_every_attempt_without_writes(dry_rig, caplog, case, reason):
    """A rejected signal still needs one gate result per attempt, including
    failures before order evidence and failures that never produce a size.
    Use real controls/quotes/sizing rather than replacing the gate itself.
    """
    caplog.set_level(logging.INFO)
    if case == "global_kill":
        dry_rig.store.kill(dry_rig.clock.now(), "batch10 early refusal")
    elif case == "symbol_ignored":
        dry_rig.store.set_disposition(dry_rig.clock.now(), "AAPL", "ignore", "batch10 test")
    elif case == "no_quote":
        dry_rig.broker.quotes.pop("AAPL")
    elif case == "price_drift":
        dry_rig.px("AAPL", 150.0)
    elif case == "size_zero":
        dry_rig.engine.flags.equity = 1.0

    outcomes = [_attempt(dry_rig), _attempt(dry_rig)]
    _no_writes(dry_rig)
    assert all(reason in outcome for outcome in outcomes), "fixture did not reach the intended refusal"
    rows = _would_buy(caplog)
    assert len(rows) == 2, f"{case}: both refused attempts must remain visible"
    assert all("gate=refused:" in row and reason in row for row in rows)


def test_F1_dry_attempt_queries_working_orders_and_rechecks_inputs(dry_rig, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    seen = []
    real_orders = dry_rig.broker.open_orders

    def query():
        seen.append("query")
        dry_rig.px("AAPL", 113.23)
        return real_orders()

    monkeypatch.setattr(dry_rig.broker, "open_orders", query)
    _attempt(dry_rig)
    _no_writes(dry_rig)
    assert seen == ["query"], "dry-run must fetch current read-only order evidence"
    from v6lite.exits import buy_limit
    assert f"LMT {buy_limit(113.23, dry_rig.cfg.entry_limit_band_pct)}" in _would_buy(caplog)[-1]


def test_F1_stale_funds_is_a_reported_refusal_not_a_false_would_buy(dry_rig, caplog):
    caplog.set_level(logging.INFO)
    old = dry_rig.clock.now() - timedelta(seconds=271)
    dry_rig.broker.funds_as_of = dry_rig.broker.funds_seen_at = old
    _attempt(dry_rig)
    _no_writes(dry_rig)
    assert any("gate=refused:funds_stale" in row for row in _would_buy(caplog))


def test_F1_combo_arriving_in_entry_query_refuses_dry_attempt(dry_rig, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    combo = OpenOrder("foreign:77", 77, "*", "Submitted", 0, 1, "SELL", False)
    monkeypatch.setattr(dry_rig.broker, "open_orders", lambda: [combo])
    _attempt(dry_rig)
    _no_writes(dry_rig)
    assert any("gate=refused:unresolved_combo_order" in row for row in _would_buy(caplog))


def test_F1_event_arriving_during_query_is_processed_before_gate(dry_rig, monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def query():
        dry_rig.broker.events.append(AnomalyEvent("during_query", "evidence changed while waiting"))
        return []

    monkeypatch.setattr(dry_rig.broker, "open_orders", query)
    _attempt(dry_rig)
    _no_writes(dry_rig)
    assert "anomaly:during_query" in dry_rig.store.anomalies()
    assert any("gate=refused:" in row and "during_query" in row for row in _would_buy(caplog))


def test_F1_dry_stream_probes_funds_after_210_seconds_without_faking_freshness(dry_rig, caplog):
    caplog.set_level(logging.DEBUG)
    old = dry_rig.clock.now() - timedelta(seconds=211)
    dry_rig.broker.funds_as_of = dry_rig.broker.funds_seen_at = old
    dry_rig.engine._refresh_equity(dry_rig.clock.now(), force=True)
    dry_rig.engine._refresh_equity(dry_rig.clock.now(), force=True)
    _no_writes(dry_rig)
    assert getattr(dry_rig.broker, "account_refreshes", 0) == 0
    assert dry_rig.broker.funds_refreshes == 1
    assert dry_rig.broker.available_funds().seen_at == old
    assert "[ACCOUNT_STREAM]" in caplog.text and "funds snapshot requested" in caplog.text


def _fake_cli(tmp_path, monkeypatch, *, md_type=1, capture=None):
    """Exercise startup formatting/validation with fake broker and temp state.

    The CLI's infinite run_engine boundary is replaced; all external channels
    and the cross-process account lock are replaced as well.
    """
    from v6lite import __main__ as cli
    from v6lite import alerts, clock
    from v6lite.broker import ibkr

    cfg = make_cfg(tmp_path, market_data_type=md_type)
    fixed = FixedClock(et(9, 0))
    fake = FakeBroker(cfg.account, clock=fixed)
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(cli, "AccountLock", lambda *a, **k: SimpleNamespace(
        acquire=lambda *a: None, release=lambda: None))
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    monkeypatch.setattr(ibkr, "IbkrBroker", lambda *a, **k: fake)
    monkeypatch.setattr(alerts, "build_channels", lambda *a: [MemoryChannel()])
    monkeypatch.setattr(clock, "SystemClock", lambda: fixed)

    def once(engine, broker, **kwargs):
        if capture is not None:
            engine.startup()
            row = engine.store.conn.execute("SELECT payload FROM audit WHERE kind='STARTUP'").fetchone()
            capture.update(audit=json.loads(row[0]), report=daily_report(engine.store, cfg, et(9).date()))
            engine.shutdown()

    monkeypatch.setattr(cli, "run_engine", once)
    return cli, fake


def test_F2_paper_formal_delayed_data_fails_with_config_error(tmp_path, monkeypatch, capsys):
    cli, fake = _fake_cli(tmp_path, monkeypatch, md_type=3)
    rc = cli.main(["run", "--config", str(tmp_path / "fake.toml")])
    err = capsys.readouterr().err
    assert rc == 2 and "CONFIG ERROR" in err and "dry-run" in err
    assert not fake.connected and not fake.sent and not fake.cancels


def test_F2_delayed_data_remains_available_for_dry_run(tmp_path, monkeypatch, capsys):
    cli, fake = _fake_cli(tmp_path, monkeypatch, md_type=3)
    assert cli.main(["run", "--dry-run", "--config", str(tmp_path / "fake.toml")]) == 0
    assert "CONFIG ERROR" not in capsys.readouterr().err
    assert not fake.connected and not fake.sent and not fake.cancels


def _md_pages(rig):
    return [m for m in rig.channel.messages if "实时行情订阅异常" in m[0]]


def _md_rig(tmp_path, *, start=et(9, 0)):
    rig = Rig(tmp_path, start=start, dry_run=True)
    rig.broker.no_subscription = ["AAPL"]
    rig.start()
    return rig


def test_F3_start_open_and_two_hour_repeats_only(tmp_path):
    rig = _md_rig(tmp_path)
    try:
        assert len(_md_pages(rig)) == 1
        for when, count in [(et(9, 29), 1), (et(9, 30), 2), (et(10), 2),
                            (et(11, 29, 59), 2), (et(11, 30), 3), (et(13, 30), 4)]:
            rig.at(when)
            rig.engine._check_market_data(when)
            assert len(_md_pages(rig)) == count, when
    finally:
        rig.store.close()


def test_F3_outside_rth_repetition_is_logged_without_paging(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    rig = _md_rig(tmp_path, start=et(8))
    try:
        caplog.clear()
        rig.at(et(8, 45))
        rig.engine._check_market_data(rig.clock.now())
        rig.at(et(16, 5))
        rig.engine._check_market_data(rig.clock.now())
        assert len(_md_pages(rig)) == 1
        assert "no real-time market data" in caplog.text
    finally:
        rig.store.close()


def test_F3_changed_symbols_page_immediately_and_resolve_resets_condition(tmp_path):
    rig = _md_rig(tmp_path, start=et(10, 5))
    try:
        n = len(_md_pages(rig))
        rig.broker.no_subscription = ["AAPL", "TSLA"]
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_md_pages(rig)) == n + 1
        rig.broker.no_subscription = []
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        rig.broker.no_subscription = ["AAPL", "TSLA"]
        rig.advance(seconds=1)
        rig.engine._check_market_data(rig.clock.now())
        assert len(_md_pages(rig)) == n + 2
    finally:
        rig.store.close()


@pytest.mark.parametrize("level,minutes,expected", [("P1", 60, 2), ("P0", 30, 3)])
def test_F3_other_alert_repeat_windows_are_preserved(level, minutes, expected):
    ch = MemoryChannel()
    alerter = Alerter([ch], synchronous=True)
    for minute in range(minutes):
        alerter.send(et(10) + timedelta(minutes=minute), level, "other operational failure", "unchanged")
    assert len(ch.messages) == expected


def test_F4_code_sha_is_present_and_identical_in_all_four_outputs(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    seen = {}
    cli, _ = _fake_cli(tmp_path, monkeypatch, capture=seen)
    assert cli.main(["run", "--dry-run", "--config", str(tmp_path / "fake.toml")]) == 0
    start = next(r.getMessage() for r in caplog.records if "V6-Lite starting" in r.getMessage())
    match = re.search(r"code_sha[=:]\s*([0-9a-f]{16})\b", start)
    assert match, "startup line cannot identify the running code"
    sha = match.group(1)
    summary = next(r.getMessage() for r in caplog.records if "[STARTUP_SUMMARY]" in r.getMessage())
    assert json.loads(summary.split("[STARTUP_SUMMARY] ", 1)[1])["code_sha"] == sha
    assert seen["audit"]["code_sha"] == sha
    assert "code_sha" in seen["report"] and sha in seen["report"]


def test_F4_code_digest_covers_sorted_non_cache_files(tmp_path):
    from v6lite.version import code_sha

    a, b = tmp_path / "first", tmp_path / "second"
    for root, names in [(a, ["z.py", "README.md"]), (b, ["README.md", "z.py"])]:
        root.mkdir()
        for name in names:
            (root / name).write_text(name + "\n", encoding="utf-8")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "ignored.pyc").write_bytes(root.name.encode())
    digest = code_sha(a)
    assert re.fullmatch(r"[0-9a-f]{16}", digest)
    assert digest == code_sha(b), "creation/enumeration order and pycache must not affect code_sha"
    (a / "__pycache__" / "ignored.pyc").write_bytes(b"new cache")
    assert code_sha(a) == digest
    (a / "README.md").write_text("changed non-Python file", encoding="utf-8")
    assert code_sha(a) != digest, "all non-cache files, not only Python modules, must be covered"


def test_F5_history_logs_symbol_elapsed_count_and_last_date(tmp_path, monkeypatch, caplog):
    from v6lite.broker.ibkr import IbkrBroker
    caplog.set_level(logging.INFO)
    broker = IbkrBroker(make_cfg(tmp_path))
    broker._contracts["AAPL"] = object()
    bars = [SimpleNamespace(date=d, open=1, high=2, low=.5, close=1.5, volume=100)
            for d in ["2026-10-05", "2026-10-06"]]
    broker.ib = SimpleNamespace(reqHistoricalData=lambda *a, **k: bars)
    assert len(broker.daily_bars("AAPL", 90)) == 2
    rows = [r for r in caplog.records if "[HISTORY]" in r.getMessage()]
    assert len(rows) == 1 and rows[0].levelno == logging.INFO
    text = rows[0].getMessage()
    assert all(s in text for s in ["AAPL", "secs=", "bars=2", "last_date=2026-10-06"])
    assert "\n" not in text


@pytest.mark.parametrize("channel,api_name", [("positions", "reqPositions"), ("openOrders", "reqAllOpenOrders")])
def test_F5_query_elapsed_includes_old_end_drain(tmp_path, monkeypatch, caplog, channel, api_name):
    from v6lite.broker import ibkr
    caplog.set_level(logging.DEBUG)
    broker = ibkr.IbkrBroker(make_cfg(tmp_path))
    elapsed = [100.0]
    monkeypatch.setattr(ibkr.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(broker, "_ensure_channel_hooks", lambda: True)
    broker._req_sent[channel] = 1

    def sleep(seconds):
        elapsed[0] += seconds
        if elapsed[0] >= 100.4:
            broker._req_ended[channel] = 1

    def request():
        elapsed[0] += 1.25
        broker._req_sent[channel] += 1
        broker._req_ended[channel] += 1
        return []

    broker.ib = SimpleNamespace(sleep=sleep, **{api_name: request})
    (broker.positions_fresh if channel == "positions" else broker.open_orders)()
    rows = [r for r in caplog.records if "[QUERY]" in r.getMessage()]
    assert len(rows) == 1 and rows[0].levelno == logging.DEBUG
    text = rows[0].getMessage()
    assert channel in text or api_name in text
    assert "drained=" in text and "\n" not in text
    assert float(re.search(r"secs=([0-9.]+)", text).group(1)) >= 1.65


def test_F5_slow_tick_reports_elapsed_and_work_phase(dry_rig, monkeypatch, caplog):
    import time
    caplog.set_level(logging.INFO)
    elapsed = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])

    def slow_scan(now):
        elapsed[0] += 31.25
        dry_rig.clock.advance(seconds=31.25)

    monkeypatch.setattr(dry_rig.engine, "_run_scheduled_scans", slow_scan)
    dry_rig.tick()
    rows = [r for r in caplog.records if "[SLOW_TICK]" in r.getMessage()]
    assert len(rows) == 1 and rows[0].levelno == logging.INFO
    text = rows[0].getMessage()
    assert float(re.search(r"secs=([0-9.]+)", text).group(1)) >= 31.25
    assert "scan" in text.lower(), "elapsed alone cannot identify the slow work phase"
    assert "\n" not in text


@pytest.mark.parametrize("fail", [False, True], ids=["ok", "fail"])
def test_F6_delivery_result_is_single_line_and_never_contains_secret(tmp_path, caplog, fail):
    caplog.set_level(logging.INFO)
    secret_url = "https://example.invalid/webhooks/123/synthetic-test-token"

    class Channel:
        name = "discord_webhook"

        def send(self, subject, body):
            if fail:
                raise RuntimeError("delivery refused at " + secret_url)

    alerter = Alerter([Channel()], synchronous=True)
    rid = alerter.send(et(10), "INFO", "batch10 check", "synthetic body")
    assert alerter.delivery(rid) is (not fail)
    rows = [r for r in caplog.records if "[ALERT_DELIVERY]" in r.getMessage()]
    assert len(rows) == 1
    text = rows[0].getMessage()
    assert "discord_webhook" in text and re.search(r"\b" + ("fail" if fail else "ok") + r"\b", text)
    assert "ms" in text and "\n" not in text
    assert secret_url not in caplog.text and "synthetic-test-token" not in caplog.text


class _NonBlockingProbeIB(ProbeIB):
    def __init__(self):
        super().__init__()
        self.blocking_refreshes = 0
        self.subscriptions = []
        self.client = SimpleNamespace(reqAccountUpdates=self.resubscribe)

    def reqAccountUpdates(self, account):
        self.blocking_refreshes += 1

    def resubscribe(self, enabled, account):
        self.subscriptions.append((enabled, account))


def test_F7_probe_refresh_is_nonblocking_and_times_first_new_callback(tmp_path, monkeypatch):
    from v6lite.tools import g2_probe
    cfg = make_cfg(tmp_path)
    ib = _NonBlockingProbeIB()
    seconds = [0.0]
    monkeypatch.setattr(g2_probe, "_utc", lambda: et(10) + timedelta(seconds=seconds[0]))
    probe = g2_probe.Probe(cfg, ib, clock=lambda: seconds[0], sleep=lambda s: None)
    # An existing value is deliberately equal to the later push: new CALLBACK,
    # not a changed numeric value or an existing cache entry, proves the latency.
    old = SimpleNamespace(account=cfg.account, tag="AvailableFunds", currency="USD", value="2500")
    probe._on_account_value(old)
    probe.refresh_account()
    assert ib.blocking_refreshes == 0
    assert ib.subscriptions == [(False, cfg.account), (True, cfg.account)]
    probe.summarize_account_stream()
    assert probe.ev.findings["account_stream"].get("resubscribe_first_available_funds_seconds") is None
    seconds[0] = 1.75
    probe._on_account_value(old)
    probe.summarize_account_stream()
    assert probe.ev.findings["account_stream"]["resubscribe_first_available_funds_seconds"] == pytest.approx(1.75)
    seconds[0] = 4.0
    probe._on_account_value(old)
    probe.summarize_account_stream()
    assert probe.ev.findings["account_stream"]["resubscribe_first_available_funds_seconds"] == pytest.approx(1.75)


def test_F7_probe_timing_summary_keeps_full_names(tmp_path):
    from v6lite.tools.g2_probe import Probe
    ticks = iter(range(10000))
    ib = _NonBlockingProbeIB()
    ev = Probe(make_cfg(tmp_path), ib, clock=lambda: next(ticks), sleep=lambda s: None).run(0, False)
    summary = ev.findings["slowest_seconds_by_call"]
    names = {t["call"] for t in ev.timings}
    assert "history AAPL" in names and "reqExecutions(engine client)" in names
    assert set(summary) == names


def test_F7_report_counts_actual_unusable_quotes_not_only_subscription_errors(dry_rig):
    dry_rig.broker.quotes.clear()
    dry_rig.broker.no_subscription = []  # no refusal event does NOT prove quotes are usable
    dry_rig.clock.advance(minutes=11)
    dry_rig.tick()
    report = daily_report(dry_rig.store, dry_rig.cfg, et(10).date())
    n = len(dry_rig.cfg.scan_symbols)
    assert f"requested real-time; no usable quotes {n}/{n}" in report

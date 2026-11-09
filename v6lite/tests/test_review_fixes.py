"""Regression tests for the 2026-09-30 Codex review findings (docs/V6_LITE_REDESIGN.md §8.1).

Each test is named after its finding. They were written BEFORE the fixes and
failed against the original skeleton (see docs/V6_LITE_FIX_REPORT_2026-09-30.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import timedelta
from pathlib import Path

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.approval import GateError
from v6lite.broker.base import BrokerError
from v6lite.config import ConfigError, load_config
from v6lite.engine import Engine, StartupError
from v6lite.models import IntentStatus, Side
from v6lite.sizing import size_entry
from v6lite.tests.helpers import Rig, et, make_cfg

ROOT = Path(__file__).resolve().parents[2]


# ======================================================================= L1
def _pending_stop_state(rig: Rig) -> None:
    """Formal state with a PENDING STOP stage whose last attempt was cancelled."""
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    assert stop.purpose == "STOP"
    rig.broker.confirm_cancel(stop.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()


def test_L1_dry_run_engine_refuses_formal_state(tmp_path):
    rig = Rig(tmp_path)
    _pending_stop_state(rig)
    sent, cancels = len(rig.broker.sent), len(rig.broker.cancels)
    dry = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock, dry_run=True)
    with pytest.raises(StartupError):
        dry.startup()
    assert (len(rig.broker.sent), len(rig.broker.cancels)) == (sent, cancels)


def test_L1_dry_run_never_writes_to_broker_even_with_pending_orders(tmp_path):
    rig = Rig(tmp_path)
    _pending_stop_state(rig)
    # a SENT entry and an UNKNOWN order also exist in this state
    rig.store.set_meta("role", "dryrun")  # simulate a state file handed to dry-run
    sent, cancels = len(rig.broker.sent), len(rig.broker.cancels)
    attempts = rig.store.conn.execute("SELECT attempts FROM exit_stages WHERE stage='STOP'").fetchone()[0]
    dry = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock, dry_run=True)
    dry.startup()
    for _ in range(5):
        rig.advance(seconds=61)
        rig.px("AAPL", 107.0)
        dry.tick()
    dry.shutdown(grace_seconds=0)
    assert (len(rig.broker.sent), len(rig.broker.cancels)) == (sent, cancels)
    assert rig.store.conn.execute("SELECT attempts FROM exit_stages WHERE stage='STOP'").fetchone()[0] == attempts


def test_L1_ibkr_cancel_is_blocked_in_read_only_session(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.tests.test_ibkr_adapter import FakeIB
    broker = IbkrBroker(make_cfg(tmp_path), read_only=True)
    broker.ib = FakeIB()
    broker.ib.cancelOrder = lambda order: pytest.fail("cancel reached the broker")
    broker.ib.openTrades = lambda: []
    broker.connect()
    from types import SimpleNamespace
    broker._trades["v6l-abc"] = SimpleNamespace(order=SimpleNamespace(orderRef="v6l-abc", orderId=1))
    with pytest.raises(BrokerError):
        broker.cancel("v6l-abc")


# ======================================================================= L2
def test_L2_account_lock_is_exclusive_across_state_dirs(tmp_path):
    from v6lite.approval import AccountLock
    first = AccountLock("DU0000000", lock_dir=tmp_path / "locks")
    first.acquire()
    with pytest.raises(GateError):
        AccountLock("DU0000000", lock_dir=tmp_path / "locks").acquire()
    AccountLock("DU0000000", lock_dir=tmp_path / "locks").acquire()  # other account is independent
    first.release()
    AccountLock("DU0000000", lock_dir=tmp_path / "locks").acquire()


def test_L2_account_lock_is_exclusive_across_processes(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from v6lite.approval import AccountLock
        lock = AccountLock("DU0000000", lock_dir=Path({str(tmp_path / 'locks')!r}))
        lock.acquire()
        print("locked", flush=True)
        sys.stdin.readline()
        os._exit(0)  # simulate a crash: do not release the lock explicitly
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "locked"
        from v6lite.approval import AccountLock
        with pytest.raises(GateError):
            AccountLock("DU0000000", lock_dir=tmp_path / "locks").acquire()
    finally:
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.stdin.close()
        proc.wait()
    # the OS releases the lock when the holder dies: no stale-lock deadlock
    from v6lite.approval import AccountLock
    AccountLock("DU0000000", lock_dir=tmp_path / "locks").acquire()


def test_L2_foreign_json_v6_lock_is_understood(tmp_path):
    from v6lite.approval import AccountLock
    foreign = tmp_path / "trading_bot_v6.lock"
    foreign.write_text(json.dumps({"pid": os.getpid(), "started": "2026-09-30T09:00:00"}))
    with pytest.raises(GateError, match="another trading process"):
        AccountLock("DU0000000", lock_dir=tmp_path / "locks").acquire((str(foreign),))
    foreign.write_text(json.dumps({"pid": 999_999}))
    AccountLock("DU0000000", lock_dir=tmp_path / "locks").acquire((str(foreign),))


def _write_cfg(tmp_path: Path, name: str, account: str, state: Path) -> Path:
    src = (ROOT / "v6lite" / "examples" / "paper.toml").read_text(encoding="utf-8")
    src = src.replace('account = "DU0000000"', f'account = "{account}"')
    src = src.replace('state_dir = ""', f'state_dir = "{state.as_posix()}"')
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return path


def test_L2_control_commands_refuse_mismatched_account(tmp_path):
    from v6lite.__main__ import main
    from v6lite.exits import compute_levels
    from v6lite.store import Store
    from functools import partial
    state = tmp_path / "shared_state"
    good = _write_cfg(tmp_path, "good.toml", "DU0000000", state)
    bad = _write_cfg(tmp_path, "bad.toml", "DU0000000", state)
    cfg = load_config(good)
    store = Store(state / "v6lite.sqlite3", partial(compute_levels, cfg))
    store.bind_account("DU0000000", "paper")
    store.close()
    assert main(["kill", "--config", str(bad), "--reason", "wrong account"]) != 0
    store = Store(state / "v6lite.sqlite3", partial(compute_levels, cfg))
    assert store.kill_state() is None


def test_L2_status_without_state_creates_nothing(tmp_path):
    from v6lite.__main__ import main
    state = tmp_path / "never_run"
    cfg = _write_cfg(tmp_path, "c.toml", "DU0000000", state)
    main(["status", "--config", str(cfg)])
    assert not (state / "v6lite.sqlite3").exists()


def test_L2_revoked_live_approval_blocks_entries_but_not_exits(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "https://example.invalid/hook")
    state = {"valid": True}

    def checker(now):
        if not state["valid"]:
            raise GateError("live approval revoked")

    rig = Rig(tmp_path, mode="live", account="U0000000", port=4001, approval_checker=checker)
    rig.broker.accounts = ["U0000000"]
    rig.open_aapl()
    assert len(rig.broker.sent) == 1
    state["valid"] = False
    rig.advance(seconds=1)
    rig.tick()                                            # next tick re-validates the approval
    blocks = rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    assert "实盘审批失效" in rig.messages()
    assert "live approval no longer valid" in caplog.text
    from v6lite.models import OrderRequest, Purpose
    req = OrderRequest("v6l-probe", "SPY", Side.BUY, 1, 500.0, Purpose.ENTRY, "pos-probe")
    assert rig.engine._entry_evidence() is None         # batch 9: an entry takes its order evidence first
    assert rig.engine._send(rig.clock.now(), req, atr=1.0, count_entry_day="2026-10-06").startswith(
        "refused:live_approval")                          # final send boundary also re-checks
    assert any(b.startswith("live_approval") for b in blocks)
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    # L7 leaves the broker-side GTC stop-limit in place even when Live
    # approval is revoked; the engine must not send a second STOP SELL.
    assert not [order for order in rig.broker.sent if order.side is Side.SELL]
    protect = rig.broker.sent_protections[0]
    assert protect.intent_id in rig.broker.orders
    assert protect.intent_id not in rig.broker.cancels


# ======================================================================= L3
def test_L3_open_orders_come_from_a_fresh_broker_query(tmp_path):
    from types import SimpleNamespace
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.tests.test_ibkr_adapter import FakeIB
    broker = IbkrBroker(make_cfg(tmp_path))
    broker.ib = FakeIB()
    broker.connect()
    trade = SimpleNamespace(order=SimpleNamespace(orderRef="v6l-x", orderId=5, account="DU0000000"),
                            orderStatus=SimpleNamespace(status="Submitted", filled=0.0, remaining=5.0),
                            contract=SimpleNamespace(symbol="AAPL"))
    broker.ib.openTrades = lambda: []            # stale local cache
    broker.ib.reqAllOpenOrders = lambda: [trade]  # server truth
    assert [o.intent_id for o in broker.open_orders()] == ["v6l-x"]


def test_L3_settle_reads_open_orders_before_executions(tmp_path):
    rig = Rig(tmp_path)
    calls = []
    orig_open, orig_exec = rig.broker.open_orders, rig.broker.executions_today
    rig.broker.open_orders = lambda: calls.append("open") or orig_open()
    rig.broker.executions_today = lambda: calls.append("exec") or orig_exec()
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    calls.clear()
    rig.broker.confirm_cancel(req.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()
    assert calls[:2] == ["open", "exec"]


def test_L3_open_orders_failure_never_finalizes(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.broker.confirm_cancel(req.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.broker.fail_open_orders = True
    rig.advance(seconds=6)
    rig.tick()
    assert rig.store.intent(req.intent_id)["status"] == IntentStatus.UNKNOWN


def test_L3_prior_day_intent_without_evidence_stays_unknown(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.broker.orders.clear()                 # next day: DAY order gone, executions reset
    rig.clock.set(et(9, 50, day=(2026, 10, 7)))
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()
    row = rig.store.intent(req.intent_id)
    assert row["status"] == IntentStatus.UNKNOWN and "prior_day" in (row["note"] or "")


def test_L3_client_id_change_with_active_orders_aborts(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    other = make_cfg(tmp_path, client_id=132)
    engine2 = Engine(other, rig.broker, rig.store, rig.alerter, rig.clock)
    with pytest.raises(StartupError, match="client"):
        engine2.startup()


def test_L3_shutdown_cancels_unknown_orders_that_are_working(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.broker.fail_next_submit = BrokerError("socket closed mid-send", maybe_sent=True)
    rig.at(et(10, 0, 30))
    rig.tick()
    intent_id = next(iter(rig.broker.orders))
    assert rig.store.intent(intent_id)["status"] == IntentStatus.UNKNOWN
    rig.engine.shutdown(grace_seconds=0)
    assert intent_id in rig.broker.cancels


def test_L3_late_entry_fill_after_close_opens_a_managed_position(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = rig.broker.sent[0]
    rig.broker.fill(entry.intent_id, 10)                 # partial entry
    rig.advance(seconds=61)
    rig.tick()                                           # timeout -> cancel
    rig.broker.confirm_cancel(entry.intent_id)
    rig.advance(seconds=6)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()                                           # settled: 10 shares
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    rig.broker.fill(stop.intent_id)                      # position closed
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    rig.broker.fill(entry.intent_id, 5)                  # the "cancelled" entry fills late
    rig.advance(seconds=1)
    rig.tick()
    hidden = rig.store.conn.execute(
        "SELECT COUNT(*) FROM positions WHERE status='CLOSED' AND qty_open>0").fetchone()[0]
    assert hidden == 0
    opened = rig.store.open_positions()
    assert len(opened) == 1 and opened[0]["qty_open"] == 5
    assert any(k.startswith("anomaly:late_entry_fill") for k in rig.store.anomalies())


def test_L3_exit_waits_for_broker_truth_after_hidden_late_sell(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    rig.broker.confirm_cancel(stop.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()                                           # STOP attempt 1 settled CANCELLED
    rig.broker.fill(stop.intent_id, 10, emit=False)      # late execution, no callback
    sells_before = len([r for r in rig.broker.sent if r.side is Side.SELL])
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert rig.store.open_positions()[0]["qty_open"] == 10   # late fill ingested first
    # batch 6: a pending STOP is not silenced by the difference; it reaches the
    # send boundary only after the truth was learned and sells the TRUE rest
    new = [r for r in rig.broker.sent if r.side is Side.SELL][sells_before:]
    assert all(r.quantity == 10 for r in new), new


# ======================================================================= L4
def test_L4_remaining_capital_uses_actual_equity(tmp_path):
    cfg = make_cfg(tmp_path)
    res = size_entry(cfg, equity=3_000, price=100.0, atr=1.0, deployed_notional=2_800)
    assert res.quantity * 100.0 <= 200.0 - 1.0


def test_L4_entry_blocked_when_available_funds_short(tmp_path):
    rig = Rig(tmp_path)
    rig.broker.available = 500.0
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.broker.sent == []


def test_L4_duplicate_execution_backfills_commission(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    fill = rig.broker.fill(req.intent_id, commission=None)
    rig.advance(seconds=1)
    rig.tick()
    from dataclasses import replace
    rig.broker.events.append(replace(fill, commission=2.0))   # replay from reqExecutions
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.fees_for_day("2026-10-06") == 2.0
    assert rig.store.open_positions()[0]["qty_open"] == 20


def test_L4_non_integer_shares_raise_anomaly_instead_of_rounding(tmp_path):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.models import AnomalyEvent
    from v6lite.tests.test_ibkr_adapter import FakeIB
    broker = IbkrBroker(make_cfg(tmp_path))
    broker.ib = FakeIB()
    broker.connect()
    ex = SimpleNamespace(execId="e1", orderRef="v6l-abc", side="BOT", shares=10.5, price=100.0,
                         time=datetime(2026, 10, 6, 14, tzinfo=timezone.utc), orderId=1, permId=2,
                         acctNumber="DU0000000")
    broker._on_exec(None, SimpleNamespace(execution=ex, contract=SimpleNamespace(symbol="AAPL")))
    events = broker.drain_events()
    assert len(events) == 1 and isinstance(events[0], AnomalyEvent)


@pytest.mark.parametrize("section,key,value", [
    ("risk", "max_positions", "2.5"),
    ("risk", "max_positions", "true"),
    ("execution", "entry_fill_timeout_seconds", "-5"),
    ("execution", "max_quote_age_seconds", "0"),
    ("risk", "atr_stop_mult", "2.5"),          # strategy key placed in the risk section
    ("risk", "daily_loss_limit_usd", "nan"),
])
def test_L4_config_numbers_and_sections_are_strict(tmp_path, section, key, value):
    src = (ROOT / "v6lite" / "examples" / "paper.toml").read_text(encoding="utf-8")
    lines = [ln for ln in src.splitlines() if not ln.startswith(f"{key} =")]
    out = []
    for ln in lines:
        out.append(ln)
        if ln.strip() == f"[{section}]":
            out.append(f"{key} = {value}")
    path = tmp_path / "c.toml"
    path.write_text("\n".join(out), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_L4_prior_close_baseline_catches_overnight_loss(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()                                          # 20 @ 113.47
    rig.broker.prev_close["AAPL"] = 113.0
    rig.clock.set(et(9, 45, day=(2026, 10, 7)))
    rig.px("AAPL", 113.0)
    rig.tick()                                               # new trading day starts
    rig.clock.set(et(11, 0, day=(2026, 10, 7)))
    rig.px("AAPL", 105.0)                                    # -8 * 20 = -160 today
    rig.tick()
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 1


def test_L4_trade_result_uses_net_pnl_and_estimates_missing_commissions(tmp_path):
    """Round 2 (R7): the streak is derived from closed trades, so it never
    waits for -- or forgets -- a commission. Until a commission is reported it
    is counted at the B30 history/config-minimum estimate. Real fees replace
    the P&L estimate; the already-triggered cooldown remains latched."""
    from v6lite.models import CommissionEvent
    from v6lite.risk import cooldown_until, loss_state
    from v6lite.tests.helpers import seed_closed_trade
    rig = Rig(tmp_path)
    rig.start()
    seed_closed_trade(rig.store, "pos-prior", -10.0, et(9, 40))            # one prior loss
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = rig.broker.sent[0]
    rig.broker.fill(entry.intent_id, price=113.47, commission=0.0)
    rig.advance(seconds=1)
    rig.tick()
    rig.store.conn.execute("UPDATE positions SET tp1=113.49, tp2=113.6, tp3=113.7 WHERE status='OPEN'")
    rig.advance(seconds=31)
    rig.px("AAPL", 113.8)
    rig.tick()                                               # TP1 sells 6
    for _ in range(4):
        req = rig.broker.sent[-1]
        if rig.store.intent(req.intent_id)["status"] in ("SENT", "PARTIAL"):
            rig.broker.fill(req.intent_id, price=113.495, commission_separately=True, commission=None)
        rig.advance(seconds=31)
        rig.px("AAPL", 113.8)
        rig.tick()
    assert rig.store.open_positions() == []
    # gross +0.50, sell commissions unknown -> estimated -> counted as a loss now
    prior_until = cooldown_until(rig.store, rig.cfg)
    assert prior_until is not None
    for row in rig.store.conn.execute("SELECT exec_id FROM fills WHERE side='SELL'").fetchall():
        rig.broker.events.append(CommissionEvent(row[0], 0.01))              # real fees are tiny
    rig.advance(seconds=1)
    rig.tick()
    assert loss_state(rig.store, rig.cfg).derived_cooldown_until is None  # final net +0.47
    assert loss_state(rig.store, rig.cfg).cooldown_until == prior_until  # B30 no automatic release
    assert loss_state(rig.store, rig.cfg).streak == 0


# ======================================================================= L5
def test_L5_alert_health_reflects_the_latest_attempt(tmp_path):
    from datetime import datetime, timezone
    channel = MemoryChannel()
    alerter = Alerter([channel], synchronous=True)
    now = datetime(2026, 10, 6, 14, tzinfo=timezone.utc)
    assert alerter.send(now, "INFO", "a")
    assert alerter.healthy(now)
    channel.fail = True
    alerter.send(now + timedelta(minutes=1), "INFO", "b")
    assert not alerter.healthy(now + timedelta(minutes=1))


def test_L5_live_config_cannot_disable_required_alerts(tmp_path, monkeypatch):
    from v6lite.config import AlertConfig
    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "https://example.invalid/hook")
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, mode="live", account="U0000000", port=4001,
                 alerts=AlertConfig(require_remote_in_live=False))


def test_L5_slow_alert_channel_does_not_block_the_loop():
    from datetime import datetime, timezone

    class Slow:
        name = "slow"

        def send(self, subject, body):
            time.sleep(2.0)

    alerter = Alerter([Slow()])
    start = time.monotonic()
    alerter.send(datetime.now(timezone.utc), "P1", "x", "y")
    assert time.monotonic() - start < 0.3
    alerter.close()


def test_L5_daily_report_is_marked_sent_only_after_delivery(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.channel.fail = True
    rig.at(et(16, 6))
    rig.tick()
    assert not str(rig.store.control("report:2026-10-06")).startswith("sent")
    rig.channel.fail = False
    rig.advance(minutes=16)
    rig.tick()
    assert str(rig.store.control("report:2026-10-06")).startswith("sent")
    assert sum("行情可用率：" in b and "code_sha：" in b
               for _, b in rig.channel.messages) == 1


def test_L5_scan_keeps_servicing_exits_between_symbol_fetches(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    calls = []
    original = rig.engine._service_protection
    rig.engine._service_protection = lambda now: calls.append(now) or original(now)
    rig.at(et(10, 0, 30))
    rig.tick()
    assert len(calls) >= len(rig.cfg.scan_symbols)


def test_L5_quote_age_is_checked_with_fresh_time_after_slow_data(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    original = rig.broker.daily_bars

    def slow_bars(symbol, days):
        rig.clock.advance(seconds=8)       # 3 symbols -> 24s of history requests
        return original(symbol, days)
    rig.broker.daily_bars = slow_bars
    rig.tick()
    assert rig.broker.sent == []           # quotes stamped at 10:00:30 are stale by then


# ======================================================================= misc
def test_foreign_fill_on_protected_symbol_is_not_an_anomaly(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.broker.external_fill("MSTR", Side.BUY, 1, 300.0)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.anomalies() == {}

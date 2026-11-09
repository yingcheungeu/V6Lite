"""Regression tests for Codex's independent review of 2026-09-30 (R1-R12, the
cancel/late-fill race and the P2 items). Tests of `repair-position` and
`adopt` were retired in batch 3 (both features removed); their scenarios are
covered by the `resync` tests in test_review_round3.py.

Every test was written to reproduce the reviewer's failing sequence BEFORE the
fix; the report lists the before/after result of each one.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.approval import GateError
from v6lite.broker.base import BrokerError
from v6lite.config import ConfigError, load_config
from v6lite.engine import Engine, StartupError
from v6lite.exits import compute_levels
from v6lite.models import CommissionEvent, FillEvent, IntentStatus, OrderRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, buy_bars, et, make_cfg

DAY2 = (2026, 10, 7)
IB_EXEC = "0000e0d5.{:08x}.01.01"


def _write_cfg(tmp_path: Path, name: str, account: str, state: Path, extra: str = "") -> Path:
    text = f"""
mode = "paper"
[ibkr]
account = "{account}"
port = 4002
client_id = 131
[universe]
scan = ["AAPL"]
tradable = ["AAPL"]
[paths]
state_dir = "{state.as_posix()}"
[alerts]
email_to = "ops@example.invalid"
smtp_host = "smtp.example.invalid"
{extra}
"""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _sells(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.side is Side.SELL]


def _entries(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


# ======================================================================= R11
def test_R11_legacy_lock_owner_access_denied_fails_closed(tmp_path, monkeypatch):
    import psutil
    from v6lite.approval import AccountLock
    legacy = tmp_path / "v6.lock"
    legacy.write_text('{"pid": 12345}', encoding="utf-8")

    class Denied:
        def __init__(self, pid):
            raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", Denied)
    lock = AccountLock("DU0000000", lock_dir=tmp_path / "locks")
    with pytest.raises(GateError):
        lock.acquire(foreign=(str(legacy),))
    assert not lock.held


def test_R11_vanished_legacy_pid_does_not_block(tmp_path, monkeypatch):
    import psutil
    from v6lite.approval import AccountLock
    legacy = tmp_path / "v6.lock"
    legacy.write_text('{"pid": 12345}', encoding="utf-8")

    class Gone:
        def __init__(self, pid):
            raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", Gone)
    lock = AccountLock("DU0000000", lock_dir=tmp_path / "locks")
    lock.acquire(foreign=(str(legacy),))
    assert lock.held
    lock.release()


# ======================================================================= R1
def test_R1_orphan_working_order_from_another_state_dir_blocks_entries(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert len(_entries(rig)) == 1                       # AAPL entry working at the broker
    other = Store(tmp_path / "other_state" / "v6lite.sqlite3", partial(compute_levels, rig.cfg))
    engine2 = Engine(rig.cfg, rig.broker, other, rig.alerter, rig.clock)
    rig.advance(seconds=5)
    engine2.startup()                                    # "crash", restart on a fresh state dir
    rig.advance(seconds=1)
    engine2.tick()
    assert len(_entries(rig)) == 1                       # no second AAPL entry
    assert any(k.startswith("anomaly:orphan_order") for k in other.anomalies())
    assert engine2.status_snapshot(rig.clock.now())["entry_blocks"]


def test_R1_client_id_change_refused_while_broker_has_working_orders(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = _entries(rig)[0]
    # the ledger lost track of it (e.g. restored from an older copy)
    rig.store.update_intent(rig.clock.now(), entry.intent_id, status=IntentStatus.CANCELLED)
    other = make_cfg(tmp_path, client_id=132)
    engine2 = Engine(other, rig.broker, rig.store, rig.alerter, rig.clock)
    with pytest.raises(StartupError, match="working"):
        engine2.startup()


# ======================================================================= R2
def _unknown_entry(rig: Rig) -> str:
    rig.start()
    rig.broker.fail_next_submit = BrokerError("socket closed mid-send", maybe_sent=True)
    rig.at(et(10, 0, 30))
    rig.tick()
    intent_id = next(iter(rig.broker.orders))
    assert rig.store.intent(intent_id)["status"] == IntentStatus.UNKNOWN
    return intent_id


def test_R2_prior_day_unknown_survives_shutdown(tmp_path):
    rig = Rig(tmp_path)
    intent_id = _unknown_entry(rig)
    rig.broker.orders.clear()                            # next day: DAY order gone, executions reset
    rig.clock.set(et(9, 50, day=DAY2))
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()
    engine2.shutdown(grace_seconds=0)
    assert rig.store.intent(intent_id)["status"] == IntentStatus.UNKNOWN


def test_R2_unknown_carried_across_midnight_by_a_running_engine_stays_unknown(tmp_path):
    rig = Rig(tmp_path)
    intent_id = _unknown_entry(rig)
    rig.broker.orders.clear()
    rig.clock.set(et(9, 50, day=DAY2))
    rig.tick()
    rig.advance(seconds=61)
    rig.tick()
    row = rig.store.intent(intent_id)
    assert row["status"] == IntentStatus.UNKNOWN
    assert rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]


def test_R2_note_overwrite_cannot_release_an_operator_hold(tmp_path):
    rig = Rig(tmp_path)
    intent_id = _unknown_entry(rig)
    rig.broker.orders.clear()
    rig.clock.set(et(9, 50, day=DAY2))
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()
    rig.store.update_intent(rig.clock.now(), intent_id, note="some later broker message")
    rig.advance(seconds=61)
    engine2.tick()
    assert rig.store.intent(intent_id)["status"] == IntentStatus.UNKNOWN


# ======================================================================= R3
def _closed_after_partial_entry(rig: Rig) -> OrderRequest:
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = rig.broker.sent[0]
    rig.broker.fill(entry.intent_id, 10)
    rig.advance(seconds=61)
    rig.tick()
    rig.broker.confirm_cancel(entry.intent_id)
    rig.advance(seconds=6)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    rig.broker.fill(stop.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    return entry


def test_R3_late_entry_fill_in_two_batches_is_fully_managed(tmp_path):
    rig = Rig(tmp_path)
    entry = _closed_after_partial_entry(rig)
    rig.px("AAPL", 113.0)
    rig.broker.fill(entry.intent_id, 5, price=113.40)
    rig.advance(seconds=1)
    rig.tick()
    rig.broker.fill(entry.intent_id, 5, price=113.50)
    rig.advance(seconds=1)
    rig.tick()
    opened = rig.store.open_positions()
    assert len(opened) == 1 and opened[0]["qty_open"] == 10
    assert opened[0]["entry_price"] == pytest.approx(113.45)
    assert not any(k.startswith("anomaly:duplicate_open_position") for k in rig.store.anomalies())
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert rig.broker.sent[-1].purpose == "STOP" and rig.broker.sent[-1].quantity == 10


def test_R3_late_entry_replay_after_restart_is_idempotent(tmp_path):
    rig = Rig(tmp_path)
    entry = _closed_after_partial_entry(rig)
    rig.broker.fill(entry.intent_id, 5, price=113.40)
    rig.advance(seconds=1)
    rig.tick()
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()                                    # replays today's executions
    rig.broker.fill(entry.intent_id, 5, price=113.50)
    rig.advance(seconds=1)
    engine2.tick()
    assert [r["qty_open"] for r in rig.store.open_positions()] == [10]


# ======================================================================= R4
def test_R4_mismatch_found_by_reconcile_first_still_recovers_executions(tmp_path):
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
    rig.engine._reconcile(rig.clock.now())               # reconcile sees the difference first
    for _ in range(4):
        rig.advance(seconds=61)
        rig.px("AAPL", 107.0)
        rig.tick()
    assert rig.store.open_positions()[0]["qty_open"] == 10
    assert _sells(rig)[-1].quantity == 10                # STOP resumed for the real remainder


# ======================================================================= cancel / late-fill race
def test_race_late_fill_of_cancelled_stop_cancels_the_retry(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop1 = rig.broker.sent[-1]
    rig.broker.confirm_cancel(stop1.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop2 = rig.broker.sent[-1]
    assert stop2.intent_id != stop1.intent_id and stop2.quantity == 20
    rig.broker.fill(stop1.intent_id, 20)                 # the "cancelled" STOP had filled after all
    rig.advance(seconds=1)
    rig.tick()
    assert stop2.intent_id in rig.broker.cancels         # retry pulled before it can oversell
    assert any(k.startswith("anomaly:late_exit_fill") for k in rig.store.anomalies())


def test_race_final_sell_boundary_counts_other_working_sells(tmp_path):
    from decimal import Decimal, ROUND_UP

    rig = Rig(tmp_path)
    rig.open_aapl()
    pos = rig.store.open_positions()[0]
    assert not rig.cfg.protection.enabled and pos["qty_open"] == pos["qty_initial"] == 20
    # B23's immutable TP pricing evidence is produced by the real fresh-quote
    # decision/queue path; a hand-constructed TP cannot grant send permission.
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    rig.px("AAPL", pos["tp1"] + .10)
    quote = rig.broker.quote("AAPL")
    assert quote is not None and quote.bid is not None and quote.age_seconds(rig.clock.now()) == 0
    rig.engine._evaluate_exits(rig.clock.now())
    assert rig.store.stage(pos["position_id"], Purpose.TP1)["status"] == "PENDING"
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    [tp] = [req for req in _sells(rig) if req.purpose is Purpose.TP1]
    expected = (Decimal(str(quote.bid)) * Decimal(".998")).quantize(Decimal(".01"), rounding=ROUND_UP)
    assert tp.quantity == 6 and Decimal(str(tp.limit_price)) == expected
    assert rig.store.intent(tp.intent_id)["status"] == IntentStatus.SENT
    [working] = [order for order in rig.broker.open_orders() if order.side == "SELL"]
    assert working.intent_id == tp.intent_id and working.remaining == 6
    assert working.order_type == "LMT" and working.tif == "DAY"
    assert rig.store.position(pos["position_id"])["qty_open"] == 20
    capacity, detail = rig.engine._sell_capacity("AAPL", fresh=False)
    assert capacity == 14, detail
    before = (len(rig.broker.sent), len(rig.broker.sent_protections),
              len(rig.broker.modified_protections), len(rig.broker.cancels))
    stop = OrderRequest("v6l-stopprobe", "AAPL", Side.SELL, 20, 105.0, Purpose.STOP, pos["position_id"])
    assert rig.engine._send(rig.clock.now(), stop, atr=None, count_entry_day=None) == "refused:sell_exceeds_broker_qty"
    assert rig.store.intent(stop.intent_id) is None
    assert (len(rig.broker.sent), len(rig.broker.sent_protections),
            len(rig.broker.modified_protections), len(rig.broker.cancels)) == before
    assert _sells(rig) == [tp] and rig.broker.orders[tp.intent_id].remaining == 6
    assert rig.store.position(pos["position_id"])["qty_open"] == 20
    assert rig.store.intent_filled_qty(tp.intent_id) == 0
    assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]


# ======================================================================= R5
def _two_signal_rig(tmp_path, **kw) -> Rig:
    rig = Rig(tmp_path, **kw)
    rig.broker.bars["SPY"] = buy_bars()
    rig.px("SPY", 113.13)
    return rig


def test_R5_consecutive_entries_cannot_reuse_the_same_funds(tmp_path):
    rig = _two_signal_rig(tmp_path)
    rig.broker.available = 2_500.0
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entries = _entries(rig)
    assert sum(r.quantity * r.limit_price for r in entries) <= 2_500.0
    assert len(entries) == 1


def test_R5_fill_after_the_funds_snapshot_is_reserved(tmp_path):
    rig = _two_signal_rig(tmp_path)
    rig.broker.available = 2_500.0
    rig.start()
    rig.at(et(10, 0, 30))
    rig.broker.bars.pop("SPY")                           # only AAPL signals at 10:00
    rig.tick()
    rig.broker.fill(_entries(rig)[0].intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.broker.funds_as_of = et(10, 0, 20)               # funds value last changed BEFORE the fill
    rig.broker.funds_seen_at = rig.clock.now()           # ...although the account stream is alive
    rig.px("SPY", 113.13)
    rig.broker.bars["SPY"] = buy_bars()
    reason = rig.engine._try_enter(rig.clock.now(), __import__("v6lite.models", fromlist=["Signal"]).Signal(
        "SPY", 78, 113.13, 2.0))
    assert "insufficient_funds" in reason


def test_R5_stale_or_unready_funds_refuse_entries(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.broker.funds_as_of = et(9, 0)                    # older than the freshness limit
    rig.tick()
    assert _entries(rig) == []
    rig2 = Rig(tmp_path / "b")
    rig2.start()
    rig2.at(et(10, 0, 30))
    rig2.broker.funds_ready = False
    rig2.tick()
    assert _entries(rig2) == []


# ======================================================================= R6
def test_R6_baseline_uses_prior_close_holdings_not_current_quantity(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()                                      # day 1: 20 @ 113.47
    rig.broker.prev_close["AAPL"] = None                 # prior close unavailable at first
    rig.clock.set(et(9, 45, day=DAY2))
    rig.px("AAPL", 120.0)
    rig.tick()                                           # TP1 (119.47) triggers while baseline pending
    tp1 = rig.broker.sent[-1]
    assert tp1.purpose == "TP1"
    rig.broker.fill(tp1.intent_id, price=120.0)
    rig.advance(seconds=1)
    rig.tick()
    rig.broker.prev_close["AAPL"] = 130.0                # data restored: yesterday closed at 130
    rig.advance(minutes=5)
    rig.px("AAPL", 120.0)
    rig.tick()
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 1   # 20 x -10 = -200


def test_R6_prior_close_unknown_blocks_entries_but_not_exits(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.broker.prev_close["AAPL"] = None
    rig.clock.set(et(10, 0, 30, day=DAY2))
    rig.px("AAPL", 113.2)
    rig.tick()
    blocks = rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    assert any("baseline" in b for b in blocks)
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert rig.broker.sent[-1].purpose == "STOP"


# ======================================================================= R7
def test_R7_late_commission_after_close_still_counts_the_loss(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    t0 = et(9, 40).isoformat()
    rig.store.conn.execute(
        "INSERT INTO positions(position_id, symbol, status, source, entry_price, atr, qty_initial, qty_open, "
        "stop, tp1, tp2, tp3, realized_pnl, opened_at, closed_at) VALUES "
        "('pos-prior','SPY','CLOSED','strategy',100,1,1,0,97.5,103,105,108,-10,?,?)", (t0, t0))
    rig.store.set_control(rig.clock.now(), "loss_streak", "1")          # (legacy counter form)
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = rig.broker.sent[0]
    rig.broker.fill(entry.intent_id, price=113.47, commission=0.0)
    rig.advance(seconds=1)
    rig.tick()
    rig.store.conn.execute("UPDATE positions SET tp1=113.47, tp2=113.48, tp3=113.49 WHERE status='OPEN'")
    for _ in range(6):
        req = rig.broker.sent[-1]
        if req.side is Side.SELL and rig.store.intent(req.intent_id)["status"] in ("SENT", "PARTIAL"):
            rig.broker.fill(req.intent_id, price=113.48, commission=None, commission_separately=True)
        rig.advance(seconds=31)
        rig.px("AAPL", 113.6)
        rig.tick()
    assert rig.store.open_positions() == []              # gross +0.20, sell commissions unknown
    rig.at(et(16, 6))
    rig.tick()                                           # end of day report
    for row in rig.store.conn.execute("SELECT exec_id FROM fills WHERE side='SELL'").fetchall():
        rig.broker.events.append(CommissionEvent(row[0], 1.0))
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.control("cooldown_until") is not None   # net -0.80: second consecutive loss


# ======================================================================= R8
def test_R8_stop_fill_during_scan_latches_before_the_next_entry(tmp_path):
    rig = Rig(tmp_path, scan_times_et=("10:00", "11:00"))
    rig.open_aapl()                                      # 20 @ 113.47
    rig.broker.bars["TSLA"] = buy_bars()                 # TSLA signals at the 11:00 scan
    rig.px("TSLA", 113.13)
    rig.at(et(11, 0, 30))
    calls = {"n": 0}
    original = rig.broker.daily_bars

    def slow(symbol, days):
        calls["n"] += 1
        if calls["n"] == 1:
            rig.px("AAPL", 107.0)
            rig.clock.advance(seconds=31)
        elif calls["n"] == 2:
            stop = rig.broker.sent[-1]
            assert stop.purpose == "STOP"
            rig.broker.fill(stop.intent_id, price=105.91)
            rig.clock.advance(seconds=1)
        rig.refresh_quotes()
        return original(symbol, days)
    rig.broker.daily_bars = slow
    rig.tick()
    assert rig.store.risk_day("2026-10-06")["daily_stop_latched"] == 1   # 151.20 + 2.00 fees
    assert len(_entries(rig)) == 1


# ======================================================================= R9
def test_R9_live_entry_checks_alert_health_at_the_send_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "https://example.invalid/hook")
    rig = Rig(tmp_path, mode="live", account="U0000000", port=4001, approval_checker=lambda now: None)
    rig.broker.accounts = ["U0000000"]
    rig.start()
    rig.at(et(10, 0, 30))
    original = rig.broker.daily_bars

    def slow(symbol, days):
        rig.channel.fail = True
        rig.alerter.send(rig.clock.now(), "P2", f"probe {symbol}")      # every delivery now fails
        return original(symbol, days)
    rig.broker.daily_bars = slow
    rig.tick()
    assert _entries(rig) == []


# ======================================================================= R10
def test_R10_slow_requests_never_stretch_the_exit_check_interval(tmp_path):
    rig = Rig(tmp_path, scan_times_et=("10:00", "11:00"))
    rig.open_aapl()
    rig.at(et(11, 0, 30))
    checks = []
    evaluate = rig.engine._evaluate_position_exit

    def record(now, row, broker_qty):
        checks.append(rig.clock.now())
        return evaluate(now, row, broker_qty)
    rig.engine._evaluate_position_exit = record
    bars, vix = rig.broker.daily_bars, rig.broker.vix_close

    def slow_bars(symbol, days):
        rig.clock.advance(seconds=8)
        return bars(symbol, days)

    def slow_vix():
        rig.clock.advance(seconds=25)
        return vix()
    rig.broker.daily_bars, rig.broker.vix_close = slow_bars, slow_vix
    rig.tick()
    for _ in range(40):
        rig.advance(seconds=1)
        rig.tick()
    gaps = [(b - a).total_seconds() for a, b in zip(checks, checks[1:])]
    assert gaps and max(gaps) <= rig.cfg.exit_check_interval_seconds + 1, gaps


# ======================================================================= R12
def test_R12_exec_id_correction_is_quarantined_not_added(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = rig.broker.sent[0]
    rig.broker.fill(entry.intent_id, 20, exec_id=IB_EXEC.format(1))
    rig.advance(seconds=1)
    rig.tick()
    correction = FillEvent("0000e0d5.00000001.01.02", entry.intent_id, "AAPL", Side.BUY, 20, 113.40,
                           rig.clock.now(), commission=1.0)
    rig.broker.events.append(correction)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions()[0]["qty_open"] == 20
    assert any(k.startswith("anomaly:exec_correction") for k in rig.store.anomalies())
    rig.broker.events.append(correction)                  # replay: still quarantined, still 20
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions()[0]["qty_open"] == 20


def test_R12_commission_correction_replaces_the_old_value(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    exec_id = rig.store.conn.execute("SELECT exec_id FROM fills").fetchone()[0]
    rig.broker.events.append(CommissionEvent(exec_id, 9.0))
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.fees_for_day("2026-10-06") == pytest.approx(9.0)


# ======================================================================= P2
def test_P2_delivery_ids_do_not_collide_across_processes():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 6, 14, tzinfo=timezone.utc)
    a = Alerter([MemoryChannel()], synchronous=True).send(now, "INFO", "x")
    b = Alerter([MemoryChannel()], synchronous=True).send(now, "INFO", "x")
    assert a != b


def test_P2_report_pending_from_a_previous_process_is_resent(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.store.set_control(et(16, 5), "report:2026-10-06", f"pending:old-process-a1:{et(16, 5).isoformat()}")
    rig.at(et(16, 7))
    rig.tick()
    assert str(rig.store.control("report:2026-10-06")).startswith("sent")


def test_P2_failed_alert_is_not_swallowed_by_dedupe():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 6, 14, tzinfo=timezone.utc)
    channel = MemoryChannel(fail=True)
    alerter = Alerter([channel], synchronous=True)
    alerter.send(now, "P1", "broker/runtime mismatch", "x")
    channel.fail = False
    assert alerter.send(now + timedelta(seconds=1), "P1", "broker/runtime mismatch", "x") is not None
    assert channel.messages


@pytest.mark.parametrize("port", ["true", "-5", "2.5", "nan", "70000"])
def test_P2_smtp_port_is_validated(tmp_path, port):
    path = _write_cfg(tmp_path, "c.toml", "DU0000000", tmp_path / "s", extra=f"smtp_port = {port}")
    with pytest.raises(ConfigError):
        load_config(path)


def test_P2_status_and_report_refuse_another_accounts_state(tmp_path):
    from v6lite.__main__ import main
    state = tmp_path / "shared_state"
    good = _write_cfg(tmp_path, "good.toml", "DU0000000", state)
    bad = _write_cfg(tmp_path, "bad.toml", "DU0000000", state)
    store = Store(state / "v6lite.sqlite3", partial(compute_levels, load_config(good)))
    store.bind_account("DU0000000", "paper")
    store.close()
    assert main(["status", "--config", str(bad)]) == 2
    assert main(["report", "--config", str(bad)]) == 2
    assert main(["status", "--config", str(good)]) == 0


def test_P2_fee_reserve_scales_with_share_count():
    from v6lite.sizing import estimate_fee
    assert estimate_fee(20, 113.47) >= 2.0
    assert estimate_fee(2_000, 5.0) > 2.0 * 5                # 2,000 shares cost more than a flat $2


def test_P2_adapter_takes_commission_from_the_canonical_fill(tmp_path):
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    t = datetime(2026, 10, 6, 14, 1, tzinfo=timezone.utc)
    execution = SimpleNamespace(execId="0000e0d5.00000001.01.01", orderRef="v6l-abc", side="BOT", shares=10.0,
                                price=113.4, time=t, orderId=77, permId=12345, acctNumber="DU0000000")
    contract = SimpleNamespace(symbol="AAPL")
    empty = SimpleNamespace(execId="", commission=0.0)
    canonical = SimpleNamespace(execution=execution, contract=contract,
                                commissionReport=SimpleNamespace(execId=execution.execId, commission=1.25))
    replay = SimpleNamespace(execution=execution, contract=contract, commissionReport=empty)
    broker.ib = SimpleNamespace(reqExecutions=lambda flt: [replay],
                                wrapper=SimpleNamespace(fills={execution.execId: canonical}))
    [event] = broker.executions_today()
    assert event.commission == 1.25


# ======================================================================= supplementary coverage
def test_R3_three_late_batches_at_different_prices(tmp_path):
    rig = Rig(tmp_path)
    entry = _closed_after_partial_entry(rig)
    for qty, price in ((3, 113.40), (3, 113.50), (4, 113.60)):
        rig.broker.fill(entry.intent_id, qty, price=price)
        rig.advance(seconds=1)
        rig.tick()
    [pos] = rig.store.open_positions()
    assert pos["qty_open"] == 10
    assert pos["entry_price"] == pytest.approx((3 * 113.40 + 3 * 113.50 + 4 * 113.60) / 10)


def test_R5_cancelled_entry_releases_its_reservation(tmp_path):
    rig = _two_signal_rig(tmp_path)
    rig.broker.available = 2_500.0
    rig.start()
    rig.at(et(10, 0, 30))
    rig.broker.bars.pop("SPY")
    rig.tick()
    entry = _entries(rig)[0]
    rig.advance(seconds=61)
    rig.tick()                                           # fill timeout -> cancel
    rig.broker.confirm_cancel(entry.intent_id)
    rig.advance(seconds=6)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()
    assert rig.store.intent(entry.intent_id)["status"] == IntentStatus.CANCELLED
    from v6lite.models import Signal
    assert rig.engine._try_enter(rig.clock.now(), Signal("SPY", 78, 113.13, 2.0)) == "sent"


def test_R5_adapter_funds_come_from_the_account_stream(tmp_path):
    from types import SimpleNamespace
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    assert broker.available_funds().value is None            # nothing heard yet -> unusable
    value = lambda tag, v: SimpleNamespace(account="DU0000000", tag=tag, value=v, currency="USD")
    broker._on_account_value(value("AvailableFunds", "2500.5"))
    snap = broker.available_funds()
    assert snap.value == 2500.5 and snap.as_of is not None and snap.seen_at is not None and snap.ready
    broker._on_account_value(value("AccountReady", "false"))
    assert not broker.available_funds().ready
    broker._on_account_value(SimpleNamespace(account="DU0000000", tag="AvailableFunds", value="1e9",
                                             currency="USD"))
    assert broker.available_funds().value == 2500.5         # other accounts ignored
    broker._events = []
    broker._on_disconnect()
    assert broker.available_funds().value is None            # reconnect must re-learn


def test_R6_full_exit_while_baseline_pending_counts_from_prior_close(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()                                      # day 1: 20 @ 113.47
    rig.broker.prev_close["AAPL"] = None
    rig.clock.set(et(9, 45, day=DAY2))
    rig.px("AAPL", 107.0)
    rig.tick()                                           # STOP 108.47 triggers
    stop = rig.broker.sent[-1]
    assert stop.purpose == "STOP"
    rig.broker.fill(stop.intent_id, price=106.0)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    rig.broker.prev_close["AAPL"] = 115.0
    rig.advance(minutes=5)
    rig.tick()
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 1   # 20 x (106 - 115) = -180


def test_R6_restart_recomputes_the_same_daily_loss(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.broker.prev_close["AAPL"] = 116.0
    rig.clock.set(et(10, 30, day=DAY2))
    rig.px("AAPL", 111.0)                                # 20 x -5 = -100 (below the 150 limit)
    rig.tick()
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()
    rig.advance(seconds=1)
    rig.px("AAPL", 108.4)                                # 20 x -7.6 = -152 -> latch
    engine2.tick()
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 1


def test_R7_commission_reported_before_its_fill_is_used(tmp_path):
    from v6lite.risk import loss_state
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    fill = rig.broker.fill(stop.intent_id, price=113.50, emit=False)
    rig.broker.events += [CommissionEvent(fill.exec_id, 1.0), fill]     # out of order
    rig.advance(seconds=1)
    rig.tick()
    [trade] = rig.store.closed_trades()
    assert trade["missing"] == [] and trade["fees"] == pytest.approx(2.0)
    assert loss_state(rig.store, rig.cfg).streak == 1                   # +0.60 gross - 2.00 fees


def test_R8_commission_arriving_during_scan_can_latch(tmp_path):
    rig = Rig(tmp_path, scan_times_et=("10:00", "11:00"))
    rig.open_aapl()
    rig.broker.bars["TSLA"] = buy_bars()
    rig.px("TSLA", 113.13)
    rig.at(et(11, 0, 30))
    calls = {"n": 0}
    original = rig.broker.daily_bars

    def slow(symbol, days):
        calls["n"] += 1
        if calls["n"] == 1:
            rig.px("AAPL", 107.0)
            rig.clock.advance(seconds=31)
        elif calls["n"] == 2:
            stop = rig.broker.sent[-1]
            rig.broker.fill(stop.intent_id, price=106.30, commission=None, commission_separately=True)
            rig.clock.advance(seconds=1)                 # 20 x 7.17 = 143.40 + 1 entry fee: below 150
        elif calls["n"] == 3:
            exec_id = rig.store.conn.execute("SELECT exec_id FROM fills WHERE side='SELL'").fetchone()[0]
            rig.broker.events.append(CommissionEvent(exec_id, 6.0))   # 150.40 -> over the limit
            rig.clock.advance(seconds=1)
        rig.refresh_quotes()
        return original(symbol, days)
    rig.broker.daily_bars = slow
    rig.tick()
    assert rig.store.risk_day("2026-10-06")["daily_stop_latched"] == 1
    assert len(_entries(rig)) == 1


def test_R9_alert_outage_blocks_live_entries_but_never_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "https://example.invalid/hook")
    rig = Rig(tmp_path, mode="live", account="U0000000", port=4001, approval_checker=lambda now: None)
    rig.broker.accounts = ["U0000000"]
    rig.open_aapl()
    rig.channel.fail = True
    rig.alerter.send(rig.clock.now(), "P2", "probe")
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    # L7 keeps the broker-side GTC stop-limit working through alert outage;
    # a second local STOP SELL would oversell if the GTC also fills.
    assert not [order for order in rig.broker.sent if order.side is Side.SELL]
    protect = rig.broker.sent_protections[0]
    assert protect.intent_id in rig.broker.orders
    assert protect.intent_id not in rig.broker.cancels
    from v6lite.models import Signal
    assert "alerts_unavailable" in rig.engine._try_enter(rig.clock.now(), Signal("SPY", 78, 500.0, 2.0)) or \
        rig.engine._send(rig.clock.now(), OrderRequest("v6l-p", "SPY", Side.BUY, 1, 500.0, Purpose.ENTRY, "pos-p"),
                         atr=1.0, count_entry_day="2026-10-06").startswith("refused")


def test_R1_dry_run_does_not_treat_formal_orders_as_orphans(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()                                           # formal AAPL entry working
    dry = Store(tmp_path / "dry" / "v6lite.sqlite3", partial(compute_levels, rig.cfg))
    dry.bind_identity(rig.cfg.account, rig.cfg.mode, role="dryrun", client_id=rig.cfg.client_id + 1000)
    engine = Engine(rig.cfg, rig.broker, dry, rig.alerter, rig.clock, dry_run=True)
    engine.startup()
    rig.advance(seconds=1)
    engine.tick()
    assert dry.anomalies() == {}


# ======================================================================= self-review follow-ups (pre-handoff)
def _held_tp1(rig: Rig, *, tp1_really_sold: int = 0):
    """Day 1: AAPL 20 @113.47, TP1 sent 15:58, settle evidence fails until after
    the close -> UNKNOWN; day 2 restart -> prior_day hold. `tp1_really_sold`
    shares of it did trade (visible in the broker position, not in today's
    executions)."""
    rig.open_aapl()
    rig.at(et(15, 58))
    rig.px("AAPL", 120.0)
    rig.tick()
    tp1 = rig.broker.sent[-1]
    assert tp1.purpose == "TP1"
    rig.broker.fail_executions = True
    rig.advance(seconds=46)
    rig.tick()
    rig.advance(seconds=61)
    rig.tick()
    assert rig.store.intent(tp1.intent_id)["status"] == IntentStatus.UNKNOWN
    rig.broker.fail_executions = False
    rig.broker.orders[tp1.intent_id].status = "Cancelled"   # DAY order expired at the close
    if tp1_really_sold:
        rig.broker.external_positions["AAPL"] = (-float(tp1_really_sold), 0.0)
    rig.clock.set(et(9, 45, day=DAY2))
    rig.px("AAPL", 119.0)
    engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine.startup()
    engine.tick()
    assert rig.store.intent(tp1.intent_id)["hold_reason"] == "prior_day_unverifiable"
    return engine, tp1


def test_C2_prior_day_held_sell_never_blocks_the_stop(tmp_path):
    rig = Rig(tmp_path)
    engine, _ = _held_tp1(rig)
    before = len(_sells(rig))
    rig.advance(seconds=31)
    rig.px("AAPL", 100.0)                                # far below the 108.47 stop
    engine.tick()
    new = _sells(rig)[before:]
    assert new and new[0].purpose == "STOP" and new[0].quantity == 20
    assert engine.status_snapshot(rig.clock.now())["entry_blocks"]      # entries still blocked by the hold


def test_C6_cancel_or_status_never_moves_a_held_intent(tmp_path):
    import argparse
    from v6lite.__main__ import cmd_resolve_intent
    from v6lite.models import StatusEvent
    rig = Rig(tmp_path)
    engine, tp1 = _held_tp1(rig)
    engine._request_cancel(rig.clock.now(), tp1.intent_id, "late_fill_of_settled_exit")
    rig.broker.events.append(StatusEvent(tp1.intent_id, "Cancelled", 0, 6))
    rig.advance(seconds=1)
    engine.tick()
    row = rig.store.intent(tp1.intent_id)
    assert row["status"] == IntentStatus.UNKNOWN and row["hold_reason"]
    assert any(b.startswith("unknown_order_state") for b in engine.status_snapshot(rig.clock.now())["entry_blocks"])
    cmd_resolve_intent(argparse.Namespace(intent_id=tp1.intent_id, operator="J", reason="TWS: expired"), rig.store)
    assert rig.store.intent(tp1.intent_id)["status"] == IntentStatus.CANCELLED


def _two_open_positions(rig: Rig) -> None:
    rig.broker.bars["SPY"] = buy_bars()
    rig.px("SPY", 113.13)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    for entry in _entries(rig):
        rig.broker.fill(entry.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    assert sorted(r["symbol"] for r in rig.store.open_positions()) == ["AAPL", "SPY"]


def test_C3_slow_refresh_for_one_position_does_not_delay_the_others(tmp_path):
    rig = Rig(tmp_path)
    _two_open_positions(rig)
    checks = []
    evaluate = rig.engine._evaluate_position_exit

    def record(now, row, broker_qty):
        if row["symbol"] == "SPY":
            checks.append(rig.clock.now())
        return evaluate(now, row, broker_qty)
    rig.engine._evaluate_position_exit = record
    executions = rig.broker.executions_today

    def slow_executions():
        rig.clock.advance(seconds=15)
        return executions()
    rig.broker.executions_today = slow_executions
    for i in range(70):
        if i == 35:
            rig.broker.external_positions["AAPL"] = (-5.0, 0.0)
        rig.advance(seconds=1)
        rig.tick()
    gaps = [(b - a).total_seconds() for a, b in zip(checks, checks[1:])]
    assert max(gaps) <= rig.cfg.exit_check_interval_seconds + 1, gaps


def test_C4_adapter_cancel_uses_cached_trades_before_a_server_query(tmp_path):
    from types import SimpleNamespace
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    trade = SimpleNamespace(order=SimpleNamespace(orderRef="v6l-x", clientId=broker.client_id,
                                                  account=broker.cfg.account, orderType="LMT", tif="DAY"))
    cancelled = []

    def no_server_query():
        raise AssertionError("reqAllOpenOrders must not be needed")
    broker.ib = SimpleNamespace(openTrades=lambda: [trade], trades=lambda: [], reqAllOpenOrders=no_server_query,
                                cancelOrder=lambda order: cancelled.append(order))
    broker.cancel("v6l-x")
    assert cancelled == [trade.order]


def test_C5_exit_orders_are_stamped_with_the_real_send_time(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.tick()
    rig.advance(seconds=31)
    open_orders = rig.broker.open_orders
    state = {"n": 0}

    def slow_open_orders():
        state["n"] += 1
        if state["n"] == 1:
            rig.clock.advance(seconds=17)
            rig.px("AAPL", 107.0)
        return open_orders()
    rig.broker.open_orders = slow_open_orders
    rig.engine._reconcile_due = True
    rig.tick()
    stop = rig.broker.sent[-1]
    assert stop.purpose == "STOP"
    assert rig.store.intent(stop.intent_id)["sent_at"] == rig.clock.now().isoformat()


def test_S1_link_restored_resubscribes_market_data(tmp_path):
    from v6lite.models import ConnectionEvent
    rig = Rig(tmp_path)
    rig.start()
    rig.broker.events.append(ConnectionEvent(False, "1100:lost"))
    rig.advance(seconds=1)
    rig.tick()
    rig.broker.events.append(ConnectionEvent(True, "1101:restored, data lost"))
    rig.advance(seconds=31)
    rig.tick()
    assert getattr(rig.broker, "subscription_refreshes", 0) == 1
    assert rig.engine.flags.broker_connected


def test_S2_fill_just_before_the_funds_change_time_is_still_reserved(tmp_path):
    rig = _two_signal_rig(tmp_path)
    rig.broker.available = 2_500.0
    rig.start()
    rig.at(et(10, 0, 30))
    rig.broker.bars.pop("SPY")
    rig.tick()
    rig.broker.fill(_entries(rig)[0].intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.broker.funds_as_of = rig.clock.now() + timedelta(seconds=5)   # local clock ahead of IBKR's
    rig.broker.funds_seen_at = rig.clock.now()
    from v6lite.models import Signal
    assert "insufficient_funds" in rig.engine._try_enter(rig.clock.now(), Signal("SPY", 78, 113.13, 2.0))


def test_S3_quiet_account_stream_is_re_requested(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 30))
    rig.broker.funds_as_of = et(10, 20)
    rig.broker.funds_seen_at = et(10, 20)
    rig.broker.account_answers_refresh = True     # simulated actual nonblocking funds snapshot reply
    rig.advance(seconds=61)
    rig.tick()
    assert getattr(rig.broker, "account_refreshes", 0) == 0
    assert rig.broker.funds_refreshes >= 1
    assert rig.broker.funds_seen_at >= et(10, 30)


def test_status_never_migrates_or_touches_another_accounts_file(tmp_path):
    import sqlite3
    from v6lite.__main__ import main
    state = tmp_path / "shared_state"
    good = _write_cfg(tmp_path, "good.toml", "DU0000000", state)
    bad = _write_cfg(tmp_path, "bad.toml", "DU0000000", state)
    store = Store(state / "v6lite.sqlite3", partial(compute_levels, load_config(good)))
    store.bind_account("DU0000000", "paper")
    store.close()
    conn = sqlite3.connect(state / "v6lite.sqlite3")
    conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    assert main(["status", "--config", str(bad)]) == 2
    conn = sqlite3.connect(state / "v6lite.sqlite3")
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    conn.close()

"""Batch-4 self-review (adversarial pass before delivery), R1-R7 plus the
follow-ups it raised. Each test failed on the pre-fix batch-4 code.

R1  refusing an old state file must not write to it
R2  a frozen symbol blocks entries until `release` (not a clearable anomaly)
R3  never freeze on partial evidence when fresh positions already match
R4  a FAILED stop stage is never re-sent by the send-time re-decision
R5  a difference found by the exit path blocks entries in the same tick
R6  the daily report shows releases and frozen symbols
R7  a frozen symbol does not force a reconcile every tick
+   freeze withdraws V6-Lite's own working orders; a short on an ignored
    symbol is never ignored; a blocked STOP pages P0
"""

from __future__ import annotations

import hashlib
import sqlite3
from functools import partial

import pytest

from v6lite.exits import compute_levels
from v6lite.models import Purpose, Side, Signal
from v6lite.store import Store, StoreError
from v6lite.tests.helpers import Rig, buy_bars, et, make_cfg
from v6lite.tests.test_review_round4 import _stale_cache_after_hidden_sell


def _sells(rig):
    return [r for r in rig.broker.sent if r.side is Side.SELL]


def _entries(rig):
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


# --------------------------------------------------------------------------
# R1: "older state files are refused before anything is opened/migrated":
# Store() runs the v4 CREATE script BEFORE the version check, so a real v3
# file (no `releases` table) is written to, then refused.
def test_R1_real_v3_state_is_not_modified_by_the_refusal(tmp_path):
    cfg = make_cfg(tmp_path)
    path = tmp_path / "s" / "v6lite.sqlite3"
    Store(path, partial(compute_levels, cfg)).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE releases")                       # v3 had `resyncs`, not `releases`
    conn.execute("CREATE TABLE resyncs (resync_id TEXT PRIMARY KEY)")
    conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    before_tables = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(StoreError, match="fresh"):
        Store(path, partial(compute_levels, cfg))
    after_tables = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")}
    assert after_tables == before_tables, sorted(after_tables - before_tables)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


# --------------------------------------------------------------------------
# R2: a frozen symbol only blocks entries through a generic, clearable
# anomaly. Once the quantity happens to match again (operator sold part of
# it) and the operator clears the anomaly, entries resume while the frozen
# position still has no automated stop.
def test_R2_entries_stay_blocked_while_a_symbol_is_frozen(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.store.conn.execute("UPDATE positions SET qty_open=10, qty_initial=10")   # ledger lost 10
    for _ in range(4):
        rig.advance(seconds=31)
        rig.tick()
    assert rig.store.control("frozen:AAPL") is not None
    rig.broker.external_positions["AAPL"] = (-10.0, 0.0)     # operator sold 10 in TWS: broker 10 == ledger 10
    rig.advance(seconds=31)
    rig.engine._reconcile_due = True
    rig.tick()
    assert rig.engine.flags.reconcile_ok                      # difference gone, symbol still frozen
    with pytest.raises(StoreError, match="release"):
        rig.store.clear_anomaly(rig.clock.now(), "frozen:AAPL", "J", "looks fine now")
    rig.store.delete_control("anomaly:frozen:AAPL")           # even with the anomaly gone by hand
    rig.px("SPY", 113.13)
    outcome = rig.engine._try_enter(rig.clock.now(), Signal("SPY", 78, 113.13, 2.0))
    rig.advance(seconds=31)
    rig.px("AAPL", 100.0)                                     # far below the stop
    rig.tick()
    aapl_sells = [s for s in _sells(rig) if s.symbol == "AAPL"]
    assert outcome != "sent", (outcome, "AAPL frozen and unprotected", aapl_sells)


# --------------------------------------------------------------------------
# R3: _maybe_freeze freezes on the OLD result whenever the forced execution
# refresh reports failure -- even when the missing execution was booked
# meanwhile (callback drained inside the refresh) and a fresh position
# read in the same step matches the ledger. False P0 + forced flatten.
def test_R3_no_freeze_when_fresh_evidence_matches_after_a_failed_refresh(tmp_path):
    rig = Rig(tmp_path)
    stop, late = _stale_cache_after_hidden_sell(rig)
    real = rig.engine._refresh_fills

    def refresh(now, *, force=False):
        if force:
            # the execution callback arrives while the query runs, then the query times out
            rig.engine._ingest_fill(rig.clock.now(), late)
            return False
        return real(now, force=force)
    rig.engine._refresh_fills = refresh
    for _ in range(4):
        rig.advance(seconds=31)
        rig.tick()
    ledger = rig.store.open_positions()[0]["qty_open"]
    truth = next(p.quantity for p in rig.broker.positions_fresh() if p.symbol == "AAPL")
    assert ledger == truth == 10
    assert rig.store.control("frozen:AAPL") is None, "frozen although fresh broker == ledger"


# --------------------------------------------------------------------------
# R4: _redecide ignores begin_stage()'s result. With a STOP stage already
# FAILED (operator paged "manual action required"), a queued TP1 that meets
# a stop price at send time is converted into a STOP and SENT on the
# FAILED stage -- something the decision pass never does.
def test_R4_redecide_never_sends_on_a_failed_stop_stage(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    pid = rig.store.open_positions()[0]["position_id"]
    rig.store.begin_stage(rig.clock.now(), pid, Purpose.STOP, 20)
    rig.store.finish_stage(rig.clock.now(), pid, Purpose.STOP, "FAILED")   # retries exhausted earlier
    real = rig.broker.open_orders
    state = {"armed": False}

    def open_orders():
        if state["armed"]:
            state["armed"] = False
            rig.px("AAPL", 107.0)                             # stop crossed while the evidence is taken
        return real()
    rig.broker.open_orders = open_orders
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)                                     # TP1 zone -> TP1 queued
    rig.engine._last_reconcile = rig.clock.now()              # no reconcile this tick
    rig.engine._reconcile_due = False
    state["armed"] = True
    rig.tick()
    assert rig.store.stage(pid, Purpose.STOP)["status"] == "FAILED"
    assert not [s for s in _sells(rig) if s.purpose == "STOP"], _sells(rig)


# --------------------------------------------------------------------------
# R5: the send boundary DISCOVERS broker != ledger from fresh evidence but
# only marks the symbol; reconcile_ok stays True until the next tick, so the
# scan later in the SAME tick still sends a BUY (F3's rule: a difference
# found during the flow must block entries immediately).
def test_R5_difference_found_at_the_exit_boundary_blocks_entries_at_once(tmp_path, caplog):
    rig = Rig(tmp_path, scan_times_et=("10:00", "10:02"))
    rig.open_aapl()                                           # ends ~10:00:31, reconcile ok
    assert rig.engine.flags.reconcile_ok
    rig.broker.external_positions["AAPL"] = (-2.0, 0.0)       # truth 18, no callback
    rig.broker.stale_positions = {"AAPL": 20.0}               # cache still 20
    rig.broker.bars["SPY"] = buy_bars()                       # SPY becomes a signal at 10:02
    rig.at(et(10, 2, 5))
    rig.px("SPY", 113.13)
    rig.px("AAPL", 107.0)                                     # stop -> exit boundary takes fresh evidence
    rig.tick()
    assert "止损下单被拦截" in rig.messages() and "AAPL" in rig.messages()
    assert "exit blocked AAPL" in caplog.text                  # difference found in this tick
    assert not [e for e in _entries(rig) if e.symbol == "SPY"], "BUY sent in the tick that found the difference"


# R5b: same, without any stale cache: the decision pass itself sees the
# broker/ledger difference (18 vs 20), only sets _reconcile_due, and the
# scan later in the same tick still buys.
def test_R5b_difference_seen_by_the_decision_pass_blocks_entries_at_once(tmp_path):
    rig = Rig(tmp_path, scan_times_et=("10:00", "10:02"))
    rig.open_aapl()
    assert rig.engine.flags.reconcile_ok
    rig.broker.external_positions["AAPL"] = (-2.0, 0.0)       # truth and cache 18, ledger 20
    rig.broker.bars["SPY"] = buy_bars()
    rig.at(et(10, 2, 5))
    rig.px("SPY", 113.13)
    rig.tick()
    assert not [e for e in _entries(rig) if e.symbol == "SPY"], "BUY sent while AAPL 18 != ledger 20 was known"


# R6: the end-of-day report never shows a release (event list still names
# the retired POSITION_REPAIRED, not POSITION_RELEASED / RELEASE_REFUSED).
def test_R6_daily_report_shows_the_release(tmp_path):
    from v6lite.report import daily_report
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)
    rig.advance(seconds=61)
    rig.store.set_control(rig.clock.now(), "release:AAPL", '{"operator": "J", "reason": "flat"}')
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    text = daily_report(rig.store, rig.cfg, et(12).date())
    assert "POSITION_RELEASED" in text and "AAPL 20 sh" in text, text


# R7: after a freeze, _mismatch_since keeps the frozen symbol, so
# _reconcile_due stays True and EVERY tick (~1 s in run_engine) issues a
# reqPositions + reqAllOpenOrders until the operator releases -- hours of
# per-second server queries (baseline: persistent diffs reconciled every
# reconcile_interval_seconds=300).
def test_R7_a_frozen_symbol_does_not_force_a_reconcile_every_tick(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.store.conn.execute("UPDATE positions SET qty_open=10, qty_initial=10")
    for _ in range(4):
        rig.advance(seconds=31)
        rig.tick()
    assert rig.store.control("frozen:AAPL") is not None
    calls = {"positions": 0, "orders": 0}
    rp, ro = rig.broker.positions_fresh, rig.broker.open_orders

    def pf():
        calls["positions"] += 1
        return rp()

    def oo():
        calls["orders"] += 1
        return ro()
    rig.broker.positions_fresh, rig.broker.open_orders = pf, oo
    rig.engine._evaluate_exits = lambda *a, **k: None          # isolate reconcile's own queries
    for _ in range(60):                                          # one minute of 1 s ticks
        rig.advance(seconds=1)
        rig.tick()
    assert calls["orders"] <= 2, calls


# --------------------------------------------------------------------------
# follow-ups
def test_R3_partial_evidence_defers_then_freezes_after_the_grace(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.store.conn.execute("UPDATE positions SET qty_open=10, qty_initial=10")   # real, unexplained
    rig.broker.fail_executions = True                          # evidence stays incomplete
    for _ in range(4):
        rig.advance(seconds=31)
        rig.tick()
    assert rig.store.control("frozen:AAPL") is None
    assert "P1" in rig.messages() and "AAPL" in rig.messages()
    assert "freeze check deferred" in caplog.text
    assert not _entries(rig)[1:] and not _sells(rig)          # nothing traded meanwhile
    for _ in range(5):
        rig.advance(seconds=31)
        rig.tick()
    assert rig.store.control("frozen:AAPL") is not None
    assert "V6-Lite 停止管理持仓" in rig.messages()
    assert "incomplete after the grace period" in caplog.text


def test_freeze_withdraws_our_own_working_orders(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)                                     # TP1 zone
    rig.tick()
    tp1 = [s for s in _sells(rig) if s.purpose == Purpose.TP1]
    assert tp1, _sells(rig)
    rig.broker.external_positions["AAPL"] = (-3.0, 0.0)       # unexplained: broker 17, ledger 20
    for _ in range(4):
        rig.advance(seconds=31)
        rig.engine._reconcile_due = True
        rig.tick()
    assert rig.store.control("frozen:AAPL") is not None
    assert tp1[0].intent_id in rig.broker.cancels
    [remote] = [body for subject, body in rig.channel.messages
                if "P0" in subject and "V6-Lite 停止管理持仓" in subject]
    assert len(remote.splitlines()) == 4
    assert "TWS" in remote and tp1[0].intent_id in remote
    assert remote.index(tp1[0].intent_id) < remote.index("保护或平仓")
    assert remote.index("保护或平仓") < remote.index("release AAPL")
    assert any("cancel requests were sent" in rec.getMessage() and tp1[0].intent_id in rec.getMessage()
               for rec in caplog.records)


def test_a_short_on_an_ignored_symbol_is_not_ignored(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.store.set_disposition(rig.clock.now(), "TSLA", "ignore", "J")
    rig.broker.external_positions["TSLA"] = (-5.0, 0.0)
    rig.advance(seconds=1)
    rig.engine._reconcile_due = True
    rig.tick()
    assert not rig.engine.flags.reconcile_ok
    assert any("TSLA: short broker position" in d for d in rig.engine.flags.reconcile_detail)


def test_a_stop_blocked_by_missing_evidence_pages_p0(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.broker.fail_open_orders = True
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)                                     # stop zone
    rig.tick()
    assert not _sells(rig)
    blocked = [subject for subject, _ in rig.channel.messages if "止损下单被拦截" in subject]
    assert blocked and all("P0" in subject for subject in blocked), rig.channel.messages
    assert "exit blocked AAPL STOP" in caplog.text

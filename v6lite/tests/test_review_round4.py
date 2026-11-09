"""Regression tests for Codex's third review (F1-F9) and the batch-4 scope cuts:

* v1 runs only on FRESH state (no migrations of older state files)       F6
* `resync` is removed: an unexplained quantity difference FREEZES the
  symbol; the operator flattens it in TWS and `release`s the ledger
  position once a fresh broker query shows 0 shares and no working order  F2 F4 F5
* SELL evidence: a failed fresh query refuses the SELL (no cached fallback),
  fresh positions (reqPositions) are compared with the ledger              F1 F2
* every exit is re-decided with the latest quote right before sending      F7
* the check interval must fit the largest blocking call; back-off is
  honoured at execution time                                               F8
* combo (BAG) orders reserve their stock legs                              F9
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from types import SimpleNamespace

import pytest

from v6lite.engine import Engine, StartupError
from v6lite.exits import compute_levels
from v6lite.models import IntentStatus, OrderRequest, Purpose, Side
from v6lite.store import Store, StoreError
from v6lite.tests.helpers import Rig, et, make_cfg
from v6lite.tests.test_review_round2 import _two_open_positions, _write_cfg


def _sells(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.side is Side.SELL]


def _true_qty(rig: Rig, symbol: str) -> float:
    return next((p.quantity for p in rig.broker.positions_fresh() if p.symbol == symbol), 0.0)


def _release(rig: Rig, symbol: str) -> int:
    from v6lite.__main__ import main
    cfg_path = _write_cfg(rig.cfg_tmp, "release.toml", rig.cfg.account, rig.cfg.resolved_state_dir())
    return main(["release", symbol, "--config", str(cfg_path), "--operator", "John", "--reason", "flattened in TWS"])


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    r.cfg_tmp = tmp_path
    return r


# ======================================================================= F1
def test_F1_failed_fresh_order_query_refuses_the_sell(rig, caplog):
    rig.open_aapl()                                          # snapshot of working orders: empty
    rig.broker._register(OrderRequest("tws-manual", "AAPL", Side.SELL, 20, 100.0, Purpose.STOP, None))
    rig.broker.fail_open_orders = True                       # the next fresh query times out
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert [s for s in _sells(rig) if s.intent_id.startswith("v6l-")] == []
    assert "止损下单被拦截" in rig.messages()
    assert "exit blocked" in caplog.text


# ======================================================================= F2 / freeze / release
def _stale_cache_after_hidden_sell(rig: Rig):
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    rig.broker.confirm_cancel(stop.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()                                               # STOP settled CANCELLED
    late = rig.broker.fill(stop.intent_id, 10, emit=False)   # really sold 10 ...
    rig.broker.invisible_execs.add(late.exec_id)             # ... execution not visible yet
    rig.broker.stale_positions = {"AAPL": 20.0}              # position cache still says 20
    return stop, late


def test_F2_stale_position_cache_never_sizes_a_sell(rig):
    _stale_cache_after_hidden_sell(rig)
    for _ in range(3):
        rig.advance(seconds=31)
        rig.px("AAPL", 107.0)
        rig.tick()
    assert not [s for s in _sells(rig)[1:] if s.quantity > 10]   # no STOP 20 on 10 real shares
    assert _true_qty(rig, "AAPL") >= 0


def test_F2_persistent_difference_freezes_the_symbol(rig, caplog):
    _stale_cache_after_hidden_sell(rig)
    for _ in range(4):
        rig.advance(seconds=31)
        rig.px("AAPL", 107.0)
        rig.tick()
    assert any(k.startswith("anomaly:frozen:AAPL") for k in rig.store.anomalies())
    assert "V6-Lite 停止管理持仓" in rig.messages() and "AAPL" in rig.messages()
    assert "V6-Lite no longer manages AAPL" in caplog.text
    sells_before = len(_sells(rig))
    rig.broker.stale_positions = None
    rig.broker.invisible_execs.clear()                       # truth becomes visible later
    for _ in range(3):
        rig.advance(seconds=31)
        rig.px("AAPL", 107.0)
        rig.tick()
    assert len(_sells(rig)) == sells_before                  # frozen: V6-Lite never trades it again


def test_F2_release_after_flatten_closes_the_ledger_position(rig):
    stop, late = _stale_cache_after_hidden_sell(rig)
    for _ in range(4):
        rig.advance(seconds=31)
        rig.px("AAPL", 107.0)
        rig.tick()
    rig.broker.stale_positions = None
    rig.broker.external_positions["AAPL"] = (-10.0, 0.0)     # operator flattened the rest in TWS
    rig.advance(seconds=61)
    assert _release(rig, "AAPL") == 0
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    row = rig.store.conn.execute("SELECT status FROM positions WHERE symbol='AAPL'").fetchone()
    assert row["status"] == "RELEASED"
    day = rig.store.risk_day("2026-10-06")
    assert day["daily_stop_latched"] == 1 and "release" in day["latched_reason"]
    rig.broker.invisible_execs.clear()                       # the old execution shows up afterwards
    rig.advance(seconds=31)
    rig.tick()
    row = rig.store.conn.execute("SELECT status FROM positions WHERE symbol='AAPL'").fetchone()
    assert row["status"] == "RELEASED"                       # never re-opened or counted as a trade
    assert rig.store.closed_trades() == []


def test_F3_refused_release_still_publishes_the_latest_positions(rig, caplog):
    rig.open_aapl()
    rig.broker.external_positions["AAPL"] = (-2.0, 0.0)      # truth 18
    rig.broker.stale_positions = {"AAPL": 20.0}              # cache 20
    rig.advance(seconds=1)
    assert _release(rig, "AAPL") == 0
    rig.advance(seconds=1)
    rig.tick()
    assert "P1" in rig.messages() and "AAPL" in rig.messages()
    assert "release refused" in caplog.text
    assert not rig.engine.flags.reconcile_ok
    from v6lite.models import Signal
    rig.broker.bars["SPY"] = __import__("v6lite.tests.helpers", fromlist=["buy_bars"]).buy_bars()
    rig.px("SPY", 113.13)
    assert rig.engine._try_enter(rig.clock.now(), Signal("SPY", 78, 113.13, 2.0)) != "sent"


def test_F5_release_and_the_day_freeze_commit_together(rig):
    rig.open_aapl()
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)
    pos = rig.store.open_positions()[0]

    def boom(*a, **k):
        raise RuntimeError("crash between release and day freeze")
    original = rig.store.ensure_risk_day
    rig.store.ensure_risk_day = boom
    with pytest.raises(RuntimeError):
        rig.store.release_position(rig.clock.now(), "AAPL", day="2026-10-06", equity=10_000.0,
                                   operator="J", reason="r")
    rig.store.ensure_risk_day = original
    assert rig.store.position(pos["position_id"])["status"] == "OPEN"       # nothing half-applied
    assert rig.store.release_times() == []
    rig.store.release_position(rig.clock.now(), "AAPL", day="2026-10-06", equity=10_000.0, operator="J",
                               reason="r")
    reopened = Store(rig.store.path, partial(compute_levels, rig.cfg))
    assert reopened.risk_day("2026-10-06")["daily_stop_latched"] == 1
    assert reopened.position(pos["position_id"])["status"] == "RELEASED"


def test_release_refused_while_orders_work_or_shares_remain(rig, caplog):
    rig.open_aapl()
    rig.advance(seconds=61)
    assert _release(rig, "AAPL") == 0                        # broker still holds 20
    rig.advance(seconds=1)
    rig.tick()
    assert "P1" in rig.messages() and "AAPL" in rig.messages() and rig.store.open_positions()
    assert "release refused" in caplog.text


def test_resync_command_no_longer_exists(rig, tmp_path):
    from v6lite.__main__ import main
    cfg_path = _write_cfg(tmp_path, "x.toml", rig.cfg.account, rig.cfg.resolved_state_dir())
    with pytest.raises(SystemExit):
        main(["resync", "AAPL", "--broker-qty", "1", "--config", str(cfg_path), "--operator", "J", "--reason", "r"])


# ======================================================================= F6
@pytest.mark.parametrize("old", ["1", "2", "3"])
def test_F6_v1_refuses_state_files_from_earlier_versions(tmp_path, old):
    import sqlite3
    cfg = make_cfg(tmp_path)
    path = tmp_path / "s" / "v6lite.sqlite3"
    Store(path, partial(compute_levels, cfg)).close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (old,))
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(StoreError, match="fresh"):
        Store(path, partial(compute_levels, cfg))
    assert path.read_bytes() == before


# ======================================================================= F7
def test_F7_exit_is_re_decided_with_the_latest_quote_before_sending(rig):
    rig.open_aapl()
    real = rig.broker.open_orders
    state = {"slow": False}

    def slow_open_orders():
        if state["slow"]:
            rig.clock.advance(seconds=15)
            rig.px("AAPL", 107.0)                            # falls through the stop meanwhile
        return real()
    rig.broker.open_orders = slow_open_orders
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)                                    # TP1 zone -> TP1 queued
    state["slow"] = True
    rig.tick()
    sent = _sells(rig)
    assert sent and sent[-1].purpose == "STOP" and sent[-1].quantity == 20
    assert not [s for s in sent if s.purpose == "TP1"]


# ======================================================================= F8
def test_F8_check_interval_must_fit_the_largest_blocking_call(tmp_path):
    rig = Rig(tmp_path, exit_check_interval_seconds=10)
    with pytest.raises(StartupError, match="exit_check_interval"):
        rig.engine.startup()


def test_F8_back_off_is_honoured_when_the_queued_item_runs(tmp_path):
    rig = Rig(tmp_path)
    _two_open_positions(rig)
    starts = []
    real = rig.broker.open_orders

    def failing_open_orders():
        starts.append(rig.clock.now())
        rig.clock.advance(seconds=15)
        raise __import__("v6lite.broker.base", fromlist=["BrokerError"]).BrokerError("timeout")
    rig.broker.open_orders = failing_open_orders
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.px("SPY", 107.0)
    rig.tick()
    first_round = list(starts)
    for _ in range(40):
        rig.advance(seconds=1)
        rig.px("AAPL", 107.0)
        rig.px("SPY", 107.0)
        rig.tick()
    exit_attempts = [t for t in starts if t not in first_round]
    # reconcile may query too; exit re-tries for a refused (position, stage) wait >= 30 s
    backoff_until = first_round[-1] + timedelta(seconds=15) + timedelta(seconds=30)
    early = [t for t in exit_attempts if t < backoff_until - timedelta(seconds=15)]
    assert not early, (first_round, exit_attempts)


# ======================================================================= F9
def test_F9_combo_order_stock_legs_are_reserved(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker._contracts = {"AAPL": SimpleNamespace(conId=265598), "TSLA": SimpleNamespace(conId=76792991)}
    legs = [SimpleNamespace(conId=265598, ratio=1, action="SELL"), SimpleNamespace(conId=76792991, ratio=1, action="BUY")]
    trade = SimpleNamespace(
        order=SimpleNamespace(orderRef="", account="DU0000000", orderId=3, permId=9, action="BUY", totalQuantity=20),
        contract=SimpleNamespace(symbol="AAPL,TSLA", secType="BAG", comboLegs=legs),
        orderStatus=SimpleNamespace(status="Submitted", filled=0.0, remaining=20.0))
    broker.ib = SimpleNamespace(reqAllOpenOrders=lambda: [trade])
    orders = {o.symbol: o for o in broker.open_orders()}
    assert orders["AAPL"].side == "SELL" and orders["AAPL"].remaining == 20 and not orders["AAPL"].ours
    assert orders["TSLA"].side == "BUY"


def test_F9_unresolvable_combo_leg_fails_closed(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker._contracts = {"AAPL": SimpleNamespace(conId=265598)}
    trade = SimpleNamespace(
        order=SimpleNamespace(orderRef="", account="DU0000000", orderId=3, permId=9, action="SELL", totalQuantity=5),
        contract=SimpleNamespace(symbol="?", secType="BAG", comboLegs=None),
        orderStatus=SimpleNamespace(status="Submitted", filled=0.0, remaining=5.0))
    broker.ib = SimpleNamespace(reqAllOpenOrders=lambda: [trade])
    [order] = broker.open_orders()
    assert order.symbol == "*" and order.side == ""          # applies to every symbol, unknown side


# ======================================================================= freeze/release replace resync (retired scenarios)
def _freeze_by_difference(rig: Rig, symbol: str = "AAPL") -> None:
    for _ in range(4):
        rig.advance(seconds=31)
        rig.tick()
    assert any(k == f"anomaly:frozen:{symbol}" for k in rig.store.anomalies())


def test_missed_buy_freezes_then_flatten_and_release(rig):
    rig.open_aapl()
    rig.store.conn.execute("UPDATE positions SET qty_open=10, qty_initial=10")   # ledger lost 10
    _freeze_by_difference(rig)
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)     # operator sold all 20 in TWS
    rig.advance(seconds=61)
    _release(rig, "AAPL")
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == [] and rig.store.control("frozen:AAPL") is None
    assert not any(k.startswith("anomaly:frozen") for k in rig.store.anomalies())
    assert rig.engine.flags.reconcile_ok


def test_late_fill_found_before_the_freeze_deadline_prevents_the_freeze(rig):
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    rig.broker.confirm_cancel(stop.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=6)
    rig.tick()
    rig.broker.fill(stop.intent_id, 10, emit=False)          # visible to reqExecutions, no callback
    for _ in range(4):
        rig.advance(seconds=31)
        rig.px("AAPL", 107.0)
        rig.tick()
    assert not any(k.startswith("anomaly:frozen") for k in rig.store.anomalies())
    assert rig.store.open_positions()[0]["qty_open"] == 10
    assert _sells(rig)[-1].quantity == 10                    # STOP resumed for the real remainder


def test_prior_day_partial_entry_is_frozen_then_released(tmp_path):
    from v6lite.__main__ import main
    rig = Rig(tmp_path)
    rig.cfg_tmp = tmp_path
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = rig.broker.sent[0]
    rig.broker.fill(entry.intent_id, 10)
    rig.advance(seconds=1)
    rig.tick()
    rig.broker.orders.clear()                                # died; 5 more filled unseen, rest expired
    rig.broker.external_positions["AAPL"] = (15.0, 113.47)
    rig.clock.set(et(9, 50, day=(2026, 10, 7)))
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()
    assert rig.store.intent(entry.intent_id)["hold_reason"] == "prior_day_unverifiable"
    for _ in range(4):
        rig.advance(seconds=31)
        engine2.tick()
    assert rig.store.control("frozen:AAPL") is not None
    rig.broker.external_positions["AAPL"] = (0.0, 0.0)       # operator flattened all 15
    rig.advance(seconds=61)
    cfg_path = _write_cfg(tmp_path, "r.toml", rig.cfg.account, rig.cfg.resolved_state_dir())
    assert main(["release", "AAPL", "--config", str(cfg_path), "--operator", "J", "--reason", "flattened"]) == 0
    rig.advance(seconds=1)
    engine2.tick()
    # A prior-day held ENTRY remains unresolved even when the broker is flat.
    # Release must not silently turn an UNKNOWN order into CANCELLED.
    assert rig.store.open_positions()
    assert rig.store.control("frozen:AAPL") is not None
    assert rig.store.intent(entry.intent_id)["hold_reason"] == "prior_day_unverifiable"
    assert rig.store.intent(entry.intent_id)["status"] != IntentStatus.CANCELLED
    assert rig.store.conn.execute(
        "SELECT 1 FROM audit WHERE kind='RELEASE_REFUSED' ORDER BY ts DESC LIMIT 1"
    ).fetchone() is not None

    # The operator explicitly rules on the order first, then requests release
    # again. Neither command places or cancels a broker order.
    writes_before = (len(rig.broker.sent), len(rig.broker.cancels))
    assert main(["resolve-intent", entry.intent_id, "--config", str(cfg_path),
                 "--as", "cancelled", "--operator", "J",
                 "--reason", "verified flat in TWS"]) == 0
    assert rig.store.intent(entry.intent_id)["status"] == IntentStatus.CANCELLED
    assert main(["release", "AAPL", "--config", str(cfg_path), "--operator", "J", "--reason", "flattened"]) == 0
    rig.advance(seconds=1)
    engine2.tick()
    assert rig.store.open_positions() == []
    assert rig.store.control("frozen:AAPL") is None
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 1
    assert (len(rig.broker.sent), len(rig.broker.cancels)) == writes_before
    assert engine2.flags.reconcile_ok


def test_release_counts_as_a_loss_for_the_streak(rig):
    from v6lite.risk import loss_state
    from v6lite.tests.helpers import seed_closed_trade
    rig.open_aapl()
    seed_closed_trade(rig.store, "pos-prior", -3.0, et(9, 40))
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)
    rig.advance(seconds=61)
    _release(rig, "AAPL")
    rig.advance(seconds=1)
    rig.tick()
    assert loss_state(rig.store, rig.cfg).cooldown_until is not None


def test_price_only_correction_blocks_entries_while_exits_continue(rig):
    from v6lite.models import FillEvent
    rig.open_aapl()
    entry_exec = rig.store.conn.execute("SELECT exec_id, intent_id FROM fills").fetchone()
    stem = entry_exec[0].rsplit(".", 1)[0]
    rig.broker.events.append(FillEvent(stem + ".02", entry_exec[1], "AAPL", Side.BUY, 20, 120.0, rig.clock.now(),
                                       commission=1.0))
    rig.advance(seconds=1)
    rig.tick()
    assert any(k.startswith("anomaly:exec_correction") for k in rig.store.anomalies())
    assert rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert _sells(rig) and _sells(rig)[-1].purpose == "STOP"    # quantity is unchanged: protection continues

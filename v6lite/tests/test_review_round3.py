"""Regression tests for Codex's second Windows review (2026-09-30, Q1-Q8 and
D1-D10) and for the batch-3 redesign of the recovery layer. Batch 4 removed
`resync` and all state migrations (v1 runs on fresh state only); their tests
were retired here and replaced by freeze/release tests in test_review_round4.py.

* sell capacity comes from FRESH broker working orders (Q1, D3, D6)
* `resync` (broker truth + attribution by order) replaces `repair-position`
  and its time watermark (Q2, Q3, Q4, D4, D5)
* v1 has no `adopt` (Q5 removed by scope)
* loss streak ordered by execution time over the full history (Q6, D1)
* every slow call, including queued exit follow-ups, is budgeted (Q7, D8)
* a refused start never migrates foreign state and never cancels orders (Q8)
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from functools import partial

import pytest

from v6lite.broker.base import BrokerError
from v6lite.engine import Engine, StartupError
from v6lite.exits import compute_levels
from v6lite.models import (
    BrokerPosition, CommissionEvent, FillEvent, IntentStatus, OrderRequest, Purpose, Side,
)
from v6lite.store import Store
from v6lite.tests.helpers import Rig, buy_bars, et, make_cfg, seed_closed_trade
from v6lite.tests.test_review_round2 import DAY2, _held_tp1, _two_open_positions, _write_cfg

IB_EXEC = "0000e0d5.{:08x}.01.01"


def _broker_qty(rig: Rig, symbol: str) -> float:
    return next((p.quantity for p in rig.broker.positions() if p.symbol == symbol), 0.0)


def _sells(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.side is Side.SELL]


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    r.cfg_tmp = tmp_path
    return r


# ======================================================================= Q1
def test_Q1_orphan_working_sell_reserves_the_shares(rig, caplog):
    rig.open_aapl()                                          # ledger 20, broker 20
    lost = OrderRequest("v6l-lost-stop", "AAPL", Side.SELL, 20, 100.0, Purpose.STOP, None)
    rig.broker._register(lost)                               # working at the broker, unknown to the ledger
    rig.advance(seconds=1)
    rig.engine._reconcile(rig.clock.now())
    assert any(k.startswith("anomaly:orphan_order") for k in rig.store.anomalies())
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)                                    # below the 108.47 stop
    rig.tick()
    assert [s.intent_id for s in _sells(rig)] == ["v6l-lost-stop"]   # nothing new: all 20 are spoken for
    assert "止损下单被拦截" in rig.messages() and "v6l-lost-stop" in rig.messages()
    assert "exit blocked" in caplog.text
    rig.broker.fill("v6l-lost-stop")
    rig.advance(seconds=31)
    rig.tick()
    assert _broker_qty(rig, "AAPL") >= 0


def test_Q1_held_order_that_shows_up_working_again_is_reserved(tmp_path, caplog):
    rig = Rig(tmp_path)
    engine, tp1 = _held_tp1(rig)
    rig.broker.orders[tp1.intent_id].status = "Submitted"   # fresh evidence: still working, 6 left
    rig.advance(seconds=1)
    engine._reconcile(rig.clock.now())
    rig.advance(seconds=31)
    rig.px("AAPL", 100.0)
    engine.tick()
    stop = [s for s in _sells(rig) if s.purpose == "STOP"]
    assert stop and stop[-1].quantity == 14                  # 20 held - 6 still working
    rig.broker.fill(tp1.intent_id)
    rig.broker.fill(stop[-1].intent_id)
    assert _broker_qty(rig, "AAPL") == 0                     # never -6
    assert "保留订单仍在途" in rig.messages() and "止损数量缩减" in rig.messages()
    assert "held order is working" in caplog.text


def test_Q1_sell_capacity_needs_fresh_broker_evidence(rig, caplog):
    rig.open_aapl()
    rig.broker.fail_open_orders = True
    rig.advance(minutes=5)                                   # last snapshot is older than the limit
    rig.px("AAPL", 107.0)
    rig.tick()
    assert _sells(rig) == []
    assert "止损下单被拦截" in rig.messages()
    assert "exit blocked" in caplog.text
    rig.broker.fail_open_orders = False
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert _sells(rig) and _sells(rig)[-1].quantity == 20


# ======================================================================= Q5 (adopt removed in v1)
def test_Q5_adopt_is_not_available_in_v1(rig, tmp_path):
    from v6lite.__main__ import main
    cfg_path = _write_cfg(tmp_path, "a.toml", rig.cfg.account, rig.cfg.resolved_state_dir())
    with pytest.raises(SystemExit):
        main(["adopt", "AAPL", "--config", str(cfg_path), "--operator", "J"])
    rig.broker.external_positions["TSLA"] = (40.0, 100.0)
    rig.start()
    rig.store.conn.execute("INSERT INTO controls(key, value, updated_at) VALUES "
                           "('disposition:TSLA', 'adopt', '2026-10-06T13:50:00+00:00')")   # legacy state
    rig.engine._reconcile(rig.clock.now())
    assert rig.store.open_positions() == [] and not rig.engine.flags.reconcile_ok


# ======================================================================= Q6
def _closed_trade(store: Store, cfg, symbol: str, entry: float, exit_: float, *, t_exit, ingest_at) -> None:
    pid = f"pos-{symbol}"
    t_entry = t_exit - timedelta(minutes=5)
    for side, purpose, price, t in ((Side.BUY, Purpose.ENTRY, entry, t_entry), (Side.SELL, Purpose.STOP, exit_, t_exit)):
        iid = f"v6l-{symbol}-{purpose}"
        store.create_intent(t, OrderRequest(iid, symbol, side, 10, price, purpose, pid), atr=1.0, count_entry_day=None)
        now = t if purpose is Purpose.ENTRY else ingest_at
        store.record_fill(now, FillEvent(IB_EXEC.format(hash(iid) & 0xFFFFFFF), iid, symbol, side, 10, price, t,
                                         commission=0.0))


def test_Q6_streak_follows_execution_time_not_arrival_time(rig):
    from v6lite.risk import loss_state
    rig.start()
    s = rig.store
    _closed_trade(s, rig.cfg, "AAPL", 100, 99, t_exit=et(10, 0), ingest_at=et(10, 0))    # loss
    _closed_trade(s, rig.cfg, "TSLA", 100, 101, t_exit=et(10, 20), ingest_at=et(10, 20))  # win
    _closed_trade(s, rig.cfg, "SPY", 100, 99, t_exit=et(10, 10), ingest_at=et(10, 30))    # loss, reported late
    assert loss_state(s, rig.cfg).cooldown_until == et(11, 10)


def test_Q6_streak_uses_the_full_history(rig):
    from v6lite.risk import loss_state
    rig.start()
    for i in range(1, 50):
        seed_closed_trade(rig.store, f"pos-{i:02d}", -1.0, et(9, 30) - timedelta(minutes=49 - i))
    seed_closed_trade(rig.store, "pos-50", 0.0, et(10, 0))
    seed_closed_trade(rig.store, "pos-51", -1.0, et(10, 40))
    assert loss_state(rig.store, rig.cfg).cooldown_until == et(11, 40)


# ======================================================================= Q7
def test_Q7_queued_exit_work_is_inside_the_protection_budget(tmp_path):
    rig = Rig(tmp_path, scan_times_et=("10:00", "11:00"))
    _two_open_positions(rig)
    rig.at(et(10, 59, 20))
    rig.px("AAPL", 120.0)
    rig.px("SPY", 120.0)
    rig.tick()                                               # TP1 working on both
    rig.advance(seconds=30)
    rig.px("AAPL", 120.0)
    rig.px("SPY", 120.0)
    rig.tick()
    tps = [s for s in _sells(rig) if s.purpose == "TP1"]
    assert len(tps) == 2
    checks = []
    evaluate = rig.engine._evaluate_position_exit

    def record(now, row, broker_qty):
        if row["symbol"] == "SPY":
            checks.append(rig.clock.now())
        return evaluate(now, row, broker_qty)
    rig.engine._evaluate_position_exit = record
    bars, vix, cancel = rig.broker.daily_bars, rig.broker.vix_close, rig.broker.cancel
    rig.broker.daily_bars = lambda symbol, days: (rig.clock.advance(seconds=10), bars(symbol, days))[1]
    rig.broker.vix_close = lambda: (rig.clock.advance(seconds=25), vix())[1]
    rig.broker.cancel = lambda iid: (rig.clock.advance(seconds=15), cancel(iid))[1]
    rig.at(et(11, 0, 5))
    rig.px("AAPL", 107.0)
    rig.px("SPY", 107.0)                                     # both fall through their stops
    checks.append(rig.clock.now())
    rig.tick()                                               # scan + STOP decisions + two slow cancels
    for _ in range(60):
        rig.advance(seconds=1)
        rig.tick()
    gaps = [(b - a).total_seconds() for a, b in zip(checks, checks[1:])]
    assert gaps and max(gaps) <= rig.cfg.exit_check_interval_seconds + 1, gaps


# ======================================================================= Q8
def test_Q8_run_refuses_a_foreign_state_file_before_touching_it(tmp_path, monkeypatch):
    import sqlite3
    from v6lite.__main__ import main
    state = tmp_path / "state"
    other = _write_cfg(tmp_path, "other.toml", "DU0000000", state)
    mine = _write_cfg(tmp_path, "mine.toml", "DU0000000", state)
    from v6lite.config import load_config
    store = Store(state / "v6lite.sqlite3", partial(compute_levels, load_config(other)))
    store.bind_account("DU0000000", "paper")
    store.close()
    conn = sqlite3.connect(state / "v6lite.sqlite3")
    conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    before = (state / "v6lite.sqlite3").read_bytes()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "locks"))
    import v6lite.broker.ibkr as ibkr
    monkeypatch.setattr(ibkr, "IbkrBroker", lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")))
    assert main(["run", "--config", str(mine)]) == 2
    assert (state / "v6lite.sqlite3").read_bytes() == before


def test_Q8_failed_startup_never_cancels_working_orders(rig):
    from v6lite.__main__ import run_engine
    rig.store.bind_account("DU0000000", "paper")             # state bound to another account
    protection = OrderRequest("v6l-foreign-protection", "AAPL", Side.SELL, 5, 100.0, Purpose.STOP, None)
    rig.broker._register(protection)
    with pytest.raises(StartupError):
        run_engine(rig.engine, rig.broker, should_stop=lambda: True)
    assert rig.broker.cancels == []
    assert not rig.broker.connected


# ======================================================================= D7
def test_D7_account_ready_key_is_matched_case_insensitively(tmp_path):
    from types import SimpleNamespace
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker._on_account_value(SimpleNamespace(account="DU0000000", tag="AvailableFunds", value="2500", currency="USD"))
    broker._on_account_value(SimpleNamespace(account="DU0000000", tag="accountReady", value="false", currency=""))
    assert not broker.available_funds().ready


# ======================================================================= pre-handoff self-review of batch 3
class _Stuck(BaseException):
    pass


def test_SR3_blocked_exit_cannot_livelock_the_tick(tmp_path):
    rig = Rig(tmp_path)
    _two_open_positions(rig)
    # a manual TWS SELL covering all AAPL shares: AAPL's STOP is refused on every pass
    rig.broker._register(OrderRequest("tws-manual-1", "AAPL", Side.SELL, 20, 150.0, Purpose.STOP, None))
    real = rig.broker.open_orders
    calls = {"n": 0}

    def slow_open_orders():
        calls["n"] += 1
        if calls["n"] > 40:
            raise _Stuck("tick never returns")
        rig.advance(seconds=15)
        return real()
    rig.broker.open_orders = slow_open_orders
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.px("SPY", 107.0)
    rig.tick()                                               # must return
    assert calls["n"] <= 8
    spy_stop = [s for s in _sells(rig) if s.symbol == "SPY" and s.purpose == "STOP"]
    assert spy_stop
    for _ in range(10):                                      # later ticks re-price the unfilled STOP
        calls["n"] = 0
        rig.advance(seconds=5)
        rig.px("AAPL", 107.0)
        rig.px("SPY", 107.0)
        rig.tick()
    assert spy_stop[0].intent_id in rig.broker.cancels       # 45 s fill timeout still handled


def test_SR3_closed_at_is_the_latest_execution_of_the_position(rig):
    from v6lite.risk import loss_state
    rig.start()
    s = rig.store
    pid = "pos-A"
    s.create_intent(et(9, 55), OrderRequest("v6l-A-e", "AAPL", Side.BUY, 20, 100.0, Purpose.ENTRY, pid),
                    atr=1.0, count_entry_day=None)
    s.create_intent(et(9, 55), OrderRequest("v6l-A-x", "AAPL", Side.SELL, 20, 99.0, Purpose.STOP, pid),
                    atr=None, count_entry_day=None)
    for n, (iid, side, qty, t) in enumerate((("v6l-A-e", Side.BUY, 20, et(9, 55)), ("v6l-A-x", Side.SELL, 10, et(10, 5)),
                                             ("v6l-A-x", Side.SELL, 10, et(10, 0)))):   # last booked, executed earlier
        s.record_fill(t, FillEvent(IB_EXEC.format(0xA0 + n), iid, "AAPL", side, qty, 100.0 if side is Side.BUY else 99.0,
                                   t, commission=0.0))
    seed_closed_trade(s, "pos-C", +5.0, et(10, 2))
    seed_closed_trade(s, "pos-B", -5.0, et(10, 6))
    assert s.position(pid)["closed_at"] == et(10, 5).isoformat()
    assert loss_state(s, rig.cfg).cooldown_until == et(11, 6)


def test_SR3_queued_take_profit_is_not_sent_after_the_stop_took_over(tmp_path):
    rig = Rig(tmp_path)
    _two_open_positions(rig)
    real = rig.broker.open_orders
    rig.broker.open_orders = lambda: (rig.clock.advance(seconds=15), real())[1]
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)
    rig.px("SPY", 120.0)
    original = rig.engine._submit_exit

    def submit(pid, stage):
        if rig.store.position(pid)["symbol"] == "SPY":
            rig.px("SPY", 107.0)                             # SPY falls through its stop meanwhile
        return original(pid, stage)
    rig.engine._submit_exit = submit
    rig.tick()
    assert not [s for s in _sells(rig) if s.symbol == "SPY" and s.purpose == "TP1"]


def test_SR3_stop_starts_in_the_same_pass_that_abandons_a_take_profit(rig):
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)
    rig.broker.fail_next_submit = BrokerError("rejected: price", maybe_sent=False)
    rig.tick()                                               # TP1 stage PENDING, order rejected
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)                                    # straight through the stop
    rig.tick()
    assert rig.broker.sent[-1].purpose == "STOP"


def test_SR3_option_orders_do_not_reserve_shares_and_zero_remaining_falls_back(tmp_path):
    from types import SimpleNamespace
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))

    def trade(sec, ref, total, remaining):
        return SimpleNamespace(order=SimpleNamespace(orderRef=ref, account="DU0000000", orderId=1, permId=7,
                                                     action="SELL", totalQuantity=total),
                               contract=SimpleNamespace(symbol="AAPL", secType=sec),
                               orderStatus=SimpleNamespace(status="Submitted", filled=0.0, remaining=remaining))
    broker.ib = SimpleNamespace(reqAllOpenOrders=lambda: [trade("OPT", "", 1, 1), trade("STK", "", 20, 0.0)])
    [order] = broker.open_orders()
    assert order.symbol == "AAPL" and order.remaining == 20 and order.side == "SELL" and not order.ours



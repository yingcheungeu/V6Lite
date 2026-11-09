"""Regression tests for Codex's fifth review (R5-F1 .. R5-F5).

R5-F1  a fresh broker/ledger difference found at the SELL boundary is
       recorded (entries blocked) before any eligibility check can return
R5-F2  an order created before the symbol was verified flat never books a
       late fill -- also when it never had a position row
R5-F3  schema-4 state written by batch 4 (release recorded, no flat marker)
       still yields the verified-flat point
R5-F4  such evidence-only fills never enter lots or day P&L
R5-F5  combo (BAG) orders: known stock legs reserve shares on their own
       symbol, any unknown leg blocks everything (the documented policy)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from v6lite.models import FillEvent, IntentStatus, OrderRequest, Purpose, Side
from v6lite.risk import day_pnl
from v6lite.tests.helpers import Rig, buy_bars, et, make_cfg


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def _entries(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


def _release(rig: Rig, symbol: str = "AAPL") -> None:
    rig.advance(seconds=61)
    rig.store.set_control(rig.clock.now(), f"release:{symbol}", '{"operator": "J", "reason": "flat in TWS"}')
    rig.advance(seconds=1)
    rig.tick()


def _booked(rig: Rig, exec_id: str) -> bool:
    return rig.store.conn.execute("SELECT 1 FROM fills WHERE exec_id=?", (exec_id,)).fetchone() is not None


# ======================================================================= R5-F1
def test_R5F1_fresh_difference_at_the_sell_boundary_blocks_the_same_tick_buy(tmp_path):
    rig = Rig(tmp_path, scan_times_et=("10:00", "10:02"))
    rig.open_aapl()
    rig.broker.bars["SPY"] = buy_bars()
    real = rig.broker.open_orders
    state = {"armed": False}

    def open_orders():
        if state["armed"]:
            state["armed"] = False
            rig.broker.external_positions["AAPL"] = (-2.0, 0.0)   # a foreign SELL 2 while we query
        return real()
    rig.broker.open_orders = open_orders
    rig.at(et(10, 2, 5))
    rig.px("SPY", 113.13)
    rig.px("AAPL", 120.0)                                         # TP1 zone -> queued
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False
    state["armed"] = True
    rig.tick()
    assert not [s for s in rig.broker.sent if s.side is Side.SELL]
    assert not [e for e in _entries(rig) if e.symbol == "SPY"], rig.engine.flags
    assert "AAPL" in rig.engine.flags.quantity_suspect


# ======================================================================= R5-F2
def test_R5F2_old_order_without_a_position_row_never_books_after_flat(rig):
    rig.open_aapl()
    old = OrderRequest("v6l-oldentry0001", "AAPL", Side.BUY, 10, 112.0, Purpose.ENTRY, "pos-never-booked")
    rig.store.create_intent(et(9, 40), old, atr=2.0, count_entry_day=None)
    rig.store.update_intent(et(9, 41), old.intent_id, status=IntentStatus.CANCELLED)
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)        # operator flattened
    _release(rig)
    assert rig.store.open_positions() == []
    late = FillEvent("0000fa4e.0000c0de.01.01", old.intent_id, "AAPL", Side.BUY, 5, 112.0, et(9, 40, 30))
    rig.engine._ingest_fill(rig.clock.now(), late)
    assert rig.store.open_positions() == []
    assert rig.store.position("pos-never-booked") is None
    assert not _booked(rig, late.exec_id)
    assert any(k.startswith("anomaly:late_fill_before_flat:") for k in rig.store.anomalies())


# ======================================================================= R5-F3
def test_R5F3_batch4_state_release_record_alone_is_a_flat_point(rig):
    entry_id = rig.open_aapl()
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)
    _release(rig)
    # batch 4 wrote the release record but no flat marker
    rig.store.delete_control("flat_verified:AAPL")
    late = FillEvent("0000fa4e.0000c0df.01.01", entry_id, "AAPL", Side.BUY, 3, 113.0, et(10, 0, 31))
    rig.engine._ingest_fill(rig.clock.now(), late)
    assert rig.store.open_positions() == [] and not _booked(rig, late.exec_id)


# ======================================================================= R5-F4
def test_R5F4_evidence_only_fill_never_enters_day_pnl(rig):
    entry_id = rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    rig.broker.fill(stop.intent_id, 20, price=107.0)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    day = et(10).date().isoformat()
    marks = {"AAPL": 110.0}
    before = day_pnl(rig.store, day, marks=marks, prior_close={"AAPL": 100.0}).pnl
    rig.store.set_control(rig.clock.now(), "flat_verified:AAPL", rig.clock.now().isoformat())   # e.g. a resolved freeze
    late = FillEvent("0000fa4e.0000c0e0.01.01", entry_id, "AAPL", Side.BUY, 5, 90.0, et(10, 0, 31))
    rig.engine._ingest_fill(rig.clock.now(), late)
    after = day_pnl(rig.store, day, marks=marks, prior_close={"AAPL": 100.0}).pnl
    assert before is not None and after == pytest.approx(before)
    assert rig.store.open_positions() == []


# ======================================================================= R5-F5
def _bag(legs, action="BUY"):
    return SimpleNamespace(
        order=SimpleNamespace(orderRef="", account="DU0000000", orderId=3, permId=9, action=action, totalQuantity=5),
        contract=SimpleNamespace(symbol="?", secType="BAG", comboLegs=legs),
        orderStatus=SimpleNamespace(status="Submitted", filled=0.0, remaining=5.0))


def test_R5F5_known_combo_legs_reserve_their_own_symbols_only(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker._contracts = {"AAPL": SimpleNamespace(conId=1), "TSLA": SimpleNamespace(conId=2),
                         "SPY": SimpleNamespace(conId=3)}
    known = _bag([SimpleNamespace(conId=2, ratio=1, action="SELL"), SimpleNamespace(conId=3, ratio=1, action="BUY")])
    broker.ib = SimpleNamespace(reqAllOpenOrders=lambda: [known])
    orders = broker.open_orders()
    assert {o.symbol for o in orders} == {"TSLA", "SPY"}            # no '*': AAPL is not claimed
    unknown = _bag([SimpleNamespace(conId=2, ratio=1, action="SELL"), SimpleNamespace(conId=99, ratio=1, action="BUY")])
    broker.ib = SimpleNamespace(reqAllOpenOrders=lambda: [unknown])
    assert "*" in {o.symbol for o in broker.open_orders()}


# ======================================================================= batch-6 self-review
def test_SR6_A1_fresh_difference_survives_an_evidence_refusal(tmp_path):
    """The R5-F1 scenario plus an unresolved combo: the refusal for missing
    evidence must not drop the fresh difference it already read."""
    rig = Rig(tmp_path, scan_times_et=("10:00", "10:02"))
    rig.open_aapl()
    rig.broker.bars["SPY"] = buy_bars()
    real = rig.broker.open_orders
    state = {"armed": False}

    def open_orders():
        if state["armed"]:
            state["armed"] = False
            rig.broker.external_positions["AAPL"] = (-2.0, 0.0)
            rig.broker._register(OrderRequest("combo-x", "*", Side.SELL, 5, 1.0, Purpose.TP1, None))
        return real()
    rig.broker.open_orders = open_orders
    rig.at(et(10, 2, 5))
    rig.px("SPY", 113.13)
    rig.px("AAPL", 120.0)
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False
    state["armed"] = True
    rig.tick()
    assert not [e for e in _entries(rig) if e.symbol == "SPY"]
    assert "AAPL" in rig.engine.flags.quantity_suspect


def test_SR6_A3_no_entry_while_an_unresolved_combo_blocks_every_sell(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.start()
    rig.broker._register(OrderRequest("combo-y", "*", Side.SELL, 5, 1.0, Purpose.TP1, None))
    rig.at(et(10, 0, 30))
    rig.engine._reconcile_due = True
    rig.tick()
    assert not _entries(rig)
    assert "组合挂单阻断退出" in rig.messages() and "combo-y" in rig.messages()
    assert "unresolved combo order working" in caplog.text


def test_SR6_A2_stale_position_stream_never_silences_a_stop(rig, caplog):
    """Stream stuck at 0 while the fresh query (== ledger) says 20 and the
    price is through the stop: the STOP reaches the send boundary, where the
    smaller of stream and fresh query still wins (no oversell) -- but the
    operator is paged P0 instead of the stop being withheld silently."""
    rig.open_aapl()
    rig.broker.stale_positions = {"AAPL": 0.0}
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert not [s for s in rig.broker.sent if s.side is Side.SELL]
    pages = [(s, b) for s, b in rig.channel.messages if "P0" in s and "AAPL" in b]
    assert pages, rig.channel.messages
    assert "exit refused AAPL STOP" in caplog.text
    rig.broker.stale_positions = None                           # the stream recovers
    for _ in range(2):
        rig.advance(seconds=31)
        rig.px("AAPL", 107.0)
        rig.tick()
    stops = [s for s in rig.broker.sent if s.purpose == Purpose.STOP]
    assert stops and stops[0].quantity == 20


def test_SR6_stop_sells_everything_still_open_even_if_its_target_is_stale(rig):
    rig.open_aapl()
    pid = rig.store.open_positions()[0]["position_id"]
    rig.store.begin_stage(rig.clock.now(), pid, Purpose.STOP, 12)   # target recorded from an older ledger
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    [stop] = [s for s in rig.broker.sent if s.purpose == Purpose.STOP]
    assert stop.quantity == 20


def test_SR6_batch5_evidence_rows_make_the_state_unusable(rig):
    from v6lite.engine import StartupError
    entry_id = rig.open_aapl()
    rig.store.conn.execute(
        "INSERT INTO fills(exec_id, intent_id, position_id, symbol, side, quantity, price, time, trading_day, "
        "exec_base) VALUES ('0000fa4e.0000dead.01.01', ?, NULL, 'AAPL', 'BUY', 5, 90.0, ?, ?, '0000fa4e.0000dead')",
        (entry_id, rig.clock.now().isoformat(), et(10).date().isoformat()))
    rig.store.raise_anomaly(rig.clock.now(), "late_fill_before_flat:0000fa4e.0000dead.01.01", "batch-5 row")
    with pytest.raises(StartupError, match="earlier version booked"):
        rig.engine.startup()

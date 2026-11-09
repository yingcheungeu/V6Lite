"""Regression tests for Codex's fourth review (R4-F1 .. R4-F8).

R4-F1  fixed-key ib_insync channels: a late End of a timed-out request must
       never complete a newer request (no false empty list, no false release)
R4-F2  a quantity difference noted after the reconcile's position read is
       not cleared by that older reconcile (no BUY in the same tick)
R4-F3  a late BUY reported after a release never re-creates a position
R4-F4  a frozen symbol whose position was closed by its own fills can be
       released (freeze ends, no second loss)
R4-F5  the freeze P0 lists every order that can still trade the symbol
R4-F6  a combo leg with an unknown conId fails closed
R4-F7  a frozen symbol with a held intent does not force a reconcile per tick
R4-F8  a refused SELL is judged from the latest quote (TP -> STOP -> P0)
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.models import OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, buy_bars, et, make_cfg


def _sells(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.side is Side.SELL]


def _entries(rig: Rig) -> list[OrderRequest]:
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def _release_request(rig: Rig, symbol: str = "AAPL") -> None:
    rig.store.set_control(rig.clock.now(), f"release:{symbol}", '{"operator": "J", "reason": "flat in TWS"}')


# ======================================================================= R4-F1
class _Server:
    """Plays the Gateway for the REAL ib_insync IB/Wrapper/Future code: no
    socket. Each request's messages are delivered in order (FIFO) on the
    event loop; `end_delay` holds back one request's End past the timeout."""

    def __init__(self, ib):
        from ib_insync import Stock, util
        self.ib, self.loop = ib, util.getLoop()
        self.stock = Stock("AAPL", "SMART", "USD", conId=265598)
        self.calls = 0
        self.free_at = 0.0
        self.plan: list[dict] = []

    def _at(self, delay: float) -> float:
        """FIFO: nothing of a later request overtakes an earlier response."""
        when = max(self.loop.time() + delay, self.free_at + 0.001)
        self.free_at = when
        return when

    def req_positions(self):
        plan = self.plan[self.calls]
        self.calls += 1
        w = self.ib.wrapper
        for qty in plan.get("positions", []):
            self.loop.call_at(self._at(0.005), w.position, "DU0000000", self.stock, qty, 100.0)
        if plan.get("end_delay") is not None:
            self.loop.call_at(self._at(plan["end_delay"]), w.positionEnd)
        else:
            self.free_at = float("inf")                         # this End never comes

    def req_all_open_orders(self):
        plan = self.plan[self.calls]
        self.calls += 1
        if plan.get("end_delay") is not None:
            self.loop.call_at(self._at(plan["end_delay"]), self.ib.wrapper.openOrderEnd)
        else:
            self.free_at = float("inf")


@pytest.fixture
def real_ib(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker.ib.RequestTimeout = 0.05
    server = _Server(broker.ib)
    broker.ib.client.reqPositions = server.req_positions          # wrapped by the adapter's hooks
    broker.ib.client.reqAllOpenOrders = server.req_all_open_orders
    # Simulate a connected, API-ready socket; all requests use the local server.
    broker.ib.client.isConnected = lambda: True
    broker.ib.client._apiReady = True
    def no_wire(message):
        raise AssertionError("offline fixed-channel fixture attempted a network send")
    broker.ib.client.conn.sendMsg = no_wire
    assert broker.ib.isConnected()
    return broker, server


def test_R4F1_late_end_of_a_timed_out_request_never_completes_the_next_one(real_ib):
    broker, server = real_ib
    # 1st: data arrives but End is withheld until the timeout has *actually*
    # fired. Scheduling End at 80 ms versus a 50 ms timeout races the Windows
    # event loop; a stalled thread may process End before wait_for's timeout.
    # The 2nd request must drain the owed End before asking the server again.
    server.plan = [{"positions": [20], "end_delay": None}, {"positions": [20], "end_delay": 0.01}]
    with pytest.raises(Exception):
        broker.positions_fresh()                                   # times out, its End is owed
    assert broker._req_sent["positions"] == 1 and broker._req_ended["positions"] == 0
    server.free_at = server.loop.time()
    server.loop.call_soon(broker.ib.wrapper.positionEnd)          # late End, now deterministic
    result = broker.positions_fresh()                              # waits for the old End, then asks
    assert [(p.symbol, p.quantity) for p in result] == [("AAPL", 20.0)]
    assert server.calls == 2
    assert broker._req_sent["positions"] == broker._req_ended["positions"] == 2


def test_R4F1_owed_end_that_never_comes_is_evidence_unavailable_not_empty(real_ib, monkeypatch):
    import v6lite.broker.ibkr as ibkr
    monkeypatch.setattr(ibkr, "CHANNEL_DRAIN_SECONDS", 0.1)
    broker, server = real_ib
    server.plan = [{"positions": [20], "end_delay": None}, {"positions": [], "end_delay": 0.01}]
    with pytest.raises(Exception):
        broker.positions_fresh()
    with pytest.raises(BrokerError, match="not ended"):
        broker.positions_fresh()                                   # never a [] that reads as "flat"
    assert server.calls == 1                                       # nothing sent on the dirty channel


def test_R4F1_open_orders_channel_is_guarded_too(real_ib, monkeypatch):
    import v6lite.broker.ibkr as ibkr
    monkeypatch.setattr(ibkr, "CHANNEL_DRAIN_SECONDS", 0.1)
    broker, server = real_ib
    server.plan = [{"end_delay": None}, {"end_delay": 0.01}]
    with pytest.raises(Exception):
        broker.open_orders()
    with pytest.raises(BrokerError, match="not ended"):
        broker.open_orders()


def test_R4F1_a_channel_stuck_too_long_renews_the_connection(real_ib, monkeypatch):
    import v6lite.broker.ibkr as ibkr
    monkeypatch.setattr(ibkr, "CHANNEL_RENEW_AFTER_SECONDS", 0)
    broker, server = real_ib
    dropped = []
    broker.disconnect = lambda: dropped.append(True)
    server.plan = [{"positions": [20], "end_delay": None}]
    with pytest.raises(Exception):
        broker.positions_fresh()
    with pytest.raises(BrokerError, match="renewed"):
        broker.positions_fresh()
    assert dropped
    broker._reset_channels()                                       # what connect() does on the new socket
    server.free_at = 0.0                                           # a new socket: nothing queued
    server.plan.append({"positions": [20], "end_delay": 0.01})
    assert [p.quantity for p in broker.positions_fresh()] == [20.0]


def test_R4F1_streamed_update_inside_a_request_does_not_double_count(real_ib):
    broker, server = real_ib
    server.plan = [{"positions": [20, 18], "end_delay": 0.03}]   # download 20, then an update to 18
    assert [(p.symbol, p.quantity) for p in broker.positions_fresh()] == [("AAPL", 18.0)]


def test_R4F1_release_needs_the_position_stream_to_agree(rig):
    rig.open_aapl()
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)        # fresh query: flat
    rig.broker.stale_positions = {"AAPL": 20.0}                  # stream still says 20
    rig.advance(seconds=61)
    _release_request(rig)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions()
    [remote] = [body for subject, body in rig.channel.messages
                if "P1" in subject and "人工释放被拒" in subject and "AAPL" in body]
    assert len(remote.splitlines()) == 4
    assert "你要做什么：" in remote and "AAPL" in remote and "TWS" in remote
    assert "20" in remote and "0 股" in remote and "release AAPL" in remote
    assert "1）" in remote and "2）" in remote and "3）" in remote
    refused = rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='RELEASE_REFUSED' ORDER BY id DESC LIMIT 1").fetchone()
    assert refused is not None and "position stream still shows 20" in refused["payload"]


# ======================================================================= R4-F2
def test_R4F2_difference_seen_during_the_reconcile_is_not_cleared_by_it(tmp_path):
    rig = Rig(tmp_path, scan_times_et=("10:00", "10:02"))
    rig.open_aapl()
    assert rig.engine.flags.reconcile_ok
    rig.broker.bars["SPY"] = buy_bars()
    rig.at(et(10, 2, 5))
    rig.px("SPY", 113.13)
    rig.engine._last_exit_check = rig.clock.now() - timedelta(seconds=20)   # due during the next query
    rig.engine._reconcile_due = True
    real_read, real_reconcile = rig.engine._fresh_positions, rig.engine._reconcile
    state = {"armed": False}

    def fresh_positions():
        out = real_read()
        if state["armed"]:                                       # the reconcile's own read: 20 ...
            state["armed"] = False
            rig.advance(seconds=15)                              # ... the read took 15 s ...
            rig.broker.external_positions["AAPL"] = (-2.0, 0.0)   # ... and a foreign sale made it 18
        return out

    def reconcile(now):
        state["armed"] = True
        return real_reconcile(now)
    rig.engine._fresh_positions = fresh_positions
    rig.engine._reconcile = reconcile
    rig.tick()
    assert not [e for e in _entries(rig) if e.symbol == "SPY"], rig.engine.flags
    assert "AAPL" in rig.engine.flags.quantity_suspect           # the protection pass saw 18
    assert not rig.engine.flags.reconcile_ok


# ======================================================================= R4-F3
def test_R4F3_late_buy_after_release_never_recreates_a_position(rig):
    entry_id = rig.open_aapl()
    rig.broker.external_positions["AAPL"] = (-20.0, 0.0)        # operator flattened in TWS
    rig.advance(seconds=61)
    _release_request(rig)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    sent_before = len(rig.broker.sent)
    late = rig.broker.fill(entry_id, 5, emit=False) if rig.broker.orders[entry_id].remaining else None
    if late is None:                                             # entry fully filled: an older batch report
        from v6lite.models import FillEvent
        late = FillEvent("0000fa4e.0000ffff.01.01", entry_id, "AAPL", Side.BUY, 5, 113.13, rig.clock.now())
    rig.engine._ingest_fill(rig.clock.now(), late)
    assert rig.store.open_positions() == []
    assert any(k.startswith(("anomaly:late_fill_after_release:", "anomaly:late_fill_before_flat:"))
               for k in rig.store.anomalies())
    assert len(rig.broker.sent) == sent_before


# ======================================================================= R4-F4
def test_R4F4_frozen_position_closed_by_its_own_fills_can_be_released(rig):
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()                                                   # STOP 20 sent
    stop = rig.broker.sent[-1]
    assert stop.purpose == Purpose.STOP
    late = rig.broker.fill(stop.intent_id, 20, emit=False)      # sold, report not visible
    rig.broker.invisible_execs.add(late.exec_id)
    for _ in range(10):
        rig.advance(seconds=31)
        rig.tick()
        if rig.store.control("frozen:AAPL") is not None:
            break
    assert rig.store.control("frozen:AAPL") is not None
    rig.broker.invisible_execs.discard(late.exec_id)            # the execution shows up
    rig.engine._refresh_fills(rig.clock.now(), force=True)
    [pos] = rig.store.conn.execute("SELECT * FROM positions").fetchall()
    assert pos["status"] == "CLOSED"
    rig.advance(seconds=61)
    _release_request(rig)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.control("frozen:AAPL") is None
    assert not any(k.startswith("anomaly:frozen:") for k in rig.store.anomalies())
    assert rig.store.release_times() == []                      # the trade itself is the loss event
    assert rig.store.risk_day(et(10).date().isoformat())["daily_stop_latched"] == 1
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"


# ======================================================================= R4-F5
def test_R4F5_freeze_lists_held_and_orphan_orders_that_were_not_cancelled(rig, caplog):
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)
    rig.tick()
    [tp1] = [s for s in _sells(rig) if s.purpose == Purpose.TP1]
    rig.store.hold_intent(rig.clock.now(), tp1.intent_id, "test_hold")
    rig.broker._register(OrderRequest("v6l-orphan01", "AAPL", Side.SELL, 4, 125.0, Purpose.TP2, None))
    rig.broker.external_positions["AAPL"] = (-3.0, 0.0)         # unexplained: broker 17, ledger 20
    for _ in range(4):
        rig.advance(seconds=31)
        rig.engine._reconcile_due = True
        rig.tick()
    assert rig.store.control("frozen:AAPL") is not None
    assert tp1.intent_id not in rig.broker.cancels and "v6l-orphan01" not in rig.broker.cancels
    [p0] = [body for subject, body in rig.channel.messages
            if "P0" in subject and "V6-Lite 停止管理持仓" in subject]
    assert len(p0.splitlines()) == 4
    assert "TWS" in p0 and "保护" in p0
    assert tp1.intent_id in p0 and "v6l-orphan01" in p0
    assert p0.index(tp1.intent_id) < p0.index("保护或平仓")
    assert p0.index("v6l-orphan01") < p0.index("保护或平仓")
    assert p0.index("保护或平仓") < p0.index("release AAPL")
    details = [rec.getMessage() for rec in caplog.records
               if "P0: V6-Lite no longer manages AAPL" in rec.getMessage()]
    assert details and tp1.intent_id in details[-1] and "v6l-orphan01" in details[-1]
    assert "NOT cancelled" in details[-1] and "THEN protect or flatten" in details[-1]


# ======================================================================= R4-F6
def test_R4F6_combo_leg_with_an_unknown_conid_fails_closed(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker._contracts = {"AAPL": SimpleNamespace(conId=265598)}
    legs = [SimpleNamespace(conId=999999, ratio=1, action="SELL")]
    trade = SimpleNamespace(
        order=SimpleNamespace(orderRef="", account="DU0000000", orderId=3, permId=9, action="BUY", totalQuantity=5),
        contract=SimpleNamespace(symbol="?", secType="BAG", comboLegs=legs),
        orderStatus=SimpleNamespace(status="Submitted", filled=0.0, remaining=5.0))
    broker.ib = SimpleNamespace(reqAllOpenOrders=lambda: [trade])
    [order] = broker.open_orders()
    assert order.symbol == "*"


# ======================================================================= R4-F7
def test_R4F7_frozen_symbol_with_a_held_intent_is_not_reconciled_every_tick(rig):
    rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)
    rig.tick()
    [tp1] = [s for s in _sells(rig) if s.purpose == Purpose.TP1]
    rig.store.hold_intent(rig.clock.now(), tp1.intent_id, "prior_day_unverifiable")
    rig.broker.external_positions["AAPL"] = (-3.0, 0.0)
    for _ in range(4):
        rig.advance(seconds=31)
        rig.engine._reconcile_due = True
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
    rig.engine._evaluate_exits = lambda *a, **k: None
    for _ in range(60):
        rig.advance(seconds=1)
        rig.tick()
    assert calls["orders"] <= 2 and calls["positions"] <= 2, calls


# ======================================================================= R4-F8
def test_R4F8_refused_take_profit_that_crossed_the_stop_pages_p0(rig, caplog):
    rig.open_aapl()
    real = rig.broker.open_orders
    state = {"armed": False}

    def open_orders():
        if state["armed"]:
            state["armed"] = False
            rig.px("AAPL", 107.0)                                # stop crossed during the query ...
            rig.broker._register(OrderRequest("combo-x", "*", Side.SELL, 5, 1.0, Purpose.TP1, None))
        return real()                                            # ... and an unexplained combo is listed
    rig.broker.open_orders = open_orders
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False
    state["armed"] = True
    rig.tick()
    assert not [s for s in _sells(rig) if s.intent_id.startswith("v6l-")]
    blocked = [s for s, _ in rig.channel.messages if "止损下单被拦截" in s]
    assert blocked and "P0" in blocked[0], rig.channel.messages
    assert "exit blocked AAPL STOP" in caplog.text

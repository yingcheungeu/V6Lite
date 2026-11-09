"""Batch-5 self-review (adversarial pass before delivery). S5-1 .. S5-3 were
failing tests against the first batch-5 version; S5-4 / S5-5 were reproduced
by the reviewer against the real ib_insync objects and are fixed here.

S5-1 (P1) a refused SELL must not begin a STOP stage sized from a ledger
     quantity the same evidence just proved wrong (judging risk is pure)
S5-2 a late BUY of an order created before the symbol was verified flat
     (release of a recovery position, or a resolved freeze) is evidence only
S5-3 cancel()'s server lookup uses the guarded open-orders channel
S5-4 an unsolicited End poisons the channel: the next request renews the
     connection instead of trusting the counts
S5-5 a request that never left the process owes no End
"""

from __future__ import annotations

import pytest

from v6lite.broker.base import BrokerError
from v6lite.models import FillEvent, Purpose, Side
from v6lite.tests.helpers import Rig, make_cfg


def _sells(rig):
    return [r for r in rig.broker.sent if r.side is Side.SELL]


# ---------------------------------------------------------------- S5-1


def test_refused_sell_does_not_size_a_stop_from_a_wrong_ledger(tmp_path, caplog):
    rig = Rig(tmp_path)
    entry_id = rig.open_aapl()
    [pos] = rig.store.open_positions()
    assert pos["qty_open"] == 20
    real = rig.broker.open_orders
    state = {"armed": False}

    def open_orders():
        if state["armed"]:
            state["armed"] = False
            rig.px("AAPL", 107.0)                                   # stop crossed during the query
            rig.broker.external_positions["AAPL"] = (10.0, 113.13)  # a 2nd entry batch (10) not yet booked
            rig.broker.stale_positions = {"AAPL": 20.0}             # the position stream lags the server
        return real()
    rig.broker.open_orders = open_orders
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False
    state["armed"] = True
    rig.tick()                                                      # TP1 queued -> refused (30 vs 20)
    assert rig.store.stage(pos["position_id"], Purpose.STOP) is None   # nothing sized from the wrong 20
    assert any("P0" in m and "止损下单被拦截" in m for m, _ in rig.channel.messages)
    assert "exit blocked AAPL STOP" in caplog.text
    # the missing execution is found and booked: ledger 30 == broker 30
    late = FillEvent("0000fa4e.0000abcd.01.01", entry_id, "AAPL", Side.BUY, 10, 113.13, rig.clock.now())
    rig.engine._ingest_fill(rig.clock.now(), late)
    assert rig.store.position(pos["position_id"])["qty_open"] == 30
    rig.broker.stale_positions = None                               # the stream caught up
    for _ in range(12):
        rig.advance(seconds=31)
        rig.px("AAPL", 107.0)
        rig.engine._reconcile_due = True
        rig.tick()
        for s in _sells(rig):
            o = rig.broker.orders[s.intent_id]
            if o.remaining:
                rig.broker.fill(s.intent_id, price=106.9)
        rig.advance(seconds=1)
        rig.tick()
    sold = sum(s.quantity for s in _sells(rig))
    left = rig.store.position(pos["position_id"])["qty_open"]
    for _ in range(20):                                             # 10 more minutes below the stop
        rig.advance(seconds=31)
        rig.px("AAPL", 106.0)
        rig.tick()
    assert left == 0, f"{left} shares stay open below the stop with no STOP possible"


# ---------------------------------------------------------------- S5-2


def test_late_buy_after_release_of_a_recovery_position_is_evidence_only(tmp_path):
    rig = Rig(tmp_path)
    entry_id = rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    assert stop.purpose == Purpose.STOP
    rig.broker.fill(stop.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    [orig] = rig.store.conn.execute("SELECT * FROM positions").fetchall()
    assert orig["status"] == "CLOSED"
    # late batch 1 of the entry (5): recovery position R
    b1 = FillEvent("0000fa4e.0000aaa1.01.01", entry_id, "AAPL", Side.BUY, 5, 113.13, rig.clock.now())
    rig.broker.external_positions["AAPL"] = (5.0, 113.13)
    rig.engine._ingest_fill(rig.clock.now(), b1)
    [rec] = rig.store.open_positions()
    assert rec["source"] == "late_fill"
    # the operator flattens everything in TWS (incl. batch 2's 5 shares, not reported yet) and releases R
    rig.broker.external_positions["AAPL"] = (0.0, 0.0)
    rig.advance(seconds=61)
    rig.store.set_control(rig.clock.now(), "release:AAPL", '{"operator": "J", "reason": "flat"}')
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.position(rec["position_id"])["status"] == "RELEASED", rig.messages()[-400:]
    # late batch 2 report
    rig.advance(seconds=5)                                      # reported (and stamped) after the release
    b2 = FillEvent("0000fa4e.0000aaa2.01.01", entry_id, "AAPL", Side.BUY, 5, 113.13, rig.clock.now())
    rig.engine._ingest_fill(rig.clock.now(), b2)
    rows = [dict(r) for r in rig.store.conn.execute("SELECT position_id,status,source,qty_open FROM positions")]
    assert rig.store.open_positions() == [], "a released position's late BUY re-created a position"


def test_late_buy_after_a_resolved_freeze_is_evidence_only(tmp_path):
    """R4-F4 flow (freeze resolved, broker verified flat), then an OLDER entry
    batch is reported: it is routed to the recovery path and creates a
    position the broker does not hold."""
    rig = Rig(tmp_path)
    entry_id = rig.open_aapl()
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    stop = rig.broker.sent[-1]
    late = rig.broker.fill(stop.intent_id, 20, emit=False)
    rig.broker.invisible_execs.add(late.exec_id)
    for _ in range(10):
        rig.advance(seconds=31)
        rig.tick()
        if rig.store.control("frozen:AAPL") is not None:
            break
    assert rig.store.control("frozen:AAPL") is not None
    rig.broker.invisible_execs.discard(late.exec_id)
    rig.engine._refresh_fills(rig.clock.now(), force=True)
    t_before_resolve = rig.clock.now()
    rig.advance(seconds=61)
    rig.store.set_control(rig.clock.now(), "release:AAPL", '{"operator": "J", "reason": "flat"}')
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.control("frozen:AAPL") is None
    # an entry batch executed BEFORE the resolution (its shares were sold too) is reported now
    b = FillEvent("0000fa4e.0000bbb1.01.01", entry_id, "AAPL", Side.BUY, 5, 113.13, t_before_resolve)
    rig.engine._ingest_fill(rig.clock.now(), b)
    rows = [dict(r) for r in rig.store.conn.execute("SELECT status,source,qty_open FROM positions")]
    assert rig.store.open_positions() == [], "late BUY after a resolved freeze re-created a position"


# ---------------------------------------------------------------- S5-3/4/5


class _Srv:
    def __init__(self, ib):
        from ib_insync import Stock, util
        self.ib, self.loop = ib, util.getLoop()
        self.stock = Stock("AAPL", "SMART", "USD", conId=265598)
        self.calls, self.free_at, self.plan = 0, 0.0, []

    def _at(self, delay):
        when = max(self.loop.time() + delay, self.free_at + 0.001)
        self.free_at = when
        return when

    def _open_order(self, ref, qty):
        from ib_insync import Order, OrderState
        order = Order(orderId=7, clientId=131, permId=4242, action="SELL", totalQuantity=qty, orderType="LMT",
                      lmtPrice=120.0, orderRef=ref, account="DU0000000")
        self.ib.wrapper.openOrder(7, self.stock, order, OrderState(status="Submitted"))

    def req_all_open_orders(self):
        plan = self.plan[self.calls]
        self.calls += 1
        for ref, qty in plan.get("orders", []):
            self.loop.call_at(self._at(0.005), self._open_order, ref, qty)
        if plan.get("end_delay") is not None:
            self.loop.call_at(self._at(plan["end_delay"]), self.ib.wrapper.openOrderEnd)
        else:
            self.free_at = float("inf")

    def req_positions(self):
        plan = self.plan[self.calls]
        self.calls += 1
        w = self.ib.wrapper
        if plan.get("unsolicited_end_first"):
            self.loop.call_at(self._at(0.002), w.positionEnd)        # an End this client never asked for
        for qty in plan.get("positions", []):
            self.loop.call_at(self._at(0.005), w.position, "DU0000000", self.stock, qty, 100.0)
        self.loop.call_at(self._at(plan.get("end_delay", 0.01)), w.positionEnd)


@pytest.fixture
def real(tmp_path):
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    broker.ib.RequestTimeout = 0.05
    srv = _Srv(broker.ib)
    broker.ib.client.reqAllOpenOrders = srv.req_all_open_orders
    broker.ib.client.reqPositions = srv.req_positions
    # Simulate a connected, API-ready socket; all requests use the local server.
    broker.ib.client.isConnected = lambda: True
    broker.ib.client._apiReady = True
    def no_wire(message):
        raise AssertionError("offline fixed-channel fixture attempted a network send")
    broker.ib.client.conn.sendMsg = no_wire
    assert broker.ib.isConnected()
    return broker, srv


def test_cancel_lookup_uses_the_guarded_channel(real):
    """cancel() sends reqAllOpenOrders WITHOUT the channel guard: the late End
    of a timed-out open_orders() completes cancel's request with an empty
    list -> 'no working order' although the server lists the order."""
    broker, srv = real
    cancelled = []
    broker.ib.cancelOrder = lambda order: cancelled.append(order.orderRef)
    srv.plan = [{"end_delay": None},                                   # open_orders: End withheld through timeout
                {"orders": [("v6l-abc", 10)], "end_delay": 0.02}]     # cancel's lookup: the order IS working
    with pytest.raises(Exception):
        broker.open_orders()
    assert broker._req_sent["openOrders"] == 1 and broker._req_ended["openOrders"] == 0
    srv.free_at = srv.loop.time()
    srv.loop.call_soon(broker.ib.wrapper.openOrderEnd)                  # late End after confirmed timeout
    try:
        broker.cancel("v6l-abc")
        outcome = "cancel sent"
    except BrokerError as exc:
        outcome = f"BrokerError: {exc}"
    assert cancelled == ["v6l-abc"], outcome
    assert broker._req_sent["openOrders"] == broker._req_ended["openOrders"] == 2


def test_unsolicited_end_poisons_the_channel_and_renews_the_connection(real):
    """A real ib_insync End callback with no matching request poisons the
    fixed-key positions channel. The next query must renew the socket before
    trusting any result. No scheduled callbacks or wall-clock waits are used.
    """
    broker, srv = real
    dropped = []
    broker.disconnect = lambda: dropped.append(True)
    assert broker._ensure_channel_hooks()  # install on real IB.client/wrapper objects
    assert broker._req_sent["positions"] == broker._req_ended["positions"] == 0
    broker.ib.wrapper.positionEnd()        # unsolicited End from the actual wrapper method
    assert "positions" in broker._channel_poisoned
    with pytest.raises(BrokerError, match="renewed"):
        broker.positions_fresh()
    assert dropped == [True]
    assert srv.calls == 0, "a poisoned channel must renew before sending reqPositions"


def test_G7_unsolicited_end_regression_does_not_need_wall_clock_sleep(real, monkeypatch):
    """A stalled machine must not change whether this regression proves renewal."""
    broker, _ = real

    def forbidden_sleep(seconds):
        raise AssertionError("test outcome depends on real-time sleep")

    monkeypatch.setattr(broker.ib, "sleep", forbidden_sleep)
    test_unsolicited_end_poisons_the_channel_and_renews_the_connection(real)


def test_a_request_that_never_left_owes_no_end(real, monkeypatch):
    broker, srv = real

    class DefinitelyUnsentConnectionError(ConnectionError, BrokerError):
        def __init__(self, message):
            BrokerError.__init__(self, message, maybe_sent=False)

    # This local request double explicitly fails before native serialization,
    # pacing enqueue, transport or End. Untyped ConnectionError after a wire
    # write remains uncertain (the B25 native-wire regression covers that).
    native_sends, wire, ends = [], [], []
    native_send = broker.ib.client.sendMsg
    wire_send = broker.ib.client.conn.sendMsg
    position_end = broker.ib.wrapper.positionEnd
    def native_spy(message):
        native_sends.append(message)
        return native_send(message)
    def wire_spy(packet):
        wire.append(packet)
        return wire_send(packet)
    def end_spy(*args):
        ends.append(args)
        return position_end(*args)
    monkeypatch.setattr(broker.ib.client, "sendMsg", native_spy)
    monkeypatch.setattr(broker.ib.client.conn, "sendMsg", wire_spy)
    monkeypatch.setattr(broker.ib.wrapper, "positionEnd", end_spy)
    queued_before = tuple(broker.ib.client._msgQ)
    assert queued_before == ()
    entered = []
    def down():
        entered.append(True)
        raise DefinitelyUnsentConnectionError("Not connected")
    broker.ib.client.reqPositions = down
    with pytest.raises(ConnectionError, match="Not connected"):
        broker.positions_fresh()                                 # hooks wrap `down`
    assert entered == [True]
    assert broker._req_sent["positions"] == broker._req_ended["positions"] == 0
    assert native_sends == [] and wire == [] and ends == []
    assert tuple(broker.ib.client._msgQ) == queued_before


def test_auto_open_orders_owes_no_end():
    from v6lite.broker.ibkr import _CHANNELS
    assert "reqAutoOpenOrders" not in _CHANNELS["openOrders"][1]

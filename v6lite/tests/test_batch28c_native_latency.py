"""B28c: engine overnight TP latency at the installed SDK connection wire.

The B28b fixture supplies a real prior-day entry, durable SQLite and restart.
This file then uses the installed SDK's synchronous request methods, Futures,
wrapper rows/End callbacks, native pacing and placeOrder serialization. Only
an offline server at conn.sendMsg is scripted. No engine evidence/gate/cache
is replaced or granted. FakeBroker remains the independent economic oracle.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import replace
from datetime import timedelta, timezone

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.models import BrokerAck, OrderRequest, ProtectionRequest, Purpose, Side
from v6lite.tests.test_batch24_pacing import _assert_wire_limits, _opcode
from v6lite.tests.test_batch28b_overnight_refresh import (
    HIT, overnight_run, _no_network, _safety, _tp_requests,
)

# NativeExecutionHarness temporarily substitutes a virtual monotonic clock.
# Capture the actual clock at collection, before any fixture installs it.
_REAL_MONOTONIC = time.monotonic


class NativeLatencyServer:
    """A synthetic broker responding to real SDK request packets after RTT.

    Never return Python lists from reqAllOpenOrders/reqPositions/reqExecutions:
    the actual matching native End must resolve the SDK's pending Future.
    Wire timestamps are taken inside conn.sendMsg, including queued writes.
    """

    def __init__(self, rig, pid, harness, monkeypatch, request, *, invalidation=None):
        from ib_insync import IB, util

        self.rig, self.pid, self.harness = rig, pid, harness
        self.adapter, self.ib = harness.adapter, harness.ib
        self.client, self.wrapper = self.ib.client, self.ib.wrapper
        self.loop = asyncio.get_event_loop_policy().get_event_loop()
        self.rtt = .06
        self.invalidation = invalidation
        self.wire, self.ends, self.requests = [], [], []
        self.reduction_wire = self.ack_at = self.tp_wire = self.fill_at = None
        self.returned_at = None
        self.ack_released = False
        self.stopped = False
        self.handles = []
        self.pending_reduction = None
        self.ref = rig.store.protection(pid)['intent_id']
        rig.at(HIT)

        # Switch the B28b preparation fixture's virtual clock to a real elapsed
        # clock without resetting any pacing history or evidence timestamp.
        old_mono = rig.clock.now().timestamp()
        new_mono = _REAL_MONOTONIC()
        delta = new_mono - old_mono
        anchor = rig.clock.now()
        monkeypatch.setattr(time, 'monotonic', _REAL_MONOTONIC)
        monkeypatch.setattr(rig.clock, 'now',
            lambda: anchor + timedelta(seconds=_REAL_MONOTONIC() - new_mono))
        monkeypatch.setattr('ib_insync.client.getLoop', lambda: self.loop)
        monkeypatch.setattr(self.ib, 'sleep', util.sleep)
        self.ib.RequestTimeout = 3
        # The preparation server has no uncompleted native requests or wire.
        assert not self.client._msgQ and not self.harness.loop.queue
        assert not self.wrapper._futures
        for channel, at in list(self.adapter._query_last_finished.items()):
            self.adapter._query_last_finished[channel] = at + delta
        pacer = self.adapter._message_pacer
        pacer.sent_times = deque(at + delta for at in pacer.sent_times)
        pacer.last_sent = {channel: at + delta for channel, at in pacer.last_sent.items()}
        self.client._timeQ = deque(at + delta for at in self.client._timeQ)
        self.harness.loop = self.loop

        # Restore actual SDK blocking calls after B28b's preparation helper.
        for name in ('reqAllOpenOrders', 'reqPositions', 'reqExecutions'):
            monkeypatch.setattr(self.ib, name, getattr(IB, name).__get__(self.ib, IB))
        monkeypatch.setattr(self.client.conn, 'sendMsg', self.send)
        self.client._serverVersion = 180
        self.wrapper.clientId = rig.cfg.client_id
        self.client.clientId = rig.cfg.client_id
        self.client._accounts = [rig.cfg.account]
        self.adapter._ensure_channel_hooks()
        rig.broker.query_generation = self.adapter.query_generation

        # Seed the native broker's already-existing order cache by actual
        # wrapper messages before enabling live callbacks. No engine proof is
        # edited; the following stage must still request its own full proof.
        self.emit_order(self.ref)
        self.ib.orderStatusEvent += self.adapter._on_status
        self.ib.openOrderEvent += self.adapter._on_open_order
        self.adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)

        def native_query(method):
            try:
                return method()
            finally:
                rig.broker.query_generation = self.adapter.query_generation

        monkeypatch.setattr(rig.broker, 'open_orders',
            lambda: native_query(self.adapter.open_orders))
        monkeypatch.setattr(rig.broker, 'positions_fresh',
            lambda: native_query(self.adapter.positions_fresh))
        monkeypatch.setattr(rig.broker, 'executions_today',
            lambda: native_query(self.adapter.executions_today))
        monkeypatch.setattr(rig.broker, 'account_executions_today',
            lambda: native_query(self.adapter.account_executions_today))
        monkeypatch.setattr(rig.broker, 'account_executions_today_scoped',
            lambda: native_query(self.adapter.account_executions_today_scoped), raising=False)
        self.fake_modify = rig.broker.modify_protection
        self.fake_submit = rig.broker.submit_limit

        def modify(req):
            _safety(rig, req)
            # Store the broker-side pending request independently of the SDK
            # Trade.order object, which placeOrder mutates before any ACK.
            self.fake_modify(req)
            self.pending_reduction = req if req.quantity == 2 and req.trigger_price == 338.62 else None
            return self.adapter.modify_protection(req)

        def submit(req):
            _safety(rig, req)
            ack = self.adapter.submit_limit(req)  # real placeOrder + pacing
            self.returned_at = self.loop.time()
            # Match FakeBroker economics to the actual serialized SDK orderId.
            rig.broker._next_id = int(ack.broker_order_id) - 1
            economic_ack = self.fake_submit(req)
            assert economic_ack.broker_order_id == ack.broker_order_id
            return BrokerAck(ack.broker_order_id, economic_ack.perm_id)

        monkeypatch.setattr(rig.broker, 'modify_protection', modify)
        monkeypatch.setattr(rig.broker, 'submit_limit', submit)
        self.refresh_book()
        request.addfinalizer(self.close)

    def later(self, seconds, callback, *args):
        handle = self.loop.call_later(seconds, callback, *args)
        self.handles.append(handle)
        return handle

    def close(self):
        self.stopped = True
        for handle in self.handles:
            handle.cancel()

    def refresh_book(self):
        if self.stopped:
            return
        if self.invalidation != 'stale-book' or not self.ack_released:
            self.rig.refresh_quotes()
        self.later(.03, self.refresh_book)

    def contract(self, symbol):
        from ib_insync import Stock
        result = Stock(symbol, 'SMART', 'USD', conId=self.rig.broker.con_ids[symbol])
        self.adapter._contracts[symbol] = result
        return result

    def emit_order(self, ref):
        from ib_insync import LimitOrder, OrderState, StopOrder
        native = self.rig.broker.orders[ref]
        req = native.req
        common = dict(orderId=native.broker_order_id,
            permId=native.broker_order_id + 50_000, clientId=self.rig.cfg.client_id,
            account=self.rig.cfg.account, orderRef=ref, outsideRth=False)
        if isinstance(req, ProtectionRequest):
            order = StopOrder('SELL', req.quantity, req.trigger_price, tif='GTC', **common)
        else:
            order = LimitOrder(str(req.side), req.quantity, req.limit_price, tif='DAY', **common)
        self.wrapper.lastTime = self.rig.clock.now()
        self.wrapper.openOrder(order.orderId, self.contract(req.symbol), order,
                               OrderState(status=native.status))
        self.wrapper.orderStatus(order.orderId, native.status, native.filled,
            native.remaining, 0., order.permId, 0, 0., order.clientId, '')

    def send(self, packet):
        message = packet[4:].decode()
        at = self.loop.time()
        self.wire.append((at, message))
        fields = message.split('\0')
        code = fields[0]
        if code in {'5', '16', '61', '7'}:
            self.requests.append((at, code, tuple(fields)))
            # A real round-trip occurs AFTER the physical request packet.
            self.later(self.rtt, self.respond, code, fields)
        elif code == '3':
            # Actual ib_insync Client.placeOrder serialization at server 180:
            # opcode/orderId, 12 contract fields, security IDs, action/qty/type.
            assert fields[16] == 'SELL'
            kind, tif, ref = fields[18], fields[21], fields[26]
            if kind == 'STP' and float(fields[17]) == 2 and float(fields[20]) == 338.62:
                self.reduction_wire = at
                self.later(self.rtt, self.release_ack)
            elif kind == 'LMT' and tif == 'DAY':
                assert self.ack_at is not None, 'DAY TP must never precede the native reduction ACK'
                assert ref.startswith('v6l-') and float(fields[17]) == 1
                assert float(fields[19]) > 0
                self.tp_wire = at
                self.later(self.rtt, self.fill_tp, ref)

    def respond(self, code, fields):
        self.wrapper.lastTime = self.rig.clock.now()
        if self.invalidation == 'socket' and self.ack_at is not None:
            # A renewed socket cannot authorize from the pre-reset round.
            # Leave its new request genuinely incomplete, so this negative
            # distinguishes safe fallback from reusing the old full proof.
            return
        if code in {'5', '16'}:
            for ref, native in list(self.rig.broker.orders.items()):
                if native.status in {'Submitted', 'PreSubmitted', 'PendingCancel'}:
                    self.emit_order(ref)
            self.wrapper.openOrderEnd()
            self.ends.append((self.loop.time(), 'openOrders', None))
        elif code == '61':
            for position in FakeBroker.positions_fresh(self.rig.broker):
                self.wrapper.position(position.account, self.contract(position.symbol),
                                      position.quantity, position.avg_cost)
            self.wrapper.positionEnd()
            self.ends.append((self.loop.time(), 'positions', None))
        else:
            req_id, since = int(fields[2]), fields[5]
            rows = FakeBroker.executions_today(self.rig.broker)
            # The synthetic TWS defaults to current ET day, as in B28b.
            rows = [fill for fill in rows if fill.time.astimezone(self.rig.clock.now().tzinfo).date()
                    == self.rig.clock.now().date()]
            for fill in rows:
                self.harness.decode(fill, fill.time.astimezone(timezone.utc).strftime('%Y%m%d %H:%M:%S UTC'),
                                    req_id=req_id)
            assert req_id in self.wrapper._futures
            self.wrapper.execDetailsEnd(req_id)
            self.harness.ends.append((req_id, self.rig.clock.now()))
            self.ends.append((self.loop.time(), 'account' if since else 'executions', req_id))

    def release_ack(self):
        if self.ack_released:
            return
        self.ack_released = True
        rig = self.rig
        pending = rig.broker.protection_pending_modifications.pop(self.ref)
        assert pending == self.pending_reduction
        # The independent server finally applies the requested decrease.
        native = rig.broker.orders[self.ref]
        native.req, native.status = pending, 'PreSubmitted'
        if self.invalidation == 'hold':
            rig.store.update_intent(rig.clock.now(), self.ref, hold_reason='b28c synthetic native identity hold')
        elif self.invalidation == 'socket':
            self.adapter._reset_channels()
            self.adapter._ensure_channel_hooks()
            rig.broker.query_generation = self.adapter.query_generation
        elif self.invalidation == 'other-SELL':
            other = OrderRequest('v6l-b28c-other-seller', 'AAPL', Side.SELL, 1, 344., Purpose.TP1)
            rig.store.create_intent(rig.clock.now(), other, atr=None, count_entry_day=None)
            ack = FakeBroker.submit_limit(rig.broker, other)
            rig.store.update_intent(rig.clock.now(), other.intent_id, status='SENT',
                broker_order_id=ack.broker_order_id, perm_id=ack.perm_id)
            # Its native report is visible before the reduction callback. The
            # next completed full openOrders query must include this seller.
            self.emit_order(other.intent_id)
        elif self.invalidation == 'stale-book':
            for symbol, quote in list(rig.broker.quotes.items()):
                rig.broker.quotes[symbol] = replace(quote, ts=rig.clock.now() - timedelta(seconds=3.01))
        self.ack_at = self.loop.time()
        self.emit_order(self.ref)  # native openOrder + orderStatus, never fake StatusEvent
        if self.invalidation != 'other-SELL':
            _safety(rig)

    def fill_tp(self, ref):
        rig = self.rig
        assert ref in rig.broker.orders, 'local submit return must register economics before RTT elapses'
        fill = FakeBroker.fill(rig.broker, ref, qty=1, price=344.04,
                               exec_id='b28c-native-tp1.01.01', emit=False)
        self.fill_at = self.loop.time()
        self.harness.decode(fill, fill.time.astimezone(timezone.utc).strftime('%Y%m%d %H:%M:%S UTC'))
        self.emit_order(ref)
        _safety(rig)

    def drive(self, *, timeout=14):
        rig = self.rig
        rig.px('AAPL', 343.94)
        deadline = self.loop.time() + timeout
        # Invoke ordinary production priority/exit entrypoints, never a fast
        # helper or private proof constructor introduced by the repair.
        rig.engine._evaluate_exits(rig.clock.now())
        rig.engine._run_queue()
        while self.loop.time() < deadline:
            self.ib.sleep(.01)
            rig.engine._drain(rig.clock.now())
            if self.ack_at is None:
                rig.engine._evaluate_exits(rig.clock.now())
                rig.engine._run_queue()
            rig.engine.process_protection_priority()
            if self.fill_at is not None:
                return
            if self.ack_at is not None and self.invalidation and self.loop.time() - self.ack_at >= 2:
                return
        assert self.ack_at is not None, ('no actual reduction ACK', self.requests)


def test_B28c_overnight_reduction_ACK_to_actual_DAY_LMT_wire_under_one_second(
        overnight_run, monkeypatch, request, record_property):
    rig, pid, harness = overnight_run()
    server = NativeLatencyServer(rig, pid, harness, monkeypatch, request)
    server.drive()
    assert server.reduction_wire is not None and server.ack_at is not None
    assert server.tp_wire is not None and server.fill_at is not None, server.wire
    elapsed = server.tp_wire - server.ack_at
    record_property('b28c_ACK_to_TP_wire_seconds', elapsed)
    assert elapsed <= 1., ('ACK -> actual conn.sendMsg DAY LMT', elapsed, server.requests, server.ends)
    # The original B clock must include all post-ACK work and real wire RTT.
    gaps = [json.loads(row['payload']) for row in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='PROTECT_GAP'")
        if json.loads(row['payload']).get('gap_kind') == 'B']
    assert len(gaps) == 1 and 0 < gaps[0]['secs'] <= 5, gaps
    assert server.fill_at - server.ack_at <= 5
    assert rig.store.position(pid)['qty_open'] == 2
    assert len(_tp_requests(rig)) == 1
    # A fast dispatch still needs a completed real four-source round before
    # the first decrease. End messages, not list mocks, establish completion.
    completed_before_reduce = {kind for at, kind, _ in server.ends if at <= server.reduction_wire}
    assert {'openOrders', 'positions', 'executions', 'account'} <= completed_before_reduce
    assert all(getattr(self_future, 'done', lambda: False)()
               for self_future in server.wrapper._futures.values())
    _assert_wire_limits(server.wire)
    _safety(rig)


@pytest.mark.parametrize('invalidation', ['socket', 'hold', 'other-SELL', 'stale-book'])
def test_B28c_changed_authority_or_book_after_preproof_never_dispatches_DAY_TP(
        overnight_run, monkeypatch, request, invalidation):
    rig, pid, harness = overnight_run()
    server = NativeLatencyServer(rig, pid, harness, monkeypatch, request, invalidation=invalidation)
    server.drive()
    assert server.ack_at is not None
    position_tps = [request for request in _tp_requests(rig) if request.position_id == pid]
    assert server.tp_wire is None and position_tps == [], (invalidation, server.wire)
    _assert_wire_limits(server.wire)
    if invalidation != 'other-SELL':
        _safety(rig)


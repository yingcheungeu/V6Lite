"""B28c adversarial native edges, independent of fast-path helper names.

Only this new file owns these cases. The frozen native_latency spec supplies
real SDK request/End/pacing/wire plumbing; no production evidence is granted
or replaced. Test-only server economics remain separate from mutable Trades.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.models import OrderRequest, Purpose, Side, StatusEvent
from v6lite.tests.test_batch24_pacing import _assert_wire_limits
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network
from v6lite.tests.test_batch28c_native_latency import NativeLatencyServer


def _observe_attempts(rig, pid, monkeypatch):
    """Passive before-write observation, including a refused unsafe attempt.

    The independent safety oracle in NativeLatencyServer may prevent an
    unsafe physical write. Such an attempted TP must still fail these tests;
    an oracle exception must not accidentally make the negative test pass.
    """
    attempts = []
    submit = rig.broker.submit_limit

    def observed(req):
        if req.position_id == pid and req.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}:
            attempts.append((rig.clock.now(), req))
        return submit(req)

    monkeypatch.setattr(rig.broker, 'submit_limit', observed)
    return attempts


class OrphanNativeSellServer(NativeLatencyServer):
    def __init__(self, *args, incomplete, **kwargs):
        self.orphan_ref = 'v6l-b28c-unrecorded-native-seller'
        self.incomplete = incomplete
        self.orphan_echo_at = None
        # Use this label only for the existing driver's bounded negative
        # observation window; mutation is entirely owned by release_ack below.
        super().__init__(*args, invalidation='other-SELL', **kwargs)

    def emit_order(self, ref):
        if ref != self.orphan_ref or not self.incomplete:
            return super().emit_order(ref)
        from ib_insync import LimitOrder, OrderState

        native = self.rig.broker.orders[ref]
        order = LimitOrder('SELL', 1, 344., tif='DAY', orderId=native.broker_order_id,
            # Missing native permId prevents a complete identity. A raw
            # working-book change must still invalidate, never silently drop.
            permId=0, clientId=self.rig.cfg.client_id, account=self.rig.cfg.account,
            orderRef=ref, outsideRth=False)
        self.wrapper.lastTime = self.rig.clock.now()
        self.wrapper.openOrder(order.orderId, self.contract('AAPL'), order,
                               OrderState(status='Submitted'))
        self.wrapper.orderStatus(order.orderId, 'Submitted', 0., 1., 0., 0, 0,
                                 0., order.clientId, '')

    def release_ack(self):
        if self.ack_released:
            return
        self.ack_released = True
        native = self.rig.broker.orders[self.ref]
        pending = self.rig.broker.protection_pending_modifications.pop(self.ref)
        native.req, native.status = pending, 'PreSubmitted'
        # This seller exists at the synthetic broker but has no durable
        # engine intent. It is exactly the orphan v6l-prefixed failure mode.
        orphan = OrderRequest(self.orphan_ref, 'AAPL', Side.SELL, 1, 344., Purpose.TP1)
        FakeBroker.submit_limit(self.rig.broker, orphan)
        assert self.rig.store.intent(self.orphan_ref) is None
        self.orphan_echo_at = self.loop.time()
        self.emit_order(self.orphan_ref)
        self.ack_at = self.loop.time()
        self.emit_order(self.ref)
        # Do not bless this exogenous race with an oracle assertion before
        # production has contained the previous conservative S=3 reservation.


@pytest.mark.parametrize('incomplete', [False, True], ids=['orphan-full-identity', 'orphan-no-permId'])
def test_B28c_raw_new_v6l_SELL_even_without_complete_identity_revokes_reduction_candidate(
        overnight_run, monkeypatch, request, incomplete):
    rig, pid, harness = overnight_run()
    server = OrphanNativeSellServer(rig, pid, harness, monkeypatch, request, incomplete=incomplete)
    attempts = _observe_attempts(rig, pid, monkeypatch)
    server.drive()
    assert server.reduction_wire is not None and server.orphan_echo_at is not None
    assert server.orphan_echo_at <= server.ack_at
    assert not attempts and server.tp_wire is None, (incomplete, attempts, server.wire)
    assert any(code in {'5', '16'} and at >= server.orphan_echo_at
               for at, code, _ in server.requests), 'candidate invalidation must fall back to real proof'
    _assert_wire_limits(server.wire)


class QueuedOldStatusServer(NativeLatencyServer):
    def __init__(self, rig, pid, harness, monkeypatch, request):
        from ib_insync import OrderStatus, StopOrder, Trade

        super().__init__(rig, pid, harness, monkeypatch, request, invalidation='old-queued-status')
        self.created_at = rig.clock.now()
        self.created_mono = self.loop.time()
        self.old_status = None
        captured = []
        self.adapter.set_urgent_handler(captured.append)
        row = rig.store.protection(pid)
        # A genuine native status event from an earlier same-order S=2 state
        # is created now, before the new full proof and reduction request.
        # Its delayed delivery must retain this immutable creation provenance.
        old_order = StopOrder('SELL', 2, 338.62, tif='GTC', orderId=row['broker_order_id'],
            permId=row['perm_id'], clientId=rig.cfg.client_id, account=rig.cfg.account,
            orderRef=self.ref, outsideRth=False)
        old_trade = Trade(self.contract('AAPL'), old_order, OrderStatus(
            status='PreSubmitted', orderId=old_order.orderId, permId=old_order.permId,
            clientId=rig.cfg.client_id, filled=0., remaining=2.))
        self.wrapper.lastTime = self.created_at
        self.adapter._on_status(old_trade)
        statuses = [event for event in captured if isinstance(event, StatusEvent)]
        assert len(statuses) == 1
        self.old_status = statuses[0]
        self.adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
        # The installed IB.placeOrder does not itself replace an existing
        # Trade.order when the adapter passes a new StopOrder. Exercise the
        # mutable-cache hazard explicitly through a local SDK modify listener;
        # this emits no broker openOrder and grants no engine evidence.
        def local_cache_listener(trade):
            if trade.order.orderRef == self.ref and self.pending_reduction is not None:
                trade.order.totalQuantity = self.pending_reduction.quantity
        self.ib.orderModifyEvent += local_cache_listener
        self.cached_local_total = None
        self.old_delivered_at = None

    def release_ack(self):
        if self.ack_released:
            return
        self.ack_released = True
        # No broker decrease or new openOrder echo occurs. Only the local SDK
        # Trade was changed by a local modify listener. Server truth is still S=3.
        assert self.rig.broker.orders[self.ref].req.quantity == 3
        assert self.rig.broker.protection_pending_modifications[self.ref].quantity == 2
        row = self.rig.store.protection(self.pid)
        key = self.wrapper.orderKey(self.rig.cfg.client_id, row['broker_order_id'], row['perm_id'])
        self.cached_local_total = self.wrapper.trades[key].order.totalQuantity
        assert self.cached_local_total == 2
        self.old_delivered_at = self.loop.time()
        self.ack_at = self.old_delivered_at  # measurement marker, deliberately not a new ACK
        self.rig.engine._on_urgent_broker_event(self.old_status)


def test_B28c_queued_old_native_status_cannot_use_locally_mutated_Trade_as_new_reduction_ACK(
        overnight_run, monkeypatch, request):
    rig, pid, harness = overnight_run()
    server = QueuedOldStatusServer(rig, pid, harness, monkeypatch, request)
    attempts = _observe_attempts(rig, pid, monkeypatch)
    server.drive()
    assert server.created_mono < server.reduction_wire < server.old_delivered_at
    assert server.cached_local_total == 2
    assert rig.broker.orders[server.ref].req.quantity == 3, 'the synthetic broker never ACKed S=2'
    assert not attempts and server.tp_wire is None, (attempts, server.wire)
    assert any(code in {'5', '16'} and at >= server.old_delivered_at
               for at, code, _ in server.requests), 'old provenance must require a new completed proof'
    _assert_wire_limits(server.wire)


class CongestedOldBookServer(NativeLatencyServer):
    def __init__(self, *args, **kwargs):
        self.book_at = self.book_ts = None
        self.congestion_at = None
        super().__init__(*args, invalidation='old-book-at-congested-wire', **kwargs)

    def refresh_book(self):
        if self.stopped or self.ack_released:
            return
        self.rig.refresh_quotes()
        self.later(.03, self.refresh_book)

    def release_ack(self):
        if self.ack_released:
            return
        self.ack_released = True
        pending = self.rig.broker.protection_pending_modifications.pop(self.ref)
        native = self.rig.broker.orders[self.ref]
        native.req, native.status = pending, 'PreSubmitted'
        self.book_at = self.rig.clock.now()
        self.book_ts = self.book_at - timedelta(seconds=2.9)
        for symbol, quote in list(self.rig.broker.quotes.items()):
            from dataclasses import replace
            self.rig.broker.quotes[symbol] = replace(quote, ts=self.book_ts)
        # Real native requests consume the real connection's 10/s window.
        # No pacer history, timestamps or readiness helper are overwritten.
        for _ in range(10):
            self.client.reqCurrentTime()
        self.congestion_at = self.loop.time()
        recent = [at for at, _ in self.wire if self.congestion_at - 1 < at <= self.congestion_at]
        assert len(recent) == 10, ('the physical global window must actually be full', recent)
        self.ack_at = self.loop.time()
        self.emit_order(self.ref)


def test_B28c_quote_2_9_seconds_old_is_not_queued_and_sent_after_3_second_wire_deadline(
        overnight_run, monkeypatch, request):
    rig, pid, harness = overnight_run()
    server = CongestedOldBookServer(rig, pid, harness, monkeypatch, request)
    server.drive()
    assert server.book_at is not None and server.congestion_at is not None
    assert (server.book_at - server.book_ts).total_seconds() == pytest.approx(2.9)
    # Keep the real event loop running past the expired deadline. A placeOrder
    # return or an empty immediate wire list must not hide a stale queued TP.
    server.ib.sleep(1.2)
    assert server.tp_wire is None, ('stale DAY TP physically transmitted after queueing',
                                    server.tp_wire, server.ack_at, server.wire)
    assert (rig.clock.now() - server.book_ts).total_seconds() > 3
    _assert_wire_limits(server.wire)

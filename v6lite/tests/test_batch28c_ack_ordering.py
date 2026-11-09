"""Both genuine native ACK orderings must meet the same physical-wire bound."""
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network, _safety
from v6lite.tests.test_batch28c_native_latency import NativeLatencyServer


class StatusFirstServer(NativeLatencyServer):
    def release_ack(self):
        if self.ack_released:
            return
        self.ack_released = True
        native = self.rig.broker.orders[self.ref]
        pending = self.rig.broker.protection_pending_modifications.pop(self.ref)
        native.req, native.status = pending, 'PreSubmitted'
        self.ack_at = self.loop.time()
        self.wrapper.orderStatus(native.broker_order_id, 'PreSubmitted', native.filled,
            native.remaining, 0., native.broker_order_id+50000, 0, 0., self.rig.cfg.client_id, 'ack-first')
        self.later(.08, self.only_open_order)

    def only_open_order(self):
        from ib_insync import StopOrder, OrderState
        native = self.rig.broker.orders[self.ref]
        req = native.req
        order = StopOrder('SELL', req.quantity, req.trigger_price, tif='GTC',
            orderId=native.broker_order_id, permId=native.broker_order_id+50000,
            clientId=self.rig.cfg.client_id, account=self.rig.cfg.account,
            orderRef=self.ref, outsideRth=False)
        self.wrapper.lastTime = self.rig.clock.now()
        self.wrapper.openOrder(order.orderId, self.contract('AAPL'), order, OrderState(status='PreSubmitted'))


def test_B28c_status_then_openOrder_without_repeated_status_dispatches_within_one_second(
        overnight_run, monkeypatch, request, record_property):
    rig, pid, harness = overnight_run()
    server = StatusFirstServer(rig, pid, harness, monkeypatch, request)
    server.drive()
    assert server.ack_at is not None and server.tp_wire is not None and server.fill_at is not None
    delay = server.tp_wire-server.ack_at
    record_property('b28c_status_first_ACK_to_TP_wire_seconds', delay)
    assert delay <= 1., (delay, server.requests)
    assert server.fill_at-server.ack_at <= 5
    _safety(rig)

"""Ordinary priority ticks must not query over a valid incomplete ACK pair."""
import pytest
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network, _safety
from v6lite.tests.test_batch28c_ack_ordering import StatusFirstServer


class SeparatedAckServer(StatusFirstServer):
    def release_ack(self):
        if self.ack_released:
            return
        self.ack_released = True
        native = self.rig.broker.orders[self.ref]
        native.req = self.rig.broker.protection_pending_modifications.pop(self.ref)
        native.status = 'PreSubmitted'
        self.ack_at = self.loop.time()
        self.wrapper.orderStatus(native.broker_order_id, 'PreSubmitted', native.filled,
            native.remaining, 0., native.broker_order_id+50000, 0, 0., self.rig.cfg.client_id, 'separated-ack')
        self.later(.40, self.only_open_order)

    def only_open_order(self):
        self.echo_at = self.loop.time()
        super().only_open_order()

    def drive(self, *, timeout=14):
        rig = self.rig
        rig.px('AAPL', 343.94)
        deadline = self.loop.time()+timeout
        ordinary_pass = False
        rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue()
        while self.loop.time() < deadline:
            self.ib.sleep(.01); rig.engine._drain(rig.clock.now())
            if self.ack_at is None:
                rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue()
            if self.ack_at is not None and self.loop.time()-self.ack_at >= .30 and not ordinary_pass:
                ordinary_pass = True
                rig.engine._protection_progress(rig.clock.now())
            rig.engine.process_protection_priority()
            if self.fill_at is not None:
                return
        assert self.fill_at is not None, self.requests


def test_B28c_status_then_400ms_openOrder_survives_ordinary_priority_tick(
        overnight_run, monkeypatch, request, record_property):
    rig, pid, harness = overnight_run()
    server = SeparatedAckServer(rig, pid, harness, monkeypatch, request)
    server.drive()
    assert server.ack_at is not None and server.tp_wire is not None and server.fill_at is not None
    delay = server.tp_wire-server.ack_at
    record_property('b28c_separated_ACK_to_TP_wire_seconds', delay)
    assert delay <= 1., (delay, server.requests)
    # The ordinary periodic pass explicitly runs at the 0.30s boundary.
    # Use actual echo time: Windows loop clocks can deliver timers slightly early.
    assert not [(at, code) for at, code, _ in server.requests if server.ack_at <= at < server.echo_at]
    assert server.fill_at-server.ack_at <= 5
    _safety(rig)

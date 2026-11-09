"""Pair deadline applies inside callbacks, even between ordinary ticks."""
import pytest
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network
from v6lite.tests.test_batch28c_ack_ordering import StatusFirstServer


class LatePairServer(StatusFirstServer):
    def __init__(self, *args, late_first, **kwargs):
        super().__init__(*args, **kwargs)
        self.late_first = late_first
        self.scheduled = False
        self.echo_at = None

    def release_ack(self):
        if self.late_first and not self.scheduled:
            self.scheduled = True
            self.later(1.25, self.release_ack)
            return
        if self.ack_released:
            return
        self.ack_released = True
        native = self.rig.broker.orders[self.ref]
        native.req = self.rig.broker.protection_pending_modifications.pop(self.ref)
        native.status = 'PreSubmitted'
        self.ack_at = self.loop.time()
        self.wrapper.orderStatus(native.broker_order_id, 'PreSubmitted', native.filled,
            native.remaining, 0., native.broker_order_id+50000, 0, 0., self.rig.cfg.client_id, 'late-pair')
        self.later(.08 if self.late_first else 3., self.only_open_order)

    def only_open_order(self):
        self.echo_at = self.loop.time()
        super().only_open_order()


@pytest.mark.parametrize('late_first', [False, True])
def test_B28c_expired_pair_cannot_be_revived_inside_late_native_callback(
        overnight_run, monkeypatch, request, late_first):
    rig, pid, harness = overnight_run()
    server = LatePairServer(rig, pid, harness, monkeypatch, request, late_first=late_first)
    rig.px('AAPL', 343.94)
    rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue()
    deadline = server.loop.time()+12
    while server.loop.time() < deadline and server.echo_at is None:
        server.ib.sleep(.01)
        rig.engine._drain(rig.clock.now())
        if server.reduction_wire is None:
            rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue()
            rig.engine.process_protection_priority()
        # Once a reduction is physically sent, intentionally leave ordinary
        # ticks idle. The SDK still delivers real callbacks and fresh quotes.
    assert server.echo_at is not None and server.ack_at is not None
    assert server.tp_wire is None, 'late pair cannot authorize without a new complete proof'
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='PROTECT_REDUCTION_CONTINUATION'").fetchone() is None

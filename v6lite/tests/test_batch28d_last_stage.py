"""Final means all remaining shares, regardless of TP ordinal.

Use a real partial original-STP fill to leave one share before the first TP;
no SQL, qty/stage edits, proof injection, or authorization helper replacement.
"""
from __future__ import annotations

from datetime import timezone

from v6lite.broker.fake import FakeBroker
from v6lite.models import Purpose, StatusEvent
from v6lite.tests.test_batch24_pacing import _assert_wire_limits, _opcode
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network, _safety, _tp_requests
from v6lite.tests.test_batch28d_native_final_tp import NativeFinalTPServer, TERMINAL, _p0


class AnyLastStageServer(NativeFinalTPServer):
    """Keep the same physical native wire; derive final stage from economics."""
    def __init__(self, rig, pid, harness, monkeypatch, request):
        self.final_stage = None
        self.final_stage_attempts = []
        super().__init__(rig, pid, harness, monkeypatch, request)
        native_submit = rig.broker.submit_limit
        def observe_attempt(req):
            if req.position_id == pid and req.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}:
                self.final_stage_attempts.append(req)
            return native_submit(req)
        monkeypatch.setattr(rig.broker, 'submit_limit', observe_attempt)

    def observe_native(self, event):
        if isinstance(event, StatusEvent) and event.intent_id == self.ref and event.raw_status == 'Cancelled':
            order = event.order_identity
            native = self.rig.broker.orders[self.ref]
            row = self.rig.store.protection(self.pid)
            # The original STP has genuinely filled two shares. The receipt
            # must prove this actual native total/filled/remainder, not zero.
            if (self.cancel_wire is not None and order is not None and event.identity_checked and
                    not event.connection_snapshot and not getattr(event, 'query_snapshot', False) and
                    event.error_code in {0, 202} and native.status == 'Cancelled' and
                    event.observed_at is not None and
                    self.cancel_wall < event.observed_at <= self.rig.clock.now() and
                    event.socket_generation == self.adapter.query_generation and
                    order.intent_id == self.ref and event.broker_order_id == order.broker_order_id == row['broker_order_id'] and
                    event.perm_id == order.perm_id == row['perm_id'] and
                    order.client_id == self.rig.cfg.client_id and order.account == self.rig.cfg.account and
                    order.con_id == row['con_id'] and order.symbol == 'AAPL' and order.side == 'SELL' and
                    order.order_type == 'STP' and order.tif == 'GTC' and order.outside_rth is False and
                    order.total_quantity == native.req.quantity and
                    event.filled == order.filled == native.filled and
                    event.remaining == order.remaining == native.remaining == 1):
                self.cancel_receipts.append(event)
        self.deliver(event)

    def send(self, packet):
        fields = packet[4:].decode().split('\0')
        if fields[0] == '3' and fields[18] == 'LMT':
            intent = self.rig.store.intent(fields[26])
            assert intent is not None and intent['position_id'] == self.pid
            stage = Purpose(intent['purpose'])
            pos = self.rig.store.position(self.pid)
            assert int(float(fields[17])) == int(pos['qty_open']) == 1
            assert self.cancel_receipts and self.rig.broker.orders[self.ref].status == 'Cancelled'
            assert self.final_stage is None, 'one final stage, one physical DAY packet'
            self.final_stage = stage
            self.final_tp_wire = self.loop.time()
        return super().send(packet)

    def fill_stage(self, ref):
        stage = Purpose(self.rig.store.intent(ref)['purpose'])
        super().fill_stage(ref)
        if stage == self.final_stage:
            self.final_fill_at = self.fill_by_stage[stage][0]


def test_B28d_TP1_can_be_the_final_share_after_real_original_STP_partial_fill(
        overnight_run, monkeypatch, request, record_property):
    rig, pid, harness = overnight_run()
    server = AnyLastStageServer(rig, pid, harness, monkeypatch, request)
    # Book the actual original STP's two-share execution through the native
    # decoder and urgent engine callback. Its native remainder is now one.
    fill = FakeBroker.fill(rig.broker, server.ref, qty=2, price=338.62,
        exec_id='b28d-original-stp-partial-two.01.01', emit=False)
    harness.decode(fill, fill.time.astimezone(timezone.utc).strftime('%Y%m%d %H:%M:%S UTC'))
    server.emit_order(server.ref)
    rig.engine._drain(rig.clock.now())
    pos = rig.store.position(pid)
    assert pos['qty_initial'] == 3 and pos['qty_open'] == 1 and pos['status'] == 'OPEN'
    assert pos['tp1_done'] == 0 and pos['tp2_done'] == 0
    assert rig.store.intent_filled_qty(server.ref) == 2
    assert rig.broker.orders[server.ref].remaining == 1
    assert _tp_requests(rig) == []
    _safety(rig)
    server.final_phase = True
    server.final_alert_start = len(rig._b26_alerts)
    # TP1 is the first reached threshold, well below the next stage.
    rig.px('AAPL', 343.94)
    assert rig.store.position(pid)['tp1'] < 343.94 < rig.store.position(pid)['tp2']
    server.until(lambda: rig.store.position(pid)['status'] == 'CLOSED')
    server.ib.sleep(.1)
    rig.engine._drain(rig.clock.now())
    assert server.final_stage is Purpose.TP1
    assert server.cancel_wire is not None and server.cancel_at is not None
    assert server.final_tp_wire is not None and server.final_fill_at is not None
    assert server.cancel_receipts and all(event.filled == 2 and event.remaining == 1
                                         for event in server.cancel_receipts)
    assert server.final_tp_wire - server.cancel_at <= 1
    assert server.final_fill_at - server.cancel_at <= 5
    record_property('b28d_final_TP1_ACK_to_wire_seconds', server.final_tp_wire - server.cancel_at)
    [tp] = _tp_requests(rig)
    assert tp.purpose is Purpose.TP1 and tp.quantity == 1
    assert len(server.final_stage_attempts) == 1
    assert rig.store.position(pid)['qty_open'] == 0
    protection = rig.store.protection(pid)
    assert protection['state'] in TERMINAL
    assert protection['confirmed_remaining'] == protection['pending_max_remaining'] == 0
    assert protection['pending_qty'] is None and protection['gap_b_started_at'] is None
    assert rig.store.intent(server.ref)['status'] == 'CANCELLED'
    assert rig.store.intent(server.ref)['last_raw_status'] in {'Cancelled', 'ApiCancelled'}
    assert rig.store.intent_filled_qty(server.ref) == 2, 'the final share filled through TP1, not the STP'
    assert len(rig.broker.sent_protections) == 1
    assert sum(_opcode(message) == '4' for _, message in server.wire) == 1
    assert _p0(server) == [], _p0(server)
    assert sum(row['quantity'] if row['side'] == 'BUY' else -row['quantity']
               for row in rig.store.position_fills(pid)) == 0
    _assert_wire_limits(server.wire)
    _safety(rig)

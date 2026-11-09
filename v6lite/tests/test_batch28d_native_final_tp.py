"""B28d native final-share TP: actual cancel/End/execute and physical wire.

The B28c fixture creates yesterday's position and restarts its real SQLite.
Only the synthetic server/economic oracle is extended here. No SQL mutation,
engine proof/gate replacement, status_seen edit, TWS, Gateway or network.
"""
from __future__ import annotations

import json
from datetime import timedelta, timezone

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.models import OrderRequest, ProtectionRequest, Purpose, Side, StatusEvent
from v6lite.tests.test_batch24_pacing import _assert_wire_limits, _opcode
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network, _safety, _tp_requests
from v6lite.tests.test_batch28c_native_latency import NativeLatencyServer

TERMINAL = {'FILLED', 'CLOSED', 'CANCELLED', 'RETIRED'}


class NativeFinalTPServer(NativeLatencyServer):
    """Real SDK wire, synthetic server; broker truth is separate from Trades."""

    def __init__(self, rig, pid, harness, monkeypatch, request, *, race=False, failure=None):
        self.race = race
        self.failure = failure
        self.final_phase = False
        self.wall_shift = timedelta(0)
        self.cancel_wire = self.cancel_at = self.final_tp_wire = self.final_fill_at = None
        self.cancel_wall = None
        self.cancel_receipts = []
        self.final_attempts = []
        self.tp_wire_by_stage = {}
        self.fill_by_stage = {}
        self.mods = []
        self.query_timeline = []
        self.race_rows = []
        self.pending_final_fill = None
        self.old_cancel_events = []
        self.extra_execution = None
        self.final_alert_start = 0
        self.suppress_current_ends = False
        super().__init__(rig, pid, harness, monkeypatch, request)
        base_now = rig.clock.now
        monkeypatch.setattr(rig.clock, 'now', lambda: base_now() + self.wall_shift)
        # All execution timestamps in this replay are explicit UTC decoder
        # fields; yesterday's uncertain entry remains the B28b fixture's fact.
        self.ib.errorEvent += self.adapter._on_error
        self.deliver = rig.engine._on_urgent_broker_event
        self.adapter.set_urgent_handler(self.observe_native)

        def modify(req):
            _safety(rig, req)
            self.fake_modify(req)
            return self.adapter.modify_protection(req)

        native_submit = rig.broker.submit_limit
        def submit(req):
            if req.position_id == pid and req.purpose is Purpose.TP3:
                self.final_attempts.append((rig.clock.now(), req))
            return native_submit(req)

        fake_cancel = rig.broker.cancel
        def cancel(ref, **kwargs):
            _safety(rig)
            fake_cancel(ref, **kwargs)
            return self.adapter.cancel(ref, **kwargs)

        monkeypatch.setattr(rig.broker, 'modify_protection', modify)
        monkeypatch.setattr(rig.broker, 'submit_limit', submit)
        monkeypatch.setattr(rig.broker, 'cancel', cancel)
        request.addfinalizer(lambda: self.ib.errorEvent.disconnect(self.adapter._on_error))

    def observe_native(self, event):
        """Independent fact from our physical cancel + immutable native ACK."""
        if isinstance(event, StatusEvent) and event.intent_id == self.ref and event.raw_status == 'Cancelled':
            order = event.order_identity or event.terminal_identity
            row = self.rig.store.protection(self.pid)
            if (self.cancel_wire is not None and order is not None and
                    not event.connection_snapshot and event.error_code in {0, 202} and
                    self.rig.broker.orders[self.ref].status == 'Cancelled' and event.observed_at is not None and
                    event.socket_generation == self.adapter.query_generation and
                    self.cancel_wall <= event.observed_at <= self.rig.clock.now() and
                    order.intent_id == self.ref and order.broker_order_id == row['broker_order_id'] and
                    order.perm_id == row['perm_id'] and order.client_id == self.rig.cfg.client_id and
                    order.account == self.rig.cfg.account and order.con_id == row['con_id'] and
                    order.symbol == 'AAPL' and order.side == 'SELL' and order.order_type == 'STP' and
                    order.tif == 'GTC' and order.outside_rth is False and
                    event.filled == order.filled == 0 and event.remaining == order.remaining == 1):
                self.cancel_receipts.append(event)
        self.deliver(event)

    def send(self, packet):
        message = packet[4:].decode()
        fields = message.split('\0')
        code = fields[0]
        if code not in {'3', '4'}:
            return super().send(packet)
        at = self.loop.time()
        self.wire.append((at, message))
        if code == '4':
            assert self.final_phase, 'the only cancellation in this replay is our final-share STP'
            assert int(fields[2]) == self.rig.store.protection(self.pid)['broker_order_id']
            self.cancel_wire, self.cancel_wall = at, self.rig.clock.now()
            self.later(self.rtt, self.cancel_response)
            return
        assert fields[16] == 'SELL'
        qty, kind, ref = int(float(fields[17])), fields[18], fields[26]
        if kind == 'STP':
            self.mods.append((at, ref, qty, float(fields[20])))
            assert ref == self.ref
            self.later(self.rtt, self.accept_modify, ref)
            return
        assert kind == 'LMT' and fields[21] == 'DAY' and qty == 1
        intent = self.rig.store.intent(ref)
        assert intent is not None and intent['position_id'] == self.pid
        stage = Purpose(intent['purpose'])
        assert stage in {Purpose.TP1, Purpose.TP2, Purpose.TP3}
        assert stage not in self.tp_wire_by_stage, 'no duplicate stage packet'
        self.tp_wire_by_stage[stage] = (at, ref, float(fields[19]))
        if stage is Purpose.TP3:
            assert self.cancel_receipts, 'physical final TP requires an independently observed own native Cancelled'
            assert self.rig.broker.orders[self.ref].status == 'Cancelled'
            self.final_tp_wire = at
            if self.race:
                self.pending_final_fill = ref
            else:
                self.later(self.rtt, self.fill_stage, ref)
        else:
            self.later(self.rtt, self.fill_stage, ref)

    def accept_modify(self, ref):
        native = self.rig.broker.orders[ref]
        pending = self.rig.broker.protection_pending_modifications.pop(ref)
        native.req, native.status = pending, 'PreSubmitted'
        self.ack_at = self.loop.time()
        if pending.trigger_price != 338.62:
            self.emit_modify_ack(ref, pending)
        else:
            self.emit_order(ref)
        _safety(self.rig)

    def emit_modify_ack(self, ref, pending):
        # The preparation BE can be accepted while an all-open-orders request
        # is in flight. Its normal response already emits that accepted order.
        # Send the separate spontaneous price echo only after its native End,
        # so this synthetic server does not inject two identical STP rows into
        # one query. Observe the real SDK Future; never edit query/proof state.
        future = self.wrapper._futures.get('openOrders')
        if (not self.final_phase and pending.quantity == 2 and
                future is not None and not future.done()):
            self.later(.005, self.emit_modify_ack, ref, pending)
            return
        # Pure BE ack deliberately has no new orderStatus. Quantity-only
        # TP2 reductions still emit a genuine working status afterwards.
        from ib_insync import OrderState, StopOrder
        native = self.rig.broker.orders[ref]
        order = StopOrder('SELL', pending.quantity, pending.trigger_price, tif='GTC',
            orderId=native.broker_order_id, permId=native.broker_order_id + 50000,
            clientId=self.rig.cfg.client_id, account=self.rig.cfg.account,
            orderRef=ref, outsideRth=False)
        self.wrapper.lastTime = self.rig.clock.now()
        self.wrapper.openOrder(order.orderId, self.contract('AAPL'), order,
                               OrderState(status='PreSubmitted'))
        if pending.quantity == 1:
            self.wrapper.orderStatus(order.orderId, 'PreSubmitted', native.filled,
                native.remaining, 0., order.permId, 0, 0., order.clientId, '')

    def fill_stage(self, ref):
        rig = self.rig
        intent = rig.store.intent(ref)
        stage = Purpose(intent['purpose'])
        prices = {Purpose.TP1: 344.04, Purpose.TP2: 346.33, Purpose.TP3: 346.93}
        assert ref in rig.broker.orders, 'the real synchronous SDK write must have returned before broker RTT'
        fill = FakeBroker.fill(rig.broker, ref, qty=1, price=prices[stage],
            exec_id='b28d-native-' + str(stage).lower() + '.01.01', emit=False)
        if stage is Purpose.TP2:
            # The opening jump triggers TP2, then the book retreats below TP3
            # before the execution report arrives; this keeps stage setup real.
            rig.px('AAPL', 344.85)
        self.fill_by_stage[stage] = (self.loop.time(), fill)
        if stage is Purpose.TP3:
            self.final_fill_at = self.loop.time()
        self.harness.decode(fill, fill.time.astimezone(timezone.utc).strftime('%Y%m%d %H:%M:%S UTC'))
        self.emit_order(ref)
        _safety(rig)

    def _cancel_status(self):
        native = self.rig.broker.orders[self.ref]
        self.wrapper.lastTime = self.rig.clock.now()
        self.wrapper.orderStatus(native.broker_order_id, 'Cancelled', native.filled,
            native.remaining, 0., native.broker_order_id + 50000, 0, 0.,
            self.rig.cfg.client_id, '')

    def cancel_response(self):
        rig = self.rig
        native = rig.broker.orders[self.ref]
        if self.failure == 'cancel-rejected':
            # A real native cancel error leaves the original STP working.
            self.wrapper.error(native.broker_order_id, 10148,
                'synthetic cancel rejected: original order remains working', '')
            native.status = 'PreSubmitted'
            self.emit_order(self.ref)
            return
        if self.failure == 'cancel-timeout':
            # No terminal response is sent. Move only the test clock beyond
            # existing 60s ACK/evidence horizons; never mutate request proof.
            native.status = 'PreSubmitted'
            self.wall_shift += timedelta(seconds=61)
            return
        native.status = 'Cancelled'
        if self.failure == 'other-SELL':
            other = OrderRequest('v6l-b28d-orphan-sell', 'AAPL', Side.SELL, 1, 346.5, Purpose.TP3)
            FakeBroker.submit_limit(rig.broker, other)
            self.emit_order(other.intent_id)
        elif self.failure == 'unknown-execution':
            # Unknown BUY preserves safe native capacity while contradicting
            # the engine's last-share ledger; it must not be excused as TP3.
            other = OrderRequest('external-b28d-buy', 'AAPL', Side.BUY, 1, 346.5, Purpose.ENTRY)
            FakeBroker.submit_limit(rig.broker, other)
            self.extra_execution = FakeBroker.fill(rig.broker, other.intent_id, qty=1,
                price=346.5, exec_id='b28d-unattributed.01.01', emit=False)
            fill = self.extra_execution
            self.harness.decode(fill, fill.time.astimezone(timezone.utc).strftime('%Y%m%d %H:%M:%S UTC'))
        elif self.failure == 'old-socket':
            captured = []
            self.adapter.set_urgent_handler(captured.append)
            try:
                self._cancel_status()
            finally:
                self.adapter.set_urgent_handler(self.observe_native)
            assert any(isinstance(event, StatusEvent) and event.raw_status == 'Cancelled' for event in captured)
            self.old_cancel_events = captured
            self.adapter._reset_channels()
            self.adapter._ensure_channel_hooks()
            rig.broker.query_generation = self.adapter.query_generation
            self.suppress_current_ends = True
            for event in captured:
                self.observe_native(event)
            return
        self.cancel_at = self.loop.time()
        self._cancel_status()
        if self.failure not in {'other-SELL'}:
            _safety(rig)

    def respond(self, code, fields):
        if self.suppress_current_ends:
            return  # a previous socket's cancellation cannot replace a new End
        if code == '61' and self.pending_final_fill is not None:
            # This is one real request with an old P=1 row, a genuine execution
            # callback, then its own positionEnd. The returned SDK Future keeps
            # P=1 while the ledger and subsequent executions now say P=0.
            self.wrapper.lastTime = self.rig.clock.now()
            positions = FakeBroker.positions_fresh(self.rig.broker)
            assert len(positions) == 1 and positions[0].quantity == 1
            for position in positions:
                self.wrapper.position(position.account, self.contract(position.symbol),
                                      position.quantity, position.avg_cost)
            ref, self.pending_final_fill = self.pending_final_fill, None
            started = self.loop.time()
            self.fill_stage(ref)
            assert self.rig.store.position(self.pid)['status'] == 'CLOSED'
            self.wrapper.positionEnd()
            ended = self.loop.time()
            self.ends.append((ended, 'positions', None))
            self.race_rows.append((started, ended, positions[0].quantity,
                                   self.rig.store.position(self.pid)['qty_open']))
            return
        return super().respond(code, fields)

    def step(self):
        self.ib.sleep(.01)
        engine = self.rig.engine
        engine._drain(self.rig.clock.now())
        engine._protection_timers(self.rig.clock.now())
        engine._evaluate_exits(self.rig.clock.now())
        engine._run_queue()
        engine.process_protection_priority()
        # Ordinary active-protection phase outside all SDK callbacks, including
        # the post-send in-flight snapshot used by the mixed-P/End replay.
        engine._protection_progress(self.rig.clock.now(), phase='active')
        engine._drain(self.rig.clock.now())

    def until(self, predicate, *, timeout=28):
        deadline = self.loop.time() + timeout
        while not predicate() and self.loop.time() < deadline:
            self.step()
        assert predicate(), ('native replay deadline', self.rig.store.position(self.pid)['qty_open'],
                             dict(self.rig.store.protection(self.pid)), self.requests[-15:])

    def last_share(self):
        rig = self.rig
        rig.px('AAPL', 343.94)
        self.until(lambda: rig.store.position(self.pid)['tp1_done'] and
            rig.store.protection(self.pid)['state'] == 'WORKING' and
            rig.store.protection(self.pid)['trigger_price'] == pytest.approx(342.77))
        assert rig.store.position(self.pid)['qty_open'] == 2
        assert Purpose.TP1 in self.fill_by_stage
        assert any(qty == 2 and price == pytest.approx(342.77) for _, _, qty, price in self.mods)
        # TP1 and its BE are yesterday's actual economic facts; today's TP2
        # and TP3 must use fresh current-day scoped queries, not yesterday's.
        self.wall_shift += timedelta(days=1)
        rig.px('AAPL', 346.23)
        self.until(lambda: rig.store.position(self.pid)['tp2_done'] and
            rig.store.position(self.pid)['qty_open'] == 1 and
            rig.store.protection(self.pid)['state'] == 'WORKING')
        assert Purpose.TP2 in self.fill_by_stage and Purpose.TP3 not in self.tp_wire_by_stage
        native = rig.broker.orders[self.ref]
        assert native.remaining == 1 and native.status == 'PreSubmitted'
        assert native.req.trigger_price == pytest.approx(342.77)
        _safety(rig)

    def final(self):
        self.final_phase = True
        self.final_alert_start = len(self.rig._b26_alerts)
        self.rig.px('AAPL', 347.04)
        self.until(lambda: self.cancel_wire is not None)
        if self.failure:
            # Allow native callback + a full fallback attempt to demonstrate
            # that no stale pre-cancel authority can submit final TP.
            end = self.loop.time() + 7
            while self.loop.time() < end:
                self.step()
        else:
            self.until(lambda: self.rig.store.position(self.pid)['status'] == 'CLOSED')
        self.ib.sleep(.1)
        self.rig.engine._drain(self.rig.clock.now())


def _gaps(server):
    return [json.loads(row['payload']) for row in server.rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='PROTECT_GAP'")
        if json.loads(row['payload']).get('gap_kind') == 'B' and
        json.loads(row['payload']).get('tp_intent_id') == server.tp_wire_by_stage.get(Purpose.TP3, (None, None))[1]]


def _p0(server):
    return [text for level, text in server.rig._b26_alerts[server.final_alert_start:] if level == 'P0']


def _server(overnight_run, monkeypatch, request, **kwargs):
    rig, pid, harness = overnight_run()
    server = NativeFinalTPServer(rig, pid, harness, monkeypatch, request, **kwargs)
    server.last_share()
    return server


def test_B28d_real_final_cancel_ACK_to_actual_TP3_wire_within_one_second(
        overnight_run, monkeypatch, request, record_property):
    server = _server(overnight_run, monkeypatch, request)
    server.final()
    assert server.cancel_at is not None and server.final_tp_wire is not None and server.final_fill_at is not None
    delay = server.final_tp_wire - server.cancel_at
    record_property('b28d_cancel_ACK_to_TP3_wire_seconds', delay)
    record_property('b28d_final_B_wire_seconds', server.final_fill_at - server.cancel_at)
    assert delay <= 1., ('Cancelled -> actual DAY LMT conn.sendMsg', delay, server.requests)
    assert server.final_fill_at - server.cancel_at <= 5
    gaps = _gaps(server)
    assert len(gaps) == 1 and 0 < gaps[0]['secs'] <= 5, gaps
    assert len(server.cancel_receipts) >= 1
    assert [req.purpose for req in _tp_requests(server.rig) if req.position_id == server.pid] == [Purpose.TP1, Purpose.TP2, Purpose.TP3]
    _assert_wire_limits(server.wire)
    _safety(server.rig)


@pytest.mark.parametrize('contract', ['no-P0', 'terminal-protection'])
def test_B28d_final_TP_fill_between_native_position_row_and_End_closes_safely(
        overnight_run, monkeypatch, request, contract):
    server = _server(overnight_run, monkeypatch, request, race=True)
    server.final()
    rig, pid = server.rig, server.pid
    assert len(server.race_rows) == 1 and server.race_rows[0][2:] == (1, 0)
    assert server.race_rows[0][0] <= server.final_fill_at <= server.race_rows[0][1]
    pos, protection = rig.store.position(pid), rig.store.protection(pid)
    assert pos['status'] == 'CLOSED' and pos['qty_open'] == 0
    if contract == 'no-P0':
        assert _p0(server) == [], _p0(server)
    else:
        assert protection['state'] in TERMINAL, dict(protection)
    assert protection['confirmed_remaining'] == protection['pending_max_remaining'] == 0
    assert protection['pending_qty'] is None and protection['gap_b_started_at'] is None
    assert rig.store.intent_filled_qty(server.ref) == 0, 'final TP must not pretend the cancelled STP filled'
    assert rig.store.intent(server.ref)['last_raw_status'] == 'Cancelled'
    assert rig.broker.orders[server.ref].status == 'Cancelled'
    assert len(rig.broker.sent_protections) == 1, 'no replacement STP after flat'
    assert sum(_opcode(message) == '4' for _, message in server.wire) == 1
    _assert_wire_limits(server.wire)
    _safety(rig)


@pytest.mark.parametrize('failure', ['cancel-rejected', 'cancel-timeout', 'other-SELL',
                                   'unknown-execution', 'old-socket'])
def test_B28d_final_TP_never_uses_rejected_missing_or_invalid_cancel_authority(
        overnight_run, monkeypatch, request, failure):
    server = _server(overnight_run, monkeypatch, request, failure=failure)
    server.final()
    assert server.cancel_wire is not None
    assert server.final_tp_wire is None and server.final_attempts == [], (failure, server.final_attempts, server.wire)
    assert Purpose.TP3 not in server.tp_wire_by_stage
    if failure in {'other-SELL', 'unknown-execution'}:
        assert _p0(server), (failure, server.rig._b26_alerts[server.final_alert_start:])
    if failure in {'cancel-rejected', 'cancel-timeout'}:
        original = server.rig.broker.orders[server.ref]
        assert original.status == 'PreSubmitted' and original.remaining == 1
        assert len(server.rig.broker.sent_protections) == 1
        _safety(server.rig)
    if failure == 'old-socket':
        assert server.old_cancel_events and not server.cancel_receipts
        assert not any(kind == 'positions' for at, kind, _ in server.ends if at > server.cancel_wire)
    _assert_wire_limits(server.wire)

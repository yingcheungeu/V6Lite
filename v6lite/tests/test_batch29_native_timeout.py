"""B29 real SDK Paper timeout replay, entirely offline.

The B28b fixture books yesterday's three shares, stops/reopens real SQLite,
and takes over its original STP. The installed SDK sends actual request and
order packets to a synthetic server; only broker economics, quotes and the
server's native callbacks are scripted. Production gates/proof are untouched.
"""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_UP
from types import SimpleNamespace
from datetime import timezone

import pytest

from v6lite import __main__ as cli
from v6lite.broker.fake import FakeBroker
from v6lite.config import TestConfig as LiteTestConfig
from v6lite.exits import sell_limit
from v6lite.models import Purpose
from v6lite.tests.helpers import Rig
from v6lite.tests import test_batch28b_overnight_refresh as preparation
from v6lite.tests.test_batch26_overnight_evidence import _watch_boundaries
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network, _safety, _tp_requests
from v6lite.tests.test_batch28c_native_latency import NativeLatencyServer
from v6lite.tests.test_batch24_pacing import _assert_wire_limits


class NativeTimeoutServer(NativeLatencyServer):
    """No synthetic fill until the actual wire test LMT has timed out."""

    def __init__(self, rig, pid, harness, monkeypatch, request, *, late_fill=False):
        self.late_fill = late_fill
        self.tp_packets = []
        self.cancel_wire = self.cancel_at = self.late_fill_at = None
        self.restore_wire = self.restore_ack_at = None
        self.mods = []
        self.actual_fills = []
        self.held_ref = None
        self.cancel_ref = None
        self.alert_start = len(rig._b26_alerts)
        self.fill_ordinary = False
        super().__init__(rig, pid, harness, monkeypatch, request)

        # Extend the adapter to the real SDK cancel path, retaining the
        # independent FakeBroker cancellation-request/economic record.
        economic_cancel = rig.broker.cancel
        def cancel(ref, **kwargs):
            _safety(rig)
            economic_cancel(ref, **kwargs)
            return self.adapter.cancel(ref, **kwargs)
        monkeypatch.setattr(rig.broker, 'cancel', cancel)

        # NativeLatencyServer replaces query transports. Re-install only
        # observational wrappers so the independent capacity oracle sees the
        # actual completed post-cancel rows, not a synthetic release flag.
        _watch_boundaries(rig, monkeypatch)

    def send(self, packet):
        message = packet[4:].decode()
        fields = message.split('\0')
        code = fields[0]
        if code not in {'3', '4'}:
            return super().send(packet)
        at = self.loop.time()
        self.wire.append((at, message))
        if code == '4':
            assert self.cancel_wire is None, 'one timeout must request exactly one TP cancellation'
            assert self.held_ref is not None
            native = self.rig.broker.orders[self.held_ref]
            assert int(fields[2]) == native.broker_order_id
            self.cancel_wire, self.cancel_ref = at, self.held_ref
            # Keep the mark below TP1 while the original stop is restored;
            # a later test step supplies the next genuine TP1 signal.
            self.rig.px('AAPL', 342.90)
            if self.late_fill:
                self.later(self.rtt / 2, self.execute_late_then_cancel, self.held_ref)
            else:
                self.later(self.rtt, self.cancel_response, self.held_ref)
            return
        assert fields[16] == 'SELL'
        kind, qty, ref = fields[18], int(float(fields[17])), fields[26]
        assert kind in {'STP', 'LMT'}, 'MKT/extra order kind is forbidden'
        if kind == 'STP':
            assert ref == self.ref and fields[21] == 'GTC', 'restore must keep the same GTC identity'
            price = float(fields[20])
            self.mods.append((at, ref, qty, price))
            if qty == 3:
                assert self.cancel_at is not None, 'GTC cannot increase before native TP Cancelled'
                self.restore_wire = at
            self.later(self.rtt, self.accept_modify, ref)
            return
        assert fields[21] == 'DAY' and qty == 1
        intent = self.rig.store.intent(ref)
        assert intent is not None and intent['purpose'] == str(Purpose.TP1)
        quote = self.rig.broker.quote('AAPL')
        row = self.rig.store.protection(self.pid)
        assert row['confirmed_remaining'] == 2 and row['pending_qty'] is None
        assert self.ack_at is not None, 'native decrease confirmation must precede TP wire'
        self.tp_packets.append({
            'at': at, 'ref': ref, 'order_id': int(fields[1]),
            'price': float(fields[19]), 'bid': quote.bid, 'ask': quote.ask,
            'quantity': qty, 'type': kind, 'tif': fields[21],
        })
        if self.held_ref is None:
            self.held_ref = ref
            self.tp_wire = at
            self.later(self.rtt, self.accept_tp, ref)
        else:
            assert self.fill_ordinary, 'no retry before protection restore and a new signal'
            assert len(self.tp_packets) == 2
            self.later(self.rtt, self.execute_tp, ref, False)

    def execute_late_then_cancel(self, ref):
        # Independent overdue timers can share one Windows event-loop clock
        # sample. Schedule the Cancelled reply after actual late-fill delivery,
        # preserving the original half-RTT separation and strict ordering test.
        self.execute_tp(ref, True)
        self.later(self.rtt / 2, self.cancel_response, ref)

    def accept_modify(self, ref):
        native = self.rig.broker.orders[ref]
        pending = self.rig.broker.protection_pending_modifications.pop(ref)
        native.req, native.status = pending, 'PreSubmitted'
        at = self.loop.time()
        if pending.quantity == 2 and pending.trigger_price == 338.62 and self.ack_at is None:
            self.ack_at = at
            self.ack_released = True
        if pending.quantity == 3:
            self.restore_ack_at = at
        self.emit_when_idle(ref)
        _safety(self.rig)

    def emit_when_idle(self, ref):
        future = self.wrapper._futures.get('openOrders')
        if future is not None and not future.done():
            self.later(.005, self.emit_when_idle, ref)
            return
        self.emit_order(ref)

    def accept_tp(self, ref):
        self.rig.broker.orders[ref].status = 'Submitted'
        future = self.wrapper._futures.get('openOrders')
        if future is not None and not future.done():
            self.later(.005, self.accept_tp, ref)
            return
        self.emit_order(ref)
        _safety(self.rig)

    def cancel_response(self, ref):
        native = self.rig.broker.orders[ref]
        native.status = 'Cancelled'
        self.cancel_at = self.loop.time()
        self.wrapper.lastTime = self.rig.clock.now()
        self.wrapper.orderStatus(native.broker_order_id, 'Cancelled', native.filled,
            native.remaining, 0., native.broker_order_id + 50_000, 0, 0.,
            self.rig.cfg.client_id, '')
        _safety(self.rig)

    def execute_tp(self, ref, late):
        native = self.rig.broker.orders[ref]
        assert native.remaining == 1
        price = native.req.limit_price
        # A late execution is broker truth after our cancellation request.
        # The execution decoder/ledger, rather than a pre-edited position,
        # must reduce P and determine the correct remaining protection.
        fill = FakeBroker.fill(self.rig.broker, ref, qty=1, price=price,
            exec_id='b29-native-' + ('late' if late else 'normal') + '.01.01', emit=False)
        at = self.loop.time()
        self.actual_fills.append((at, fill))
        if late:
            assert self.cancel_wire is not None and at > self.cancel_wire
            self.late_fill_at = at
        self.harness.decode(fill, fill.time.astimezone(timezone.utc).strftime('%Y%m%d %H:%M:%S UTC'))
        self.emit_order(ref)
        _safety(self.rig)

    def step(self):
        self.ib.sleep(.01)
        # Exercise the real steady scheduler, including its evidence budget,
        # ready-proof decision ordering and stage retry cadence.
        self.rig.refresh_quotes()
        self.rig.engine.tick()
        _safety(self.rig)

    def until(self, predicate, *, timeout=35):
        deadline = self.loop.time() + timeout
        while not predicate() and self.loop.time() < deadline:
            self.step()
        assert predicate(), ('native timeout replay deadline', dict(self.rig.store.protection(self.pid)),
                             self.tp_packets, self.mods, self.requests[-12:])

    def request(self, monkeypatch):
        before = len(self.wire)
        with monkeypatch.context() as patch:
            patch.setattr(cli, '_now', self.rig.clock.now)
            # Real production command handler, using the actual fixture cfg.
            # The CLI parser/config guards are separately covered; no control
            # request or ready epoch is fabricated by this replay.
            cli.cmd_test_tp_timeout(SimpleNamespace(symbol='AAPL', operator='John'), self.rig.cfg)
        assert len(self.wire) == before, 'the control command itself cannot perform broker operations'
        assert self.rig.store.control('test_tp_nonmarketable_once:AAPL') is not None

    def start_injected_tp(self):
        self.rig.px('AAPL', 343.94)
        self.until(lambda: self.held_ref is not None)
        [packet] = self.tp_packets
        expected = float((Decimal(str(packet['ask'])) * Decimal('1.01')).quantize(
            Decimal('.01'), rounding=ROUND_UP))
        assert packet['price'] == expected > packet['ask']
        assert packet['at'] >= self.ack_at
        assert self.rig.broker.orders[self.ref].remaining == 2
        assert self.rig.store.control('test_tp_nonmarketable_once:AAPL') is None
        completed = {kind for at, kind, _ in self.ends if at <= self.reduction_time()}
        assert {'openOrders', 'positions', 'executions', 'account'} <= completed

    def reduction_time(self):
        return next(at for at, _, qty, price in self.mods if qty == 2 and price == 338.62)

    def alerts(self):
        return self.rig._b26_alerts[self.alert_start:]

    def assert_success_alerts(self):
        assert not [(level, text) for level, text in self.alerts() if level in {'P1', 'P0'}], self.alerts()
        notices = [text for level, text in self.alerts() if level == 'INFO' and
                   '测试' in text and '止盈超时' in text and '撤单' in text and '恢复保护' in text]
        assert len(notices) == 1, self.alerts()

    def audits(self):
        return [json.loads(row['payload']) for row in self.rig.store.conn.execute(
            "SELECT payload FROM audit WHERE kind='TEST_TP_TIMEOUT_RESULT' ORDER BY id")]

    def assert_audit(self):
        rows = self.audits()
        assert rows
        phases = {value.get('phase') for value in rows}
        assert {'claimed', 'send', 'cancel_request', 'cancel_confirm', 'gap_close'} <= phases, rows
        if not self.late_fill:
            assert 'restore_confirm' in phases, rows
        assert any(value.get('outcome') == 'claimed' or value.get('phase') == 'claimed' for value in rows)
        priced = [value for value in rows if value.get('ask') == self.tp_packets[0]['ask']]
        assert any(value.get('limit_price', value.get('sent_price')) == self.tp_packets[0]['price']
                   for value in priced), rows
        assert any(value.get('broker_order_id', value.get('orderId')) == self.tp_packets[0]['order_id']
                   for value in rows), rows
        assert any(value.get('S', value.get('restored_S')) == (2 if self.late_fill else 3)
                   and value.get('gap_b_seconds', value.get('gap_b_secs')) is not None for value in rows), rows
        _assert_wire_limits(self.wire)
        assert len(self.rig.broker.sent_protections) == 1
        assert self.rig.broker.cancels == [self.held_ref]


@pytest.fixture
def native_timeout(overnight_run, monkeypatch, request):
    # Configure the actual test engine before it starts. Do not swap an
    # already-running config, grant a ready epoch, or touch SQL state.
    def paper_test_rig(directory, **kwargs):
        kwargs.update(client_id=132, state_dir=str(directory / 'paper-test'),
            test=LiteTestConfig(enabled=True, qty=3, stop_mult=.3, tp1_mult=.3, tp2_mult=.5, tp3_mult=.8))
        return Rig(directory, **kwargs)
    monkeypatch.setattr(preparation, 'Rig', paper_test_rig)

    def make(*, late_fill=False):
        rig, pid, harness = overnight_run()
        server = NativeTimeoutServer(rig, pid, harness, monkeypatch, request, late_fill=late_fill)
        return server
    return make


def test_B29_native_test_request_timeout_restores_same_GTC_then_retries_normal_TP1(
        native_timeout, monkeypatch, record_property):
    server = native_timeout()
    rig, pid = server.rig, server.pid
    server.request(monkeypatch)
    server.start_injected_tp()
    server.until(lambda: server.restore_ack_at is not None and
        rig.store.protection(pid)['state'] == 'WORKING' and
        rig.store.protection(pid)['gap_b_started_at'] is None and
        rig.store.protection(pid)['confirmed_remaining'] == 3)
    assert server.cancel_wire - server.ack_at >= rig.cfg.protection.gap_b_cancel_seconds
    assert server.cancel_at <= server.restore_wire <= server.restore_ack_at
    assert rig.store.position(pid)['qty_open'] == 3
    assert rig.store.position(pid)['tp1_done'] == 0
    stage = rig.store.stage(pid, Purpose.TP1)
    assert stage is None or stage['status'] == 'PENDING'
    assert not server.actual_fills
    assert len(server.tp_packets) == 1
    assert rig.store.protection(pid)['intent_id'] == server.ref
    server.fill_ordinary = True
    rig.px('AAPL', 343.94)
    server.until(lambda: rig.store.position(pid)['tp1_done'] and
        rig.store.position(pid)['qty_open'] == 2 and
        rig.store.protection(pid)['state'] == 'WORKING' and
        rig.store.protection(pid)['pending_qty'] is None)
    assert len(server.tp_packets) == 2 and len(server.actual_fills) == 1
    normal = server.tp_packets[1]
    assert normal['price'] == sell_limit(normal['bid'], rig.cfg.exit_limit_band_pct)
    assert normal['price'] <= normal['bid']
    assert rig.broker.orders[server.ref].remaining == 2
    server.assert_success_alerts()
    server.assert_audit()
    record_property('b29_timeout_cancel_after_ACK_seconds', server.cancel_wire - server.ack_at)
    record_property('b29_cancel_to_same_GTC_restore_seconds', server.restore_ack_at - server.cancel_at)


def test_B29_native_late_fill_after_timeout_cancel_keeps_two_share_GTC_and_books_once(
        native_timeout, monkeypatch, record_property):
    server = native_timeout(late_fill=True)
    rig, pid = server.rig, server.pid
    server.request(monkeypatch)
    server.start_injected_tp()
    server.until(lambda: server.cancel_at is not None and server.late_fill_at is not None and
        rig.store.position(pid)['qty_open'] == 2 and rig.store.position(pid)['tp1_done'] and
        rig.store.protection(pid)['state'] == 'WORKING' and
        rig.store.protection(pid)['confirmed_remaining'] == 2 and
        rig.store.protection(pid)['pending_qty'] is None and
        rig.store.protection(pid)['gap_b_started_at'] is None)
    assert server.cancel_wire < server.late_fill_at < server.cancel_at
    assert len(server.tp_packets) == len(server.actual_fills) == 1
    assert server.restore_wire is None, 'a fully filled one-share TP leaves P=2; never increase GTC to 3'
    assert rig.broker.orders[server.ref].remaining == 2
    fill = server.actual_fills[0][1]
    rows = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (fill.exec_id,)).fetchall()
    assert len(rows) == 1 and rows[0]['quantity'] == 1
    assert rig.store.intent_filled_qty(server.held_ref) == 1
    assert rig.store.protection(pid)['intent_id'] == server.ref
    server.assert_success_alerts()
    server.assert_audit()
    record_property('b29_cancel_request_to_late_fill_seconds', server.late_fill_at - server.cancel_wire)

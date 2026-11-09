"""Independent B28d cancel-ACK provenance and quote/wire boundary specs.

Draft only: reuse the frozen native server, actual SDK callbacks/Futures and
conn.sendMsg; no SQL, generated authority, edited proof, or production mocks.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.models import Purpose, StatusEvent
from v6lite.tests.test_batch24_pacing import _assert_wire_limits, _opcode
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network, _safety
from v6lite.tests.test_batch28d_native_final_tp import NativeFinalTPServer, _p0, TERMINAL


def _prepared(overnight_run, monkeypatch, request, server_class, **kwargs):
    rig, pid, harness = overnight_run()
    server = server_class(rig, pid, harness, monkeypatch, request, **kwargs)
    server.last_share()
    return server


class QuerySnapshotCancelServer(NativeFinalTPServer):
    """A same-socket Cancelled arrives inside a real outstanding order query."""
    def __init__(self, *args, **kwargs):
        self.owned_query = None
        self.query_cancel_events = []
        super().__init__(*args, failure='query-snapshot-cancel', **kwargs)

    def cancel_response(self):
        # Begin a genuine asynchronous native request; a synchronous IB query
        # inside the cancellation callback would itself be an invalid fixture.
        self.owned_query = self.wrapper.startReq('openOrders')
        self.client.reqAllOpenOrders()

    def respond(self, code, fields):
        if self.final_phase and self.owned_query is not None and code in {'5', '16'}:
            native = self.rig.broker.orders[self.ref]
            native.status = 'Cancelled'
            self.cancel_at = self.loop.time()
            assert not self.owned_query.done()
            self._cancel_status()
            self.suppress_current_ends = True
            return  # no corresponding End, no complete absence authority
        return super().respond(code, fields)

    def observe_native(self, event):
        if (isinstance(event, StatusEvent) and event.intent_id == self.ref and
                event.raw_status == 'Cancelled' and self.owned_query is not None):
            self.query_cancel_events.append(event)
        return super().observe_native(event)


def test_B28d_query_snapshot_Cancelled_never_becomes_a_fast_cancel_receipt(
        overnight_run, monkeypatch, request):
    server = _prepared(overnight_run, monkeypatch, request, QuerySnapshotCancelServer)
    server.final()
    assert server.cancel_wire is not None and server.query_cancel_events
    assert all(not event.connection_snapshot and getattr(event, 'query_snapshot', False)
               for event in server.query_cancel_events), server.query_cancel_events
    assert any(code in {'5', '16'} and at > server.cancel_wire
               for at, code, _ in server.requests)
    assert not any(kind == 'openOrders' and at > server.cancel_wire for at, kind, _ in server.ends)
    assert server.final_tp_wire is None and server.final_attempts == []
    assert Purpose.TP3 not in server.tp_wire_by_stage
    _assert_wire_limits(server.wire)


class LateCancelServer(NativeFinalTPServer):
    def __init__(self, *args, **kwargs):
        self.delayed_once = False
        super().__init__(*args, **kwargs)

    def cancel_response(self):
        if not self.delayed_once:
            self.delayed_once = True
            self.later(2.15, self.release_late_cancel)

    def release_late_cancel(self):
        # The server kept the original stop working during the whole delay.
        assert self.rig.broker.orders[self.ref].status == 'PreSubmitted'
        return super().cancel_response()


def test_B28d_late_cancel_ACK_requires_new_completed_proof_before_final_TP(
        overnight_run, monkeypatch, request):
    server = _prepared(overnight_run, monkeypatch, request, LateCancelServer)
    server.final()
    assert server.cancel_at - server.cancel_wire > 2
    assert server.final_tp_wire is not None and server.final_tp_wire >= server.cancel_at
    completed = {kind for at, kind, _ in server.ends
                 if server.cancel_at < at <= server.final_tp_wire}
    assert {'openOrders', 'positions', 'executions', 'account'} <= completed, server.ends
    assert len(server.final_attempts) == 1
    assert server.rig.store.position(server.pid)['status'] == 'CLOSED'
    assert server.rig.store.protection(server.pid)['state'] in TERMINAL
    assert _p0(server) == [], _p0(server)
    _assert_wire_limits(server.wire)
    _safety(server.rig)


class DuplicateCancelServer(NativeFinalTPServer):
    def __init__(self, *args, **kwargs):
        self.duplicates_sent = []
        super().__init__(*args, **kwargs)

    def cancel_response(self):
        super().cancel_response()
        self.later(.01, self.repeat_terminal, 'first-duplicate')
        self.later(.11, self.repeat_terminal, 'after-fill-duplicate')

    def repeat_terminal(self, marker):
        native = self.rig.broker.orders[self.ref]
        self.wrapper.lastTime = self.rig.clock.now()
        self.duplicates_sent.append((marker, self.loop.time()))
        # whyHeld changes only to force SDK emission of an otherwise identical
        # orderStatus. All hard identity, quantities and cancellation facts
        # remain exact duplicates; no StatusEvent is fabricated by the test.
        self.wrapper.orderStatus(native.broker_order_id, 'Cancelled', native.filled,
            native.remaining, 0., native.broker_order_id + 50000, 0, 0.,
            self.rig.cfg.client_id, marker)


def test_B28d_duplicate_native_cancel_ACK_before_and_after_fill_is_idempotent(
        overnight_run, monkeypatch, request):
    server = _prepared(overnight_run, monkeypatch, request, DuplicateCancelServer)
    server.final()
    assert len(server.duplicates_sent) == 2
    assert len(server.cancel_receipts) >= 3, 'the SDK must actually emit the duplicate native callbacks'
    assert len(server.final_attempts) == 1 and server.final_tp_wire is not None
    assert sum(_opcode(message) == '4' for _, message in server.wire) == 1
    assert _p0(server) == [], _p0(server)
    assert server.rig.store.protection(server.pid)['state'] in TERMINAL
    _assert_wire_limits(server.wire)
    _safety(server.rig)


class OldQuoteCancelServer(NativeFinalTPServer):
    def __init__(self, *args, congested=False, **kwargs):
        self.congested = congested
        self.freeze_book = False
        self.book_at = self.book_ts = self.congestion_at = None
        super().__init__(*args, failure='final-old-book', **kwargs)

    def refresh_book(self):
        if self.stopped or self.freeze_book:
            return
        self.rig.refresh_quotes()
        self.later(.03, self.refresh_book)

    def cancel_response(self):
        self.freeze_book = True
        self.book_at = self.rig.clock.now()
        self.book_ts = self.book_at - timedelta(seconds=2.9 if self.congested else 3.01)
        for symbol, quote in list(self.rig.broker.quotes.items()):
            self.rig.broker.quotes[symbol] = replace(quote, ts=self.book_ts)
        if self.congested:
            # Fill the real connection window using actual low-level native
            # requests. No pacer counters, histories or send-ready APIs change.
            for _ in range(10):
                self.client.reqCurrentTime()
            self.congestion_at = self.loop.time()
            recent = [at for at, _ in self.wire if self.congestion_at - 1 < at <= self.congestion_at]
            assert len(recent) == 10, recent
        return super().cancel_response()


@pytest.mark.parametrize('congested', [False, True], ids=['already-stale', '2.9s-book-congested-wire'])
def test_B28d_final_cancel_does_not_bypass_quote_or_physical_wire_deadline(
        overnight_run, monkeypatch, request, congested):
    server = _prepared(overnight_run, monkeypatch, request, OldQuoteCancelServer, congested=congested)
    server.final()
    assert server.cancel_at is not None and server.cancel_receipts
    assert server.book_at is not None
    if congested:
        assert server.congestion_at is not None
        assert (server.book_at - server.book_ts).total_seconds() == pytest.approx(2.9)
    # Drain the actual client scheduler beyond the expired quote deadline.
    # An empty immediate wire list or a local placeOrder return is insufficient.
    server.ib.sleep(1.2)
    server.rig.engine._drain(server.rig.clock.now())
    assert (server.rig.clock.now() - server.book_ts).total_seconds() > 3
    assert server.final_tp_wire is None and Purpose.TP3 not in server.tp_wire_by_stage
    assert server.rig.store.position(server.pid)['qty_open'] == 1
    row = server.rig.store.protection(server.pid)
    assert not (row['state'] == 'WORKING' and row['confirmed_remaining'] == 0 and
                row['pending_max_remaining'] == 0), dict(row)
    _assert_wire_limits(server.wire)
    _safety(server.rig)

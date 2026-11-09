"""A confirmed-cancelled original STP cannot revive silently during restore.

The native Cancelled really arrives first. A stale quote makes the final TP
definitely unsent. Only then does the synthetic broker contradict its own
cancel receipt, either spontaneously or inside a complete own order query.
No SQL, authority-cache edits, gate replacement or synthetic StatusEvents.
"""
from __future__ import annotations

import json
from datetime import datetime

from v6lite.models import Purpose, StatusEvent
from v6lite.tests.test_batch24_pacing import _assert_wire_limits, _opcode
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network, _safety
from v6lite.tests.test_batch28d_cancel_edges import OldQuoteCancelServer
from v6lite.tests.test_batch28d_native_final_tp import _p0


class RevivedCancelledStopServer(OldQuoteCancelServer):
    def __init__(self, rig, pid, harness, monkeypatch, request, *, spontaneous):
        self.spontaneous = spontaneous
        self.revival_at = self.confirmed_at = None
        self.after_callback = None
        self.revival_events = []
        self.modification_attempts = []
        self.unsent_state = None
        super().__init__(rig, pid, harness, monkeypatch, request, congested=False)
        modify = rig.broker.modify_protection
        def observe_modify(req):
            if self.revival_at is not None:
                self.modification_attempts.append((self.loop.time(), req))
            return modify(req)
        monkeypatch.setattr(rig.broker, 'modify_protection', observe_modify)

    def cancel_response(self):
        super().cancel_response()
        rig = self.rig
        row = rig.store.protection(self.pid)
        fact = json.loads(rig.store.control('final_tp_cancel:' + self.pid))
        assert fact.get('confirmed_at') and fact.get('tp_intent_id') is None
        assert row['state'] == 'TP_NEEDS_RESTORE' and row['tp_intent_id'] is None
        assert self.final_attempts == [] and self.final_tp_wire is None
        assert rig.store.intent(self.ref)['status'] == 'CANCELLED'
        assert rig.broker.orders[self.ref].status == 'Cancelled'
        self.confirmed_at = datetime.fromisoformat(fact['confirmed_at'])
        self.unsent_state = (row['state'], row['tp_intent_id'])
        # Economic/native broker input only: the server now reports that the
        # very same ID, which it had just cancelled, remains working.
        rig.broker.orders[self.ref].status = 'PreSubmitted'
        self.revival_at = self.loop.time()
        if self.spontaneous:
            # The test only pumps callbacks until this fires. A real 40ms
            # interval avoids Windows timestamp granularity accidentally
            # turning this into a same-observation-time provenance test.
            self.later(.04, self.emit_spontaneous_revival)
        # Query-only mode emits nothing here. The normal native responder
        # exposes the revived row only in the next reqAllOpenOrders result.

    def emit_spontaneous_revival(self):
        assert not self.wrapper._futures, 'emit outside all synchronous own queries'
        assert self.rig.clock.now() > self.confirmed_at
        self.emit_order(self.ref)
        current = self.rig.store.protection(self.pid)
        original = self.rig.store.intent(self.ref)
        self.after_callback = {
            'state': current['state'], 'hold': original['hold_reason'],
            'p0': list(_p0(self)), 'modifications': list(self.modification_attempts),
            'query_inflight': bool(self.wrapper._futures),
        }

    def observe_native(self, event):
        if (self.revival_at is not None and isinstance(event, StatusEvent) and
                event.intent_id == self.ref and event.raw_status == 'PreSubmitted'):
            self.revival_events.append(event)
        return super().observe_native(event)


def _prepared(overnight_run, monkeypatch, request, *, spontaneous):
    rig, pid, harness = overnight_run()
    server = RevivedCancelledStopServer(rig, pid, harness, monkeypatch, request,
                                        spontaneous=spontaneous)
    server.last_share()
    server.final_phase = True
    server.final_alert_start = len(rig._b26_alerts)
    rig.px('AAPL', 347.04)
    server.until(lambda: server.cancel_wire is not None)
    return server


def _assert_no_reuse(server):
    assert not [req for _, req in server.modification_attempts if req.intent_id == server.ref], (
        'confirmed cancelled original ID was modified', server.modification_attempts)
    assert server.final_attempts == [] and server.final_tp_wire is None
    assert Purpose.TP3 not in server.tp_wire_by_stage
    after = [(at, message.split('\0')) for at, message in server.wire
             if server.revival_at is not None and at >= server.revival_at and _opcode(message) == '3']
    assert not [fields for _, fields in after if int(fields[1]) ==
                server.rig.broker.orders[server.ref].broker_order_id], after
    assert server.rig.store.position(server.pid)['qty_open'] == 1
    _assert_wire_limits(server.wire)
    _safety(server.rig)


def test_B28d_spontaneous_working_after_cancelled_but_unsent_TP_is_immediately_held(
        overnight_run, monkeypatch, request):
    server = _prepared(overnight_run, monkeypatch, request, spontaneous=True)
    # Only pump actual native callbacks. Do not start a new synchronous query:
    # its eventual contradiction must not hide failure to handle this callback.
    deadline = server.loop.time() + 1
    while server.after_callback is None and server.loop.time() < deadline:
        server.ib.sleep(.01)
        server.rig.engine._drain(server.rig.clock.now())
    assert server.unsent_state == ('TP_NEEDS_RESTORE', None)
    assert server.after_callback is not None
    assert server.revival_events
    assert all(event.observed_at > server.confirmed_at and
               not event.connection_snapshot and not event.query_snapshot and
               event.socket_generation == server.adapter.query_generation
               for event in server.revival_events)
    snapshot = server.after_callback
    assert snapshot['p0'], 'fresh spontaneous working contradiction must page before any further query'
    assert snapshot['state'] == 'UNKNOWN' and snapshot['hold'], snapshot
    assert not snapshot['query_inflight'] and snapshot['modifications'] == []
    _assert_no_reuse(server)


def test_B28d_complete_own_query_cannot_restore_by_modifying_revived_cancelled_ID(
        overnight_run, monkeypatch, request):
    server = _prepared(overnight_run, monkeypatch, request, spontaneous=False)
    deadline = server.loop.time() + 12
    while server.loop.time() < deadline:
        server.step()
        if (_p0(server) and any(kind == 'openOrders' and at >= server.revival_at
                               for at, kind, _ in server.ends)):
            break
    assert server.unsent_state == ('TP_NEEDS_RESTORE', None)
    assert server.revival_at is not None and server.confirmed_at is not None
    assert server.revival_events, 'the native own-query result must actually contain the working original ID'
    assert all(event.observed_at > server.confirmed_at and event.query_snapshot and
               not event.connection_snapshot for event in server.revival_events)
    assert any(code in {'5', '16'} and at >= server.revival_at
               for at, code, _ in server.requests)
    assert any(kind == 'openOrders' and at >= server.revival_at for at, kind, _ in server.ends), (
        'no complete actual native order-query End', server.requests, server.ends)
    assert _p0(server), server.rig._b26_alerts[server.final_alert_start:]
    assert server.rig.store.protection(server.pid)['state'] == 'UNKNOWN'
    assert server.rig.store.intent(server.ref)['hold_reason']
    assert server.rig.store.conn.execute(
        "SELECT 1 FROM audit WHERE kind='PROTECT_FINAL_CANCEL_CONTRADICTION' "
        "AND json_extract(payload,'$.position_id')=?", (server.pid,)).fetchone()
    _assert_no_reuse(server)

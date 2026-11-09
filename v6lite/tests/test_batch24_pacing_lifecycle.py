"""B24 lifecycle safety at the real offline ib_insync connection wire.

A socket generation cannot carry queued place/cancel messages into its
successor, and an obsolete timer/End cannot manufacture fresh authority.
These tests intentionally execute obsolete callbacks even after cancellation,
covering a timer that was already runnable when reset/disconnect occurred.
"""
from __future__ import annotations

import pytest

from v6lite.broker.base import BrokerError
from v6lite.tests.test_batch24_pacing import _native_wire as _base_wire, _opcode, _assert_wire_limits, _no_network


def _native_wire(tmp_path, monkeypatch, request):
    # The shared fixture retains the actual IB until its reset finalizer.
    return _base_wire(tmp_path, monkeypatch, request)


def _reconnect_without_network(adapter):
    client = adapter.ib.client
    client.connState = client.CONNECTED
    client._apiReady = True
    client._serverVersion = 180
    # _native_wire binds only this instance's IB.connect to a no-op. Running
    # the real adapter connect lifecycle installs/resets its native hooks.
    adapter.connect()


def _invalidate(client, operation):
    getattr(client, operation)()
    assert not client.isConnected() and not client.isReady()
    assert not client._msgQ and not client._timeQ


def _force_obsolete_timers(loop, timers):
    # Ignore cancelled handles on purpose: cancellation alone cannot stop a
    # callback already selected by the event loop from entering the drain.
    for at, _, handle, callback, args in timers:
        loop.now = max(loop.now, at)
        callback(*args)


@pytest.mark.parametrize('operation', ['reset', 'disconnect'])
def test_B24_reset_or_disconnect_discards_queued_writes_and_old_timer_cannot_replay_on_reconnect(
        tmp_path, monkeypatch, request, operation):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    client = adapter.ib.client
    # One old-socket query is actually sent and gets its own End. Keeping its
    # rate timestamp across reconnect avoids a channel burst after reset.
    client.reqPositions(); adapter.ib.wrapper.positionEnd()
    for n in range(client.MaxRequests - 1):
        client.sendMsg('49\0old-capacity-' + str(n) + '\0')
    client.sendMsg('3\0obsolete-place\0')
    client.sendMsg('4\0obsolete-cancel\0')
    timers = list(loop.queue)
    assert timers and len(client._msgQ) >= 2
    assert not any('obsolete-' in message for _, message in messages)
    _invalidate(client, operation)
    _reconnect_without_network(adapter)
    start_of_new_generation = len(messages)
    assert adapter.positions_fresh() == []
    _force_obsolete_timers(loop, timers)
    loop.drain()
    assert not any('obsolete-' in message for _, message in messages), messages
    position_times = [at for at, message in messages if _opcode(message) == '61']
    assert len(position_times) == 2
    assert position_times[1] - position_times[0] >= 1 - 1e-6, position_times
    _assert_wire_limits(messages[start_of_new_generation:])


@pytest.mark.parametrize('operation', ['reset', 'disconnect'])
def test_B24_reset_or_disconnect_inside_conn_send_aborts_old_drain_before_remaining_writes(
        tmp_path, monkeypatch, request, operation):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    client = adapter.ib.client
    for n in range(client.MaxRequests):
        client.sendMsg('49\0old-capacity-' + str(n) + '\0')
    client.sendMsg('3\0old-place-one\0')
    client.sendMsg('4\0old-cancel-two\0')
    client.sendMsg('3\0old-place-three\0')
    timers = list(loop.queue)
    assert timers and len(client._msgQ) == 3
    assert not any(_opcode(message) in {'3', '4'} for _, message in messages)
    reset_count = []
    def transport(packet):
        message = packet[4:].decode()
        messages.append((loop.time(), message))
        if message == '3\0old-place-one\0':
            reset_count.append(operation)
            # Actual native lifecycle reset while sendMsg still owns local
            # deque references; the drain must notice the changed generation.
            _invalidate(client, operation)
            # A new generation may become ready while the old drain still
            # owns its stack. Its valid protection must drain automatically,
            # without resurrecting old messages or needing an external kick.
            _reconnect_without_network(adapter)
            client.sendMsg('3\0new-generation-stop\0')
    monkeypatch.setattr(client.conn, 'sendMsg', transport)
    loop.queue.clear()
    loop.now = 2.0
    _force_obsolete_timers(loop, timers)
    loop.drain()
    sent_old_writes = [message for _, message in messages if _opcode(message) in {'3', '4'} and 'old-' in message]
    assert reset_count == [operation]
    assert sent_old_writes == ['3\0old-place-one\0'], ('old deque continued after reset', sent_old_writes)
    new_stops = [(at, message) for at, message in messages if message == '3\0new-generation-stop\0']
    assert len(new_stops) == 1, 'new generation stop must drain without another external send'
    assert new_stops[0][0] - 2.0 < 5
    assert not client._msgQ
    _reconnect_without_network(adapter)
    _force_obsolete_timers(loop, timers)
    loop.drain()
    assert [message for _, message in messages if _opcode(message) in {'3', '4'} and 'old-' in message] == sent_old_writes
    assert sum(message == '3\0new-generation-stop\0' for _, message in messages) == 1


def test_B24_old_End_after_reconnect_without_new_request_poisoned_not_fresh_empty_snapshot(
        tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    client = adapter.ib.client
    old_end = adapter.ib.wrapper.openOrderEnd
    client.reqAllOpenOrders()  # old request was still awaiting its End
    client.reset()
    _reconnect_without_network(adapter)
    old_end()  # obsolete response must not become a successful empty proof
    assert 'openOrders' in adapter._channel_poisoned
    with pytest.raises(BrokerError, match='poisoned|unsolicited|unavailable'):
        adapter.open_orders()
    assert not client.isConnected()


def test_B24_query_interrupted_by_reset_cannot_return_old_result_as_new_fresh_evidence(
        tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    client = adapter.ib.client
    old_end = adapter.ib.wrapper.openOrderEnd
    stale_result = [object()]
    def interrupted_request():
        client.reqAllOpenOrders()
        client.reset()
        _reconnect_without_network(adapter)
        old_end()
        return stale_result
    monkeypatch.setattr(adapter.ib, 'reqAllOpenOrders', interrupted_request)
    with pytest.raises(BrokerError, match='count mismatch|unsolicited|poisoned|unavailable'):
        adapter.open_orders()
    assert not any(_opcode(message) in {'3', '4'} for _, message in messages)

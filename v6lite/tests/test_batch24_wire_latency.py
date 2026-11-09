"""B24 real native wire timestamps survive synchronous callback/socket cost."""
import heapq

import pytest

from v6lite.tests.test_batch24_pacing import _native_wire, _assert_wire_limits, _opcode
from v6lite.tests.test_batch24_stp_echo import _no_network


def _drain_elapsed_callbacks(loop):
    # A real event loop cannot rewind to the scheduled time when a synchronous
    # callback consumes time. Keep that rule local to latency replay.
    count = 0
    while loop.queue:
        at, _, handle, callback, args = heapq.heappop(loop.queue)
        loop.now = max(loop.now, at)
        if not handle.cancelled:
            callback(*args)
        count += 1
        assert count < 100000


@pytest.mark.parametrize('cost_source', ['query_notice', 'connection_write'])
def test_B24_real_elapsed_callback_and_connection_cost_preserve_wire_intervals(
        tmp_path, monkeypatch, request, cost_source):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    client = adapter.ib.client
    elapsed = []
    recorder = client.conn.sendMsg
    def send(packet):
        recorder(packet)  # actual native connection boundary, never invented timestamps
        if cost_source == 'connection_write' and b'late-wire' in packet:
            elapsed.append(loop.time())
            loop.now += .4  # synchronous real socket operation consumes clock time
    monkeypatch.setattr(client.conn, 'sendMsg', send)
    def slow_notice(*args):
        elapsed.append(loop.time())
        loop.now += .4  # synchronous audit/alert callback, without helper-output mocks
    if cost_source == 'query_notice':
        setter = getattr(adapter, 'set_query_throttle_handler', None)
        if setter is not None:
            setter(slow_notice)
        else:
            # B23 has the real native event but no adapter notice API. Its red
            # still must fail the actual wire contract, never missing API.
            client.throttleStart += slow_notice
    for n in range(10):
        client.sendMsg('49\0warm-' + str(n) + '\0')
    if cost_source == 'query_notice':
        loop.now += 1.000001
        client.sendMsg('61\0position-first\0')
        client.sendMsg('61\0position-second\0')
        client.sendMsg('7\0execution-first\0')
        client.sendMsg('7\0execution-second\0')
        client.sendMsg('16\0orders-first\0')
        client.sendMsg('5\0orders-second\0')
    else:
        for n in range(21):
            client.sendMsg('49\0' + ('late-wire' if n == 0 else 'queued-' + str(n)) + '\0')
        client.sendMsg('61\0position-first\0')
        client.sendMsg('61\0position-second\0')
    client.sendMsg('3\0entry-order\0')
    client.sendMsg('4\0cancel-order\0')
    client.sendMsg('3\0stop-order\0')
    _drain_elapsed_callbacks(loop)
    writes = [msg.split('\0')[1] for _,msg in messages if _opcode(msg) in {'3','4'}]
    assert writes == ['entry-order','cancel-order','stop-order']
    _assert_wire_limits(messages)
    assert elapsed, 'the scenario must really consume native callback/wire time'
    assert len(messages) == (19 if cost_source == 'query_notice' else 36)
    assert not client._msgQ and not loop.queue

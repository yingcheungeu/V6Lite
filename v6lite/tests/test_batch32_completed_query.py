"""Actual SDK decoder/wire, synthetic response vector, no broker connection."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import pytest
from ib_insync.decoder import Decoder
from v6lite.broker.base import BrokerError
from v6lite.tests.test_batch24_pacing import _native_wire, _opcode, _assert_wire_limits, _no_network


def _install_response(adapter, loop, monkeypatch, *, quantity='0', end=True):
    ib = adapter.ib
    data = json.loads(Path(__file__).with_name('fixtures').joinpath('b32_completed_native_fields.json').read_text())
    fields = list(data['fields'])
    fields[data['field_names'].index('o.filledQuantity')] = str(quantity)
    fields[0] = '101'
    decoder = ib.client.decoder
    decoder.serverVersion = data['server_version']
    def query(apiOnly):
        assert apiOnly is False
        future = ib.wrapper.startReq('completedOrders')
        ib.client.reqCompletedOrders(apiOnly)
        loop.drain()
        decoder.interpret(list(fields))
        if not end:
            future.cancel()
            raise TimeoutError('synthetic lost completedOrdersEnd')
        ib.wrapper.completedOrdersEnd()
        assert future.done()
        return future.result()
    monkeypatch.setattr(ib, 'reqCompletedOrders', query)
    return decoder, fields


def test_B32_completed_uses_native_aggregate_preserves_missing_order_ID_and_no_status_event(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    _install_response(adapter, loop, monkeypatch, quantity=1)
    snapshot = adapter.completed_orders()
    [row] = snapshot.orders
    assert row.broker_order_id is None and row.perm_id == 753795445
    assert row.account == 'SYNTHETIC-ACCOUNT' and row.con_id == 265598
    assert row.status == 'Cancelled' and row.filled == 1
    [cached] = adapter.ib.wrapper._results.get('completedOrders', []) or adapter.ib.wrapper.permId2Trade.values()
    assert cached.orderStatus.filled == 0, 'default SDK status count is not the raw aggregate'
    assert snapshot.query_request_id == 1 and snapshot.socket_generation == adapter.query_generation
    assert not adapter.drain_events(), 'completed query cannot emit status/fill/price authority'
    with pytest.raises(FrozenInstanceError):
        row.filled = 0
    assert [msg.split('\0') for _, msg in messages] == [['99', '0', '']]


@pytest.mark.parametrize('quantity', ['', 'NaN', 'Infinity', '-1', '.5', '1.7976931348623157e+308'])
def test_B32_invalid_native_completed_filled_is_not_default_zero(tmp_path, monkeypatch, request, quantity):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    _install_response(adapter, loop, monkeypatch, quantity=quantity)
    assert adapter.completed_orders().orders[0].filled is None


def test_B32_completed_startup_cache_never_substitutes_new_empty_round(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    _install_response(adapter, loop, monkeypatch)
    assert len(adapter.completed_orders().orders) == 1
    def empty(apiOnly):
        future = adapter.ib.wrapper.startReq('completedOrders')
        adapter.ib.client.reqCompletedOrders(apiOnly); loop.drain()
        adapter.ib.wrapper.completedOrdersEnd()
        return future.result()
    monkeypatch.setattr(adapter.ib, 'reqCompletedOrders', empty)
    snapshot = adapter.completed_orders()
    assert not snapshot.orders and snapshot.query_request_id == 2
    assert adapter.ib.wrapper.permId2Trade, 'old completed cache still exists but is not used'


def test_B32_completed_timeout_debt_blocks_followup_until_old_End(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    _install_response(adapter, loop, monkeypatch, end=False)
    with pytest.raises(TimeoutError):
        adapter.completed_orders()
    _install_response(adapter, loop, monkeypatch)
    with pytest.raises(BrokerError, match='earlier request has not ended'):
        adapter.completed_orders()
    assert sum(_opcode(msg) == '99' for _, msg in messages) == 1
    adapter.ib.wrapper.completedOrdersEnd()
    assert adapter.completed_orders().query_request_id == 2


def test_B32_completed_unsolicited_End_and_retired_callback_cannot_prove_new_round(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    decoder, fields = _install_response(adapter, loop, monkeypatch)
    old_end = adapter.ib.wrapper.completedOrdersEnd
    old_row = adapter.ib.wrapper.completedOrder
    adapter.ib.client.reset()
    adapter.ib.client.connState = adapter.ib.client.CONNECTED
    adapter.ib.client._apiReady = True
    adapter.ib.client._serverVersion = 180
    adapter.connect()
    old_end()
    with pytest.raises(BrokerError, match='unsolicited|unavailable|poisoned'):
        adapter.completed_orders()
    assert not adapter.ib.client.isConnected()
    assert not adapter._raw_completed_rounds


def test_B32_completed_wire_uses_query_interval_and_global_message_budget(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    _install_response(adapter, loop, monkeypatch)
    for n in range(8):
        for i in range(11):
            adapter.ib.client.sendMsg('49\0health\0')
        adapter.completed_orders()
    _assert_wire_limits(messages)
    times = [at for at, msg in messages if _opcode(msg) == '99']
    assert len(times) == 8 and all(b-a >= 1-1e-6 for a,b in zip(times, times[1:]))
    assert not any(_opcode(msg) in {'3','4'} for _,msg in messages)


@pytest.mark.parametrize('raw,zone',[
    ('20261029  10:20:30 Unknown/Zone','America/New_York'),
    ('20261029 10:20:30 +00:00','America/New_York'),
    ('20261101 01:30:00 America/New_York',''),
    ('20260308 02:30:00 America/New_York',''),
    ('20261029 10:20:30 garbage','America/New_York'),
])
def test_B32_completed_time_rejects_unknown_suffix_and_ambiguous_local_clock(raw,zone):
    from v6lite.broker.completed_orders import _completed_time
    assert _completed_time(raw,zone) is None


def test_B32_explicit_TWS_zone_can_resolve_a_strict_naive_completed_clock():
    from v6lite.broker.completed_orders import _completed_time
    assert _completed_time('20261029 11:50:00','America/New_York').isoformat()=='2026-10-29T15:50:00+00:00'



def test_B32_registered_startup_completed_debt_cannot_finish_new_query(tmp_path,monkeypatch,request):
    adapter,loop,messages=_native_wire(tmp_path,monkeypatch,request)
    _install_response(adapter,loop,monkeypatch)
    # Same fixed-key registration/native send as connectAsync, deliberately
    # leave its End outstanding. No cache can repay this request debt.
    startup=adapter.ib.wrapper.startReq('completedOrders')
    adapter._connection_syncing=True
    adapter.ib.client.reqCompletedOrders(False);loop.drain()
    adapter._connection_syncing=False
    with pytest.raises(BrokerError,match='earlier request has not ended'):
        adapter.completed_orders()
    assert sum(_opcode(msg)=='99' for _,msg in messages)==1
    adapter.ib.wrapper.completedOrdersEnd();assert startup.done()
    snapshot=adapter.completed_orders()
    assert snapshot.query_request_id==2 and len(snapshot.orders)==1


def test_B32_retired_row_and_decoder_inside_new_query_are_ignored(tmp_path,monkeypatch,request):
    from copy import deepcopy
    from ib_insync import OrderState
    adapter,loop,messages=_native_wire(tmp_path,monkeypatch,request)
    decoder,fields=_install_response(adapter,loop,monkeypatch)
    adapter.completed_orders()
    [trade]=list(adapter.ib.wrapper.permId2Trade.values())
    trade=deepcopy(trade)
    old_row=adapter.ib.wrapper.completedOrder
    old_decode=decoder.handlers[101]
    adapter.ib.client.reset()
    adapter.ib.client.connState=adapter.ib.client.CONNECTED
    adapter.ib.client._apiReady=True;adapter.ib.client._serverVersion=180
    adapter.connect()
    def fresh_empty(apiOnly):
        future=adapter.ib.wrapper.startReq('completedOrders')
        adapter.ib.client.reqCompletedOrders(apiOnly);loop.drain()
        old_row(trade.contract,trade.order,OrderState(status='Filled'))
        old_decode(list(fields))
        assert not adapter.ib.wrapper._results['completedOrders']
        adapter.ib.wrapper.completedOrdersEnd();return future.result()
    monkeypatch.setattr(adapter.ib,'reqCompletedOrders',fresh_empty)
    assert adapter.completed_orders().orders==()

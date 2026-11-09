"""B25 independent native handshake/restart contracts; no sockets/operator state.

The actual ib_insync wrapper/client remain installed. Only this instance's
transport and connection coroutine are replaced by a deterministic server.
Handshake snapshots never substitute for fresh three-source request Futures.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig, TestConfig as LiteTestConfig
from v6lite.engine import Engine
from v6lite.models import ConnectionEvent, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et, make_cfg, buy_bars
from v6lite.tests.test_batch24_pacing import OfflineLoop, _opcode
from v6lite.tests.test_batch24_stp_echo import _no_network

ORDER_ID = 501
PERM_ID = 45208044
CON_ID = 265598
CLIENT_ID = 132
STOP = 340.52
REF = 'v6l-protect-163f331d4c894f33a1a8'


def _pages(rig, level=None):
    return [(title, body) for title, body in rig.channel.messages
            if ('P0' in title or 'P1' in title) and (level is None or level in title)]


def _native_server(tmp_path, monkeypatch, request, *, cfg=None, clock=None,
                   echo=0.0, orders=True, extra_sync_end=False, bootstrap_end=True):
    """Real wrapper Futures, native request methods and End; local transport."""
    from ib_insync import Stock, Order, OrderState, ExecutionFilter, CommissionReport
    from v6lite.clock import FixedClock
    cfg = cfg or make_cfg(tmp_path, client_id=CLIENT_ID, test=LiteTestConfig(enabled=True),
                          state_dir=str(tmp_path / 'native-test'),
                          protection=ProtectionConfig(enabled=True))
    clock = clock or FixedClock(et(9, 19, 52, day=(2026, 10, 14)))
    adapter = IbkrBroker(cfg)
    ib, client, wrapper = adapter.ib, adapter.ib.client, adapter.ib.wrapper
    request.addfinalizer(client.reset)
    loop = OfflineLoop()
    loop.now = clock.now().timestamp()
    trace, wire, disconnects = [], [], []
    monkeypatch.setattr('ib_insync.client.getLoop', lambda: loop)
    monkeypatch.setattr('v6lite.broker.ibkr.time.monotonic', lambda: clock.now().timestamp())
    def sleep(seconds):
        clock.advance(seconds=seconds)
        loop.advance(seconds)
    monkeypatch.setattr(ib, 'sleep', sleep)
    monkeypatch.setattr(client.conn, 'sendMsg', lambda packet: wire.append(
        (clock.now(), packet[4:].decode())))
    contract = Stock('AAPL', 'SMART', 'USD', conId=CON_ID)
    contract.localSymbol = 'AAPL'; contract.tradingClass = 'NMS'
    order = Order(orderId=ORDER_ID, clientId=CLIENT_ID, permId=PERM_ID,
                  action='SELL', totalQuantity=3, orderType='STP', lmtPrice=echo,
                  auxPrice=STOP, tif='GTC', account=cfg.account,
                  orderRef=REF, outsideRth=False)
    fresh_positions = [3.0]
    fresh_executions = []
    def callback_pair(stage):
        wrapper.lastTime = clock.now()
        trace.append((stage, 'openOrder'))
        wrapper.openOrder(ORDER_ID, contract, order, OrderState(status='PreSubmitted'))
        trace.append((stage, 'orderStatus'))
        wrapper.orderStatus(ORDER_ID, 'PreSubmitted', 0, 3, 0, PERM_ID, 0, 0,
                            CLIENT_ID, 'trigger', 0)
    def request_orders(stage='fresh', method='reqAllOpenOrders'):
        trace.append((stage, 'openOrders-request'))
        future = wrapper.startReq('openOrders')
        getattr(client, method)()
        loop.drain()
        if orders:
            callback_pair(stage)
        wrapper.openOrderEnd()
        assert future.done(), 'only matching native End may finish this request Future'
        trace.append((stage, 'openOrders-End'))
        return future.result()
    def request_positions(stage='fresh'):
        trace.append((stage, 'positions-request'))
        future = wrapper.startReq('positions')
        client.reqPositions(); loop.drain()
        if fresh_positions[0]:
            wrapper.position(cfg.account, contract, fresh_positions[0], 342.53)
        wrapper.positionEnd()
        assert future.done()
        trace.append((stage, 'positions-End'))
        return future.result()
    def request_executions(filt=None):
        trace.append(('fresh', 'executions-request'))
        req_id = client.getReqId()
        future = wrapper.startReq(req_id)
        client.reqExecutions(req_id, filt or ExecutionFilter()); loop.drain()
        for execution in fresh_executions:
            wrapper.execDetails(req_id, contract, execution)
            wrapper.commissionReport(CommissionReport(execId=execution.execId, commission=1.0, currency='USD'))
        wrapper.execDetailsEnd(req_id)
        assert future.done()
        trace.append(('fresh', 'executions-End'))
        return future.result()
    def connect(*args, **kwargs):
        trace.append(('handshake', 'connect'))
        client.connState = client.CONNECTED
        client._serverVersion = 176; client._accounts = [cfg.account]
        client._apiReady = False
        if orders:
            callback_pair('handshake')
        if bootstrap_end:
            trace.append(('handshake', 'openOrderEnd'))
            wrapper.openOrderEnd()
        client._apiReady = True
        client.apiStart.emit()
        trace.append(('handshake', 'API-ready'))
        if extra_sync_end:
            trace.append(('sync', 'connection-snapshot-End'))
            wrapper.openOrderEnd()
        # These are IB.connectAsync's registered bootstrap requests. Their
        # completed Futures remain distinct from engine takeover requests.
        request_orders('sync', 'reqOpenOrders')
        request_positions('sync')
        trace.append(('sync', 'complete'))
        return ib
    def disconnect():
        disconnects.append(clock.now())
        client.reset()
        ib.disconnectedEvent.emit()
    monkeypatch.setattr(ib, 'connect', connect)
    monkeypatch.setattr(ib, 'disconnect', disconnect)
    monkeypatch.setattr(ib, 'reqAllOpenOrders', request_orders)
    monkeypatch.setattr(ib, 'reqPositions', request_positions)
    monkeypatch.setattr(ib, 'reqExecutions', request_executions)
    adapter._contracts['AAPL'] = contract
    adapter._b25_offline_drain = loop.drain  # local fixture scheduler, never broker authority
    return adapter, clock, trace, wire, disconnects, fresh_positions, fresh_executions


@pytest.mark.parametrize('echo', [0.0, 340.19, sys.float_info.max],
                         ids=['zero', 'finite', 'UNSET'])
@pytest.mark.parametrize('extra_sync_end', [False, True], ids=['handshake', 'handshake-and-sync'])
def test_B25_native_restart_order501_pre_ready_End_is_not_poison_and_fresh_query_required(
        tmp_path, monkeypatch, request, echo, extra_sync_end):
    adapter, clock, trace, wire, disconnects, _, _ = _native_server(
        tmp_path, monkeypatch, request, echo=echo, extra_sync_end=extra_sync_end)
    start = clock.now()
    adapter.connect()
    assert trace.index(('handshake', 'openOrderEnd')) < trace.index(('handshake', 'API-ready'))
    assert 'openOrders' not in adapter._channel_poisoned, 'current-connection bootstrap End is expected'
    assert disconnects == [] and adapter.is_connected()
    before = len(trace)
    [snapshot] = adapter.open_orders()
    held = adapter.positions_fresh()
    assert adapter.executions_today() == []
    assert all(('fresh', name + '-request') in trace[before:]
               for name in ('openOrders', 'positions', 'executions'))
    assert (snapshot.broker_order_id, snapshot.perm_id, snapshot.client_id,
            snapshot.con_id, snapshot.status, snapshot.remaining) == (
                ORDER_ID, PERM_ID, CLIENT_ID, CON_ID, 'PreSubmitted', 3)
    assert snapshot.order_type == 'STP' and snapshot.tif == 'GTC'
    assert snapshot.aux_price == STOP and snapshot.limit_price is None
    assert snapshot.broker_echo_lmt == echo
    assert len(held) == 1 and held[0].quantity == 3 and held[0].account == adapter.cfg.account
    assert (clock.now() - start).total_seconds() < 30
    assert not [message for _, message in wire if _opcode(message) in {'3', '4'}]
    assert disconnects == []


def test_B25_post_sync_unowned_End_still_poisoned_and_never_proves_empty_snapshot(
        tmp_path, monkeypatch, request):
    adapter, _, _, wire, disconnects, _, _ = _native_server(tmp_path, monkeypatch, request, bootstrap_end=False)
    adapter.connect()
    # Complete own-query boundary before a genuinely unowned later End.
    adapter.open_orders()
    adapter.ib.wrapper.openOrderEnd()
    assert 'openOrders' in adapter._channel_poisoned
    with pytest.raises(BrokerError):
        adapter.open_orders()
    assert len(disconnects) == 1
    assert not [message for _, message in wire if _opcode(message) in {'3', '4'}]


def test_B25_old_generation_End_cannot_complete_new_generation_Future(
        tmp_path, monkeypatch, request):
    adapter, _, _, wire, _, _, _ = _native_server(tmp_path, monkeypatch, request, bootstrap_end=False)
    adapter.connect(); adapter.open_orders()
    old_end = adapter.ib.wrapper.openOrderEnd
    adapter.disconnect(); adapter.connect()
    wrapper, client = adapter.ib.wrapper, adapter.ib.client
    future = wrapper.startReq('openOrders')
    client.reqAllOpenOrders()
    old_end()
    assert not future.done(), 'stale connection End cannot complete a newer request'
    assert 'openOrders' in adapter._channel_poisoned
    with pytest.raises(BrokerError):
        adapter.open_orders()
    future.cancel()
    assert not [message for _, message in wire if _opcode(message) in {'3', '4'}]


def _persisted_overnight(tmp_path, monkeypatch):
    rig = Rig(tmp_path, client_id=CLIENT_ID, test=LiteTestConfig(enabled=True),
              state_dir=str(tmp_path / 'overnight-test'), protection=ProtectionConfig(enabled=True),
              risk_per_trade_pct=.0015, start=et(9, 50, day=(2026, 10, 13)))
    rig.broker._next_id = 499; rig.broker.con_ids['AAPL'] = CON_ID
    original_levels = rig.store._levels_fn
    rig.store._levels_fn = lambda price, atr: replace(original_levels(price, atr), stop=STOP)
    import v6lite.protection_engine as pe
    original_id = pe.new_id
    monkeypatch.setattr(pe, 'new_id', lambda prefix: REF if prefix == 'v6l-protect' else original_id(prefix))
    rig.start(); rig.at(et(10, 0, 30, day=(2026, 10, 13)))
    rig.px('AAPL', 342.53)
    rig.broker.bars['AAPL'] = buy_bars(base=329.4)  # same ATR/wave, signal close 342.53
    rig.store.set_control(rig.clock.now(), 'test_entry:batch25', json.dumps({
        'symbol': 'AAPL', 'operator': 'SyntheticTest', 'at': rig.clock.now().isoformat(),
        'epoch': rig.store.control('test_entry_ready')}))
    rig.tick()
    [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
    assert entry.quantity == 3 and entry.limit_price >= 342.53
    rig.broker.fill(entry.intent_id, qty=3, price=342.53)
    rig.advance(seconds=1); rig.tick()
    [position] = rig.store.open_positions(); pid = position['position_id']
    assert rig.store.protection(pid)['state'] == 'WORKING'
    # Synthetic native identity in the persisted 10/13 fixture matches the
    # redacted callback; FakeBroker's generic orderId+50000 perm convention
    # is not a second identity source.
    rig.store.update_protection(rig.clock.now(), pid, perm_id=PERM_ID)
    rig.store.update_intent(rig.clock.now(), REF, perm_id=PERM_ID)
    rig.engine.shutdown(grace_seconds=0)
    assert REF not in rig.broker.cancels
    assert rig.store.protection(pid)['broker_order_id'] == ORDER_ID
    path, levels = rig.store.path, rig.store._levels_fn
    rig.store.close(); rig.store = Store(path, levels)
    rig.at(et(9, 19, 52, day=(2026, 10, 14)))
    rig.px('AAPL', 343.0)
    rig.channel = MemoryChannel(); rig.alerter = Alerter([rig.channel], synchronous=True)
    return rig, pid, entry


@pytest.mark.parametrize('overnight_stop_fill', [False, True], ids=['WORKING-GTC', 'filled-while-stopped'])
def test_B25_sqlite_reopen_native_connect_takeover_or_book_overnight_STOP_once(
        tmp_path, monkeypatch, request, overnight_stop_fill):
    from ib_insync import Execution
    rig, pid, entry = _persisted_overnight(tmp_path, monkeypatch)
    adapter, _, trace, wire, disconnects, positions, executions = _native_server(
        tmp_path, monkeypatch, request, cfg=rig.cfg, clock=rig.clock,
        orders=not overnight_stop_fill)
    if overnight_stop_fill:
        executions.append(Execution(execId='b25-overnight-stop.01.01', time=rig.clock.now(),
            acctNumber=rig.cfg.account, exchange='NASDAQ', side='SLD', shares=3,
            price=STOP, permId=PERM_ID, clientId=CLIENT_ID, orderId=ORDER_ID,
            orderRef=REF, cumQty=3, avgPrice=STOP))
        positions[0] = 0
        # Server truth changed while process was stopped; this local generic
        # broker must not retain a contradictory fresh position.
        rig.broker.orders[REF].status = 'Filled'
    broker = rig.broker
    original_connect = broker.connect
    original_drain = broker.drain_events
    def connect():
        original_connect()
        adapter.connect()
    monkeypatch.setattr(broker, 'connect', connect)
    monkeypatch.setattr(broker, 'is_connected', adapter.is_connected)
    monkeypatch.setattr(broker, 'is_synchronized', lambda: getattr(
        adapter, 'is_synchronized', adapter.is_connected)(), raising=False)
    monkeypatch.setattr(broker, 'open_orders', adapter.open_orders)
    monkeypatch.setattr(broker, 'positions_fresh', adapter.positions_fresh)
    monkeypatch.setattr(broker, 'positions', adapter.positions_fresh)
    monkeypatch.setattr(broker, 'executions_today', adapter.executions_today)
    monkeypatch.setattr(broker, 'account_executions_today', adapter.account_executions_today)
    monkeypatch.setattr(broker, 'drain_events', lambda: original_drain() + adapter.drain_events())
    before = tuple(len(x) for x in (broker.sent, broker.sent_protections,
                                   broker.modified_protections, broker.cancels))
    start = rig.clock.now()
    rig.engine = Engine(rig.cfg, broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'restart-reports')
    rig.engine.startup(); rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    assert disconnects == [] and 'openOrders' not in adapter._channel_poisoned
    assert (rig.clock.now() - start).total_seconds() < 30
    assert all(('fresh', name + '-End') in trace
               for name in ('openOrders', 'positions', 'executions'))
    assert tuple(len(x) for x in (broker.sent, broker.sent_protections,
                                broker.modified_protections, broker.cancels)) == before
    assert not [message for _, message in wire if _opcode(message) in {'3', '4'}]
    assert _pages(rig) == []
    row = rig.store.protection(pid); pos = rig.store.position(pid)
    if overnight_stop_fill:
        assert pos['status'] == 'CLOSED' and pos['qty_open'] == 0
        assert row['state'] == 'FILLED' and row['confirmed_remaining'] == 0
        assert rig.store.intent_filled_qty(REF) == 3
        assert pos['realized_pnl'] == pytest.approx(3 * (STOP - 342.53))
        assert len(rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?',
                                        ('b25-overnight-stop.01.01',)).fetchall()) == 1
        assert rig.store.control('loss_streak') == '1'
        rig.engine._recover(rig.clock.now()); rig.engine._drain(rig.clock.now())
        assert rig.store.intent_filled_qty(REF) == 3
        assert rig.store.position(pid)['realized_pnl'] == pos['realized_pnl']
        assert rig.store.control('loss_streak') == '1'
    else:
        assert pos['status'] == 'OPEN' and pos['qty_open'] == 3
        assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
        assert row['broker_order_id'] == ORDER_ID and row['perm_id'] == PERM_ID
        assert row['gap_a_started_at'] is None and row['confirmed_at'] is not None
        assert row['limit_price'] is None and row['pending_limit'] is None
        assert rig.store.intent(REF)['limit_price'] is None
        assert rig.store.control('frozen:AAPL') is None
        audits = [json.loads(r['payload']) for r in rig.store.conn.execute('SELECT payload FROM audit')]
        assert any(a.get('broker_echo_lmt') == 0.0 for a in audits)
        assert not rig.store.intent(REF)['hold_reason']

def test_B25_sync_finished_without_fresh_own_query_does_not_allow_later_unowned_End(
        tmp_path, monkeypatch, request):
    adapter, _, trace, _, _, _, _ = _native_server(tmp_path, monkeypatch, request, bootstrap_end=False)
    adapter.connect()
    assert ('sync', 'complete') in trace
    assert 'openOrders' not in adapter._channel_poisoned
    # No adapter.open_orders() was invoked yet. The sync phase alone must
    # close the exemption; a later unsolicited End is not a new snapshot.
    adapter.ib.wrapper.openOrderEnd()
    assert 'openOrders' in adapter._channel_poisoned
    with pytest.raises(BrokerError):
        adapter.open_orders()


@pytest.mark.parametrize('channel', ['openOrders', 'positions'])
def test_B25_SDK_sync_timeout_with_missing_registered_End_cannot_become_ready_or_fresh(
        tmp_path, monkeypatch, request, channel):
    adapter, _, _, wire, _, _, _ = _native_server(tmp_path, monkeypatch, request, bootstrap_end=False)
    ib, client, wrapper = adapter.ib, adapter.ib.client, adapter.ib.wrapper
    original_connect = ib.connect
    missing = []
    def sdk_timeout(*args, **kwargs):
        original_connect(*args, **kwargs)
        future = wrapper.startReq(channel)
        missing.append(future)
        getattr(client, 'reqAllOpenOrders' if channel == 'openOrders' else 'reqPositions')()
        assert not future.done(), 'SDK registered request really owes its native End'
        raise TimeoutError('synthetic SDK sync timeout: ' + channel + ' End missing')
    monkeypatch.setattr(ib, 'connect', sdk_timeout)
    with pytest.raises(TimeoutError):
        adapter.connect()
    assert not getattr(adapter, 'is_synchronized', adapter.is_connected)(), (
        'a failed SDK connection must never be marked synchronized')
    getter = adapter.open_orders if channel == 'openOrders' else adapter.positions_fresh
    with pytest.raises(BrokerError):
        getter()
    assert not any(_opcode(msg) in {'3', '4'} for _, msg in wire)
    for future in missing:
        future.cancel()


@pytest.mark.parametrize('kind', ['End-during-send', 'send-failed'])
def test_B25_native_openOrders_send_boundary_cannot_poison_normal_End_or_create_phantom_credit(
        tmp_path, monkeypatch, request, kind):
    adapter, _, _, wire, _, _, _ = _native_server(tmp_path, monkeypatch, request, bootstrap_end=False)
    adapter.connect()
    adapter.ib.sleep(1.000001)  # SDK wire query's native pacing window must expire
    client, wrapper = adapter.ib.client, adapter.ib.wrapper
    original_transport = client.conn.sendMsg
    futures = []
    deliveries, entered = [], []
    def transport(packet):
        message = packet[4:].decode()
        if _opcode(message) == '16':
            entered.append(message)
            if kind == 'send-failed':
                raise BrokerError('synthetic transport refused before send', maybe_sent=False)
            deliveries.append(message)
            original_transport(packet)
            wrapper.openOrderEnd()  # synchronous receive reentry inside send
            return
        original_transport(packet)
    monkeypatch.setattr(client.conn, 'sendMsg', transport)
    def query():
        future = wrapper.startReq('openOrders'); futures.append(future)
        client.reqAllOpenOrders()
        adapter._b25_offline_drain()
        assert entered, 'native transport must actually execute before interpreting the request outcome'
        if kind == 'End-during-send':
            assert future.done()
            return future.result()
        raise AssertionError('failed transport cannot return a completed query')
    monkeypatch.setattr(adapter.ib, 'reqAllOpenOrders', query)
    if kind == 'End-during-send':
        assert adapter.open_orders() == []
        assert len(deliveries) == 1 and 'openOrders' not in adapter._channel_poisoned
        assert adapter._req_sent['openOrders'] == adapter._req_ended['openOrders']
    else:
        sent = adapter._req_sent['openOrders']
        with pytest.raises(BrokerError, match='transport refused'):
            adapter.open_orders()
        assert adapter._req_sent['openOrders'] == sent, 'failed send must leave no request credit'
        assert adapter._req_sent['openOrders'] == adapter._req_ended['openOrders']
        for future in futures:
            future.cancel()
    assert len(entered) == 1, 'both send boundary cases must enter actual conn.sendMsg'
    assert not any(_opcode(msg) in {'3', '4'} for _, msg in wire)


@pytest.mark.parametrize('source', ['positions', 'executions'])
def test_B25_complete_bootstrap_cannot_substitute_for_failed_fresh_takeover_source(
        tmp_path, monkeypatch, request, source):
    rig, pid, _ = _persisted_overnight(tmp_path, monkeypatch)
    adapter, _, trace, wire, disconnects, _, _ = _native_server(
        tmp_path, monkeypatch, request, cfg=rig.cfg, clock=rig.clock, bootstrap_end=False)
    # This synthetic crash-window row has an acknowledged native identity,
    # but A has not yet been closed by a completed three-source proof.
    rig.store.update_protection(rig.clock.now(), pid, state='SUBMIT_PENDING',
        confirmed_remaining=0, pending_max_remaining=3, pending_qty=3,
        pending_trigger=STOP, confirmed_at=None, gap_a_started_at=rig.clock.now())
    broker = rig.broker
    connect = broker.connect
    drain = broker.drain_events
    def connect_with_snapshot():
        connect(); adapter.connect()
    monkeypatch.setattr(broker, 'connect', connect_with_snapshot)
    monkeypatch.setattr(broker, 'is_connected', adapter.is_connected)
    monkeypatch.setattr(broker, 'is_synchronized', lambda: getattr(
        adapter, 'is_synchronized', adapter.is_connected)(), raising=False)
    monkeypatch.setattr(broker, 'open_orders', adapter.open_orders)
    monkeypatch.setattr(broker, 'positions', adapter.positions_fresh)
    monkeypatch.setattr(broker, 'positions_fresh', adapter.positions_fresh)
    monkeypatch.setattr(broker, 'executions_today', adapter.executions_today)
    monkeypatch.setattr(broker, 'account_executions_today', adapter.account_executions_today)
    monkeypatch.setattr(broker, 'drain_events', lambda: drain() + adapter.drain_events())
    failures = []
    def fresh_unavailable(*args, **kwargs):
        failures.append(source)
        raise BrokerError('synthetic failed fresh ' + source + ' request')
    monkeypatch.setattr(adapter.ib, 'reqPositions' if source == 'positions' else 'reqExecutions',
                        fresh_unavailable)
    before = tuple(len(x) for x in (broker.sent, broker.sent_protections,
                                   broker.modified_protections, broker.cancels))
    rig.engine = Engine(rig.cfg, broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'failed-fresh-restart-report')
    rig.engine.startup(); rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    assert ('handshake', 'openOrder') in trace and ('handshake', 'orderStatus') in trace
    assert ('sync', 'complete') in trace and failures, 'complete bootstrap plus actual failed own source'
    assert disconnects == [] and 'openOrders' not in adapter._channel_poisoned
    row = rig.store.protection(pid)
    assert row['confirmed_at'] is None and row['gap_a_started_at'] is not None
    assert row['confirmed_remaining'] == 0 and row['state'] != 'WORKING'
    assert rig.store.position(pid)['qty_open'] == 3
    assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
    assert tuple(len(x) for x in (broker.sent, broker.sent_protections,
                                broker.modified_protections, broker.cancels)) == before
    assert not any(_opcode(msg) in {'3', '4'} for _, msg in wire)


def test_B25_stale_previous_generation_End_during_new_handshake_is_not_exempt(
        tmp_path, monkeypatch, request):
    adapter, _, _, wire, _, _, _ = _native_server(tmp_path, monkeypatch, request, bootstrap_end=False)
    adapter.connect(); adapter.open_orders()
    old_end = adapter.ib.wrapper.openOrderEnd
    adapter.disconnect()
    ib, client = adapter.ib, adapter.ib.client
    original_connect = ib.connect
    seen = []
    def next_handshake(*args, **kwargs):
        # Adapter.connect already established its next hook generation, but
        # the newer API socket has not yet become ready.
        client.connState = client.CONNECTED; client._apiReady = False
        before = adapter._req_ended['openOrders']
        old_end()
        seen.append((before, adapter._req_ended['openOrders']))
        assert 'openOrders' in adapter._channel_poisoned
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(ib, 'connect', next_handshake)
    adapter.connect()
    assert seen and seen[0][0] == seen[0][1], 'old End cannot increment current-generation credit'
    assert 'openOrders' in adapter._channel_poisoned
    with pytest.raises(BrokerError):
        adapter.open_orders()
    assert not any(_opcode(msg) in {'3', '4'} for _, msg in wire)


@pytest.mark.parametrize('channel', ['openOrders', 'positions'])
def test_B25_SDK_swallowed_sync_timeout_return_cannot_clear_owed_End_or_grant_ready(
        tmp_path, monkeypatch, request, channel):
    adapter, _, _, wire, _, _, _ = _native_server(tmp_path, monkeypatch, request, bootstrap_end=False)
    ib, client, wrapper = adapter.ib, adapter.ib.client, adapter.ib.wrapper
    original_connect = ib.connect
    owed = []
    def sdk_logs_timeout_and_returns(*args, **kwargs):
        original_connect(*args, **kwargs)
        future = wrapper.startReq(channel)
        owed.append(future)
        getattr(client, 'reqAllOpenOrders' if channel == 'openOrders' else 'reqPositions')()
        assert not future.done()
        # ib_insync default RaiseSyncErrors=False logs startup timeout and
        # returns normally once other startup requests complete.
        return ib
    monkeypatch.setattr(ib, 'connect', sdk_logs_timeout_and_returns)
    rejected = False
    try:
        adapter.connect()
    except BrokerError:
        rejected = True
    assert rejected or not getattr(adapter, 'is_synchronized', adapter.is_connected)(), (
        'SDK return is not synchronization authority when a registered End is still owed')
    getter = adapter.open_orders if channel == 'openOrders' else adapter.positions_fresh
    before_sent = adapter._req_sent[channel]
    with pytest.raises(BrokerError):
        getter()
    assert adapter._req_sent[channel] == before_sent, 'no second fixed-key Future while SDK End is owed'
    assert len(owed) == 1
    # A straggling SDK End must not bless an adapter request: none may have
    # been submitted. Whether the implementation closed the socket or retains
    # the old SDK Future, this is never fresh three-source authority.
    end = wrapper.openOrderEnd if channel == 'openOrders' else wrapper.positionEnd
    end()
    assert not any(_opcode(msg) in {'3', '4'} for _, msg in wire)
    future = owed[0]
    if future.done() and not future.cancelled():
        future.exception()  # consume a genuine native disconnect error, if any
    else:
        future.cancel()


@pytest.mark.parametrize('kind', ['uncertain-before-End', 'End-then-throw', 'generic-runtime-error'])
def test_B25_uncertain_send_or_End_then_exception_cannot_grant_reusable_fresh_authority(
        tmp_path, monkeypatch, request, kind):
    adapter, _, _, wire, _, _, _ = _native_server(
        tmp_path, monkeypatch, request, bootstrap_end=False)
    adapter.connect()
    adapter.ib.sleep(1.000001)  # expire the bootstrap openOrders physical-wire floor
    client, wrapper = adapter.ib.client, adapter.ib.wrapper
    transport = client.conn.sendMsg
    futures, attempts = [], []
    def uncertain_transport(packet):
        message = packet[4:].decode()
        if _opcode(message) != '16':
            return transport(packet)
        attempts.append(message)
        if kind == 'End-then-throw':
            transport(packet)
            wrapper.openOrderEnd()
            raise BrokerError('synthetic request exception after synchronous End', maybe_sent=True)
        if kind == 'generic-runtime-error':
            raise RuntimeError('synthetic transport state unknown')
        raise BrokerError('synthetic transport partial send unknown', maybe_sent=True)
    monkeypatch.setattr(client.conn, 'sendMsg', uncertain_transport)
    def query():
        future = wrapper.startReq('openOrders'); futures.append(future)
        client.reqAllOpenOrders()
        adapter._b25_offline_drain()
        assert attempts, 'uncertain-send fault must enter actual native transport'
        raise AssertionError('an uncertain send exception cannot yield successful query evidence')
    monkeypatch.setattr(adapter.ib, 'reqAllOpenOrders', query)
    with pytest.raises((BrokerError, RuntimeError)):
        adapter.open_orders()
    assert len(attempts) == 1
    ambiguous = ('openOrders' in adapter._channel_poisoned or
                 adapter._req_sent['openOrders'] > adapter._req_ended['openOrders'] or
                 not getattr(adapter, 'is_synchronized', adapter.is_connected)())
    assert ambiguous, 'uncertain send/End exception cannot leave a clean reusable channel'
    with pytest.raises(BrokerError):
        adapter.open_orders()
    assert len(attempts) == 1, 'ambiguous old send cannot authorize a newer fixed-key request'
    assert not any(_opcode(msg) in {'3', '4'} for _, msg in wire)
    for future in futures:
        if future.done() and not future.cancelled():
            future.exception()
        else:
            future.cancel()

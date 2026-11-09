"""B24 offline pacing contracts at the actual native wire and evidence gates."""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ConfigError, ProtectionConfig, load_config, validate
from v6lite.models import StatusEvent
from v6lite.tests.helpers import Rig, make_cfg
from v6lite.tests.test_review_round2 import _two_open_positions
from v6lite.tests.test_batch24_stp_echo import _no_network, NativeIB, ECHO

CHANNEL_FIELDS = (
    'open_orders_min_interval_seconds',
    'positions_min_interval_seconds',
    'executions_min_interval_seconds',
)


class OfflineLoop:
    """Only the IB client scheduler; advance time without real sleeps/network."""
    def __init__(self):
        self.now = 0.0
        self.queue = []
        self.seq = 0
    def time(self):
        return self.now
    def call_at(self, when, callback, *args):
        self.seq += 1
        handle = SimpleNamespace(cancelled=False)
        handle.cancel = lambda: setattr(handle, 'cancelled', True)
        heapq.heappush(self.queue, (max(float(when) + .000001, self.now + .000001),
            self.seq, handle, callback, args))
        return handle
    def advance(self, seconds):
        target = self.now + seconds
        count = 0
        while self.queue and self.queue[0][0] <= target:
            at, _, handle, callback, args = heapq.heappop(self.queue)
            self.now = at
            if not handle.cancelled:
                callback(*args)
            count += 1
            assert count < 100000, 'a native scheduler must make time progress'
        self.now = target
    def drain(self):
        while self.queue:
            self.advance(max(.000001, self.queue[0][0] - self.now))


def _native_wire(tmp_path, monkeypatch, request):
    from ib_insync import ExecutionFilter
    cfg = make_cfg(tmp_path, protection=ProtectionConfig(enabled=False))
    adapter = IbkrBroker(cfg)
    ib, client = adapter.ib, adapter.ib.client
    # Keep this real IB alive through teardown, then reset before its weak
    # disconnect listeners can be garbage collected. Never invoke a socket.
    request.addfinalizer(lambda: ib.client.reset())
    loop = OfflineLoop()
    messages = []
    monkeypatch.setattr('ib_insync.client.getLoop', lambda: loop)
    monkeypatch.setattr('v6lite.broker.ibkr.time.monotonic', loop.time)
    monkeypatch.setattr(ib, 'sleep', loop.advance)
    monkeypatch.setattr(client.conn, 'sendMsg', lambda packet: messages.append((loop.time(), packet[4:].decode())))
    client.connState = client.CONNECTED
    client._apiReady = True
    client._serverVersion = 180
    # This instance method is a no-op connection fixture. The real adapter
    # lifecycle still installs all pre-connect native request/End/wire hooks.
    monkeypatch.setattr(ib, 'connect', lambda *args, **kwargs: ib)
    adapter.connect()
    loop.drain()
    # Startup really used wire capacity. Advance its full sliding window;
    # clearing only native queues would leave a separate pacer's history.
    loop.advance(1.000001)
    messages.clear()
    def orders():
        client.reqAllOpenOrders(); loop.drain(); ib.wrapper.openOrderEnd()
        return []
    def positions():
        client.reqPositions(); loop.drain(); ib.wrapper.positionEnd()
        return []
    next_id = [700]
    def executions(filt=None):
        next_id[0] += 1
        client.reqExecutions(next_id[0], filt or ExecutionFilter())
        loop.drain()
        return []
    monkeypatch.setattr(ib, 'reqAllOpenOrders', orders)
    monkeypatch.setattr(ib, 'reqPositions', positions)
    monkeypatch.setattr(ib, 'reqExecutions', executions)
    return adapter, loop, messages


def _opcode(message):
    return message.split('\0', 1)[0]


def _assert_wire_limits(messages):
    assert messages, 'the test must observe actual conn.sendMsg calls'
    times = [at for at, _ in messages]
    for at in times:
        count = sum(at - 1 + 1e-7 < value <= at + 1e-7 for value in times)
        assert count <= 10, ('actual IBKR messages per sliding second', at, count)
    for name, codes in [('openOrders', {'5', '16'}), ('positions', {'61'}), ('executions', {'7'})]:
        actual = [at for at, msg in messages if _opcode(msg) in codes]
        assert all(b - a >= 1 - 1e-6 for a, b in zip(actual, actual[1:])), (name, actual)


def test_B24_all_native_outgoing_messages_are_limited_at_connection_wire(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    for n in range(1000):
        # Mixed currentTime, market data, cancel, placeOrder and account APIs:
        # opcode classification cannot leave a bypass around the global cap.
        code = ('49', '1', '4', '3', '62', '6')[n % 6]
        adapter.ib.client.sendMsg(code + '\0fixture-' + str(n) + '\0')
    loop.drain()
    assert len(messages) == 1000
    _assert_wire_limits(messages)


def test_B24_all_three_query_channels_use_actual_one_second_minimum(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    for _ in range(8):
        assert adapter.open_orders() == []
        assert adapter.positions_fresh() == []
        assert adapter.executions_today() == []
        assert adapter.account_executions_today() == []  # same executions channel
    _assert_wire_limits(messages)
    assert sum(_opcode(msg) == '7' for _, msg in messages) == 16


def test_B24_startup_native_queries_cannot_bypass_following_fresh_channel_limits(tmp_path, monkeypatch, request):
    from ib_insync import ExecutionFilter
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    # connectAsync issues these native calls itself, before engine queries.
    client = adapter.ib.client
    client.reqPositions(); loop.drain(); adapter.ib.wrapper.positionEnd()
    client.reqAllOpenOrders(); loop.drain(); adapter.ib.wrapper.openOrderEnd()
    client.reqExecutions(600, ExecutionFilter()); loop.drain()
    adapter.positions_fresh(); adapter.open_orders(); adapter.executions_today()
    _assert_wire_limits(messages)


def test_B24_congested_read_queries_do_not_delay_stop_beyond_A5_or_reorder_orders(tmp_path, monkeypatch, request):
    adapter, loop, messages = _native_wire(tmp_path, monkeypatch, request)
    client = adapter.ib.client
    for n in range(80):
        client.sendMsg('49\0health-' + str(n) + '\0')
    started = loop.time()
    client.sendMsg('3\0entry-order\0')
    client.sendMsg('4\0cancel-order\0')
    client.sendMsg('3\0stop-order\0')
    loop.drain()
    orders = [(at, msg) for at, msg in messages if _opcode(msg) in {'3', '4'}]
    assert [msg.split('\0')[1] for _, msg in orders] == ['entry-order', 'cancel-order', 'stop-order']
    assert orders[-1][0] - started < 5, ('queued protection latency', orders[-1][0] - started)
    _assert_wire_limits(messages)


def test_B24_query_intervals_default_to_one_second_and_change_fingerprint(tmp_path):
    cfg = make_cfg(tmp_path)
    assert all(getattr(cfg, name, None) == 1 for name in CHANNEL_FIELDS)
    for name in CHANNEL_FIELDS:
        changed = validate(replace(cfg, **{name: 1.75}))
        assert changed.fingerprint() != cfg.fingerprint(), name


@pytest.mark.parametrize('field', CHANNEL_FIELDS)
@pytest.mark.parametrize('value', [.5, 0, -1, float('nan'), float('inf'), True])
def test_B24_query_intervals_cannot_be_configured_below_safety_floor(tmp_path, field, value):
    cfg = make_cfg(tmp_path)
    assert hasattr(cfg, field), 'the interval must be a typed, fingerprinted config field'
    with pytest.raises(ConfigError, match=field):
        validate(replace(cfg, **{field: value}))


def test_B24_query_intervals_load_from_ibkr_without_reading_operator_config(tmp_path):
    path = tmp_path / 'synthetic-pacing.toml'
    path.write_text('mode="paper"\n[ibkr]\naccount="DU0000000"\nport=4002\n' +
        '\n'.join(name + '=1.75' for name in CHANNEL_FIELDS) + '\n[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n', encoding='utf-8')
    cfg = load_config(path)
    assert all(getattr(cfg, name) == 1.75 for name in CHANNEL_FIELDS)


def _native_from_open(rig, order, *, wrong=False):
    req = rig.broker.orders[order.intent_id].req
    native_order = SimpleNamespace(orderRef=order.intent_id, orderId=order.broker_order_id,
        permId=order.perm_id, account=order.account, clientId=999 if wrong else order.client_id,
        action=order.side, orderType=order.order_type, tif=order.tif, outsideRth=order.outside_rth,
        totalQuantity=req.quantity, auxPrice=order.aux_price,
        lmtPrice=ECHO if wrong else None)
    status = SimpleNamespace(orderId=order.broker_order_id, permId=order.perm_id,
        clientId=native_order.clientId, status='PreSubmitted', filled=order.filled,
        remaining=order.remaining, mktCapPrice=0)
    contract = SimpleNamespace(symbol=order.symbol, conId=order.con_id, secType='STK', currency='USD')
    return SimpleNamespace(order=native_order, orderStatus=status, contract=contract,
        log=[], fills=[], advancedError='')


@pytest.mark.parametrize("driver", ["coordinator", "main-loop"])
def test_B24_five_minutes_bad_identity_backoff_keeps_hold_and_allows_health_and_other_position(tmp_path, monkeypatch, driver):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=.0015)
    _two_open_positions(rig)
    rows = {row['symbol']: row for row in rig.store.protections()}
    bad = rows['AAPL']; good = rows['SPY']
    original_open = rig.broker.open_orders
    position_truth = rig.broker.positions_fresh
    execution_truth = rig.broker.executions_today
    raw_orders = original_open()
    native = [_native_from_open(rig, order, wrong=order.symbol == 'AAPL') for order in raw_orders]
    adapter = IbkrBroker(rig.cfg); adapter.ib = NativeIB(rig.clock)
    adapter.ib.snapshot = native
    def native_positions():
        return [SimpleNamespace(account=position.account,
            contract=SimpleNamespace(symbol=position.symbol, conId=position.con_id,
                secType='STK', currency='USD'),
            position=position.quantity, avgCost=position.avg_cost)
            for position in position_truth()]
    def native_executions():
        return [SimpleNamespace(
            execution=SimpleNamespace(execId=fill.exec_id, acctNumber=rig.cfg.account,
                clientId=rig.cfg.client_id, orderRef=fill.intent_id,
                side='BOT' if fill.side == 'BUY' else 'SLD', shares=fill.quantity,
                price=fill.price, time=fill.time, orderId=fill.broker_order_id or 0,
                permId=fill.perm_id or 0),
            contract=SimpleNamespace(symbol=fill.symbol,
                conId=fill.con_id or rig.broker.con_ids[fill.symbol],
                secType='STK', currency='USD'),
            commissionReport=SimpleNamespace(
                execId=fill.exec_id if fill.commission is not None else '',
                commission=fill.commission))
            for fill in execution_truth()]
    adapter.ib.positions_provider = native_positions
    adapter.ib.executions_provider = native_executions
    adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
    monkeypatch.setattr('v6lite.broker.ibkr.time.monotonic', lambda: rig.clock.now().timestamp())
    # All fresh queries use the real adapter's guard and converters. Streamed
    # positions remains its cache path rather than Fake.positions re-querying.
    for method in ('open_orders', 'positions', 'positions_fresh',
                   'executions_today', 'account_executions_today'):
        monkeypatch.setattr(rig.broker, method, getattr(adapter, method))
    # This native contradiction creates the actual UNKNOWN/hold/frozen state.
    adapter._on_status(next(trade for trade in native if trade.contract.symbol == 'AAPL'))
    assert rig.store.protection(bad['position_id'])['state'] == 'UNKNOWN'
    assert rig.store.intent(bad['intent_id'])['hold_reason'] and rig.store.control('frozen:AAPL')
    hold = rig.store.intent(bad['intent_id'])['hold_reason']
    attempts = []
    query_trace = adapter.ib.queries
    evidence = rig.engine._protection_evidence
    def traced(row):
        attempts.append((row['symbol'], rig.clock.now().timestamp()))
        return evidence(row)
    monkeypatch.setattr(rig.engine, '_protection_evidence', traced)
    heartbeats = []
    heartbeat = rig.broker.gateway_heartbeat_request
    def healthy_request():
        heartbeats.append(rig.clock.now().timestamp())
        return heartbeat()
    monkeypatch.setattr(rig.broker, 'gateway_heartbeat_request', healthy_request)
    query_trace.clear(); rig.channel.messages.clear()
    writes = tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections, rig.broker.modified_protections, rig.broker.cancels))
    started = rig.clock.now().timestamp()
    # Real openOrders converts native orders and generates PriceObservation
    # events, reproducing the urgent-pending feedback that caused the storm.
    while rig.clock.now().timestamp() - started < 300:
        if driver == 'main-loop':
            rig.tick()  # Real recovery/resolve/reconcile paths remain enabled.
            before_pump = rig.clock.now().timestamp()
            adapter.pump(1)
            if rig.clock.now().timestamp() - before_pump < .1:
                # A self-generated urgent wake must not stall virtual time;
                # this is CPU elapsed time, never a new broker confirmation.
                rig.advance(seconds=.1)
        else:
            rig.engine._protection_progress(rig.clock.now(), steady_budget=True, phase='active')
            rig.engine._protection_progress(rig.clock.now(), steady_budget=True, phase='steady')
            rig.engine._gateway_heartbeat(rig.clock.now())
        row = rig.store.protection(bad['position_id'])
        assert row['state'] == 'UNKNOWN' and row['confirmed_remaining'] == bad['confirmed_remaining']
        assert row['pending_max_remaining'] == bad['pending_max_remaining']
        assert rig.store.intent(bad['intent_id'])['hold_reason'] == hold and rig.store.control('frozen:AAPL')
        rig.advance(seconds=.1)
    bad_times = [at for symbol, at in attempts if symbol == 'AAPL']
    good_times = [at for symbol, at in attempts if symbol == 'SPY']
    assert 10 <= len(bad_times) <= 15, ('five-minute identity attempts', len(bad_times))
    gaps = [b - a for a, b in zip(bad_times, bad_times[1:])]
    expected = [1, 2, 4, 8, 16] + [30] * (len(gaps) - 5)
    assert all(actual >= wanted - 1e-6 for actual, wanted in zip(gaps, expected)), (gaps, expected)
    assert len(good_times) >= 10, 'a different protected holding still gets fresh evidence'
    assert max(b - a for a, b in zip(good_times, good_times[1:])) <= 32
    assert len(heartbeats) >= 4
    assert rig.store.protection(good['position_id'])['state'] == 'WORKING'
    assert (rig.clock.now() - __import__('datetime').datetime.fromisoformat(rig.store.protection(good['position_id'])['confirmed_at'])).total_seconds() <= 32
    for channel in ('openOrders', 'positions', 'executions'):
        times = [at for name, at in query_trace if name == channel]
        assert len(times) <= 42, (driver, channel, len(times))
        assert all(b - a >= 1 - 1e-6 for a, b in zip(times, times[1:])), (channel, times)
    for at in [t for _, t in query_trace] + heartbeats:
        assert sum(at - 1 + 1e-7 < t <= at + 1e-7 for _, t in query_trace) + sum(at - 1 + 1e-7 < t <= at + 1e-7 for t in heartbeats) <= 10
    limits = [(title, body) for title, body in rig.channel.messages if 'P1' in title and '券商查询已限速' in title]
    assert len(limits) == 1 and 'AAPL' in limits[0][0] + limits[0][1]
    assert tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections, rig.broker.modified_protections, rig.broker.cancels)) == writes
    assert not any('STP LMT' == getattr(req, 'order_type', '') or 'MKT' == getattr(req, 'order_type', '') for req in rig.broker.sent_protections)


def test_B24_repeated_bad_native_callbacks_do_not_reset_position_retry_or_forge_confirmation(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=.0015)
    rig.open_aapl(); [pos] = rig.store.open_positions(); row = rig.store.protection(pos['position_id'])
    actual = rig.broker.open_orders()[0]
    bad = replace(actual, client_id=999, limit_price=ECHO)
    rig.engine._on_urgent_broker_event(StatusEvent(row['intent_id'], 'PreSubmitted', 0, 3,
        row['broker_order_id'], row['perm_id'], order_identity=bad, identity_checked=True))
    monkeypatch.setattr(rig.broker, 'open_orders', lambda: [bad])
    attempted = []; evidence = rig.engine._protection_evidence
    def trace(protection):
        attempted.append(rig.clock.now().timestamp()); return evidence(protection)
    monkeypatch.setattr(rig.engine, '_protection_evidence', trace)
    original_confirmed = rig.store.protection(pos['position_id'])['confirmed_at']
    for _ in range(100):
        rig.engine._on_urgent_broker_event(StatusEvent(row['intent_id'], 'PreSubmitted', 0, 3,
            row['broker_order_id'], row['perm_id'], order_identity=bad, identity_checked=True))
        rig.engine.process_protection_priority()
        rig.engine._protection_progress(rig.clock.now())
        rig.advance(seconds=.1)
    assert len(attempted) <= 4, attempted
    assert rig.store.protection(pos['position_id'])['state'] == 'UNKNOWN'
    assert rig.store.protection(pos['position_id'])['confirmed_at'] == original_confirmed
    assert rig.store.intent(row['intent_id'])['hold_reason'] and rig.store.control('frozen:AAPL')

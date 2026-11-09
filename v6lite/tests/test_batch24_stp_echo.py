"""B24 native STP echoes: real G3-T callbacks, isolated state, no network.

Fixture-only FakeBroker overrides provide the complete original native wire
values. No broker implementation is replaced and no operator state is read.
"""
from __future__ import annotations

import copy
import json
import math
import socket
import sys
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig, TestConfig as LiteTestConfig
from v6lite.models import BrokerAck, ProtectionRequest, Purpose, StatusEvent
from v6lite.tests.helpers import Rig, et

ECHO = 340.19000000000005
STOP = 340.22
ORDER_ID = 64
PERM_ID = 2052906961
CON_ID = 265598
CLIENT_ID = 132
REF = 'v6l-protect-3255088aaf6c4956b4bc'
CALLBACKS = (
    ('2026-10-12T14:06:32.081546+00:00', 'ApiPending', 0, sys.float_info.max, ''),
    ('2026-10-12T14:06:32.122215+00:00', 'PendingSubmit', PERM_ID, ECHO, ''),
    ('2026-10-12T14:06:32.212543+00:00', 'PreSubmitted', PERM_ID, ECHO, 'trigger'),
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import asyncio
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    from ib_insync import IB
    def forbidden(*args, **kwargs):
        raise AssertionError('B24 tests must never connect to a socket/TWS/Gateway')
    async def forbidden_async(*args, **kwargs):
        forbidden(*args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


class NativeIB:
    def __init__(self, clock):
        self.clock = clock
        self.snapshot = []
        self.queries = []
        self.placed = []
        self.cancelled = []
        self.positions_provider = lambda: []
        self.executions_provider = lambda: []
    def connect(self, *args, **kwargs):
        raise AssertionError('offline native double cannot connect')
    def isConnected(self):
        return True
    def sleep(self, seconds):
        self.clock.advance(seconds=seconds)
    def reqAllOpenOrders(self):
        self.queries.append(('openOrders', self.clock.now().timestamp()))
        return list(self.snapshot)
    def positions(self, account=None):
        rows = list(self.positions_provider())
        return [row for row in rows if account is None or row.account == account]
    def reqPositions(self):
        self.queries.append(('positions', self.clock.now().timestamp()))
        return self.positions()
    def reqExecutions(self, execution_filter=None):
        self.queries.append(('executions', self.clock.now().timestamp()))
        return list(self.executions_provider())
    def openTrades(self):
        return list(self.snapshot)
    def trades(self):
        return list(self.snapshot)
    def placeOrder(self, contract, order):
        self.placed.append((copy.deepcopy(contract), copy.deepcopy(order)))
        order.clientId = CLIENT_ID
        status = SimpleNamespace(orderId=order.orderId, permId=order.permId,
            clientId=CLIENT_ID, status='PreSubmitted', filled=0, remaining=order.totalQuantity,
            mktCapPrice=0)
        trade = SimpleNamespace(order=order, orderStatus=status, contract=contract,
            log=[], fills=[], advancedError='')
        self.snapshot = [trade]
        return trade
    def cancelOrder(self, order):
        self.cancelled.append(copy.deepcopy(order))


def _native(rig, *, raw='PreSubmitted', perm=PERM_ID, echo=ECHO, **changes):
    order = SimpleNamespace(orderRef=REF, orderId=ORDER_ID, permId=perm,
        account=rig.cfg.account, clientId=CLIENT_ID, action='SELL', orderType='STP',
        tif='GTC', outsideRth=False, totalQuantity=3, auxPrice=STOP, lmtPrice=echo)
    status = SimpleNamespace(orderId=ORDER_ID, permId=perm, clientId=CLIENT_ID,
        status=raw, filled=0.0, remaining=3.0, avgFillPrice=0.0, mktCapPrice=0.0,
        whyHeld='trigger' if raw == 'PreSubmitted' else '')
    contract = SimpleNamespace(symbol='AAPL', conId=CON_ID, secType='STK', currency='USD',
        exchange='SMART', primaryExchange='NASDAQ', localSymbol='AAPL', tradingClass='NMS')
    for name, value in changes.items():
        target, field = name.split('__', 1)
        setattr({'order': order, 'status': status, 'contract': contract}[target], field, value)
    return SimpleNamespace(order=order, orderStatus=status, contract=contract,
        log=[SimpleNamespace(time=rig.clock.now(), status=raw, errorCode=0, message='')],
        fills=[], advancedError='')


def _pending_rig(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True),
        risk_per_trade_pct=.0015, client_id=CLIENT_ID, test=LiteTestConfig(enabled=True),
        state_dir=str(tmp_path / 'paper-test'), start=et(9, 50, day=(2026, 10, 12)))
    rig.broker._next_id = 62
    rig.broker.con_ids['AAPL'] = CON_ID
    rig.broker.protection_auto_status = False
    import v6lite.protection_engine as protect
    original_id = protect.new_id
    monkeypatch.setattr(protect, 'new_id', lambda prefix: REF if prefix == 'v6l-protect' else original_id(prefix))
    submit = rig.broker.submit_stop
    def pending_ack(request):
        ack = submit(request)
        return BrokerAck(ack.broker_order_id, None)
    monkeypatch.setattr(rig.broker, 'submit_stop', pending_ack)
    rig.start(); rig.at(et(10, 0, 30, day=(2026, 10, 12)))
    rig.store.set_control(rig.clock.now(), 'test_entry:batch24', json.dumps({
        'symbol': 'AAPL', 'operator': 'SyntheticTest', 'at': rig.clock.now().isoformat(),
        'epoch': rig.store.control('test_entry_ready')}))
    rig.tick()
    [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
    assert entry.quantity == 3 and rig.broker.orders[entry.intent_id].broker_order_id == 63
    # Controlled fill-level fixture establishes the actual 10/12 stop; the
    # engine still creates and submits its real plan and real protection row.
    original_levels = rig.store._levels_fn
    rig.store._levels_fn = lambda price, atr: replace(original_levels(price, atr), stop=STOP)
    rig.at(datetime(2026, 10, 12, 14, 6, 32, 27256, tzinfo=timezone.utc))
    fill = rig.broker.fill(entry.intent_id, qty=3, price=342.24, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    [position] = rig.store.open_positions()
    row = rig.store.protection(position['position_id'])
    assert (row['intent_id'], row['broker_order_id'], row['perm_id'], row['trigger_price']) == (REF, 64, None, STOP)
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = NativeIB(rig.clock)
    monkeypatch.setattr('v6lite.broker.ibkr.time.monotonic', lambda: rig.clock.now().timestamp())
    adapter._contracts['AAPL'] = _native(rig).contract
    rig.channel.messages.clear()
    return rig, position['position_id'], adapter


def _deliver(rig, adapter, trade):
    adapter._trades[REF] = trade
    adapter.ib.snapshot = [trade]
    adapter._on_status(trade)
    events = adapter.drain_events()
    assert any(isinstance(event, StatusEvent) for event in events)
    for event in events:
        rig.engine._on_urgent_broker_event(event)


def _bind_snapshot(rig, adapter, monkeypatch):
    monkeypatch.setattr(rig.broker, 'open_orders', adapter.open_orders)


def _assert_no_echo_ledger(rig, pid):
    protection = dict(rig.store.protection(pid))
    intent = dict(rig.store.intent(REF))
    assert protection['trigger_price'] == STOP
    assert protection['limit_price'] is None and protection['pending_limit'] is None
    assert intent['limit_price'] is None
    assert 'broker_echo_lmt' not in protection and 'broker_echo_lmt' not in intent
    raw = rig.store.control('protect_native_contract:' + REF)
    if raw:
        binding = json.loads(raw)
        assert binding.get('limit_price') is None
        assert 'broker_echo_lmt' not in binding, 'the diagnostic echo must not enter durable contract authority'
    assert all(req.limit_price is None and req.trigger_price == STOP for req in rig.broker.sent_protections)


def test_B24_original_G3T_native_callback_replay_confirms_STP_within_A5_no_pages(tmp_path, monkeypatch):
    rig, pid, adapter = _pending_rig(tmp_path, monkeypatch)
    _bind_snapshot(rig, adapter, monkeypatch)
    start = datetime.fromisoformat(rig.store.protection(pid)['gap_a_started_at'])
    for timestamp, status, perm, echo, held in CALLBACKS:
        rig.at(datetime.fromisoformat(timestamp))
        trade = _native(rig, raw=status, perm=perm, echo=echo)
        assert trade.orderStatus.whyHeld == held
        _deliver(rig, adapter, trade)
    # A normal outer-loop pass follows the native callbacks. Neither callback
    # is allowed to issue a nested synchronous broker query.
    rig.engine.process_protection_priority()
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING', dict(row)
    assert row['confirmed_remaining'] == 3 and row['pending_max_remaining'] == 3
    assert row['perm_id'] == PERM_ID and row['gap_a_started_at'] is None
    assert (datetime.fromisoformat(row['confirmed_at']) - start).total_seconds() < 5
    assert not [(title, body) for title, body in rig.channel.messages if 'P0' in title or 'P1' in title]
    assert not rig.store.intent(REF)['hold_reason'] and rig.store.control('frozen:AAPL') is None
    assert len(adapter.ib.queries) <= 5
    times = [at for channel, at in adapter.ib.queries]
    assert all(b - a >= 1 - 1e-6 for a, b in zip(times, times[1:]))
    _assert_no_echo_ledger(rig, pid)
    audit = [json.loads(row['payload']) for row in rig.store.conn.execute('SELECT payload FROM audit')]
    assert any(record.get('broker_echo_lmt') == ECHO for record in audit), 'raw STP echo belongs in audit'


@pytest.mark.parametrize('echo', [None, sys.float_info.max, ECHO, 0.0, -1.0, float('nan'), float('inf'), float('-inf')])
def test_B24_STP_echo_is_diagnostic_for_all_native_status_values(tmp_path, monkeypatch, echo):
    rig, pid, adapter = _pending_rig(tmp_path, monkeypatch)
    trade = _native(rig, echo=echo)
    _deliver(rig, adapter, trade)
    row = rig.store.protection(pid)
    assert row['perm_id'] == PERM_ID and not rig.store.intent(REF)['hold_reason']
    assert row['state'] != 'UNKNOWN' and rig.store.control('frozen:AAPL') is None
    [event] = [e for e in adapter.ib.snapshot if e.order.orderRef == REF]
    identity = adapter._status_identity(event)
    assert identity is not None and identity.limit_price is None
    assert hasattr(identity, 'broker_echo_lmt'), 'native echo must survive as a separate raw field'
    raw = identity.broker_echo_lmt
    assert (isinstance(raw, float) and math.isnan(raw)) if isinstance(echo, float) and math.isnan(echo) else raw == echo
    _assert_no_echo_ledger(rig, pid)


STRICT_CHANGES = [
    {'order__account': ''}, {'order__account': 'DU0000000'},
    {'order__clientId': 999}, {'status__clientId': 999},
    {'contract__conId': 999}, {'contract__symbol': 'TSLA'},
    {'order__orderRef': 'v6l-protect-wrong'}, {'order__orderId': 999},
    {'status__orderId': 999}, {'order__permId': 999}, {'status__permId': 999},
    {'order__permId': 0}, {'status__permId': 0}, {'order__action': 'BUY'},
    {'order__orderType': 'STP LMT'}, {'order__orderType': 'LMT'},
    {'order__tif': 'DAY'}, {'order__outsideRth': True},
    {'contract__secType': 'OPT'}, {'contract__currency': 'EUR'},
    {'order__auxPrice': 340.21}, {'order__totalQuantity': 4},
    {'order__totalQuantity': 3.5}, {'status__filled': .5, 'status__remaining': 2.5},
]


@pytest.mark.parametrize('changes', STRICT_CHANGES, ids=lambda x: next(iter(x)))
def test_B24_finite_echo_never_relaxes_any_other_native_authority(tmp_path, monkeypatch, changes):
    rig, pid, adapter = _pending_rig(tmp_path, monkeypatch)
    trade = _native(rig, **changes)
    _deliver(rig, adapter, trade)
    row = rig.store.protection(pid)
    assert row['perm_id'] is None and row['confirmed_at'] is None
    assert row['confirmed_remaining'] == 0 and row['state'] != 'WORKING'
    assert len(rig.broker.sent_protections) == 1 and rig.broker.modified_protections == []
    _assert_no_echo_ledger(rig, pid)


def test_B24_auxPrice_compares_cents_and_does_not_copy_float_tail_into_ledger(tmp_path, monkeypatch):
    rig, pid, adapter = _pending_rig(tmp_path, monkeypatch)
    _deliver(rig, adapter, _native(rig, order__auxPrice=340.22000000000003))
    assert rig.store.protection(pid)['perm_id'] == PERM_ID
    assert not rig.store.intent(REF)['hold_reason']
    _assert_no_echo_ledger(rig, pid)


@pytest.mark.parametrize('operation', ['modify', 'cancel', 'snapshot'])
def test_B24_native_finite_echo_accepts_exact_STP_modify_cancel_and_snapshot(tmp_path, monkeypatch, operation):
    rig, pid, adapter = _pending_rig(tmp_path, monkeypatch)
    trade = _native(rig)
    adapter._trades[REF] = trade; adapter.ib.snapshot = [trade]
    if operation == 'modify':
        req = ProtectionRequest(REF, pid, 'AAPL', 2, STOP, None,
            broker_order_id=64, perm_id=PERM_ID, con_id=CON_ID, client_id=CLIENT_ID,
            account=rig.cfg.account, allow_lookup=False)
        adapter.modify_protection(req)
        [(_, wire)] = adapter.ib.placed
        assert wire.orderType == 'STP' and wire.auxPrice == STOP and wire.totalQuantity == 2
        assert wire.lmtPrice == sys.float_info.max, 'echo must never be sent as an actual stop limit'
        assert adapter.ib.cancelled == []
    elif operation == 'cancel':
        adapter.cancel(REF, expected_order_id=64, expected_perm_id=PERM_ID, expected_con_id=CON_ID, allow_lookup=False)
        assert len(adapter.ib.cancelled) == 1 and adapter.ib.placed == []
    else:
        [snapshot] = adapter.open_orders()
        assert snapshot.limit_price is None
        assert snapshot.broker_echo_lmt == ECHO
    _assert_no_echo_ledger(rig, pid)


def test_B24_terminal_STP_with_finite_echo_retains_refusal_classification(tmp_path, monkeypatch):
    rig, pid, adapter = _pending_rig(tmp_path, monkeypatch)
    _deliver(rig, adapter, _native(rig, echo=None))
    trade = _native(rig, raw='Inactive')
    trade.log[-1].errorCode = 201
    trade.log[-1].message = 'We cannot accept your stop order.'
    _deliver(rig, adapter, trade)
    assert rig.store.protection(pid)['state'] == 'REJECTED'
    assert rig.store.control('frozen:AAPL')
    assert any('P0' in title and '券商已拒绝保护单' in title for title, body in rig.channel.messages)
    _assert_no_echo_ledger(rig, pid)


@pytest.mark.parametrize('source', ['orderStatus', 'openOrder', 'openOrders'])
def test_B24_every_native_price_observation_source_keeps_echo_audit_only(tmp_path, monkeypatch, source):
    rig, pid, adapter = _pending_rig(tmp_path, monkeypatch)
    trade = _native(rig)
    _deliver(rig, adapter, trade)
    assert rig.store.protection(pid)['perm_id'] == PERM_ID
    adapter._observe_prices(trade, source=source)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    assert rig.store.protection(pid)['state'] != 'UNKNOWN'
    assert not rig.store.intent(REF)['hold_reason'] and rig.store.control('frozen:AAPL') is None
    assert not any('P0' in title or 'P1' in title for title, body in rig.channel.messages)
    _assert_no_echo_ledger(rig, pid)
    records = [json.loads(row['payload']) for row in rig.store.conn.execute('SELECT payload FROM audit')]
    assert any(record.get('source') == source and record.get('broker_echo_lmt') == ECHO for record in records)

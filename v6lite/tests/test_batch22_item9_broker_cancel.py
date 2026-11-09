"""Item 9: broker-ended protective orders are a distinct, durable P0.

TEMP/FakeBroker only. Wire doubles pass through IbkrBroker's real callbacks;
they never call connect/placeOrder. The setup follows each baseline's actual
protective order type (B20 STP LMT, B22 STP), so item-9 failures are about
cancellation handling, not a new STP constructor or a missing import.
"""
from __future__ import annotations

import json
import re
import socket
from dataclasses import replace
from functools import partial
from types import SimpleNamespace

import pytest

from v6lite.alerts import AlertCard, Alerter, MemoryChannel, P1_ORANGE
from v6lite.broker.ibkr import IbkrBroker
from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine, StartupError
from v6lite.exits import compute_levels
from v6lite.models import IntentStatus, OrderRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et

REASON = 'Order canceled - reason: We cannot accept your stop order. Synthetic regulatory price check.'
LONG_REASON = (
    'Order canceled - reason: We cannot accept your stop order. '
    'Synthetic broker diagnostic; no live order or account is involved. '
    + 'Original diagnostic text must reach the remote operator without truncation. ' * 4
    + 'END-OF-ORIGINAL-REASON-保留原文'
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Windows asyncio builds its internal socketpair when the local loop is
    # created. eventkit imports/caches that loop. Initialize both before the
    # socket guard; this performs no IB connection or notification request.
    import asyncio
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    from ib_insync import IB
    from ib_insync.util import getLoop
    assert getLoop() is loop

    def forbidden(*args, **kwargs):
        raise AssertionError('item9 fixtures must never open a network connection')

    async def forbidden_async(*args, **kwargs):
        forbidden(*args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _writes(rig):
    return tuple(len(items) for items in (
        rig.broker.sent, rig.broker.sent_protections,
        rig.broker.modified_protections, rig.broker.cancels))


def _pages(rig, level=None):
    return [(title, body) for title, body in rig.channel.messages
            if ('P0' in title or 'P1' in title) and (level is None or level in title)]


def _drive(rig):
    # Actual queued/query work, with FakeBroker complete evidence and no sleep.
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()


def _wire(rig, pid, *, raw='Submitted', code=0, reason='', shape='complete', wrong=None):
    """Native same-order callback; mutable terminal metadata can be cleared."""
    row = rig.store.protection(pid)
    broker_order = rig.broker.orders[row['intent_id']]
    # Read the real baseline wire type, not a fabricated STP assumption on B20.
    native_type = getattr(broker_order.req, 'order_type', 'STP LMT')
    contract = SimpleNamespace(symbol=row['symbol'], conId=row['con_id'],
                               secType='STK', currency='USD', exchange='SMART')
    order = SimpleNamespace(
        orderRef=row['intent_id'], orderId=broker_order.broker_order_id,
        permId=broker_order.broker_order_id + 50_000,
        account=rig.cfg.account, clientId=rig.cfg.client_id,
        action='SELL', orderType=native_type, tif='GTC', outsideRth=False,
        totalQuantity=broker_order.req.quantity,
        auxPrice=broker_order.req.trigger_price, lmtPrice=broker_order.req.limit_price)
    status = SimpleNamespace(status=raw, filled=broker_order.filled,
                             remaining=broker_order.remaining,
                             orderId=order.orderId, permId=order.permId,
                             clientId=order.clientId)
    if shape == 'metadata_cleared':
        # Hard identity remains exact. Only wire attributes that IB can clear
        # after an order ends are absent/reset; this is not foreign authority.
        order.action = order.orderType = order.tif = ''
        order.outsideRth = None
        order.totalQuantity = 0
        order.auxPrice = order.lmtPrice = 0.0
    elif shape == 'prices_reset':
        order.auxPrice = order.lmtPrice = 0.0
    else:
        assert shape == 'complete'
    if wrong == 'account':
        order.account = 'DU0000000'
    elif wrong == 'orderId':
        order.orderId += 99
        status.orderId = order.orderId
    elif wrong == 'permId':
        order.permId += 99
        status.permId = order.permId
    elif wrong == 'perm_missing':
        order.permId = status.permId = 0
    elif wrong == 'conId':
        contract.conId += 99
    elif wrong == 'clientId':
        order.clientId += 99
        status.clientId = order.clientId
    elif wrong == 'symbol':
        contract.symbol = 'TSLA'
    elif wrong == 'auxPrice':
        order.auxPrice += 1.0
    else:
        assert wrong is None
    trade = SimpleNamespace(order=order, contract=contract, orderStatus=status,
        log=[SimpleNamespace(time=rig.clock.now(), status=raw, message=reason, errorCode=code)],
        fills=[], advancedError='')
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = SimpleNamespace(trades=lambda: [trade], openTrades=lambda: [trade],
                                 isConnected=lambda: True)
    adapter._trades[row['intent_id']] = trade
    adapter._contracts[row['symbol']] = contract
    return adapter, trade


def _deliver(rig, adapter, trade, *, send_error=True):
    adapter._on_status(trade)
    if send_error and trade.log[-1].errorCode in {201, 202}:
        adapter._on_error(trade.order.orderId, trade.log[-1].errorCode,
                          trade.log[-1].message, trade.contract)
    events = adapter.drain_events()
    assert events, 'native callback must not disappear before engine consumption'
    for event in events:
        rig.engine._on_urgent_broker_event(event)


def _pending_three(tmp_path, *, skip_binding=False):
    # ATR=2, risk/share=5; the normal sizing rule yields exactly three shares.
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=0.0015)
    rig.broker.protection_auto_status = False
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    [entry] = [request for request in rig.broker.sent if request.purpose is Purpose.ENTRY]
    assert entry.quantity == 3
    fill = rig.broker.fill(entry.intent_id, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    [pos] = rig.store.open_positions()
    row = rig.store.protection(pos['position_id'])
    assert pos['qty_open'] == row['requested_qty'] == 3
    assert row['state'] == 'SUBMIT_PENDING' and row['gap_a_started_at']
    assert len(rig.broker.sent_protections) == 1
    # Bind a prior complete native identity without running a confirming query.
    # The terminal callback is not allowed to repair a wrong hard identity.
    if not skip_binding:
        adapter, trade = _wire(rig, pos['position_id'])
        _deliver(rig, adapter, trade, send_error=False)
    assert not rig.store.intent(row['intent_id'])['hold_reason']
    rig.channel.messages.clear()
    return rig, pos['position_id']


def _broker_cancel(rig, pid, *, raw='Cancelled', code=202, reason=REASON, shape='complete'):
    row = rig.store.protection(pid)
    assert rig.store.intent(row['intent_id'])['cancel_requested_at'] is None
    rig.broker.orders[row['intent_id']].status = raw
    adapter, trade = _wire(rig, pid, raw=raw, code=code, reason=reason, shape=shape)
    _deliver(rig, adapter, trade)
    return adapter, trade


def _assert_cancel_remote(rig, pid, reason, *, expected_fact='cancelled'):
    assert expected_fact in {'cancelled', 'unknown'}
    expected_title = {'cancelled': '券商已取消保护单', 'unknown': '券商保护终态待核实'}[expected_fact]
    pages = [(title, body) for title, body in _pages(rig, 'P0')
             if expected_title in title]
    assert pages, _pages(rig)
    if expected_fact == 'unknown':
        assert any('券商终态事实待核实' in body for _, body in pages)
        assert not any('券商已取消保护单' in title or '券商已拒绝保护单' in title
                       for title, _ in _pages(rig, 'P0'))
    assert any(reason in body for _, body in pages), 'remote P0 must show the complete original broker reason'
    assert not any('状态无法核实' in title for title, _ in _pages(rig, 'P0'))
    assert rig.store.control('frozen:AAPL')
    assert 'identity mismatch' not in (rig.store.intent(rig.store.protection(pid)['intent_id'])['hold_reason'] or '')
    assert rig.store.conn.execute(
        "SELECT COUNT(*) FROM audit WHERE kind='PROTECT_STATUS_IDENTITY_MISMATCH'").fetchone()[0] == 0


@pytest.mark.parametrize('raw', ['Cancelled', 'Inactive'])
@pytest.mark.parametrize('code,shape', [(201, 'metadata_cleared'),
                                       (202, 'prices_reset'), (0, 'metadata_cleared')])
def test_B22_item9_terminal_same_native_ids_with_cleared_metadata_is_cancelled_P0_not_identity_error(
        tmp_path, raw, code, shape):
    rig, pid = _pending_three(tmp_path)
    before = _writes(rig)
    _broker_cancel(rig, pid, raw=raw, code=code, shape=shape)
    _drive(rig)
    # A 201 rejection code combined with an explicitly canceled original reason
    # has an unknown display fact. It still cannot gain identity/release rights.
    _assert_cancel_remote(rig, pid, REASON, expected_fact='unknown' if code == 201 else 'cancelled')
    assert _writes(rig) == before, 'broker-ended protection must never be automatically replaced'
    assert rig.store.position(pid)['qty_open'] == 3


@pytest.mark.parametrize('raw', ['Cancelled', 'Inactive'])
def test_B22_item9_complete_no_working_evidence_reports_exact_three_zero_three(tmp_path, raw):
    rig, pid = _pending_three(tmp_path)
    _broker_cancel(rig, pid, raw=raw)
    _drive(rig)
    assert [(p.symbol, p.quantity) for p in rig.broker.positions_fresh()] == [('AAPL', 3)]
    assert rig.broker.open_orders() == []
    pages = _pages(rig, 'P0')
    assert any(all(text in body for text in (
        '持仓数量=3 股', '保护单已确认=0 股', '保护单可能=0 股',
        '止盈在途=0 股', '外部卖单在途=0 股', '未保护=3 股')) for _, body in pages), pages
    row = rig.store.protection(pid)
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 0
    assert rig.store.position(pid)['qty_open'] == 3


@pytest.mark.parametrize('shape', ['metadata_cleared', 'prices_reset'])
def test_B22_item9_cancel_P0_prevents_A_five_second_and_trigger_lower_level_pages(tmp_path, shape):
    rig, pid = _pending_three(tmp_path)
    _broker_cancel(rig, pid, shape=shape)
    assert _pages(rig, 'P0')
    before = _writes(rig)
    rig.advance(seconds=5)
    rig.engine._hard_protection_deadlines(rig.clock.now())
    row = rig.store.protection(pid)
    rig.px('AAPL', row['trigger_price'] - 0.50)
    rig.engine._monitor_protection_marks(rig.clock.now())
    assert _pages(rig, 'P1') == [], 'same cancellation already paged P0 must not emit a lower-level A/trigger card'
    assert _writes(rig) == before


@pytest.mark.parametrize('code', [201, 202])
def test_B22_item9_original_reason_over_two_hundred_characters_reaches_remote_card(tmp_path, code):
    assert len(LONG_REASON) > 200
    rig, pid = _pending_three(tmp_path)
    _broker_cancel(rig, pid, code=code, reason=LONG_REASON)
    # Check the actual channel delivery, not only SQLite's untruncated audit.
    assert any(LONG_REASON in body for _, body in _pages(rig, 'P0')), _pages(rig)
    _assert_cancel_remote(rig, pid, LONG_REASON, expected_fact='unknown' if code == 201 else 'cancelled')


def test_B22_item9_same_cancel_after_thirty_one_minutes_stays_P0_without_global_P1_INFO_mute(tmp_path):
    rig, pid = _pending_three(tmp_path)
    adapter, trade = _broker_cancel(rig, pid)
    assert _pages(rig, 'P0')
    incident_key = f'protect_incident:{pid}:coverage'
    original_incident = json.loads(rig.store.control(incident_key))
    assert original_incident.get('p0_sent') is True
    before = _writes(rig)
    rig.channel.messages.clear()
    rig.advance(minutes=31)
    # An ACKed, unchanged cancellation receives neither a second P0 nor a
    # lower-priority reminder. Keep its durable incident rather than deleting
    # it to make notification suppression appear successful.
    _deliver(rig, adapter, trade)
    assert _pages(rig, 'P0') == [], 'the same ACKed cancellation must not repeat P0 after 31 minutes'
    assert _pages(rig, 'P1') == [], 'same broker-cancellation incident must not downgrade into a 30-minute P1 reminder'
    current_incident = json.loads(rig.store.control(incident_key))
    assert current_incident.get('p0_sent') is True
    assert current_incident['signature'] == original_incident['signature']
    assert _writes(rig) == before
    # Other incidents and a distinct recovered connection remain deliverable.
    independent = AlertCard('🟠 P1 · 独立合成事件', P1_ORANGE,
                            (('独立证据', 'different-event-body'),))
    rig.alerter.send(rig.clock.now(), 'P1', 'independent synthetic incident',
                     'different-event-body', card=independent)
    assert any('different-event-body' in body for _, body in _pages(rig, 'P1'))
    rig.alerter.send(rig.clock.now(), 'INFO', 'IB Gateway responding', 'synthetic recovered heartbeat')
    assert any('IBKR 已恢复应答' in title for title, _ in rig.channel.messages)


def test_B22_item9_cancel_replay_and_restart_never_rehang_or_change_booked_position(tmp_path):
    rig, pid = _pending_three(tmp_path)
    adapter, trade = _broker_cancel(rig, pid, shape='prices_reset')
    before = _writes(rig)
    entry_position = dict(rig.store.position(pid))
    old_ref = rig.store.protection(pid)['intent_id']
    for _ in range(3):
        _deliver(rig, adapter, trade)
        _drive(rig)
    assert _writes(rig) == before
    assert dict(rig.store.position(pid)) == entry_position
    assert rig.store.protection(pid)['intent_id'] == old_ref
    path = rig.store.path
    rig.store.close()
    rig.store = Store(path, partial(compute_levels, rig.cfg))
    rig.channel = MemoryChannel()
    rig.alerter = Alerter([rig.channel], synchronous=True)
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'restarted-reports')
    try:
        rig.engine.startup()
    except StartupError:
        pass  # Fail-closed startup is an allowed outcome for the still-open cancelled position.
    assert _writes(rig) == before
    assert dict(rig.store.position(pid)) == entry_position
    assert rig.store.protection(pid)['intent_id'] == old_ref
    assert rig.store.control('frozen:AAPL')


def _assert_no_release_after_wrong_identity(rig, pid):
    row = rig.store.protection(pid)
    assert rig.store.intent(row['intent_id'])['hold_reason']
    rig.broker.orders[row['intent_id']].status = 'Cancelled'
    manual = OrderRequest('external-item9-manual-flat', 'AAPL', Side.SELL, 3, 100.0, Purpose.TP1)
    rig.broker._register(manual)
    rig.broker.fill(manual.intent_id, emit=False)
    rig.advance(seconds=61)
    assert rig.broker.positions_fresh() == [] and rig.broker.open_orders() == []
    before = _writes(rig)
    rig.store.set_control(rig.clock.now(), 'release:AAPL', json.dumps(
        {'operator': 'synthetic-operator', 'reason': 'synthetic flat proof does not repair wrong native identity'}))
    rig.engine._apply_releases(rig.clock.now())
    assert rig.store.position(pid)['status'] == 'OPEN'
    assert rig.store.flat_point('AAPL') is None
    assert rig.store.control('frozen:AAPL')
    assert _writes(rig) == before


@pytest.mark.parametrize('wrong', ['account', 'orderId', 'permId', 'conId', 'clientId', 'symbol'])
def test_B22_item9_terminal_foreign_hard_identity_cannot_end_protection_or_authorize_release(tmp_path, wrong):
    rig, pid = _pending_three(tmp_path)
    before = _writes(rig)
    adapter, trade = _wire(rig, pid, raw='Cancelled', code=202, reason=REASON, wrong=wrong)
    _deliver(rig, adapter, trade)
    row = rig.store.protection(pid)
    assert row['state'] == 'UNKNOWN' and rig.store.intent(row['intent_id'])['hold_reason']
    assert _pages(rig, 'P0')
    assert not any('券商已取消保护单' in title for title, _ in _pages(rig))
    assert _writes(rig) == before
    _assert_no_release_after_wrong_identity(rig, pid)


@pytest.mark.parametrize('wrong', ['account', 'orderId', 'permId', 'conId', 'clientId', 'auxPrice'])
def test_B22_item9_active_native_identity_mismatch_still_P0_blocks_release_and_writes(tmp_path, wrong):
    rig, pid = _pending_three(tmp_path)
    before = _writes(rig)
    adapter, trade = _wire(rig, pid, raw='PreSubmitted', wrong=wrong)
    _deliver(rig, adapter, trade, send_error=False)
    row = rig.store.protection(pid)
    assert row['state'] == 'UNKNOWN' and rig.store.intent(row['intent_id'])['hold_reason']
    assert _pages(rig, 'P0')
    assert not any('券商已取消保护单' in title for title, _ in _pages(rig))
    assert _writes(rig) == before
    _assert_no_release_after_wrong_identity(rig, pid)


def test_B22_item9_planned_final_share_cancel_202_normal_reason_keeps_real_L7_TP_flow(tmp_path):
    rig, pid = _pending_three(tmp_path)
    rig.broker.protection_auto_status = True
    # Complete first-submit acknowledgement through the actual FakeBroker
    # queue and Engine.tick consumer before testing the normal L7 workflow.
    rig.broker.status(rig.store.protection(pid)['intent_id'], 'Submitted')
    rig.tick()
    _drive(rig)
    assert rig.store.protection(pid)['state'] == 'WORKING'
    # The real plan already divides initial quantity 3 into 1/1/remainder.
    # Let the normal 30-second exit scheduler observe each new TP price.
    for purpose, level in [(Purpose.TP1, 'tp1'), (Purpose.TP2, 'tp2')]:
        rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
        rig.px('AAPL', rig.store.position(pid)[level] + 0.10)
        rig.tick()
        _drive(rig)
        [tp] = [request for request in rig.broker.sent if request.purpose is purpose]
        assert tp.quantity == 1
        rig.broker.fill(tp.intent_id)
        rig.advance(seconds=1)
        rig.tick()
        _drive(rig)
    assert rig.store.position(pid)['qty_open'] == 1
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    rig.px('AAPL', rig.store.position(pid)['tp3'] + 0.10)
    rig.tick()
    _drive(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'PROTECT_CANCEL_PENDING'
    # The dedicated GTC path persists cancellation in the protection row;
    # its intent stays SENT until terminal evidence (both frozen baselines).
    assert rig.store.intent(row['intent_id'])['status'] == IntentStatus.SENT
    assert row['intent_id'] in rig.broker.cancels
    rig.channel.messages.clear()
    # Ordinary requested cancellation is not a broker refusal, even with 202.
    rig.broker.orders[row['intent_id']].status = 'Cancelled'
    adapter, trade = _wire(rig, pid, raw='Cancelled', code=202, reason='Order Canceled - reason')
    _deliver(rig, adapter, trade)
    _drive(rig)
    assert not any('券商已取消保护单' in title or '券商拒绝了保护单' in title for title, _ in _pages(rig))
    assert not rig.store.control('frozen:AAPL')
    assert not rig.store.intent(row['intent_id'])['hold_reason']
    [tp3] = [request for request in rig.broker.sent if request.purpose is Purpose.TP3]
    assert tp3.quantity == rig.store.position(pid)['qty_open'] == 1
    assert len(rig.broker.sent_protections) == 1
    rig.broker.fill(tp3.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    _drive(rig)
    assert rig.store.position(pid)['status'] == 'CLOSED'
    assert rig.store.position(pid)['qty_open'] == 0
    assert rig.broker.open_orders() == []


@pytest.mark.parametrize('failed_source', ['positions', 'executions'])
def test_B22_item9_fresh_working_only_is_not_complete_three_source_quantity_evidence(
        tmp_path, monkeypatch, failed_source):
    rig, pid = _pending_three(tmp_path)
    original_orders = rig.broker.open_orders
    original_positions = rig.broker.positions_fresh
    seen = {'working': 0, 'positions': 0}

    def fresh_working():
        seen['working'] += 1
        return original_orders()

    def positions():
        seen['positions'] += 1
        if failed_source == 'positions':
            raise BrokerError('synthetic positions proof unavailable after a fresh working reply')
        return original_positions()

    monkeypatch.setattr(rig.broker, 'open_orders', fresh_working)
    monkeypatch.setattr(rig.broker, 'positions_fresh', positions)
    rig.broker.fail_executions = failed_source == 'executions'
    _broker_cancel(rig, pid)
    before = _writes(rig)
    _drive(rig)
    assert seen['working'] > 0 and seen['positions'] > 0
    assert rig.engine._working_at == rig.clock.now(), 'fresh working reply really occurred before later proof failed'
    pages = _pages(rig, 'P0')
    assert pages
    exact = ('持仓数量=3 股', '保护单已确认=0 股', '未保护=3 股')
    assert not any(all(text in body for text in exact) for _, body in pages), (
        'a fresh working timestamp must not pretend the failed position/execution proof is complete', pages)
    assert any('待核实' in body for _, body in pages)
    assert _writes(rig) == before
    # Restore the actual missing source, then obtain complete broker evidence.
    monkeypatch.setattr(rig.broker, 'positions_fresh', original_positions)
    rig.broker.fail_executions = False
    rig.channel.messages.clear()
    rig.advance(seconds=1)
    rig.engine._protection_progress(rig.clock.now())
    _drive(rig)
    assert original_orders() == []
    assert [(p.symbol, p.quantity) for p in original_positions()] == [('AAPL', 3)]
    assert any(all(text in body for text in exact) for _, body in _pages(rig, 'P0')), (
        'after complete three-source proof the cancelled protection must report accurate coverage', _pages(rig))
    row = rig.store.protection(pid)
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 0
    assert rig.store.position(pid)['qty_open'] == 3
    assert _writes(rig) == before


MULTICARD_REASON = (
    'Order canceled - reason: We cannot accept your stop order. BEGIN-ORIGINAL\n'
    + '原文🍎🚫12345\n' * 210
    + 'END-ORIGINAL-完整保留'
)


def _utf16_units(text):
    return len(text.encode('utf-16-le')) // 2


class _CardMemory(MemoryChannel):
    """Local remote-channel double: preserve actual embed payload and ACK."""
    def __init__(self, *, fail_attempts=()):
        super().__init__()
        self.fail_attempts = set(fail_attempts)
        self.attempts = []
        self.accepted = []

    def send_card(self, card):
        payload = card.as_embed()
        self.attempts.append(payload)
        if len(self.attempts) in self.fail_attempts:
            raise RuntimeError('synthetic remote transport failure')
        self.accepted.append(payload)
        self.send(card.title, card.as_plain_text())


def _card_channel(rig, *, fail_attempts=()):
    channel = _CardMemory(fail_attempts=fail_attempts)
    rig.channel = channel
    rig.alerter = Alerter([channel], synchronous=True)
    rig.engine.alerter = rig.alerter
    return channel


def _reason_parts(payloads):
    parts = []
    for payload in payloads:
        for field in payload['fields']:
            if '券商原因原文' in field['name']:
                ordinal = re.search(r'(\d+)/(\d+)', field['name'])
                number, total = (map(int, ordinal.groups()) if ordinal else (1, 1))
                parts.append((number, total, field['value']))
    return parts


def _assert_complete_reason_payloads(payloads, reason):
    parts = _reason_parts(payloads)
    assert len(parts) >= 2, 'a >900 UTF16 reason must be visible across multiple real remote cards'
    assert len({number for number, _, _ in parts}) == len(parts), 'already ACKed reason cards must not duplicate'
    total = parts[0][1]
    assert all(count == total for _, count, _ in parts)
    assert sorted(number for number, _, _ in parts) == list(range(1, total + 1))
    assert all(_utf16_units(value) <= 900 for _, _, value in parts)
    assert ''.join(value for _, _, value in sorted(parts)) == reason
    return parts


def _restart_existing_cancel(rig, tmp_path, pid):
    before = dict(rig.store.position(pid))
    old = dict(rig.store.protection(pid))
    writes = _writes(rig)
    path = rig.store.path
    rig.store.close()
    rig.store = Store(path, partial(compute_levels, rig.cfg))
    rig.channel = MemoryChannel()
    rig.alerter = Alerter([rig.channel], synchronous=True)
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'terminal-restart-reports')
    try:
        rig.engine.startup()
    except StartupError:
        pass
    assert dict(rig.store.position(pid)) == before
    assert rig.store.protection(pid)['intent_id'] == old['intent_id']
    assert rig.store.protection(pid)['state'] == old['state']
    assert rig.store.control('frozen:AAPL')
    assert _writes(rig) == writes


def test_B22_item9_cleared_terminal_then_complete_same_tuple_keeps_manual_hold_and_cancel_provenance(tmp_path):
    rig, pid = _pending_three(tmp_path)
    _broker_cancel(rig, pid, shape='metadata_cleared')
    row = rig.store.protection(pid)
    ref = row['intent_id']
    hold = rig.store.intent(ref)['hold_reason']
    assert row['state'] == 'UNKNOWN' and hold
    assert row['pending_max_remaining'] == 3
    _assert_cancel_remote(rig, pid, REASON)
    writes = _writes(rig)
    adapter, trade = _wire(rig, pid, raw='Cancelled', code=202, reason=REASON)
    _deliver(rig, adapter, trade)
    _drive(rig)
    latest = rig.store.protection(pid)
    assert latest['state'] == 'UNKNOWN'
    assert latest['intent_id'] == ref and latest['pending_max_remaining'] == 3
    assert rig.store.intent(ref)['hold_reason'] == hold
    _assert_cancel_remote(rig, pid, REASON)
    assert _writes(rig) == writes
    _assert_no_release_after_wrong_identity(rig, pid)


def test_B22_item9_real_cancel_action_includes_exact_operator_warning_and_numbered_steps(tmp_path):
    rig, pid = _pending_three(tmp_path)
    _broker_cancel(rig, pid)
    pages = [(title, body) for title, body in _pages(rig, 'P0') if '券商已取消保护单' in title]
    assert pages
    assert any('不要点击 TWS 的重新提交/IBALGO 建议' in body for _, body in pages)
    assert any(all(text in body for text in ('1）', '2）', '3）', 'Ctrl+C', 'bid', '限价', '持仓', '工作'))
               for _, body in pages)
    assert any('release AAPL' in body and '身份' in body and '证据' in body for _, body in pages)
    assert rig.store.control('frozen:AAPL')


def test_B22_item9_reason_above_nine_hundred_UTF16_units_is_exact_in_remote_embed_segments(tmp_path):
    assert _utf16_units(MULTICARD_REASON) > 1800
    rig, pid = _pending_three(tmp_path)
    channel = _card_channel(rig)
    _broker_cancel(rig, pid, reason=MULTICARD_REASON)
    _assert_complete_reason_payloads(channel.accepted, MULTICARD_REASON)
    assert all('P0' in payload['title'] for payload in channel.accepted)
    assert rig.store.control('frozen:AAPL')


@pytest.mark.parametrize('failed_part', [1, 2], ids=['first-card-failed', 'primary-ACK-reason-part-failed'])
def test_B22_item9_partial_remote_ACK_does_not_mute_unacknowledged_reason_and_retry_is_exact(tmp_path, failed_part):
    rig, pid = _pending_three(tmp_path)
    channel = _card_channel(rig, fail_attempts=[failed_part])
    ref = rig.store.protection(pid)['intent_id']
    rig.broker.orders[ref].status = 'Cancelled'
    adapter, trade = _wire(rig, pid, raw='Cancelled', code=202, reason=MULTICARD_REASON)
    # One real native callback per attempt; an immediate duplicate error must
    # not silently heal this deliberately unacknowledged delivery before we inspect it.
    _deliver(rig, adapter, trade, send_error=False)
    assert len(channel.attempts) >= 2
    first_parts = _reason_parts(channel.accepted)
    assert len(first_parts) < first_parts[0][1] if first_parts else not channel.accepted
    raw = rig.store.control(f'protect_incident:{pid}:coverage')
    assert raw
    assert json.loads(raw).get('p0_sent') is not True
    writes = _writes(rig)
    _deliver(rig, adapter, trade, send_error=False)
    _assert_complete_reason_payloads(channel.accepted, MULTICARD_REASON)
    assert json.loads(rig.store.control(f'protect_incident:{pid}:coverage')).get('p0_sent') is True
    attempts = len(channel.attempts)
    _deliver(rig, adapter, trade, send_error=False)
    assert len(channel.attempts) == attempts
    assert _pages(rig, 'P1') == []
    assert _writes(rig) == writes


@pytest.mark.parametrize('raw,code,reason', [
    ('Cancelled', 202, 'Order canceled by system: synthetic rule'),
    ('Inactive', 0, 'Broker has cancelled the protective order'),
    ('Cancelled', 10147, 'Synthetic broker terminal reason outside 201 and 202'),
])
def test_B22_item9_broad_terminal_reasons_freeze_with_exact_evidence_and_remain_non_rehang_on_restart(
        tmp_path, raw, code, reason):
    rig, pid = _pending_three(tmp_path)
    _broker_cancel(rig, pid, raw=raw, code=code, reason=reason)
    _drive(rig)
    _assert_cancel_remote(rig, pid, reason)
    exact = ('持仓数量=3 股', '保护单已确认=0 股', '未保护=3 股')
    assert any(all(text in body for text in exact) for _, body in _pages(rig, 'P0'))
    writes = _writes(rig)
    rig.advance(minutes=31)
    rig.engine._hard_protection_deadlines(rig.clock.now())
    _drive(rig)
    assert _pages(rig, 'P1') == []
    assert rig.store.control('frozen:AAPL')
    assert rig.store.position(pid)['status'] == 'OPEN' and rig.store.position(pid)['qty_open'] == 3
    assert _writes(rig) == writes
    _restart_existing_cancel(rig, tmp_path, pid)


@pytest.mark.parametrize('shape,wrong', [('metadata_cleared', None), ('complete', 'perm_missing')],
                         ids=['first-terminal-cleared-fields', 'first-terminal-zero-permId'])
def test_B22_item9_immediate_terminal_without_prior_binding_displays_fact_but_grants_no_authority(tmp_path, shape, wrong):
    rig, pid = _pending_three(tmp_path, skip_binding=True)
    row = rig.store.protection(pid)
    ref = row['intent_id']
    assert rig.store.control('protect_native_contract:' + ref) is None
    writes = _writes(rig)
    rig.broker.orders[ref].status = 'Cancelled'
    adapter, trade = _wire(rig, pid, raw='Cancelled', code=202, reason=REASON, shape=shape, wrong=wrong)
    _deliver(rig, adapter, trade)
    _drive(rig)
    latest = rig.store.protection(pid)
    assert latest['state'] == 'UNKNOWN'
    assert latest['pending_max_remaining'] == 3, 'partial identity is not authority to declare the old GTC capacity zero'
    assert rig.store.intent(ref)['hold_reason']
    _assert_cancel_remote(rig, pid, REASON)
    assert _writes(rig) == writes
    _assert_no_release_after_wrong_identity(rig, pid)



def test_B22_item9_failed_reason_part_then_real_external_SELL_has_independent_remote_provenance(tmp_path):
    rig, pid = _pending_three(tmp_path)
    channel = _card_channel(rig, fail_attempts=[2])
    ref = rig.store.protection(pid)['intent_id']
    rig.broker.orders[ref].status = 'Cancelled'
    adapter, trade = _wire(rig, pid, raw='Cancelled', code=202, reason=MULTICARD_REASON)
    _deliver(rig, adapter, trade, send_error=False)
    original_acked = list(channel.accepted)
    first_parts = _reason_parts(original_acked)
    assert first_parts and len(first_parts) < first_parts[0][1]
    assert json.loads(rig.store.control(f'protect_incident:{pid}:coverage')).get('p0_sent') is not True

    # An account-visible external SELL is a new event, not another terminal
    # callback. The fresh three-source query must discover it through the real
    # progress path; no incident flags or row phases are edited by the fixture.
    external = OrderRequest('external-item9-independent-seller', 'AAPL', Side.SELL,
                            1, 120.0, Purpose.TP1)
    rig.broker._register(external)
    writes = _writes(rig)
    start = len(channel.accepted)
    _drive(rig)
    external_cards = [payload for payload in channel.accepted[start:]
                      if '托管标的出现外部卖单' in payload['title']]
    assert external_cards, 'the independent account-visible SELL must receive its own real remote P0'
    assert all('P0' in payload['title'] for payload in external_cards)
    assert all(not any('券商原因原文' in field['name'] for field in payload['fields'])
               for payload in external_cards)
    assert all(MULTICARD_REASON not in json.dumps(payload, ensure_ascii=False)
               and 'BEGIN-ORIGINAL-MULTICARD' not in json.dumps(payload, ensure_ascii=False)
               for payload in external_cards)
    assert rig.store.control('frozen:AAPL')
    assert _writes(rig) == writes, 'the independent external SELL must not authorize a new broker order'

    # Real terminal replay still retries the unacknowledged cancellation text.
    # The complete quantity proof is a separate material update and may create
    # a fresh P0 batch; aggregate its original-text ordinals without claiming
    # that an earlier ACKed batch has been erased or that material cards dedupe.
    _deliver(rig, adapter, trade, send_error=False)
    assert channel.accepted[:len(original_acked)] == original_acked
    cancel_cards = [payload for payload in channel.accepted
                    if '券商已取消保护单' in payload['title']]
    parts = _reason_parts(cancel_cards)
    assert parts and all(_utf16_units(value) <= 900 for _, _, value in parts)
    totals = {total for _, total, _ in parts}
    assert len(totals) == 1
    total = totals.pop()
    by_number = {}
    for number, _, value in parts:
        assert number not in by_number or by_number[number] == value
        by_number[number] = value
    assert sorted(by_number) == list(range(1, total + 1))
    assert ''.join(by_number[number] for number in range(1, total + 1)) == MULTICARD_REASON
    assert json.loads(rig.store.control(f'protect_incident:{pid}:coverage')).get('p0_sent') is True
    # Query backoff is its own event, with its own single P1. It neither
    # downgrades the cancellation nor inherits the broker's raw reason.
    limited = [(title, body) for title, body in _pages(rig, 'P1')
               if '券商查询已限速' in title]
    assert len(limited) <= 1
    assert all('AAPL' in title and '1、2、4、8、16、30' in body
               and MULTICARD_REASON not in body and 'BEGIN-ORIGINAL-MULTICARD' not in body
               for title, body in limited)
    assert _pages(rig, 'P1') == limited and _writes(rig) == writes


def test_B22_item9_real_complete_working_recovery_retires_terminal_suppression_before_new_crossing(tmp_path, monkeypatch):
    rig, pid = _pending_three(tmp_path)
    ref = rig.store.protection(pid)['intent_id']
    writes = _writes(rig)
    # This is an ordinary complete terminal callback, with no manual identity
    # hold. A later exact native working callback plus three complete queries
    # can correct that hint; the fixture does not resubmit the protection.
    _broker_cancel(rig, pid, code=0, reason='Synthetic ordinary terminal status later corrected by broker')
    assert _pages(rig, 'P0')
    assert not rig.store.intent(ref)['hold_reason']
    assert rig.store.protection(pid)['pending_qty'] == 3
    queried = []
    for name in ('open_orders', 'positions_fresh', 'executions_today'):
        original_query = getattr(rig.broker, name)
        def record_query(_name=name, _query=original_query):
            queried.append(_name)
            return _query()
        monkeypatch.setattr(rig.broker, name, record_query)

    rig.broker.status(ref, 'Submitted')
    adapter, trade = _wire(rig, pid, raw='Submitted')
    _deliver(rig, adapter, trade, send_error=False)
    _drive(rig)
    recovered = rig.store.protection(pid)
    assert set(('open_orders', 'positions_fresh', 'executions_today')).issubset(queried)
    assert recovered['state'] == 'WORKING' and recovered['intent_id'] == ref
    assert recovered['confirmed_remaining'] == recovered['pending_max_remaining'] == 3
    assert recovered['pending_qty'] is None and recovered['gap_a_started_at'] is None
    assert not rig.store.intent(ref)['hold_reason']
    assert rig.store.conn.execute(
        "SELECT COUNT(*) FROM audit WHERE kind='PROTECTION_INCIDENT_RESOLVED'").fetchone()[0] >= 1
    assert any('券商保护事件已解除' in title for title, _ in rig.channel.messages)
    assert _writes(rig) == writes

    before = len(rig.channel.messages)
    # At the original stop remains above B20's old limit and is a legitimate
    # new crossing on both baseline contracts. Tick observes the actual quote
    # independently of the symbol's continued entry freeze.
    rig.px('AAPL', float(recovered['trigger_price']))
    rig.tick()
    new_pages = [(title, body) for title, body in rig.channel.messages[before:] if 'P1' in title]
    assert any('已到止损价' in title and f'orderRef={ref}' in body for title, body in new_pages)
    assert _writes(rig) == writes, 'new crossing monitoring must not submit, modify or cancel broker orders'



def test_B22_item9_capture_crash_after_stop_commit_already_persists_identity_hold_and_blocks_release(tmp_path, monkeypatch):
    import sqlite3

    rig, pid = _pending_three(tmp_path)
    ref = rig.store.protection(pid)['intent_id']
    _broker_cancel(rig, pid)
    assert rig.store.intent(ref)['status'] == IntentStatus.CANCEL_SEEN
    assert not rig.store.intent(ref)['hold_reason']
    writes = _writes(rig)
    adapter, trade = _wire(rig, pid, raw='Cancelled', code=202, reason=REASON, wrong='account')
    capture = getattr(rig.engine, '_protection_capture_terminal', None)
    if capture is not None:
        class SimulatedProcessCrash(BaseException):
            pass

        def crash_after_capture(*args, **kwargs):
            capture(*args, **kwargs)
            # Stop immediately after capture returns, before Engine can call
            # the deferred identity/metadata hold path. This local exception
            # simulates process loss without running a subprocess or os._exit.
            raise SimulatedProcessCrash('synthetic crash after terminal stop transaction committed')

        with monkeypatch.context() as scoped:
            scoped.setattr(rig.engine, '_protection_capture_terminal', crash_after_capture)
            with pytest.raises(SimulatedProcessCrash):
                _deliver(rig, adapter, trade, send_error=False)
    else:
        # Frozen B20/B22 do not have this newly introduced capture boundary.
        # Verify their real native mismatch fail-closed path without importing
        # a new API, inventing a hold, or deleting a persisted control.
        _deliver(rig, adapter, trade, send_error=False)

    assert not rig.store.conn.in_transaction
    # A separate SQLite connection proves the hold is durable, rather than
    # an in-memory flag or an uncommitted update on the interrupted connection.
    with sqlite3.connect(rig.store.path) as persisted:
        persisted.row_factory = sqlite3.Row
        held = persisted.execute('SELECT * FROM intents WHERE intent_id=?', (ref,)).fetchone()
        stopped = persisted.execute('SELECT * FROM protections WHERE position_id=?', (pid,)).fetchone()
        assert held['hold_reason'], 'the terminal stop transaction must already revoke prior CANCEL_SEEN release authority'
        assert held['status'] == IntentStatus.UNKNOWN
        assert stopped['state'] in {'UNKNOWN', 'REJECTED'}
        assert stopped['intent_id'] == ref
    assert rig.store.control('frozen:AAPL')
    assert _writes(rig) == writes
    _restart_existing_cancel(rig, tmp_path, pid)
    assert rig.store.intent(ref)['hold_reason'] and rig.store.intent(ref)['status'] == IntentStatus.UNKNOWN
    _assert_no_release_after_wrong_identity(rig, pid)

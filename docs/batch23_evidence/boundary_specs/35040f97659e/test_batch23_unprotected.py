"""B23: a TP price pause is independent of enabling broker STP protection.

Three isolated functional regressions on stable batch22a interfaces. All state
is TEMP/FakeBroker, native callbacks are local doubles, and no network/broker
connection or runtime/main loop is used. Existing B23 specs stay independent.
"""
from __future__ import annotations

import json
import socket
from decimal import Decimal, ROUND_UP
from types import SimpleNamespace

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine
from v6lite.models import IntentStatus, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig
from v6lite.tests.test_batch22_item9_broker_cancel import (
    _card_channel, _reason_parts, _pending_three, _wire, _drive,
)

TP_PURPOSES = {Purpose.TP1, Purpose.TP2, Purpose.TP3}
REASON = ('Order rejected - reason: Your order price is outside the allowed price range. '
          'Synthetic non-protected TP diagnostic; no live account/order. '
          + '原始原因完整保留🍎 price constraint details; ' * 45
          + 'END-UNPROTECTED-ORIGINAL-REASON')


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import asyncio
    policy = asyncio.get_event_loop_policy()
    try: loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    from ib_insync import IB
    from ib_insync.util import getLoop
    assert getLoop() is loop
    def forbidden(*args, **kwargs):
        raise AssertionError('B23 unprotected tests must never connect to a broker/network')
    async def forbidden_async(*args, **kwargs): forbidden(*args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _writes(rig):
    return tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections,
                                  rig.broker.modified_protections, rig.broker.cancels))


def _unprotected(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=False), risk_per_trade_pct=.0015)
    def no_market(*args, **kwargs): raise AssertionError('MKT forbidden')
    rig.broker.submit_market = no_market
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    assert pos['qty_open'] == 3 and not rig.cfg.protection.enabled
    assert rig.store.protection(pos['position_id']) is None
    _card_channel(rig)
    return rig, pos['position_id']


def _capacity_without_STP(rig, pid):
    assert not rig.cfg.protection.enabled and rig.store.protection(pid) is None
    assert not rig.broker.sent_protections and not rig.broker.modified_protections
    assert not rig.store.conn.execute("SELECT 1 FROM intents WHERE purpose='PROTECT'").fetchone()
    by_ref = {}
    for intent in rig.store.active_intents_for_position(pid):
        if intent['side'] != 'SELL': continue
        assert intent['purpose'] != Purpose.PROTECT and intent['limit_price'] > 0
        possible = max(0, intent['quantity'] - rig.store.intent_filled_qty(intent['intent_id']))
        by_ref[intent['intent_id']] = possible
    for order in rig.broker.open_orders():
        if order.symbol == 'AAPL' and order.side is Side.SELL:
            assert order.order_type == 'LMT' and order.limit_price > 0
            by_ref[order.intent_id] = max(by_ref.get(order.intent_id, 0), int(order.remaining))
    held = {p.symbol: int(p.quantity) for p in rig.broker.positions_fresh()}
    assert sum(by_ref.values()) <= held['AAPL'] == rig.store.position(pid)['qty_open'] == 3


def _queue_real_TP(rig, pid):
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    rig.px('AAPL', float(rig.store.position(pid)['tp1']) + .20)
    rig.engine._evaluate_exits(rig.clock.now())
    assert rig.store.stage(pid, Purpose.TP1)['status'] == 'PENDING'
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    rows = list(rig.store.conn.execute("SELECT * FROM intents WHERE position_id=? AND purpose='TP1'", (pid,)))
    assert len(rows) == 1, 'the real old ordinary TP submit path must create its intent'
    assert rows[0]['quantity'] == 1 and rows[0]['limit_price'] > 0
    _capacity_without_STP(rig, pid)
    return rows[0]['intent_id']


def _native_refusal(rig, ref, *, reason=REASON):
    physical = rig.broker.orders[ref]
    req, oid = physical.req, physical.broker_order_id
    contract = SimpleNamespace(symbol=req.symbol, conId=rig.broker.con_ids[req.symbol],
                               secType='STK', currency='USD', exchange='SMART')
    order = SimpleNamespace(orderRef=ref, orderId=oid, permId=oid + 50_000,
        account=rig.cfg.account, clientId=rig.cfg.client_id, action='SELL',
        orderType='LMT', tif='DAY', outsideRth=False, totalQuantity=req.quantity,
        auxPrice=0.0, lmtPrice=req.limit_price, usePriceMgmtAlgo=False)
    status = SimpleNamespace(status='Inactive', filled=physical.filled, remaining=physical.remaining,
        orderId=oid, permId=oid + 50_000, clientId=rig.cfg.client_id, mktCapPrice=0.0)
    trade = SimpleNamespace(order=order, contract=contract, orderStatus=status,
        log=[SimpleNamespace(time=rig.clock.now(), status='Inactive', errorCode=201, message=reason)],
        fills=[], advancedError='')
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = SimpleNamespace(trades=lambda: [trade], openTrades=lambda: [trade], isConnected=lambda: True)
    adapter._trades[ref] = trade
    adapter._contracts[req.symbol] = contract
    physical.status = 'Inactive'
    adapter._on_status(trade)
    events = adapter.drain_events()
    assert events, 'complete native asynchronous TP refusal must be delivered'
    for event in events: rig.engine._on_urgent_broker_event(event)


def _assert_pause_remote_and_restart(rig, pid, ref, tmp_path, code, *, reason=REASON):
    parts = _reason_parts(rig.channel.accepted)
    assert parts and ''.join(text for _, _, text in sorted(parts)) == reason, \
        'unprotected TP refusal also needs the complete original reason in real P0 remote cards'
    assert any('P0' in title and '价格' in title for title, _ in rig.channel.messages)
    raw = rig.store.control(f'tp_pause:{pid}')
    assert raw, 'TP pause must not depend on a protection row existing'
    pause = json.loads(raw)
    assert pause['position_id'] == pid and pause['symbol'] == 'AAPL' and pause['intent_id'] == ref
    assert pause['stage'] == str(Purpose.TP1) and pause['pause_id'] and pause['reason'] == reason
    assert pause['error_code'] == code
    assert rig.store.intent(ref)['limit_price'] > 0 and not rig.store.intent(ref)['hold_reason']
    _capacity_without_STP(rig, pid)
    writes = _writes(rig)
    position = dict(rig.store.position(pid))
    rig.advance(minutes=31); rig.tick()
    assert rig.store.control(f'tp_pause:{pid}') == raw and _writes(rig) == writes
    assert dict(rig.store.position(pid)) == position
    _capacity_without_STP(rig, pid)
    # A successful normal restart; arbitrary StartupError cannot prove a pause.
    path, levels = rig.store.path, rig.store._levels_fn
    rig.store.close(); rig.store = Store(path, levels)
    rig.channel = MemoryChannel(); rig.alerter = Alerter([rig.channel], synchronous=True)
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'restart-reports')
    rig.engine.startup()
    assert not rig.engine.cfg.protection.enabled and rig.store.protection(pid) is None
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1); rig.tick()
    assert rig.store.control(f'tp_pause:{pid}') == raw and _writes(rig) == writes
    assert dict(rig.store.position(pid)) == position
    _capacity_without_STP(rig, pid)
    # Operator request cannot release a still-held three-share position, clear
    # its TP pause, or turn off the existing complete-evidence release gates.
    rig.store.set_control(rig.clock.now(), 'release:AAPL', json.dumps(
        {'operator': 'John', 'reason': 'synthetic still-held position cannot be released'}))
    rig.engine._apply_releases(rig.clock.now())
    assert rig.store.position(pid)['status'] == 'OPEN' and rig.store.position(pid)['qty_open'] == 3
    assert rig.store.flat_point('AAPL') is None and rig.store.control(f'tp_pause:{pid}') == raw
    assert _writes(rig) == writes
    _capacity_without_STP(rig, pid)


def test_B23_unprotected_direct_price_rejection_persists_full_P0_pause_without_enabling_STP(tmp_path):
    rig, pid = _unprotected(tmp_path)
    rig.broker.fail_next_submit = BrokerError(REASON)
    ref = _queue_real_TP(rig, pid)
    assert rig.store.intent(ref)['status'] == IntentStatus.REJECTED
    assert ref not in rig.broker.orders, 'definitely unsent price rejection is not a working SELL'
    assert rig.store.intent(ref)['note'] == REASON
    _assert_pause_remote_and_restart(rig, pid, ref, tmp_path, 0)
    evidence = [json.loads(row['payload']) for row in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='TP_EVIDENCE' ORDER BY id")]
    matched = [event for event in evidence if event.get('intent_id') == ref]
    assert not any(event.get('phase') == 'send' for event in matched), 'a definite broker rejection is not a successful send'
    assert any(event.get('phase') in {'status', 'definitely_unsent', 'rejected'}
               and event.get('reason') == REASON for event in matched)


def test_B23_unprotected_async_native_price_refusal_persists_full_P0_pause_without_enabling_STP(tmp_path):
    rig, pid = _unprotected(tmp_path)
    ref = _queue_real_TP(rig, pid)
    assert rig.store.intent(ref)['status'] == IntentStatus.SENT
    req = rig.broker.orders[ref].req
    original_price = req.limit_price
    _native_refusal(rig, ref)
    assert rig.store.intent(ref)['limit_price'] == original_price
    assert rig.store.intent(ref)['broker_order_id'] == rig.broker.orders[ref].broker_order_id
    assert rig.store.intent(ref)['perm_id'] == rig.broker.orders[ref].broker_order_id + 50_000
    _assert_pause_remote_and_restart(rig, pid, ref, tmp_path, 201)


def test_B23_corrupt_persisted_pricing_book_cannot_authorize_original_TP_price_or_extra_attempt(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), risk_per_trade_pct=.0015)
    def no_market(*args, **kwargs): raise AssertionError('MKT forbidden')
    rig.broker.submit_market = no_market
    rig.open_aapl(); [pos] = rig.store.open_positions(); pid = pos['position_id']
    assert pos['qty_open'] == 3 and rig.store.protection(pid)['state'] == 'WORKING'
    rig.broker.protection_auto_status = False
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    rig.px('AAPL', float(pos['tp1']) + .20)
    rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue(); rig.engine._drain(rig.clock.now())
    assert rig.store.protection(pid)['state'] == 'REDUCE_PENDING'
    attempts = rig.store.stage(pid, Purpose.TP1)['attempts']
    start = rig.clock.now()
    original_set = rig.store.set_control
    corrupted = []
    def persist_incoherent_pricing(now, key, value):
        if key.startswith('tp_send:') and not corrupted:
            snapshot = json.loads(value)
            assert snapshot['bid'] == float(snapshot['bid_raw'])
            assert snapshot['ask'] == float(snapshot['ask_raw'])
            assert snapshot['calculated_price'] == snapshot['sent_price']
            snapshot['bid'] -= 1.0; snapshot['ask'] -= 1.0
            assert snapshot['bid'] > 0 and snapshot['ask'] >= snapshot['bid']
            assert snapshot['sent_price'] > snapshot['bid']
            corrupted.append(snapshot.copy())
            value = json.dumps(snapshot)
        return original_set(now, key, value)
    # Corrupt the naturally produced auxiliary pricing record during its real
    # local persistence, before _send reads it. No phase/quantity/permission,
    # broker quote, OrderRequest price, or production flag is hand-rewritten.
    monkeypatch.setattr(rig.store, 'set_control', persist_incoherent_pricing)
    rig.broker.confirm_protection_modify(rig.store.protection(pid)['intent_id'])
    for _ in range(2):
        rig.engine._drain(rig.clock.now()); rig.engine.process_protection_priority()
    assert not [r for r in rig.broker.sent if r.purpose in TP_PURPOSES], \
        'equal req/sent/calculated prices do not validate a contradicting bid/raw-book snapshot'
    assert len(corrupted) == 1
    assert rig.store.stage(pid, Purpose.TP1)['attempts'] == attempts
    assert rig.store.control(f'tp_pause:{pid}') is None
    ref = next(iter(rig.store.controls_with_prefix('tp_send:'))).split(':', 1)[1]
    intent = rig.store.intent(ref)
    assert intent is not None and intent['status'] in {IntentStatus.REJECTED, IntentStatus.CANCELLED}
    assert intent['limit_price'] == corrupted[0]['sent_price'], 'failure must not reprice the original request'
    assert intent['broker_order_id'] is None and intent['perm_id'] is None
    row = rig.store.protection(pid)
    assert row['gap_b_started_at'] == start.isoformat()
    if row['state'] == 'TP_NEEDS_RESTORE': rig.tick()
    row = rig.store.protection(pid)
    assert row['state'] == 'RESTORE_PENDING' and row['pending_qty'] == 3
    rig.broker.confirm_protection_modify(row['intent_id'])
    for _ in range(2):
        rig.engine._drain(rig.clock.now()); rig.engine.process_protection_priority()
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.store.protection(pid)['confirmed_remaining'] == rig.store.protection(pid)['pending_max_remaining'] == 3
    assert len(rig.broker.sent_protections) == 1 and not [r for r in rig.broker.sent if r.purpose in TP_PURPOSES]

@pytest.mark.parametrize('reason', ['价格不在监管参考价的允许范围内', '限价偏离当前参考价太多'])
def test_B23_unprotected_affirmative_Chinese_native_price_refusal_keeps_original_reason_and_pause(tmp_path, reason):
    rig, pid = _unprotected(tmp_path)
    ref = _queue_real_TP(rig, pid)
    original_price = rig.store.intent(ref)['limit_price']
    _native_refusal(rig, ref, reason=reason)
    assert rig.store.intent(ref)['limit_price'] == original_price
    _assert_pause_remote_and_restart(rig, pid, ref, tmp_path, 201, reason=reason)
    assert rig.store.intent(ref)['limit_price'] == original_price


def test_B23_release_query_failure_preserves_complete_cause_and_refuses_all_position_writes(tmp_path, monkeypatch):
    from v6lite import __main__ as cli
    rig, pid = _unprotected(tmp_path)
    position = dict(rig.store.position(pid)); writes = _writes(rig)
    monkeypatch.setattr(cli, '_now', rig.clock.now)
    cli.cmd_release(SimpleNamespace(symbol='AAPL', operator='John',
                    reason='synthetic local release evidence check'), rig.store)
    assert rig.store.control('release:AAPL')
    reason = ('Synthetic openOrders proof failed: ' + '保留完整查询失败原因🍎 ' * 45
              + 'END-RELEASE-QUERY-ORIGINAL-CAUSE')
    def unavailable(): raise BrokerError(reason)
    with monkeypatch.context() as failed:
        failed.setattr(rig.broker, 'open_orders', unavailable)
        rig.engine._apply_releases(rig.clock.now())
    refused = [json.loads(row['payload']) for row in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='RELEASE_REFUSED' ORDER BY id")]
    assert refused and any(reason in event.get('reason', '') for event in refused), \
        'durable release refusal must retain the full broker cause, not an 80-character preview'
    assert dict(rig.store.position(pid)) == position and rig.store.flat_point('AAPL') is None
    assert not rig.store.conn.execute('SELECT 1 FROM releases').fetchone()
    assert _writes(rig) == writes
    _capacity_without_STP(rig, pid)


@pytest.mark.parametrize('raw,code,fact', [('Cancelled', 202, '已取消'), ('Inactive', 201, '已拒绝')])
def test_B23_native_PROTECT_cancel_or_rejection_card_uses_one_consistent_known_system_fact(tmp_path, raw, code, fact):
    rig, pid = _pending_three(tmp_path)
    row = rig.store.protection(pid); ref = row['intent_id']
    before = _writes(rig)
    # Deliberately neutral original text: any Chinese cancellation/rejection
    # wording in this card must be the system's fact, never a translated reason.
    reason = 'Synthetic native diagnostic marker. END-B23-FACT-ORIGINAL'
    rig.broker.orders[ref].status = raw
    adapter, trade = _wire(rig, pid, raw=raw, code=code, reason=reason)
    adapter._on_status(trade)
    events = adapter.drain_events(); assert events
    for event in events: rig.engine._on_urgent_broker_event(event)
    _drive(rig)
    cards = [(title, body) for title, body in rig.channel.messages
             if 'P0' in title and '保护单' in title and '券商已' in title]
    assert cards and any('券商' + fact + '保护单' in title for title, _ in cards), cards
    assert any(reason in body for _, body in cards), 'original broker marker must remain untranslated'
    if fact == '已取消':
        assert all('已拒绝' not in title for title, _ in cards)
        assert all('券商已明确拒绝 STP' not in body and '券商已明确拒绝该保护请求' not in body
                   for _, body in cards), 'a known Cancelled/202 fact must not inherit a refusal diagnosis'
        assert any('已回报取消' in body or '已取消' in body for _, body in cards)
    else:
        assert all('已取消' not in title for title, _ in cards)
        assert all('券商已回报取消' not in body for _, body in cards)
        assert any('已拒绝' in body or '已明确拒绝' in body for _, body in cards)
    assert rig.store.control('frozen:AAPL')
    assert rig.store.position(pid)['qty_open'] == 3 and _writes(rig) == before
    assert len(rig.broker.sent_protections) == 1

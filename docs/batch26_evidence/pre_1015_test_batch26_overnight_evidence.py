"""B26 independent opt-in overnight TP evidence contracts, entirely offline.

Yesterday is booked through the real engine, gracefully stopped, and SQLite is
closed/reopened. Today both execution downloads explicitly exclude yesterday.
No production evidence function is mocked. The capacity oracle uses FakeBroker
truth and actual completed query records, never an engine authorization flag.
"""
from __future__ import annotations

import json
import math
import socket
from dataclasses import fields, replace
from datetime import timedelta

import pytest

from v6lite.alerts import Alerter
from v6lite.broker.base import BrokerError
from v6lite.broker.fake import FakeBroker
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine
from v6lite.market_calendar import trading_day
from v6lite.models import FillEvent, IntentStatus, OrderRequest, ProtectionRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et

YESTERDAY = (2026, 10, 13)
TODAY = (2026, 10, 14)
TP = {Purpose.TP1, Purpose.TP2, Purpose.TP3}
TERMINAL = {'Cancelled', 'ApiCancelled', 'Inactive', 'Filled'}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # A Windows asyncio local socketpair may call connect during first SDK
    # import; initialize that local machinery before installing network bans.
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
        raise AssertionError('B26 overnight tests must not connect sockets, TWS or Gateway')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden)


def _protection_config(enabled):
    # This is a test-only, explicitly opted-in config, not an authorization
    # stub. B25 has no new field: attach only that future option to this
    # synthetic frozen-but-not-slotted dataclass so baseline failures remain
    # behavioral assertions, never dataclasses.replace unknown-field errors.
    options = {'enabled': True}
    declared = {field.name for field in fields(ProtectionConfig)}
    if 'overnight_tp_enabled' in declared:
        options['overnight_tp_enabled'] = enabled
    config = ProtectionConfig(**options)
    if 'overnight_tp_enabled' not in declared:
        object.__setattr__(config, 'overnight_tp_enabled', enabled)
    return config


def _writes(rig):
    return tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                         rig.broker.modified_protections, rig.broker.cancels))


def _tp_requests(rig):
    return [request for request in rig.broker.sent if request.purpose in TP
            and request.intent_id.startswith('v6l-')]


def _capture_alerts(rig, monkeypatch):
    original = rig.alerter.send

    def send(now, level, subject, body='', **kwargs):
        card = kwargs.get('card')
        text = '\n'.join((subject, body, card.as_plain_text() if card is not None else ''))
        rig._b26_alerts.append((level, text))
        rig._b26_timeline.append(('alert', level, text))
        return original(now, level, subject, body, **kwargs)

    monkeypatch.setattr(rig.alerter, 'send', send)


def _restore_infos(rig):
    return [text for level, text in rig._b26_alerts
            if level == 'INFO' and '过夜' in text and '止盈' in text and '恢复' in text]


def _overnight_p1(rig):
    return [text for level, text in rig._b26_alerts
            if level == 'P1' and '过夜' in text and ('核验' in text or '证据' in text or '止盈' in text)]


def _today_rows(rig):
    day = trading_day(rig.clock.now()).isoformat()
    return rig.store.conn.execute('SELECT * FROM fills WHERE trading_day>=?', (day,)).fetchall()


def _terminal_absence_proved(rig, ref):
    order = rig.broker.orders.get(ref)
    intent = rig.store.intent(ref)
    if order is None or intent is None or intent['hold_reason'] or order.status not in TERMINAL:
        return False
    # A completed cancellation proof remains valid across a subsequent STP
    # write; a late execution changes native filled and revokes it immediately.
    if rig._b26_queries['released'].get(ref) == order.filled:
        return True
    proof = rig._b26_queries['complete']
    if proof is None or proof['epoch'] != rig._b26_queries['epoch']:
        return False
    required = {row['exec_id'] for row in _today_rows(rig)}
    if (any(item.intent_id == ref for item in proof['orders']) or
            not required.issubset({fill.exec_id for fill in proof['fills']})):
        return False
    fresh_truth = {p.symbol: p.quantity for p in FakeBroker.positions_fresh(rig.broker)}
    if {p.symbol: p.quantity for p in proof['positions']} != fresh_truth:
        return False
    rig._b26_queries['released'][ref] = order.filled
    return True


def _safety(rig, prospective=None):
    """Independent possible SELL maxima: actual P, store S, native T/X.

    Cancellation hints never zero a reserve. Today-only completed evidence can
    settle a *today's* TP, while yesterday's entry stays in persistent history.
    Actual native fills count economically even before the queued DB replay.
    """
    claims = {}

    def claim(ref, symbol, amount):
        old = claims.get(ref, (symbol, 0))
        claims[ref] = (symbol, max(old[1], max(0, int(math.ceil(amount)))))

    for row in rig.store.conn.execute('SELECT * FROM protections'):
        claim(row['intent_id'], row['symbol'], max(row['confirmed_remaining'], row['pending_max_remaining']))
    for ref, order in rig.broker.orders.items():
        req = order.req
        protective = isinstance(req, ProtectionRequest)
        if protective:
            assert req.order_type == 'STP' and req.limit_price is None
        else:
            assert isinstance(req, OrderRequest) and req.limit_price is not None and req.limit_price > 0
        if not protective and req.side is not Side.SELL:
            continue
        if order.status not in TERMINAL:
            possible = order.remaining
            pending = rig.broker.protection_pending_modifications.get(ref)
            if pending is not None:
                possible = max(possible, pending.quantity - order.filled)
            claim(ref, req.symbol, possible)
    for intent in rig.store.conn.execute("SELECT * FROM intents WHERE side='SELL' AND purpose!='PROTECT'"):
        ref = intent['intent_id']
        native = rig.broker.orders.get(ref)
        native_filled = native.filled if native is not None else 0
        left = max(0, int(intent['quantity']) - max(native_filled, rig.store.intent_filled_qty(ref)))
        reached = native is not None or intent['status'] in {
            IntentStatus.SENT, IntentStatus.PARTIAL, IntentStatus.CANCEL_REQUESTED,
            IntentStatus.CANCEL_SEEN, IntentStatus.UNKNOWN}
        terminal = intent['status'] in {IntentStatus.REJECTED, IntentStatus.CANCEL_SEEN,
                                        IntentStatus.CANCELLED, IntentStatus.FILLED}
        if reached and left and (intent['hold_reason'] or not terminal or
                                  not _terminal_absence_proved(rig, ref)):
            claim(ref, intent['symbol'], left)
    if prospective is not None and (isinstance(prospective, ProtectionRequest) or prospective.side is Side.SELL):
        native = rig.broker.orders.get(prospective.intent_id)
        filled = max(rig.store.intent_filled_qty(prospective.intent_id), native.filled if native else 0)
        claim(prospective.intent_id, prospective.symbol, prospective.quantity - filled)
    truth = {p.symbol: p.quantity for p in FakeBroker.positions_fresh(rig.broker)}
    totals = {}
    for symbol, amount in claims.values():
        totals[symbol] = totals.get(symbol, 0) + amount
    for symbol, total in totals.items():
        assert total <= truth.get(symbol, 0), ('S+T+X<=P', symbol, total, truth.get(symbol, 0), claims)
    assert rig._b26_market_calls == []


def _watch_boundaries(rig, monkeypatch):
    rig._b26_queries = {'epoch': 0, 'partial': {}, 'complete': None, 'released': {}, 'calls': []}
    for name in ('open_orders', 'positions_fresh', 'executions_today'):
        original = getattr(rig.broker, name)

        def query(*args, _name=name, _original=original, **kwargs):
            state = rig._b26_queries
            if _name == 'open_orders':
                state['partial'] = {}; state['complete'] = None
            result = _original(*args, **kwargs)
            state['calls'].append((_name, rig.clock.now(), result))
            state['partial'][_name] = result
            if _name == 'executions_today' and all(
                    item in state['partial'] for item in ('open_orders', 'positions_fresh', 'executions_today')):
                part = state['partial']
                state['complete'] = {'epoch': state['epoch'], 'orders': part['open_orders'],
                                     'positions': part['positions_fresh'], 'fills': part['executions_today']}
            return result

        monkeypatch.setattr(rig.broker, name, query)
    for name in ('submit_limit', 'submit_stop', 'modify_protection', 'cancel'):
        original = getattr(rig.broker, name)

        def write(*args, _name=name, _original=original, **kwargs):
            request = args[0] if args and isinstance(args[0], (OrderRequest, ProtectionRequest)) else None
            _safety(rig, request)
            rig._b26_timeline.append(('write', _name, request))
            rig._b26_queries['epoch'] += 1
            try:
                return _original(*args, **kwargs)
            finally:
                _safety(rig)

        monkeypatch.setattr(rig.broker, name, write)


@pytest.fixture
def seed_overnight(tmp_path, monkeypatch):
    rigs = []

    def make(*, enabled=True):
        rig = Rig(tmp_path / ('overnight-' + str(len(rigs))), start=et(9, 50, day=YESTERDAY),
                  protection=_protection_config(enabled), risk_per_trade_pct=.0015)
        rigs.append(rig)
        rig._b26_alerts = []; rig._b26_timeline = []; rig._b26_visible_extra = []
        rig._b26_market_calls = []

        def no_market(*args, **kwargs):
            rig._b26_market_calls.append((args, kwargs))
            raise AssertionError('only protective STP is permitted; exits remain LMT')

        monkeypatch.setattr(rig.broker, 'submit_market', no_market, raising=False)
        rig.start()
        rig.at(et(10, 0, 30, day=YESTERDAY)); rig.tick()
        [entry] = [request for request in rig.broker.sent if request.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        fill = rig.broker.fill(entry.intent_id, qty=3, price=113.13)
        rig.advance(seconds=1); rig.tick()
        [position] = rig.store.open_positions()
        pid = position['position_id']; row = dict(rig.store.protection(pid))
        assert position['qty_open'] == 3 and row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
        assert fill.exec_id in {item['exec_id'] for item in rig.store.conn.execute('SELECT * FROM fills')}
        rig.engine.shutdown(grace_seconds=0)
        [shutdown] = [json.loads(item['payload']) for item in rig.store.conn.execute(
            "SELECT payload FROM audit WHERE kind='SHUTDOWN'")]
        assert shutdown['unresolved'] == [] and shutdown['broker_working'] == []
        assert row['intent_id'] not in rig.broker.cancels
        path, levels = rig.store.path, rig.store._levels_fn
        rig.store.close(); rig.store = Store(path, levels)
        rig.at(et(10, 1, day=TODAY))
        assert trading_day(fill.time) < trading_day(rig.clock.now())
        original_execs = rig.broker.executions_today

        def today_execs():
            day = trading_day(rig.clock.now())
            return [item for item in original_execs() if trading_day(item.time) == day] + list(rig._b26_visible_extra)

        def today_account_execs():
            return [replace(item, intent_id='') for item in today_execs()]

        monkeypatch.setattr(rig.broker, 'executions_today', today_execs)
        monkeypatch.setattr(rig.broker, 'account_executions_today', today_account_execs)
        assert rig.broker.executions_today() == [] and rig.broker.account_executions_today() == []
        _watch_boundaries(rig, monkeypatch)
        rig.channel.messages.clear()
        rig._b26_entry = entry; rig._b26_entry_fill = fill; rig._b26_original = row
        return rig, pid

    yield make
    for rig in rigs:
        rig.alerter.close()
        rig.store.close()


def _restart(rig, tmp_path, monkeypatch, *, reopen=False):
    if reopen:
        path, levels = rig.store.path, rig.store._levels_fn
        rig.alerter.close(); rig.store.close(); rig.store = Store(path, levels)
    rig.alerter = Alerter([rig.channel], synchronous=True)
    _capture_alerts(rig, monkeypatch)
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'today-reports')
    rig.engine.startup(); rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()


def _settle(rig):
    for _ in range(3):
        rig.engine._drain(rig.clock.now())
        rig.engine._protection_progress(rig.clock.now())
        rig.engine._drain(rig.clock.now())


def _attempt_tp(rig, pid):
    rig.advance(seconds=31)
    rig.px('AAPL', float(rig.store.position(pid)['tp1']) + .20)
    rig.engine._protection_progress(rig.clock.now())
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())


def _activate(rig, pid):
    rig.broker.protection_auto_status = False
    _attempt_tp(rig, pid)
    row = rig.store.protection(pid); ref = row['intent_id']
    assert row['state'] == 'REDUCE_PENDING', 'valid opt-in overnight evidence must allow the real TP reduction'
    assert _tp_requests(rig) == [] and rig.broker.orders[ref].remaining == 3
    assert rig.broker.protection_pending_modifications[ref].quantity == 2
    _safety(rig)
    rig.broker.confirm_protection_modify(ref)
    _safety(rig); _settle(rig); _safety(rig)
    [tp] = _tp_requests(rig)
    assert tp.quantity == 1 and tp.purpose is Purpose.TP1
    assert rig.broker.orders[ref].remaining == 2
    assert rig.store.protection(pid)['state'] == 'TP_ACTIVE'
    return tp, ref


def _p1_reason(rig, fragments):
    pages = _overnight_p1(rig)
    assert pages, 'failed overnight evidence must emit its own specific P1, not only a generic P0'
    assert any('AAPL' in text and any(fragment in text for fragment in fragments) for text in pages), pages
    assert _restore_infos(rig) == [], 'a failed contract cannot claim automatic TP was restored'


def test_B26_overnight_opt_in_default_is_declared_and_disabled():
    assert getattr(ProtectionConfig(), 'overnight_tp_enabled', None) is False


def test_B26_opt_in_overnight_TP_waits_for_reduction_ACK_then_cancel_proof_and_restores_same_GTC(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch)
    assert _writes(rig) == before and rig.broker.executions_today() == []
    tp, ref = _activate(rig, pid)
    infos = _restore_infos(rig)
    assert len(infos) == 1
    info = infos[0]
    assert all(part in info for part in ('AAPL', '3', '账本', '当日', rig._b26_entry_fill.exec_id))
    assert '2026-10-13' in info and ('GTC' in info or 'STP' in info)
    tp_write_index = next(index for index, item in enumerate(rig._b26_timeline)
                          if item[0] == 'write' and isinstance(item[2], OrderRequest) and item[2].purpose in TP)
    info_index = next(index for index, item in enumerate(rig._b26_timeline)
                      if item[0] == 'alert' and item[1] == 'INFO' and '过夜' in item[2] and '止盈' in item[2])
    assert info_index < tp_write_index
    rig.advance(seconds=11)
    rig.engine._protection_timers(rig.clock.now()); _settle(rig)
    assert tp.intent_id in rig.broker.cancels
    assert rig.broker.orders[ref].remaining == 2
    assert not rig.broker.protection_pending_modifications
    _safety(rig)
    rig._b26_queries['epoch'] += 1
    rig.broker.confirm_cancel(tp.intent_id)
    _settle(rig)
    pending = rig.broker.protection_pending_modifications[ref]
    assert pending.quantity == 3 and pending.intent_id == ref
    _safety(rig)
    rig.broker.confirm_protection_modify(ref); _settle(rig); _safety(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
    assert row['gap_b_started_at'] is None and row['tp_stage'] is None
    assert len(rig.broker.sent_protections) == 1
    assert (row['intent_id'], row['broker_order_id'], row['perm_id']) == tuple(
        rig._b26_original[key] for key in ('intent_id', 'broker_order_id', 'perm_id'))


def test_B26_overnight_TP1_fill_modifies_only_same_STP_to_breakeven_with_today_fill_evidence(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    tp, ref = _activate(rig, pid)
    fill = rig.broker.fill(tp.intent_id, qty=1, price=rig.broker.quote('AAPL').bid)
    _safety(rig); _settle(rig); _safety(rig)
    assert fill.exec_id in {item.exec_id for item in rig.broker.executions_today()}
    assert rig._b26_entry_fill.exec_id not in {item.exec_id for item in rig.broker.executions_today()}
    pos = rig.store.position(pid)
    assert pos['qty_open'] == 2 and pos['tp1_done'] == 1 and pos['breakeven_stop'] is not None
    pending = rig.broker.protection_pending_modifications.get(ref)
    assert pending is not None and pending.quantity == 2
    assert pending.trigger_price == pytest.approx(pos['breakeven_stop'])
    rig.broker.confirm_protection_modify(ref); _settle(rig); _safety(rig)
    row = rig.store.protection(pid)
    assert row['trigger_price'] == pytest.approx(pos['breakeven_stop'])
    assert row['confirmed_remaining'] == 2 and row['state'] == 'WORKING'
    assert row['intent_id'] == ref and len(rig.broker.sent_protections) == 1


@pytest.mark.parametrize('failure', ['manual_sell_one', 'cancelled_gtc', 'missing_entry_exec',
                                    'unknown_today_exec', 'position_mismatch'])
def test_B26_each_required_overnight_evidence_failure_freezes_TP_with_specific_P1_and_no_auto_repair(
        seed_overnight, tmp_path, monkeypatch, failure):
    rig, pid = seed_overnight()
    ref = rig.store.protection(pid)['intent_id']
    if failure == 'manual_sell_one':
        fill = rig.broker.external_fill('AAPL', Side.SELL, 1, 113.0)
        rig._b26_visible_extra.append(fill); rig.broker.events.clear()
        fragments = ('持仓', '数量', 'qty', '账本')
    elif failure == 'cancelled_gtc':
        rig.broker.orders[ref].status = 'Cancelled'
        fragments = ('保护', '工作单', 'GTC', '取消')
    elif failure == 'missing_entry_exec':
        rig.store.conn.execute('DELETE FROM fills WHERE exec_id=?', (rig._b26_entry_fill.exec_id,))
        fragments = ('入场', 'exec', '账本', '成交')
    elif failure == 'unknown_today_exec':
        rig._b26_visible_extra.append(FillEvent('b26-unknown-today.01.01', '', 'AAPL', Side.BUY,
                                               1, 113.0, rig.clock.now(), con_id=rig._b26_original['con_id']))
        fragments = ('归属', '未知', '外部', '成交')
    else:
        rig.broker.external_positions['AAPL'] = (1.0, 113.0)
        fragments = ('持仓', '数量', '账本')
    # Manual/external actions can themselves leave S>P. This negative contract
    # forbids all automatic writes; it does not call that external state safe.
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch)
    _attempt_tp(rig, pid); _settle(rig)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert rig.store.position(pid)['status'] == 'OPEN'
    assert rig.broker.orders[ref].status == ('Cancelled' if failure == 'cancelled_gtc' else 'Submitted')
    _p1_reason(rig, fragments)


@pytest.mark.parametrize('field', ['account', 'client_id', 'con_id', 'broker_order_id', 'perm_id',
                                  'aux_price', 'order_type', 'tif', 'outside_rth'])
def test_B26_overnight_evidence_never_relaxes_the_original_GTC_strong_identity(
        seed_overnight, tmp_path, monkeypatch, field):
    rig, pid = seed_overnight()
    original = rig.broker.open_orders
    values = {'account': 'DU0000000', 'client_id': 999, 'con_id': 999999,
              'broker_order_id': 999999, 'perm_id': None, 'aux_price': 1.0,
              'order_type': 'STP LMT', 'tif': 'DAY', 'outside_rth': True}

    def orders():
        return [replace(order, **{field: values[field]}) if order.intent_id == rig._b26_original['intent_id']
                else order for order in original()]

    monkeypatch.setattr(rig.broker, 'open_orders', orders)
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    _p1_reason(rig, ('身份', '保护', 'GTC', 'STP', '工作单'))


@pytest.mark.parametrize('corruption', ['entry_perm', 'orphan_intent', 'net_quantity', 'entry_price'])
def test_B26_history_requires_consistent_persisted_entry_identity_and_economic_totals(
        seed_overnight, tmp_path, monkeypatch, corruption):
    rig, pid = seed_overnight()
    eid = rig._b26_entry_fill.exec_id
    if corruption == 'entry_perm':
        rig.store.conn.execute('UPDATE fills SET perm_id=perm_id+1 WHERE exec_id=?', (eid,))
    elif corruption == 'orphan_intent':
        rig.store.conn.execute('UPDATE fills SET intent_id=NULL WHERE exec_id=?', (eid,))
    elif corruption == 'net_quantity':
        rig.store.conn.execute('UPDATE fills SET quantity=2 WHERE exec_id=?', (eid,))
    else:
        rig.store.conn.execute('UPDATE fills SET price=price+1 WHERE exec_id=?', (eid,))
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    _p1_reason(rig, ('入场', '账本', '成交', '数量', '价格', '身份'))


@pytest.mark.parametrize('source', ['open_orders', 'positions_fresh', 'executions_today'])
def test_B26_successful_earlier_overnight_proof_cannot_mask_a_new_query_failure(
        seed_overnight, tmp_path, monkeypatch, source):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    rig.engine._protection_progress(rig.clock.now())

    def unavailable(*args, **kwargs):
        raise BrokerError('synthetic B26 fresh ' + source + ' unavailable')

    monkeypatch.setattr(rig.broker, source, unavailable)
    before = _writes(rig)
    _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert _overnight_p1(rig), 'fresh query failure must revoke the earlier overnight TP authority'


@pytest.mark.parametrize('change', ['missing_entry_exec', 'external_working_sell'])
def test_B26_previous_overnight_success_never_authorizes_TP_after_evidence_changes(
        seed_overnight, tmp_path, monkeypatch, change):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    rig.engine._protection_progress(rig.clock.now())
    old_info_count = len(_restore_infos(rig))
    if change == 'missing_entry_exec':
        rig.store.conn.execute('DELETE FROM fills WHERE exec_id=?', (rig._b26_entry_fill.exec_id,))
    else:
        req = OrderRequest('external-b26-seller', 'AAPL', Side.SELL, 1, 113.0, Purpose.STOP)
        rig.broker._register(req)  # External working SELL was created outside Lite.
    before = _writes(rig)
    _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert len(_restore_infos(rig)) == old_info_count
    assert _overnight_p1(rig)


@pytest.mark.parametrize('change', ['undercovered', 'missing_gtc'])
def test_B26_historical_TP_authority_is_not_permission_to_increase_or_recreate_a_GTC(
        seed_overnight, tmp_path, monkeypatch, change):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    rig.engine._protection_progress(rig.clock.now())
    ref = rig._b26_original['intent_id']
    if change == 'undercovered':
        rig.broker.orders[ref].req = replace(rig.broker.orders[ref].req, quantity=2)
    else:
        rig.broker.orders[ref].status = 'Cancelled'
    before = _writes(rig)
    _attempt_tp(rig, pid); _settle(rig)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert len(rig.broker.sent_protections) == 1
    assert _overnight_p1(rig)


def test_B26_historical_entry_proof_does_not_replace_protected_release_execution_evidence(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    rig.engine._protection_progress(rig.clock.now())
    ref = rig._b26_original['intent_id']
    rig.broker.external_positions['AAPL'] = (-3.0, 113.0)
    rig.broker.orders[ref].status = 'Cancelled'
    rig.store.update_intent(rig.clock.now(), ref, status=IntentStatus.CANCEL_SEEN,
                           last_raw_status='Cancelled', cancel_seen_at=rig.clock.now())
    rig.store.update_protection(rig.clock.now(), pid, state='CANCELLED', requested_qty=0,
                               confirmed_remaining=0, pending_max_remaining=0, pending_qty=None)
    rig.store.set_control(rig.clock.now(), 'frozen:AAPL', 'synthetic operator flatten awaiting proof')
    rig.store.set_control(rig.clock.now(), 'release:AAPL', json.dumps(
        {'operator': 'SyntheticTest', 'reason': 'no current-day SELL execution was supplied'}))
    before = _writes(rig)
    rig.engine._apply_releases(rig.clock.now())
    assert rig.store.position(pid)['status'] == 'OPEN' and rig.store.position(pid)['qty_open'] == 3
    assert rig.store.conn.execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 0
    assert _writes(rig) == before


def test_B26_default_off_retains_b25_overnight_stop_and_blocks_new_TP(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight(enabled=False)
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.broker.orders[rig._b26_original['intent_id']].remaining == 3
    assert _restore_infos(rig) == []
    _safety(rig)


def test_B26_disabling_opt_in_cannot_interrupt_an_already_started_TP_cancel_restore(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    tp, ref = _activate(rig, pid)
    # Only this synthetic cfg changes; no operator configuration or hot-reload
    # mechanism is modified. Existing SELL settlement must retain its scope.
    object.__setattr__(rig.cfg.protection, 'overnight_tp_enabled', False)
    rig.advance(seconds=11)
    rig.engine._protection_timers(rig.clock.now()); _settle(rig)
    assert tp.intent_id in rig.broker.cancels
    rig._b26_queries['epoch'] += 1
    rig.broker.confirm_cancel(tp.intent_id); _settle(rig)
    assert rig.broker.protection_pending_modifications[ref].quantity == 3
    rig.broker.confirm_protection_modify(ref); _settle(rig); _safety(rig)
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.store.protection(pid)['gap_b_started_at'] is None
    assert len(_tp_requests(rig)) == 1 and len(rig.broker.sent_protections) == 1


def test_B26_invalid_overnight_ledger_cannot_modify_a_persisted_breakeven_price(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    rig.store.conn.execute('UPDATE positions SET tp1_done=1,breakeven_stop=entry_price+.01 WHERE position_id=?', (pid,))
    rig.store.conn.execute('DELETE FROM fills WHERE exec_id=?', (rig._b26_entry_fill.exec_id,))
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _settle(rig)
    assert _writes(rig) == before
    assert rig.broker.orders[rig._b26_original['intent_id']].req.trigger_price == rig._b26_original['trigger_price']
    _p1_reason(rig, ('入场', '账本', '成交', '保本'))


def test_B26_overnight_restore_INFO_is_not_repeated_after_same_proof_and_sqlite_reopen(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    rig.engine._protection_progress(rig.clock.now())
    assert len(_restore_infos(rig)) == 1
    before = _writes(rig)
    for _ in range(3):
        rig.advance(seconds=16)
        rig.engine._protection_progress(rig.clock.now())
    assert len(_restore_infos(rig)) == 1 and _writes(rig) == before
    rig.engine.shutdown(grace_seconds=0)
    _restart(rig, tmp_path, monkeypatch, reopen=True)
    rig.engine._protection_progress(rig.clock.now())
    assert len(_restore_infos(rig)) == 1 and _writes(rig) == before
    assert rig.store.protection(pid)['state'] == 'WORKING'

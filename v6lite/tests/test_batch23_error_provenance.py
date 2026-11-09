"""B23 independent regressions: request provenance and diagnostic-only evidence.

Only TEMP SQLite/FakeBroker and the installed IB local wrapper are used. No
connection, runtime state, main loop, or same-batch test-module import. These
cases separate display facts from trading authority and economic booking from
optional evidence decoding. Baseline22a may already preserve the former; new
TP evidence assertions can fail there without a missing-interface exception.
"""
from __future__ import annotations

import json
import socket
from datetime import timedelta
from decimal import Decimal, ROUND_UP
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.market_calendar import trading_day
from v6lite.models import IntentStatus, ProtectionRequest, Purpose
from v6lite.report import daily_report
from v6lite.tests.helpers import Rig
from v6lite.tests.test_batch19_funds import raw_adapter, _start_download, _cancels
from v6lite.tests.test_batch22_item9_broker_cancel import (
    _pending_three, _wire, _drive, _writes, _pages,
    _assert_no_release_after_wrong_identity,
)

TP_PURPOSES = {Purpose.TP1, Purpose.TP2, Purpose.TP3}
PRICE_REASON = ('Order rejected - reason: Your order price is outside the allowed price range. '
                'Synthetic diagnostic; no live account or order. END-PRICE-B23')
DISPLAY_REASON = ('Order canceled - reason: We cannot accept your stop order. '
                  + 'Original synthetic terminal diagnostic; ' * 6
                  + 'END-ERROR-ONLY-完整原文')


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Windows eventkit needs its local asyncio socketpair before network guards.
    import asyncio
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop(); policy.set_event_loop(loop)
    from ib_insync import IB
    from ib_insync.util import getLoop
    assert getLoop() is loop
    def forbidden(*args, **kwargs):
        raise AssertionError('B23 provenance tests cannot connect to IB or a network')
    async def forbidden_async(*args, **kwargs):
        forbidden(*args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _working(tmp_path, *, qty=3):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True),
              risk_per_trade_pct=qty * .0005)
    def no_market(*args, **kwargs):
        raise AssertionError('MKT forbidden')
    rig.broker.submit_market = no_market
    entry = rig.open_aapl()
    [position] = rig.store.open_positions()
    pid = position['position_id']
    assert rig.store.intent(entry)['quantity'] == position['qty_open'] == qty
    assert rig.store.protection(pid)['state'] == 'WORKING'
    rig.channel.messages.clear()
    return rig, pid


def _settle(rig):
    for _ in range(2):
        rig.engine._drain(rig.clock.now())
        rig.engine.process_protection_priority()


def _begin_reduction(rig, pid):
    rig.broker.protection_auto_status = False
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    rig.px('AAPL', float(rig.store.position(pid)['tp1']) + .20)
    rig.engine._evaluate_exits(rig.clock.now())
    assert rig.store.stage(pid, Purpose.TP1)['status'] == 'PENDING'
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    assert rig.store.protection(pid)['state'] == 'REDUCE_PENDING'


def _activate_tp(rig, pid):
    _begin_reduction(rig, pid)
    ref = rig.store.protection(pid)['intent_id']
    rig.broker.confirm_protection_modify(ref)
    _settle(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'TP_ACTIVE'
    request = rig.broker.orders[row['tp_intent_id']].req
    assert request.purpose is Purpose.TP1 and request.quantity >= 1
    assert request.limit_price > 0 and not isinstance(request, ProtectionRequest)
    return request


def _native_tp(rig, req, *, raw='Submitted', code=0, reason=''):
    physical = rig.broker.orders[req.intent_id]
    oid = physical.broker_order_id
    contract = SimpleNamespace(symbol=req.symbol, conId=rig.broker.con_ids[req.symbol],
                               secType='STK', currency='USD', exchange='SMART')
    order = SimpleNamespace(orderRef=req.intent_id, orderId=oid, permId=oid + 50_000,
        account=rig.cfg.account, clientId=rig.cfg.client_id, action='SELL',
        orderType='LMT', tif='DAY', outsideRth=False, totalQuantity=req.quantity,
        auxPrice=0.0, lmtPrice=req.limit_price, usePriceMgmtAlgo=False)
    status = SimpleNamespace(status=raw, filled=physical.filled, remaining=physical.remaining,
        orderId=oid, permId=oid + 50_000, clientId=rig.cfg.client_id, mktCapPrice=0.0)
    trade = SimpleNamespace(order=order, contract=contract, orderStatus=status,
        log=[SimpleNamespace(time=rig.clock.now(), status=raw, errorCode=code, message=reason)],
        fills=[], advancedError='')
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = SimpleNamespace(trades=lambda: [trade], openTrades=lambda: [trade],
                                 isConnected=lambda: True)
    adapter._trades[req.intent_id] = trade
    adapter._contracts[req.symbol] = contract
    return adapter, trade


def _deliver_tp(rig, req, *, raw='Inactive', code=201, reason=PRICE_REASON):
    rig.broker.orders[req.intent_id].status = raw
    adapter, trade = _native_tp(rig, req, raw=raw, code=code, reason=reason)
    adapter._on_status(trade)
    events = adapter.drain_events()
    assert events, 'complete native terminal must be delivered by the existing callback'
    for event in events:
        rig.engine._on_urgent_broker_event(event)
    return adapter, trade


def _evidence(rig, kind='TP_EVIDENCE'):
    return [json.loads(row['payload']) for row in rig.store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY id', (kind,))]


def _paused(tmp_path):
    rig, pid = _working(tmp_path)
    req = _activate_tp(rig, pid)
    _deliver_tp(rig, req)
    raw = rig.store.control(f'tp_pause:{pid}')
    assert raw, 'a real price rejection must persist its pause episode'
    pause = json.loads(raw)
    assert isinstance(pause, dict) and pause.get('pause_id')
    _settle(rig)
    row = rig.store.protection(pid)
    if row['state'] == 'RESTORE_PENDING':
        rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.store.protection(pid)['confirmed_remaining'] == 3
    return rig, pid, req, pause


@pytest.mark.parametrize('shape,wrong', [('metadata_cleared', None), ('complete', 'perm_missing')],
                         ids=['first-terminal-cleared-metadata', 'first-terminal-zero-perm'])
def test_B23_error_only_immediate_terminal_displays_reason_without_granting_cancel_or_release_authority(
        tmp_path, shape, wrong):
    rig, pid = _pending_three(tmp_path, skip_binding=True)
    ref = rig.store.protection(pid)['intent_id']
    assert rig.store.control('protect_native_contract:' + ref) is None
    before = _writes(rig)
    rig.broker.orders[ref].status = 'Cancelled'
    adapter, trade = _wire(rig, pid, raw='Cancelled', shape=shape, wrong=wrong)
    # The original reason exists ONLY in this error callback. No prior
    # Submitted binding and no trade.log/orderStatus callback supplies it.
    trade.log = []
    adapter._on_error(trade.order.orderId, 202, DISPLAY_REASON, trade.contract)
    events = adapter.drain_events()
    assert events, 'same-order display-only terminal error must not disappear behind the strict authority gate'
    for event in events:
        rig.engine._on_urgent_broker_event(event)
    _drive(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'UNKNOWN' and row['pending_max_remaining'] == 3
    assert rig.store.intent(ref)['hold_reason']
    assert rig.store.intent(ref)['status'] not in {IntentStatus.CANCEL_SEEN, IntentStatus.CANCELLED}
    assert rig.store.control('frozen:AAPL')
    pages = _pages(rig, 'P0')
    assert any('券商已取消保护单' in title and DISPLAY_REASON in body for title, body in pages), pages
    assert not any('identity mismatch' in body for _, body in pages)
    assert _writes(rig) == before and rig.store.position(pid)['qty_open'] == 3
    _assert_no_release_after_wrong_identity(rig, pid)


@pytest.mark.parametrize('query', ['funds', 'history'])
def test_B23_known_nonorder_request_id_collision_cannot_become_native_order_error(
        tmp_path, monkeypatch, raw_adapter, query):
    rig, pid = _pending_three(tmp_path)
    ref = rig.store.protection(pid)['intent_id']
    _, trade = _wire(rig, pid, raw='Submitted')
    trade.log = []
    raw = raw_adapter
    broker = raw.broker
    broker._trades[ref] = trade
    broker._contracts['AAPL'] = trade.contract
    assert broker._ensure_channel_hooks() is True
    oid = trade.order.orderId
    monkeypatch.setattr(raw.ib.client, 'getReqId', lambda: oid)
    before = _writes(rig)
    position = dict(rig.store.position(pid))
    protection = dict(rig.store.protection(pid))
    original_intent = dict(rig.store.intent(ref))
    future = None
    if query == 'funds':
        req_id, old_funds = _start_download(raw)
        assert req_id == oid
    else:
        # Exact local IB historical request registration. The native wrapper
        # ends this request BEFORE errorEvent, so a live-future-only test is
        # insufficient; attribution must retain request provenance afterward.
        future = raw.ib.wrapper.startReq(oid, trade.contract, container=[])
        assert oid in raw.ib.wrapper._futures
    reason = 'Synthetic nonorder ' + query + ' request rejected; not an order. END-QUERY-PROVENANCE'
    raw.ib.wrapper.error(oid, 201, reason, '')
    if future is not None:
        assert future.done() and oid not in raw.ib.wrapper._futures
    else:
        assert _cancels(raw) and _cancels(raw)[-1][2] == oid
        assert broker.available_funds() == old_funds
    events = broker.drain_events()
    for event in events:
        rig.engine._on_urgent_broker_event(event)
    assert not [event for event in events if getattr(event, 'intent_id', None) == ref], events
    assert dict(rig.store.position(pid)) == position
    assert dict(rig.store.protection(pid)) == protection
    assert dict(rig.store.intent(ref)) == original_intent
    assert rig.store.control('frozen:AAPL') is None
    assert rig.store.control(f'tp_pause:{pid}') is None
    assert rig.store.control('price_reissue_block:AAPL') is None
    assert _writes(rig) == before


@pytest.mark.parametrize('corruption', ['json_array', 'json_null', 'json_string', 'bad_bid', 'nan_bid', 'bad_gap'])
def test_B23_corrupt_optional_send_audit_never_rolls_back_real_TP_fill_or_duplicates_PnL(tmp_path, corruption):
    rig, pid = _working(tmp_path)
    req = _activate_tp(rig, pid)
    source_bid = rig.broker.quote('AAPL').bid
    key = f'tp_send:{req.intent_id}'
    original = rig.store.control(key)
    # On 22a this optional B23 diagnostic is absent. It is still safe to test
    # corrupt auxiliary input against an actual old TP, not a fabricated fill.
    if corruption == 'json_array':
        damaged = '[]'
    elif corruption == 'json_null':
        damaged = 'null'
    elif corruption == 'json_string':
        damaged = '"synthetic corrupt send object"'
    else:
        payload = json.loads(original) if original else {'bid': source_bid}
        assert isinstance(payload, dict)
        if corruption == 'bad_bid': payload['bid'] = 'unknown'
        elif corruption == 'nan_bid': payload['bid'] = float('nan')
        damaged = json.dumps(payload)
    rig.store.set_control(rig.clock.now(), key, damaged)
    if corruption == 'bad_gap':
        # Deliberately corrupt ONLY the diagnostic clock; the real TP/order,
        # quantities and durable stop counters remain as actually submitted.
        rig.store.conn.execute('UPDATE protections SET gap_b_started_at=? WHERE position_id=?',
                               ('synthetic-invalid-clock', pid))
    before = dict(rig.store.position(pid))
    writes = _writes(rig)
    fill = rig.broker.fill(req.intent_id, qty=req.quantity, price=req.limit_price,
                           exec_id='b23-aux-corrupt-' + corruption, emit=False)
    error = None
    try:
        if corruption == 'bad_gap':
            # Store's real economic transaction isolates this invalid timer
            # from subsequent engine deadline validation. No new SELL power.
            rig.store.record_fill(rig.clock.now(), fill)
        else:
            rig.engine._on_urgent_broker_event(fill)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    booked = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (fill.exec_id,)).fetchone()
    assert booked is not None, ('optional diagnostic corruption rolled back a real execution', corruption, error)
    after = dict(rig.store.position(pid))
    assert after['qty_open'] == before['qty_open'] - fill.quantity
    assert after['realized_pnl'] == pytest.approx(
        before['realized_pnl'] + fill.quantity * (fill.price - before['entry_price']))
    assert rig.store.intent_filled_qty(req.intent_id) == fill.quantity
    assert booked['broker_order_id'] == fill.broker_order_id and booked['perm_id'] == fill.perm_id
    assert error is None, 'optional audit decoding must not abort the real ledger transaction: ' + str(error)
    assert _writes(rig) == writes
    # Exact exec replay must preserve both ledger and economic position.
    outcome = rig.store.record_fill(rig.clock.now(), fill)
    assert not outcome.inserted and dict(rig.store.position(pid)) == after
    assert rig.store.intent_filled_qty(req.intent_id) == fill.quantity and _writes(rig) == writes
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills WHERE exec_id=?', (fill.exec_id,)).fetchone()[0] == 1
    audits = [row for row in _evidence(rig) if row.get('phase') == 'fill' and row.get('exec_id') == fill.exec_id]
    assert len(audits) == 1, 'booked TP executions need one audit even when its optional send input is corrupt'
    event = audits[0]
    if corruption == 'bad_gap':
        assert event.get('gap_b_secs') is None
        assert event.get('fill_minus_bid') == pytest.approx(fill.price - source_bid)
    else:
        assert event.get('bid_at_send') is None
        assert event.get('fill_minus_bid') is None
        assert event.get('fill_minus_bid_usd') is None and event.get('fill_minus_bid_bps') is None


def test_B23_explicit_nonprice_terminal_reason_restores_protection_without_creating_price_pause(tmp_path):
    rig, pid = _working(tmp_path)
    req = _activate_tp(rig, pid)
    reason = 'Synthetic terminal rejection unrelated to price'
    _deliver_tp(rig, req, reason=reason)
    assert rig.store.protection(pid)['state'] == 'TP_NEEDS_RESTORE'
    assert rig.store.control(f'tp_pause:{pid}') is None, 'a statement that the rejection is unrelated to price is not a price constraint'
    assert rig.store.control(f'tp_rejection:{req.intent_id}') is None
    assert not any('P0' in title and '价格' in title for title, _ in rig.channel.messages)
    _settle(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'RESTORE_PENDING' and row['pending_qty'] == 3
    rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == row['pending_max_remaining'] == 3
    assert len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES]) == 1
    assert len(rig.broker.sent_protections) == 1


@pytest.mark.parametrize('source', ['order_price', 'protect_terminal', 'positions_query'])
def test_B23_full_broker_reason_tail_survives_actual_daily_report(tmp_path, monkeypatch, source):
    reason = 'Synthetic ' + source + ' diagnostic: ' + '完整保留原文🍎 ' * 80 + 'END-DAILY-' + source
    if source == 'protect_terminal':
        rig, pid = _pending_three(tmp_path)
        rig.broker.orders[rig.store.protection(pid)['intent_id']].status = 'Cancelled'
        adapter, trade = _wire(rig, pid, raw='Inactive', code=201, reason=reason)
        adapter._on_status(trade)
        for event in adapter.drain_events(): rig.engine._on_urgent_broker_event(event)
        kind = 'PROTECT_BROKER_REJECTED'; field = 'reason'
    else:
        rig, pid = _working(tmp_path)
        if source == 'order_price':
            req = _activate_tp(rig, pid)
            adapter, trade = _native_tp(rig, req, code=399, reason=reason)
            adapter._on_status(trade)
            for event in adapter.drain_events(): rig.engine._on_urgent_broker_event(event)
            kind = 'ORDER_PRICE_OBSERVATION'; field = 'reason'
        else:
            def unavailable(): raise BrokerError(reason)
            with monkeypatch.context() as failed:
                failed.setattr(rig.broker, 'positions_fresh', unavailable)
                with pytest.raises(BrokerError, match='Synthetic positions_query'):
                    rig.engine._fresh_positions()
            kind = 'BROKER_QUERY_ERROR'; field = 'full_error'
    events = _evidence(rig, kind)
    assert any(event.get(field) == reason for event in events), 'the actual broker diagnostic must be durable before checking its report'
    report = daily_report(rig.store, rig.cfg, trading_day(rig.clock.now()))
    assert reason in report and 'END-DAILY-' + source in report, 'the full local daily report must keep broker reason, not the 160-character event preview'


@pytest.mark.parametrize('invalid_id', ['empty', 'missing'])
def test_B23_corrupt_pause_episode_id_cannot_be_consumed_by_equal_invalid_manual_request(tmp_path, invalid_id):
    rig, pid, req, pause = _paused(tmp_path)
    if invalid_id == 'empty': pause['pause_id'] = ''
    else: pause.pop('pause_id')
    raw = json.dumps(pause)
    rig.store.set_control(rig.clock.now(), f'tp_pause:{pid}', raw)
    request = {'position_id': pid, 'symbol': 'AAPL', 'operator': 'John',
               'reason': 'synthetic invalid episode must stay blocked', 'at': rig.clock.now().isoformat()}
    if invalid_id == 'empty': request['pause_id'] = ''
    rig.store.set_control(rig.clock.now(), f'resume_tp:{pid}', json.dumps(request))
    before = _writes(rig)
    other_controls = {key: value for key, value in rig.store.controls_with_prefix('').items()
                      if key.startswith(('frozen:', 'price_reissue_block:', 'kill'))}
    rig.tick()
    assert rig.store.control(f'tp_pause:{pid}') == raw, 'blank/missing equal values are not an identified pause episode'
    assert rig.store.control(f'resume_tp:{pid}') is not None
    assert not [event for event in _evidence(rig, 'TP_RESUMED_BY_OPERATOR')]
    assert _writes(rig) == before and rig.store.protection(pid)['confirmed_remaining'] == 3
    assert {key: value for key, value in rig.store.controls_with_prefix('').items()
            if key.startswith(('frozen:', 'price_reissue_block:', 'kill'))} == other_controls


def test_B23_resume_CLI_rejects_explicit_blank_pause_id_even_if_corrupt_pause_matches(tmp_path, monkeypatch):
    from v6lite import __main__ as cli
    rig, pid, req, pause = _paused(tmp_path)
    pause['pause_id'] = ''
    raw = json.dumps(pause)
    rig.store.set_control(rig.clock.now(), f'tp_pause:{pid}', raw)
    monkeypatch.setattr(cli, '_now', rig.clock.now)
    config_path = tmp_path / 'b23-provenance-cli.toml'
    config_path.write_text('mode="paper"\n[ibkr]\naccount="DU0000000"\nport=4002\nclient_id=131\n'
        '[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n[paths]\nstate_dir='
        + json.dumps(rig.cfg.resolved_state_dir().as_posix()) + '\n', encoding='utf-8')
    try:
        result = cli.main(['resume-tp', 'AAPL', '--position-id', pid, '--pause-id', '',
                           '--operator', 'John', '--reason', 'synthetic blank episode is invalid',
                           '--config', str(config_path)])
    except SystemExit as exc:
        result = exc.code
    assert result == 2, 'explicit blank --pause-id must be rejected rather than recording a resume request'
    assert rig.store.control(f'resume_tp:{pid}') is None
    assert rig.store.control(f'tp_pause:{pid}') == raw


def test_B23_send_evidence_preserves_one_complete_actual_pricing_quote_snapshot(tmp_path, monkeypatch):
    rig, pid = _working(tmp_path)
    _begin_reduction(rig, pid)
    pricing = rig.broker.quote('AAPL')
    expected = float((Decimal(str(pricing.bid)) * Decimal('.998')).quantize(Decimal('.01'), rounding=ROUND_UP))
    original_send = rig.engine._send
    substitutions = []
    def newer_quote_after_pricing(now, req, **kwargs):
        assert req.purpose is Purpose.TP1
        substitutions.append(req)
        # Genuine new local market observation after price construction, before
        # the existing final send boundary. No durable trading flag is changed.
        rig.broker.set_quote('AAPL', bid=pricing.bid + 1, ask=pricing.ask + 1,
                             last=pricing.last + 1, ts=rig.clock.now() - timedelta(seconds=.5))
        return original_send(now, req, **kwargs)
    monkeypatch.setattr(rig.engine, '_send', newer_quote_after_pricing)
    rig.broker.confirm_protection_modify(rig.store.protection(pid)['intent_id'])
    _settle(rig)
    assert len(substitutions) == 1
    req = substitutions[0]
    assert req.limit_price == expected
    assert rig.broker.orders[req.intent_id].req.limit_price == expected
    assert rig.store.intent(req.intent_id)['limit_price'] == expected
    sends = [event for event in _evidence(rig) if event.get('phase') == 'send' and event.get('intent_id') == req.intent_id]
    assert sends, 'actual submitted TP must have a send evidence snapshot'
    for event in sends:
        assert event.get('bid') == pricing.bid and event.get('ask') == pricing.ask
        assert event.get('bid_raw') == str(pricing.bid) and event.get('ask_raw') == str(pricing.ask)
        assert event.get('quote_time') == pricing.ts.isoformat()
        assert event.get('quote_age_seconds') == pytest.approx((rig.clock.now() - pricing.ts).total_seconds())
        assert event.get('market_data_type') == getattr(pricing, 'market_data_type', None)
        assert event.get('sent_price') == event.get('calculated_price') == expected

@pytest.mark.parametrize('delay_at', ['capacity_proof', 'durable_intent'])
def test_B23_final_physical_TP_boundary_rechecks_original_quote_after_slow_evidence_or_local_persistence(
        tmp_path, monkeypatch, delay_at):
    rig, pid = _working(tmp_path)
    _begin_reduction(rig, pid)
    row = rig.store.protection(pid)
    attempts = rig.store.stage(pid, Purpose.TP1)['attempts']
    before_tp = [request for request in rig.broker.sent if request.purpose in TP_PURPOSES]
    start = rig.clock.now()
    delayed = []
    if delay_at == 'capacity_proof':
        original = rig.engine._sell_capacity
        def slow_capacity(*args, **kwargs):
            result = original(*args, **kwargs)
            if not delayed:
                delayed.append(True); rig.clock.advance(seconds=3.6)
            return result
        monkeypatch.setattr(rig.engine, '_sell_capacity', slow_capacity)
    else:
        original = rig.store.create_intent
        def slow_persistence(now, request, **kwargs):
            result = original(now, request, **kwargs)
            if request.purpose in TP_PURPOSES and not delayed:
                delayed.append(True); rig.clock.advance(seconds=3.6)
            return result
        monkeypatch.setattr(rig.store, 'create_intent', slow_persistence)
    # The actual reduction status at start starts B. Delay occurs only later
    # inside the existing final send path, with no quote refresh/repricing.
    rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    assert delayed == [True]
    assert [request for request in rig.broker.sent if request.purpose in TP_PURPOSES] == before_tp
    assert rig.store.stage(pid, Purpose.TP1)['attempts'] == attempts
    assert rig.store.control(f'tp_pause:{pid}') is None
    unsent = list(rig.store.conn.execute("SELECT * FROM intents WHERE position_id=? AND purpose='TP1'", (pid,)))
    for intent in unsent:
        assert intent['status'] in {IntentStatus.REJECTED, IntentStatus.CANCELLED}
        assert intent['broker_order_id'] is None and intent['perm_id'] is None
    current = rig.store.protection(pid)
    assert current['state'] in {'TP_NEEDS_RESTORE', 'RESTORE_PENDING'}
    assert current['gap_b_started_at'] == start.isoformat(), 'notSent must retain the first B origin'
    # A regular tick supplies complete proof if priority was already consumed.
    if current['state'] == 'TP_NEEDS_RESTORE': rig.tick(); _settle(rig)
    current = rig.store.protection(pid)
    assert current['state'] == 'RESTORE_PENDING' and current['pending_qty'] == 3
    rig.refresh_quotes()
    rig.broker.confirm_protection_modify(current['intent_id']); _settle(rig)
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.store.protection(pid)['confirmed_remaining'] == 3
    gaps = _evidence(rig, 'PROTECT_GAP')
    assert any(event.get('gap_kind') == 'B' and event.get('started_at') == start.isoformat()
               and event.get('secs', 0) >= 3.6 for event in gaps)
    assert [request for request in rig.broker.sent if request.purpose in TP_PURPOSES] == before_tp


def test_B23_unprotected_ordinary_TP_price_observation_block_prevents_retry_after_real_terminal_proof(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=False), risk_per_trade_pct=.0015)
    def no_market(*args, **kwargs): raise AssertionError('MKT forbidden')
    rig.broker.submit_market = no_market
    rig.open_aapl()
    [position] = rig.store.open_positions(); pid = position['position_id']
    assert position['qty_open'] == 3 and rig.store.protection(pid) is None
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    rig.px('AAPL', float(position['tp1']) + .20)
    rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue(); rig.engine._drain(rig.clock.now())
    [request] = [order for order in rig.broker.sent if order.purpose is Purpose.TP1]
    original_price = rig.store.intent(request.intent_id)['limit_price']
    adapter, trade = _native_tp(rig, request)
    trade.order.lmtPrice = original_price + .25
    adapter._on_status(trade)
    events = adapter.drain_events(); assert events
    for event in events: rig.engine._on_urgent_broker_event(event)
    assert any('P1' in title and '价格' in title for title, _ in rig.channel.messages)
    block = rig.store.control('price_reissue_block:AAPL'); assert block
    assert rig.store.intent(request.intent_id)['limit_price'] == original_price
    _deliver_tp(rig, request, raw='Cancelled', code=202,
                reason='Order canceled by system: synthetic ordinary TP terminal')
    rig.advance(seconds=rig.cfg.cancel_grace_seconds + 1)
    rig.engine._settle(rig.clock.now(), request.intent_id)
    assert rig.store.intent(request.intent_id)['status'] == IntentStatus.CANCELLED
    assert not rig.store.intent(request.intent_id)['hold_reason']
    before = len([order for order in rig.broker.sent if order.purpose in TP_PURPOSES])
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    rig.tick(); rig.engine._run_queue(); rig.engine._drain(rig.clock.now())
    assert len([order for order in rig.broker.sent if order.purpose in TP_PURPOSES]) == before
    assert rig.store.control('price_reissue_block:AAPL') == block
    assert rig.store.intent(request.intent_id)['limit_price'] == original_price
    assert rig.store.position(pid)['qty_open'] == 3 and rig.store.intent_filled_qty(request.intent_id) == 0

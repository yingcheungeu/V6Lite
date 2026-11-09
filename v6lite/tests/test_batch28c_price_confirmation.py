"""28c PRICE_PENDING contracts at real offline SDK openOrder/request/End paths.

Only the server-side economic state and native request completion are scripted.
The engine creates every position, TP, BE request and durable token itself.
No test writes status_seen_generation, installs proof, or replaces a gate.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.exits import breakeven_stop
from v6lite.models import PriceObservationEvent, ProtectionRequest, Purpose, StatusEvent
from v6lite.tests.helpers import et
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, _overnight_p1, _restart, _safety, _tp_requests, _writes,
)
from v6lite.tests.test_batch28b_overnight_refresh import (
    HIT, NEXT_DAY, TP_DAY, overnight_run, _audits, _tick_until,
)


class NativePriceWire:
    """Use the installed wrapper and adapter for openOrder and fresh snapshots.

    FakeBroker remains the exchange's economic book. Every protection modify
    also traverses the real IbkrBroker.modify_protection/_place_order path;
    only IB.placeOrder itself is an offline server stub. Query futures are
    completed by their actual native End methods, never by a returned cache.
    """
    def __init__(self, rig, pid, harness, monkeypatch):
        self.rig, self.pid, self.harness = rig, pid, harness
        self.adapter, self.ib = harness.adapter, harness.ib
        self.sources, self.ends, self.native_modifications = [], [], []
        self.status_callbacks = []
        self.transform = lambda order: order
        self.after_source = lambda name, result: None
        self.ib.openOrderEvent += self.adapter._on_open_order
        self.ib.orderStatusEvent += self.adapter._on_status
        self.ib.orderStatusEvent += lambda trade: self.status_callbacks.append(
            (rig.clock.now(), trade.order.orderRef, trade.orderStatus.status))
        # The handler resolves rig.engine at delivery time, including restart.
        self.deliver = lambda event: rig.engine._on_urgent_broker_event(event)
        self.adapter.set_urgent_handler(self.deliver)
        # The inherited native execution helper explicitly drains its decoded
        # callback before booking it. Keep that ownership for fills while live
        # price callbacks use the real same-loop urgent path.
        original_fill = rig._b28b_callback_fill
        def callback_fill(*args, **kwargs):
            self.adapter.set_urgent_handler(None)
            try:
                return original_fill(*args, **kwargs)
            finally:
                self.adapter.set_urgent_handler(self.deliver)
        monkeypatch.setattr(rig, '_b28b_callback_fill', callback_fill)
        monkeypatch.setattr(rig.broker, 'set_urgent_handler',
                            self.adapter.set_urgent_handler, raising=False)
        self.cache_original()

        def place(contract, order):
            from ib_insync import OrderStatus, Trade
            self.native_modifications.append((rig.clock.now(), copy.deepcopy(order)))
            economic = rig.broker.orders[order.orderRef]
            trade = Trade(contract, order, OrderStatus(
                orderId=order.orderId, permId=order.permId, clientId=order.clientId,
                status='PreSubmitted', filled=economic.filled,
                remaining=economic.remaining))
            key = self.ib.wrapper.orderKey(order.clientId, order.orderId, order.permId)
            self.ib.wrapper.trades[key] = trade
            self.ib.wrapper.permId2Trade[order.permId] = trade
            return trade
        monkeypatch.setattr(self.ib, 'placeOrder', place)
        economic_modify = rig.broker.modify_protection
        def modify(req):
            economic_ack = economic_modify(req)
            native_ack = self.adapter.modify_protection(req)
            assert native_ack == economic_ack
            return economic_ack
        monkeypatch.setattr(rig.broker, 'modify_protection', modify)

        def request_orders():
            future = self.ib.wrapper.startReq('openOrders')
            self.ib.client.reqAllOpenOrders(); harness.loop.drain()
            self.ib.wrapper.lastTime = rig.clock.now()
            for value in FakeBroker.open_orders(rig.broker):
                self.open_order(self.transform(value))
            self.ib.wrapper.openOrderEnd()
            assert future.done(), 'own native openOrderEnd must finish this request'
            self.ends.append(('openOrders', rig.clock.now(), self.adapter.query_generation))
            return future.result()
        monkeypatch.setattr(self.ib, 'reqAllOpenOrders', request_orders)

        def request_positions():
            from ib_insync import Stock
            future = self.ib.wrapper.startReq('positions')
            self.ib.client.reqPositions(); harness.loop.drain()
            self.ib.wrapper.lastTime = rig.clock.now()
            for value in FakeBroker.positions_fresh(rig.broker):
                contract = Stock(value.symbol, 'SMART', 'USD', conId=value.con_id)
                self.ib.wrapper.position(value.account, contract, value.quantity, value.avg_cost)
            self.ib.wrapper.positionEnd()
            assert future.done(), 'own native positionEnd must finish this request'
            self.ends.append(('positions', rig.clock.now(), self.adapter.query_generation))
            return future.result()
        monkeypatch.setattr(self.ib, 'reqPositions', request_positions)

        # Preserve the independent S+T+X observer's real query accounting.
        def source(name, query):
            def run(*args, **kwargs):
                state = rig._b26_queries
                if name == 'open_orders':
                    state['partial'] = {}; state['complete'] = None
                try:
                    result = query(*args, **kwargs)
                finally:
                    rig.broker.query_generation = self.adapter.query_generation
                if name in {'open_orders', 'positions_fresh'}:
                    state['calls'].append((name, rig.clock.now(), result))
                    state['partial'][name] = result
                self.sources.append((name, rig.clock.now(), result,
                                     rig.engine._protection_query_position == pid))
                self.after_source(name, result)
                return result
            return run
        monkeypatch.setattr(rig.broker, 'open_orders', source('open_orders', self.adapter.open_orders))
        monkeypatch.setattr(rig.broker, 'positions_fresh', source('positions_fresh', self.adapter.positions_fresh))
        monkeypatch.setattr(rig.broker, 'open_orders_snapshot_current',
                            self.adapter.open_orders_snapshot_current, raising=False)
        for name in ('executions_today', 'account_executions_today_scoped'):
            monkeypatch.setattr(rig.broker, name, source(name, getattr(rig.broker, name)))

    def native(self, value):
        from ib_insync import LimitOrder, OrderState, Stock, StopOrder
        contract = Stock(value.symbol, 'SMART', 'USD', conId=value.con_id)
        common = dict(orderId=value.broker_order_id, permId=value.perm_id,
            clientId=value.client_id, account=value.account, orderRef=value.intent_id,
            tif=value.tif, outsideRth=value.outside_rth, usePriceMgmtAlgo=False)
        if value.order_type == 'STP':
            order = StopOrder(value.side, value.total_quantity, value.aux_price, **common)
        else:
            order = LimitOrder(value.side, value.total_quantity, value.limit_price, **common)
        return contract, order, OrderState(status=value.status)

    def cache_original(self):
        from ib_insync import OrderStatus, Trade
        row = self.rig.store.protection(self.pid)
        value = self.rig.broker._open_order_snapshot(row['intent_id'],
                    self.rig.broker.orders[row['intent_id']])
        contract, order, _ = self.native(value)
        trade = Trade(contract, order, OrderStatus(orderId=order.orderId,
            permId=order.permId, clientId=order.clientId, status=value.status,
            filled=value.filled, remaining=value.remaining))
        self.ib.wrapper.trades[self.ib.wrapper.orderKey(order.clientId, order.orderId, order.permId)] = trade
        self.ib.wrapper.permId2Trade[order.permId] = trade
        self.adapter._trades[value.intent_id] = trade
        return value

    def open_order(self, value):
        contract, order, state = self.native(value)
        self.ib.wrapper.lastTime = self.rig.clock.now()
        self.ib.wrapper.openOrder(order.orderId, contract, order, state)

    def apply_pending(self):
        """The broker accepts a request; deliberately emit no status event."""
        row = self.rig.store.protection(self.pid)
        ref = row['intent_id']
        request = self.rig.broker.protection_pending_modifications.pop(ref)
        native = self.rig.broker.orders[ref]
        native.req = request; native.status = 'PreSubmitted'
        return self.rig.broker._open_order_snapshot(ref, native)

    def ack(self, *, only_open_order=False):
        value = self.apply_pending()
        self.rig.advance(microseconds=1000)
        before = len(self.status_callbacks)
        self.open_order(value)
        if only_open_order:
            assert len(self.status_callbacks) == before, 'wrapper.openOrder must not synthesize orderStatus'
        else:
            self.ib.wrapper.orderStatus(value.broker_order_id, value.status,
                value.filled, value.remaining, 0., value.perm_id, 0, 0.,
                value.client_id, 'trigger', 0.)
        self.rig.engine._drain(self.rig.clock.now())
        _safety(self.rig)
        return value

    def queued(self, value, *, snapshot=False):
        """Create through the real callback now; deliver the resulting event later."""
        self.adapter.set_urgent_handler(None)
        self.adapter._connection_syncing = snapshot
        try:
            self.open_order(value)
            events = self.adapter.drain_events()
        finally:
            self.adapter._connection_syncing = False
            self.adapter.set_urgent_handler(self.deliver)
        assert events and any(isinstance(event, PriceObservationEvent) for event in events)
        assert not any(isinstance(event, StatusEvent) for event in events)
        return events


@pytest.fixture
def price_run(overnight_run, monkeypatch):
    def make():
        rig, pid, harness = overnight_run()
        wire = NativePriceWire(rig, pid, harness, monkeypatch)
        rig.at(HIT); rig.px('AAPL', 344.05)
        return rig, pid, wire
    return make


def _tp1_inflight(rig, pid, wire):
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    row = rig.store.protection(pid)
    assert row['pending_qty'] == 2 and not _tp_requests(rig)
    wire.ack()
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 1)
    [tp] = _tp_requests(rig)
    assert tp.purpose is Purpose.TP1 and tp.quantity == 1
    assert rig.store.protection(pid)['state'] == 'TP_ACTIVE'
    _safety(rig)
    return tp


def _be_pending(rig, pid, wire, *, tp=None):
    tp = tp or _tp1_inflight(rig, pid, wire)
    rig._b28b_callback_fill(tp.intent_id, quantity=1,
        price=rig.broker.quote('AAPL').bid, exec_id='b28c-tp1.01.01')
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'PRICE_PENDING')
    row, pos = rig.store.protection(pid), rig.store.position(pid)
    assert pos['qty_open'] == 2 and pos['tp1_done'] == 1
    assert row['pending_qty'] == 2 and row['modify_generation'] == 2
    assert row['pending_trigger'] == breakeven_stop(rig.cfg, 342.60)
    assert row['trigger_price'] == 338.62 and row['status_seen_generation'] == -1
    assert rig.store.control('overnight_be_flow:' + pid) is not None
    _safety(rig)
    return dict(row)


def _confirmed(rig, pid, before):
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING', 'a matching native openOrder/own fresh round must confirm BE without orderStatus'
    assert row['trigger_price'] == before['pending_trigger']
    assert row['pending_qty'] is row['pending_trigger'] is row['pending_limit'] is None
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 2
    assert row['broker_order_id'] == before['broker_order_id'] == 706
    assert row['perm_id'] == before['perm_id'] and row['intent_id'] == before['intent_id']
    assert rig.store.control('frozen:AAPL') is None
    _safety(rig)


def _finish_tp2_and_tp3(rig, pid, wire):
    ref = rig.store.protection(pid)['intent_id']
    rig.px('AAPL', float(rig.store.position(pid)['tp2']) + .20)
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    assert rig.broker.protection_pending_modifications[ref].quantity == 1
    wire.ack()
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 2)
    tp2 = _tp_requests(rig)[-1]
    assert tp2.purpose is Purpose.TP2 and tp2.quantity == 1
    rig._b28b_callback_fill(tp2.intent_id, quantity=1,
        price=rig.broker.quote('AAPL').bid, exec_id='b28c-tp2.01.01')
    _tick_until(rig, lambda: rig.store.position(pid)['tp2_done'] == 1 and
                rig.store.protection(pid)['state'] == 'WORKING')
    assert rig.store.position(pid)['qty_open'] == 1
    rig.px('AAPL', float(rig.store.position(pid)['tp3']) + .20)
    _tick_until(rig, lambda: ref in rig.broker.cancels)
    assert len(_tp_requests(rig)) == 2, 'last TP waits for native cancel and stable absence proof'
    economic = rig.broker.orders[ref]
    economic.status = 'Cancelled'
    wire.ib.wrapper.orderStatus(economic.broker_order_id, 'Cancelled',
        economic.filled, economic.remaining, 0., economic.broker_order_id + 50000,
        0, 0., rig.cfg.client_id, '', 0.)
    rig.engine._drain(rig.clock.now()); _safety(rig)
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 3)
    tp3 = _tp_requests(rig)[-1]
    assert tp3.purpose is Purpose.TP3 and tp3.quantity == 1
    rig._b28b_callback_fill(tp3.intent_id, quantity=1,
        price=rig.broker.quote('AAPL').bid, exec_id='b28c-tp3.01.01')
    _tick_until(rig, lambda: rig.store.position(pid)['status'] == 'CLOSED')
    assert rig.store.position(pid)['qty_open'] == 0
    assert [request.purpose for request in _tp_requests(rig)] == [Purpose.TP1, Purpose.TP2, Purpose.TP3]
    assert len(rig.broker.sent_protections) == 1
    _safety(rig)


def test_B28c_native_openOrder_only_BE_confirms_then_TP2_TP3_complete_without_oversell(price_run):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    query_count, statuses = len(wire.sources), len(wire.status_callbacks)
    wire.ack(only_open_order=True)
    assert len(wire.status_callbacks) == statuses
    assert len(wire.sources) == query_count, 'live openOrder acknowledgement itself must not need a blocking query'
    _confirmed(rig, pid, pending)
    _finish_tp2_and_tp3(rig, pid, wire)
    assert not _overnight_p1(rig)
    assert not [text for level, text in rig._b26_alerts if level == 'P0']


@pytest.mark.parametrize('restart_day', [TP_DAY, NEXT_DAY], ids=['same-day', 'next-trading-day'])
def test_B28c_reopen_PRICE_PENDING_waits_for_owned_complete_three_sources_before_confirming(
        price_run, tmp_path, monkeypatch, restart_day):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    wire.apply_pending()  # Server correct; no orderStatus or live openOrder is emitted.
    rig.engine.shutdown(grace_seconds=0)
    rig.at(et(9, 31, day=restart_day))
    before_writes, native_ends = _writes(rig), len(wire.ends)
    checkpoints = []
    first_round = {'complete': False}
    def before_source_returns(name, result):
        if rig.engine._protection_query_position == pid and not first_round['complete']:
            checkpoints.append((name, rig.store.protection(pid)['state']))
            assert rig.store.protection(pid)['state'] == 'PRICE_PENDING', (
                'neither a connect snapshot, an openOrders row, nor a partial round may confirm BE', name)
            if name == 'account_executions_today_scoped':
                first_round['complete'] = True
    wire.after_source = before_source_returns
    _restart(rig, tmp_path, monkeypatch, reopen=True)
    wire.after_source = lambda name, result: None
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'WORKING')
    _confirmed(rig, pid, pending)
    assert {'open_orders', 'positions_fresh', 'executions_today',
            'account_executions_today_scoped'} <= {name for name, _ in checkpoints}
    assert {'openOrders', 'positions'} <= {name for name, _, _ in wire.ends[native_ends:]}
    scopes = [result.scope for name, _, result, proof in wire.sources
              if proof and name == 'account_executions_today_scoped']
    ended = {request_id for request_id, _ in wire.harness.ends}
    assert scopes and any(scope.get('complete') is True and scope.get('request_id') in ended and
                         scope.get('day') == '-'.join(map(str, restart_day)) for scope in scopes)
    assert _writes(rig) == before_writes, 'restart confirms the existing STP without modifying or recreating it'
    rig.px('AAPL', float(rig.store.position(pid)['tp2']) + .20)
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    assert rig.broker.protection_pending_modifications[pending['intent_id']].quantity == 1
    _safety(rig)


@pytest.mark.parametrize('source', ['live-openOrder', 'owned-snapshot'])
@pytest.mark.parametrize('wrong', ['aux-price', 'quantity', 'permId'])
def test_B28c_pending_BE_mismatch_never_confirms_or_repairs_original_STP(
        price_run, source, wrong):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    correct = wire.apply_pending()
    if wrong == 'aux-price':
        bad = replace(correct, aux_price=correct.aux_price + .03)
    elif wrong == 'quantity':
        bad = replace(correct, total_quantity=1, remaining=1.)
    else:
        bad = replace(correct, perm_id=correct.perm_id + 17)
    wire.transform = lambda order: bad if order.intent_id == correct.intent_id else order
    before = _writes(rig)
    original = rig.broker.orders[correct.intent_id].req
    if source == 'live-openOrder':
        wire.open_order(bad); rig.engine._drain(rig.clock.now())
        assert rig.store.protection(pid)['state'] != 'WORKING'
        assert rig.store.protection(pid)['trigger_price'] == pending['trigger_price']
        assert _writes(rig) == before and len(_tp_requests(rig)) == 1
    rig.px('AAPL', float(rig.store.position(pid)['tp2']) + .20)
    _tick_until(rig, lambda: bool(_overnight_p1(rig)))
    row = rig.store.protection(pid)
    assert row['state'] != 'WORKING' and row['pending_qty'] is not None
    assert row['trigger_price'] == pending['trigger_price']
    assert len(_tp_requests(rig)) == 1 and _writes(rig) == before
    assert rig.broker.orders[correct.intent_id].req == original
    assert rig.store.control('frozen:AAPL') is not None or _audits(rig, 'OVERNIGHT_TP_FROZEN')
    assert _overnight_p1(rig)
    _safety(rig)


def test_B28c_creation_bound_connect_openOrder_cannot_confirm_after_sync_finishes(price_run):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    value = wire.apply_pending()
    events = wire.queued(value, snapshot=True)
    assert all(event.connection_snapshot for event in events)
    before = _writes(rig)
    for event in events:
        rig.engine._handle_event(rig.clock.now(), event)
    assert rig.store.protection(pid)['state'] == 'PRICE_PENDING'
    assert rig.store.protection(pid)['trigger_price'] == pending['trigger_price']
    assert _writes(rig) == before and len(_tp_requests(rig)) == 1
    _safety(rig)


def test_B28c_queued_pre_request_openOrder_cannot_ack_the_later_BE_generation(price_run):
    rig, pid, wire = price_run()
    tp = _tp1_inflight(rig, pid, wire)
    row = rig.store.protection(pid)
    current = rig.broker._open_order_snapshot(row['intent_id'], rig.broker.orders[row['intent_id']])
    # An old native observation can coincidentally carry the future exact price.
    # Its creation predates the actual BE request and must retain that role.
    future_price = breakeven_stop(rig.cfg, 342.60)
    events = wire.queued(replace(current, aux_price=future_price))
    wire.cache_original()
    rig.advance(microseconds=1000)
    pending = _be_pending(rig, pid, wire, tp=tp)
    wire.apply_pending()
    before = _writes(rig)
    for event in events:
        rig.engine._handle_event(rig.clock.now(), event)
    assert rig.store.protection(pid)['state'] == 'PRICE_PENDING'
    assert rig.store.protection(pid)['trigger_price'] == pending['trigger_price']
    assert _writes(rig) == before and len(_tp_requests(rig)) == 1
    _safety(rig)


def test_B28c_queued_previous_socket_openOrder_cannot_ack_on_a_new_generation(price_run):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    value = wire.apply_pending()
    events = wire.queued(value)
    old_generation = wire.adapter.query_generation
    wire.adapter._reset_channels()
    rig.broker.query_generation = wire.adapter.query_generation
    assert wire.adapter.query_generation != old_generation
    before = _writes(rig)
    for event in events:
        rig.engine._handle_event(rig.clock.now(), event)
    assert rig.store.protection(pid)['state'] == 'PRICE_PENDING'
    assert rig.store.protection(pid)['trigger_price'] == pending['trigger_price']
    assert _writes(rig) == before and len(_tp_requests(rig)) == 1
    _safety(rig)


def test_B28c_unconfirmed_BE_records_one_daily_wait_and_never_starts_TP2(price_run):
    rig, pid, wire = price_run()
    _be_pending(rig, pid, wire)
    before = _writes(rig)
    for day in (TP_DAY, NEXT_DAY):
        if day == NEXT_DAY:
            rig.at(et(11, 0, day=day))
        for index in range(8):
            rig.advance(seconds=31)
            level = float(rig.store.position(pid)['tp2'])
            rig.px('AAPL', level + (.20 if index % 2 == 0 else -.20))
            # Real fast decision pass: the wait gate precedes begin_stage and
            # queue admission. Full ticks/queries are exercised by other tests.
            rig.engine._evaluate_exits(rig.clock.now())
            _safety(rig)
    records = [(row['kind'], json.loads(row['payload'])) for row in rig.store.conn.execute(
        'SELECT kind,payload FROM audit ORDER BY id')]
    waiting = [(kind, payload) for kind, payload in records
               if payload.get('position_id') == pid and '等待保本修改确认' in str(payload)]
    assert len(waiting) == 2, 'one durable waiting explanation per position/trading day'
    assert not [payload for kind, payload in records if kind in {'STAGE_BEGIN', 'STAGE_ABANDONED'}
                and payload.get('position_id') == pid and payload.get('stage') == str(Purpose.TP2)]
    assert rig.store.protection(pid)['state'] == 'PRICE_PENDING'
    assert len(_tp_requests(rig)) == 1 and _writes(rig) == before

"""B23 independent functional contracts on the unchanged batch22a interface.

All mutable state/config is under tmp_path. Broker callbacks are native-shaped
local doubles. No network, runtime database, operator config, or main loop.
Mapping: B original clock (1.1); async terminal restoration (1.2); full reason
P0 (1.3); durable stage pause (1.4); PMA/working/cap (1.5); actual scenario
quantities (1.6); two book-age checks and bid pricing (1.7); Chinese facts (1.8);
per-phase automatic evidence and daily attribution (section 5).
"""
from __future__ import annotations

import json
import re
import socket
import tomllib
import v6lite
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_UP
from pathlib import Path
from types import SimpleNamespace

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.broker.base import BrokerError
from v6lite.broker.fake import FakeBroker
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ConfigError, LiteConfig, ProtectionConfig, load_config
from v6lite.engine import Engine, StartupError
from v6lite.models import IntentStatus, OrderRequest, ProtectionRequest, Purpose, Side
from v6lite.report import daily_report
from v6lite.store import Store
from v6lite.tests.helpers import Rig
from v6lite.tests.test_batch22_config_broker import _LocalIB
from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel, _reason_parts

TP_PURPOSES = {Purpose.TP1, Purpose.TP2, Purpose.TP3}
TERMINAL = {'Cancelled', 'ApiCancelled', 'Inactive', 'Filled'}
PRICE_REASON = (
    'Order rejected - reason: Your order price is outside the allowed price range. '
    'Synthetic IBKR price-band diagnostic; not an account or a live order. '
    + '保留完整原文🍎 price constraint detail; ' * 70
    + 'END-B23-ORIGINAL-PRICE-REASON'
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Windows/eventkit initializes a local socketpair before the network guard.
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
        raise AssertionError('B23 must never open a network or IB connection')
    async def forbidden_async(*args, **kwargs):
        forbidden(*args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _writes(rig):
    return tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections,
                                  rig.broker.modified_protections, rig.broker.cancels))


def _complete_absence(rig, ref, symbol):
    """Only a completed post-callback triple can release terminal potential."""
    trace=getattr(rig,'_b23_queries',None)
    if trace is None:
        return False
    order=rig.broker.orders.get(ref)
    intent=rig.store.intent(ref)
    # An unrelated callback cannot undo a completed cancellation proof. A new
    # late exec or active native callback invalidates that particular proof.
    if (order is not None and intent is not None and not intent['hold_reason'] and
            order.status in TERMINAL and trace['released'].get(ref)==order.filled):
        return True
    if trace['proof'] is None:
        return False
    proof=trace['proof']
    if proof['epoch']!=trace['epoch'] or proof['fills'] is None:
        return False
    if (rig.clock.now()-proof['at']).total_seconds()>3:
        return False
    held={p.symbol:int(p.quantity) for p in proof['positions']}
    required={r['exec_id'] for r in rig.store.conn.execute('SELECT exec_id FROM fills WHERE symbol=?',(symbol,))}
    complete=(required.issubset({f.exec_id for f in proof['fills']}) and
            held.get(symbol,0)=={p.symbol:int(p.quantity) for p in FakeBroker.positions_fresh(rig.broker)}.get(symbol,0) and
            not any(o.intent_id==ref for o in proof['orders']))
    if complete and order is not None:
        trace['released'][ref]=order.filled
    return complete


def _safety(rig, prospective=None):
    """Store possible maxima plus offline working capacity, with no query side effect."""
    truth={p.symbol:int(p.quantity) for p in FakeBroker.positions_fresh(rig.broker)}
    by_ref={}
    def claim(ref,symbol,quantity):
        old=by_ref.get(ref,(symbol,0))
        by_ref[ref]=(symbol,max(old[1],max(0,int(quantity))))
    # The generic PROTECT intent quantity can be the original submitted total;
    # confirmed/pending row counters carry the current possible remainder.
    for row in rig.store.conn.execute('SELECT * FROM protections'):
        claim(row['intent_id'],row['symbol'],max(row['confirmed_remaining'],row['pending_max_remaining']))
    for ref,order in rig.broker.orders.items():
        req=order.req
        protect=isinstance(req,ProtectionRequest)
        if protect:
            assert req.order_type=='STP' and req.limit_price is None
        else:
            assert isinstance(req,OrderRequest) and req.limit_price>0
            assert getattr(req,'order_type','LMT')=='LMT'
        if not protect and req.side is not Side.SELL:
            continue
        if order.status not in TERMINAL:
            possible=order.remaining
            pending=rig.broker.protection_pending_modifications.get(ref)
            if pending is not None:
                assert pending.order_type=='STP' and pending.limit_price is None
                possible=max(possible,pending.quantity-order.filled)
            claim(ref,req.symbol,possible)
    for intent in rig.store.conn.execute("SELECT * FROM intents WHERE side='SELL' AND purpose!='PROTECT'"):
        ref=intent['intent_id']
        left=max(0,int(intent['quantity'])-rig.store.intent_filled_qty(ref))
        order=rig.broker.orders.get(ref)
        sent=order is not None or intent['status'] in {
            IntentStatus.SENT,IntentStatus.PARTIAL,IntentStatus.CANCEL_REQUESTED,
            IntentStatus.CANCEL_SEEN,IntentStatus.UNKNOWN}
        terminal=intent['status'] in {IntentStatus.REJECTED,IntentStatus.CANCEL_SEEN,IntentStatus.CANCELLED,IntentStatus.FILLED}
        # CANCEL_SEEN/UNKNOWN/maybe_sent are never zero merely because the Fake
        # stopped listing the order. Hold always retains unbooked capacity.
        if sent and left and (intent['hold_reason'] or not terminal or
                              not _complete_absence(rig,ref,intent['symbol'])):
            claim(ref,intent['symbol'],left)
    if prospective is not None and (isinstance(prospective,ProtectionRequest) or prospective.side is Side.SELL):
        filled=rig.store.intent_filled_qty(prospective.intent_id)
        claim(prospective.intent_id,prospective.symbol,prospective.quantity-filled)
    totals={}
    for symbol,quantity in by_ref.values():totals[symbol]=totals.get(symbol,0)+quantity
    for symbol,total in totals.items():
        assert total<=truth.get(symbol,0),(symbol,'S+T+X',total,'P',truth.get(symbol,0),by_ref)
    assert rig.market_calls==[]


def _watch_boundaries(rig):
    """Real queries complete a generation; every broker write has two checks."""
    rig._b23_queries={'epoch':0,'proof':None,'partial':{},'released':{}}
    rig._b23_write_checks=[]
    for name in ('open_orders','positions_fresh','executions_today'):
        original=getattr(rig.broker,name)
        def query(*args,_name=name,_original=original,**kwargs):
            trace=rig._b23_queries
            if _name=='open_orders':
                trace['partial']={};trace['proof']=None
            result=_original(*args,**kwargs)
            trace['partial'][_name]=result
            if _name=='executions_today' and all(k in trace['partial'] for k in ('open_orders','positions_fresh','executions_today')):
                parts=trace['partial']
                trace['proof']={'epoch':trace['epoch'],'at':rig.clock.now(),
                                'orders':parts['open_orders'],'positions':parts['positions_fresh'],
                                'fills':parts['executions_today']}
            return result
        setattr(rig.broker,name,query)
    for name in ('submit_limit','submit_stop','modify_protection','cancel'):
        original=getattr(rig.broker,name)
        def write(*args,_name=name,_original=original,**kwargs):
            req=args[0] if args and isinstance(args[0],(OrderRequest,ProtectionRequest)) else None
            _safety(rig,req);rig._b23_write_checks.append((_name,'before'))
            try:return _original(*args,**kwargs)
            finally:
                _safety(rig);rig._b23_write_checks.append((_name,'after'))
        setattr(rig.broker,name,write)


def _working(tmp_path, *, qty=3, dry_run=False, **overrides):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), dry_run=dry_run,
              risk_per_trade_pct=qty * .0005, **overrides)
    rig.market_calls = []
    def forbidden_market(*args, **kwargs):
        rig.market_calls.append((args, kwargs))
        raise AssertionError('MKT forbidden')
    rig.broker.submit_market = forbidden_market
    _watch_boundaries(rig)
    entry = rig.open_aapl()
    [pos] = rig.store.open_positions()
    assert pos['qty_open'] == qty and rig.store.intent(entry)['quantity'] == qty
    assert rig.store.protection(pos['position_id'])['state'] == 'WORKING'
    _safety(rig)
    rig.channel.messages.clear()
    return rig, pos['position_id']


def _settle(rig):
    for _ in range(2):
        rig.engine._drain(rig.clock.now())
        _safety(rig)
        rig.engine.process_protection_priority()
        _safety(rig)


def _set_book(rig, *, age=0, bid=None, ask=None, last=None):
    pos = rig.store.open_positions()[0]
    price = float(pos['tp1']) + .20
    bid = round(price - .03, 2) if bid is None else bid
    ask = round(price + .03, 2) if ask is None else ask
    last = price if last is None else last
    rig.broker.set_quote('AAPL', bid=bid, ask=ask, last=last,
                         ts=rig.clock.now() - timedelta(seconds=age))


def _queue_tp(rig, pid, stage=Purpose.TP1):
    rig.advance(seconds=rig.cfg.exit_check_interval_seconds + 1)
    pos = rig.store.position(pid)
    rig.px('AAPL', float(pos[{Purpose.TP1:'tp1',Purpose.TP2:'tp2',Purpose.TP3:'tp3'}[stage]]) + .20)
    rig.engine._evaluate_exits(rig.clock.now())
    st = rig.store.stage(pid, stage)
    assert st is not None and st['status'] == 'PENDING'
    _safety(rig)


def _begin_tp(rig, pid, stage=Purpose.TP1, *, age=0, **book):
    _queue_tp(rig, pid, stage)
    if stage is Purpose.TP1:
        _set_book(rig, age=age, **book)
    elif age:
        q = rig.broker.quote('AAPL')
        rig.broker.set_quote('AAPL', bid=q.bid, ask=q.ask, last=q.last,
                            ts=rig.clock.now() - timedelta(seconds=age))
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    _safety(rig)


def _activate_tp(rig, pid, stage=Purpose.TP1):
    rig.broker.protection_auto_status = False
    _begin_tp(rig, pid, stage)
    row = rig.store.protection(pid)
    assert row['state'] == 'REDUCE_PENDING'
    _safety(rig)
    rig.broker.confirm_protection_modify(row['intent_id'])
    _settle(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'TP_ACTIVE'
    req = rig.broker.orders[row['tp_intent_id']].req
    assert req.purpose is stage and req.quantity >= 1
    _safety(rig)
    return req


def _native_status(rig, ref, *, raw='Submitted', code=0, reason='', working_price=None,
                   cap=0.0, wrong=None):
    broker_order = rig.broker.orders[ref]
    req = broker_order.req
    protect = isinstance(req, ProtectionRequest)
    oid = broker_order.broker_order_id
    contract = SimpleNamespace(symbol=req.symbol, conId=rig.broker.con_ids[req.symbol],
                               secType='STK', currency='USD', exchange='SMART')
    order = SimpleNamespace(orderRef=ref, orderId=oid, permId=oid + 50_000,
        account=rig.cfg.account, clientId=rig.cfg.client_id,
        action='SELL' if protect else str(req.side), orderType='STP' if protect else 'LMT',
        tif='GTC' if protect else 'DAY', outsideRth=False, totalQuantity=req.quantity,
        auxPrice=req.trigger_price if protect else 0.0, lmtPrice=req.limit_price,
        usePriceMgmtAlgo=False)
    if working_price is not None:
        if protect:
            order.auxPrice = working_price
        else:
            order.lmtPrice = working_price
    status = SimpleNamespace(status=raw, filled=broker_order.filled, remaining=broker_order.remaining,
        orderId=oid, permId=oid + 50_000, clientId=rig.cfg.client_id, mktCapPrice=cap)
    if wrong == 'account': order.account = 'DU0000000'
    elif wrong == 'orderId': order.orderId = status.orderId = oid + 99
    elif wrong == 'permId': order.permId = status.permId = oid + 99
    elif wrong == 'conId': contract.conId += 99
    else: assert wrong is None
    trade = SimpleNamespace(order=order, contract=contract, orderStatus=status,
        log=[SimpleNamespace(time=rig.clock.now(),status=raw,errorCode=code,message=reason)],
        fills=[], advancedError='')
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = SimpleNamespace(trades=lambda:[trade],openTrades=lambda:[trade],isConnected=lambda:True)
    adapter._trades[ref] = trade
    adapter._contracts[req.symbol] = contract
    return adapter, trade


def _deliver_status(rig, adapter, trade):
    trace=getattr(rig,'_b23_queries',None)
    if trace is not None:
        trace['epoch']+=1;trace['proof']=None;trace['partial']={}
        if trade.orderStatus.status not in TERMINAL:
            trace['released'].pop(trade.order.orderRef,None)
    adapter._on_status(trade)
    events = adapter.drain_events()
    assert events, 'the native adapter callback must produce a real event'
    for event in events:
        rig.engine._on_urgent_broker_event(event)
    _safety(rig)


def _terminal_tp(rig, req, *, raw='Inactive', code=201, reason=PRICE_REASON, wrong=None):
    if wrong is None:
        rig.broker.orders[req.intent_id].status = raw
    adapter, trade = _native_status(rig, req.intent_id, raw=raw, code=code, reason=reason, wrong=wrong)
    _deliver_status(rig, adapter, trade)
    return adapter, trade


def _evidence(rig, pid=None):
    result=[]
    for row in rig.store.conn.execute("SELECT ts,payload FROM audit WHERE kind='TP_EVIDENCE' ORDER BY id"):
        payload=json.loads(row['payload'])
        if pid is None or payload.get('position_id') == pid:
            result.append(dict(payload, audit_ts=row['ts']))
    return result


def _restart(rig, tmp_path):
    path,levels=rig.store.path,rig.store._levels_fn
    rig.store.close()
    rig.store=Store(path,levels)
    rig.channel=MemoryChannel()
    rig.alerter=Alerter([rig.channel],synchronous=True)
    rig.engine=Engine(rig.cfg,rig.broker,rig.store,rig.alerter,rig.clock,
                      report_dir=tmp_path/'restarted-reports')
    # A normal restored pause is a successful startup, not a swallowed failure.
    rig.engine.startup()
    _safety(rig)


def scenario_tp_price_rejection(tmp_path):
    rig,pid=_working(tmp_path)
    req=_activate_tp(rig,pid)
    _terminal_tp(rig,req)
    return rig,pid,req


def scenario_protect_cancel(tmp_path):
    rig,pid=_working(tmp_path)
    ref=rig.store.protection(pid)['intent_id']
    reason='Order canceled by system: synthetic broker cancellation'
    rig.broker.orders[ref].status='Cancelled'
    adapter,trade=_native_status(rig,ref,raw='Cancelled',code=202,reason=reason)
    _deliver_status(rig,adapter,trade)
    _settle(rig)
    return rig,pid,rig.broker.orders[ref].req


def scenario_working_price_mismatch(tmp_path):
    rig,pid=_working(tmp_path)
    req=_activate_tp(rig,pid)
    adapter,trade=_native_status(rig,req.intent_id,working_price=req.limit_price+.25,cap=req.limit_price+.10)
    _deliver_status(rig,adapter,trade)
    return rig,pid,req


def scenario_second_stale(tmp_path):
    rig,pid=_working(tmp_path)
    rig.broker.protection_auto_status=False
    _begin_tp(rig,pid)
    ref=rig.store.protection(pid)['intent_id']
    rig.broker.confirm_protection_modify(ref)
    _set_book(rig,age=3.01)
    _settle(rig)
    return rig,pid,None


def test_B23_default_exit_band_is_0_2_percent_and_quote_age_three_seconds(tmp_path):
    rig,pid=_working(tmp_path)
    assert rig.cfg.exit_limit_band_pct == .002
    assert getattr(rig.cfg,'tp_max_quote_age_seconds',None) == 3
    _safety(rig)


@pytest.mark.parametrize('filename',['paper.toml','paper_delayed.toml','paper_test.toml','paper_g3n.toml','live.toml'])
def test_B23_all_shipped_examples_use_0_2_percent_exit_band(filename):
    raw=tomllib.loads((Path(v6lite.__file__).resolve().parent/'examples'/filename).read_text(encoding='utf-8'))
    default_band=LiteConfig.__dataclass_fields__['exit_limit_band_pct'].default
    resolved=raw.get('execution',{}).get('exit_limit_band_pct',default_band)
    assert resolved == .002, 'every shipped example must resolve to the approved 0.2% band'


def test_B23_final_share_B_original_callback_clock_survives_slow_confirmation_and_drives_deadline(tmp_path,monkeypatch):
    rig,pid=_working(tmp_path,qty=1)
    rig.broker.protection_auto_status=False
    _begin_tp(rig,pid)
    row=rig.store.protection(pid)
    assert row['state']=='PROTECT_CANCEL_PENDING'
    ref=row['intent_id']
    rig.broker.orders[ref].status='Cancelled'
    adapter,trade=_native_status(rig,ref,raw='Cancelled',code=202,reason='Order Canceled - reason')
    t0=rig.clock.now()
    _deliver_status(rig,adapter,trade)
    assert rig.store.protection(pid)['gap_b_started_at']==t0.isoformat()
    calls=[]
    with monkeypatch.context() as slow:
        for name in ('open_orders','positions_fresh','executions_today'):
            original=getattr(rig.broker,name)
            def delayed(_name=name,_original=original):
                calls.append(_name)
                rig.advance(seconds=.6)
                rig.refresh_quotes()
                assert rig.broker.quote('AAPL').ts==rig.clock.now()
                return _original()
            slow.setattr(rig.broker,name,delayed)
        _settle(rig)
    row=rig.store.protection(pid)
    assert row['state']=='TP_ACTIVE'
    assert set(calls)=={'open_orders','positions_fresh','executions_today'}
    assert rig.clock.now()>t0
    assert row['gap_b_started_at']==t0.isoformat(), 'three-source latency is part of B, not permission to rewrite T0'
    tp=row['tp_intent_id']
    rig.clock.set(t0+timedelta(seconds=9.99)); rig.refresh_quotes()
    rig.engine._hard_protection_deadlines(rig.clock.now()); _safety(rig)
    assert tp not in rig.broker.cancels
    rig.clock.set(t0+timedelta(seconds=10)); rig.refresh_quotes()
    rig.engine._hard_protection_deadlines(rig.clock.now()); _safety(rig)
    assert tp in rig.broker.cancels


@pytest.mark.parametrize('raw,code,reason',[('Cancelled',202,'Order canceled by system: synthetic rule'),
                                         ('Inactive',201,'Synthetic terminal rejection unrelated to price')])
def test_B23_async_TP_terminal_enters_restore_immediately_without_ten_second_wait(tmp_path,raw,code,reason):
    rig,pid=_working(tmp_path)
    req=_activate_tp(rig,pid)
    t0=rig.clock.now()
    _terminal_tp(rig,req,raw=raw,code=code,reason=reason)
    assert rig.clock.now()==t0
    assert rig.store.protection(pid)['state']=='TP_NEEDS_RESTORE'
    assert rig.store.intent(req.intent_id)['status'] in {IntentStatus.REJECTED,IntentStatus.CANCEL_SEEN,IntentStatus.CANCELLED}
    _settle(rig)
    row=rig.store.protection(pid)
    assert row['pending_qty']==3 and row['state'] in {'RESTORE_PENDING','WORKING'}
    if row['state']!='WORKING':
        rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    assert rig.store.protection(pid)['confirmed_remaining']==3
    _safety(rig)


def test_B23_price_rejection_real_P0_keeps_complete_original_reason_and_send_snapshot(tmp_path):
    rig,pid=_working(tmp_path)
    channel=_card_channel(rig)
    req=_activate_tp(rig,pid)
    quote=rig.broker.quote('AAPL')
    _terminal_tp(rig,req)
    cards=[p for p in channel.accepted if 'P0' in p['title'] and '止盈' in p['title']]
    assert cards, 'price rejection must have its own actual remote P0'
    parts=_reason_parts(cards)
    assert parts and ''.join(value for _,_,value in sorted(parts))==PRICE_REASON
    text='\n'.join(json.dumps(p,ensure_ascii=False) for p in cards)
    fields='\n'.join(f"{f['name']}：{f['value']}" for p in cards for f in p['fields'])
    quantity_pattern=rf'(?:止盈数量|TP数量|数量)[^\n\d]{{0,12}}{req.quantity}(?:\s*股|\b)|TP\s*[=:：]\s*{req.quantity}(?:\s*股|\b)'
    assert re.search(quantity_pattern,fields), 'actual TP quantity needs an explicit label, not a digit inside error 201'
    intent=rig.store.intent(req.intent_id)
    assert re.search(rf'(?:orderId|订单号|订单ID|券商订单号)\s*[:=：]\s*{intent["broker_order_id"]}\b',fields)
    assert re.search(rf'(?:permId|perm_id)\s*[:=：]\s*{intent["perm_id"]}\b',fields)
    # Compare exact labelled prices; 119.7 and 119.70 are the same cents.
    for label,expected in (('发送限价',req.limit_price),('bid',quote.bid),('ask',quote.ask)):
        match=re.search(rf'{label}\s*[:=：]\s*([0-9]+(?:\.[0-9]+)?)',fields)
        assert match, f'actual remote card must include the labelled {label}'
        assert Decimal(match.group(1))==Decimal(str(expected))
    for value in ('AAPL',req.intent_id,str(intent['broker_order_id']),str(intent['perm_id']),'201'):
        assert value in text
    assert '年龄' in text or 'quote_age_seconds' in text
    assert not any('P1' in title and '未发出' in title for title,_ in rig.channel.messages)
    rows=_evidence(rig,pid)
    assert any(e.get('phase')=='status' and e.get('reason')==PRICE_REASON and e.get('error_code')==201 for e in rows)
    assert PRICE_REASON in rig.store.intent(req.intent_id)['note'] or any(e.get('reason')==PRICE_REASON for e in rows)
    _safety(rig)


def test_B23_price_rejection_pause_is_durable_and_quotes_cannot_resubmit_after_restore_or_restart(tmp_path):
    rig,pid,req=scenario_tp_price_rejection(tmp_path)
    raw=rig.store.control(f'tp_pause:{pid}')
    assert raw, 'a broker price rejection must durably pause this position TP stage'
    pause=json.loads(raw)
    assert pause.get('position_id')==pid and pause.get('intent_id')==req.intent_id
    assert pause.get('stage')==str(Purpose.TP1) and pause.get('reason')==PRICE_REASON and pause.get('pause_id')
    _settle(rig)
    row=rig.store.protection(pid)
    if row['state']=='RESTORE_PENDING':
        rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    assert rig.store.protection(pid)['confirmed_remaining']==3
    count=len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES])
    for _ in range(3):
        rig.advance(seconds=31); _set_book(rig); rig.tick(); _settle(rig)
        assert len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES])==count
        assert rig.store.control(f'tp_pause:{pid}')==raw
        _safety(rig)
    _restart(rig,tmp_path)
    rig.advance(seconds=31); _set_book(rig); rig.tick(); _settle(rig)
    assert len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES])==count
    assert rig.store.control(f'tp_pause:{pid}')==raw


@pytest.mark.parametrize('age,accepted',[(3.0,True),(3.01,False)])
def test_B23_first_quote_gate_boundary_precedes_protection_reduction_and_retry_accounting(tmp_path,age,accepted):
    rig,pid=_working(tmp_path)
    rig.broker.protection_auto_status=False
    _queue_tp(rig,pid)
    before=_writes(rig); row=dict(rig.store.protection(pid)); stage=dict(rig.store.stage(pid,Purpose.TP1))
    _set_book(rig,age=age)
    rig.engine._run_queue(); rig.engine._drain(rig.clock.now()); _safety(rig)
    if accepted:
        assert rig.store.protection(pid)['state']=='REDUCE_PENDING'
        assert len(rig.broker.modified_protections)==before[2]+1
    else:
        assert _writes(rig)==before
        assert dict(rig.store.protection(pid))==row
        assert dict(rig.store.stage(pid,Purpose.TP1))==stage
        assert (pid,str(Purpose.TP1)) not in rig.engine._exit_backoff
        assert rig.channel.messages==[]


@pytest.mark.parametrize('bid,ask',[(0.0,130.0),(-1.0,130.0),(130.0,129.0)])
def test_B23_first_quote_gate_invalid_book_never_reduces_or_uses_valid_last(tmp_path,bid,ask):
    rig,pid=_working(tmp_path)
    rig.broker.protection_auto_status=False
    _queue_tp(rig,pid)
    before=_writes(rig); row=dict(rig.store.protection(pid)); stage=dict(rig.store.stage(pid,Purpose.TP1))
    _set_book(rig,bid=bid,ask=ask,last=120.0)
    rig.engine._run_queue(); rig.engine._drain(rig.clock.now()); _safety(rig)
    assert _writes(rig)==before
    assert dict(rig.store.protection(pid))==row and dict(rig.store.stage(pid,Purpose.TP1))==stage
    assert (pid,str(Purpose.TP1)) not in rig.engine._exit_backoff
    assert rig.channel.messages==[]


def test_B23_second_quote_gate_restores_original_B_without_price_pause_or_TP_send(tmp_path):
    rig,pid,_=scenario_second_stale(tmp_path)
    row=rig.store.protection(pid)
    assert not [r for r in rig.broker.sent if r.purpose in TP_PURPOSES]
    assert row['state']=='RESTORE_PENDING' and row['pending_qty']==3
    assert row['gap_b_started_at'] is not None
    assert rig.store.control(f'tp_pause:{pid}') is None
    assert rig.store.stage(pid,Purpose.TP1)['attempts']==0
    assert any('P1' in title and '未发出' in title for title,_ in rig.channel.messages)
    start=datetime.fromisoformat(row['gap_b_started_at'])
    rig.advance(seconds=4)
    rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    row=rig.store.protection(pid)
    assert row['state']=='WORKING' and row['confirmed_remaining']==3 and row['gap_b_started_at'] is None
    gaps=[json.loads(r['payload']) for r in rig.store.conn.execute("SELECT payload FROM audit WHERE kind='PROTECT_GAP'")]
    assert any(g.get('gap_kind')=='B' and g['secs']>=(rig.clock.now()-start).total_seconds() for g in gaps)
    _safety(rig)


def test_B23_fresh_TP_uses_bid_times_band_ROUND_UP_cent_not_mark(tmp_path):
    rig,pid=_working(tmp_path)
    rig.broker.protection_auto_status=False
    _begin_tp(rig,pid,bid=120.37,ask=120.40,last=120.99)
    ref=rig.store.protection(pid)['intent_id']
    rig.broker.confirm_protection_modify(ref); _settle(rig)
    [req]=[r for r in rig.broker.sent if r.purpose in TP_PURPOSES]
    expected=float((Decimal('120.37')*(Decimal('1')-Decimal('0.002'))).quantize(Decimal('.01'),rounding=ROUND_UP))
    assert req.limit_price==expected==120.13
    _safety(rig)


@pytest.mark.parametrize('purpose',[Purpose.ENTRY,Purpose.TP1,Purpose.TP2,Purpose.TP3])
def test_B23_native_LMT_constructor_explicitly_disables_PMA_without_override_or_algo(tmp_path,monkeypatch,purpose):
    import ib_insync
    original_constructor=ib_insync.LimitOrder
    kwargs_seen=[]
    def observe_constructor(*args,**kwargs):
        kwargs_seen.append(dict(kwargs))
        return original_constructor(*args,**kwargs)
    monkeypatch.setattr(ib_insync,'LimitOrder',observe_constructor)
    rig=Rig(tmp_path,protection=ProtectionConfig(enabled=True))
    broker=IbkrBroker(rig.cfg); broker.ib=_LocalIB()
    broker._contracts['AAPL']=SimpleNamespace(symbol='AAPL',conId=10000,secType='STK',currency='USD',exchange='SMART')
    req=OrderRequest('v6l-b23-pma-'+str(purpose),'AAPL',Side.BUY if purpose is Purpose.ENTRY else Side.SELL,3,332.01,purpose)
    broker.submit_limit(req)
    [(_,order)]=broker.ib.placed
    assert order.orderType=='LMT' and order.usePriceMgmtAlgo is False
    assert kwargs_seen and all(k.get('usePriceMgmtAlgo') is False for k in kwargs_seen), 'False must be explicit at the real SDK constructor boundary'
    assert order.overridePercentageConstraints is False and order.algoStrategy==''


@pytest.mark.parametrize('modify',[False,True])
def test_B23_native_STP_submit_and_same_ID_modify_explicitly_disable_PMA(tmp_path,monkeypatch,modify):
    import ib_insync
    original_constructor=ib_insync.StopOrder
    kwargs_seen=[]
    def observe_constructor(*args,**kwargs):
        kwargs_seen.append(dict(kwargs))
        return original_constructor(*args,**kwargs)
    monkeypatch.setattr(ib_insync,'StopOrder',observe_constructor)
    rig=Rig(tmp_path,protection=ProtectionConfig(enabled=True))
    broker=IbkrBroker(rig.cfg); broker.ib=_LocalIB()
    contract=SimpleNamespace(symbol='AAPL',conId=10000,secType='STK',currency='USD',exchange='SMART')
    broker._contracts['AAPL']=contract
    req=ProtectionRequest('v6l-protect-b23-pma','pos-b23','AAPL',3,330.41,None)
    broker.submit_stop(req)
    if modify:
        broker.modify_protection(replace(req,quantity=2,broker_order_id=77,perm_id=50077,
                                         con_id=10000,client_id=rig.cfg.client_id,account=rig.cfg.account,allow_lookup=False))
    order=broker.ib.placed[-1][1]
    assert order.orderType=='STP' and order.usePriceMgmtAlgo is False
    assert kwargs_seen and all(k.get('usePriceMgmtAlgo') is False for k in kwargs_seen), 'False must be explicit at submit and same-ID modification constructors'
    assert order.overridePercentageConstraints is False and order.algoStrategy==''
    assert 'lmtPrice' not in order.nonDefaults()


@pytest.mark.parametrize('kind',['working','cap'])
def test_B23_TP_working_or_cap_price_difference_pages_P1_blocks_repeat_and_never_rewrites_ledger(tmp_path,kind):
    rig,pid=_working(tmp_path)
    req=_activate_tp(rig,pid)
    original=rig.store.intent(req.intent_id)['limit_price']
    adapter,trade=_native_status(rig,req.intent_id,working_price=original+.25 if kind=='working' else None,
                                cap=original+.10 if kind=='cap' else 0)
    _deliver_status(rig,adapter,trade)
    assert any('P1' in title and '价格' in title for title,_ in rig.channel.messages)
    assert rig.store.intent(req.intent_id)['limit_price']==original
    rows=_evidence(rig,pid)
    expected_cap=trade.orderStatus.mktCapPrice if kind=='cap' else None
    assert any(e.get('sent_price')==original and e.get('working_price')==trade.order.lmtPrice
               and 'mkt_cap_price' in e and e['mkt_cap_price']==expected_cap for e in rows)
    before=len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES])
    rig.advance(seconds=31); _set_book(rig); rig.tick(); _settle(rig)
    assert len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES])==before
    _safety(rig)


def test_B23_PROTECT_price_difference_stays_P0_and_audits_working_aux_and_cap(tmp_path):
    rig,pid=_working(tmp_path)
    row=dict(rig.store.protection(pid)); ref=row['intent_id']
    adapter,trade=_native_status(rig,ref,working_price=row['trigger_price']+.25,cap=row['trigger_price']+.10)
    _deliver_status(rig,adapter,trade)
    assert any('P0' in title for title,_ in rig.channel.messages)
    assert rig.store.protection(pid)['trigger_price']==row['trigger_price']
    assert rig.store.intent(ref)['hold_reason']
    all_audits=[json.loads(r['payload']) for r in rig.store.conn.execute('SELECT payload FROM audit')]
    assert any(e.get('aux_price')==trade.order.auxPrice and e.get('mkt_cap_price')==trade.orderStatus.mktCapPrice for e in all_audits)
    _safety(rig)


def test_B23_actual_cancel_card_has_consistent_cancel_fact_chinese_diagnosis_and_exact_quantity(tmp_path):
    rig,pid,req=scenario_protect_cancel(tmp_path)
    cards=[(title,body) for title,body in rig.channel.messages if '券商已取消保护单' in title]
    assert cards
    assert all('券商已明确拒绝该保护请求' not in body for _,body in cards)
    assert all('GTC absent from complete working snapshot' not in body for _,body in cards)
    assert all('broker explicitly refused the STP request' not in body for _,body in cards)
    assert any(all(x in body for x in ('持仓数量=3 股','保护单已确认=0 股','未保护=3 股')) for _,body in cards)
    assert any('Order canceled by system: synthetic broker cancellation' in body for _,body in cards)
    _safety(rig)


def test_B23_TP_send_status_fill_automatic_evidence_and_daily_bid_difference(tmp_path):
    rig,pid=_working(tmp_path)
    req=_activate_tp(rig,pid)
    bid=rig.broker.quote('AAPL').bid
    adapter,trade=_native_status(rig,req.intent_id,cap=req.limit_price)
    _deliver_status(rig,adapter,trade)
    fill=rig.broker.fill(req.intent_id,price=bid-.04,emit=False)
    rig.engine._on_urgent_broker_event(fill); _safety(rig)
    _settle(rig)
    row=rig.store.protection(pid)
    if row['state']=='PRICE_PENDING':
        rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    events=_evidence(rig,pid)
    phases={e.get('phase') for e in events}
    assert {'reduce_request','reduce_confirm','send','status','fill'}.issubset(phases)
    [sent]=[e for e in events if e.get('phase')=='send' and e.get('intent_id')==req.intent_id]
    for key in ('bid','ask','quote_age_seconds','market_data_type','calculated_price','sent_price'):
        assert key in sent
    assert sent['bid']==bid and sent['sent_price']==req.limit_price and sent['quote_age_seconds']<=3
    [executed]=[e for e in events if e.get('phase')=='fill' and e.get('exec_id')==fill.exec_id]
    assert executed.get('fill_price')==fill.price and executed.get('bid_at_send')==bid
    assert executed.get('fill_minus_bid')==pytest.approx(fill.price-bid)
    assert executed.get('gap_b_secs') is not None
    text=daily_report(rig.store,rig.cfg,date(2026,10,6))
    health=text.partition('- 券商端保护:')[2].partition('- kill switch:')[0]
    assert req.intent_id in health and 'bid' in health and '空窗' in health
    assert f'{fill.price:.2f}' in health and f'{bid:.2f}' in health


def test_B23_dry_run_every_protection_TP_entry_point_has_zero_physical_write(tmp_path):
    rig,pid=_working(tmp_path)
    calls=[]
    for name in ('submit_limit','submit_stop','modify_protection','cancel','submit_market'):
        def record(*args,_name=name,**kwargs):
            calls.append(_name)
            raise AssertionError('dry-run physical broker write '+_name)
        setattr(rig.broker,name,record)
    before=_writes(rig)
    _queue_tp(rig,pid)
    rig.engine.dry_run=True
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    rig.engine._protection_begin_tp(rig.clock.now(),pid,Purpose.TP1)
    rig.engine._protection_submit_first(rig.clock.now(),rig.store.protection(pid),3)
    rig.engine._protection_cancel(rig.clock.now(),rig.store.protection(pid),'dry-test')
    assert calls==[] and _writes(rig)==before
    _safety(rig)

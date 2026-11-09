"""B23 independent extension contracts; no imports from same-batch new specs.

All mutable state/config is under tmp_path. Broker callbacks are native-shaped
local doubles. No network, runtime database, operator config, or main loop.
Mapping: B original clock (1.1); async terminal restoration (1.2); full reason
P0 (1.3); durable stage pause (1.4); PMA/working/cap (1.5); actual scenario
quantities (1.6); two book-age checks and bid pricing (1.7); Chinese facts (1.8);
per-phase automatic evidence and daily attribution (section 5).
"""
from __future__ import annotations

import json
import socket
import tomllib
import v6lite
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_UP
from pathlib import Path
from types import SimpleNamespace

import pytest

from v6lite.alerts import AlertCard, Alerter, MemoryChannel, P1_ORANGE
from v6lite.broker.base import BrokerError
from v6lite.broker.fake import FakeBroker
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ConfigError, LiteConfig, ProtectionConfig, TestConfig as LiteTestConfig, load_config
from v6lite.engine import Engine, StartupError
from v6lite.models import IntentStatus, OrderRequest, ProtectionRequest, Purpose, Side
from v6lite.report import daily_report
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et
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




def _config_path(tmp_path, *, quote_age=None, state_dir=None):
    path=tmp_path/'b23-contract.toml'
    text=('mode="paper"\n[ibkr]\naccount="DU0000000"\nport=4002\nclient_id=131\n'
          '[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n')
    if state_dir is not None:
        text+='[paths]\nstate_dir='+json.dumps(str(Path(state_dir).as_posix()))+'\n'
    if quote_age is not None:
        text+='[execution]\ntp_max_quote_age_seconds='+str(quote_age).lower()+'\n'
    path.write_text(text,encoding='utf-8')
    return path


def _paused(tmp_path):
    rig,pid=_working(tmp_path)
    req=_activate_tp(rig,pid)
    _terminal_tp(rig,req)
    raw=rig.store.control(f'tp_pause:{pid}')
    assert raw, 'real broker price rejection must create a durable pause before resume can be considered'
    pause=json.loads(raw)
    _settle(rig)
    row=rig.store.protection(pid)
    if row['state']=='RESTORE_PENDING':
        rig.broker.confirm_protection_modify(row['intent_id']); _settle(rig)
    assert rig.store.protection(pid)['state']=='WORKING'
    assert rig.store.protection(pid)['confirmed_remaining']==rig.store.position(pid)['qty_open']==3
    _safety(rig)
    return rig,pid,req,pause


def _request_resume(rig,pid,pause,**changes):
    request={'symbol':'AAPL','position_id':pid,'pause_id':pause['pause_id'],
             'operator':'John','reason':'synthetic verified TP resume','at':rig.clock.now().isoformat()}
    request.update(changes)
    rig.store.set_control(rig.clock.now(),f'resume_tp:{pid}',json.dumps(request))
    return request


@pytest.mark.parametrize('value',[1,3,10])
def test_B23_quote_age_config_accepts_documented_range_and_binds_fingerprint(tmp_path,value):
    path=_config_path(tmp_path,quote_age=value)
    error=None
    try:
        cfg=load_config(path)
    except ConfigError as exc:
        error=str(exc)
    assert error is None, 'valid documented tp_max_quote_age_seconds rejected: '+str(error)
    assert getattr(cfg,'tp_max_quote_age_seconds',None)==value
    other=_config_path(tmp_path,quote_age=2 if value!=2 else 3)
    assert cfg.fingerprint()!=load_config(other).fingerprint()


@pytest.mark.parametrize('value',[0,11,True])
def test_B23_quote_age_config_rejects_out_of_range_and_bool(tmp_path,value):
    with pytest.raises(ConfigError):
        load_config(_config_path(tmp_path,quote_age=value))


@pytest.mark.parametrize('case',['missing_bid','infinite_bid','infinite_ask','nan_bid','future_ts','naive_ts'])
def test_B23_strict_TP_quote_gate_rejects_invalid_book_time_without_phase_retry_or_cards(tmp_path,case):
    rig,pid=_working(tmp_path)
    rig.broker.protection_auto_status=False
    _queue_tp(rig,pid)
    row=dict(rig.store.protection(pid)); stage=dict(rig.store.stage(pid,Purpose.TP1)); writes=_writes(rig)
    q=rig.broker.quote('AAPL')
    bid,ask,ts=q.bid,q.ask,rig.clock.now()
    if case=='missing_bid': bid=None
    elif case=='infinite_bid': bid=float('inf');ask=float('inf')
    elif case=='infinite_ask': ask=float('inf')
    elif case=='nan_bid': bid=float('nan')
    elif case=='future_ts': ts+=timedelta(seconds=.01)
    elif case=='naive_ts': ts=ts.replace(tzinfo=None)
    rig.broker.set_quote('AAPL',bid=bid,ask=ask,last=q.last,ts=ts)
    rig.engine._run_queue();rig.engine._drain(rig.clock.now())
    assert _writes(rig)==writes
    assert dict(rig.store.protection(pid))==row and dict(rig.store.stage(pid,Purpose.TP1))==stage
    assert (pid,str(Purpose.TP1)) not in rig.engine._exit_backoff
    assert rig.channel.messages==[]
    _safety(rig)


def test_B23_first_stale_rounds_persist_counter_but_log_once_per_minute_without_trade_mutation(tmp_path,caplog):
    import logging
    rig,pid=_working(tmp_path)
    rig.broker.protection_auto_status=False
    _queue_tp(rig,pid)
    row=dict(rig.store.protection(pid)); stage=dict(rig.store.stage(pid,Purpose.TP1)); writes=_writes(rig)
    caplog.set_level(logging.DEBUG);caplog.clear()
    for _ in range(3):
        _set_book(rig,age=3.01)
        # Three actual TP decisions, not calls skipped by the unchanged 30s
        # global scheduler. No cadence flag, stage, retry or gate is fabricated.
        held={p.symbol:p.quantity for p in rig.broker.positions()}
        rig.engine._evaluate_position_exit(rig.clock.now(),rig.store.position(pid),held)
        rig.engine._run_queue()
        assert _writes(rig)==writes
        assert dict(rig.store.protection(pid))==row and dict(rig.store.stage(pid,Purpose.TP1))==stage
        assert (pid,str(Purpose.TP1)) not in rig.engine._exit_backoff
        assert rig.channel.messages==[]
        _safety(rig)
    raw=rig.store.control(f'tp_quote_skip:2026-10-06:{pid}')
    assert raw and json.loads(raw).get('count')==3
    skips=[e for e in _evidence(rig,pid) if e.get('phase')=='quote_skip']
    assert len(skips)==3
    logs=[r for r in caplog.records if 'TP_QUOTE_SKIP' in r.getMessage()]
    assert len(logs)==1 and logs[0].levelno<=logging.INFO
    rig.advance(seconds=60)
    _set_book(rig,age=3.01)
    rig.engine._evaluate_exits(rig.clock.now());rig.engine._run_queue()
    assert json.loads(rig.store.control(f'tp_quote_skip:2026-10-06:{pid}'))['count']==4
    logs=[r for r in caplog.records if 'TP_QUOTE_SKIP' in r.getMessage()]
    assert len(logs)==2 and all(r.levelno<=logging.INFO for r in logs)
    health=daily_report(rig.store,rig.cfg,date(2026,10,6)).partition('- 券商端保护:')[2].partition('- kill switch:')[0]
    assert pid in health and '跳过' in health and '4' in health
    assert _writes(rig)==writes and rig.channel.messages==[]


def test_B23_dry_stale_TP_gate_has_no_counter_audit_pause_or_broker_writes(tmp_path):
    rig,pid=_working(tmp_path)
    _queue_tp(rig,pid)
    _set_book(rig,age=3.01)
    rig.engine.dry_run=True
    before=rig.store.conn.total_changes; writes=_writes(rig); evidence=_evidence(rig,pid)
    rig.engine._protection_begin_tp(rig.clock.now(),pid,Purpose.TP1)
    assert rig.store.conn.total_changes==before
    assert rig.store.control(f'tp_quote_skip:2026-10-06:{pid}') is None
    assert rig.store.control(f'tp_pause:{pid}') is None
    assert _evidence(rig,pid)==evidence and _writes(rig)==writes
    _safety(rig)


def _g3t_working(tmp_path,monkeypatch):
    from v6lite import __main__ as cli
    from v6lite.tests.test_batch16 import _test_tp_timeout_config
    rig=Rig(tmp_path,state_dir=str(tmp_path/'paper-test'),client_id=132,
            test=LiteTestConfig(enabled=True,qty=3,stop_mult=.3,tp1_mult=.3,tp2_mult=.5,tp3_mult=.8),
            protection=ProtectionConfig(enabled=True),scan_times_et=('13:30',))
    rig.market_calls=[]
    def forbidden(*args,**kwargs):
        rig.market_calls.append((args,kwargs));raise AssertionError('MKT forbidden')
    rig.broker.submit_market=forbidden
    rig.start();path,_=_test_tp_timeout_config(tmp_path)
    monkeypatch.setattr(cli,'_now',rig.clock.now)
    rig.at(et(10,0))
    assert cli.main(['test-entry','AAPL','--operator','John','--config',str(path)])==0
    rig.at(et(10,1));rig.tick()
    [entry]=[r for r in rig.broker.sent if r.purpose is Purpose.ENTRY]
    assert entry.quantity==3
    rig.broker.fill(entry.intent_id);rig.advance(seconds=1);rig.tick()
    [pos]=rig.store.open_positions();pid=pos['position_id']
    assert rig.store.protection(pid)['state']=='WORKING'
    _safety(rig);rig.channel.messages.clear()
    return rig,pid,path


def test_B23_existing_queued_timeout_control_cannot_override_fresh_bid_pricing(tmp_path,monkeypatch):
    rig,pid,path=_g3t_working(tmp_path,monkeypatch)
    epoch=rig.store.control('test_entry_ready')
    assert epoch and epoch==rig.engine._test_entry_epoch
    # This exact old persisted operator request represents a pre-upgrade queue.
    # It creates no trade/phase/hold and cannot grant a pricing exemption.
    rig.store.set_control(rig.clock.now(),'test_tp_nonmarketable_once:AAPL',json.dumps({
        'request_id':'synthetic-preupgrade-request','symbol':'AAPL','operator':'John',
        'at':rig.clock.now().isoformat(),'epoch':epoch}))
    rig.broker.protection_auto_status=False
    _queue_tp(rig,pid)
    rig.engine._run_queue();rig.engine._drain(rig.clock.now());_safety(rig)
    row=rig.store.protection(pid);assert row['state']=='REDUCE_PENDING'
    rig.broker.confirm_protection_modify(row['intent_id']);_settle(rig)
    [req]=[r for r in rig.broker.sent if r.purpose in TP_PURPOSES]
    bid=rig.broker.quote('AAPL').bid
    expected=float((Decimal(str(bid))*Decimal('.998')).quantize(Decimal('.01'),rounding=ROUND_UP))
    assert req.limit_price==expected and req.limit_price<=bid
    _safety(rig)


def test_B23_old_timeout_command_explicitly_refuses_to_manufacture_nonmarketable_TP(tmp_path,monkeypatch,capsys):
    from v6lite import __main__ as cli
    rig,pid,path=_g3t_working(tmp_path,monkeypatch)
    before=_writes(rig)
    assert cli.main(['test-tp-timeout','AAPL','--operator','John','--config',str(path)])==2
    assert rig.store.control('test_tp_nonmarketable_once:AAPL') is None
    assert _writes(rig)==before
    capture=capsys.readouterr()
    output=capture.err+capture.out
    assert 'bid' in output or '不再' in output or 'disabled' in output.lower()


class _Event:
    def __init__(self):self.handlers=[]
    def __iadd__(self,handler):self.handlers.append(handler);return self
    def emit(self,*args):
        for handler in tuple(self.handlers):handler(*args)


class _HookIB(_LocalIB):
    def __init__(self):
        super().__init__();self.connected=False
        for name in ('execDetailsEvent','commissionReportEvent','orderStatusEvent','openOrderEvent',
                     'disconnectedEvent','errorEvent','accountValueEvent'):
            setattr(self,name,_Event())
    def connect(self,*args,**kwargs):self.connected=True
    def disconnect(self):self.connected=False
    def isConnected(self):return self.connected
    def reqMarketDataType(self,*args):pass


def test_B23_openOrder_only_working_price_change_is_hooked_once_and_reaches_real_P1(tmp_path):
    rig,pid=_working(tmp_path);req=_activate_tp(rig,pid)
    adapter,trade=_native_status(rig,req.intent_id,working_price=req.limit_price+.25)
    ib=_HookIB();ib.snapshot=[trade];adapter.ib=ib
    adapter.connect();adapter.connect()
    assert len(ib.openOrderEvent.handlers)==1, 'actual openOrder event must be wired exactly once across reconnect'
    ib.openOrderEvent.emit(trade)
    events=adapter.drain_events()
    assert events, 'openOrder-only price change cannot depend on a subsequent orderStatus callback'
    for event in events:rig.engine._on_urgent_broker_event(event)
    assert any('P1' in title and '价格' in title for title,_ in rig.channel.messages)
    assert rig.store.intent(req.intent_id)['limit_price']==req.limit_price
    _safety(rig)


@pytest.mark.parametrize('cap',[0.0,float('nan'),1.7976931348623157e308])
def test_B23_unknown_LMT_cap_is_audited_as_unknown_without_false_mismatch_or_pause(tmp_path,cap):
    rig,pid=_working(tmp_path);req=_activate_tp(rig,pid)
    adapter,trade=_native_status(rig,req.intent_id,cap=cap)
    before=len(rig.channel.messages)
    _deliver_status(rig,adapter,trade)
    assert not any(('P0' in t or 'P1' in t) and '价格' in t for t,_ in rig.channel.messages[before:])
    assert rig.store.control(f'tp_pause:{pid}') is None
    assert rig.store.intent(req.intent_id)['limit_price']==req.limit_price
    rows=_evidence(rig,pid)
    assert any('mkt_cap_price' in e and e['mkt_cap_price'] is None for e in rows)
    _safety(rig)


def test_B23_STP_positive_cap_is_independent_evidence_not_a_trigger_price_comparison(tmp_path):
    rig,pid=_working(tmp_path);row=dict(rig.store.protection(pid));ref=row['intent_id']
    cap=float(row['trigger_price'])-.50
    adapter,trade=_native_status(rig,ref,cap=cap)
    before=len(rig.channel.messages)
    _deliver_status(rig,adapter,trade)
    assert not any('P0' in t or 'P1' in t for t,_ in rig.channel.messages[before:])
    assert not rig.store.intent(ref)['hold_reason'] and rig.store.protection(pid)['state']=='WORKING'
    audits=[json.loads(r['payload']) for r in rig.store.conn.execute('SELECT payload FROM audit')]
    assert any(e.get('aux_price')==row['trigger_price'] and e.get('mkt_cap_price')==cap for e in audits)
    _safety(rig)


def test_B23_ENTRY_working_price_difference_real_P1_blocks_later_scan_without_ledger_reprice(tmp_path):
    rig=Rig(tmp_path,protection=ProtectionConfig(enabled=True),scan_times_et=('10:00','10:02'))
    rig.market_calls=[]
    def forbidden(*args,**kwargs):rig.market_calls.append(1);raise AssertionError('MKT forbidden')
    rig.broker.submit_market=forbidden
    rig.start();rig.at(et(10,0,30));rig.tick()
    [entry]=[r for r in rig.broker.sent if r.purpose is Purpose.ENTRY]
    adapter,trade=_native_status(rig,entry.intent_id,working_price=entry.limit_price+.25)
    _deliver_status(rig,adapter,trade)
    assert any('P1' in title and '价格' in title for title,_ in rig.channel.messages)
    assert rig.store.intent(entry.intent_id)['limit_price']==entry.limit_price
    rig.broker.status(entry.intent_id,'Cancelled')
    rig.advance(seconds=61);rig.tick()
    rig.at(et(10,2,30));rig.tick()
    assert len([r for r in rig.broker.sent if r.purpose is Purpose.ENTRY])==1
    _safety(rig)


@pytest.mark.parametrize('wrong',['account','orderId','permId','conId'])
def test_B23_foreign_TP_terminal_cannot_restore_or_pause_from_local_ref_alone(tmp_path,wrong):
    rig,pid=_working(tmp_path);req=_activate_tp(rig,pid)
    before=_writes(rig);row=dict(rig.store.protection(pid))
    _terminal_tp(rig,req,wrong=wrong)
    _settle(rig)
    assert _writes(rig)==before
    assert rig.store.protection(pid)['confirmed_remaining']==row['confirmed_remaining']==2
    assert rig.store.control(f'tp_pause:{pid}') is None
    assert rig.store.intent(req.intent_id)['hold_reason']
    _safety(rig)


def test_B23_other_broker_code_nonterminal_keeps_full_reason_but_grants_no_restore_or_pause(tmp_path):
    rig,pid=_working(tmp_path);req=_activate_tp(rig,pid)
    reason='Synthetic matched-order warning code 399: '+('保留原始告警🍎 '*80)+'END-WARNING-399'
    before=_writes(rig);row=dict(rig.store.protection(pid))
    adapter,trade=_native_status(rig,req.intent_id,raw='Submitted',code=399,reason=reason)
    _deliver_status(rig,adapter,trade)
    events=_evidence(rig,pid)
    assert any(e.get('error_code')==399 and e.get('reason')==reason for e in events)
    assert rig.store.protection(pid)['state']=='TP_ACTIVE' and rig.store.protection(pid)['confirmed_remaining']==row['confirmed_remaining']
    assert rig.store.control(f'tp_pause:{pid}') is None and _writes(rig)==before
    _safety(rig)


def test_B23_exact_episode_manual_request_clears_pause_only_after_full_coverage_without_same_tick_resend(tmp_path):
    rig,pid,req,pause=_paused(tmp_path)
    count=len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES])
    _request_resume(rig,pid,pause)
    rig.tick();_safety(rig)
    assert rig.store.control(f'tp_pause:{pid}') is None
    assert len([r for r in rig.broker.sent if r.purpose in TP_PURPOSES])==count
    assert rig.store.protection(pid)['confirmed_remaining']==3
    audits=[json.loads(r['payload']) for r in rig.store.conn.execute('SELECT payload FROM audit')]
    assert any(e.get('pause_id')==pause['pause_id'] and e.get('operator')=='John' and e.get('reason')=='synthetic verified TP resume' for e in audits)


@pytest.mark.parametrize('change',[{'pause_id':'wrong-episode'},{'position_id':'other-position'},{'symbol':'TSLA'}])
def test_B23_wrong_episode_manual_resume_cannot_clear_pause_or_emit_TP(tmp_path,change):
    rig,pid,req,pause=_paused(tmp_path)
    raw=rig.store.control(f'tp_pause:{pid}');before=_writes(rig)
    _request_resume(rig,pid,pause,**change)
    rig.tick();_safety(rig)
    assert rig.store.control(f'tp_pause:{pid}')==raw and _writes(rig)==before


@pytest.mark.parametrize('source',['open_orders','positions_fresh','executions_today'])
def test_B23_manual_resume_requires_all_three_current_sources_even_when_STP_row_looks_working(tmp_path,monkeypatch,source):
    rig,pid,req,pause=_paused(tmp_path)
    raw=rig.store.control(f'tp_pause:{pid}');before=_writes(rig)
    def unavailable(*args,**kwargs):raise BrokerError('synthetic '+source+' unavailable')
    monkeypatch.setattr(rig.broker,source,unavailable)
    _request_resume(rig,pid,pause)
    rig.tick()
    assert rig.store.control(f'tp_pause:{pid}')==raw and _writes(rig)==before
    _safety(rig)


def test_B23_manual_resume_external_SELL_or_dry_cannot_clear_existing_pause(tmp_path):
    rig,pid,req,pause=_paused(tmp_path)
    raw=rig.store.control(f'tp_pause:{pid}')
    external=OrderRequest('external-b23-resume-seller','AAPL',Side.SELL,1,120.,Purpose.TP1)
    rig.broker._register(external)
    # A pre-existing external SELL plus full S exceeds P; the resume must stay
    # closed while normal safety processing may only reduce/cancel protection.
    _request_resume(rig,pid,pause);rig.tick()
    assert rig.store.control(f'tp_pause:{pid}')==raw
    assert not [r for r in rig.broker.sent if r.purpose in TP_PURPOSES and r.intent_id!=req.intent_id and r.intent_id!=external.intent_id]
    rig.broker.orders[external.intent_id].status='Cancelled'
    rig.engine.dry_run=True
    _request_resume(rig,pid,pause);rig.tick()
    assert rig.store.control(f'tp_pause:{pid}')==raw


def test_B23_resume_TP_CLI_only_queues_exact_durable_request_and_has_zero_broker_writes(tmp_path,monkeypatch):
    from v6lite import __main__ as cli
    rig,pid,req,pause=_paused(tmp_path)
    path=_config_path(tmp_path,state_dir=rig.cfg.resolved_state_dir())
    monkeypatch.setattr(cli,'_now',rig.clock.now)
    before=_writes(rig);raw=rig.store.control(f'tp_pause:{pid}')
    try:
        result=cli.main(['resume-tp','AAPL','--position-id',pid,'--pause-id',pause['pause_id'],
                         '--operator','John','--reason','synthetic reviewed restart','--config',str(path)])
    except SystemExit as exc:
        result=exc.code
    assert result==0, 'public CLI must support the reviewed durable resume request'
    request=rig.store.control(f'resume_tp:{pid}')
    assert request and json.loads(request)['pause_id']==pause['pause_id']
    assert json.loads(request)['position_id']==pid and json.loads(request)['operator']=='John'
    assert rig.store.control(f'tp_pause:{pid}')==raw and _writes(rig)==before



def test_B23_price_rejection_ACK_deduplicates_after_31_minutes_and_successful_restart_without_global_mute(tmp_path):
    rig,pid=_working(tmp_path);channel=_card_channel(rig);req=_activate_tp(rig,pid)
    adapter,trade=_terminal_tp(rig,req)
    first=_reason_parts(channel.accepted)
    assert first and ''.join(v for _,_,v in sorted(first))==PRICE_REASON
    assert len({n for n,_,_ in first})==len(first)
    _settle(rig)
    row=rig.store.protection(pid)
    if row['state']=='RESTORE_PENDING':
        rig.broker.confirm_protection_modify(row['intent_id']);_settle(rig)
    assert rig.store.protection(pid)['state']=='WORKING'
    pause=rig.store.control(f'tp_pause:{pid}');assert pause
    writes=_writes(rig);channel.messages.clear();accepted=len(channel.accepted)
    rig.advance(minutes=31)
    _deliver_status(rig,adapter,trade);_settle(rig)
    assert not [(t,b) for t,b in rig.channel.messages if 'P0' in t or 'P1' in t]
    assert len(channel.accepted)==accepted and rig.store.control(f'tp_pause:{pid}')==pause
    assert _writes(rig)==writes
    _restart(rig,tmp_path)  # Must succeed with the restored STP, not swallow StartupError.
    rig.channel.messages.clear()
    _deliver_status(rig,adapter,trade);_settle(rig)
    assert not [(t,b) for t,b in rig.channel.messages if 'P0' in t or 'P1' in t]
    assert rig.store.control(f'tp_pause:{pid}')==pause and _writes(rig)==writes
    card=AlertCard('🟠 P1 · B23 独立事件',P1_ORANGE,(('独立证据','B23-independent-P1-visible'),))
    rig.alerter.send(rig.clock.now(),'P1','B23 independent synthetic incident','B23-independent-P1-visible',card=card)
    assert any('P1' in t and 'B23-independent-P1-visible' in b for t,b in rig.channel.messages)
    rig.alerter.send(rig.clock.now(),'INFO','IB Gateway responding','B23 synthetic recovered connection')
    assert any('IB Gateway 已恢复应答' in t for t,_ in rig.channel.messages)
    _safety(rig)


def test_B23_price_rejection_unACKed_reason_replay_retries_only_remaining_remote_parts(tmp_path):
    rig,pid=_working(tmp_path);req=_activate_tp(rig,pid)
    channel=_card_channel(rig,fail_attempts=[2])
    adapter,trade=_terminal_tp(rig,req)
    assert len(channel.attempts)>=2, 'long actual broker reason must use actual multipart remote delivery'
    acked=list(channel.accepted);first=_reason_parts(acked)
    assert first and len(first)<first[0][1], 'the deliberately failed second card must still be outstanding'
    before=_writes(rig)
    _deliver_status(rig,adapter,trade)
    parts=_reason_parts(channel.accepted)
    assert sorted(n for n,_,_ in parts)==list(range(1,parts[0][1]+1))
    assert len({n for n,_,_ in parts})==len(parts), 'ACKed reason segments cannot repeat on retry'
    assert ''.join(v for _,_,v in sorted(parts))==PRICE_REASON
    for payload in acked:assert channel.accepted.count(payload)==1
    attempts=len(channel.attempts)
    _deliver_status(rig,adapter,trade)
    assert len(channel.attempts)==attempts and _writes(rig)==before
    assert not any('P1' in t for t,_ in channel.messages)
    assert rig.store.control(f'tp_pause:{pid}')
    _safety(rig)


@pytest.mark.parametrize('block',['native_hold','operator_freeze','corrupt_pause'])
def test_B23_manual_resume_preserves_real_hold_freeze_and_corrupt_pause_fail_closed(tmp_path,block):
    rig,pid,req,pause=_paused(tmp_path)
    if block=='native_hold':
        ref=rig.store.protection(pid)['intent_id']
        adapter,trade=_native_status(rig,ref,wrong='permId')
        _deliver_status(rig,adapter,trade)
        assert rig.store.intent(ref)['hold_reason'], 'foreign active native identity must create the real hold'
    elif block=='operator_freeze':
        # A genuine durable gate, never deleted or fabricated as a TP permission.
        rig.store.set_control(rig.clock.now(),'frozen:AAPL','synthetic operator freeze')
    else:
        # Corruption of an already naturally created pause is the negative input.
        rig.store.set_control(rig.clock.now(),f'tp_pause:{pid}','{invalid-json')
    raw=rig.store.control(f'tp_pause:{pid}');before=_writes(rig)
    _request_resume(rig,pid,pause);rig.tick()
    assert rig.store.control(f'tp_pause:{pid}')==raw and _writes(rig)==before
    if block=='native_hold':assert rig.store.intent(ref)['hold_reason']
    elif block=='operator_freeze':assert rig.store.control('frozen:AAPL')=='synthetic operator freeze'
    _safety(rig)


@pytest.mark.parametrize('incomplete',['execution_query_failed','execution_reply_partial'])
def test_B23_cancel_with_late_partial_fill_needs_complete_three_sources_before_restore_and_every_write_reserves_possible_T(tmp_path,monkeypatch,incomplete):
    rig,pid=_working(tmp_path,qty=6);req=_activate_tp(rig,pid)
    assert req.quantity>=2
    initial=dict(rig.store.protection(pid))
    assert initial['confirmed_remaining']==6-req.quantity
    adapter,trade=_terminal_tp(rig,req,raw='Cancelled',code=202,reason='Order canceled by system: synthetic TP terminal')
    assert rig.store.protection(pid)['state']=='TP_NEEDS_RESTORE'
    # A real exec on the cancelled TP arrives before cancellation/position proof.
    # It books once; the unfilled remainder remains possible until full proof.
    # Receive the real complete exec independently. Fake.fill's automatic
    # bare status has no permId, so it cannot stand in for this native proof.
    fill=rig.broker.fill(req.intent_id,qty=1,price=req.limit_price,exec_id='b23-cancel-late-partial',emit=False)
    rig.engine._on_urgent_broker_event(fill)
    adapter,trade=_native_status(rig,req.intent_id,raw='Cancelled',code=202,
                                reason='Order canceled by system: synthetic TP terminal')
    assert trade.orderStatus.filled==1 and trade.orderStatus.remaining==req.quantity-1
    _deliver_status(rig,adapter,trade)
    assert not rig.store.intent(req.intent_id)['hold_reason']
    assert rig.store.position(pid)['qty_open']==5 and rig.store.intent_filled_qty(req.intent_id)==1
    _safety(rig)
    before=_writes(rig);checks=len(rig._b23_write_checks)
    with monkeypatch.context() as missing:
        if incomplete=='execution_query_failed':
            def unavailable():raise BrokerError('synthetic reqExecutions unavailable after TP cancel')
            missing.setattr(rig.broker,'executions_today',unavailable)
        else:
            rig.broker.invisible_execs.add(fill.exec_id)
        _settle(rig)
        row=rig.store.protection(pid)
        possible_S=max(row['confirmed_remaining'],row['pending_max_remaining'])
        possible_T=req.quantity-rig.store.intent_filled_qty(req.intent_id)
        assert possible_S+possible_T<=rig.store.position(pid)['qty_open']==5
        assert possible_S==initial['confirmed_remaining']
        assert _writes(rig)==before and len(rig._b23_write_checks)==checks
        _safety(rig)
    rig.broker.invisible_execs.discard(fill.exec_id)
    _settle(rig)
    row=rig.store.protection(pid)
    assert row['state']=='RESTORE_PENDING' and row['pending_qty']==5
    assert rig.broker.modified_protections[-1].quantity==5
    assert rig._b23_write_checks[-2:]==[('modify_protection','before'),('modify_protection','after')]
    rig.broker.confirm_protection_modify(row['intent_id']);_settle(rig)
    assert rig.store.protection(pid)['confirmed_remaining']==5
    # Replay the same real exec; neither capacity nor ledger/PnL may book twice.
    before=_writes(rig);position=dict(rig.store.position(pid))
    rig.engine._on_urgent_broker_event(fill);_settle(rig)
    assert rig.store.intent_filled_qty(req.intent_id)==1 and dict(rig.store.position(pid))==position
    assert _writes(rig)==before
    _safety(rig)

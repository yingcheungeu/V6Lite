"""Hostile native snapshot rows must never become a picked good row."""
from dataclasses import replace
import pytest
from v6lite.broker.base import BrokerError
from v6lite.broker.fake import FakeBroker
from v6lite.models import OpenOrdersNormalizationEvent,Purpose
from v6lite.report import daily_report
from v6lite.market_calendar import trading_day
from v6lite.tests.test_batch26_overnight_evidence import _no_network,_safety,_writes,_restart,_tp_requests
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run,_audits,_tick_until
from v6lite.tests.test_batch32_open_orders import oct29,_pending_BE,_second_TP1


def _native_query(wire,values,*,before_row=None):
    def query():
        future=wire.ib.wrapper.startReq('openOrders')
        wire.ib.client.reqAllOpenOrders();wire.harness.loop.drain()
        for i,value in enumerate(values):
            if before_row:before_row(i,value)
            wire.open_order(value)
        wire.ib.wrapper.openOrderEnd();assert future.done();return future.result()
    return query


@pytest.mark.parametrize('bad_kind',['filled','lmt'])
def test_B32_identical_invalid_native_filled_cannot_be_promoted_to_ACK(oct29,monkeypatch,bad_kind):
    rig,pid,wire=oct29;errors=[];states=[]
    def response(row):
        value=wire.apply_pending()
        def invalid(i,value):
            key=wire.ib.wrapper.orderKey(value.client_id,value.broker_order_id,value.perm_id)
            if bad_kind=='filled':wire.ib.wrapper.trades[key].orderStatus.filled=float('nan')
        if bad_kind=='lmt':
            native=wire.native
            def bad_price(value):
                c,o,s=native(value);o.lmtPrice=float('nan');return c,o,s
            monkeypatch.setattr(wire,'native',bad_price)
        for count in ([3,1] if bad_kind=='filled' else [3]):
            monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_native_query(wire,[value]*count,before_row=invalid))
            rig.advance(microseconds=1)
            try:rig.broker.open_orders()
            except BrokerError as exc:errors.append(str(exc))
            states.append(rig.store.protection(pid)['state'])
    _pending_BE(rig,pid,wire,monkeypatch,response)
    assert errors, 'three equally malformed native fill counts must reject the entire query'
    assert len(errors)==(2 if bad_kind=='filled' else 1) and all(s!='WORKING' for s in states)
    assert rig.store.control('frozen:AAPL') is not None
    assert any(l=='P1' for l,t in rig._b26_alerts) and any(l=='P0' for l,t in rig._b26_alerts)
    _safety(rig)


@pytest.mark.parametrize('fault',['aux','quantity','status','terminal','permId','second-orderId',
    'account','missing-account','conId','secType','currency','exchange','raw-lmt','order-ref','filled','bool-quantity'])
def test_B32_any_native_row_contradiction_rejects_whole_snapshot(oct29,monkeypatch,fault):
    rig,pid,wire=oct29;errors=[];states=[];writes=[]
    native=wire.native
    def response(row):
        good=wire.apply_pending();values=[good,good,good]
        if fault in {'aux','quantity','status','terminal','permId','second-orderId','account','missing-account','conId','order-ref'}:
            changes={'aux':{'aux_price':341.},'quantity':{'total_quantity':3,'remaining':3.},
                'status':{'status':'PendingSubmit'},'terminal':{'status':'Filled'},
                'permId':{'perm_id':good.perm_id+1},'second-orderId':{'broker_order_id':good.broker_order_id+1},
                'account':{'account':'OTHER-ACCOUNT'},'missing-account':{'account':''},
                'conId':{'con_id':good.con_id+1},'order-ref':{'intent_id':'external-contradicting-ref'}}
            values[1]=replace(good,**changes[fault])
        calls=[0]
        def native_row(value):
            contract,order,state=native(value)
            if calls[0]==1:
                if fault=='secType':contract.secType='OPT'
                elif fault=='currency':contract.currency='EUR'
                elif fault=='exchange':contract.exchange='NYSE'
                elif fault=='raw-lmt':order.lmtPrice=.25
                elif fault=='bool-quantity':order.totalQuantity=True
            calls[0]+=1
            return contract,order,state
        monkeypatch.setattr(wire,'native',native_row)
        def before(i,value):
            if fault=='filled':
                key=wire.ib.wrapper.orderKey(good.client_id,good.broker_order_id,good.perm_id)
                wire.ib.wrapper.trades[key].orderStatus.filled=1. if i==1 else 0.
        monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_native_query(wire,values,before_row=before))
        rig.advance(microseconds=1);writes.append(_writes(rig))
        try:rig.broker.open_orders()
        except BrokerError as exc:errors.append(str(exc))
        states.append(rig.store.protection(pid)['state'])
    _pending_BE(rig,pid,wire,monkeypatch,response)
    assert errors, fault
    assert states!=['WORKING']
    assert _writes(rig)==writes[0], 'contradictory echoes cannot cancel, replace or modify the physical STP'
    assert rig.store.control('frozen:AAPL') is not None
    assert any(l=='P1' for l,t in rig._b26_alerts) and any(l=='P0' for l,t in rig._b26_alerts)
    assert _audits(rig,'OPEN_ORDERS_CONFLICT')
    _safety(rig)


def _repeat_current(wire):
    def query():
        values=FakeBroker.open_orders(wire.rig.broker)
        return _native_query(wire,[row for value in values for row in [value]*3])()
    return query


def test_B32_uncached_identical_native_rows_have_one_authoritative_result(oct29,monkeypatch):
    rig,pid,wire=oct29
    wire.ib.wrapper.trades.clear();wire.ib.wrapper.permId2Trade.clear();wire.adapter._trades.clear()
    monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_repeat_current(wire))
    orders=rig.broker.open_orders()
    assert len(orders)==1 and orders[0].remaining==3
    assert not _audits(rig,'OPEN_ORDERS_CONFLICT')
    [a]=_audits(rig,'OPEN_ORDERS_NORMALIZED')
    assert (a['raw_rows'],a['unique_rows'],a['duplicate_rows'])==(3,1,2)
    assert not [(l,t) for l,t in rig._b26_alerts if l in {'P0','P1'}]


def test_B32_restart_pending_uses_normalized_three_source_proof_and_report_S2(oct29,tmp_path,monkeypatch):
    rig,pid,wire=oct29
    _pending_BE(rig,pid,wire,monkeypatch,lambda row:None)
    wire.apply_pending()
    rig.broker.set_quote('AAPL',bid=346.0,ask=346.1,last=346.05,ts=rig.clock.now())
    monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_repeat_current(wire))
    rig.engine.shutdown(grace_seconds=0)
    _restart(rig,tmp_path,monkeypatch,reopen=True)
    _tick_until(rig,lambda:rig.store.protection(pid)['state']=='WORKING')
    assert any(a['generation']==4 and a['source']=='own complete three-source round' for a in _audits(rig,'PROTECT_PRICE_CONFIRMED'))
    proof=rig.engine._protection_notice_evidence.get(pid)
    assert proof and len(proof['own'])==1 and proof['own'][0].remaining==2
    report=daily_report(rig.store,rig.cfg,trading_day(rig.clock.now()))
    assert 'P=2 S=2' in report and 'S=6' not in report
    assert rig.store.control('frozen:AAPL') is None
    assert not _audits(rig,'OVERNIGHT_TP_FROZEN')
    _safety(rig)


def test_B32_duplicate_diagnostic_is_idempotent_audit_only_and_bounded(oct29,monkeypatch):
    rig,pid,wire=oct29;events=[]
    deliver=wire.deliver
    def capture(event):
        if isinstance(event,OpenOrdersNormalizationEvent):events.append(event)
        deliver(event)
    wire.adapter.set_urgent_handler(capture)
    monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_repeat_current(wire))
    rig.broker.open_orders();[event]=events
    count=len(_audits(rig,'OPEN_ORDERS_NORMALIZED'));messages=list(rig.channel.messages)
    for _ in range(2):rig.engine._handle_event(rig.clock.now(),event)
    assert len(_audits(rig,'OPEN_ORDERS_NORMALIZED'))==count
    assert rig.channel.messages==messages
    assert not rig.store.controls_with_prefix('open_orders_normalization:')
    _safety(rig)


def test_B32_bad_native_filled_single_stays_unverifiable(oct29,monkeypatch):
    rig,pid,wire=oct29;errors=[];states=[]
    def response(row):
        value=wire.apply_pending()
        def invalid(i,value):
            key=wire.ib.wrapper.orderKey(value.client_id,value.broker_order_id,value.perm_id)
            wire.ib.wrapper.trades[key].orderStatus.filled=float('nan')
        monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_native_query(wire,[value],before_row=invalid))
        rig.advance(microseconds=1)
        try:rig.broker.open_orders()
        except BrokerError as exc:errors.append(str(exc))
        states.append(rig.store.protection(pid)['state'])
    _pending_BE(rig,pid,wire,monkeypatch,response)
    assert errors and states!=['WORKING'], 'a new single bad row cannot bypass original native quantity completeness'


def test_B32_exact_integer_identity_and_foreign_rows_are_not_coalesced(oct29):
    from v6lite.broker.open_orders import normalize_open_order_rows
    from types import SimpleNamespace
    rig,pid,wire=oct29
    value=FakeBroker.open_orders(rig.broker)[0]
    def row(perm,ref):
        c,o,s=wire.native(replace(value,perm_id=perm,intent_id=ref))
        s.filled=0.;s.remaining=3.
        return SimpleNamespace(contract=c,order=o,orderStatus=s)
    huge=10**40
    a,b=row(huge+1,value.intent_id),row(huge+2,value.intent_id)
    result,events=normalize_open_order_rows([a,b],at=rig.clock.now(),generation=wire.adapter.query_generation,request_id=1)
    assert len(result)==2 and any('order.permId' in e.conflicting_fields for e in events)
    foreign=row(huge+1,'external-order')
    result,events=normalize_open_order_rows([foreign,foreign,foreign],at=rig.clock.now(),generation=wire.adapter.query_generation,request_id=2)
    assert len(result)==3 and not events


def test_B32_duplicate_diagnostic_between_real_native_ACK_components_preserves_handoff(overnight_run,monkeypatch,request):
    from v6lite.tests.test_batch28c_ack_ordering import StatusFirstServer
    class Server(StatusFirstServer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.checks=[]
        def release_ack(self):
            if self.ack_released:return
            super().release_ack();self.later(.04,self.interleave)
        def interleave(self):
            row=self.rig.store.protection(self.pid)
            before=self.rig.engine._protection_reduction_proofs.get(self.pid)
            fingerprint=self.rig.engine._protection_reduction_fingerprint(row)
            # Diagnostic-only event; actual continuation was armed by the
            # native server's genuine proof and status/openOrder ACK pair.
            event=OpenOrdersNormalizationEvent((row['broker_order_id'],),(row['perm_id'],),
                (row['intent_id'],),3,2,(),self.rig.clock.now(),self.adapter.query_generation,1)
            for _ in range(2):self.adapter._dispatch_urgent(event,wake=False)
            self.checks.append((before is not None,self.rig.engine._protection_reduction_proofs.get(self.pid) is before,
                fingerprint==self.rig.engine._protection_reduction_fingerprint(row)))
    rig,pid,harness=overnight_run();server=Server(rig,pid,harness,monkeypatch,request)
    server.drive()
    assert server.checks==[(True,True,True)]
    assert server.tp_wire-server.ack_at<=1. and server.fill_at-server.ack_at<=5.
    _safety(rig)


def test_B32_missing_raw_capture_cannot_normalize_mutable_SDK_aliases(oct29,monkeypatch):
    rig,pid,wire=oct29;errors=[]
    def response(row):
        value=wire.apply_pending()
        # A decoder without our raw capture can return the same mutable SDK
        # Trade three times; its final values cannot prove each original row.
        wire.ib.wrapper.openOrder=wire.adapter._raw_open_order_original
        wire.adapter._raw_open_order_owner=None
        monkeypatch.setattr(wire.adapter,'_install_raw_open_order_hook',lambda wrapper:None)
        monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_native_query(wire,[value]*3))
        rig.advance(microseconds=1)
        try:rig.broker.open_orders()
        except BrokerError as exc:errors.append(str(exc))
    _pending_BE(rig,pid,wire,monkeypatch,response)
    assert errors and rig.store.control('frozen:AAPL') is not None
    assert any('unpreserved_native_rows' in a['conflicting_fields'] for a in _audits(rig,'OPEN_ORDERS_CONFLICT'))
    assert not _audits(rig,'PROTECT_PRICE_CONFIRMED')
    _safety(rig)


def test_B32_queued_conflict_blocks_return_before_engine_drains_it(oct29,monkeypatch):
    rig,pid,wire=oct29;errors=[];states=[]
    def response(row):
        good=wire.apply_pending();bad=replace(good,perm_id=good.perm_id+1)
        wire.adapter.set_urgent_handler(None)
        monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_native_query(wire,[good,bad,good]))
        rig.advance(microseconds=1)
        try:rig.broker.open_orders()
        except BrokerError as exc:errors.append(str(exc))
        states.append(rig.store.protection(pid)['state'])
        rig.engine._drain(rig.clock.now())
        wire.adapter.set_urgent_handler(wire.deliver)
    _pending_BE(rig,pid,wire,monkeypatch,response)
    assert errors and states==['PRICE_PENDING']
    assert rig.store.control('frozen:AAPL') is not None and _audits(rig,'OPEN_ORDERS_CONFLICT')
    assert not _audits(rig,'PROTECT_PRICE_CONFIRMED')
    _safety(rig)


def test_B32_old_socket_conflict_diagnostic_cannot_freeze_new_socket(oct29):
    rig,pid,wire=oct29;row=rig.store.protection(pid);before=_writes(rig)
    event=OpenOrdersNormalizationEvent((row['broker_order_id'],),(row['perm_id'],),(row['intent_id'],),
        3,0,('order.permId',),rig.clock.now(),wire.adapter.query_generation-1,1)
    for _ in range(2):rig.engine._handle_event(rig.clock.now(),event)
    assert rig.store.control('frozen:AAPL') is None
    assert len(_audits(rig,'OPEN_ORDERS_CONFLICT_STALE'))==1
    assert not [(l,t) for l,t in rig._b26_alerts if l in {'P0','P1'}]
    assert _writes(rig)==before


def test_B32_conflict_freezes_all_bound_symbols_and_locates_by_permId(oct29):
    from v6lite.models import OrderRequest,Side
    rig,pid,wire=oct29;row=rig.store.protection(pid);before=_writes(rig)
    req=OrderRequest('v6l-b32-entry-msft','MSFT',Side.BUY,1,100.,Purpose.ENTRY)
    rig.store.create_intent(rig.clock.now(),req,atr=None,count_entry_day=None)
    event=OpenOrdersNormalizationEvent((99999,),(row['perm_id'],),(req.intent_id,),
        2,0,('order.orderRef','contract.symbol'),rig.clock.now(),wire.adapter.query_generation,1)
    rig.engine._handle_event(rig.clock.now(),event)
    assert rig.store.control('frozen:AAPL') is not None and rig.store.control('frozen:MSFT') is not None
    assert rig.store.anomalies()
    assert any(l=='P0' for l,t in rig._b26_alerts) and any(l=='P1' for l,t in rig._b26_alerts)
    assert _writes(rig)==before


def test_B32_shared_normalized_book_reserves_one_STP_and_one_TP(oct29,monkeypatch):
    rig,pid,wire=oct29
    second=_second_TP1(rig,pid,wire)
    monkeypatch.setattr(wire.ib,'reqAllOpenOrders',_repeat_current(wire))
    orders=rig.broker.open_orders()
    assert len(orders)==2
    assert sum(o.remaining for o in orders if o.order_type=='STP')==2
    assert sum(o.remaining for o in orders if o.order_type=='LMT')==1
    capacity,held,detail=rig.engine._sell_capacity_fresh('AAPL')
    assert held==3 and capacity==0 and 'reserved 3' in detail
    assert len(rig.engine._working)==2
    assert not [(l,t) for l,t in rig._b26_alerts if l in {'P0','P1'}]
    _safety(rig)

"""27b independent native raw-time/scope contracts; no network or real state.

SDK decoder 11 -> wrapper.execDetails -> adapter -> Engine/Store is real.
Only the transport, request coroutine completion and wall clock are scripted.
All accounts, prices, execution/order IDs and malformed samples are synthetic.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from v6lite.broker.ibkr import IbkrBroker
from v6lite.broker.base import BrokerError
from ib_insync.wrapper import RequestError
from v6lite.clock import FixedClock
from v6lite.market_calendar import ET, trading_day
from v6lite.models import FillEvent, OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, et, make_cfg
from v6lite.tests.test_batch24_pacing import OfflineLoop, _opcode
from v6lite.tests.test_batch26_overnight_evidence import _no_network

UTC=timezone.utc
DAY=(2026,10,20)
TIME_TAG='[EXECUTION_TIME_SUMMARY]'


class NativeExecutionHarness:
    """An offline server retaining actual native reqId/filter/End and bytes."""
    def __init__(self, cfg, clock, monkeypatch, request):
        from ib_insync import ExecutionFilter
        self.cfg=cfg; self.clock=clock; self.adapter=IbkrBroker(cfg)
        self.ib=self.adapter.ib; self.client=self.ib.client; self.wrapper=self.ib.wrapper
        request.addfinalizer(self.client.reset)
        self.loop=OfflineLoop(); self.loop.now=clock.now().timestamp()
        self.rows=lambda: []
        self.transform=lambda rows: rows
        self.raw=lambda fill: fill.time.strftime('%Y%m%d %H:%M:%S')
        self.mode='complete'; self.filters=[]; self.ends=[]; self.wire=[]; self.decoded=[]
        # Explicit offline server visibility, never evidence of a scope on an
        # unfiltered request. A real wire since-filter overrides this default.
        self.visibility='trade-log-7day'
        self.scoped_mode=None  # Fault only d when ordinary recovery/core queries must remain healthy.
        monkeypatch.setattr('ib_insync.client.getLoop', lambda:self.loop)
        monkeypatch.setattr('v6lite.broker.ibkr.time.monotonic', lambda:clock.now().timestamp())
        monkeypatch.setattr(self.adapter, '_execution_query_now', lambda:clock.now(), raising=False)
        def sleep(seconds):
            clock.advance(seconds=seconds); self.loop.advance(seconds)
        monkeypatch.setattr(self.ib,'sleep',sleep)
        self.client.connState=self.client.CONNECTED
        self.client._apiReady=True; self.client._serverVersion=176
        self.client._accounts=[cfg.account]
        monkeypatch.setattr(self.client.conn,'sendMsg',lambda packet:self.wire.append(packet[4:].decode()))
        self.adapter._ensure_channel_hooks()
        self.ib.execDetailsEvent+=self.adapter._on_exec
        def execute(filt=None):
            filt=filt or ExecutionFilter()
            if self.mode=='unscoped-filter': filt.time=''
            if self.mode=='historical-filter': filt.time='20261001-04:00:00'
            self.filters.append((clock.now(),replace(filt)))
            req_id=self.client.getReqId()
            future=self.wrapper.startReq(req_id)
            self.client.reqExecutions(req_id,filt); self.loop.drain()
            self.wrapper.lastTime=clock.now()
            # The server retains *all* TradeLog rows. Apply a since-bound only
            # when that exact native request actually transmitted one; legacy
            # unbounded requests must expose their historical rows as before.
            sent=[message.split('\0') for message in self.wire
                  if _opcode(message)=='7' and message.split('\0')[2]==str(req_id)]
            assert len(sent)==1,'the server must identify the actual native request bytes'
            wire_since=sent[0][5]
            rows=list(self.rows())
            if wire_since:
                since=datetime.strptime(wire_since,'%Y%m%d-%H:%M:%S').replace(tzinfo=UTC)
                rows=[fill for fill in rows if fill.time>=since]
            elif self.visibility=='default-midnight':
                # Model TWS's documented default current-day server view.
                # The Python result is STILL an unscoped legacy query: the
                # fixture never adds scope just because this state is selected.
                since=datetime.combine(trading_day(clock.now()),time(0),tzinfo=ET).astimezone(UTC)
                rows=[fill for fill in rows if fill.time>=since]
            elif self.visibility=='trade-log-7day':
                rows=[fill for fill in rows if fill.time>=clock.now()-timedelta(days=7)]
            else:
                raise AssertionError('unknown explicit synthetic server visibility')
            for fill in self.transform(rows):
                self.decode(fill,self.raw(fill),req_id=req_id)
            if self.mode=='raise-after-row':
                self.wrapper.error(req_id,322,'synthetic incomplete execution request','')
                return future.result()
            if self.mode=='missing-End':
                result=list(self.wrapper._results.pop(req_id,[]))
                self.wrapper._futures.pop(req_id,None)
                future.cancel()
                return result  # Partial list is explicitly NOT a completed server response.
            self.wrapper.execDetailsEnd(req_id)
            self.ends.append((req_id,clock.now()))
            assert future.done(),'only the actual matching End completes the native Future'
            result=future.result()
            if self.mode=='generation-reset': self.adapter._reset_channels()
            if self.mode=='cross-midnight': clock.advance(days=1)
            return result
        monkeypatch.setattr(self.ib,'reqExecutions',execute)

    def decode(self,fill,raw,*,req_id=-1):
        from ib_insync import LimitOrder, OrderStatus, Stock, Trade
        contract=Stock(fill.symbol,'SMART','USD',conId=fill.con_id)
        if req_id==-1:
            order=LimitOrder(str(fill.side),fill.quantity,fill.price,
                orderId=fill.broker_order_id,permId=fill.perm_id,
                clientId=self.cfg.client_id,account=self.cfg.account,orderRef=fill.intent_id)
            self.wrapper.permId2Trade[fill.perm_id]=Trade(contract,order,OrderStatus(
                status='Submitted',orderId=fill.broker_order_id,permId=fill.perm_id,
                clientId=self.cfg.client_id))
        self.wrapper.lastTime=self.clock.now()
        fields=['11',str(req_id),str(fill.broker_order_id),str(fill.con_id),fill.symbol,'STK','','0','','',
                'SMART','USD',fill.symbol,'NMS',fill.exec_id,raw,self.cfg.account,'NASDAQ',
                'BOT' if fill.side is Side.BUY else 'SLD',str(fill.quantity),str(fill.price),str(fill.perm_id),
                str(self.cfg.client_id),'0',str(fill.quantity),str(fill.price),fill.intent_id,'','0','','1']
        assert len(fields)==31
        self.ib.client.decoder.interpret(fields)
        self.decoded.append((req_id,fill.exec_id,raw))

    def callback(self,fill,raw=None):
        self.decode(fill,self.raw(fill) if raw is None else raw)
        return self.adapter.drain_events()

    def client_query(self):
        result=self.adapter.executions_today()
        return list(result) if self.mode=='list-only' else result

    def account_query(self):
        result=self.adapter.account_executions_today()
        return list(result) if self.mode=='list-only' else result

    def scoped_query(self):
        # Future optional capability is accessed behaviorally. Baseline uses
        # its real legacy plain query; no missing-API/collection failure.
        query=getattr(self.adapter,'account_executions_today_scoped',self.adapter.account_executions_today)
        prior=self.mode
        if self.scoped_mode is not None:self.mode=self.scoped_mode
        try:
            result=query()
            return list(result) if self.mode=='list-only' else result
        finally:self.mode=prior


def _fill(now,*,exec_id='b27b-raw.01.01',intent='v6l-b27b-entry',perm_id=50500):
    return FillEvent(exec_id,intent,'AAPL',Side.BUY,3,113.13,now,
                     broker_order_id=500,perm_id=perm_id,con_id=265598)


def _audit_payloads(store):
    return [(row['kind'],json.loads(row['payload'])) for row in store.conn.execute(
        'SELECT kind,payload FROM audit ORDER BY id')]


@pytest.fixture
def raw_rig(tmp_path,monkeypatch,request):
    made=[]
    def make():
        rig=Rig(tmp_path/('raw-'+str(len(made))),start=et(10,1,day=DAY))
        made.append(rig)
        harness=NativeExecutionHarness(rig.cfg,rig.clock,monkeypatch,request)
        fill=_fill(rig.clock.now())
        rig.store.create_intent(fill.time-timedelta(seconds=5),
            OrderRequest(fill.intent_id,fill.symbol,fill.side,3,fill.price,Purpose.ENTRY,'b27b-raw-position'),
            atr=2.0,count_entry_day=None)
        return rig,harness,fill
    yield make
    for rig in made:
        rig.alerter.close();rig.store.close()


@pytest.mark.parametrize('category,raw',[
    ('naive','20261020 10:01:00'),
    ('unparseable','not-a-valid-execution-time'),
    ('zoned','20261020 14:01:00 UTC'),
])
def test_B27b_raw_native_decoder_time_is_audited_without_inventing_exact_verification(raw_rig,category,raw):
    rig,harness,fill=raw_rig()
    if category=='zoned':
        # A prior genuine uncertain callback exists. A later zoned query with
        # wrong permId must retain its raw time in REVIEW, never rebook.
        for event in harness.callback(fill,'20261020 10:01:00'):
            rig.engine._handle_event(rig.clock.now(),event)
        harness.rows=lambda:[replace(fill,perm_id=fill.perm_id+1)]
        harness.raw=lambda _:raw
        events=harness.client_query()
        for event in events:rig.engine._handle_event(rig.clock.now(),event)
    else:
        for event in harness.callback(fill,raw):
            rig.engine._handle_event(rig.clock.now(),event)
    payloads=_audit_payloads(rig.store)
    matches=[(kind,payload) for kind,payload in payloads if payload.get('raw_time')==raw]
    assert matches,'raw native field 15 must survive the actual decoder -> event -> audit chain'
    assert all(rig.cfg.account not in json.dumps(payload,ensure_ascii=False) for _,payload in matches)
    if category=='naive':
        assert any(kind=='EXECUTION_TIME_UNVERIFIED' for kind,_ in matches)
        assert rig.store.control('execution_time_unverified:'+fill.exec_id) is not None
    elif category=='unparseable':
        assert any(kind=='EXECUTION_TIME_REVIEW_REQUIRED' for kind,_ in matches)
        assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]==0
    else:
        assert any(kind=='EXECUTION_TIME_REVIEW_REQUIRED' for kind,_ in matches)
        assert not [kind for kind,_ in payloads if kind=='EXECUTION_TIME_VERIFIED']
    assert harness.ib.TimezoneTWS=='','tests and production must not silently set a TWS timezone'


def test_B27b_complete_native_query_raw_classification_summary_and_audit_are_redacted(raw_rig,caplog):
    rig,harness,base=raw_rig()
    fills=[replace(base,exec_id=f'b27b-raw-summary-{n}.01.01') for n in range(3)]
    raw={fills[0].exec_id:'20261020 14:01:00 UTC',
         fills[1].exec_id:'20261020 10:01:00',
         fills[2].exec_id:'invalid-time-'+rig.cfg.account}
    harness.rows=lambda:fills
    harness.raw=lambda fill:raw[fill.exec_id]
    caplog.set_level(logging.INFO)
    try:
        result=harness.client_query()
    except (BrokerError,TimeoutError,RequestError):
        # A diagnostic-invalid row may explicitly reject the request; it may
        # never be dropped and turned into complete authorization instead.
        result=None
    for event in harness.adapter.drain_events():rig.engine._handle_event(rig.clock.now(),event)
    summaries=[record.getMessage() for record in caplog.records if TIME_TAG in record.getMessage()]
    assert len(summaries)==1,summaries
    summary=summaries[0]
    assert all(part in summary for part in ('returned=3','zoned=1','naive=1','unparseable=1','sample=')),summary
    assert rig.cfg.account not in '\n'.join(record.getMessage() for record in caplog.records)
    payloads=_audit_payloads(rig.store)
    assert any(payload.get('raw_time') and 'invalid-time-' in payload['raw_time'] for _,payload in payloads)
    assert rig.cfg.account not in json.dumps(payloads,ensure_ascii=False)
    if result is not None:
        scope=getattr(result,'scope',None)
        assert not scope or scope.get('complete') is not True,'one undecodable row invalidates complete evidence'
    assert harness.ib.TimezoneTWS==''


@pytest.mark.parametrize('day,expected_filter',[
    ((2026,10,20),'20261020-04:00:00'),
    ((2026,11,2),'20261102-05:00:00'),
])
def test_B27b_native_execution_filter_and_matching_End_prove_current_ET_day_scope(
        tmp_path,monkeypatch,request,day,expected_filter):
    clock=FixedClock(et(10,1,day=day));cfg=make_cfg(tmp_path)
    harness=NativeExecutionHarness(cfg,clock,monkeypatch,request)
    for query in (harness.scoped_query,):
        result=query()
        assert harness.filters[-1][1].time==expected_filter
        scope=getattr(result,'scope',None)
        assert isinstance(scope,dict),'plain lists carry no authoritative day/query-completeness scope'
        midnight=datetime.combine(trading_day(clock.now()),time(0),tzinfo=ET).astimezone(UTC)
        assert scope.get('day')==trading_day(clock.now()).isoformat()
        assert scope.get('since_utc')==midnight and scope.get('time_filter')==expected_filter
        assert scope.get('generation')==harness.adapter.query_generation
        assert scope.get('complete') is True
        assert scope.get('started_at')<=scope.get('completed_at')<=clock.now()
        assert harness.ends and harness.ends[-1][1]==scope.get('completed_at')
        packets=[message.split('\0') for message in harness.wire if _opcode(message)=='7']
        assert packets[-1][5]==expected_filter,'assert actual native wire filter, not only a prebuilt Python object'


@pytest.mark.parametrize('mode',['missing-End','generation-reset','cross-midnight','unscoped-filter','historical-filter'])
def test_B27b_incomplete_old_or_unknown_native_query_cannot_claim_today_scope(tmp_path,monkeypatch,request,mode):
    clock=FixedClock(et(10,1,day=DAY));harness=NativeExecutionHarness(make_cfg(tmp_path),clock,monkeypatch,request)
    harness.mode=mode
    try:result=harness.scoped_query()
    except (BrokerError,TimeoutError,RequestError):return
    scope=getattr(result,'scope',None)
    assert not scope or scope.get('complete') is not True,(mode,scope)



def test_B27b_legacy_non_overnight_client_and_release_account_queries_keep_unbounded_wire_filter(
        tmp_path,monkeypatch,request):
    clock=FixedClock(et(10,1,day=DAY));harness=NativeExecutionHarness(make_cfg(tmp_path),clock,monkeypatch,request)
    for query in (harness.client_query,harness.account_query):
        query()
        packets=[message.split('\0') for message in harness.wire if _opcode(message)=='7']
        assert packets[-1][5]=='','new today-only authority must not change recovery/release query visibility'



def test_B27b_repeating_identical_native_raw_observations_does_not_flood_new_observation_audit(raw_rig):
    rig,harness,fill=raw_rig()
    for event in harness.callback(fill,'20261020 10:01:00'):
        rig.engine._handle_event(rig.clock.now(),event)
    harness.rows=lambda:[fill]
    for _ in range(6):
        result=harness.client_query()
        for event in result:rig.engine._handle_event(rig.clock.now(),event)
        for event in harness.adapter.drain_events():rig.engine._handle_event(rig.clock.now(),event)
    payloads=_audit_payloads(rig.store)
    observed=[payload for kind,payload in payloads
              if kind=='EXECUTION_TIME_OBSERVED' and payload.get('exec_id')==fill.exec_id]
    assert len(observed)<=2,'only this new raw observation family deduplicates identical callback/query payloads'
    assert len([payload for kind,payload in payloads if kind=='EXECUTION_TIME_UNVERIFIED'])==1
    assert len([payload for kind,payload in payloads if kind=='EXECUTION_TIME_REVIEW_REQUIRED'])<=1
    assert rig.store.control('execution_time_unverified:'+fill.exec_id) is not None
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]==1


def test_B27b_invalid_raw_is_scoped_to_its_actual_reqId_and_never_poisons_another_complete_query(
        raw_rig,monkeypatch):
    rig,harness,base=raw_rig()
    depth=[0];inner=[];req_ids=[]
    def overlapping(filt):
        req_id=harness.client.getReqId();req_ids.append(req_id)
        future=harness.wrapper.startReq(req_id)
        harness.client.reqExecutions(req_id,filt);harness.loop.drain()
        harness.wrapper.lastTime=rig.clock.now()
        if depth[0]==0:
            depth[0]=1
            harness.decode(replace(base,exec_id='b27b-invalid-A.01.01'),'bad-native-A',req_id=req_id)
            inner.append(harness.scoped_query())
            depth[0]=0
        else:
            harness.decode(replace(base,exec_id='b27b-valid-B.01.01'),
                           '20261020 14:01:00 UTC',req_id=req_id)
            # A concurrent malformed unsolicited callback is a different source
            # and reqId. It cannot invalidate B's otherwise complete response.
            harness.decode(replace(base,exec_id='b27b-invalid-callback.01.01'),
                           'bad-unsolicited-callback')
        harness.wrapper.execDetailsEnd(req_id)
        assert future.done()
        return future.result()
    monkeypatch.setattr(harness.ib,'reqExecutions',overlapping)
    try:outer=harness.scoped_query()
    except (BrokerError,TimeoutError,RequestError):outer=None
    assert len(req_ids)==2 and req_ids[0]!=req_ids[1] and len(inner)==1
    inner_scope=getattr(inner[0],'scope',None)
    assert isinstance(inner_scope,dict) and inner_scope.get('complete') is True
    assert len(inner[0])==1 and inner[0][0].exec_id=='b27b-valid-B.01.01'
    if outer is not None:
        outer_scope=getattr(outer,'scope',None)
        assert not outer_scope or outer_scope.get('complete') is not True
    for event in harness.adapter.drain_events():rig.engine._handle_event(rig.clock.now(),event)
    payloads=_audit_payloads(rig.store)
    reviewed=[payload.get('raw_time') for kind,payload in payloads if kind=='EXECUTION_TIME_REVIEW_REQUIRED']
    assert 'bad-native-A' in reviewed and 'bad-unsolicited-callback' in reviewed
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]==0

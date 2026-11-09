"""B30 hostile accounting/report inputs and durable maintenance boundaries."""
from dataclasses import replace
from datetime import date
import json
import pytest
from v6lite.commissions import fee_breakdown
from v6lite.models import FillEvent, Side
from v6lite.report import daily_report
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch23_contracts import _no_network
from v6lite.tests.test_batch30_commission import _close_oct26,DAY


def test_B30_foreign_unbound_missing_commission_does_not_break_daily_report(tmp_path):
    rig=Rig(tmp_path);rig.start()
    fill=FillEvent('b30-unbound-real-ledger','', 'AAPL',Side.BUY,1,100.,rig.clock.now(),991,992)
    rig.engine._ingest_fill(rig.clock.now(),fill)
    row=rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?',(fill.exec_id,)).fetchone()
    assert row is not None and row['position_id'] is None and rig.store.anomalies()
    text=daily_report(rig.store,rig.cfg,date(2026,10,6))
    assert '佣金未回报 1 笔' in text and '$1.00' in text
    assert rig.store.anomalies(), 'rendering must not clear the fail-closed foreign-fill anomaly'


def test_B30_closed_estimate_stays_frozen_after_config_floor_changes(tmp_path):
    rig,pid,_=_close_oct26(tmp_path)
    trade=next(t for t in rig.store.closed_trades() if t['position_id']==pid)
    before=fee_breakdown(trade,rig.store,rig.cfg)
    assert before['estimated_fees']==pytest.approx(1.)
    changed=replace(rig.cfg,commission_estimate_min_usd=5.)
    after=fee_breakdown(trade,rig.store,changed)
    assert after['estimated_fees']==before['estimated_fees']
    assert after['estimated_net']==pytest.approx(-.17)



def test_B30_comparable_history_median_floor_and_expiry_are_explicit(tmp_path):
    from v6lite.commissions import estimate_fill_commission
    from v6lite.models import CommissionEvent
    from datetime import timedelta
    rig,pid,final=_close_oct26(tmp_path)
    trade=next(t for t in rig.store.closed_trades() if t['position_id']==pid)
    known=list(rig.store.conn.execute("SELECT f.exec_id FROM fills f JOIN commissions c ON c.exec_id=f.exec_id WHERE f.position_id=? AND f.side='SELL'",(pid,)))
    assert len(known)==2
    for row,amount in zip(known,(1.20,1.40)):
        rig.engine._handle_event(rig.clock.now(),CommissionEvent(row['exec_id'],amount))
    target=dict(rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?',(final.exec_id,)).fetchone())
    estimate=estimate_fill_commission(rig.store,rig.cfg,target,trade['closed_at'])
    assert estimate['amount']==pytest.approx(1.30)
    assert estimate['source']=='recent_same_symbol_side_quantity_median'
    assert set(estimate['sample_exec_ids'])=={r['exec_id'] for r in known}
    assert estimate_fill_commission(rig.store,replace(rig.cfg,commission_estimate_min_usd=2.),target,trade['closed_at'])['amount']==2.
    absent=estimate_fill_commission(rig.store,rig.cfg,dict(target,quantity=7),trade['closed_at'])
    assert absent['amount']==1. and absent['source']=='config_minimum'
    expired=estimate_fill_commission(rig.store,rig.cfg,target,trade['closed_at']+timedelta(days=31))
    assert expired['amount']==1. and expired['sample_exec_ids']==[]
    assert fee_breakdown(trade,rig.store,rig.cfg)['estimated_fees']==1., 'later actual history cannot change frozen missing-exec estimate'


@pytest.mark.parametrize('minimum',[0.,-1.,float('nan'),float('inf'),True,'1.0'])
def test_B30_accounting_floor_is_validated_without_changing_sizing(tmp_path,minimum):
    from v6lite.config import validate,ConfigError
    from v6lite.sizing import estimate_fee
    rig=Rig(tmp_path)
    with pytest.raises(ConfigError):validate(replace(rig.cfg,commission_estimate_min_usd=minimum))
    assert estimate_fee(1,344.72)==2., 'funds/sizing reserve stays at B29 value'


def _writes(rig):
    return tuple(len(x) for x in (rig.broker.sent,rig.broker.sent_protections,rig.broker.modified_protections,rig.broker.cancels))


def _attempts(rig):
    return rig.store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind='COMMISSION_RECHECK_REQUESTED'").fetchone()[0]


def test_B30_failed_fee_query_consumes_durable_300_second_slot(tmp_path,monkeypatch):
    from v6lite.broker.base import BrokerError
    rig,pid,final=_close_oct26(tmp_path);original=rig.broker.executions_today;calls=[]
    def query():
        calls.append(rig.clock.now())
        if len(calls)==1:raise BrokerError('synthetic fee query unavailable')
        return [replace(f,commission=1.) if f.exec_id==final.exec_id else f for f in original()]
    monkeypatch.setattr(rig.broker,'executions_today',query)
    before=_writes(rig);rig.tick()
    assert len(calls)==1 and _attempts(rig)==1
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='COMMISSION_RECHECK_FAILED'").fetchone()
    rig.advance(seconds=299);rig.tick();assert len(calls)==1
    rig.advance(seconds=1);rig.tick();assert len(calls)==2 and _attempts(rig)==2
    assert rig.store.position_net_pnl(pid)==pytest.approx(-.17)
    rig.advance(seconds=300);rig.tick();assert len(calls)==2
    assert _writes(rig)==before


def test_B30_restart_preserves_rate_limit_and_existing_cooldown(tmp_path,monkeypatch):
    from v6lite.engine import Engine
    rig,pid,final=_close_oct26(tmp_path);original=rig.broker.executions_today
    first=rig.clock.now();before_until=rig.store.control('cooldown_until')
    def query():
        return [replace(f,commission=1.) if f.exec_id==final.exec_id and (rig.clock.now()-first).total_seconds()>=300 else f for f in original()]
    monkeypatch.setattr(rig.broker,'executions_today',query)
    before=_writes(rig);rig.tick();assert _attempts(rig)==1
    engine=Engine(rig.cfg,rig.broker,rig.store,rig.alerter,rig.clock,report_dir=tmp_path/'restarted-reports')
    engine.startup()  # ordinary startup queries do not consume/erase the fee-repair slot
    rig.advance(seconds=299);engine.tick();assert _attempts(rig)==1
    rig.advance(seconds=1);engine.tick();assert _attempts(rig)==2
    assert rig.store.position_net_pnl(pid)==pytest.approx(-.17)
    assert rig.store.control('cooldown_until')==before_until and _writes(rig)==before


@pytest.mark.parametrize('guard',['dry_run','urgent','shutdown','priority','not_started','unsynchronized','queued_exit'])
def test_B30_fee_query_yields_to_engine_and_protection_guards(tmp_path,monkeypatch,guard):
    rig,_,_=_close_oct26(tmp_path);rig.tick();rig.advance(seconds=301);calls=[]
    monkeypatch.setattr(rig.broker,'executions_today',lambda:calls.append(True) or [])
    if guard=='unsynchronized':monkeypatch.setattr(rig.broker,'is_synchronized',lambda:False)
    elif guard=='queued_exit':rig.engine._enqueue('exit:synthetic-not-dispatched',lambda:None)
    else:
        field={'dry_run':'dry_run','urgent':'_in_urgent_callback','shutdown':'_shutting_down','priority':'_protection_priority_due','not_started':'_started'}[guard]
        monkeypatch.setattr(rig.engine,field,guard!='not_started')
    before=_attempts(rig);rig.engine._refresh_missing_commissions(rig.clock.now())
    assert calls==[] and _attempts(rig)==before


def test_B30_close_deadline_rechecked_after_protection_budget_and_clock_rollback(tmp_path,monkeypatch):
    rig,_,_=_close_oct26(tmp_path);rig.tick();attempts=_attempts(rig);calls=[]
    monkeypatch.setattr(rig.broker,'executions_today',lambda:calls.append(True) or [])
    rig.at(et(11,51,day=DAY));rig.engine._refresh_missing_commissions(rig.clock.now())
    assert calls==[] and _attempts(rig)==attempts
    rig.at(et(15,59,59,day=DAY));protect=rig.engine._protect
    def slow_protection(*args,**kwargs):
        protect(*args,**kwargs);rig.at(et(16,0,1,day=DAY))
    monkeypatch.setattr(rig.engine,'_protect',slow_protection)
    rig.engine._refresh_missing_commissions(rig.clock.now())
    assert calls==[] and _attempts(rig)==attempts
    rig.at(et(16,5,day=DAY));rig.engine._refresh_missing_commissions(rig.clock.now())
    assert calls==[], '13:30 PT testing window is not the 13:00 PT exchange fee-query deadline'


@pytest.mark.parametrize('field',['symbol','side','quantity','price','broker_order_id','perm_id','intent_id'])
def test_B30_fee_repair_does_not_match_only_exec_id(tmp_path,monkeypatch,field):
    rig,pid,final=_close_oct26(tmp_path);original=rig.broker.executions_today
    values={'symbol':'SPY','side':Side.BUY,'quantity':2,'price':final.price+.01,
            'broker_order_id':final.broker_order_id+1,'perm_id':final.perm_id+1,'intent_id':'v6l-other-bound-fill'}
    wrong=replace(final,commission=1.,**{field:values[field]})
    monkeypatch.setattr(rig.broker,'executions_today',lambda:[wrong if f.exec_id==final.exec_id else f for f in original()])
    before=_writes(rig);rig.tick()
    assert rig.store.conn.execute('SELECT 1 FROM commissions WHERE exec_id=?',(final.exec_id,)).fetchone() is None
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='COMMISSION_RECHECK_MISMATCH'").fetchone()
    assert rig.store.position_net_pnl(pid)==pytest.approx(.83) and _writes(rig)==before


@pytest.mark.parametrize('actual',[0.,-.01])
def test_B30_zero_and_rebate_actual_fee_follow_existing_broker_semantics(tmp_path,monkeypatch,actual):
    rig,pid,final=_close_oct26(tmp_path);original=rig.broker.executions_today
    until=rig.store.control('cooldown_until');before=_writes(rig)
    monkeypatch.setattr(rig.broker,'executions_today',lambda:[replace(f,commission=actual) if f.exec_id==final.exec_id else f for f in original()])
    rig.tick()
    assert rig.store.position_net_pnl(pid)==pytest.approx(.83-actual)
    assert next(t for t in rig.store.closed_trades() if t['position_id']==pid)['missing']==[]
    assert rig.store.control('cooldown_until')==until and _writes(rig)==before

"""Nonblocking close notification, scoped to the closing execution's fee."""
import pytest
from v6lite.models import CommissionEvent
from v6lite.tests.test_batch23_contracts import _no_network
from v6lite.tests.test_batch30_commission import _close_oct26

def cards(rig): return [b for t,b in rig.channel.messages if '持仓已关闭' in t]

@pytest.mark.parametrize('arrival',[.009,None])
def test_B31_close_waits_for_final_fee_or_three_second_estimate(tmp_path,arrival,record_property):
    rig,pid,final=_close_oct26(tmp_path,wait_for_close_card=False)
    try:
        assert cards(rig)==[]
        if arrival is not None:
            rig.advance(microseconds=9000)
            rig.broker.events.append(CommissionEvent(final.exec_id,1.))
            rig.engine._drain(rig.clock.now())
            assert len(cards(rig))==1 and '最终净值 -0.17' in cards(rig)[0]
            assert '未回报' not in cards(rig)[0]
        else:
            rig.advance(microseconds=2999000);rig.engine._drain(rig.clock.now())
            assert cards(rig)==[]
            rig.advance(microseconds=1000);rig.engine._drain(rig.clock.now())
            assert len(cards(rig))==1 and '估算净值 -0.17' in cards(rig)[0]
        record_property('b31_close_card',cards(rig)[0])
        rig.advance(seconds=5);rig.engine._drain(rig.clock.now())
        assert len(cards(rig))==1
    finally: rig.alerter.close();rig.store.close()

def test_B31_unrelated_commission_does_not_release_wait(tmp_path):
    rig,pid,final=_close_oct26(tmp_path,wait_for_close_card=False)
    try:
        rig.advance(microseconds=9000)
        rig.broker.events.append(CommissionEvent('unrelated-execution',1.))
        rig.engine._drain(rig.clock.now())
        assert not cards(rig)
        rig.advance(seconds=3);rig.engine._drain(rig.clock.now())
        assert len(cards(rig))==1
    finally: rig.alerter.close();rig.store.close()


def test_B31_restart_retains_original_deadline_and_does_not_repeat_sent_card(tmp_path):
    from v6lite.engine import Engine
    from v6lite.store import Store
    import json
    rig,pid,final=_close_oct26(tmp_path,wait_for_close_card=False)
    try:
        token=rig.store.control('close_card_pending:'+pid)
        assert token is not None
        rig.engine.shutdown(grace_seconds=0)
        path,levels=rig.store.path,rig.store._levels_fn
        rig.store.close();rig.store=Store(path,levels)
        rig.advance(seconds=2)
        rig.engine=Engine(rig.cfg,rig.broker,rig.store,rig.alerter,rig.clock,report_dir=tmp_path/'restart-report')
        rig.engine.startup();rig.engine._drain(rig.clock.now())
        assert rig.store.control('close_card_pending:'+pid)==token
        assert not cards(rig)
        rig.advance(seconds=1);rig.engine._drain(rig.clock.now())
        assert len(cards(rig))==1
        rig.engine.shutdown(grace_seconds=0)
        rig.engine=Engine(rig.cfg,rig.broker,rig.store,rig.alerter,rig.clock,report_dir=tmp_path/'restart2-report')
        rig.engine.startup();rig.engine._drain(rig.clock.now())
        assert len(cards(rig))==1 and rig.store.control('close_card_pending:'+pid) is None
    finally: rig.alerter.close();rig.store.close()


def test_B31_waiting_close_card_allows_other_position_TP_and_queries(tmp_path):
    from v6lite.config import ProtectionConfig
    from v6lite.models import Purpose
    from v6lite.tests.helpers import Rig
    from v6lite.tests.test_review_round2 import _two_open_positions
    rig=Rig(tmp_path,protection=ProtectionConfig(enabled=True))
    try:
        _two_open_positions(rig)
        aapl=next(p for p in rig.store.open_positions() if p['symbol']=='AAPL')
        spy=next(p for p in rig.store.open_positions() if p['symbol']=='SPY')
        ref=rig.store.protection(aapl['position_id'])['intent_id']
        from datetime import timedelta
        rig.at(rig.engine._last_exit_check+timedelta(seconds=rig.cfg.exit_check_interval_seconds-1))
        rig.channel.messages.clear()
        rig.broker.fill(ref,qty=aapl['qty_open'],price=113.,commission=None)
        rig.engine._drain(rig.clock.now())
        assert not cards(rig)
        start=rig.clock.now()
        rig.advance(microseconds=100000);rig.px('SPY',float(spy['tp1'])+.2)
        for _ in range(9):
            rig.tick()
            if any(r.symbol=='SPY' and r.purpose is Purpose.TP1 for r in rig.broker.sent): break
            rig.advance(microseconds=250000)
        assert any(r.symbol=='SPY' and r.purpose is Purpose.TP1 for r in rig.broker.sent)
        assert (rig.clock.now()-start).total_seconds()<3 and not cards(rig)
        assert rig.store.protection(spy['position_id'])['tp_stage']=='TP1'
        assert rig.broker.open_orders() and rig.broker.positions_fresh()
        rig.advance(seconds=3);rig.engine._drain(rig.clock.now())
        assert len(cards(rig))==1
    finally: rig.alerter.close();rig.store.close()


def test_B31_native_fee_is_consumed_while_query_has_not_reached_End(tmp_path,monkeypatch,request):
    from ib_insync import Trade,Order,OrderStatus
    from v6lite.tests.test_batch30_native_commission import CommissionWire
    rig,pid,final=_close_oct26(tmp_path/'book',wait_for_close_card=False)
    try:
        server=CommissionWire(tmp_path/'wire',monkeypatch,request,cfg=rig.cfg,clock=rig.clock,fill=final)
        [known]=server.adapter.executions_today()
        trade=Trade(server.contract,Order(orderId=final.broker_order_id,permId=final.perm_id,
            clientId=rig.cfg.client_id,orderRef=final.intent_id,account=rig.cfg.account),
            OrderStatus(status='Filled'))
        server.wrapper.permId2Trade[final.perm_id]=trade
        server.ib.commissionReportEvent+=server.adapter._on_commission
        server.adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
        future=server.wrapper.startReq('positions')
        rig.advance(microseconds=9000);server.report(1.)
        assert not future.done(), 'commission handler must not wait for another query End'
        assert rig.store.position_net_pnl(pid)==pytest.approx(-.17)
        assert len(cards(rig))==1 and '最终净值 -0.17' in cards(rig)[0]
        server.wrapper.positionEnd();assert future.done()
    finally:rig.alerter.close();rig.store.close()


def test_B31_native_deadline_sends_estimate_without_waiting_for_query_End(tmp_path,monkeypatch,request):
    from v6lite.tests.test_batch24_pacing import OfflineLoop
    from v6lite.tests.test_batch30_native_commission import CommissionWire
    rig,pid,final=_close_oct26(tmp_path/'book',wait_for_close_card=False)
    try:
        server=CommissionWire(tmp_path/'wire',monkeypatch,request,cfg=rig.cfg,clock=rig.clock,fill=final)
        loop=OfflineLoop()
        loop.call_later=lambda seconds,callback:loop.call_at(loop.now+seconds,callback)
        monkeypatch.setattr('ib_insync.util.getLoop',lambda:loop)
        monkeypatch.setattr(rig.broker,'schedule_local_deadline',server.adapter.schedule_local_deadline,raising=False)
        rig.engine._arm_close_card_deadline(rig.clock.now(),pid)
        future=server.wrapper.startReq('positions')
        rig.advance(seconds=3);loop.advance(3.00001)
        assert not future.done() and len(cards(rig))==1
        assert '估算净值 -0.17' in cards(rig)[0]
        server.wrapper.positionEnd();assert future.done()
    finally:rig.alerter.close();rig.store.close()

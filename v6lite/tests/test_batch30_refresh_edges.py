"""B30 final current-day commission report refresh regressions."""
import pytest
from v6lite.models import CommissionEvent,FillEvent,Side
from v6lite.tests.helpers import Rig,et
from v6lite.tests.test_batch30_commission import _close_oct26,DAY
from v6lite.tests.test_batch23_contracts import _no_network


def test_B30_success_then_new_fee_within_60_seconds_is_flushed_at_shutdown(tmp_path):
    rig,pid,final=_close_oct26(tmp_path)
    rig.engine._handle_event(rig.clock.now(),CommissionEvent(final.exec_id,1.))
    rig.at(et(16,5,day=DAY));rig.tick()
    path=rig.engine.report_dir/'2026-10-26.md';report_state=rig.store.control('report:2026-10-26')
    known=[r['exec_id'] for r in rig.store.conn.execute("SELECT exec_id FROM fills WHERE position_id=? AND side='SELL' AND exec_id!=? ORDER BY time",(pid,final.exec_id))]
    assert len(known)==2
    rig.channel.messages.clear()
    rig.broker.events.append(CommissionEvent(known[0],1.10));rig.advance(seconds=1);rig.tick()
    first=path.read_text(encoding='utf-8');assert 'fees: 4.11' in first
    rig.broker.events.append(CommissionEvent(known[1],1.20));rig.advance(seconds=20)
    rig.engine._drain(rig.clock.now());rig.engine.shutdown()
    last=path.read_text(encoding='utf-8')
    assert last!=first and 'fees: 4.31' in last and '最终净值 -0.47' in last
    assert rig.store.control('report:2026-10-26')==report_state
    assert not [t for t,_ in rig.channel.messages if '每日健康报告' in t or '持仓已关闭' in t or '亏损后冷却' in t]


@pytest.mark.parametrize('kind',['OPEN','unbound'])
def test_B30_known_current_day_open_or_unbound_fee_refreshes_topline_without_new_card(tmp_path,kind):
    if kind=='OPEN':
        rig=Rig(tmp_path,start=et(9,50,day=DAY))
        rig.start();rig.at(et(10,0,30,day=DAY));rig.tick()
        [req]=rig.broker.sent
        fill=rig.broker.fill(req.intent_id,commission=None,commission_separately=True)
        rig.advance(seconds=1);rig.tick()
        assert rig.store.open_positions()
    else:
        rig,_,_=_close_oct26(tmp_path)
        fill=FillEvent('b30-unbound-fee-after-report','', 'AAPL',Side.BUY,1,100.,rig.clock.now(),991,992)
        rig.engine._ingest_fill(rig.clock.now(),fill)
        assert rig.store.conn.execute('SELECT position_id FROM fills WHERE exec_id=?',(fill.exec_id,)).fetchone()[0] is None
    rig.at(et(16,5,day=DAY));rig.tick()
    path=rig.engine.report_dir/'2026-10-26.md';before=path.read_text(encoding='utf-8')
    state=rig.store.control('report:2026-10-26');rig.channel.messages.clear()
    rig.broker.events.append(CommissionEvent(fill.exec_id,.50));rig.advance(seconds=1);rig.tick()
    after=path.read_text(encoding='utf-8')
    assert after!=before, 'a known fee affects daily totals even without a closed position'
    assert f"fees: {rig.store.fees_for_day('2026-10-26'):.2f}" in after
    assert rig.store.control('report:2026-10-26')==state
    assert not [t for t,_ in rig.channel.messages if '每日健康报告' in t or '持仓已关闭' in t or '亏损后冷却' in t]



def test_B30_local_report_write_failure_retries_without_republishing_remote(tmp_path,monkeypatch):
    from v6lite import report
    rig,pid,final=_close_oct26(tmp_path)
    rig.at(et(16,5,day=DAY));rig.tick()
    path=rig.engine.report_dir/'2026-10-26.md';before=path.read_text(encoding='utf-8')
    state=rig.store.control('report:2026-10-26');original=report.write_daily_report;calls=[]
    def fail_once(*args,**kwargs):
        calls.append(rig.clock.now())
        if len(calls)==1:raise OSError('synthetic local report file lock')
        return original(*args,**kwargs)
    monkeypatch.setattr(report,'write_daily_report',fail_once)
    rig.channel.messages.clear();rig.broker.events.append(CommissionEvent(final.exec_id,1.))
    rig.advance(seconds=1);rig.tick()
    assert len(calls)==1 and path.read_text(encoding='utf-8')==before
    assert rig.store.control('commission_report_dirty:2026-10-26') is not None
    rig.advance(seconds=59);rig.tick();assert len(calls)==1
    rig.advance(seconds=1);rig.tick();assert len(calls)==2
    assert '佣金未回报 1 笔' not in path.read_text(encoding='utf-8')
    assert rig.store.control('commission_report_dirty:2026-10-26') is None
    assert rig.store.control('report:2026-10-26')==state
    assert not [t for t,_ in rig.channel.messages if '每日健康报告' in t or '持仓已关闭' in t or '亏损后冷却' in t]

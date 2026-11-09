"""B30 October-26 closed-trade/commission regressions; original red source is archived."""
from dataclasses import replace
from datetime import date
import pytest
from v6lite.config import ProtectionConfig
from v6lite.models import Purpose
from v6lite.report import daily_report
from v6lite.risk import global_entry_blocks
from v6lite.tests.helpers import Rig, buy_bars, et, seed_closed_trade
from v6lite.tests.test_batch23_contracts import _no_network
DAY = (2026, 10, 26)
PRIOR_ID = 'b30-prior-opening-stop-loss'


def _until(rig, condition, seconds=80):
    for _ in range(seconds):
        if condition():
            return
        rig.advance(seconds=1); rig.tick()
    assert condition(), 'actual Engine.tick did not reach broker/ledger target'


def _close_oct26(tmp_path, *, wait_for_close_card=True):
    rig = Rig(tmp_path, start=et(10, 19, 50, day=DAY),
              protection=ProtectionConfig(enabled=True), scan_times_et=('10:20',),
              risk_per_trade_pct=.0015, max_daily_entries=1,
              tp1_mult=.5, tp2_mult=1., tp3_mult=5.)
    rig.broker.bars['AAPL'] = buy_bars(base=331.39); rig.px('AAPL', 344.52)
    seed_closed_trade(rig.store, PRIOR_ID, -2., et(9, 30, day=DAY))
    rig.start(); rig.at(et(10, 23, day=DAY)); rig.tick()
    [entry] = [r for r in rig.broker.sent if r.purpose is Purpose.ENTRY]
    assert entry.quantity == 3
    rig.broker.fill(entry.intent_id, qty=3, price=344.52, commission=1.01)
    rig.advance(seconds=1); rig.tick()
    [pos] = rig.store.open_positions(); pid = pos['position_id']
    _until(rig, lambda: rig.store.protection(pid)['state'] == 'WORKING')
    for stage, quote, price in ((Purpose.TP1,345.87,345.87),(Purpose.TP2,346.84,346.81)):
        rig.advance(seconds=31); rig.px('AAPL',quote); rig.tick()
        _until(rig, lambda: any(r.purpose is stage for r in rig.broker.sent))
        [tp] = [r for r in rig.broker.sent if r.purpose is stage]
        assert tp.quantity == 1
        rig.broker.fill(tp.intent_id,qty=1,price=price,commission=1.)
        rig.advance(seconds=1); rig.tick()
        _until(rig, lambda: rig.store.protection(pid)['state'] == 'WORKING'
               and rig.store.protection(pid)['tp_stage'] is None)
    row = rig.store.protection(pid)
    assert rig.store.position(pid)['qty_open'] == 1
    assert rig.broker.orders[row['intent_id']].remaining == 1
    rig.at(et(11,52,6,day=DAY)); rig.px('AAPL',344.72)
    rig.channel.messages.clear()
    final = rig.broker.fill(row['intent_id'],qty=1,price=344.72,commission=None,
                           commission_separately=True,exec_id='0000dc8f.6bef670d.01.01')
    rig.engine._drain(rig.clock.now())
    trade = next(t for t in rig.store.closed_trades() if t['position_id'] == pid)
    assert trade['gross'] == pytest.approx(3.84)
    assert trade['fees'] == pytest.approx(3.01) and len(trade['missing']) == 1
    assert rig.store.position(pid)['qty_open'] == 0
    assert rig.store.control('cooldown_until') is not None
    if wait_for_close_card:
        rig.advance(seconds=3); rig.engine._drain(rig.clock.now())
    return rig,pid,final


def test_B30_close_and_cooldown_cards_explain_reported_and_estimated_net(tmp_path,record_property):
    rig,pid,_ = _close_oct26(tmp_path)
    try:
        closed = [b for t,b in rig.channel.messages if '持仓已关闭' in t]
        assert len(closed) == 1
        assert '已回报净值' in closed[0] and '0.83' in closed[0]
        assert '估算净值' in closed[0] and '-0.17' in closed[0]
        assert '估算' in closed[0] and '1.00' in closed[0]
        cooled = [b for t,b in rig.channel.messages if '冷却' in t]
        assert len(cooled) == 1
        assert PRIOR_ID in cooled[0] and pid in cooled[0]
        assert '估算' in cooled[0] and '-0.17' in cooled[0]
        report=daily_report(rig.store,rig.cfg,date(*DAY))
        assert '发送时 bid=345.84 成交相对 bid=+0.0300 USD/股' in report
        assert '发送时 bid=346.81 成交相对 bid=+0.0000 USD/股' in report
        record_property('b30_closed_card',closed[0]);record_property('b30_cooldown_card',cooled[0])
        record_property('b30_close_estimate_basis',rig.store.control('closed_commission_basis:'+pid))
    finally:
        rig.alerter.close(); rig.store.close()


@pytest.mark.parametrize('actual,final_net,flips',[(1.,-.17,False),(.01,.82,True)])
def test_B30_own_query_repairs_fee_once_without_reopening_cooldown(
        tmp_path,monkeypatch,actual,final_net,flips,record_property):
    rig,pid,final = _close_oct26(tmp_path)
    try:
        before_until = rig.store.control('cooldown_until')
        before_fills = rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
        def writes():
            return tuple(len(x) for x in (rig.broker.sent,rig.broker.sent_protections,
                         rig.broker.modified_protections,rig.broker.cancels))
        before_writes = writes(); rig.channel.messages.clear(); calls = []
        original = rig.broker.executions_today
        def repaired_query():
            calls.append(rig.clock.now())
            return [replace(f,commission=actual,time_source='reqExecutions')
                    if f.exec_id == final.exec_id else f for f in original()]
        monkeypatch.setattr(rig.broker,'executions_today',repaired_query)
        # Never emit final CommissionEvent; actual tick must request its own query.
        rig.advance(seconds=300); rig.tick()
        assert calls, 'closed trade missing commission needs its own executions query'
        assert len(calls) == 1
        assert rig.store.position_net_pnl(pid) == pytest.approx(final_net)
        trade = next(t for t in rig.store.closed_trades() if t['position_id'] == pid)
        assert trade['missing'] == [] and trade['fees'] == pytest.approx(3.01+actual)
        assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == before_fills
        assert rig.store.position(pid)['qty_open'] == 0
        assert rig.store.position(pid)['realized_pnl'] == pytest.approx(3.84)
        assert writes() == before_writes
        assert rig.store.control('cooldown_until') == before_until
        blocks = global_entry_blocks(rig.cfg,rig.store,rig.engine.flags,rig.clock.now())
        assert any(b.startswith('loss_cooldown_until:') for b in blocks)
        report = daily_report(rig.store,rig.cfg,date(*DAY))
        assert f'{3.01+actual:.2f}' in report and f'{final_net:.2f}' in report
        assert not [t for t,_ in rig.channel.messages if '持仓已关闭' in t or '冷却' in t]
        fee_messages = [b for t,b in rig.channel.messages
                        if '佣金' in t or ('佣金' in b and '净值' in b)]
        if flips:
            assert len(fee_messages) == 1
            record_property('b30_fee_sign_change_card',fee_messages[0])
            assert any('变' in b and ('冷却' in b or '门控' in b) for b in fee_messages)
            kinds = [r['kind'] for r in rig.store.conn.execute('SELECT kind FROM audit')]
            assert any('COMMISSION' in k and ('SIGN' in k or 'NET' in k) for k in kinds)
        else:
            assert fee_messages == []
        rig.advance(seconds=301); rig.tick()
        assert len(calls) == 1 and rig.store.position_net_pnl(pid) == pytest.approx(final_net)
        assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == before_fills
    finally:
        rig.alerter.close(); rig.store.close()


def test_B30_late_fee_after_daily_file_refreshes_local_report_without_extra_card(tmp_path):
    from v6lite.models import CommissionEvent
    rig,pid,final=_close_oct26(tmp_path)
    rig.at(et(16,5,day=DAY));rig.tick()
    path=rig.engine.report_dir/'2026-10-26.md'
    before=path.read_text(encoding='utf-8')
    assert '佣金未回报 1 笔' in before and '估算净值 -0.17' in before
    report_state=rig.store.control('report:2026-10-26');assert report_state.startswith('sent:')
    rig.channel.messages.clear()
    rig.broker.events.append(CommissionEvent(final.exec_id,1.))
    rig.advance(seconds=1);rig.tick()
    after=path.read_text(encoding='utf-8')
    assert after!=before, 'already-published local report must reflect newly booked final fee'
    assert '佣金未回报 1 笔' not in after and '最终净值 -0.17' in after
    assert rig.store.control('report:2026-10-26')==report_state
    assert not rig.channel.messages, 'same-sign final fee must not resend daily/closed/cooldown cards'

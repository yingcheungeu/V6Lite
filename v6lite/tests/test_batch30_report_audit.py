"""B30 real lifecycle and read-only daily evidence regressions."""
from __future__ import annotations
import json
import re
from dataclasses import replace
from datetime import timedelta
import pytest
from v6lite.market_calendar import trading_day
from v6lite.models import Purpose, StatusEvent
from v6lite.report import daily_report, write_daily_report
from v6lite.tests.test_batch23_contracts import _activate_tp, _g3t_working, _settle, _watch_boundaries, _safety
from v6lite.tests.test_batch29_faults import _request
from v6lite.tests.test_batch29_native_timeout import native_timeout, overnight_run, _no_network


def _audit(rig, kind):
    return [json.loads(r['payload']) for r in rig.store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY id', (kind,))]


def _report_tp_line(text, exec_id):
    return next(line for line in text.splitlines() if '成交相对 bid=' in line and 'execId='+exec_id in line)


def test_B30_real_native_timeout_audit_only_changes_and_at_most_eight_rows(native_timeout, monkeypatch, record_property):
    server = native_timeout()
    rig, pid = server.rig, server.pid
    server.request(monkeypatch); server.start_injected_tp()
    # Actual SDK/query/orderStatus callbacks repeat on the real Engine.tick scheduler.
    server.until(lambda: server.restore_ack_at is not None and rig.store.protection(pid)['state'] == 'WORKING'
        and rig.store.protection(pid)['gap_b_started_at'] is None and rig.store.protection(pid)['confirmed_remaining'] == 3)
    rows = server.audits()
    ids = {r.get('request_id') for r in rows if r.get('phase') == 'claimed'}
    assert len(ids) == 1
    request_id = next(iter(ids)); lifecycle = [r for r in rows if r.get('request_id') == request_id]
    phases = [r.get('phase') for r in lifecycle]
    record_property('b30_native_timeout_lifecycle_phases', json.dumps(phases))
    assert {'claimed','send','cancel_request','cancel_confirm','restore_confirm'} <= set(phases)
    assert len(lifecycle) <= 8, phases
    assert phases.count('send') == phases.count('cancel_confirm') == 1, phases
    server.assert_success_alerts(); server.assert_audit()


def test_B30_report_splits_actual_normal_and_injected_windows_and_normal_compliance(tmp_path, monkeypatch, record_property):
    rig, pid, path = _g3t_working(tmp_path, monkeypatch); _watch_boundaries(rig)
    first = _activate_tp(rig, pid, Purpose.TP1)
    rig.broker.protection_auto_status = True
    rig.advance(seconds=2.063); rig.broker.fill(first.intent_id, price=rig.broker.quote('AAPL').bid); _settle(rig)
    assert rig.store.position(pid)['tp1_done'] and rig.store.position(pid)['qty_open'] == 2
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert _request(path) == 0
    injected = _activate_tp(rig, pid, Purpose.TP2)
    assert injected.limit_price > rig.broker.quote('AAPL').ask
    rig.advance(seconds=10); rig.tick()
    rig.broker.confirm_cancel(injected.intent_id); rig.engine._drain(rig.clock.now()); _settle(rig)
    assert rig.store.protection(pid)['state'] == 'RESTORE_PENDING'
    rig.advance(seconds=4.256)
    rig.broker.confirm_protection_modify(rig.store.protection(pid)['intent_id']); _settle(rig)
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert rig.store.position(pid)['qty_open'] == 2 and not rig.store.position(pid)['tp2_done']
    ordinary = _activate_tp(rig, pid, Purpose.TP2)
    rig.broker.protection_auto_status = True
    rig.advance(seconds=2.051); rig.broker.fill(ordinary.intent_id, price=rig.broker.quote('AAPL').bid); _settle(rig)
    assert rig.store.position(pid)['tp2_done'] and rig.store.position(pid)['qty_open'] == 1
    gaps = [r for r in _audit(rig, 'PROTECT_GAP') if r.get('gap_kind') == 'B']
    assert len(gaps) == 3, gaps
    by_tp = {r['tp_intent_id']:r['secs'] for r in gaps}
    normal, test = max(by_tp[first.intent_id], by_tp[ordinary.intent_id]), by_tp[injected.intent_id]
    assert normal == pytest.approx(2.063) and test == pytest.approx(14.256)
    path_out, text = write_daily_report(rig.store, rig.cfg, trading_day(rig.clock.now()), tmp_path/'export')
    assert path_out.read_text(encoding='utf-8') == text
    # Public wording can be adjusted for punctuation while retaining independent quantities.
    assert re.search(r'B[（(]正常[）)][^；\n]*'+re.escape(f'{normal:.3f}s'), text), text
    assert re.search(r'B[（(]测试注入[）)][^；\n]*'+re.escape(f'{test:.3f}s'), text), text
    assert re.search(r'正常[^；\n]*(?:≤\s*5|<=\s*5)[^；\n]*(?:通过|合规|PASS)', text), text
    section = text.split('## Test TP timeout / 测试止盈超时',1)[1].split('## Fills',1)[0]
    assert all(word in section for word in ('claimed','cancel_request','cancel_confirm','restore_confirm','14.256'))
    assert str(rig.store.intent(injected.intent_id)['broker_order_id']) in section
    record_property('b30_gap_pair', json.dumps({'normal':normal,'injected':test}))
    _safety(rig)


def test_B30_report_bid_delta_updates_after_complete_query_verifies_uncertain_first_callback(tmp_path, monkeypatch):
    rig, pid, _ = _g3t_working(tmp_path, monkeypatch); _watch_boundaries(rig)
    req = _activate_tp(rig, pid)
    bid = json.loads(rig.store.control('tp_send:'+req.intent_id))['bid']
    rig.advance(seconds=1)
    raw = rig.broker.fill(req.intent_id, price=bid+.03, emit=False)
    callback = replace(raw, time_source='callback', time_uncertain=True)
    query = replace(raw, time=raw.time-timedelta(seconds=.1), time_source='reqExecutions', time_uncertain=False)
    # Initial complete query is still uncertain; later real query is certain.
    rig.broker.orders[req.intent_id].fills[-1] = replace(query, time_uncertain=True)
    rig.broker.events.extend([callback, StatusEvent(req.intent_id,'Filled',raw.quantity,0,raw.broker_order_id,raw.perm_id)])
    rig.broker.protection_auto_status = True
    rig.engine._drain(rig.clock.now()); _settle(rig)
    assert rig.store.control('execution_time_unverified:'+raw.exec_id) is not None
    initial = [r for r in _audit(rig,'TP_EVIDENCE') if r.get('exec_id') == raw.exec_id]
    assert len(initial) == 1 and initial[0]['fill_minus_bid'] is None
    rig.broker.orders[req.intent_id].fills[-1] = query
    rig.advance(seconds=16); rig.tick()
    assert rig.store.control('execution_time_unverified:'+raw.exec_id) is None
    assert any(r['exec_id'] == raw.exec_id for r in _audit(rig,'EXECUTION_TIME_VERIFIED'))
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills WHERE exec_id=?',(raw.exec_id,)).fetchone()[0] == 1
    line = _report_tp_line(daily_report(rig.store,rig.cfg,trading_day(rig.clock.now())),raw.exec_id)
    assert f'发送时 bid={bid:.2f}' in line
    assert '成交相对 bid=+0.0300 USD/股' in line, line
    # Report repair leaves the original callback evidence immutable.
    assert [r for r in _audit(rig,'TP_EVIDENCE') if r.get('exec_id') == raw.exec_id] == initial
    _safety(rig)


@pytest.mark.parametrize('missing',['send_snapshot','send_bid','verified_time'])
def test_B30_unknown_bid_delta_names_missing_input(tmp_path, monkeypatch, missing):
    rig, pid, _ = _g3t_working(tmp_path, monkeypatch); _watch_boundaries(rig)
    req = _activate_tp(rig,pid); key = 'tp_send:'+req.intent_id
    if missing == 'send_snapshot':
        rig.store.delete_control(key)  # Optional diagnostic loss, no economic/authority state edited.
    elif missing == 'send_bid':
        source = json.loads(rig.store.control(key)); source['bid'] = 'synthetic-invalid-bid'
        rig.store.set_control(rig.clock.now(),key,json.dumps(source))
    if missing in {'send_snapshot','send_bid'}:
        for event in list(rig.store.conn.execute("SELECT id,payload FROM audit WHERE kind='TP_EVIDENCE'")):
            data = json.loads(event['payload'])
            if data.get('phase') == 'send' and data.get('intent_id') == req.intent_id:
                if missing == 'send_snapshot':
                    rig.store.conn.execute('DELETE FROM audit WHERE id=?',(event['id'],))
                else:
                    data['bid'] = None
                    rig.store.conn.execute('UPDATE audit SET payload=? WHERE id=?',(json.dumps(data),event['id']))
    raw = rig.broker.fill(req.intent_id,emit=False)
    callback = replace(raw,time_source='callback',time_uncertain=missing == 'verified_time')
    rig.broker.orders[req.intent_id].fills[-1] = replace(raw,time_source='reqExecutions',time_uncertain=missing == 'verified_time')
    rig.broker.events.extend([callback,StatusEvent(req.intent_id,'Filled',raw.quantity,0,raw.broker_order_id,raw.perm_id)])
    rig.broker.protection_auto_status = True
    rig.engine._drain(rig.clock.now()); _settle(rig)
    line = _report_tp_line(daily_report(rig.store,rig.cfg,trading_day(rig.clock.now())),raw.exec_id)
    assert '成交相对 bid=未知' in line
    assert '原报价或成交时间未核实' not in line
    if missing == 'verified_time':
        assert '成交时间未核实' in line
    elif missing == 'send_bid':
        assert 'bid' in line and any(reason in line for reason in ('无效','缺失','invalid'))
    else:
        assert any(reason in line for reason in ('发送报价缺失','发送快照缺失','send_snapshot_missing'))
    _safety(rig)

def test_B30_lost_send_control_recovers_exact_immutable_send_bid(tmp_path,monkeypatch):
    rig,pid,_ = _g3t_working(tmp_path,monkeypatch);_watch_boundaries(rig)
    req = _activate_tp(rig,pid);bid = json.loads(rig.store.control('tp_send:'+req.intent_id))['bid']
    rig.store.delete_control('tp_send:'+req.intent_id)
    fill = rig.broker.fill(req.intent_id,price=bid+.07)
    rig.broker.protection_auto_status = True
    rig.engine._drain(rig.clock.now());_settle(rig)
    text = daily_report(rig.store,rig.cfg,trading_day(rig.clock.now()))
    assert '成交相对 bid=+0.0700 USD/股' in _report_tp_line(text,fill.exec_id)
    _safety(rig)



@pytest.mark.parametrize('bad',['none-pricing','list-pricing','huge-ask','weak-marker','other-workflow','bad-request-type'])
def test_B30_malformed_or_weak_test_claim_never_exempts_normal_B(tmp_path,monkeypatch,bad):
    from v6lite.report_evidence import injected_gap
    from v6lite.test_tp_timeout import CLAIM
    rig,pid,path=_g3t_working(tmp_path,monkeypatch);_watch_boundaries(rig)
    assert _request(path)==0
    req=_activate_tp(rig,pid);row=rig.store.protection(pid)
    fact=json.loads(rig.store.control(CLAIM+req.intent_id))
    gap=dict(row);gap['workflow_id']=fact['workflow_id']
    assert injected_gap(rig.store,gap)
    if bad=='weak-marker':rig.store.delete_control(CLAIM+req.intent_id)
    else:
        if bad=='none-pricing':fact['pricing']=None
        elif bad=='list-pricing':fact['pricing']=[]
        elif bad=='huge-ask':fact['pricing']['ask']=1e300
        elif bad=='other-workflow':fact['workflow_id']='other-closed-workflow'
        else:fact['request_id']=['malformed']
        rig.store.set_control(rig.clock.now(),CLAIM+req.intent_id,json.dumps(fact))
    assert not injected_gap(rig.store,gap)
    text=daily_report(rig.store,rig.cfg,trading_day(rig.clock.now()))
    assert 'B（正常）' in text and 'B（测试注入）' in text


def test_B30_native_P0_and_late_fill_are_single_durable_lifecycle_events(native_timeout,monkeypatch):
    server=native_timeout(late_fill=True);rig,pid=server.rig,server.pid
    server.request(monkeypatch);server.start_injected_tp()
    server.until(lambda: server.cancel_at is not None and server.late_fill_at is not None and
        rig.store.position(pid)['qty_open']==2 and rig.store.protection(pid)['state']=='WORKING' and
        rig.store.protection(pid)['gap_b_started_at'] is None)
    fill=server.actual_fills[0][1];rig.engine._ingest_fill(rig.clock.now(),fill)
    events=server.audits();late=[e for e in events if e.get('phase')=='late_fill']
    assert len(late)==1 and late[0]['exec_id']==fill.exec_id
    assert len([e for e in events if e.get('phase')=='cancel_confirm'])==1
    before=len(events)
    # Simulate a process with no diagnostic shortcut; original audit still dedupes.
    for key in rig.store.controls_with_prefix('test_tp_timeout_audit_seen:'):
        rig.store.delete_control(key)
    rig.engine._test_timeout_event(rig.clock.now(),rig.store.protection(pid),'cancel_confirm',server.held_ref)
    assert len(server.audits())==before
    server.assert_success_alerts();server.assert_audit()


def test_B30_native_failed_restore_keeps_original_P0_and_audits_once(native_timeout,monkeypatch):
    server=native_timeout();rig,pid=server.rig,server.pid
    monkeypatch.setattr(server,'cancel_response',lambda ref:None)
    server.request(monkeypatch);server.start_injected_tp()
    server.until(lambda:any(level=='P0' for level,_ in server.alerts()),timeout=45)
    events=server.audits()
    assert len([e for e in events if e.get('phase')=='P0'])==1
    assert server.restore_wire is None and rig.store.position(pid)['qty_open']==3
    assert rig.broker.orders[server.ref].remaining==2 and len(server.tp_packets)==1

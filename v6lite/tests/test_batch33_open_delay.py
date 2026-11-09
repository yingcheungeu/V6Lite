"""B33 opening TP barrier: real overnight ledger/restart and offline native ACKs.

Dates/first trigger and quotes follow the Oct 29 export. Thresholds, identities
and later quotes are explicit synthetic test inputs, never missing broker facts.
"""
from dataclasses import replace
from datetime import timedelta, date
import json
import pytest

from v6lite.models import Purpose
from v6lite.store import Store
from v6lite.tests.helpers import et
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _restart, _safety, _tp_requests, _writes
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _audits, _tick_until
from v6lite.tests.test_batch28c_price_confirmation import NativePriceWire

DAY = (2026, 10, 29)
OPEN = et(9, 30, day=DAY)


@pytest.fixture
def opening(overnight_run, monkeypatch):
    rig, pid, harness = overnight_run(entry_day=(2026, 10, 28), tp_day=DAY,
        entry_price=344.40, stop=340.58, tp1=345.68, tp2=346.63, tp3=347.58,
        first_id=2888, con_id=265598)
    wire = NativePriceWire(rig, pid, harness, monkeypatch)
    # Refresh a-d through the normal preopen tick, before the trigger arrives.
    rig.at(OPEN - timedelta(seconds=5)); rig.tick()
    return rig, pid, wire


def evaluate(rig, pid):
    rig.engine._evaluate_position_exit(rig.clock.now(), rig.store.position(pid),
                                     {'AAPL': rig.store.position(pid)['qty_open']})
    _safety(rig)


def hold(rig, pid, before):
    assert _writes(rig) == before
    assert not _audits(rig, 'STAGE_BEGIN')
    assert not rig.store.pending_stages(pid)
    assert rig.store.control('tp_workflow:' + pid) is None
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['tp_stage'] is None
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == rig.store.position(pid)['qty_open']
    assert rig.broker.orders[row['intent_id']].remaining == rig.store.position(pid)['qty_open']
    assert not _audits(rig, 'OVERNIGHT_TP_FROZEN')
    assert rig.store.control('frozen:AAPL') is None
    _safety(rig)


def start_tp(rig, pid, wire):
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'REDUCE_PENDING')
    count = len(_tp_requests(rig))
    wire.ack()
    _tick_until(rig, lambda: len(_tp_requests(rig)) > count)
    return _tp_requests(rig)[-1]


def finish_tp(rig, pid, wire, stage):
    req = start_tp(rig, pid, wire)
    assert req.purpose is stage and req.quantity == 1
    rig._b28b_callback_fill(req.intent_id, quantity=1, price=rig.broker.quote('AAPL').bid,
                          exec_id='b33-' + str(stage) + '.01.01')
    if stage is Purpose.TP1:
        _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'PRICE_PENDING')
        wire.ack()
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'WORKING' and
                rig.store.protection(pid)['tp_stage'] is None)
    _safety(rig)


def test_B33_oct29_real_overnight_replay(opening, record_property):
    rig, pid, wire = opening
    assert rig.store.control('overnight_tp_restored:' + pid)
    assert rig.store.position(pid)['opened_at'].startswith('2026-10-28')
    rig.at(OPEN + timedelta(microseconds=828649))
    rig.broker.set_quote('AAPL', bid=347.54, ask=347.82, last=347.70, ts=rig.clock.now())
    before = _writes(rig)
    evaluate(rig, pid)
    rig.tick()
    hold(rig, pid, before)
    rig.at(OPEN + timedelta(seconds=29, milliseconds=999))
    evaluate(rig, pid)
    hold(rig, pid, before)
    [defer] = _audits(rig, 'TP_START_DEFERRED_OPEN')
    assert defer['stage'] == 'TP1' and defer['trigger_level'] == 345.68
    assert defer['bid'] == 347.54 and defer['ask'] == 347.82
    assert defer['scheduled_start'] == (OPEN + timedelta(seconds=30)).isoformat()
    rig.at(OPEN + timedelta(seconds=30))
    rig.broker.set_quote('AAPL', bid=349.47, ask=349.64, last=349.55, ts=rig.clock.now())
    evaluate(rig, pid)
    begins = list(rig.store.conn.execute("SELECT ts FROM audit WHERE kind='STAGE_BEGIN'"))
    assert len(begins) == 1 and begins[0]['ts'] == rig.clock.now().isoformat()
    req = start_tp(rig, pid, wire)
    assert req.purpose is Purpose.TP1 and req.quantity == 1 and req.limit_price == 348.78
    assert len(_audits(rig, 'TP_START_DEFERRED_OPEN')) == 1
    record_property('b33_first_actual_stage_begin', begins[0]['ts'])
    record_property('b33_current_quote_limit', req.limit_price)


def test_B33_stop_crossing_at_open_plus_5_is_unchanged(opening):
    rig, pid, wire = opening
    ref = rig.store.protection(pid)['intent_id']
    before = _writes(rig)
    rig.at(OPEN + timedelta(seconds=5)); rig.px('AAPL', 339.50)
    rig.tick()
    assert _writes(rig) == before and not _tp_requests(rig)
    assert rig.store.control('protect_trigger:' + pid)
    assert any(level == 'P1' and '已到止损价' in text for level, text in rig._b26_alerts)
    assert rig.broker.orders[ref].status not in {'Cancelled', 'Filled'}
    rig._b28b_callback_fill(ref, quantity=3, price=339.48, exec_id='b33-stop.01.01')
    assert rig.store.position(pid)['qty_open'] == 0
    assert _audits(rig, 'PROTECT_FILL') and not _audits(rig, 'TP_START_DEFERRED_OPEN')
    _safety(rig)


@pytest.fixture
def last_overnight(overnight_run, tmp_path, monkeypatch):
    rig, pid, harness = overnight_run(entry_day=(2026, 10, 27), tp_day=(2026, 10, 28))
    wire = NativePriceWire(rig, pid, harness, monkeypatch)
    rig.at(et(11, 3, day=(2026, 10, 28))); rig.px('AAPL', 343.94)
    finish_tp(rig, pid, wire, Purpose.TP1)
    rig.px('AAPL', 344.65)
    finish_tp(rig, pid, wire, Purpose.TP2)
    assert rig.store.position(pid)['qty_open'] == 1
    rig.engine.shutdown(grace_seconds=0); rig.alerter.close()
    path, levels = rig.store.path, rig.store._levels_fn
    rig.store.close(); rig.store = Store(path, levels)
    rig.at(et(8, 59, 59, day=DAY)); _restart(rig, tmp_path, monkeypatch)
    assert rig.store.control('overnight_tp_restored:' + pid)
    assert rig.store.protection(pid)['confirmed_remaining'] == 1
    return rig, pid, wire


def test_B33_last_overnight_share_waits_before_TP3_cancel(last_overnight):
    rig, pid, wire = last_overnight
    ref = rig.store.protection(pid)['intent_id']
    count = len(_audits(rig, 'STAGE_BEGIN')); before = _writes(rig)
    rig.at(OPEN + timedelta(seconds=1)); rig.px('AAPL', 350.)
    evaluate(rig, pid); rig.tick()
    assert _writes(rig) == before
    assert len(_audits(rig, 'STAGE_BEGIN')) == count
    assert rig.broker.orders[ref].remaining == 1 and ref not in rig.broker.cancels
    assert rig.store.protection(pid)['tp_stage'] is None
    rig.at(OPEN + timedelta(seconds=30)); evaluate(rig, pid)
    _tick_until(rig, lambda: ref in rig.broker.cancels)
    assert rig.store.protection(pid)['tp_stage'] == 'TP3'
    assert len(_tp_requests(rig)) == 2, 'no final DAY SELL before cancel confirmation'
    rig.broker.confirm_cancel(ref); rig.engine._drain(rig.clock.now())
    _tick_until(rig, lambda: len(_tp_requests(rig)) == 3)
    req = _tp_requests(rig)[-1]
    assert req.purpose is Purpose.TP3 and req.quantity == 1
    _safety(rig)


@pytest.mark.parametrize('seconds,offset,blocked', [(0, .8, False), (60, 59.999, True), (60, 60., False)])
def test_B33_configured_boundary(opening, seconds, offset, blocked):
    rig, pid, wire = opening
    rig.cfg = replace(rig.cfg, tp_start_after_open_seconds=seconds); rig.engine.cfg = rig.cfg
    if offset > 50:
        rig.at(OPEN + timedelta(seconds=55)); rig.tick()
    rig.at(OPEN + timedelta(seconds=offset)); rig.px('AAPL', 347.70)
    before = _writes(rig); evaluate(rig, pid)
    assert bool(_audits(rig, 'STAGE_BEGIN')) is not blocked
    if blocked: hold(rig, pid, before)


def test_B33_vanished_trigger_is_not_replayed(opening):
    rig, pid, wire = opening
    rig.at(OPEN + timedelta(seconds=10)); rig.px('AAPL', 347.70)
    before = _writes(rig); evaluate(rig, pid)
    rig.at(OPEN + timedelta(seconds=25)); rig.px('AAPL', 345.)
    rig.at(OPEN + timedelta(seconds=30)); evaluate(rig, pid); rig.tick()
    hold(rig, pid, before)
    assert len(_audits(rig, 'TP_START_DEFERRED_OPEN')) == 1


def test_B33_multiple_thresholds_do_not_skip_or_merge(opening):
    rig, pid, wire = opening
    rig.at(OPEN + timedelta(seconds=10)); rig.px('AAPL', 345.80); evaluate(rig, pid)
    rig.at(OPEN + timedelta(seconds=20)); rig.px('AAPL', 350.); evaluate(rig, pid)
    assert not _audits(rig, 'STAGE_BEGIN')
    rig.at(OPEN + timedelta(seconds=30)); evaluate(rig, pid)
    req = start_tp(rig, pid, wire)
    assert req.purpose is Purpose.TP1 and req.quantity == 1
    assert [r['stage'] for r in _audits(rig, 'STAGE_BEGIN')] == ['TP1']
    _safety(rig)


def test_B33_workflow_entry_cannot_bypass_wait(opening):
    rig, pid, wire = opening
    rig.at(OPEN + timedelta(seconds=10)); rig.px('AAPL', 350.)
    before = _writes(rig)
    rig.engine._protection_begin_tp(rig.clock.now(), pid, Purpose.TP1)
    hold(rig, pid, before)
    assert len(_audits(rig, 'TP_START_DEFERRED_OPEN')) == 1


def test_B33_new_quote_must_be_fresh_at_actual_start(opening):
    rig, pid, wire = opening
    rig.at(OPEN + timedelta(seconds=10)); rig.px('AAPL', 350.); evaluate(rig, pid)
    rig.at(OPEN + timedelta(seconds=30))
    rig.broker.set_quote('AAPL', bid=349.47, ask=349.64, last=349.55,
                         ts=rig.clock.now() - timedelta(seconds=121))
    before = _writes(rig); evaluate(rig, pid)
    hold(rig, pid, before)


def test_B33_existing_workflow_restoration_continues(opening):
    rig, pid, wire = opening
    # Establish a preopen workflow through the existing N=0 path, then enable
    # the barrier. Existing reduction/timeout/cancel/restore must keep running.
    rig.cfg = replace(rig.cfg, tp_start_after_open_seconds=0); rig.engine.cfg = rig.cfg
    rig.at(OPEN - timedelta(seconds=1)); rig.px('AAPL', 350.)
    evaluate(rig, pid); rig.engine._run_queue()
    assert rig.store.protection(pid)['state'] == 'REDUCE_PENDING'
    rig.cfg = replace(rig.cfg, tp_start_after_open_seconds=30); rig.engine.cfg = rig.cfg
    rig.at(OPEN + timedelta(seconds=1)); wire.ack()
    _tick_until(rig, lambda: bool(_tp_requests(rig)))
    req = _tp_requests(rig)[0]
    rig.advance(seconds=10); rig.tick()
    _tick_until(rig, lambda: req.intent_id in rig.broker.cancels)
    rig.broker.confirm_cancel(req.intent_id); rig.engine._drain(rig.clock.now())
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'RESTORE_PENDING')
    rig.px('AAPL', 345.)
    wire.ack()
    _tick_until(rig, lambda: rig.store.protection(pid)['state'] == 'WORKING')
    assert rig.store.protection(pid)['state'] == 'WORKING'
    assert not _audits(rig, 'TP_START_DEFERRED_OPEN')
    _safety(rig)


@pytest.mark.parametrize('day,hour', [((2026, 11, 27), 14), ((2026, 3, 6), 14),
    ((2026, 3, 9), 13), ((2026, 10, 30), 13), ((2026, 11, 2), 14)])
def test_B33_calendar_half_day_and_DST(day, hour):
    from v6lite.tp_start import tp_start_deadline
    from v6lite.config import LiteConfig
    now = et(9, 30, 29, day=day)
    deadline = tp_start_deadline(LiteConfig(mode='paper', account='DU_TEST'), now)
    assert deadline.hour == hour and deadline.minute == 30 and deadline.second == 30
    if day == (2026, 11, 27):
        from v6lite.market_calendar import session_for
        assert session_for(date(*day)).close_utc == et(13, day=day)


@pytest.mark.parametrize('day', [(2026, 7, 3), (2026, 11, 1), (2026, 3, 8)])
def test_B33_closed_day_has_no_barrier(day):
    from v6lite.tp_start import tp_start_deadline
    from v6lite.config import LiteConfig
    assert tp_start_deadline(LiteConfig(mode='paper', account='DU_TEST'), et(9, 30, day=day)) is None


@pytest.mark.parametrize('value', [-1, 1.5, 30., float('nan'), float('inf'), -float('inf'), True, '30', None])
def test_B33_invalid_config_refuses_start(tmp_path, value):
    from v6lite.config import ConfigError, validate
    from v6lite.tests.helpers import make_cfg
    cfg = replace(make_cfg(tmp_path), tp_start_after_open_seconds=value)
    with pytest.raises(ConfigError, match='tp_start_after_open_seconds'):
        validate(cfg)


def test_B33_config_default_TOML_and_fingerprint(tmp_path):
    from v6lite.config import load_config
    from v6lite.tests.helpers import make_cfg
    cfg = make_cfg(tmp_path)
    assert cfg.tp_start_after_open_seconds == 30
    assert replace(cfg, tp_start_after_open_seconds=0).fingerprint() != cfg.fingerprint()
    p = tmp_path / 'valid.toml'
    p.write_text('mode="paper"\n[ibkr]\naccount="DU_TEST"\n[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n[execution]\ntp_start_after_open_seconds=60\n')
    assert load_config(p).tp_start_after_open_seconds == 60


def test_B33_report_joins_actual_start_and_never_invents_one(opening, tmp_path):
    from v6lite.report import daily_report
    from v6lite.telemetry import redact_accounts
    rig, pid, wire = opening
    rig.at(OPEN + timedelta(seconds=10)); rig.px('AAPL', 350.); evaluate(rig, pid)
    report = daily_report(rig.store, rig.cfg, date(*DAY))
    assert '开盘止盈延后：1 次' in report and '尚未开始' in report
    rig.at(OPEN + timedelta(seconds=30)); evaluate(rig, pid)
    report = daily_report(rig.store, rig.cfg, date(*DAY))
    assert '实际开始 2026-10-29T13:30:30+00:00' in report
    card = rig.engine._startup_card(rig.clock.now(), rig.engine.status_snapshot(rig.clock.now()))
    assert '开盘后 30 秒内不开始新止盈' in card.as_plain_text()
    # Delivered examples come from the same real renderer, with test account masked.
    import os
    out = os.environ.get('V6LITE_B33_SAMPLES')
    if out:
        from pathlib import Path
        Path(out).mkdir(parents=True, exist_ok=True)
        Path(out, 'daily_report_sample.md').write_text(redact_accounts(report, rig.cfg.account), encoding='utf-8')
        Path(out, 'startup_card_sample.txt').write_text(redact_accounts(card.as_plain_text(), rig.cfg.account), encoding='utf-8')


def test_B33_report_observes_workflow_start_for_preexisting_pending_stage(opening):
    from v6lite.report import daily_report
    rig,pid,wire=opening
    rig.cfg=replace(rig.cfg,tp_start_after_open_seconds=0);rig.engine.cfg=rig.cfg
    rig.at(OPEN-timedelta(seconds=1));rig.px('AAPL',350.);evaluate(rig,pid)
    assert rig.store.stage(pid,Purpose.TP1)['status']=='PENDING'
    rig.cfg=replace(rig.cfg,tp_start_after_open_seconds=30);rig.engine.cfg=rig.cfg
    rig.at(OPEN+timedelta(seconds=10))
    rig.engine._protection_begin_tp(rig.clock.now(),pid,Purpose.TP1)
    assert '尚未开始' in daily_report(rig.store,rig.cfg,date(*DAY))
    rig.at(OPEN+timedelta(seconds=30))
    rig.engine._protection_begin_tp(rig.clock.now(),pid,Purpose.TP1)
    assert rig.store.protection(pid)['state']=='REDUCE_PENDING'
    report=daily_report(rig.store,rig.cfg,date(*DAY))
    [actual]=list(rig.store.conn.execute("SELECT ts FROM audit WHERE kind='TP_EVIDENCE' AND json_extract(payload,'$.phase')='reduce_request'"))
    assert '实际开始 '+actual['ts'] in report
    assert '尚未开始' not in report.split('## Opening TP deferrals')[1].split('## Commission')[0]
    assert len(_audits(rig,'STAGE_BEGIN'))==1


def test_B33_disabled_preserves_b32_order_trace(opening):
    rig,pid,wire=opening
    if 'tp_start_after_open_seconds' in rig.cfg.__dataclass_fields__:
        rig.cfg=replace(rig.cfg,tp_start_after_open_seconds=0);rig.engine.cfg=rig.cfg
    rig.at(OPEN+timedelta(microseconds=828649))
    rig.broker.set_quote('AAPL',bid=347.54,ask=347.82,last=347.70,ts=rig.clock.now())
    evaluate(rig,pid)
    assert [a['stage'] for a in _audits(rig,'STAGE_BEGIN')]==['TP1']
    req=start_tp(rig,pid,wire)
    assert req.purpose is Purpose.TP1 and req.quantity==1 and req.limit_price==346.85
    assert _writes(rig)==(2,1,1,0)
    assert not _audits(rig,'TP_START_DEFERRED_OPEN')
    _safety(rig)


@pytest.mark.parametrize('bad', ['identity','missing_end','stale_book'])
def test_B33_actual_start_rechecks_existing_fail_closed_gates(opening, monkeypatch, bad):
    rig,pid,wire=opening
    rig.at(OPEN+timedelta(seconds=10));rig.px('AAPL',350.);evaluate(rig,pid)
    before=_writes(rig)
    rig.at(OPEN+timedelta(seconds=30));evaluate(rig,pid)
    if bad=='identity':
        wire.transform=lambda o: replace(o,perm_id=o.perm_id+1) if o.order_type=='STP' else o
    elif bad=='missing_end':
        # Offline server withholds its own End; no authority is injected.
        monkeypatch.setattr(wire.ib.wrapper,'openOrderEnd',lambda:None)
    else:
        q=rig.broker.quote('AAPL')
        rig.broker.set_quote('AAPL',bid=q.bid,ask=q.ask,last=q.last,ts=rig.clock.now()-timedelta(seconds=4))
    rig.engine._run_queue();rig.engine._drain(rig.clock.now())
    assert _writes(rig)==before and not _tp_requests(rig)
    assert rig.broker.orders[rig.store.protection(pid)['intent_id']].remaining==3
    assert not _audits(rig,'TP_EVIDENCE') or not [a for a in _audits(rig,'TP_EVIDENCE') if a.get('phase')=='reduce_request']


def test_B33_wait_has_no_card_retry_or_workflow_effect(opening):
    rig,pid,wire=opening
    rig.at(OPEN+timedelta(seconds=10));rig.px('AAPL',350.)
    cards=list(rig.channel.messages);before=_writes(rig)
    for _ in range(3):
        evaluate(rig,pid)
        rig.engine._protection_begin_tp(rig.clock.now(),pid,Purpose.TP1)
    hold(rig,pid,before)
    assert rig.channel.messages==cards
    assert not rig.engine._exit_backoff
    assert len(_audits(rig,'TP_START_DEFERRED_OPEN'))==1


@pytest.mark.parametrize('field,value', [('bid',float('nan')),('ask',float('inf')),
    ('last',float('nan')),('last',float('inf'))])
def test_B33_defer_audit_nonfinite_quote_stays_valid_JSON_and_idempotent(opening,field,value):
    rig,pid,wire=opening
    rig.at(OPEN+timedelta(seconds=10))
    book=dict(bid=348.,ask=350.,last=349.);book[field]=value
    rig.broker.set_quote('AAPL',**book,ts=rig.clock.now())
    before=_writes(rig);cards=list(rig.channel.messages)
    evaluate(rig,pid)
    rows=list(rig.store.conn.execute("SELECT payload,json_valid(payload) AS valid FROM audit WHERE kind='TP_START_DEFERRED_OPEN'"))
    if not rows:
        rig.engine._protection_begin_tp(rig.clock.now(),pid,Purpose.TP1)
        rows=list(rig.store.conn.execute("SELECT payload,json_valid(payload) AS valid FROM audit WHERE kind='TP_START_DEFERRED_OPEN'"))
    assert len(rows)==1 and rows[0]['valid']==1, 'new audit must not persist JSON NaN/Infinity'
    fact=json.loads(rows[0]['payload']);assert fact[field] is None
    assert fact[field+'_raw'] in {'nan','inf'}
    evaluate(rig,pid)
    rig.engine._protection_begin_tp(rig.clock.now(),pid,Purpose.TP1)
    hold(rig,pid,before)
    assert rig.channel.messages==cards
    assert len(_audits(rig,'TP_START_DEFERRED_OPEN'))==1

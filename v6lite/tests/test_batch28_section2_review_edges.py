"""Review edge regressions: process scope and protection priority before audit."""
from datetime import timedelta
import pytest
from v6lite.tests.helpers import et
from v6lite.tests.test_batch28_section2 import (
    DAY, _reporting, _finish_report, _market_lines, _seed_repeats,
    _quiet_observer_tick,
)
from v6lite.tests.test_batch27_observation_audit import START, observation_rig, _count


@pytest.mark.parametrize('kind', ['SCAN', 'SHUTDOWN', 'ORDER_PRICE_OBSERVATION'])
def test_overnight_process_restarted_once_today_is_disclosed(tmp_path, kind):
    start=et(15,9,57,day=DAY)
    with _reporting(tmp_path,start=start) as rig:
        rig.store.audit(et(15,day=(2026,10,19)), 'STARTUP', code_sha='synthetic-overnight')
        rig.store.audit(et(11,day=DAY),kind,reason='synthetic previous process running today')
        rig.engine._heartbeat(start)
        assert rig.store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind='STARTUP' AND ts>=?",
                                      (et(0,day=DAY).isoformat(),)).fetchone()[0]==1
        for line in _market_lines(_finish_report(rig)):
            assert '当天有过重启' in line
            assert '2026-10-20 12:09' in line


def test_normal_daily_start_after_yesterday_shutdown_is_not_same_day_restart(tmp_path):
    start=et(15,9,57,day=DAY)
    with _reporting(tmp_path,start=start) as rig:
        rig.store.audit(et(10,day=(2026,10,19)),'STARTUP',code_sha='synthetic-yesterday')
        rig.store.audit(et(16,day=(2026,10,19)),'SHUTDOWN',source='SIGINT')
        rig.store.audit(et(8,day=DAY),'KILL',reason='synthetic CLI only')
        rig.engine._heartbeat(start)
        for line in _market_lines(_finish_report(rig)):
            assert '当天有过重启' not in line


def test_due_audit_flush_runs_after_protection_events_even_when_disconnected(
        observation_rig,monkeypatch):
    rig=observation_rig()
    _seed_repeats(rig)
    _quiet_observer_tick(rig,monkeypatch)
    order=[]
    monkeypatch.setattr(rig.engine,'_drain',lambda *args:order.append('events'))
    monkeypatch.setattr(rig.engine,'_protection_timers',lambda *args:order.append('protection'))
    original=rig.store.flush_due_observations
    def flush(now):
        order.append('flush')
        return original(now)
    monkeypatch.setattr(rig.store,'flush_due_observations',flush)
    rig.at(START+timedelta(minutes=30,seconds=1))
    rig.tick()
    assert order==['events','events','protection','flush']
    assert _count(rig.store,'OBSERVATION_SUMMARY')==1


def test_urgent_protection_continuation_defers_due_audit_until_next_clear_tick(
        observation_rig,monkeypatch):
    rig=observation_rig()
    _seed_repeats(rig)
    _quiet_observer_tick(rig,monkeypatch)
    def urgent(*args):
        rig.engine._protection_priority_due=True
    monkeypatch.setattr(rig.engine,'_drain',urgent)
    rig.at(START+timedelta(minutes=30,seconds=1))
    rig.tick()
    assert _count(rig.store,'OBSERVATION_SUMMARY')==0
    _quiet_observer_tick(rig,monkeypatch)
    rig.advance(seconds=1)
    rig.tick()
    assert _count(rig.store,'OBSERVATION_SUMMARY')==1

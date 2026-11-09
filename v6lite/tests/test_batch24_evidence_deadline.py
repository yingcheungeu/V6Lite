"""Persistent steady-proof deadlines remain independent of query backoff."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from v6lite.config import ProtectionConfig
from v6lite.tests.helpers import Rig


def _pages(rig):
    return [title for title, _ in rig.channel.messages
            if "P0" in title and "保护单三项证据不完整" in title]


def _writes(rig):
    return tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                         rig.broker.modified_protections, rig.broker.cancels))


def _backoffs(rig, pid):
    rows = rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='PROTECT_VERIFICATION_BACKOFF' ORDER BY id")
    return [payload for row in rows if (payload := json.loads(row['payload']))['position_id'] == pid]


def _track_queries(rig, monkeypatch):
    counts = {name: 0 for name in ('open_orders', 'positions_fresh', 'executions_today')}
    for name in counts:
        query = getattr(rig.broker, name)
        def counted(*args, _name=name, _query=query, **kwargs):
            counts[_name] += 1
            return _query(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, counted)
    return counts


def _rig(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(
        steady_check_seconds=15, evidence_fail_p0_seconds=60),
        scan_times_et=("10:00", "10:01"))
    rig.open_aapl()
    [row] = rig.store.protections()
    assert row['state'] == 'WORKING' and row['pending_qty'] is None
    assert row['tp_stage'] is None
    assert row['gap_a_started_at'] is None and row['gap_b_started_at'] is None
    rig.advance(seconds=16)
    return rig, row


def test_B24_steady_evidence_P0_is_due_at_60_seconds_during_query_backoff(tmp_path, monkeypatch):
    rig, row = _rig(tmp_path)
    try:
        pid = row['position_id']
        key = f'protect_evidence_failure:{pid}'
        start = rig.clock.now()
        counts = _track_queries(rig, monkeypatch)
        writes = _writes(rig)
        exits, scans = [], []
        real_exit, real_scan = rig.engine._evaluate_exits, rig.engine._run_scheduled_scans
        def observed_exit(now, *, regular=False):
            if regular:
                exits.append(now)
            return real_exit(now, regular=regular)
        def observed_scan(now):
            scans.append(now)
            return real_scan(now)
        monkeypatch.setattr(rig.engine, '_evaluate_exits', observed_exit)
        monkeypatch.setattr(rig.engine, '_run_scheduled_scans', observed_scan)
        rig.broker.fail_open_orders = True
        for index, elapsed in enumerate((0, 1, 2, 13, 59), start=1):
            rig.at(start + timedelta(seconds=elapsed))
            rig.tick()
            assert _pages(rig) == []
            assert len(exits) == len(scans) == index
            assert not rig.engine._protection_priority_due
        backoffs = _backoffs(rig, pid)
        assert [item['failures'] for item in backoffs] == [1, 2, 3, 4]
        assert [item['seconds'] for item in backoffs] == [1, 2, 4, 8]
        failure = json.loads(rig.store.control(key))
        assert datetime.fromisoformat(failure['since']) == start and failure['paged'] is False
        waiting_counts = dict(counts)

        # The actual timer callback must neither query nor postpone the F4 deadline.
        rig.at(start + timedelta(seconds=59.999))
        rig.engine._hard_protection_deadlines(rig.clock.now())
        assert _pages(rig) == []
        assert counts == waiting_counts and _backoffs(rig, pid) == backoffs
        rig.at(start + timedelta(seconds=60))
        rig.tick()
        assert len(_pages(rig)) == 1
        assert counts == waiting_counts and _backoffs(rig, pid) == backoffs
        assert len(exits) == len(scans) == 6
        assert not rig.engine._protection_priority_due
        failure = json.loads(rig.store.control(key))
        assert datetime.fromisoformat(failure['since']) == start and failure['paged'] is True
        for _ in range(3):
            rig.engine._hard_protection_deadlines(rig.clock.now())
        rig.at(start + timedelta(seconds=66.999))
        rig.tick()
        assert len(_pages(rig)) == 1
        assert counts == waiting_counts and _backoffs(rig, pid) == backoffs
        assert len(exits) == len(scans) == 7
        assert not rig.engine._protection_priority_due
        assert _writes(rig) == writes
        waiting = rig.store.protection(pid)
        assert waiting['intent_id'] == row['intent_id'] and waiting['confirmed_remaining'] == 20
        assert rig.store.position(pid)['qty_open'] == 20

        # Only the genuinely due, complete three-source query clears the incident.
        rig.broker.fail_open_orders = False
        rig.at(start + timedelta(seconds=67))
        rig.tick()
        assert all(counts[name] > waiting_counts[name] for name in counts)
        assert rig.store.control(key) is None
        assert _backoffs(rig, pid) == backoffs and _writes(rig) == writes
        restored = rig.store.protection(pid)
        assert restored['state'] == 'WORKING' and restored['intent_id'] == row['intent_id']
        assert restored['confirmed_remaining'] == restored['pending_max_remaining'] == 20
        assert datetime.fromisoformat(restored['confirmed_at']) == rig.clock.now()
        rig.advance(seconds=61)
        rig.tick()
        assert len(_pages(rig)) == 1 and rig.store.control(key) is None
        assert _writes(rig) == writes
    finally:
        rig.store.close()


def test_B24_complete_fresh_proof_before_deadline_prevents_an_old_evidence_P0(tmp_path, monkeypatch):
    rig, row = _rig(tmp_path)
    try:
        pid = row['position_id']
        key = f'protect_evidence_failure:{pid}'
        start = rig.clock.now()
        counts = _track_queries(rig, monkeypatch)
        writes = _writes(rig)
        rig.broker.fail_open_orders = True
        for elapsed in (0, 1, 13):
            rig.at(start + timedelta(seconds=elapsed))
            rig.tick()
        assert _pages(rig) == [] and rig.store.control(key) is not None
        assert [item['seconds'] for item in _backoffs(rig, pid)] == [1, 2, 4]
        rig.broker.fail_open_orders = False
        before = dict(counts)
        rig.at(start + timedelta(seconds=17))
        rig.tick()
        assert all(counts[name] > before[name] for name in counts)
        assert rig.store.control(key) is None
        healthy = rig.store.protection(pid)
        assert healthy['state'] == 'WORKING' and healthy['confirmed_remaining'] == 20
        assert healthy['intent_id'] == row['intent_id']
        assert datetime.fromisoformat(healthy['confirmed_at']) == rig.clock.now()
        after = dict(counts)
        backoffs = _backoffs(rig, pid)
        for elapsed in (59.999, 60, 67):
            rig.at(start + timedelta(seconds=elapsed))
            rig.engine._hard_protection_deadlines(rig.clock.now())
        assert _pages(rig) == []
        assert counts == after and _backoffs(rig, pid) == backoffs
        assert rig.store.control(key) is None and _writes(rig) == writes
        assert not rig.engine._protection_priority_due
    finally:
        rig.store.close()

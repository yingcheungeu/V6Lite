"""B28 section 2 contracts, using only TEMP SQLite and synthetic broker replies.

Behavioral regressions enter through APIs already present in B28a.  The last
two tests are explicitly candidate-only controls for the new due-flush API;
their absence on B28a is not evidence of a behavioral baseline failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from v6lite.models import FundsSnapshot, OrderRequest, Purpose, Side
from v6lite.report import daily_report, write_daily_report
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch27_observation_audit import (
    START, observation_rig, _observe, _count, _summary_items,
)
from v6lite.tests.test_batch28_lifecycle import _holding, _writes, no_network


DAY = (2026, 10, 20)
REPORT_DAY = date(*DAY)


def _db_digest(store):
    rows = [tuple(row) for row in store.conn.execute(
        'SELECT id,ts,kind,payload FROM audit ORDER BY id')]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode('utf-8')).hexdigest()


def _proof_snapshot(engine):
    return copy.deepcopy({name: getattr(engine, name) for name in (
        '_fresh_seq', '_fresh_held', '_entry_evidence_at', '_last_reconcile',
        '_protection_notice_evidence', '_last_fill_at',
    )})


@pytest.mark.parametrize('case,ready,age,reconciled,value,level', [
    ('held-normal', True, 211, True, 1_000_000., logging.DEBUG),
    ('funds-unknown', True, 211, True, None, logging.WARNING),
    ('not-ready', False, 211, True, 1_000_000., logging.WARNING),
    ('too-old', True, 271, True, 1_000_000., logging.WARNING),
    ('unverified', True, 211, False, 1_000_000., logging.WARNING),
], ids=lambda value: value if isinstance(value, str) else None)
def test_B28_held_silent_account_uses_funds_snapshot_without_replacing_evidence(
        tmp_path, monkeypatch, caplog, case, ready, age, reconciled, value, level):
    """A request is never a funds reply or a new three-source proof."""
    rig, pid = _holding(tmp_path, monkeypatch)
    try:
        rig.at(et(11, day=DAY))
        now = rig.clock.now()
        seen = now - timedelta(seconds=age)
        original = FundsSnapshot(value, seen, seen, ready)
        monkeypatch.setattr(rig.broker, 'available_funds', lambda: original)
        rig.broker.funds_as_of = seen
        rig.broker.funds_seen_at = seen
        rig.broker.account_answers_refresh = False
        rig.engine.flags.reconcile_ok = reconciled
        rig.engine._last_account_refresh = None
        before_proof = _proof_snapshot(rig.engine)
        before_position = dict(rig.store.position(pid))
        before_protection = dict(rig.store.protection(pid))
        before_writes = _writes(rig)
        before_audit = _db_digest(rig.store)
        funds_count = getattr(rig.broker, 'funds_refreshes', 0)
        stream_count = getattr(rig.broker, 'account_refreshes', 0)
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger='v6lite.engine'):
            rig.engine._refresh_equity(now, force=True)
        assert getattr(rig.broker, 'funds_refreshes', 0) == funds_count + 1, case
        assert getattr(rig.broker, 'account_refreshes', 0) == stream_count, case
        messages = [record for record in caplog.records if '[ACCOUNT_STREAM]' in record.message]
        assert len(messages) == 1 and messages[0].levelno == level
        assert 're-subscribed' not in messages[0].message
        assert rig.broker.available_funds() == original
        assert rig.broker.funds_as_of == seen == rig.broker.funds_seen_at
        assert _proof_snapshot(rig.engine) == before_proof
        assert dict(rig.store.position(pid)) == before_position
        assert dict(rig.store.protection(pid)) == before_protection
        assert _writes(rig) == before_writes
        assert _db_digest(rig.store) == before_audit
        assert rig.engine.flags.reconcile_ok is reconciled
        if case != 'held-normal':
            rig.engine._entry_evidence_at = now
            req = OrderRequest('b28-funds-gate', 'TSLA', Side.BUY, 1, 250., Purpose.ENTRY)
            refusal = rig.engine._entry_gate(now, req)
            assert refusal and refusal.startswith('refused:'), (case, refusal)
            if reconciled:
                assert 'funds_' in refusal, (case, refusal)
            else:
                assert 'reconcile:' in refusal
            assert _writes(rig) == before_writes
    finally:
        rig.alerter.close()
        rig.store.close()


@pytest.mark.parametrize('missing', ['as_of', 'seen_at'])
def test_B28_missing_funds_timestamp_warns_and_does_not_cancel_account_stream(
        tmp_path, monkeypatch, caplog, missing):
    rig, _ = _holding(tmp_path, monkeypatch)
    try:
        now = rig.clock.now()
        old = now - timedelta(seconds=211)
        snapshot = FundsSnapshot(1_000_000., None if missing == 'as_of' else old,
                                 None if missing == 'seen_at' else old, True)
        monkeypatch.setattr(rig.broker, 'available_funds', lambda: snapshot)
        rig.engine.flags.reconcile_ok = True
        rig.engine._last_account_refresh = None
        before = getattr(rig.broker, 'account_refreshes', 0)
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger='v6lite.engine'):
            rig.engine._refresh_equity(now, force=True)
        assert getattr(rig.broker, 'account_refreshes', 0) == before
        assert getattr(rig.broker, 'funds_refreshes', 0) >= 1
        messages = [record for record in caplog.records if '[ACCOUNT_STREAM]' in record.message]
        assert len(messages) == 1 and messages[0].levelno == logging.WARNING
        assert rig.broker.available_funds() == snapshot
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_funds_requests_stay_rate_limited_without_marking_silent_data_fresh(
        tmp_path, monkeypatch):
    rig, _ = _holding(tmp_path, monkeypatch)
    try:
        now = rig.clock.now()
        old = now - timedelta(seconds=211)
        snapshot = FundsSnapshot(1_000_000., old, old, True)
        monkeypatch.setattr(rig.broker, 'available_funds', lambda: snapshot)
        rig.engine.flags.reconcile_ok = True
        rig.engine._last_account_refresh = None
        before = getattr(rig.broker, 'funds_refreshes', 0)
        stream_count = getattr(rig.broker, 'account_refreshes', 0)
        rig.engine._refresh_equity(now, force=True)
        rig.engine._refresh_equity(now + timedelta(seconds=209), force=True)
        assert getattr(rig.broker, 'funds_refreshes', 0) == before + 1
        rig.engine._refresh_equity(now + timedelta(seconds=210), force=True)
        assert getattr(rig.broker, 'funds_refreshes', 0) == before + 2
        assert getattr(rig.broker, 'account_refreshes', 0) == stream_count
        assert rig.broker.available_funds() == snapshot
    finally:
        rig.alerter.close()
        rig.store.close()


@contextmanager
def _reporting(tmp_path, *, start=None):
    rig = Rig(tmp_path, start=start or et(9, day=DAY), dry_run=True,
              scan_times_et=('10:00',))
    try:
        rig.engine.startup()
        yield rig
    finally:
        rig.alerter.close()
        rig.store.close()


def _finish_report(rig, day=DAY):
    rig.at(et(16, 6, day=day))
    rig.engine._heartbeat(rig.clock.now())
    rig.engine._end_of_day(rig.clock.now())
    text = (rig.engine.report_dir / (date(*day).isoformat() + '.md')).read_text(encoding='utf-8')
    reports = [body for subject, body in rig.channel.messages
               if '行情可用率：' in body and 'code_sha：' in body]
    assert reports
    return text, reports[-1]


def _market_lines(pair):
    local, phone = pair
    return (next(line for line in local.splitlines() if line.startswith('- market data:')),
            next(line for line in phone.splitlines() if line.startswith('行情可用率：')))


def _assert_scope(line, day, clock):
    assert day in line and clock in line and 'PT' in line, line
    assert '本进程' in line or 'this process' in line, line


def test_B28_daily_local_and_phone_report_disclose_actual_restart_sample_start(tmp_path):
    start = et(15, 9, 57, day=DAY)  # 12:09:57 PT, the second process of the day.
    with _reporting(tmp_path, start=start) as rig:
        # Both STARTUP rows are real durable audit facts. The earlier process's
        # counters are deliberately unavailable to this new Engine instance.
        rig.store.audit(et(9, 32, day=DAY), 'STARTUP', code_sha='synthetic-previous-process')
        rig.engine._heartbeat(start)
        rig.at(start + timedelta(minutes=1))
        rig.engine._heartbeat(rig.clock.now())
        assert rig.engine.quote_availability_stats(REPORT_DAY) == {
            'samples': 2, 'usable': 6, 'total': 6, 'subscribed': 3}
        local, phone = _market_lines(_finish_report(rig))
        for line in (local, phone):
            assert '100%' in line
            _assert_scope(line, '2026-10-20', '12:09')
            assert '当天有过重启' in line
        assert rig.store.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE kind='STARTUP'").fetchone()[0] == 2
        assert rig.broker.sent == [] and rig.broker.cancels == []


def test_B28_single_process_scope_starts_at_first_rth_sample_not_engine_startup(tmp_path):
    with _reporting(tmp_path, start=et(8, day=DAY)) as rig:
        rig.engine._sample_rth_quotes(et(8, day=DAY), {'usable_quotes': 3})
        rig.at(et(10, 5, 30, day=DAY))
        rig.engine._heartbeat(rig.clock.now())
        for line in _market_lines(_finish_report(rig)):
            _assert_scope(line, '2026-10-20', '07:05')
            assert '当天有过重启' not in line
            assert '05:00' not in line


def test_B28_cross_day_samples_reset_count_and_scope_start(tmp_path):
    with _reporting(tmp_path) as rig:
        rig.engine._sample_rth_quotes(et(10, 5, day=DAY), {'usable_quotes': 3})
        next_day = (2026, 10, 21)
        rig.engine._sample_rth_quotes(et(8, day=next_day), {'usable_quotes': 3})
        assert rig.engine.quote_availability_stats(date(*next_day)) == {
            'samples': 0, 'usable': 0, 'total': 0, 'subscribed': 3}
        rig.at(et(11, 12, 20, day=next_day))
        rig.engine._heartbeat(rig.clock.now())
        for line in _market_lines(_finish_report(rig, next_day)):
            _assert_scope(line, '2026-10-21', '08:12')
            assert '2026-10-20 07:05' not in line
        assert rig.engine.quote_availability_stats(date(*next_day)) == {
            'samples': 1, 'usable': 3, 'total': 3, 'subscribed': 3}
        assert rig.engine.quote_availability_stats(REPORT_DAY) == {
            'samples': 0, 'usable': 0, 'total': 0, 'subscribed': 3}


def test_B28_quote_none_is_sampled_as_monitoring_outage_and_keeps_first_scope(tmp_path):
    with _reporting(tmp_path) as rig:
        rig.engine._sample_rth_quotes(et(10, 5, day=DAY), None)
        rig.engine._sample_rth_quotes(et(10, 6, day=DAY), {'usable_quotes': 3})
        assert rig.engine.quote_availability_stats(REPORT_DAY) == {
            'samples': 2, 'usable': 3, 'total': 6, 'subscribed': 3}
        for line in _market_lines(_finish_report(rig)):
            assert '50%' in line
            _assert_scope(line, '2026-10-20', '07:05')


def test_B28_zero_samples_do_not_invent_a_scope_start_or_claim_100_percent(tmp_path):
    with _reporting(tmp_path) as rig:
        for line in _market_lines(_finish_report(rig)):
            assert '100%' not in line
            assert 'n/a' in line or '暂无数据' in line
            assert not re.search(r'2026-10-20\s+\d\d:\d\d', line), line
        assert rig.engine.quote_availability_stats(REPORT_DAY) == {
            'samples': 0, 'usable': 0, 'total': 0, 'subscribed': 3}


def test_B28_direct_daily_report_quotes_none_remains_a_compatible_read_only_call(tmp_path):
    with _reporting(tmp_path) as rig:
        digest = _db_digest(rig.store)
        text = daily_report(rig.store, rig.cfg, REPORT_DAY, quotes=None)
        assert '# V6-Lite daily report 2026-10-20' in text
        assert '- market data:' in text
        assert _db_digest(rig.store) == digest


def _events(text):
    section = text.split('## Events', 1)[1].split('\n## ', 1)[0]
    important = section.split('### Important events', 1)[1]
    event_lines = [line for line in important.splitlines() if re.match(r'- \d\d:\d\d:\d\dZ', line)]
    return section, important, event_lines


def test_B28_important_events_display_chronologically_with_stable_equal_time_order(tmp_path):
    with _reporting(tmp_path) as rig:
        # Clear only this disposable fixture's generated audit, then insert
        # mixed priority classes in deliberately nonchronological ID order.
        rig.store.conn.execute('DELETE FROM audit')
        entries = [
            (12, 5, 'TP_EVIDENCE', 'latest-critical'),
            (12, 1, 'SCAN', 'earliest-ordinary'),
            (12, 3, 'SCAN', 'same-first-id'),
            (12, 3, 'TP_EVIDENCE', 'same-second-id'),
            (12, 2, 'TP_EVIDENCE', 'middle-critical'),
        ]
        for hour, minute, kind, marker in entries:
            rig.store.audit(et(hour, minute, day=DAY), kind, marker=marker, reason=marker)
        digest = _db_digest(rig.store)
        text = daily_report(rig.store, rig.cfg, REPORT_DAY)
        section, important, lines = _events(text)
        expected = ['earliest-ordinary', 'middle-critical', 'same-first-id',
                    'same-second-id', 'latest-critical']
        assert len(lines) == 5
        assert [next(item for item in expected if item in line) for line in lines] == expected
        assert [line[2:10] for line in lines] == sorted(line[2:10] for line in lines)
        assert '另有' not in important and len(section.splitlines()) <= 200
        assert _db_digest(rig.store) == digest


def test_B28_chronology_preserves_priority_selection_budgets_attachment_and_omissions(tmp_path):
    with _reporting(tmp_path) as rig:
        rig.store.conn.execute('DELETE FROM audit')
        initial = et(10, day=DAY)
        for index in range(400):
            rig.store.audit(initial + timedelta(seconds=index), 'SCAN',
                            reason=f'ordinary-{index:04d}-' + 'x' * 150)
        for index, seconds in enumerate((130, 20, 90)):
            rig.store.audit(initial + timedelta(seconds=seconds), 'TP_EVIDENCE',
                            phase='synthetic', reason=f'critical-must-survive-{index}')
        digest = _db_digest(rig.store)
        path, text = write_daily_report(rig.store, rig.cfg, REPORT_DAY, rig.engine.report_dir)
        section, important, lines = _events(text)
        assert len(text.encode('utf-8')) <= 100 * 1024
        assert len(section.splitlines()) <= 200
        assert [line[2:10] for line in lines] == sorted(line[2:10] for line in lines)
        for index in range(3):
            assert f'critical-must-survive-{index}' in important
        [omitted] = re.findall(r'另有 (\d+) 条', important)
        assert len(lines) + int(omitted) == 403
        attachment = path.with_name('2026-10-20.critical.jsonl')
        facts = [json.loads(line) for line in attachment.read_text(encoding='utf-8').splitlines()]
        assert len(facts) == 403 and len({fact['id'] for fact in facts}) == 403
        assert hashlib.sha256(attachment.read_bytes()).hexdigest() in text
        assert _db_digest(rig.store) == digest


def _quiet_observer_tick(rig, monkeypatch):
    """Run real Engine.tick with independent broker paths unable to emit repeats.

    Merely stopping observations is the incident under test. No compressor,
    Store operation, scheduler, tick wrapper or tick implementation is mocked.
    """
    rig.engine.flags.broker_connected = False
    rig.engine._protection_priority_due = False
    for name in ('_drain', '_protection_timers', '_protection_progress',
                 '_maintain_connection', '_heartbeat'):
        monkeypatch.setattr(rig.engine, name, lambda *args, **kwargs: None)


def _seed_repeats(rig, count=4):
    for trade in rig._b27_native.ib.snapshot:
        _observe(rig, trade)
    for _ in range(count):
        rig.advance(seconds=1)
        for pid, trade in zip(rig._b27_pids, rig._b27_native.ib.snapshot):
            _observe(rig, trade)
            rig.store.update_protection(rig.clock.now(), pid, confirmed_at=rig.clock.now())
    assert _count(rig.store, 'OBSERVATION_SUMMARY') == 0


def test_B28_closed_window_is_durable_on_tick_without_new_repeat_or_graceful_shutdown(
        observation_rig, monkeypatch):
    rig = observation_rig(count=2, memory=False)
    _seed_repeats(rig)
    _quiet_observer_tick(rig, monkeypatch)
    deadline = START + timedelta(minutes=30, seconds=1)
    rig.at(deadline - timedelta(microseconds=1))
    rig.tick()
    assert _count(rig.store, 'OBSERVATION_SUMMARY') == 0
    before = _writes(rig)
    rig.at(deadline)
    rig.tick()
    database = rig.cfg.resolved_state_dir() / 'v6lite.sqlite3'
    # Raw connection close models process loss without Store.close/shutdown
    # getting an opportunity to flush process-local duplicate counters.
    rig.store.conn.close()
    rig.store._observations_closed = True
    with sqlite3.connect(database) as reopened:
        payloads = [json.loads(row[0]) for row in reopened.execute(
            "SELECT payload FROM audit WHERE kind='OBSERVATION_SUMMARY' ORDER BY id")]
    counts = Counter()
    for payload in payloads:
        assert payload['reason'] == 'periodic'
        for item in payload['observations']:
            counts[(item['position_id'], item['kind'])] += item['count']
    expected = Counter({(pid, kind): 4 for pid in rig._b27_pids
                        for kind in ('ORDER_PRICE_OBSERVATION', 'PROTECT_STATE')})
    assert counts == expected, (dict(counts), dict(expected))
    assert _writes(rig) == before


def test_B28_candidate_due_flush_is_atomic_across_positions_and_retry_exactly_once(
        observation_rig, record_property):
    """Candidate-only: this controls the new API, not the B28a natural red."""
    record_property('candidate_only', True)
    rig = observation_rig(count=2)
    _seed_repeats(rig)
    due = START + timedelta(minutes=30, seconds=1)
    second = rig._b27_pids[1].replace("'", "''")
    rig.store.conn.execute(
        'CREATE TEMP TRIGGER b28_due_second_abort BEFORE INSERT ON audit '
        "WHEN NEW.kind='OBSERVATION_SUMMARY' "
        "AND json_extract(NEW.payload,'$.position_id')='" + second + "' "
        "BEGIN SELECT RAISE(ABORT,'synthetic due second-position failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match='synthetic due second-position failure'):
        rig.store.flush_due_observations(due)
    assert _count(rig.store, 'OBSERVATION_SUMMARY') == 0
    assert not rig.store.conn.in_transaction
    rig.store.conn.execute('DROP TRIGGER b28_due_second_abort')
    rig.store.flush_due_observations(due)
    counts = Counter()
    for item in _summary_items(rig.store):
        counts[(item['position_id'], item['kind'])] += item['count']
    assert counts == Counter({(pid, kind): 4 for pid in rig._b27_pids
                             for kind in ('ORDER_PRICE_OBSERVATION', 'PROTECT_STATE')})
    before = _count(rig.store, 'OBSERVATION_SUMMARY')
    rig.store.flush_due_observations(due + timedelta(seconds=1))
    assert _count(rig.store, 'OBSERVATION_SUMMARY') == before


def test_B28_candidate_due_flush_respects_outer_rollback_and_unfinished_window(
        observation_rig, record_property):
    """Candidate-only API control: retain counts until the enclosing COMMIT."""
    record_property('candidate_only', True)
    rig = observation_rig()
    _seed_repeats(rig)
    due = START + timedelta(minutes=30, seconds=1)
    rig.store.flush_due_observations(due - timedelta(microseconds=1))
    assert _count(rig.store, 'OBSERVATION_SUMMARY') == 0
    with pytest.raises(RuntimeError, match='synthetic outer rollback'):
        with rig.store.tx():
            rig.store.flush_due_observations(due)
            assert _count(rig.store, 'OBSERVATION_SUMMARY') == 1
            raise RuntimeError('synthetic outer rollback')
    assert _count(rig.store, 'OBSERVATION_SUMMARY') == 0
    rig.store.flush_due_observations(due)
    assert Counter({item['kind']: item['count'] for item in _summary_items(rig.store)}) == {
        'ORDER_PRICE_OBSERVATION': 4, 'PROTECT_STATE': 4}

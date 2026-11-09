"""27a independent time-evidence contracts; offline synthetic SQLite only.

Disclosed facts replayed verbatim: execId 0000e0d5.6ace9d9f.01.01,
ENTRY orderId 500, BUY 3, callback receive time 2026-10-13T19:12:43.329461Z.
The instruction does NOT disclose entry price, permId, conId or orderRef:
those values and generated orderRefs below are explicitly synthetic.
The disclosed positionId is calibrated consistently across all persisted and
FakeBroker references before reboot; this is a historical DB replay fixture.
No private evidence-authority function is replaced or forced successful.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from v6lite.market_calendar import trading_day
from v6lite.models import FillEvent, Purpose, Side, StatusEvent
from v6lite.report import daily_report
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch26_overnight_evidence import (
    _activate, _attempt_tp, _no_network, _overnight_p1, _protection_config,
    _restart, _restore_infos, _safety, _settle, _tp_requests, _writes, seed_overnight,
)

ENTRY_EXEC = '0000e0d5.6ace9d9f.01.01'
RECEIVED = datetime.fromisoformat('2026-10-13T19:12:43.329461+00:00')
MONDAY = (2026, 10, 19)
DISCLOSED_PID = 'pos-99ec110ffc6246e8b7c4'


def _fill_row(rig, exec_id=ENTRY_EXEC):
    return dict(rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (exec_id,)).fetchone())


def _time_key(exec_id=ENTRY_EXEC):
    return 'execution_time_unverified:' + exec_id


def _audits(rig, kind):
    return [json.loads(row['payload']) for row in rig.store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY rowid', (kind,))]


def _economic(row):
    return {key: value for key, value in row.items() if key not in ('time', 'closed_at')}


@pytest.fixture
def replay_history(seed_overnight):
    def make(*, callback=True, enabled=True):
        rig, pid = seed_overnight(enabled=enabled)
        old_pid = pid
        pid = DISCLOSED_PID
        # The whole synthetic historical snapshot is calibrated together;
        # never leave an old position reference in a table, JSON or request.
        for table in ('positions', 'intents', 'protections', 'protection_gap_a',
                      'fills', 'exit_stages', 'releases'):
            rig.store.conn.execute(f'UPDATE {table} SET position_id=? WHERE position_id=?', (pid, old_pid))
        rig.store.conn.execute('UPDATE intents SET recovery_position_id=? WHERE recovery_position_id=?', (pid, old_pid))
        rig.store.conn.execute('UPDATE controls SET key=replace(key,?,?),value=replace(value,?,?)',
                               (old_pid, pid, old_pid, pid))
        rig.store.conn.execute('UPDATE audit SET payload=replace(payload,?,?)', (old_pid, pid))
        for native in rig.broker.orders.values():
            if native.req.position_id == old_pid:
                native.req = replace(native.req, position_id=pid)
        for items in (rig.broker.sent, rig.broker.sent_protections, rig.broker.modified_protections):
            items[:] = [replace(item, position_id=pid) if item.position_id == old_pid else item for item in items]
        rig._b26_entry = replace(rig._b26_entry, position_id=pid)
        rig._b26_original['position_id'] = pid
        assert all(old_pid not in str(tuple(row)) for table in
                   ('positions', 'intents', 'protections', 'fills', 'protection_gap_a', 'controls', 'audit')
                   for row in rig.store.conn.execute(f'SELECT * FROM {table}'))
        old = rig._b26_entry_fill
        # Calibrate a *synthetic persisted historical snapshot*, not a live
        # event and not a claim that unknown broker fields were disclosed.
        # FakeBroker's native permId formula makes 50500 synthetic.
        fill = replace(old, exec_id=ENTRY_EXEC, time=RECEIVED,
                       broker_order_id=500, perm_id=50500,
                       time_source='callback' if callback else 'reqExecutions',
                       time_uncertain=callback)
        rig.store.conn.execute(
            'UPDATE fills SET exec_id=?,exec_base=?,time=?,trading_day=?,broker_order_id=?,perm_id=? '
            'WHERE exec_id=?',
            (fill.exec_id, fill.exec_id.rsplit('.', 2)[0], fill.time.isoformat(),
             trading_day(fill.time).isoformat(), fill.broker_order_id, fill.perm_id, old.exec_id))
        rig.store.conn.execute('UPDATE commissions SET exec_id=? WHERE exec_id=?', (fill.exec_id, old.exec_id))
        rig.store.update_intent(rig.clock.now(), fill.intent_id,
                               broker_order_id=fill.broker_order_id, perm_id=fill.perm_id)
        native = rig.broker.orders[fill.intent_id]
        native.broker_order_id = fill.broker_order_id
        native.fills[:] = [fill if item.exec_id == old.exec_id else item for item in native.fills]
        rig.store.conn.execute('UPDATE protection_gap_a SET exec_id=? WHERE exec_id=?',
                               (fill.exec_id, old.exec_id))
        rig._b26_entry_fill = fill
        if callback:
            # Reconstruct precisely the callback marker schema written by the
            # real Store.record_fill; never call a future helper to grant proof.
            marker = {'provisional_time': fill.time.isoformat(),
                      'identity': [fill.exec_id, fill.intent_id, fill.symbol, str(fill.side),
                                   fill.quantity, fill.price, fill.broker_order_id,
                                   fill.perm_id, fill.con_id]}
            rig.store.set_control(rig.clock.now(), _time_key(), json.dumps(marker))
        rig.at(et(10, 1, day=MONDAY))
        assert rig.store.position(pid)['qty_open'] == 3
        assert rig.broker.executions_today() == []
        assert rig.broker.account_executions_today() == []
        return rig, pid
    return make


@pytest.mark.parametrize('callback', [False, True], ids=['broker-time-verified', 'callback-unverified'])
def test_B27a_1013_history_reboot1019_authorizes_TP_without_rewriting_history(
        replay_history, tmp_path, monkeypatch, callback):
    rig, pid = replay_history(callback=callback)
    before_fill = _fill_row(rig)
    marker = rig.store.control(_time_key())
    before_verified = _audits(rig, 'EXECUTION_TIME_VERIFIED')
    before_writes = _writes(rig)
    _restart(rig, tmp_path, monkeypatch)
    assert _writes(rig) == before_writes and rig.store.position(pid)['qty_open'] == 3
    tp, ref = _activate(rig, pid)  # Real reduce request -> native ACK -> DAY TP, with safety oracle.
    assert tp.quantity == 1 and tp.purpose is Purpose.TP1
    assert rig.broker.orders[ref].remaining == 2 and rig.store.protection(pid)['state'] == 'TP_ACTIVE'
    assert _fill_row(rig) == before_fill and rig.store.control(_time_key()) == marker
    assert _audits(rig, 'EXECUTION_TIME_VERIFIED') == before_verified
    infos = _restore_infos(rig)
    assert len(infos) == 1 and ENTRY_EXEC in infos[0]
    assert '依据运行约束' in infos[0] and 'A' in infos[0] and 'B' in infos[0]
    if callback:
        assert '收到' in infos[0] and '之前' in infos[0] and '未经券商查询核实' in infos[0], infos[0]
        assert '2026-10-19' in infos[0] or '2026-10-18' in infos[0]
    _safety(rig)


@pytest.mark.parametrize('failure', [
    'within-midnight-margin', 'received-today', 'identity-conid-mismatch',
    'provisional-does-not-equal-row-time', 'malformed-control',
], ids=['under-one-hour', 'today', 'identity', 'time-not-equal', 'json'])
def test_B27a_historical_callback_five_invalid_receipts_freeze_TP_keep_three_share_STP(
        replay_history, tmp_path, monkeypatch, failure):
    rig, pid = replay_history()
    marker = json.loads(rig.store.control(_time_key()))
    if failure in {'within-midnight-margin', 'received-today'}:
        when = (et(23, 30, day=(2026, 10, 18)) if failure == 'within-midnight-margin'
                else et(0, 30, day=MONDAY))
        marker['provisional_time'] = when.isoformat()
        rig.store.conn.execute('UPDATE fills SET time=?,trading_day=? WHERE exec_id=?',
                               (when.isoformat(), trading_day(when).isoformat(), ENTRY_EXEC))
    elif failure == 'identity-conid-mismatch':
        marker['identity'][8] += 1
    elif failure == 'provisional-does-not-equal-row-time':
        marker['provisional_time'] = (RECEIVED + timedelta(microseconds=1)).isoformat()
    value = '{not valid JSON' if failure == 'malformed-control' else json.dumps(marker)
    rig.store.set_control(rig.clock.now(), _time_key(), value)
    before_fill, before_writes = _fill_row(rig), _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before_writes
    assert _fill_row(rig) == before_fill and rig.store.control(_time_key()) == value
    assert _restore_infos(rig) == [] and len(_overnight_p1(rig)) == 1
    assert '未执行（a 未通过）' in _overnight_p1(rig)[0], _overnight_p1(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
    assert rig.broker.orders[row['intent_id']].remaining == 3
    _safety(rig)


@pytest.mark.parametrize('identity_index', range(9), ids=[
    'execId', 'intent', 'symbol', 'side', 'quantity', 'price', 'orderId', 'permId', 'conId'])
def test_B27a_every_marker_identity_field_is_bound_to_persisted_evidence(
        replay_history, tmp_path, monkeypatch, identity_index):
    rig, pid = replay_history()
    marker = json.loads(rig.store.control(_time_key()))
    old = marker['identity'][identity_index]
    marker['identity'][identity_index] = old + 1 if isinstance(old, (int, float)) else str(old) + '-conflict'
    rig.store.set_control(rig.clock.now(), _time_key(), json.dumps(marker))
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert _overnight_p1(rig) and _restore_infos(rig) == []
    _safety(rig)


@pytest.mark.parametrize('callback', [False, True], ids=['verified', 'callback'])
@pytest.mark.parametrize('failure', ['unknown-today-fill', 'native-qty-mismatch', 'native-stop-missing'])
def test_B27a_history_time_exception_never_waives_fresh_b_c_d(
        replay_history, tmp_path, monkeypatch, callback, failure):
    rig, pid = replay_history(callback=callback)
    ref = rig.store.protection(pid)['intent_id']
    if failure == 'unknown-today-fill':
        rig._b26_visible_extra.append(FillEvent(
            'b27a-unknown.01.01', '', 'AAPL', Side.BUY, 1, 113.13,
            rig.clock.now(), con_id=rig.store.protection(pid)['con_id']))
    elif failure == 'native-qty-mismatch':
        rig.broker.external_positions['AAPL'] = (-1.0, 113.13)
    else:
        rig.broker.orders[ref].status = 'Cancelled'
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert _restore_infos(rig) == [] and _overnight_p1(rig)


@pytest.fixture
def callback_rig(tmp_path, monkeypatch):
    made = []
    def make(*, stop_fill=False, immediate_query=False):
        rig = Rig(tmp_path / ('callback-' + str(len(made))),
                  start=et(9, 50, day=(2026, 10, 16)),
                  protection=_protection_config(True), risk_per_trade_pct=.0015)
        made.append(rig)
        rig.start(); rig.at(et(10, 0, 30, day=(2026, 10, 16))); rig.tick()
        [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        if stop_fill:
            rig.broker.fill(entry.intent_id, qty=3, price=113.13)
            rig.advance(seconds=1); rig.tick()
            [pos] = rig.store.open_positions(); ref = rig.store.protection(pos['position_id'])['intent_id']
            rig.at(et(14, 55, 44, day=(2026, 10, 16)))
            exec_id = '0000e0d5.6ad25378.01.01'
        else:
            ref = entry.intent_id
            exec_id = 'b27a-callback-entry.01.01'
        original = rig.broker.fill(ref, qty=3, price=113.0 if stop_fill else 113.13,
                                   commission=1.0, exec_id=exec_id, emit=False)
        callback = replace(original, time_source='callback', time_uncertain=True)
        query = replace(original, time=original.time - timedelta(seconds=2),
                        time_source='reqExecutions', time_uncertain=False)
        # The delayed-verification scenarios start with a real complete
        # download whose time is *still uncertain*. It cannot prove the
        # callback time. Once the initial event drain finishes, the broker
        # script exposes its later certain response; no marker is fabricated.
        rig.broker.orders[ref].fills[-1] = (query if immediate_query else
            replace(query, time_uncertain=True))
        # This is an actual event stream: economic truth changes only once.
        rig.broker.events += [callback, StatusEvent(ref, 'Filled', 3, 0,
                               original.broker_order_id, original.perm_id)]
        rig.engine._drain(rig.clock.now())
        if not immediate_query:
            assert rig.store.control(_time_key(exec_id)) is not None
        rig.broker.orders[ref].fills[-1] = query
        return rig, callback, query
    yield make
    for rig in made:
        rig.alerter.close(); rig.store.close()


@pytest.mark.parametrize('stop_fill', [False, True], ids=['ENTRY-callback', '1016-STP-callback'])
def test_B27a_callback_schedules_complete_today_query_and_verifies_known_exec_once(
        callback_rig, monkeypatch, stop_fill):
    rig, callback, query = callback_rig(stop_fill=stop_fill)
    before_fill = _fill_row(rig, callback.exec_id)
    before_pos = dict(rig.store.position(before_fill['position_id']))
    calls = []
    original = rig.broker.executions_today
    def query_today():
        calls.append(rig.clock.now()); return original()
    monkeypatch.setattr(rig.broker, 'executions_today', query_today)
    for _ in range(3):
        rig.advance(seconds=1); rig.tick()
    assert calls, 'callback must actively schedule a complete execution query, including after CLOSED'
    assert rig.store.control(_time_key(callback.exec_id)) is None, 'known execId cannot be filtered before time verification'
    after_fill = _fill_row(rig, callback.exec_id)
    assert after_fill['time'] == query.time.isoformat()
    assert _economic(after_fill) == _economic(before_fill)
    assert _economic(dict(rig.store.position(before_fill['position_id']))) == _economic(before_pos)
    verified = [item for item in _audits(rig, 'EXECUTION_TIME_VERIFIED') if item['exec_id'] == callback.exec_id]
    assert len(verified) == 1 and verified[0]['source'] == 'reqExecutions'
    for _ in range(2):
        rig.advance(seconds=16); rig.tick()
    assert len([item for item in _audits(rig, 'EXECUTION_TIME_VERIFIED')
                if item['exec_id'] == callback.exec_id]) == 1


def test_B27a_completed_protection_query_also_verifies_existing_callback_exec(
        callback_rig):
    rig, callback, query = callback_rig()
    before = _fill_row(rig, callback.exec_id)
    row = rig.store.protection(before['position_id'])
    held, own, others, full = rig.engine._protection_evidence(row)
    assert held == 3 and len(own) == 1 and others == [] and full
    assert rig.store.control(_time_key(callback.exec_id)) is None
    after = _fill_row(rig, callback.exec_id)
    assert after['time'] == query.time.isoformat() and _economic(after) == _economic(before)
    assert any(item['exec_id'] == callback.exec_id for item in _audits(rig, 'EXECUTION_TIME_VERIFIED'))


@pytest.mark.parametrize('field', ['price', 'con_id', 'broker_order_id'])
def test_B27a_complete_query_identity_mismatch_preserves_economics_and_requires_review(
        callback_rig, field):
    rig, callback, query = callback_rig()
    before = _fill_row(rig, callback.exec_id)
    wrong = replace(query, **{field: getattr(query, field) + 1})
    rig.broker.orders[query.intent_id].fills[-1] = wrong
    # The real protection download must forward duplicates into Store's
    # time-only verification rather than granting time proof itself.
    rig.engine._protection_evidence(rig.store.protection(before['position_id']))
    assert _fill_row(rig, callback.exec_id) == before
    assert rig.store.control(_time_key(callback.exec_id)) is not None
    assert not [item for item in _audits(rig, 'EXECUTION_TIME_VERIFIED') if item['exec_id'] == callback.exec_id]
    assert [item for item in _audits(rig, 'EXECUTION_TIME_REVIEW_REQUIRED') if item['exec_id'] == callback.exec_id]


def test_B27a_daily_report_lists_still_unverified_fills_without_rewriting_them(callback_rig):
    rig, callback, _ = callback_rig(stop_fill=True)
    before = _fill_row(rig, callback.exec_id)
    report = daily_report(rig.store, rig.cfg, trading_day(rig.clock.now()))
    assert callback.exec_id in report and ('未核实' in report or 'unverified' in report.lower()), report
    assert _fill_row(rig, callback.exec_id) == before


@pytest.mark.parametrize('offset_us,allowed', [(0, False), (-1, True), (1, False)],
                         ids=['exact-cutoff-freeze', 'one-microsecond-before-allow', 'one-microsecond-after-freeze'])
def test_B27a_historical_receipt_cutoff_is_strict_not_inclusive(
        replay_history, tmp_path, monkeypatch, offset_us, allowed):
    rig, pid = replay_history()
    cutoff = et(23, day=(2026, 10, 18))
    received = cutoff + timedelta(microseconds=offset_us)
    rig.store.conn.execute('UPDATE fills SET time=?,trading_day=? WHERE exec_id=?',
                           (received.isoformat(), trading_day(received).isoformat(), ENTRY_EXEC))
    marker = json.loads(rig.store.control(_time_key()))
    marker['provisional_time'] = received.isoformat()
    rig.store.set_control(rig.clock.now(), _time_key(), json.dumps(marker))
    before = _fill_row(rig), rig.store.control(_time_key())
    _restart(rig, tmp_path, monkeypatch)
    if allowed:
        _activate(rig, pid)
        assert _restore_infos(rig) and _overnight_p1(rig) == []
    else:
        _attempt_tp(rig, pid)
        assert _tp_requests(rig) == [] and _overnight_p1(rig)
    assert (_fill_row(rig), rig.store.control(_time_key())) == before
    _safety(rig)


@pytest.mark.parametrize('shape', ['null', 'array', 'string', 'number', 'identity-null',
                                   'identity-short', 'identity-long', 'bool-quantity',
                                   'nan-price', 'infinite-price', 'naive-time'])
def test_B27a_historical_malformed_but_JSON_controls_fail_closed_without_crashing(
        replay_history, tmp_path, monkeypatch, shape):
    rig, pid = replay_history()
    marker = json.loads(rig.store.control(_time_key()))
    if shape == 'null': marker = None
    elif shape == 'array': marker = []
    elif shape == 'string': marker = 'synthetic wrong shape'
    elif shape == 'number': marker = 42
    elif shape == 'identity-null': marker['identity'] = None
    elif shape == 'identity-short': marker['identity'] = marker['identity'][:-1]
    elif shape == 'identity-long': marker['identity'].append('unbound')
    elif shape == 'bool-quantity': marker['identity'][4] = True
    elif shape == 'nan-price': marker['identity'][5] = float('nan')
    elif shape == 'infinite-price': marker['identity'][5] = float('inf')
    else: marker['provisional_time'] = RECEIVED.replace(tzinfo=None).isoformat()
    value = json.dumps(marker)
    rig.store.set_control(rig.clock.now(), _time_key(), value)
    before = _fill_row(rig), _writes(rig)
    _restart(rig, tmp_path, monkeypatch); _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _overnight_p1(rig) and _restore_infos(rig) == []
    assert _fill_row(rig) == before[0] and _writes(rig) == before[1]
    assert rig.store.control(_time_key()) == value
    _safety(rig)


@pytest.mark.parametrize('corruption', ['missing-pin', 'pin-conflict', 'row-conid-missing'])
def test_B27a_historical_conId_must_have_persisted_independent_pin_and_protection_binding(
        replay_history, tmp_path, monkeypatch, corruption):
    rig, pid = replay_history()
    # seed_overnight closes/reopens its Store; bind a new Engine before
    # exercising this helper, rather than using its retired closed handle.
    _restart(rig, tmp_path, monkeypatch)
    row = rig.store.protection(pid)
    assert int(rig.store.control('conid:AAPL')) == row['con_id']
    if corruption == 'missing-pin':
        rig.store.delete_control('conid:AAPL')
    elif corruption == 'pin-conflict':
        rig.store.set_control(rig.clock.now(), 'conid:AAPL', str(row['con_id'] + 1))
    else:
        rig.store.update_protection(rig.clock.now(), pid, con_id=None)
    before = _fill_row(rig), rig.store.control(_time_key())
    with pytest.raises(ValueError):
        rig.engine._overnight_history(rig.store.protection(pid), rig.clock.now())
    writes = _writes(rig)
    _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == writes
    assert _overnight_p1(rig)
    assert (_fill_row(rig), rig.store.control(_time_key())) == before


@pytest.mark.parametrize('zone', ['UTC', '+09:00', 'America/New_York'])
def test_B27a_historical_receipt_aware_offsets_use_instants_and_preserve_original_strings(
        replay_history, tmp_path, monkeypatch, zone):
    from zoneinfo import ZoneInfo
    rig, pid = replay_history()
    tz = timezone(timedelta(hours=9)) if zone == '+09:00' else ZoneInfo(zone)
    received = RECEIVED.astimezone(tz)
    marker = json.loads(rig.store.control(_time_key()))
    marker['provisional_time'] = received.isoformat()
    rig.store.conn.execute('UPDATE fills SET time=? WHERE exec_id=?', (received.isoformat(), ENTRY_EXEC))
    rig.store.set_control(rig.clock.now(), _time_key(), json.dumps(marker))
    before = _fill_row(rig), rig.store.control(_time_key())
    _restart(rig, tmp_path, monkeypatch); _activate(rig, pid)
    assert (_fill_row(rig), rig.store.control(_time_key())) == before
    _safety(rig)


@pytest.mark.parametrize('kind', ['failed', 'empty', 'still-uncertain'])
@pytest.mark.parametrize('stop_fill', [False, True], ids=['open', 'closed'])
def test_B27a_unverified_callback_requires_an_actual_matching_complete_query_before_clearing(
        callback_rig, monkeypatch, kind, stop_fill):
    from v6lite.broker.base import BrokerError
    rig, callback, query = callback_rig(stop_fill=stop_fill)
    before = _fill_row(rig, callback.exec_id)
    calls = []
    def query_today():
        calls.append(rig.clock.now())
        if kind == 'failed': raise BrokerError('synthetic complete execution request failed')
        if kind == 'empty': return []
        return [replace(query, time_source='callback', time_uncertain=True)]
    monkeypatch.setattr(rig.broker, 'executions_today', query_today)
    for _ in range(3):
        rig.advance(seconds=1); rig.tick()
    assert calls, 'closed positions also need a limited active retry while callback timestamps are pending'
    assert rig.store.control(_time_key(callback.exec_id)) is not None
    assert _fill_row(rig, callback.exec_id) == before
    assert not [item for item in _audits(rig, 'EXECUTION_TIME_VERIFIED') if item['exec_id'] == callback.exec_id]


def test_B27a_history_receive_time_basis_is_explicit_in_daily_report(replay_history, tmp_path, monkeypatch):
    rig, pid = replay_history()
    before = _fill_row(rig), rig.store.control(_time_key())
    _restart(rig, tmp_path, monkeypatch)
    report = daily_report(rig.store, rig.cfg, trading_day(rig.clock.now()))
    assert ENTRY_EXEC in report and '收到' in report and '之前' in report and '未经券商查询核实' in report, report
    assert (_fill_row(rig), rig.store.control(_time_key())) == before



def test_B27a_callback_can_be_verified_by_first_natural_protection_query_without_extra_tick(callback_rig):
    # Do not hold back the broker's correct complete response. Production may
    # safely verify during the same natural drain; an unnecessary timer/query
    # round must not become a prerequisite for accepting already-valid proof.
    rig, callback, query = callback_rig(immediate_query=True)
    row = _fill_row(rig, callback.exec_id)
    assert rig.store.control(_time_key(callback.exec_id)) is None
    assert row['time'] == query.time.isoformat()
    assert row['quantity'] == callback.quantity == 3 and row['price'] == callback.price
    assert rig.store.position(row['position_id'])['qty_open'] == 3
    verified = [item for item in _audits(rig, 'EXECUTION_TIME_VERIFIED')
                if item['exec_id'] == callback.exec_id]
    assert len(verified) == 1 and verified[0]['source'] == 'reqExecutions'

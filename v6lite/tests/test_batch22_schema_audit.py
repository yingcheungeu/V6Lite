"""Batch 22: independent schema, legacy refusal and execution-audit contracts.

All databases and reports are pytest TEMP paths; every broker is FakeBroker.
The old schema-4 fixture is deliberately literal B20 DDL, not an invented
new constructor. Baseline failures must be behavior/field assertions. This
file owns no production interfaces or implementation helpers.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from functools import partial

import pytest

import v6lite.store as store_module
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine, StartupError
from v6lite.exits import compute_levels
from v6lite.models import IntentStatus, OrderRequest, Purpose, Side
from v6lite.report import daily_report
from v6lite.store import Store, StoreError
from v6lite.tests.helpers import Rig, et


# Literal pre-STP schema 4 definitions: preserving historical prices and
# native identity is part of the contract, including explicit custom indexes.
_OLD_INTENTS = """
CREATE TABLE intents (
 intent_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK (quantity > 0),
 limit_price REAL NOT NULL CHECK (limit_price > 0),
 purpose TEXT NOT NULL, position_id TEXT, atr REAL, status TEXT NOT NULL,
 broker_order_id INTEGER, perm_id INTEGER, last_raw_status TEXT,
 created_at TEXT NOT NULL, sent_at TEXT, cancel_requested_at TEXT,
 cancel_seen_at TEXT, updated_at TEXT NOT NULL, note TEXT, hold_reason TEXT,
 recovery_position_id TEXT
);
CREATE INDEX intents_status ON intents(status);
CREATE INDEX b22_legacy_intent_price ON intents(symbol, limit_price, quantity);
"""
_OLD_PROTECTIONS = """
CREATE TABLE protections (
 position_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL UNIQUE,
 symbol TEXT NOT NULL, account TEXT, client_id INTEGER, con_id INTEGER,
 trigger_price REAL NOT NULL, limit_price REAL NOT NULL,
 requested_qty INTEGER NOT NULL DEFAULT 0,
 confirmed_remaining INTEGER NOT NULL DEFAULT 0,
 pending_max_remaining INTEGER NOT NULL DEFAULT 0,
 pending_qty INTEGER, pending_trigger REAL, pending_limit REAL,
 modify_generation INTEGER NOT NULL DEFAULT 0,
 status_seen_generation INTEGER NOT NULL DEFAULT -1,
 state TEXT NOT NULL, broker_order_id INTEGER, perm_id INTEGER,
 confirmed_at TEXT, gap_a_started_at TEXT, gap_b_started_at TEXT,
 tp_stage TEXT, tp_qty INTEGER, tp_intent_id TEXT, updated_at TEXT NOT NULL
);
CREATE INDEX protections_intent ON protections(intent_id);
CREATE INDEX b22_legacy_protect_identity ON protections(con_id, broker_order_id, perm_id);
"""


def _snapshot(conn):
    conn.row_factory = sqlite3.Row
    tables = ('intents', 'protections', 'positions', 'fills', 'controls')
    rows = {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
            for table in tables}
    schemas = dict(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN ('intents','protections')"))
    indexes = dict(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
        "AND tbl_name IN ('intents','protections','positions') ORDER BY name"))
    return {'rows': rows, 'schemas': schemas, 'indexes': indexes}


def _legacy_database(path, *, protected=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Unchanged auxiliary tables use the existing schema. Both subject tables
    # are then replaced with their exact pre-STP definitions while still empty.
    conn.executescript(store_module._SCHEMA)
    conn.executescript('DROP TABLE intents; DROP TABLE protections;')
    conn.executescript(_OLD_INTENTS + _OLD_PROTECTIONS)
    now = et(9, 40).isoformat()
    conn.executemany('INSERT INTO meta(key,value) VALUES(?,?)', [
        ('schema_version', '4'), ('account', 'DU0000000'), ('mode', 'paper'),
        ('role', 'formal'), ('client_id', '131'), ('b22_keep_meta', 'opaque legacy value')])
    conn.execute(
        "INSERT INTO intents(intent_id,symbol,side,quantity,limit_price,purpose,position_id,atr,status,"
        "broker_order_id,perm_id,last_raw_status,created_at,sent_at,updated_at,note) "
        "VALUES('v6l-b22legacyentry','AAPL','BUY',20,113.47,'ENTRY',?,2,'FILLED',1001,51001,"
        "'Filled',?,?,?,'entry note must survive')",
        ('pos-b22legacy' if protected else None, now, now, now))
    conn.execute("INSERT INTO controls VALUES('b22_keep_control','opaque control',?)", (now,))
    conn.execute("INSERT INTO audit(ts,kind,payload) VALUES(?,'B22_LEGACY_SENTINEL','{\"keep\":true}')",
                 (now,))
    if protected:
        conn.execute(
            "INSERT INTO positions(position_id,symbol,status,source,entry_price,atr,qty_initial,qty_open,"
            "stop,tp1,tp2,tp3,realized_pnl,opened_at) "
            "VALUES('pos-b22legacy','AAPL','OPEN','strategy',113.47,2,20,20,108.47,119.47,123.47,129.47,0,?)",
            (now,))
        conn.execute(
            "INSERT INTO fills(exec_id,intent_id,position_id,symbol,side,quantity,price,time,trading_day,"
            "broker_order_id,perm_id,exec_base) VALUES('0000fa4e.00000b22.01.01',"
            "'v6l-b22legacyentry','pos-b22legacy','AAPL','BUY',20,113.47,?,'2026-10-06',1001,51001,"
            "'0000fa4e.00000b22')", (now,))
        conn.execute(
            "INSERT INTO intents(intent_id,symbol,side,quantity,limit_price,purpose,position_id,status,"
            "broker_order_id,perm_id,last_raw_status,created_at,sent_at,updated_at,note) "
            "VALUES('v6l-b22legacyprotect','AAPL','SELL',20,105.21,'PROTECT','pos-b22legacy',"
            "'SENT',1002,51002,'Submitted',?,?,?,'protection note must survive')", (now, now, now))
        conn.execute(
            "INSERT INTO protections(position_id,intent_id,symbol,account,client_id,con_id,trigger_price,"
            "limit_price,requested_qty,confirmed_remaining,pending_max_remaining,modify_generation,"
            "status_seen_generation,state,broker_order_id,perm_id,confirmed_at,updated_at) "
            "VALUES('pos-b22legacy','v6l-b22legacyprotect','AAPL','DU0000000',131,10000,108.47,105.21,"
            "20,20,20,3,3,'WORKING',1002,51002,?,?)", (now, now))
    conn.commit()
    before = _snapshot(conn)
    conn.close()
    return before


def _assert_preserved(store, before):
    for table, old_rows in before['rows'].items():
        current = [dict(row) for row in store.conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
        assert len(current) == len(old_rows), (table, current, old_rows)
        for old, new in zip(old_rows, current):
            assert {key: new[key] for key in old} == old, (table, old, new)
    current_indexes = dict(store.conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
        "AND tbl_name IN ('intents','protections','positions') ORDER BY name"))
    assert current_indexes == before['indexes']
    assert store.meta('b22_keep_meta') == 'opaque legacy value'
    assert store.conn.execute("SELECT payload FROM audit WHERE kind='B22_LEGACY_SENTINEL'").fetchone()[0] == '{"keep":true}'


def _rig(tmp_path):
    return Rig(tmp_path, protection=ProtectionConfig(enabled=True))


def _legacy_rig(tmp_path):
    rig = _rig(tmp_path)
    rig.store.close()
    path = tmp_path / 'legacy-state' / 'v6lite.sqlite3'
    before = _legacy_database(path, protected=True)
    rig.store = Store(path, partial(compute_levels, rig.cfg))
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / 'legacy-reports')
    rig.broker.con_ids['AAPL'] = 10000
    return rig, before


def _writes(rig):
    return tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                        rig.broker.modified_protections, rig.broker.cancels))


def _open(rig):
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    assert pos['qty_open'] == 20
    row = dict(rig.store.protection(pos['position_id']))
    assert row['state'] == 'WORKING'
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 20
    return dict(pos), row


def _settle(rig):
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()


def _retired_pair(tmp_path):
    rig = _rig(tmp_path)
    pos, old = _open(rig)
    rig.engine._protection_cancel(rig.clock.now(), old, 'b22_retired_audit_fixture')
    rig.broker.confirm_cancel(old['intent_id'])
    _settle(rig)
    for _ in range(4):
        rig.advance(seconds=1)
        rig.tick()
        _settle(rig)
        if rig.store.protection(pos['position_id'])['intent_id'] != old['intent_id']:
            break
    new = dict(rig.store.protection(pos['position_id']))
    assert new['state'] == 'WORKING'
    assert new['intent_id'] != old['intent_id']
    assert new['broker_order_id'] != old['broker_order_id']
    assert new['perm_id'] != old['perm_id']
    assert new['con_id'] == old['con_id']
    assert new['confirmed_remaining'] == new['pending_max_remaining'] == 20
    assert rig.store.intent(old['intent_id'])['status'] == IntentStatus.CANCELLED
    assert len(rig.broker.sent_protections) == 2
    return rig, pos, old, new


def _audit(store, kind, exec_id=None):
    result = [json.loads(row['payload']) for row in store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY id', (kind,))]
    return result if exec_id is None else [row for row in result if row.get('exec_id') == exec_id]


def _slippage_audit(rig, fill, stop, *, avg=None, retired=False):
    rows = _audit(rig.store, 'PROTECT_FILL', fill.exec_id)
    assert len(rows) == 1, (fill.exec_id, rows)
    row = rows[0]
    required = {'stop_price', 'fill_price', 'avg_fill_price', 'quantity', 'slippage_usd', 'bps'}
    assert required <= row.keys(), row
    assert row['stop_price'] == pytest.approx(stop)
    assert row['fill_price'] == pytest.approx(fill.price)
    assert row['avg_fill_price'] == pytest.approx(fill.price if avg is None else avg)
    assert row['quantity'] == fill.quantity
    assert row['slippage_usd'] == pytest.approx((stop - fill.price) * fill.quantity)
    assert row['bps'] == pytest.approx((stop - fill.price) / stop * 10000)
    if retired:
        assert row.get('retired') is True
        assert len(_audit(rig.store, 'PROTECT_RETIRED_FILL', fill.exec_id)) == 1
    return row


def _change_stop(rig, row, stop, *, auto_status=True):
    rig.broker.protection_auto_status = auto_status
    [order] = [order for order in rig.broker.open_orders() if order.intent_id == row['intent_id']]
    assert rig.engine._protection_modify(rig.clock.now(), row, order,
        int(row['confirmed_remaining']), increase=False, state='PRICE_PENDING', trigger=stop)
    pending = rig.store.protection(row['position_id'])
    assert pending['state'] == 'PRICE_PENDING'
    assert pending['pending_trigger'] == pytest.approx(stop)
    assert pending['trigger_price'] == pytest.approx(row['trigger_price'])
    return dict(pending)


def test_B22_old_schema4_empty_protection_database_remains_compatible(tmp_path):
    path = tmp_path / 'old-empty' / 'state.sqlite3'
    before = _legacy_database(path)
    store = Store(path, partial(compute_levels, _rig(tmp_path / 'cfg').cfg))
    try:
        assert store.meta('schema_version') == store_module.SCHEMA_VERSION == '4'
        columns = {row['name']: row for row in store.conn.execute('PRAGMA table_info(protections)')}
        assert 'order_type' in columns, columns
        for table in ('intents', 'protections'):
            columns = {row['name']: row for row in store.conn.execute(f'PRAGMA table_info({table})')}
            assert columns['limit_price']['notnull'] == 0, (table, columns['limit_price'])
        assert store.protections() == []
        _assert_preserved(store, before)
    finally:
        store.close()


def test_B22_old_schema4_rows_gain_only_legacy_type_metadata_and_keep_indexes(tmp_path):
    path = tmp_path / 'old-populated' / 'state.sqlite3'
    before = _legacy_database(path, protected=True)
    cfg = _rig(tmp_path / 'cfg').cfg
    store = Store(path, partial(compute_levels, cfg))
    try:
        row = dict(store.protection('pos-b22legacy'))
        assert row.get('order_type') == 'STP LMT', row
        _assert_preserved(store, before)
        assert store.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        # Original uniqueness and quantity CHECK remain effective after rebuild.
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                "INSERT INTO intents(intent_id,symbol,side,quantity,limit_price,purpose,status,created_at,updated_at) "
                "VALUES('b22-bad-qty','SPY','BUY',0,1,'ENTRY','NEW','x','x')")
        old = before['rows']['protections'][0]
        duplicate = {**old, 'position_id': 'different-position'}
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                f"INSERT INTO protections({','.join(duplicate)}) VALUES({','.join('?' for _ in duplicate)})",
                tuple(duplicate.values()))
    finally:
        store.close()
    reopened = Store(path, partial(compute_levels, cfg))
    try:
        assert dict(reopened.protection('pos-b22legacy')).get('order_type') == 'STP LMT'
        _assert_preserved(reopened, before)
    finally:
        reopened.close()


def test_B22_schema_upgrade_rolls_back_both_tables_when_protection_ddl_fails(tmp_path, monkeypatch):
    path = tmp_path / 'atomic-failure' / 'state.sqlite3'
    before = _legacy_database(path, protected=True)
    cfg = _rig(tmp_path / 'cfg').cfg
    real_connect = sqlite3.connect
    denied = []

    def failing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        def authorize(action, arg1, arg2, database, origin):
            if (action in {sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}
                    and 'protections' in (arg1, arg2)):
                denied.append((action, arg1, arg2))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(store_module.sqlite3, 'connect', failing_connect)
    with pytest.raises((sqlite3.DatabaseError, StoreError)):
        Store(path, partial(compute_levels, cfg))
    assert denied, 'fixture must deny real protection migration DDL'
    conn = real_connect(path)
    try:
        after = _snapshot(conn)
        assert after == before, (before, after)
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '4'
    finally:
        conn.close()


def test_B22_legacy_protection_startup_pages_p0_and_makes_zero_broker_writes(tmp_path):
    rig, before = _legacy_rig(tmp_path)
    failure = ''
    try:
        rig.engine.startup()
    except (StartupError, StoreError) as exc:
        failure = str(exc)
    messages = rig.messages() + '\n' + failure
    assert any('P0' in subject for subject, body in rig.channel.messages), messages
    assert '旧类型保护单' in messages and '人工' in messages, messages
    assert _writes(rig) == (0, 0, 0, 0)
    for table in ('protections', 'intents', 'positions', 'fills'):
        rows = [dict(row) for row in rig.store.conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
        assert len(rows) == len(before['rows'][table])
        for old, row in zip(before['rows'][table], rows):
            assert {key: row[key] for key in old} == old, (table, old, row)
    assert dict(rig.store.protection('pos-b22legacy')).get('order_type') == 'STP LMT'


def test_B22_legacy_type_release_is_refused_even_with_complete_flat_proof(tmp_path):
    rig, _ = _legacy_rig(tmp_path)
    now = rig.clock.now()
    rig.store.update_intent(now, 'v6l-b22legacyprotect', status=IntentStatus.CANCEL_SEEN,
        last_raw_status='Cancelled', cancel_seen_at=(now - timedelta(seconds=61)).isoformat())
    rig.store.set_control(now, 'frozen:AAPL', 'synthetic legacy review')
    rig.broker.connect()
    rig.broker.external_positions['AAPL'] = (20, 113.47)
    request = OrderRequest('tws-b22-legacy-flat', 'AAPL', Side.SELL, 20, 107.0, Purpose.STOP)
    rig.broker._register(request)
    rig.broker.fill(request.intent_id, price=107.0, emit=False)
    rig.advance(seconds=61)
    assert rig.broker.open_orders() == []
    assert rig.broker.positions_fresh() == []
    assert rig.broker.positions() == []
    assert any(fill.side == Side.SELL for fill in rig.broker.account_executions_today())
    writes = _writes(rig)
    rig.store.set_control(rig.clock.now(), 'release:AAPL', json.dumps({
        'operator': 'synthetic-operator', 'reason': 'complete flat proof must not convert old order type'}))
    rig.engine._apply_releases(rig.clock.now())
    position = rig.store.position('pos-b22legacy')
    assert position['status'] == 'OPEN' and position['qty_open'] == 20, dict(position)
    assert rig.store.release_times() == []
    refusals = _audit(rig.store, 'RELEASE_REFUSED')
    assert len(refusals) == 1, refusals
    assert '旧类型' in refusals[0]['reason'] or 'STP LMT' in refusals[0]['reason'], refusals
    assert _writes(rig) == writes


@pytest.mark.parametrize('purpose', [Purpose.ENTRY, Purpose.TP1, Purpose.STOP])
@pytest.mark.parametrize('bad_limit', [None, 0.0, -1.0], ids=['NULL', 'zero', 'negative'])
def test_B22_nonprotect_sql_still_requires_a_positive_nonnull_limit(tmp_path, purpose, bad_limit):
    # Coverage that was already green on B20: nullable PROTECT must not weaken
    # SQL constraints on any ordinary BUY or SELL intent.
    rig = _rig(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        rig.store.conn.execute(
            "INSERT INTO intents(intent_id,symbol,side,quantity,limit_price,purpose,status,created_at,updated_at) "
            "VALUES('v6l-b22-invalid','AAPL','SELL',1,?,?,'NEW',?,?)",
            (bad_limit, str(purpose), rig.clock.now().isoformat(), rig.clock.now().isoformat()))
    assert rig.store.intent('v6l-b22-invalid') is None


@pytest.mark.parametrize('bad_limit', [0.0, -1.0])
def test_B22_create_intent_explicitly_refuses_nonprotect_invalid_limit(tmp_path, bad_limit):
    rig = _rig(tmp_path)
    request = OrderRequest('v6l-b22-store-refusal', 'AAPL', Side.SELL, 1, bad_limit, Purpose.TP1)
    with pytest.raises(StoreError):
        rig.store.create_intent(rig.clock.now(), request, atr=None, count_entry_day=None)
    assert rig.store.intent(request.intent_id) is None
    assert _writes(rig) == (0, 0, 0, 0)


def test_B22_new_protection_plan_uses_stp_metadata_and_null_limit_before_entry_fill(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    [entry] = [request for request in rig.broker.sent if request.purpose == Purpose.ENTRY]
    row = dict(rig.store.protection(entry.position_id))
    assert row['state'] == 'PLANNED'
    assert row.get('order_type') == 'STP', row
    assert row['limit_price'] is None, row
    assert rig.store.intent(entry.intent_id)['limit_price'] > 0
    assert not rig.broker.sent_protections


def test_B22_new_stp_intent_has_null_limit_and_native_order_has_no_limit(tmp_path):
    rig = _rig(tmp_path)
    _, row = _open(rig)
    intent = rig.store.intent(row['intent_id'])
    assert row.get('order_type') == 'STP', row
    assert row['limit_price'] is None and intent['limit_price'] is None, (row, dict(intent))
    [native] = [order for order in rig.broker.open_orders() if order.intent_id == row['intent_id']]
    assert native.order_type == 'STP' and native.tif == 'GTC'
    assert native.aux_price == pytest.approx(row['trigger_price'])
    assert native.limit_price is None and native.outside_rth is False
    assert native.broker_order_id == row['broker_order_id'] and native.perm_id == row['perm_id']
    assert native.con_id == row['con_id']
    assert all(order.order_type == 'LMT' for order in rig.broker.open_orders()
               if order.intent_id != row['intent_id'])
    assert all(request.limit_price > 0 for request in rig.broker.sent)


@pytest.mark.parametrize('quantity,offset', [(5, -0.50), (20, 0.25)], ids=['partial-adverse', 'full-favorable'])
def test_B22_each_native_protect_exec_has_exact_signed_slippage_and_replay_is_idempotent(tmp_path, quantity, offset):
    rig = _rig(tmp_path)
    _, row = _open(rig)
    stop = row['trigger_price']
    fill = rig.broker.fill(row['intent_id'], qty=quantity, price=stop + offset, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    payload = _slippage_audit(rig, fill, stop)
    writes = _writes(rig)
    count = rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
    position = dict(rig.store.position(row['position_id']))
    for _ in range(3):
        rig.engine._ingest_fill(rig.clock.now(), fill)
    assert _audit(rig.store, 'PROTECT_FILL', fill.exec_id) == [payload]
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == count
    assert dict(rig.store.position(row['position_id'])) == position
    assert _writes(rig) == writes


def test_B22_protect_average_is_per_intent_weighted_actual_execution_price(tmp_path):
    rig = _rig(tmp_path)
    _, row = _open(rig)
    stop = row['trigger_price']
    first = rig.broker.fill(row['intent_id'], qty=3, price=stop - 0.50, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), first)
    _slippage_audit(rig, first, stop)
    second = rig.broker.fill(row['intent_id'], qty=7, price=stop - 1.00, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), second)
    _slippage_audit(rig, second, stop, avg=(first.price * 3 + second.price * 7) / 10)
    assert rig.store.position(row['position_id'])['qty_open'] == 10
    assert rig.store.intent_filled_qty(row['intent_id']) == 10


def test_B22_retired_identity_persists_order_type_and_original_stop_across_reopen(tmp_path):
    rig, _, old, new = _retired_pair(tmp_path)
    key = 'retired_protection:' + old['intent_id']
    raw = rig.store.control(key)
    assert raw is not None
    identity = json.loads(raw)
    assert identity.get('order_type') == 'STP', identity
    assert identity.get('trigger_price') == pytest.approx(old['trigger_price']), identity
    for field in ('position_id', 'intent_id', 'symbol', 'account', 'client_id', 'con_id', 'broker_order_id', 'perm_id'):
        assert identity[field] == old[field]
    assert identity['quantity'] == 20
    path = rig.store.path
    rig.store.close()
    rig.store = Store(path, partial(compute_levels, rig.cfg))
    assert rig.store.control(key) == raw
    assert rig.store.protection(old['position_id'])['intent_id'] == new['intent_id']
    assert rig.store.intent(old['intent_id'])['broker_order_id'] == old['broker_order_id']


def test_B22_retired_fill_uses_immutable_old_stop_not_replacement_price(tmp_path):
    rig, pos, old, new = _retired_pair(tmp_path)
    replacement_stop = old['trigger_price'] + 2.0
    _change_stop(rig, new, replacement_stop)
    _settle(rig)
    current = rig.store.protection(pos['position_id'])
    assert current['state'] == 'WORKING' and current['pending_trigger'] is None
    assert current['trigger_price'] == pytest.approx(replacement_stop)
    fill = rig.broker.fill(old['intent_id'], qty=5, price=old['trigger_price'] - 0.50, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    _slippage_audit(rig, fill, old['trigger_price'], retired=True)
    assert rig.store.intent_filled_qty(old['intent_id']) == 5
    assert rig.store.intent_filled_qty(new['intent_id']) == 0
    _settle(rig)
    current = rig.store.protection(pos['position_id'])
    assert current['intent_id'] == new['intent_id'] and current['state'] == 'WORKING'
    assert current['confirmed_remaining'] == current['pending_max_remaining'] == 15
    sells = [order for order in rig.broker.open_orders() if order.symbol == 'AAPL' and order.side in ('SELL', '')]
    assert sum(order.remaining for order in sells) == 15
    assert sum(order.remaining for order in sells) <= next(
        position.quantity for position in rig.broker.positions_fresh() if position.symbol == 'AAPL')
    assert all(request.quantity <= 15 for request in rig.broker.modified_protections[1:])


def test_B22_price_pending_execution_audit_keeps_stop_and_slippage_unknown(tmp_path):
    rig = _rig(tmp_path)
    _, old = _open(rig)
    pending = _change_stop(rig, old, old['trigger_price'] + 1.0, auto_status=False)
    assert pending['trigger_price'] != pending['pending_trigger']
    assert rig.broker.orders[old['intent_id']].req.trigger_price == old['trigger_price']
    fill = rig.broker.fill(old['intent_id'], qty=5, price=old['trigger_price'] - 0.50, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    rows = _audit(rig.store, 'PROTECT_FILL', fill.exec_id)
    assert len(rows) == 1
    payload = rows[0]
    for field in ('stop_price', 'slippage_usd', 'bps'):
        assert field in payload and payload[field] is None, payload
    assert payload.get('slippage_status') in {'unknown', 'candidates'}, payload
    assert payload.get('fill_price') == pytest.approx(fill.price)
    assert payload.get('avg_fill_price') == pytest.approx(fill.price)
    assert payload.get('quantity') == 5
    # A known current order must keep the actual capacity evidence, even when
    # its trigger attribution is ambiguous; NULL is not permission to guess.
    row = rig.store.protection(old['position_id'])
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 15
    assert row['pending_trigger'] == pytest.approx(old['trigger_price'] + 1.0)


def test_B22_delayed_reqexecutions_slippage_report_uses_execution_trading_day(tmp_path):
    rig = _rig(tmp_path)
    pos, row = _open(rig)
    rig.at(et(12))
    fill = rig.broker.fill(row['intent_id'], qty=20, price=row['trigger_price'] - 0.50, emit=False)
    fill = replace(fill, time_source='reqExecutions', time_uncertain=False)
    rig.broker.orders[row['intent_id']].fills[-1] = fill
    rig.at(et(10, day=(2026, 10, 7)))
    rig.engine._ingest_fill(rig.clock.now(), fill)
    recorded = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (fill.exec_id,)).fetchone()
    assert recorded['trading_day'] == '2026-10-06'
    assert rig.store.position(pos['position_id'])['status'] == 'CLOSED'
    events = rig.store.conn.execute("SELECT ts,payload FROM audit WHERE kind='PROTECT_FILL'").fetchall()
    assert len(events) == 1 and events[0]['ts'].startswith('2026-10-07')
    previous = daily_report(rig.store, rig.cfg, date(2026, 10, 6))
    current = daily_report(rig.store, rig.cfg, date(2026, 10, 7))
    assert fill.exec_id in previous, previous
    line = next(line for line in previous.splitlines() if fill.exec_id in line)
    assert f'{(row["trigger_price"] - fill.price) * 20:.2f}' in line, line
    assert 'bps' in line.lower(), line
    assert fill.exec_id not in current, current


def test_B22_after_closed_real_retired_exec_is_audited_without_second_pnl_or_close(tmp_path):
    rig, pos, old, new = _retired_pair(tmp_path)
    first = rig.broker.fill(old['intent_id'], qty=5, price=old['trigger_price'] - 0.50, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), first)
    _settle(rig)
    row = rig.store.protection(pos['position_id'])
    assert row['state'] == 'WORKING'
    assert row['confirmed_remaining'] == row['pending_max_remaining'] == 15
    final = rig.broker.fill(new['intent_id'], qty=15, price=new['trigger_price'] - 0.25, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), final)
    closed = dict(rig.store.position(pos['position_id']))
    assert closed['status'] == 'CLOSED' and closed['qty_open'] == 0
    assert rig.broker.positions_fresh() == []
    closes = _audit(rig.store, 'TRADE_CLOSED')
    assert len([row for row in closes if row.get('position_id') == pos['position_id']]) == 1
    # The retired order still has native 15-share capacity. This second 5-share
    # execution is genuine broker truth, producing a short, not a fabricated
    # callback. It must retain audit evidence without applying another trade.
    second = rig.broker.fill(old['intent_id'], qty=5, price=old['trigger_price'] - 1.50, emit=False)
    assert next(position.quantity for position in rig.broker.positions_fresh() if position.symbol == 'AAPL') == -5
    writes = _writes(rig)
    rig.engine._ingest_fill(rig.clock.now(), second)
    payload = _slippage_audit(rig, second, old['trigger_price'],
        avg=(first.price * 5 + second.price * 5) / 10, retired=True)
    after = rig.store.position(pos['position_id'])
    assert dict(after) == closed
    assert rig.store.intent_filled_qty(old['intent_id']) == 10
    assert rig.store.intent_filled_qty(new['intent_id']) == 15
    assert rig.store.conn.execute('SELECT realized_pnl FROM fills WHERE exec_id=?', (second.exec_id,)).fetchone()[0] == 0
    assert any(key.startswith('anomaly:exit_after_close:' + second.exec_id) for key in rig.store.anomalies())
    assert _audit(rig.store, 'TRADE_CLOSED') == closes
    assert _writes(rig) == writes
    fill_count = rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
    for _ in range(3):
        rig.engine._ingest_fill(rig.clock.now(), second)
    assert _audit(rig.store, 'PROTECT_FILL', second.exec_id) == [payload]
    assert len(_audit(rig.store, 'PROTECT_RETIRED_FILL', second.exec_id)) == 1
    assert rig.store.conn.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == fill_count
    assert dict(rig.store.position(pos['position_id'])) == closed
    assert _audit(rig.store, 'TRADE_CLOSED') == closes
    assert _writes(rig) == writes

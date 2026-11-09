"""B26 independent edge contracts; core 33 and legacy files stay immutable.

All files/configuration are synthetic tmp_path fixtures. Evidence and broker
writes are real Engine/FakeBroker calls. No authority is mocked successful.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ConfigError, load_config, validate
from v6lite.market_calendar import trading_day
from v6lite.models import Purpose, Side
from v6lite.tests.helpers import et, make_cfg
from v6lite.tests.test_batch26_overnight_evidence import (
    _activate, _attempt_tp, _no_network, _overnight_p1, _protection_config,
    _restart, _safety, _settle, _tp_requests, _writes, seed_overnight,
)


def _config_file(tmp_path, value=None):
    path=tmp_path / ('synthetic-' + ('omitted' if value is None else value.replace('"', '')) + '.toml')
    state=(tmp_path / 'synthetic-state-never-created').as_posix()
    text=('mode = "paper"\n[ibkr]\naccount = "DU0000000"\nport = 7497\nclient_id = 131\n'
          '[universe]\nscan = ["AAPL"]\ntradable = ["AAPL"]\n'
          '[paths]\nstate_dir = "' + state + '"\nforeign_lock_paths = []\n')
    if value is not None:
        text += '[protection]\novernight_tp = ' + value + '\n'
    path.write_text(text, encoding='utf-8')
    return path


def _must_load(path):
    try:
        return load_config(path)
    except ConfigError as exc:
        # New-option support is an asserted loader behavior, not a failed
        # fixture or a TypeError while constructing a future dataclass field.
        pytest.fail('valid reviewed overnight opt-in TOML must load: ' + str(exc))


def test_B26_edge_real_TOML_omission_matches_freeze_and_constraint_changes_fingerprint(tmp_path):
    old = _must_load(_config_file(tmp_path))
    off = _must_load(_config_file(tmp_path, '"freeze"'))
    on = _must_load(_config_file(tmp_path, '"constraint"'))
    assert old.protection.overnight_tp == 'freeze'
    assert off.protection.overnight_tp == 'freeze' and on.protection.overnight_tp == 'constraint'
    assert old.fingerprint() == off.fingerprint() and on.fingerprint() != off.fingerprint()
    assert not (tmp_path / 'synthetic-state-never-created').exists()


@pytest.mark.parametrize('value', [0, 1, 'true', 'false', None, True, False])
def test_B26_edge_validate_rejects_invalid_policy_without_truthiness_coercion(tmp_path, value):
    protection = _protection_config(False)
    cfg = make_cfg(tmp_path, protection=protection)
    object.__setattr__(protection, 'overnight_tp', value)  # Synthetic invalid config only.
    with pytest.raises(ConfigError, match='overnight_tp'):
        validate(cfg)


@pytest.mark.parametrize('value', ['0', '1', '"false"'])
def test_B26_edge_real_TOML_loader_rejects_non_enum_policy(tmp_path, value):
    with pytest.raises(ConfigError, match='overnight_tp'):
        load_config(_config_file(tmp_path, value))


@pytest.mark.parametrize('field', ['quantity', 'price', 'side', 'broker_order_id', 'perm_id', 'duplicate_download'])
def test_B26_edge_same_execId_conflicting_economics_is_not_rebooked_or_accepted_as_TP_proof(
        seed_overnight, tmp_path, monkeypatch, field):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    tp1, ref = _activate(rig, pid)
    # A real current-day TP1 is economically booked first. It is not an old
    # ENTRY with a fabricated new time, so execution-time review cannot act
    # as a proxy for recognizing the later economic/identity contradiction.
    original = rig.broker.fill(tp1.intent_id, qty=1, price=rig.broker.quote('AAPL').bid)
    _safety(rig); _settle(rig); _safety(rig)
    pending = rig.broker.protection_pending_modifications.get(ref)
    assert pending is not None and pending.quantity == 2
    assert pending.trigger_price == pytest.approx(rig.store.position(pid)['breakeven_stop'])
    rig.broker.confirm_protection_modify(ref); _settle(rig); _safety(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['pending_qty'] is None
    assert row['trigger_price'] == pytest.approx(rig.store.position(pid)['breakeven_stop'])
    assert rig.store.position(pid)['qty_open'] == 2 and rig.store.position(pid)['tp1_done'] == 1
    assert trading_day(original.time) == trading_day(rig.clock.now())
    assert original in rig.broker.executions_today()
    assert rig._b26_entry_fill.exec_id not in {fill.exec_id for fill in rig.broker.executions_today()}
    before_fill = dict(rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (original.exec_id,)).fetchone())
    assert before_fill['time'] == original.time.isoformat()
    before_pos = dict(rig.store.position(pid))
    before_tps = tuple(_tp_requests(rig))
    assert before_tps == (tp1,)
    values = {'quantity': 2, 'price': original.price + 1, 'side': Side.BUY,
              'broker_order_id': original.broker_order_id + 1, 'perm_id': original.perm_id + 1}
    if field == 'duplicate_download':
        # Fake's real TP execution is already first. Append bad then good:
        # this complete download is good/bad/good with the same real time.
        # Last-write-wins dict indexing must not erase the middle conflict.
        bad = replace(original, quantity=2)
        rig._b26_visible_extra.extend([bad, original])
    else:
        bad = replace(original, **{field: values[field]})
        rig._b26_visible_extra.append(bad)
    assert bad.exec_id == original.exec_id and bad.time == original.time
    rig.broker.protection_auto_status = True
    rig._b26_alerts.clear()  # Only subsequent P1 can prove this new conflict.
    before_writes = _writes(rig)
    _at_stage(rig, pid, Purpose.TP2)  # Real quote/decision/queue; never seed a successful stage.
    assert tuple(_tp_requests(rig)) == before_tps and _writes(rig) == before_writes
    after = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', (original.exec_id,)).fetchall()
    assert len(after) == 1 and dict(after[0]) == before_fill
    pos = rig.store.position(pid)
    assert pos['qty_open'] == before_pos['qty_open'] == 2
    assert pos['realized_pnl'] == before_pos['realized_pnl']
    pages = _overnight_p1(rig)
    assert pages and any(any(word in text for word in ('冲突', '不一致', '成交', '身份')) for text in pages)


def _at_stage(rig, pid, stage):
    level = {Purpose.TP1: 'tp1', Purpose.TP2: 'tp2', Purpose.TP3: 'tp3'}[stage]
    rig.advance(seconds=31)
    rig.px('AAPL', float(rig.store.position(pid)[level]) + .20)
    rig.engine._protection_progress(rig.clock.now())
    rig.engine._evaluate_exits(rig.clock.now())
    rig.engine._run_queue(); rig.engine._drain(rig.clock.now()); _settle(rig)
    _safety(rig)


def test_B26_edge_real_TP1_TP2_fills_then_final_TP3_cancel_restores_only_one_share(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    tp1, old_ref = _activate(rig, pid)
    rig.broker.fill(tp1.intent_id, qty=1, price=rig.broker.quote('AAPL').bid)
    _safety(rig); _settle(rig)
    if old_ref in rig.broker.protection_pending_modifications:
        rig.broker.confirm_protection_modify(old_ref); _settle(rig)
    _safety(rig)
    assert rig.store.position(pid)['qty_open'] == 2 and rig.store.position(pid)['tp1_done'] == 1
    rig.broker.protection_auto_status = True
    _at_stage(rig, pid, Purpose.TP2)
    [tp2] = [request for request in _tp_requests(rig) if request.purpose is Purpose.TP2]
    assert tp2.quantity == 1
    rig.broker.fill(tp2.intent_id, qty=1, price=rig.broker.quote('AAPL').bid)
    _safety(rig); _settle(rig); _safety(rig)
    pos = rig.store.position(pid)
    assert pos['qty_open'] == 1 and pos['tp1_done'] == 1 and pos['tp2_done'] == 1
    assert rig.broker.orders[old_ref].remaining == 1
    before = len(_tp_requests(rig))
    _at_stage(rig, pid, Purpose.TP3)
    assert old_ref in rig.broker.cancels
    assert rig.broker.orders[old_ref].status in {'Submitted', 'PreSubmitted'}
    assert len(_tp_requests(rig)) == before, 'final TP must await original GTC cancellation evidence'
    rig._b26_queries['epoch'] += 1
    rig.broker.confirm_cancel(old_ref); _settle(rig); _safety(rig)
    [tp3] = [request for request in _tp_requests(rig) if request.purpose is Purpose.TP3]
    assert tp3.quantity == 1 and rig.broker.orders[old_ref].status == 'Cancelled'
    assert rig.store.position(pid)['qty_open'] == 1
    rig.advance(seconds=11)
    rig.engine._protection_timers(rig.clock.now()); _settle(rig); _safety(rig)
    assert tp3.intent_id in rig.broker.cancels
    assert len(rig.broker.sent_protections) == 1, 'unconfirmed TP cancel cannot authorize replacement protection'
    rig._b26_queries['epoch'] += 1
    rig.broker.confirm_cancel(tp3.intent_id); _settle(rig); _safety(rig)
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 1
    assert row['gap_b_started_at'] is None and row['tp_stage'] is None
    assert rig.store.position(pid)['qty_open'] == 1
    assert len(rig.broker.sent_protections) == 2
    replacement = rig.broker.sent_protections[-1]
    assert replacement.quantity == 1 and replacement.order_type == 'STP' and replacement.limit_price is None
    assert replacement.intent_id == row['intent_id'] and replacement.intent_id != old_ref
    retired = json.loads(rig.store.control('retired_protection:' + old_ref))
    assert retired['broker_order_id'] == rig._b26_original['broker_order_id']
    assert retired['perm_id'] == rig._b26_original['perm_id']
    assert len([request for request in _tp_requests(rig) if request.purpose is Purpose.TP3]) == 1


def test_B26_edge_query_generation_change_revokes_earlier_overnight_proof(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    rig.broker.query_generation = 1  # Fake counterpart of the adapter's real socket generation.
    _restart(rig, tmp_path, monkeypatch)
    rig.engine._protection_progress(rig.clock.now())
    original = rig.broker.positions_fresh

    def positions():
        result = original()
        rig.broker.query_generation += 1  # Connection changed between the three replies.
        return result

    monkeypatch.setattr(rig.broker, 'positions_fresh', positions)
    before = _writes(rig)
    _attempt_tp(rig, pid)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    # B28b: a superseded socket is pending new proof, not a failed a-d result.
    assert not _overnight_p1(rig)
    assert pid not in rig.engine._protection_notice_evidence
    assert pid in rig.engine._protection_verification_retry
    assert not rig.store.controls_with_prefix('overnight_tp_frozen_at_price:' + pid)


def test_B26_edge_new_trading_day_requires_new_queries_not_yesterdays_cached_permission(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    rig.engine._protection_progress(rig.clock.now())
    rig.at(et(10, 1, day=(2026, 10, 15)))
    attempts = []

    def today_unavailable():
        attempts.append(trading_day(rig.clock.now()).isoformat())
        raise BrokerError('synthetic new-day executions incomplete; prior-day permission is stale')

    monkeypatch.setattr(rig.broker, 'executions_today', today_unavailable)
    before = _writes(rig)
    _attempt_tp(rig, pid)
    assert attempts and set(attempts) == {'2026-10-15'}
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert _overnight_p1(rig)

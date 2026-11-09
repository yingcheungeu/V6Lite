"""B26 10/15 policy ruling: enum, accepted constraints and stable monitoring.

Real Engine/FakeBroker/SQLite paths. Only synthetic configuration, visible
query rows and Fake's streamed-cache adapter are changed. No private authority
helper or complete-evidence result is mocked. All writes and GTC identities
remain observable; no TWS, Gateway, user state or operator config is touched.
"""
from __future__ import annotations

import re
import tomllib
from collections import Counter
from dataclasses import fields, replace
from pathlib import Path

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.config import ConfigError, ProtectionConfig, validate
from v6lite.models import FillEvent, Purpose, Side
from v6lite.tests.helpers import Rig, et, make_cfg
from v6lite.tests.test_batch26_overnight_evidence import (
    _activate, _attempt_tp, _no_network, _overnight_p1, _protection_config,
    _restart, _restore_infos, _safety, _settle, _tp_requests, _writes, seed_overnight,
)


def _policy(rig, value):
    # Future field attached only to synthetic historical B25/B26 configs.
    # The old bool is retained only when actually declared by that baseline.
    object.__setattr__(rig.cfg.protection, 'overnight_tp', value)
    if 'overnight_tp_enabled' in {f.name for f in fields(ProtectionConfig)}:
        object.__setattr__(rig.cfg.protection, 'overnight_tp_enabled', value == 'constraint')


def _unknown(rig, suffix):
    return FillEvent('b26-policy-unknown-' + suffix + '.01.01', '', 'AAPL', Side.BUY,
                     1, 113.0, rig.clock.now(), con_id=rig._b26_original['con_id'])


def _accepted_constraints(text):
    assert re.search(r'依据运行约束\s*A[、,，]\s*B', text), text
    assert '账户独占' in text and '账本不回滚' in text, text
    assert re.search(r'(?:不是|并非|不构成|不能视为|非)技术证明', text), text
    assert 'Lite' in text and ('只由' in text or '仅由' in text), text
    assert re.search(r'停机.*撤单.*平仓.*release', text, flags=re.S), text
    assert all(word in text for word in ('替换', '恢复', '编辑', '状态库')), text


def _no_false_evidence_p0(rig):
    assert not [text for level, text in rig._b26_alerts
                if level == 'P0' and ('三项证据不完整' in text or 'evidence incomplete' in text)], (
        'a stable GTC plus restricted overnight TP is not a P0 evidence incident', rig._b26_alerts)


def test_B26_policy_enum_default_and_all_five_examples_remain_freeze():
    assert getattr(ProtectionConfig(), 'overnight_tp', None) == 'freeze'
    assert 'overnight_tp_enabled' not in {f.name for f in fields(ProtectionConfig)}, (
        'the reviewed enum replaces the temporary bool, rather than leaving two authorities')
    examples = Path(__file__).parents[1] / 'examples'
    for name in ('live.toml', 'paper.toml', 'paper_delayed.toml', 'paper_g3n.toml', 'paper_test.toml'):
        data = tomllib.loads((examples / name).read_text(encoding='utf-8'))
        assert data['protection']['overnight_tp'] == 'freeze', name
        assert 'overnight_tp_enabled' not in data['protection'], name


@pytest.mark.parametrize('value', ['', 'FREEZE', 'Constraint', 'enabled'])
def test_B26_policy_invalid_enum_strings_are_rejected_without_normalization(tmp_path, value):
    protection = _protection_config(False)
    object.__setattr__(protection, 'overnight_tp', value)
    with pytest.raises(ConfigError, match='overnight_tp'):
        validate(make_cfg(tmp_path, protection=protection))


def test_B26_policy_first_real_overnight_TP_INFO_states_accepted_constraints_not_technical_proof(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    tp, ref = _activate(rig, pid)
    assert tp.quantity == 1 and rig.broker.orders[ref].remaining == 2
    infos = _restore_infos(rig)
    assert len(infos) == 1
    _accepted_constraints(infos[0])
    assert all(word in infos[0] for word in ('AAPL', '账本', '当日', rig._b26_entry_fill.exec_id))
    _safety(rig)


@pytest.mark.parametrize('initial', ['freeze', 'unknown_today_exec'])
def test_B26_policy_only_one_durable_explanation_per_position_despite_reason_change_30m_and_restart(
        seed_overnight, tmp_path, monkeypatch, initial):
    rig, pid = seed_overnight(enabled=initial != 'freeze')
    if initial == 'unknown_today_exec':
        rig._b26_visible_extra.append(_unknown(rig, 'first'))
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch)
    _attempt_tp(rig, pid)
    pages = _overnight_p1(rig)
    assert len(pages) == 1, 'restricted overnight TP needs one durable explanation card'
    _accepted_constraints(pages[0])
    assert 'AAPL' in pages[0] and any(word in pages[0] for word in ('freeze', '限制', '归属', '未知', '成交'))
    _no_false_evidence_p0(rig)
    # The failure reason really changes, rather than repeating one exact body.
    rig.advance(seconds=31)
    _policy(rig, 'constraint')
    rig._b26_visible_extra[:] = [_unknown(rig, 'different-reason')]
    for seconds in (0, 1801, 1801):
        if seconds:
            rig.advance(seconds=seconds)
        rig.px('AAPL', float(rig.store.position(pid)['tp1']) + .20)
        rig.tick()
    assert len(_overnight_p1(rig)) == 1, 'no changed-reason or 30-minute reminder for this position'
    _no_false_evidence_p0(rig)
    _restart(rig, tmp_path, monkeypatch, reopen=True)
    _attempt_tp(rig, pid)
    assert len(_overnight_p1(rig)) == 1, 'SQLite reopen must preserve explanation-card deduplication'
    _no_false_evidence_p0(rig)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
    assert rig.broker.orders[row['intent_id']].remaining == 3
    _safety(rig)


@pytest.mark.parametrize('failure', ['freeze', 'missing_entry_exec', 'unknown_today_exec'])
def test_B26_policy_failed_TP_scope_with_stable_GTC_uses_one_complete_query_set_every_15_seconds(
        seed_overnight, tmp_path, monkeypatch, failure):
    rig, pid = seed_overnight(enabled=failure != 'freeze')
    if failure == 'missing_entry_exec':
        rig.store.conn.execute('DELETE FROM fills WHERE exec_id=?', (rig._b26_entry_fill.exec_id,))
    elif failure == 'unknown_today_exec':
        rig._b26_visible_extra.append(_unknown(rig, 'cadence'))
    # Real adapter positions() is streamed cache. Fake implements that cache
    # via self.positions_fresh(), which would wrongly count each exit/risk
    # cache read as reqPositions. Delegate only its cache to raw real truth;
    # all four active evidence methods below remain real and observable.
    monkeypatch.setattr(rig.broker, 'positions', lambda: FakeBroker.positions_fresh(rig.broker))
    rig.cfg = replace(rig.cfg, reconcile_interval_seconds=3600)
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch)
    rig.tick()  # establish the real t=0 monitoring attempt before counting
    calls = []
    channels = ('open_orders', 'positions_fresh', 'executions_today', 'account_executions_today')
    for name in channels:
        original = getattr(rig.broker, name)

        def query(*args, _name=name, _original=original, **kwargs):
            result = _original(*args, **kwargs)
            calls.append((_name, rig.clock.now()))
            return result

        monkeypatch.setattr(rig.broker, name, query)
    started = rig.clock.now()
    # Quotes remain refreshed every second and below the first TP threshold.
    # A fast loop cannot be explained away by stale quote or pending exit.
    assert rig.broker.quote('AAPL').mark() < rig.store.position(pid)['tp1']
    for _ in range(31):
        rig.advance(seconds=1)
        rig.tick()
    assert Counter(name for name, _ in calls) == Counter({name: 2 for name in channels}), calls
    for name in channels:
        elapsed = [(at - started).total_seconds() for channel, at in calls if channel == name]
        assert elapsed == [15, 30], (name, elapsed)
    assert _tp_requests(rig) == [] and _writes(rig) == before
    row = rig.store.protection(pid)
    assert row['state'] == 'WORKING' and row['confirmed_remaining'] == 3
    assert rig.broker.orders[row['intent_id']].remaining == 3
    assert len(_overnight_p1(rig)) == 1
    _no_false_evidence_p0(rig)
    _safety(rig)


def test_B26_policy_actual_missing_protection_still_retains_original_P0(
        seed_overnight, tmp_path, monkeypatch):
    rig, pid = seed_overnight()
    ref = rig.store.protection(pid)['intent_id']
    rig.broker.orders[ref].status = 'Cancelled'
    before = _writes(rig)
    _restart(rig, tmp_path, monkeypatch)
    _attempt_tp(rig, pid)
    assert any(level == 'P0' and '保护' in text and 'AAPL' in text
               for level, text in rig._b26_alerts), 'genuine absent GTC must not be hidden as a TP limitation'
    assert _tp_requests(rig) == [] and _writes(rig) == before
    assert rig.broker.orders[ref].status == 'Cancelled'


def test_B26_policy_same_day_BE_modification_requires_current_entry_execution_evidence(tmp_path):
    rig = Rig(tmp_path, start=et(9, 50, day=(2026, 10, 14)),
              protection=_protection_config(True), risk_per_trade_pct=.0015)
    try:
        rig.start(); rig.at(et(10, 0, 30, day=(2026, 10, 14))); rig.tick()
        [entry] = [request for request in rig.broker.sent if request.purpose is Purpose.ENTRY]
        assert entry.quantity == 3
        entry_fill = rig.broker.fill(entry.intent_id, qty=3, price=113.13)
        rig.advance(seconds=1); rig.tick()
        [pos] = rig.store.open_positions(); pid = pos['position_id']
        original = dict(rig.store.protection(pid)); ref = original['intent_id']
        assert original['state'] == 'WORKING' and original['confirmed_remaining'] == 3
        rig.broker.protection_auto_status = False
        rig.advance(seconds=31); rig.px('AAPL', float(pos['tp1']) + .20)
        rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue(); rig.engine._drain(rig.clock.now())
        assert rig.store.protection(pid)['state'] == 'REDUCE_PENDING'
        assert rig.broker.protection_pending_modifications[ref].quantity == 2
        rig.broker.confirm_protection_modify(ref)
        for _ in range(3):
            rig.engine._drain(rig.clock.now()); rig.engine._protection_progress(rig.clock.now())
        [tp] = [request for request in rig.broker.sent if request.purpose is Purpose.TP1]
        assert tp.quantity == 1 and rig.broker.orders[ref].remaining == 2
        # Only the fresh execution download loses the already-persisted real
        # ENTRY. Native P/GTC and the complete persisted ledger remain intact.
        rig.broker.invisible_execs.add(entry_fill.exec_id)
        tp_fill = rig.broker.fill(tp.intent_id, qty=1, price=rig.broker.quote('AAPL').bid)
        assert tp_fill.exec_id in {fill.exec_id for fill in rig.broker.executions_today()}
        assert entry_fill.exec_id not in {fill.exec_id for fill in rig.broker.executions_today()}
        before = _writes(rig)
        for _ in range(3):
            rig.engine._drain(rig.clock.now()); rig.engine._protection_progress(rig.clock.now())
        pos = rig.store.position(pid); row = rig.store.protection(pid)
        assert pos['qty_open'] == 2 and pos['tp1_done'] == 1 and pos['breakeven_stop'] is not None
        assert _writes(rig) == before, 'same-day BE is a broker modification and also requires fresh full evidence'
        assert row['trigger_price'] == original['trigger_price'] and row['pending_trigger'] is None
        assert rig.broker.orders[ref].req.trigger_price == original['trigger_price']
        assert ref not in rig.broker.protection_pending_modifications
        assert rig.broker.orders[ref].remaining == row['confirmed_remaining'] == 2
        assert len(rig.broker.sent_protections) == 1
    finally:
        rig.alerter.close(); rig.store.close()

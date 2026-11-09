"""B26 candidate regression: a BE confirmation token must have exact bindings.

Real yesterday entry/SQLite reopen, actual today's TP1 fill and a genuine
PRICE_PENDING BE token precede the sole mutation: corrupt only its JSON binding.
No private confirmation helper or evidence authority is mocked.
"""
from __future__ import annotations

import json

import pytest

from v6lite.tests.test_batch26_overnight_evidence import (
    _activate, _no_network, _overnight_p1, _restart, _safety, _settle,
    _tp_requests, _writes, seed_overnight,
)


@pytest.mark.parametrize('corruption', ['empty_binding', 'missing_modify_generation'])
def test_B26_regression_real_BE_ACK_cannot_confirm_or_enable_TP_with_partial_token_binding(
        seed_overnight, tmp_path, monkeypatch, corruption):
    rig, pid = seed_overnight()
    _restart(rig, tmp_path, monkeypatch)
    tp1, ref = _activate(rig, pid)
    rig.broker.fill(tp1.intent_id, qty=1, price=rig.broker.quote('AAPL').bid)
    _safety(rig); _settle(rig); _safety(rig)
    pos = rig.store.position(pid)
    row = rig.store.protection(pid)
    assert pos['qty_open'] == 2 and pos['tp1_done'] == 1
    assert row['state'] == 'PRICE_PENDING' and row['pending_qty'] == 2
    assert row['pending_trigger'] == pytest.approx(pos['breakeven_stop'])
    request = rig.broker.protection_pending_modifications[ref]
    assert request.quantity == 2 and request.trigger_price == pytest.approx(pos['breakeven_stop'])
    key = 'overnight_be_flow:' + pid
    raw = rig.store.control(key)
    assert raw is not None, 'real authorized BE modification must persist its own token before the write'
    token = json.loads(raw)
    expected_fields = {'position_id', 'intent_id', 'broker_order_id', 'perm_id', 'con_id',
                       'account', 'client_id', 'modify_generation', 'pending_qty', 'pending_trigger'}
    assert set(token['binding']) == expected_fields
    assert all(token['binding'][name] == row[name] for name in expected_fields)
    bad = json.loads(raw)
    if corruption == 'empty_binding':
        bad['binding'] = {}
    else:
        del bad['binding']['modify_generation']
    # This is the only mutation to application state. All broker truth, prices,
    # quantities, dates, history digests and other token fields stay genuine.
    rig.store.set_control(rig.clock.now(), key, json.dumps(bad, sort_keys=True))
    before_writes = _writes(rig)
    before_tps = tuple(_tp_requests(rig))
    rig._b26_alerts.clear()
    rig.broker.confirm_protection_modify(ref)  # Actual native-shaped BE ACK.
    _safety(rig); _settle(rig); _safety(rig)
    after = rig.store.protection(pid)
    assert after['state'] != 'WORKING', (
        'all({}.items()) or a missing generation cannot authorize BE confirmation')
    assert _writes(rig) == before_writes and tuple(_tp_requests(rig)) == before_tps
    # Even a fresh real TP2 quote cannot promote the corrupt confirmation.
    rig.advance(seconds=31)
    rig.px('AAPL', float(rig.store.position(pid)['tp2']) + .20)
    rig.engine._protection_progress(rig.clock.now())
    rig.engine._evaluate_exits(rig.clock.now()); rig.engine._run_queue()
    rig.engine._drain(rig.clock.now()); _settle(rig); _safety(rig)
    assert _writes(rig) == before_writes and tuple(_tp_requests(rig)) == before_tps
    assert rig.store.protection(pid)['state'] != 'WORKING'
    native = rig.broker.orders[ref]
    assert native.remaining == 2 and native.req.intent_id == ref
    assert native.broker_order_id == rig._b26_original['broker_order_id']
    assert native.broker_order_id + 50_000 == rig._b26_original['perm_id']
    assert native.req.trigger_price == pytest.approx(pos['breakeven_stop'])
    assert len(rig.broker.sent_protections) == 1
    pages = _overnight_p1(rig)
    assert pages and any(any(word in text for word in ('保本', '绑定', 'token', '证据', '身份')) for text in pages)

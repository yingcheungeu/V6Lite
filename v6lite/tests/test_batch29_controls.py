"""Batch 29 control boundaries, using real CLI requests and temporary Store state.

Only FakeBroker is used. These cases do not create successful proof dictionaries,
change stage/position quantities with SQL, or replace a product authorization gate.
"""
from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal, ROUND_UP

import pytest

from v6lite import __main__ as cli
from v6lite.config import ProtectionConfig, TestConfig as LiteTestConfig
from v6lite.models import Purpose
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch16 import _test_tp_timeout_config
from v6lite.tests.test_batch23_contracts import (
    TP_PURPOSES, _activate_tp, _g3t_working, _no_network, _queue_tp,
    _restart, _safety, _settle, _writes,
)

KEY = "test_tp_nonmarketable_once:AAPL"


def _request(path, *, symbol="AAPL", operator="John", dry_run=False):
    argv = ["test-tp-timeout", symbol, "--operator", operator, "--config", str(path)]
    if dry_run:
        argv.append("--dry-run")
    try:
        return cli.main(argv)
    except SystemExit as exc:
        # The command need not expose a dry-run option. argparse refusing that
        # option before opening any state is an acceptable explicit refusal.
        if not dry_run:
            raise
        assert exc.code == 2
        return 2


def _results(rig):
    return [json.loads(row["payload"]) for row in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='TEST_TP_TIMEOUT_RESULT' ORDER BY id")]


def _tp_requests(rig):
    return [req for req in rig.broker.sent if req.purpose in TP_PURPOSES]


def _assert_normal_price(rig, req):
    quote = rig.broker.quote(req.symbol)
    assert quote is not None and quote.bid is not None
    expected = (Decimal(str(quote.bid)) * Decimal(str(
        1 - rig.cfg.exit_limit_band_pct))).quantize(Decimal(".01"), rounding=ROUND_UP)
    assert Decimal(str(req.limit_price)) == expected
    assert req.limit_price <= quote.bid
    order = next(order for order in rig.broker.open_orders() if order.intent_id == req.intent_id)
    assert order.order_type == "LMT" and order.tif == "DAY"
    assert rig.market_calls == []
    _safety(rig)


def _next_normal_tp(rig, pid):
    before = {req.intent_id for req in _tp_requests(rig)}
    req = _activate_tp(rig, pid)
    assert req.intent_id not in before
    _assert_normal_price(rig, req)
    assert rig.store.control(KEY) is None
    return req


def _finish_normal_stage(rig, pid, stage):
    req = _activate_tp(rig, pid, stage)
    _assert_normal_price(rig, req)
    # The actual fill can initiate the usual TP1 breakeven modification.
    # Let FakeBroker emit its normal modification confirmation; no state edit.
    rig.broker.protection_auto_status = True
    rig.broker.fill(req.intent_id)
    _settle(rig)
    for _ in range(3):
        row = rig.store.protection(pid)
        if row["state"] == "WORKING" and row["tp_stage"] is None:
            break
        rig.advance(seconds=1)
        rig.engine.process_protection_priority()
        _settle(rig)
    row = rig.store.protection(pid)
    assert row["state"] == "WORKING" and row["tp_stage"] is None
    assert rig.store.stage(pid, stage)["status"] == "DONE"
    _safety(rig)


def _run_last_normal_tp(rig, pid, stage):
    before = {req.intent_id for req in _tp_requests(rig)}
    original = rig.store.protection(pid)["intent_id"]
    rig.broker.protection_auto_status = True
    _queue_tp(rig, pid, stage)
    rig.engine._run_queue()
    rig.engine._drain(rig.clock.now())
    assert original in rig.broker.cancels
    rig.broker.confirm_cancel(original)
    _settle(rig)
    for _ in range(5):
        sent = [req for req in _tp_requests(rig) if req.intent_id not in before]
        if sent:
            break
        rig.advance(seconds=1)
        rig.engine.process_protection_priority()
        _settle(rig)
    sent = [req for req in _tp_requests(rig) if req.intent_id not in before]
    assert len(sent) == 1
    req = sent[0]
    assert req.purpose is stage and req.quantity == rig.store.position(pid)["qty_open"] == 1
    _assert_normal_price(rig, req)
    assert rig.store.control(KEY) is None
    return req


def test_B29_CLI_accepts_bound_request_without_broker_write_and_duplicate_cannot_replace(
        tmp_path, monkeypatch, capsys):
    rig, pid, path = _g3t_working(tmp_path, monkeypatch)
    before = _writes(rig)
    assert _request(path, symbol="aapl") == 0
    raw = rig.store.control(KEY)
    assert raw is not None
    request = json.loads(raw)
    assert request["version"] == 29
    assert request["symbol"] == "AAPL" and request["operator"] == "John"
    assert request["position_id"] == pid
    assert request["protection_intent_id"] == rig.store.protection(pid)["intent_id"]
    assert request["epoch"] == rig.store.control("test_entry_ready")
    assert request["request_id"] and request["at"]
    assert _writes(rig) == before
    capsys.readouterr()
    assert _request(path) == 2
    refused = capsys.readouterr()
    assert (refused.err + refused.out).strip(), "duplicate refusal must explain its reason"
    assert rig.store.control(KEY) == raw, "a second command must not replace the accepted request"
    assert _writes(rig) == before
    assert _tp_requests(rig) == []


@pytest.mark.parametrize("gate", [
    "formal", "client131", "dry_run", "no_position", "active_tp",
    "blank_operator", "unconfigured_symbol", "invalid_symbol", "no_epoch",
])
def test_B29_CLI_refuses_unsafe_control_without_creating_request_or_broker_write(
        tmp_path, monkeypatch, capsys, gate):
    if gate == "no_position":
        rig = Rig(tmp_path, state_dir=str(tmp_path / "paper-test"), client_id=132,
                  test=LiteTestConfig(enabled=True), protection=ProtectionConfig(enabled=True),
                  scan_times_et=("13:30",))
        rig.start()
        path, _ = _test_tp_timeout_config(tmp_path)
        monkeypatch.setattr(cli, "_now", rig.clock.now)
        assert rig.store.open_positions() == []
    else:
        rig, pid, path = _g3t_working(tmp_path, monkeypatch)
    if gate == "formal":
        path, _ = _test_tp_timeout_config(tmp_path, test_enabled=False)
    elif gate == "client131":
        path.write_text(path.read_text(encoding="utf-8").replace(
            "client_id = 132", "client_id = 131"), encoding="utf-8")
    elif gate == "active_tp":
        _activate_tp(rig, pid)
        assert rig.store.protection(pid)["tp_stage"] is not None
    elif gate == "no_epoch":
        # A missing readiness token is a refusal fixture, never fabricated authority.
        rig.store.delete_control("test_entry_ready")
    before = _writes(rig)
    capsys.readouterr()
    result = _request(path, operator=" " if gate == "blank_operator" else "John",
                      symbol="MSTR" if gate == "unconfigured_symbol" else
                             "AAPL;" if gate == "invalid_symbol" else "AAPL",
                      dry_run=gate == "dry_run")
    assert result == 2
    output = capsys.readouterr()
    assert (output.err + output.out).strip(), "every refusal must state a reason"
    assert rig.store.control(KEY) is None
    assert _writes(rig) == before


@pytest.mark.parametrize("invalidation", ["expired", "restart", "disconnect", "cross_day"])
def test_B29_stale_or_invalidated_request_is_audited_and_cannot_change_normal_TP_price(
        tmp_path, monkeypatch, invalidation):
    rig, pid, path = _g3t_working(tmp_path, monkeypatch)
    if invalidation == "cross_day":
        # This is the supported overnight configuration, not a replaced gate
        # or a successful proof installed by the test.
        rig.cfg = replace(rig.cfg, protection=replace(rig.cfg.protection, overnight_tp="constraint"))
        rig.engine.cfg = rig.cfg
        path.write_text(path.read_text(encoding="utf-8") +
                        '\n[protection]\novernight_tp = "constraint"\n', encoding="utf-8")
    assert _request(path) == 0
    accepted = json.loads(rig.store.control(KEY))
    if invalidation == "expired":
        rig.advance(minutes=6)
    elif invalidation == "restart":
        # Reopen the real temporary SQLite and start a new Engine. Startup
        # must invalidate the previously published readiness epoch itself.
        _restart(rig, tmp_path)
        assert rig.store.control(KEY) is None
        assert rig.store.control("test_entry_ready") != accepted["epoch"]
    elif invalidation == "disconnect":
        rig.broker.drop_connection()
        rig.engine._drain(rig.clock.now())
        assert rig.store.control(KEY) is None
        rig.advance(seconds=31)
        rig.tick()
        assert rig.broker.is_connected() and rig.engine.flags.reconcile_ok
        assert rig.store.control("test_entry_ready") != accepted["epoch"]
    elif invalidation == "cross_day":
        rig.at(et(10, 2, day=(2026, 10, 7)))
        rig.tick()
    _next_normal_tp(rig, pid)
    outcomes = _results(rig)
    assert any(item.get("outcome") and item.get("outcome") != "claimed" for item in outcomes), outcomes
    assert not any(item.get("outcome") == "claimed" and
                   item.get("request_id") == accepted["request_id"] for item in outcomes)


@pytest.mark.parametrize("last_stage", [Purpose.TP3, Purpose.TP1], ids=["final-TP3", "final-TP1"])
def test_B29_last_remaining_share_refuses_injection_and_keeps_normal_final_TP(
        tmp_path, monkeypatch, capsys, last_stage):
    rig, pid, path = _g3t_working(tmp_path, monkeypatch)
    original = rig.store.protection(pid)["intent_id"]
    if last_stage is Purpose.TP3:
        _finish_normal_stage(rig, pid, Purpose.TP1)
        _finish_normal_stage(rig, pid, Purpose.TP2)
    else:
        # Real STP economics leave only one share although TP1 is not done.
        rig.broker.fill(original, qty=2)
        _settle(rig)
        assert rig.store.intent_filled_qty(original) == 2
        assert not rig.store.position(pid)["tp1_done"]
    assert rig.store.position(pid)["qty_open"] == 1
    assert rig.store.protection(pid)["confirmed_remaining"] == 1
    before = _writes(rig)
    capsys.readouterr()
    assert _request(path) == 2
    output = capsys.readouterr()
    assert (output.err + output.out).strip()
    assert rig.store.control(KEY) is None and _writes(rig) == before
    _run_last_normal_tp(rig, pid, last_stage)


def test_B29_accepted_request_becomes_final_TP1_after_real_STP_fill_and_is_invalidated(
        tmp_path, monkeypatch):
    rig, pid, path = _g3t_working(tmp_path, monkeypatch)
    original = rig.store.protection(pid)["intent_id"]
    assert _request(path) == 0
    accepted = json.loads(rig.store.control(KEY))
    rig.broker.fill(original, qty=2)
    _settle(rig)
    assert rig.store.position(pid)["qty_open"] == 1
    assert rig.store.intent_filled_qty(original) == 2
    _run_last_normal_tp(rig, pid, Purpose.TP1)
    results = _results(rig)
    assert any(item.get("outcome") and item.get("outcome") != "claimed" for item in results)
    assert not any(item.get("outcome") == "claimed" and
                   item.get("request_id") == accepted["request_id"] for item in results)


def test_B29_old_unversioned_control_never_authorizes_nonmarketable_pricing(
        tmp_path, monkeypatch):
    rig, pid, _ = _g3t_working(tmp_path, monkeypatch)
    # An actual pre-upgrade serialized request format remains untrusted.
    rig.store.set_control(rig.clock.now(), KEY, json.dumps({
        "request_id": "synthetic-legacy-unversioned", "symbol": "AAPL",
        "operator": "John", "at": rig.clock.now().isoformat(),
        "epoch": rig.store.control("test_entry_ready"),
    }))
    _next_normal_tp(rig, pid)
    results = _results(rig)
    assert any(item.get("outcome") and item.get("outcome") != "claimed" for item in results)
    assert not any(item.get("outcome") == "claimed" and
                   item.get("request_id") == "synthetic-legacy-unversioned" for item in results)

"""Batch 17 alert severities exercise actual protection state transitions."""

from __future__ import annotations

from datetime import timedelta

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.models import OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, et


def _rig(tmp_path):
    return Rig(tmp_path, protection=ProtectionConfig(enabled=True))


def _reduced_before_tp(rig):
    rig.open_aapl()
    pos = rig.store.open_positions()[0]
    protection = rig.store.protection(pos["position_id"])
    rig.broker.protection_auto_status = False
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    assert rig.store.protection(pos["position_id"])["state"] == "REDUCE_PENDING"
    assert protection["intent_id"] in rig.broker.protection_pending_modifications
    return pos, protection


def _new_titles(rig, before):
    return [title for title, _ in rig.channel.messages[before:]]


@pytest.mark.parametrize("cause", ["no_quote", "definite_rejection"])
def test_definitely_unsent_TP_automatic_restore_initially_P1(tmp_path, cause):
    rig = _rig(tmp_path)
    pos, protection = _reduced_before_tp(rig)
    if cause == "no_quote":
        rig.broker.quotes.pop("AAPL")
    else:
        rig.broker.fail_next_submit = BrokerError("synthetic definite TP refusal", maybe_sent=False)
    before = len(rig.channel.messages)
    rig.broker.confirm_protection_modify(protection["intent_id"])
    rig.engine._drain(rig.clock.now())
    titles = _new_titles(rig, before)
    assert any("P1" in title and "止盈单未发出，正在恢复保护" in title for title in titles), titles
    assert not any("P0" in title and "保护单状态无法核实" in title for title in titles), titles
    body = next(body for title, body in rig.channel.messages[before:]
                if "止盈单未发出，正在恢复保护" in title)
    assert "AAPL" in body and "持仓数量=20 股" in body
    assert "保护单已确认=" in body and "止盈在途=" in body and "未保护=" in body
    assert f"orderId={protection['broker_order_id']}" in body
    assert protection["intent_id"] in body
    action = body.split("你要做什么：", 1)[1]
    assert action.index("1）") < action.index("2）") < action.index("3）")
    assert "不要另下卖单" in action
    assert "从 B 空窗起点累计 30 秒仍未恢复" in action and "P0" in action
    assert "原 B 空窗持续计时" in body
    assert rig.store.protection(pos["position_id"])["state"] == "TP_NEEDS_RESTORE"
    assert not [r for r in rig.broker.sent if r.side == Side.SELL]
    rig.px("AAPL", pos["entry_price"])
    rig.broker.protection_auto_status = True
    rig.engine.process_protection_priority()
    rig.tick()
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "WORKING" and row["gap_b_started_at"] is None
    assert row["confirmed_remaining"] == pos["qty_open"]
    assert len(rig.broker.sent_protections) == 1
    # A restored B episode must release the alert dedupe latch: another
    # definitely-unsent TP gets an immediate P1 within the normal 15-minute
    # repeat suppression interval, without waiting for it to expire.
    rig.broker.protection_auto_status = False
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    assert rig.store.protection(pos["position_id"])["state"] == "REDUCE_PENDING"
    if cause == "no_quote":
        rig.broker.quotes.pop("AAPL")
    else:
        rig.broker.fail_next_submit = BrokerError("synthetic second definite TP refusal", maybe_sent=False)
    second_before = len(rig.channel.messages)
    rig.broker.confirm_protection_modify(protection["intent_id"])
    rig.engine._drain(rig.clock.now())
    repeated = _new_titles(rig, second_before)
    assert any("P1" in title and "止盈单未发出，正在恢复保护" in title for title in repeated), repeated
    assert rig.store.protection(pos["position_id"])["state"] == "TP_NEEDS_RESTORE"


def test_unsent_TP_restore_not_confirmed_escalates_at_30_seconds(tmp_path):
    rig = _rig(tmp_path)
    pos, protection = _reduced_before_tp(rig)
    rig.broker.quotes.pop("AAPL")
    rig.broker.confirm_protection_modify(protection["intent_id"])
    rig.engine._drain(rig.clock.now())
    start = rig.clock.now()
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "TP_NEEDS_RESTORE" and row["gap_b_started_at"]
    # Hold the restoration's broker confirmation, rather than inventing a
    # TP_UNKNOWN. Complete three-source evidence still allows same-id restore.
    rig.px("AAPL", pos["entry_price"])
    rig.engine.process_protection_priority()
    assert rig.store.protection(pos["position_id"])["state"] == "RESTORE_PENDING"
    before = len(rig.channel.messages)
    rig.clock.set(start + timedelta(seconds=29))
    rig.engine._hard_protection_deadlines(rig.clock.now())
    assert not any("P0" in title for title in _new_titles(rig, before))
    rig.clock.set(start + timedelta(seconds=30))
    rig.engine._hard_protection_deadlines(rig.clock.now())
    titles = _new_titles(rig, before)
    assert any("P0" in title and "保护缺口未恢复" in title for title in titles), titles
    assert rig.store.protection(pos["position_id"])["state"] == "RESTORE_PENDING"
    assert not [r for r in rig.broker.sent if r.side == Side.SELL]


def test_maybe_sent_TP_stays_UNKNOWN_and_must_not_claim_definitely_unsent(tmp_path):
    rig = _rig(tmp_path)
    pos, protection = _reduced_before_tp(rig)
    reduced = rig.broker.protection_pending_modifications[protection["intent_id"]].quantity
    rig.broker.fail_next_submit = BrokerError("synthetic unknown TP acknowledgement", maybe_sent=True)
    before = len(rig.channel.messages)
    rig.broker.confirm_protection_modify(protection["intent_id"])
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.tick()
    titles = _new_titles(rig, before)
    assert any("P1" in title and "提交回执不明" in title for title in titles), titles
    assert not any("止盈单未发出，正在恢复保护" in title for title in titles), titles
    assert rig.broker.orders[protection["intent_id"]].req.quantity == reduced
    assert not [r for r in rig.broker.modified_protections if r.quantity == pos["qty_open"]]
    rig.advance(seconds=30)
    rig.engine._hard_protection_deadlines(rig.clock.now())
    assert any("P0" in title and "止盈" in title for title in _new_titles(rig, before))


def _cancelled_TP_restored(rig):
    rig.open_aapl()
    pos = rig.store.open_positions()[0]
    protect = rig.store.protection(pos["position_id"])
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(r for r in rig.broker.sent if r.purpose == Purpose.TP1)
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    rig.px("AAPL", pos["entry_price"])
    rig.engine.process_protection_priority()
    rig.tick()
    assert rig.store.protection(pos["position_id"])["confirmed_remaining"] == pos["qty_open"]
    assert rig.store.protection(pos["position_id"])["tp_stage"] is None
    return pos, protect, tp


def test_late_TP_reduction_confirmed_P1_pending_stays_P0(tmp_path):
    rig = _rig(tmp_path)
    pos, protect, tp = _cancelled_TP_restored(rig)
    rig.broker.protection_auto_status = False
    before = len(rig.channel.messages)
    rig.broker.fill(tp.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    pending = _new_titles(rig, before)
    assert any("P0" in title and "减量待券商确认" in title for title in pending), pending
    assert not any("P1" in title and "保护单已减量" in title for title in pending), pending
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "MODIFY_PENDING" and row["pending_qty"] is not None
    target = rig.broker.protection_pending_modifications[protect["intent_id"]].quantity
    before = len(rig.channel.messages)
    rig.broker.confirm_protection_modify(protect["intent_id"])
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    titles = _new_titles(rig, before)
    assert any("P1" in title and "保护单已减量" in title for title in titles), titles
    assert not any("P0" in title and "保护单已减量" in title for title in titles), titles
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "WORKING"
    assert row["confirmed_remaining"] == target
    assert row["confirmed_remaining"] <= pos["qty_open"] - 1
    assert not rig.store.control(f"late_tp_reduce:{pos['position_id']}")


@pytest.mark.parametrize("atr", [None, 0.0, float("nan")], ids=["missing", "zero", "nonfinite"])
def test_missing_or_invalid_ATR_BUY_refused_is_P1(tmp_path, atr):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    req = OrderRequest("v6l-attempt-no-atr", "AAPL", Side.BUY, 3, 113.47,
                       Purpose.ENTRY, "position-no-atr")
    before = len(rig.channel.messages)
    result = rig.engine._send(rig.clock.now(), req, atr=atr, count_entry_day="2026-10-06")
    assert result == "refused:invalid_protection_price"
    assert req.intent_id not in rig.broker.orders
    assert rig.store.intent(req.intent_id) is None
    titles = _new_titles(rig, before)
    assert any("P1" in title and "买单已拦截" in title for title in titles), titles
    assert not any("P0" in title and "买单已拦截" in title for title in titles), titles


def test_BUY_already_filled_invalid_protection_price_stays_P0(tmp_path, monkeypatch):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.purpose == Purpose.ENTRY)
    def invalid_prices(*args, **kwargs):
        raise ValueError("synthetic actual-position invalid protection price")
    monkeypatch.setattr("v6lite.protection_engine.protection_prices", invalid_prices)
    before = len(rig.channel.messages)
    rig.broker.fill(buy.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    assert rig.store.open_positions()[0]["qty_open"] == 1
    titles = _new_titles(rig, before)
    assert any("P0" in title and "成交后无法建立有效保护价" in title for title in titles), titles
    assert not any("P1" in title and "成交后无法建立有效保护价" in title for title in titles), titles
    assert not rig.broker.sent_protections
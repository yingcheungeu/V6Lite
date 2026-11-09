"""Batch 17 L3: route booked late ENTRY fills to their actual managed position."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from v6lite.config import ProtectionConfig
from v6lite.engine import Engine
from v6lite.models import IntentStatus, OrderRequest, Purpose, Side
from v6lite.protection import protection_prices
from v6lite.tests.helpers import Rig, et


def _closed_partial_entry(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = next(r for r in rig.broker.sent if r.purpose == Purpose.ENTRY)
    rig.broker.fill(entry.intent_id, qty=10)
    rig.advance(seconds=61)
    rig.tick()
    rig.broker.confirm_cancel(entry.intent_id)
    for _ in range(2):
        rig.advance(seconds=6)
        rig.tick()
    assert rig.store.intent(entry.intent_id)["status"] == IntentStatus.CANCELLED
    pos = rig.store.open_positions()[0]
    old = rig.store.protection(pos["position_id"])
    assert old["state"] == "WORKING"
    rig.broker.fill(old["intent_id"], price=114.0)
    rig.advance(seconds=1)
    rig.tick()
    assert not rig.store.open_positions()
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    return rig, entry, pos, old


def _late_callback(rig, entry, *, qty=5, price=115.00):
    fill = rig.broker.fill(entry.intent_id, qty=qty, price=price, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    return fill


def _assert_recovery_has_plan(rig, entry, old_position, fill):
    intent = rig.store.intent(entry.intent_id)
    pid = intent["recovery_position_id"]
    assert pid and pid != old_position["position_id"]
    pos = rig.store.position(pid)
    assert pos is not None and pos["status"] == "OPEN"
    assert rig.store.conn.execute("SELECT position_id FROM fills WHERE exec_id=?",
                                  (fill.exec_id,)).fetchone()[0] == pid
    row = rig.store.protection(pid)
    assert row is not None, "booked recovery position must receive its own L7 protection hook"
    return pos, row


def _assert_gross(rig, pos):
    held = rig.store.position(pos["position_id"])["qty_open"]
    row = rig.store.protection(pos["position_id"])
    s = max(row["confirmed_remaining"], row["pending_max_remaining"])
    # Observe FakeBroker and ledger reservations directly: the test observer
    # itself does not add a nested broker query inside the execution callback.
    by_ref = {}
    for ref, order in rig.broker.orders.items():
        request = order.req
        if (request.symbol == pos["symbol"] and getattr(request, "side", Side.SELL) == Side.SELL
                and ref != row["intent_id"] and order.status not in {"Cancelled", "ApiCancelled", "Inactive", "Filled"}):
            by_ref[ref] = order.remaining
    for intent in rig.store.active_intents():
        if intent["symbol"] == pos["symbol"] and intent["side"] == Side.SELL and intent["intent_id"] != row["intent_id"]:
            remaining = intent["quantity"] - rig.store.intent_filled_qty(intent["intent_id"])
            by_ref[intent["intent_id"]] = max(by_ref.get(intent["intent_id"], 0), remaining)
    other = sum(by_ref.values())
    assert s + other <= held, (held, s, other, dict(row))
    assert not [r for r in rig.broker.sent if r.purpose == Purpose.STOP]


def test_late_five_gets_independent_GTC_and_each_transition_preserves_gross_capacity(tmp_path, monkeypatch):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    phases = []
    original_submit = rig.broker.submit_stop
    def observe_submission(request):
        target = rig.store.position(request.position_id)
        assert target["status"] == "OPEN" and target["qty_open"] == 5
        _assert_gross(rig, target)
        phases.append("before_actual_GTC_submission")
        result = original_submit(request)
        _assert_gross(rig, target)
        phases.append("after_actual_GTC_submission")
        return result
    monkeypatch.setattr(rig.broker, "submit_stop", observe_submission)
    late = _late_callback(rig, entry)
    pos, row = _assert_recovery_has_plan(rig, entry, closed, late)
    assert phases == ["before_actual_GTC_submission", "after_actual_GTC_submission"]
    assert pos["qty_open"] == 5 and pos["entry_price"] == 115.00
    assert row["intent_id"] != old["intent_id"]
    assert (row["trigger_price"], row["limit_price"]) == protection_prices(pos["stop"])
    assert row["order_type"] == "STP" and row["limit_price"] is None
    assert row["trigger_price"] != old["trigger_price"]
    assert len(rig.broker.sent_protections) == 2
    assert rig.broker.orders[row["intent_id"]].remaining == 5
    _assert_gross(rig, pos)
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    assert rig.store.protection(pos["position_id"])["state"] == "WORKING"
    _assert_gross(rig, pos)
    original_tp_submit = rig.broker.submit_limit
    def observe_TP_after_reduction(request):
        if request.purpose == Purpose.TP1:
            _assert_gross(rig, pos)
            assert rig.store.protection(pos["position_id"])["confirmed_remaining"] == 4
            phases.append("reduction_confirmed_before_TP_submission")
        return original_tp_submit(request)
    monkeypatch.setattr(rig.broker, "submit_limit", observe_TP_after_reduction)
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(r for r in rig.broker.sent if r.purpose == Purpose.TP1)
    assert tp.quantity == 1
    assert "reduction_confirmed_before_TP_submission" in phases
    _assert_gross(rig, pos)
    rig.broker.fill(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    _assert_gross(rig, pos)
    before = (len(rig.broker.sent_protections), len(rig.broker.modified_protections), len(rig.broker.cancels))
    rig.engine._ingest_fill(rig.clock.now(), late)  # repeated execDetails is evidence only
    assert (len(rig.broker.sent_protections), len(rig.broker.modified_protections), len(rig.broker.cancels)) == before
    assert rig.store.position(pos["position_id"])["qty_open"] == 4
    assert rig.store.protection(closed["position_id"])["state"] == "FILLED"


def test_recovery_GTC_PreSubmitted_is_a_working_confirmed_protection(tmp_path):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    rig.at(et(16, 30))  # late execution after the regular session has closed
    rig.broker.protection_auto_status = False
    late = _late_callback(rig, entry)
    pos, row = _assert_recovery_has_plan(rig, entry, closed, late)
    assert row["state"] == "SUBMIT_PENDING"
    rig.broker.status(row["intent_id"], "PreSubmitted")
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    current = rig.store.protection(pos["position_id"])
    assert current["state"] == "WORKING"
    assert current["confirmed_remaining"] == current["pending_max_remaining"] == 5
    assert rig.broker.orders[row["intent_id"]].status == "PreSubmitted"
    assert len(rig.broker.sent_protections) == 2 and row["intent_id"] != old["intent_id"]
    _assert_gross(rig, pos)


def test_late_entry_merged_into_existing_OPEN_uses_three_evidence_same_GTC_increase(tmp_path, monkeypatch):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    req = OrderRequest("v6l-current-open-entry", "AAPL", Side.BUY, 4, 113.47,
                       Purpose.ENTRY, "existing-open-position")
    assert rig.engine._entry_evidence() is None
    assert rig.engine._send(rig.clock.now(), req, atr=2.0, count_entry_day="2026-10-06") == "sent"
    rig.broker.fill(req.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    current_pos = rig.store.position(req.position_id)
    current = rig.store.protection(req.position_id)
    assert current["state"] == "WORKING" and current["confirmed_remaining"] == 4
    count_before = len(rig.broker.sent_protections)
    modifies_before = len(rig.broker.modified_protections)
    observed = []
    for name in ("positions_fresh", "open_orders", "executions_today"):
        original = getattr(rig.broker, name)
        def query(*args, _name=name, _original=original, **kwargs):
            observed.append(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, query)
    late = _late_callback(rig, entry)
    assert rig.store.intent(entry.intent_id)["recovery_position_id"] == req.position_id
    assert rig.store.position(req.position_id)["qty_open"] == 9
    assert len(rig.store.open_positions()) == 1
    assert rig.store.conn.execute("SELECT position_id FROM fills WHERE exec_id=?", (late.exec_id,)).fetchone()[0] == req.position_id
    row = rig.store.protection(req.position_id)
    assert row["gap_a_started_at"] is not None, "new routed ENTRY shares must invoke the existing GTC hook"
    assert row["confirmed_remaining"] == 4
    assert not observed and len(rig.broker.modified_protections) == modifies_before
    assert len(rig.broker.sent_protections) == count_before
    original_modify = rig.broker.modify_protection
    def guarded_modify(request):
        assert {"positions_fresh", "open_orders", "executions_today"} <= set(observed), observed
        assert request.intent_id == current["intent_id"]
        return original_modify(request)
    monkeypatch.setattr(rig.broker, "modify_protection", guarded_modify)
    rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    assert len(rig.broker.sent_protections) == count_before
    assert rig.broker.orders[current["intent_id"]].remaining == 9
    assert rig.store.protection(req.position_id)["confirmed_remaining"] == 9
    assert rig.store.protection(closed["position_id"])["state"] == "FILLED"
    _assert_gross(rig, current_pos)


@pytest.mark.parametrize("fault", ["zero_stop", "sub_dollar_stop"])
def test_invalid_booked_recovery_protection_evidence_pages_P0_without_GTC_and_blocks_entries(
        tmp_path, monkeypatch, fault):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    original_record = rig.store.record_fill
    def fault_in_booked_recovery(now, fill, **kwargs):
        outcome = original_record(now, fill, **kwargs)
        if fill.intent_id == entry.intent_id and outcome.inserted:
            pid = rig.store.intent(entry.intent_id)["recovery_position_id"]
            assert pid != closed["position_id"]
            value = 0.0 if fault == "zero_stop" else 0.5
            rig.store.conn.execute("UPDATE positions SET stop=? WHERE position_id=?", (value, pid))
        return outcome
    monkeypatch.setattr(rig.store, "record_fill", fault_in_booked_recovery)
    before = len(rig.channel.messages)
    count_before = len(rig.broker.sent_protections)
    late = _late_callback(rig, entry)
    pos = rig.store.open_positions()[0]
    assert pos["qty_open"] == 5 and pos["position_id"] != closed["position_id"]
    assert rig.store.conn.execute("SELECT position_id FROM fills WHERE exec_id=?", (late.exec_id,)).fetchone()[0] == pos["position_id"]
    titles = [title for title, _ in rig.channel.messages[before:]]
    assert any("P0" in title and "成交后无法建立有效保护价" in title for title in titles), titles
    assert len(rig.broker.sent_protections) == count_before
    assert rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    rig.engine.process_protection_priority()
    assert len(rig.broker.sent_protections) == count_before
    assert rig.store.protection(closed["position_id"])["state"] == "FILLED"


def test_restart_books_late_fill_and_replays_it_without_duplicate_GTC(tmp_path):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    late = rig.broker.fill(entry.intent_id, qty=5, price=115.0, emit=False)
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "recovery-restart")
    resumed.startup()
    rig.engine = resumed
    pos, row = _assert_recovery_has_plan(rig, entry, closed, late)
    rig.tick()
    assert len(rig.broker.sent_protections) == 2
    assert rig.broker.orders[row["intent_id"]].remaining == 5
    _assert_gross(rig, pos)
    fills_before = rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    resumed._ingest_fill(rig.clock.now(), late)
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == fills_before
    assert len(rig.broker.sent_protections) == 2
    restarted = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                       report_dir=tmp_path / "recovery-restart-again")
    restarted.startup()
    assert len(rig.broker.sent_protections) == 2
    assert rig.store.position(pos["position_id"])["qty_open"] == 5


def test_recovery_gap_A_deadlines_remain_five_and_fifteen_seconds_and_block_other_entries(tmp_path):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    rig.broker.protection_auto_status = False
    late = _late_callback(rig, entry)
    pos, row = _assert_recovery_has_plan(rig, entry, closed, late)
    start = datetime.fromisoformat(row["gap_a_started_at"])
    req = OrderRequest("v6l-tsla-while-recovery-unconfirmed", "TSLA", Side.BUY, 1, 250.0,
                       Purpose.ENTRY, "tsla-no-protection-confirm")
    assert rig.engine._entry_evidence() is None
    assert rig.engine._send(rig.clock.now(), req, atr=2.0, count_entry_day="2026-10-06").startswith(
        "refused:protection_unconfirmed:AAPL")
    assert req.intent_id not in rig.broker.orders and rig.store.intent(req.intent_id) is None
    before = len(rig.channel.messages)
    for seconds in (4, 5, 14, 15):
        rig.clock.set(start + timedelta(seconds=seconds))
        rig.engine._hard_protection_deadlines(rig.clock.now())
        titles = [title for title, _ in rig.channel.messages[before:]]
        assert sum("P1" in title and "保护单尚未确认" in title for title in titles) == int(seconds >= 5)
        assert sum("P0" in title and "未获券商保护" in title for title in titles) == int(seconds >= 15)
    assert len(rig.broker.sent_protections) == 2
    _assert_gross(rig, pos)

@pytest.mark.parametrize("atr", [0.0, None], ids=["zero", "missing"])
def test_invalid_original_ENTRY_ATR_rolls_back_late_booking_and_urgent_callback_pages_P0(
        tmp_path, atr):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    rig.store.conn.execute("UPDATE intents SET atr=? WHERE intent_id=?", (atr, entry.intent_id))
    fills_before = rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    count_before = len(rig.broker.sent_protections)
    before = len(rig.channel.messages)
    late = rig.broker.fill(entry.intent_id, qty=5, price=115.0, emit=False)
    with pytest.raises((TypeError, ValueError)):
        rig.engine._on_urgent_broker_event(late)
    assert not rig.store.open_positions()
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == fills_before
    assert rig.store.conn.execute("SELECT 1 FROM fills WHERE exec_id=?", (late.exec_id,)).fetchone() is None
    assert len(rig.broker.sent_protections) == count_before
    assert rig.engine._protection_emergency
    assert any("P0" in title and "成交回调处理失败" in title
               for title, _ in rig.channel.messages[before:])
    req = OrderRequest("v6l-blocked-after-bad-recovery-ATR", "TSLA", Side.BUY, 1, 250.0,
                       Purpose.ENTRY, "bad-ATR-must-block-new-risk")
    assert rig.engine._send(rig.clock.now(), req, atr=2.0, count_entry_day="2026-10-06") == (
        "refused:protection_callback_failure")
    assert req.intent_id not in rig.broker.orders and rig.store.intent(req.intent_id) is None
    assert rig.store.protection(closed["position_id"])["state"] == "FILLED"
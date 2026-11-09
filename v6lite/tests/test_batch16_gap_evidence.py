"""Batch 16 gap timing and daily-evidence regressions; broker data is synthetic.

Extracted from the post-review Batch 15 test backup. These tests use the
FakeBroker and local Store only; no Gateway connection is attempted.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from types import SimpleNamespace
import json
import logging

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.config import ConfigError, load_config
from v6lite.models import Side
from v6lite.tests.helpers import Rig, et


def _gap_audits(rig: Rig, kind: str):
    return [json.loads(event["payload"])
            for event in rig.store.audit_for_day(
                et(0).isoformat(), et(0, day=(2026, 10, 7)).isoformat(),
                ("PROTECT_GAP",))
            if json.loads(event["payload"]).get("gap_kind") == kind]


def test_L7_disabled_protection_priority_cannot_starve_normal_scan_and_entry(
        tmp_path):
    """A stale L7 priority from recovery must not short-circuit a legacy tick."""
    rig = Rig(tmp_path)
    if hasattr(rig.cfg, "protection"):
        assert rig.cfg.protection.enabled is False
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.store.control("scan:2026-10-06:10:00") is not None
    buys = [req for req in rig.broker.sent if req.side is Side.BUY]
    assert len(buys) == 1 and buys[0].symbol == "AAPL"


def test_L7_dry_run_stale_priority_cannot_starve_scan_or_WOULD_BUY(tmp_path):
    rig = Rig(tmp_path, dry_run=True)
    rig.start()
    # A stale in-memory flag may survive a callback; dry-run still has no
    # protection broker writes and must execute its scheduled scan.
    rig.engine._protection_priority_due = True
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.store.control("scan:2026-10-06:10:00") is not None
    dry_rows = rig.store.conn.execute(
        "SELECT COUNT(*) FROM audit WHERE kind='DRY_RUN'").fetchone()[0]
    assert dry_rows >= 1
    assert rig.broker.sent == []


def test_L7_A_gap_audit_uses_clock_after_slow_three_source_confirmation(tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    _entry_and_fill(rig)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    started = datetime.fromisoformat(row["gap_a_started_at"])
    rig.advance(seconds=1)
    original = rig.broker.executions_today
    slow = []

    def delayed_executions():
        if not slow:
            slow.append(True)
            rig.clock.advance(seconds=4)
        return original()

    monkeypatch.setattr(rig.broker, "executions_today", delayed_executions)
    rig.broker.status(row["intent_id"], "Submitted")
    rig.engine._drain(rig.clock.now())
    audits = _gap_audits(rig, "A")
    assert slow and len(audits) == 1
    actual = (rig.clock.now() - started).total_seconds()
    assert audits[0]["secs"] == pytest.approx(actual, abs=0.001)
    assert audits[0]["secs"] >= 5


def test_L7_B_restoration_audit_uses_clock_after_slow_three_source_confirmation(
        tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos = rig.store.open_positions()[0]
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=10)
    rig.tick()
    rig.broker.protection_auto_status = False
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "RESTORE_PENDING"
    started = datetime.fromisoformat(row["gap_b_started_at"])
    rig.advance(seconds=1)
    original = rig.broker.executions_today
    slow = []

    def delayed_executions():
        if not slow:
            slow.append(True)
            rig.clock.advance(seconds=4)
        return original()

    monkeypatch.setattr(rig.broker, "executions_today", delayed_executions)
    rig.broker.confirm_protection_modify(row["intent_id"])
    rig.engine._drain(rig.clock.now())
    audits = _gap_audits(rig, "B")
    assert slow and len(audits) == 1
    assert audits[0]["secs"] == pytest.approx(
        (rig.clock.now() - started).total_seconds(), abs=0.001)


def test_L7_B_TP_fill_audit_ends_at_execDetails_receipt_not_later_local_processing(
        tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos = rig.store.open_positions()[0]
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    row = rig.store.protection(pos["position_id"])
    started = datetime.fromisoformat(row["gap_b_started_at"])
    original = rig.engine._protection_after_tp_fill

    def delayed_close(received_at, position_id):
        rig.clock.advance(seconds=4)
        return original(received_at, position_id)

    monkeypatch.setattr(rig.engine, "_protection_after_tp_fill", delayed_close)
    rig.advance(seconds=2)
    received_at = rig.clock.now()
    rig.broker.fill(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    audits = _gap_audits(rig, "B")
    assert len(audits) == 1
    assert audits[0]["secs"] == pytest.approx(
        (received_at - started).total_seconds(), abs=0.001)


def test_L7_priority_callback_during_evidence_must_finish_TP_before_return(
        tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos = rig.store.open_positions()[0]
    rig.broker.protection_auto_status = False
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "REDUCE_PENDING"
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    original = rig.broker.executions_today
    injected = []

    def evidence_with_status_callback():
        if not injected:
            injected.append(True)
            rig.clock.advance(seconds=3)
            rig.broker.confirm_protection_modify(row["intent_id"])
            for event in rig.broker.drain_events():
                rig.engine._on_urgent_broker_event(event)
        return original()

    monkeypatch.setattr(rig.broker, "executions_today", evidence_with_status_callback)
    rig.engine._protection_priority_due = True
    rig.engine.process_protection_priority()
    assert injected
    assert len([req for req in rig.broker.sent if req.side is Side.SELL]) == 1, (
        "urgent orderStatus during evidence must not be lost by progress finally; "
        "the same priority phase must submit TP without engine.tick()")


def test_L7_daily_health_reports_open_cross_day_gap_elapsed_not_zero(tmp_path):
    from v6lite.report import daily_health_report

    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    _entry_and_fill(rig, quantity=1)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    assert row["gap_a_started_at"] is not None
    # No closed PROTECT_GAP row exists. The window continues into the next day.
    rig.at(et(10, 0, day=(2026, 10, 7)))
    elapsed = (rig.clock.now() - datetime.fromisoformat(
        row["gap_a_started_at"])).total_seconds()
    text = daily_health_report(
        rig.store, rig.cfg, date(2026, 10, 7),
        status={"broker_connected": False, "reconcile_ok": False,
                "alerts_healthy": True, "open_positions": [("AAPL", 1)]},
        gateway={"replies": 0}, alerts={"sent": 0, "failed": 0},
        quotes={"samples": 0, "total": 0, "usable": 0, "subscribed": 0})
    protection_line = next(line for line in text.splitlines() if "broker protection" in line)
    assert any(token in protection_line for token in ("进行中", "未闭合"))
    assert any(token in protection_line for token in ("未核实", "待核实"))
    assert f"{elapsed:.3f}s" in protection_line
    assert "A=0.000s" not in protection_line


def test_L7_real_adapter_execDetails_submits_first_GTC_before_callback_returns(
        tmp_path, monkeypatch):
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.tests.test_ibkr_adapter import FakeIB

    rig = _protected_rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    fake_fill = rig.broker.fill(buy.intent_id, qty=1, emit=False)
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = FakeIB()
    adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
    for name in ("positions_fresh", "open_orders", "executions_today"):
        monkeypatch.setattr(
            rig.broker, name,
            lambda _name=name: pytest.fail(
                f"nested synchronous {_name} broker query inside execDetails callback"))
    fill = SimpleNamespace(
        execution=SimpleNamespace(
            acctNumber=rig.cfg.account, execId=fake_fill.exec_id,
            orderRef=buy.intent_id, side="BOT", shares=1,
            price=fake_fill.price, time=fake_fill.time,
            orderId=fake_fill.broker_order_id,
            permId=fake_fill.broker_order_id + 50_000),
        contract=SimpleNamespace(symbol="AAPL"))
    adapter._on_exec(None, fill)
    assert len(rig.broker.sent_protections) == 1
    assert rig.broker.sent_protections[0].quantity == 1
    assert rig.store.protection(rig.store.open_positions()[0]["position_id"])["state"] == "SUBMIT_PENDING"


def test_L7_custom_four_gap_deadlines_change_fingerprint_and_real_A_B_actions(
        tmp_path):
    from v6lite.config import ProtectionConfig

    deadlines = ProtectionConfig(gap_a_p1_seconds=2, gap_a_p0_seconds=4,
                                 gap_b_cancel_seconds=3, gap_b_p0_seconds=6)
    config_file = tmp_path / "custom.toml"
    config_file.write_text(
        'mode="paper"\n[ibkr]\naccount="DU0000000"\nport=4002\n'
        '[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n'
        '[protection]\ngap_a_p1_seconds=2\ngap_a_p0_seconds=4\n'
        'gap_b_cancel_seconds=3\ngap_b_p0_seconds=6\n', encoding="utf-8")
    loaded = load_config(config_file)
    assert loaded.protection == deadlines
    rig = Rig(tmp_path / "A", protection=deadlines)
    for name in ("gap_a_p1_seconds", "gap_a_p0_seconds",
                 "gap_b_cancel_seconds", "gap_b_p0_seconds"):
        changed = replace(rig.cfg, protection=replace(
            deadlines, **{name: getattr(deadlines, name) + 1}))
        assert changed.fingerprint() != rig.cfg.fingerprint()
    rig.broker.protection_auto_status = False
    _entry_and_fill(rig, quantity=1)
    rig.advance(seconds=2)
    rig.engine._hard_protection_deadlines(rig.clock.now())
    assert "P1" in rig.messages(), repr(rig.channel.messages)
    rig.advance(seconds=2)
    rig.engine._hard_protection_deadlines(rig.clock.now())
    assert "P0" in rig.messages(), repr(rig.channel.messages)

    other = Rig(tmp_path / "B", protection=deadlines)
    _entry_and_fill(other)
    pos = other.store.open_positions()[0]
    other.px("AAPL", pos["tp1"] + 1)
    other.advance(seconds=31)
    other.tick()
    tp = next(req for req in other.broker.sent if req.side is Side.SELL)
    other.advance(seconds=3)
    other.engine._hard_protection_deadlines(other.clock.now())
    assert tp.intent_id in other.broker.cancels
    other.advance(seconds=3)
    other.engine._hard_protection_deadlines(other.clock.now())
    assert "P0" in other.messages(), repr(other.channel.messages)


def test_L7_overlapping_partial_BUY_fills_have_independent_A_gap_audits(
        tmp_path):
    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    buy = _entry_and_fill(rig, quantity=1)
    first = rig.broker.orders[buy.intent_id].fills[0]
    first_start = datetime.fromisoformat(
        rig.store.protection(rig.store.open_positions()[0]["position_id"])[
            "gap_a_started_at"])  # callback receipt, not FakeBroker's queued fill time
    rig.advance(seconds=2)
    second = rig.broker.fill(buy.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    rig.advance(seconds=3)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    rig.broker.status(row["intent_id"], "Submitted")
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.engine._protection_progress(rig.clock.now())
    if row["intent_id"] in rig.broker.protection_pending_modifications:
        rig.broker.confirm_protection_modify(row["intent_id"])
        rig.engine._drain(rig.clock.now())
        rig.engine.process_protection_priority()
    audits = _gap_audits(rig, "A")
    current = rig.store.protection(row["position_id"])
    assert current["confirmed_remaining"] == 2, (
        current["state"], current["pending_qty"], current["confirmed_remaining"],
        current["pending_max_remaining"], current["gap_a_started_at"],
        len(rig.broker.modified_protections), rig.store.open_positions()[0]["qty_open"])
    by_exec = {event.get("exec_id"): event for event in audits}
    assert first.exec_id in by_exec and second.exec_id in by_exec
    assert by_exec[first.exec_id]["secs"] == pytest.approx(
        (rig.clock.now() - first_start).total_seconds(), abs=0.001)
    assert by_exec[second.exec_id]["secs"] == pytest.approx(
        (rig.clock.now() - second.time).total_seconds(), abs=0.001)
    assert by_exec[first.exec_id]["secs"] > by_exec[second.exec_id]["secs"]
    from v6lite.report import daily_health_report

    text = daily_health_report(
        rig.store, rig.cfg, date(2026, 10, 6),
        status={"broker_connected": True, "reconcile_ok": True,
                "alerts_healthy": True, "open_positions": [("AAPL", 2)]},
        gateway={"replies": 1}, alerts={"sent": 1, "failed": 0},
        quotes={"samples": 1, "total": 1, "usable": 1, "subscribed": 1})
    assert f"A={by_exec[first.exec_id]['secs']:.3f}s" in text


def test_L7_schema4_legacy_open_A_gap_survives_new_partial_fill_and_sets_daily_max(
        tmp_path):
    """A pre-upgrade open window has no per-exec row; an added row must not hide it."""
    from functools import partial
    from v6lite.exits import compute_levels
    from v6lite.report import daily_health_report
    from v6lite.store import Store

    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    buy = _entry_and_fill(rig, quantity=1)
    pos = rig.store.open_positions()[0]
    old = rig.store.protection(pos["position_id"])
    t0 = datetime.fromisoformat(old["gap_a_started_at"])
    assert rig.store.meta("schema_version") == "4"
    # The old schema-4 file persisted only protections.gap_a_started_at.
    # Remove solely the later additive table from this synthetic SQLite file,
    # then reopen through the actual Store initializer to create that table.
    db_path = rig.store.path
    rig.store.conn.execute("DROP TABLE protection_gap_a")
    rig.store.close()
    rig.store = Store(db_path, partial(compute_levels, rig.cfg))
    rig.engine.store = rig.store
    assert rig.store.open_protection_gaps_a() == []
    assert rig.store.protection(pos["position_id"])["gap_a_started_at"] == t0.isoformat()
    rig.advance(seconds=2)
    second = rig.broker.fill(buy.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    t1 = datetime.fromisoformat(rig.store.open_protection_gaps_a()[0]["started_at"])
    assert t1 > t0
    rig.advance(seconds=3)
    row = rig.store.protection(pos["position_id"])
    rig.broker.status(row["intent_id"], "Submitted")
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.engine._protection_progress(rig.clock.now())
    assert row["intent_id"] in rig.broker.protection_pending_modifications
    rig.broker.confirm_protection_modify(row["intent_id"])
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    current = rig.store.protection(pos["position_id"])
    assert current["confirmed_remaining"] == 2
    audits = _gap_audits(rig, "A")
    legacy = [item for item in audits if item.get("legacy_window") is True]
    newest = [item for item in audits if item.get("exec_id") == second.exec_id]
    assert len(legacy) == len(newest) == 1, audits
    assert legacy[0]["secs"] == pytest.approx((rig.clock.now() - t0).total_seconds())
    assert newest[0]["secs"] == pytest.approx((rig.clock.now() - t1).total_seconds())
    assert legacy[0]["secs"] > newest[0]["secs"]
    text = daily_health_report(
        rig.store, rig.cfg, date(2026, 10, 6),
        status={"broker_connected": True, "reconcile_ok": True,
                "alerts_healthy": True, "open_positions": [("AAPL", 2)]},
        gateway={"replies": 1}, alerts={"sent": 1, "failed": 0},
        quotes={"samples": 1, "total": 1, "usable": 1, "subscribed": 1})
    assert f"A={legacy[0]['secs']:.3f}s" in text


def test_L7_historical_daily_gap_excludes_future_and_caps_ongoing_at_day_end(
        tmp_path):
    from v6lite.report import daily_health_report

    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    _entry_and_fill(rig, quantity=1)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    t0 = datetime.fromisoformat(row["gap_a_started_at"])
    # A later day's separate open execution must not contaminate Oct 6.
    rig.store.conn.execute(
        "INSERT INTO protection_gap_a(exec_id,position_id,started_at) VALUES (?,?,?)",
        ("future-exec", "future-pos", et(9, 0, day=(2026, 10, 8)).isoformat()))
    rig.at(et(12, 0, day=(2026, 10, 8)))
    status = {"broker_connected": False, "reconcile_ok": False,
              "alerts_healthy": True, "open_positions": [("AAPL", 1)]}
    context = dict(status=status, gateway={"replies": 0},
                   alerts={"sent": 0, "failed": 0},
                   quotes={"samples": 0, "total": 0, "usable": 0, "subscribed": 0})
    historical = daily_health_report(rig.store, rig.cfg, date(2026, 10, 6), **context)
    line = next(part for part in historical.splitlines() if "broker protection" in part)
    day_end = et(0, 0, day=(2026, 10, 7))
    expected = (day_end - t0).total_seconds()
    assert f"A={expected:.3f}s" in line
    assert "future-pos" not in line
    before_start = daily_health_report(rig.store, rig.cfg, date(2026, 10, 5), **context)
    before_line = next(part for part in before_start.splitlines() if "broker protection" in part)
    assert "A=0.000s" in before_line
    assert "空窗进行中" not in before_line


@pytest.mark.parametrize("gap_kind", ["A", "B"])
def test_L7_historical_daily_counts_gap_closed_after_midnight(tmp_path, gap_kind):
    from v6lite.report import daily_health_report

    rig = _protected_rig(tmp_path)
    # Production's durable closed-gap shape: end timestamp plus elapsed secs.
    # The window began 23:59 ET on Oct 6 and ended 00:10 ET on Oct 7.
    rig.store.audit(et(0, 10, day=(2026, 10, 7)),
                    "PROTECT_GAP", gap_kind=gap_kind, secs=660.0,
                    symbol="AAPL", intent_id="synthetic-cross-midnight")
    rig.at(et(12, 0, day=(2026, 10, 8)))
    text = daily_health_report(
        rig.store, rig.cfg, date(2026, 10, 6),
        status={"broker_connected": False, "reconcile_ok": True,
                "alerts_healthy": True, "open_positions": []},
        gateway={"replies": 0}, alerts={"sent": 0, "failed": 0},
        quotes={"samples": 0, "total": 0, "usable": 0, "subscribed": 0})
    line = next(part for part in text.splitlines() if "broker protection" in part)
    # Either count the 60-second overlap or explicitly flag it unverified.
    import re
    match = re.search(("B（正常）最大 " if gap_kind == "B" else "A=")+r"([0-9]+(?:\.[0-9]+)?)s", line)
    assert match is not None
    assert float(match.group(1)) >= 60 or "跨日未证实" in line


@pytest.mark.parametrize("gap_kind", ["A", "B"])
def test_L7_G3_cross_midnight_six_second_gap_counts_full_window_on_both_days(
        tmp_path, gap_kind):
    """G3's ≤5s gate cannot split one six-second risk window into two threes."""
    from v6lite.report import daily_health_report

    rig = _protected_rig(tmp_path)
    # 2026-10-06 23:59:57 ET → 2026-10-07 00:00:03 ET.
    rig.store.audit(et(0, 0, 3, day=(2026, 10, 7)),
                    "PROTECT_GAP", gap_kind=gap_kind, secs=6.0,
                    symbol="AAPL", intent_id="synthetic-g3-cross-midnight")
    rig.at(et(12, 0, day=(2026, 10, 8)))
    for report_day in (date(2026, 10, 6), date(2026, 10, 7)):
        text = daily_health_report(
            rig.store, rig.cfg, report_day,
            status={"broker_connected": False, "reconcile_ok": True,
                    "alerts_healthy": True, "open_positions": []},
            gateway={"replies": 0}, alerts={"sent": 0, "failed": 0},
            quotes={"samples": 0, "total": 0, "usable": 0, "subscribed": 0})
        line = next(part for part in text.splitlines() if "broker protection" in part)
        import re
        match = re.search(("B（正常）最大 " if gap_kind == "B" else "A=")+r"([0-9]+(?:\.[0-9]+)?)s", line)
        assert match is not None
        assert float(match.group(1)) >= 6.0, (gap_kind, report_day, line)


def test_L7_recovered_old_BUY_fill_cannot_be_reported_as_short_verified_A_gap(
        tmp_path):
    from v6lite.engine import Engine
    from v6lite.report import daily_health_report

    rig = _protected_rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    old_fill = rig.broker.fill(buy.intent_id, qty=1, emit=False)
    rig.advance(minutes=10)
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "recovery-report")
    rig.engine = resumed
    resumed.startup()
    assert rig.store.position(buy.position_id)["qty_open"] == 1
    # Broker status and three-source confirmation may require another priority
    # cycle, but the delayed execution's real time is already known.
    resumed.process_protection_priority()
    resumed._drain(rig.clock.now())
    resumed._protection_progress(rig.clock.now())
    row = rig.store.protection(buy.position_id)
    assert row is not None
    records = [item for item in _gap_audits(rig, "A")
               if item.get("exec_id") == old_fill.exec_id]
    open_rows = [item for item in rig.store.open_protection_gaps_a()
                 if item["exec_id"] == old_fill.exec_id]
    assert records or open_rows
    if records:
        record = records[0]
        assert record["secs"] >= 600 or record.get("recovered_unverified") is True
    else:
        started = datetime.fromisoformat(open_rows[0]["started_at"])
        assert ((rig.clock.now() - started).total_seconds() >= 600 or
                rig.store.control(f"recovered_gap_unverified:{old_fill.exec_id}") is not None)
    report = daily_health_report(
        rig.store, rig.cfg, date(2026, 10, 6),
        status={"broker_connected": True, "reconcile_ok": True,
                "alerts_healthy": True, "open_positions": [("AAPL", 1)]},
        gateway={"replies": 1}, alerts={"sent": 1, "failed": 0},
        quotes={"samples": 1, "total": 1, "usable": 1, "subscribed": 1})
    line = next(part for part in report.splitlines() if "broker protection" in part)
    import re
    match = re.search(r"A=([0-9]+(?:\.[0-9]+)?)s", line)
    assert match is not None
    assert float(match.group(1)) >= 600 or any(
        marker in line for marker in ("回补", "起点待核实", "未验证"))


def test_L7_actual_A_B_gap_audits_feed_daily_max_and_log(tmp_path, caplog):
    from v6lite.report import daily_health_report

    caplog.set_level(logging.INFO, logger="v6lite.protection")
    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    _entry_and_fill(rig)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    rig.advance(seconds=2)
    rig.broker.status(row["intent_id"], "Submitted")
    rig.engine._drain(rig.clock.now())
    assert len(_gap_audits(rig, "A")) == 1
    rig.broker.protection_auto_status = True
    pos = rig.store.open_positions()[0]
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=3)
    rig.broker.fill(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    a = _gap_audits(rig, "A")
    b = _gap_audits(rig, "B")
    assert len(a) == len(b) == 1
    assert "[PROTECT_GAP] kind=A" in caplog.text
    assert "[PROTECT_GAP] kind=B" in caplog.text
    text = daily_health_report(
        rig.store, rig.cfg, date(2026, 10, 6),
        status={"broker_connected": True, "reconcile_ok": True,
                "alerts_healthy": True, "open_positions": [("AAPL", pos["qty_open"])]},
        gateway={"replies": 1}, alerts={"sent": 1, "failed": 0},
        quotes={"samples": 1, "total": 1, "usable": 1, "subscribed": 1})
    assert f"A={a[0]['secs']:.3f}s" in text
    assert f"B（正常）最大 {b[0]['secs']:.3f}s" in text


def test_L7_entry_preplan_is_durable_before_broker_BUY_write(tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path)
    original = rig.broker.submit_limit
    observed = []

    def require_plan(req):
        if req.side is Side.BUY:
            row = rig.store.protection(req.position_id)
            assert row is not None and row["state"] == "PLANNED"
            assert row["intent_id"].startswith("v6l-")
            assert row["account"] == rig.cfg.account
            assert row["client_id"] == rig.cfg.client_id
            assert row["con_id"] > 0
            observed.append(row["intent_id"])
        return original(req)

    monkeypatch.setattr(rig.broker, "submit_limit", require_plan)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert len(observed) == 1


def _protected_rig(tmp_path, **overrides):
    from v6lite.config import ProtectionConfig

    return Rig(tmp_path, protection=ProtectionConfig(), **overrides)


def _entry_and_fill(rig: Rig, *, quantity: int | None = None):
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    rig.broker.fill(buy.intent_id, qty=quantity)
    rig.advance(seconds=1)
    rig.tick()
    return buy


def _protection_orders(rig: Rig):
    return rig.broker.sent_protections


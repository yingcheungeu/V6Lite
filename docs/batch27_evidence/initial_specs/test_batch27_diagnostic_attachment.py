"""Real PROTECT price diagnostics remain complete in the linked attachment.

Both normal and diagnostic ORDER_PRICE_OBSERVATION rows are summary-only in
main Events. Diagnostic rows must retain each original audit identity in the
actual exported attachment; a PROTECT observation has no TP_EVIDENCE copy.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

from v6lite.report import write_daily_report
from v6lite.tests.helpers import et
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _safety, _writes
from v6lite.tests.test_batch27_observation_audit import observation_rig, _observe, START
from v6lite.tests.test_batch27_critical_attachments import _attachment, _bounded
from v6lite.tests.test_batch27_daily_report_volume import _audit_digest, _table_line


def _order_rows(store):
    return [dict(row) for row in store.conn.execute(
        "SELECT id,ts,kind,payload FROM audit WHERE kind='ORDER_PRICE_OBSERVATION' ORDER BY id")]


def test_B27_real_PROTECT_diagnostics_export_every_audit_identity_while_main_stays_summary_only(
        observation_rig, record_property):
    rig = observation_rig()
    pid = rig._b27_pids[0]
    ref = rig.store.protection(pid)["intent_id"]
    assert rig.store.intent(ref)["purpose"] == "PROTECT"
    writes = _writes(rig)
    tp_before = rig.store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind='TP_EVIDENCE'").fetchone()[0]
    _observe(rig)
    rig.advance(seconds=1)
    _observe(rig)
    normal = _order_rows(rig.store)
    assert normal and all(json.loads(row["payload"])["error_code"] == 0 and
        json.loads(row["payload"])["reason"] == "" for row in normal)
    normal_ids = {row["id"] for row in normal}
    reason = "Synthetic PROTECT broker diagnostic；完整原因🍎；END-B27-PROTECT-DIAGNOSTIC"
    for kwargs in ({"error_code": 399}, {"reason": reason}):
        for _ in range(2):
            rig.advance(seconds=1)
            event = _observe(rig, **kwargs)
            assert event.identity.intent_id == ref
    rows = _order_rows(rig.store)
    diagnostics = [row for row in rows if row["id"] not in normal_ids]
    assert len(diagnostics) == 4, "each repeated real error/reason observation must remain durable"
    assert sum(json.loads(row["payload"])["error_code"] == 399 for row in diagnostics) == 2
    assert sum(json.loads(row["payload"])["reason"] == reason for row in diagnostics) == 2
    assert rig.store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind='TP_EVIDENCE'").fetchone()[0] == tp_before
    before = _audit_digest(rig.store)
    path, text = write_daily_report(rig.store, rig.cfg, START.date(), rig.engine.report_dir)
    section, size, lines = _bounded(text)
    assert not re.search(r"^- \d{2}:\d{2}:\d{2}Z ORDER_PRICE_OBSERVATION\b", section, re.MULTILINE), (
        "all ORDER observations are statistics-only in the bounded main report")
    table = _table_line(text, "ORDER_PRICE_OBSERVATION")
    assert table and re.search(r"\|\s*" + str(len(rows)) + r"\s*\|", table), (
        "main report must retain the exact persisted observation count", len(rows))
    records, digest = _attachment(path, text)
    exported = {row["id"]: row for row in records}
    assert normal_ids.isdisjoint(exported), "normal no-diagnostic observations remain statistics-only"
    for source in diagnostics:
        actual = exported.get(source["id"])
        assert actual is not None, ("missing diagnostic audit identity", source["id"])
        assert (actual["ts"], actual["kind"], actual["payload"]) == (
            source["ts"], source["kind"], source["payload"]), (
            "diagnostic attachment must preserve exact original timestamp/kind/raw payload", source["id"])
    assert _audit_digest(rig.store) == before
    assert _writes(rig) == writes
    assert rig.store.protection(pid)["state"] == "WORKING"
    _safety(rig)
    record_property("b27_real_diagnostic_attachment", json.dumps({
        "normal_observation_rows": len(normal), "diagnostic_observation_rows": len(diagnostics),
        "tp_evidence_copies_added": 0, "main_bytes": size, "events_lines": lines,
        "attachment_records": len(records), "attachment_sha256": digest}, sort_keys=True))


def test_B27_late_commission_correction_and_time_review_keep_today_receive_day_not_old_fill_day(
        observation_rig, record_property):
    rig = observation_rig()
    fill_before = dict(rig.store.conn.execute("SELECT f.* FROM fills f JOIN intents i "
        "ON i.intent_id=f.intent_id WHERE i.purpose='ENTRY' ORDER BY f.time LIMIT 1").fetchone())
    original_fill = rig.broker.orders[fill_before["intent_id"]].fills[0]
    assert original_fill.exec_id == fill_before["exec_id"]
    rig.store.record_commission(rig.clock.now(), original_fill.exec_id, 1.0)
    writes = _writes(rig)
    rig.at(et(7, 16, 41, day=(2026, 10, 16)))
    now = rig.clock.now()
    assert fill_before["trading_day"] == "2026-10-15"
    assert rig.store.record_commission(now, original_fill.exec_id, 1.02)
    # An actually replayed complete query claims a different execution day.
    # Store must record a review, never re-date/rebook the older known fill.
    replay = replace(original_fill, time=now, time_source="reqExecutions", time_uncertain=False,
        raw_time=now.strftime("%Y%m%d %H:%M:%S UTC"))
    rig.store.record_fill(now, replay)
    after = dict(rig.store.conn.execute("SELECT * FROM fills WHERE exec_id=?", (original_fill.exec_id,)).fetchone())
    assert after == fill_before
    expected = [dict(row) for row in rig.store.conn.execute(
        "SELECT id,ts,kind,payload FROM audit WHERE kind IN "
        "('COMMISSION_CORRECTED','EXECUTION_TIME_REVIEW_REQUIRED') AND "
        "json_extract(payload,'$.exec_id')=? ORDER BY id", (original_fill.exec_id,))]
    assert len(expected) == 2 and {row["kind"] for row in expected} == {
        "COMMISSION_CORRECTED", "EXECUTION_TIME_REVIEW_REQUIRED"}
    assert all(row["ts"] == now.isoformat() for row in expected)
    before = _audit_digest(rig.store)
    path, text = write_daily_report(rig.store, rig.cfg, now.date(), rig.engine.report_dir)
    section, size, lines = _bounded(text)
    for source in expected:
        assert any(" " + source["kind"] + " " in line and original_fill.exec_id in line
            for line in section.splitlines()), ("today correction/review hidden by older ledger date", source["kind"])
    records, digest = _attachment(path, text)
    exported = {row["id"]: row for row in records}
    for source in expected:
        actual = exported.get(source["id"])
        assert actual is not None, ("today correction/review missing from complete attachment", source["kind"])
        assert (actual["ts"], actual["kind"], actual["payload"]) == (
            source["ts"], source["kind"], source["payload"])
    assert _audit_digest(rig.store) == before
    assert dict(rig.store.conn.execute("SELECT * FROM fills WHERE exec_id=?", (original_fill.exec_id,)).fetchone()) == fill_before
    assert _writes(rig) == writes
    _safety(rig)
    record_property("b27_old_fill_today_diagnostics", json.dumps({
        "original_ledger_day": fill_before["trading_day"], "diagnostic_receive_day": now.date().isoformat(),
        "diagnostic_rows": len(expected), "main_bytes": size, "events_lines": lines,
        "attachment_sha256": digest}, sort_keys=True))

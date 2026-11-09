"""Batch27 historical-report contracts; every database is synthetic test data.

The 90k-row shape and exact price payload come from the sanitized 10/15
statistics, not a copied operator database.  Timing starts after construction.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import tracemalloc
from datetime import timedelta
from functools import partial

import pytest

from v6lite.exits import compute_levels
from v6lite.report import daily_report, daily_health_report
from v6lite.store import Store
from v6lite.tests.helpers import make_cfg
from v6lite.tests.test_batch27_observation_audit import START, SAMPLE

DAY = START.date()
KINDS = ("ORDER_PRICE_OBSERVATION", "PROTECT_STATE")


def _audit_digest(store):
    digest = hashlib.sha256()
    for row in store.conn.execute("SELECT id,ts,kind,payload FROM audit ORDER BY id"):
        digest.update(json.dumps(tuple(row), ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _events(report):
    assert "## Events" in report, "report must retain a readable Events section"
    section = report.split("## Events", 1)[1]
    return section.split("\n## ", 1)[0]


def _table_line(report, kind):
    for line in report.splitlines():
        if line.lstrip().startswith("|") and kind in line:
            return line
    return ""


@pytest.fixture
def historical_store(tmp_path):
    opened = []
    def make(count=90000, *, name="history", important=True):
        cfg = make_cfg(tmp_path / name)
        store = Store(tmp_path / (name + ".sqlite3"), partial(compute_levels, cfg))
        opened.append(store)
        store.bind_identity(cfg.account, cfg.mode, client_id=cfg.client_id)
        def entries():
            for index in range(count):
                kind = KINDS[index % 2]
                ts = START + timedelta(seconds=index * (13 * 3600 - 1) / max(1, count - 1))
                payload = SAMPLE if kind == KINDS[0] else {
                    "position_id": SAMPLE["position_id"], "confirmed_at": ts.isoformat()}
                yield ts.isoformat(), kind, json.dumps(payload, sort_keys=True)
        with store.tx():
            store.conn.executemany("INSERT INTO audit(ts,kind,payload) VALUES (?,?,?)", entries())
        for index in range(5 if important else 0):
            store.audit(START + timedelta(minutes=index), "TP_EVIDENCE",
                phase="status", marker="B27-important-TP-" + str(index),
                position_id=SAMPLE["position_id"], intent_id="v6l-tp-synthetic",
                symbol="AAPL", reason="synthetic preserved evidence " + str(index))
        if important:
            store.audit(START + timedelta(minutes=6), "SCAN",
                        marker="B27-important-SCAN", signals=["AAPL"], no_trades=True)
        return store, cfg
    yield make
    for store in opened:
        store.close()


def test_B27_90k_legacy_report_is_bounded_preserves_important_events_and_history(
        historical_store, record_property):
    store, cfg = historical_store()
    before = _audit_digest(store)
    report = daily_report(store, cfg, DAY)
    event_lines = _events(report).splitlines()
    record_property("b27_legacy_event_lines", len(event_lines))
    record_property("b27_legacy_report_bytes", len(report.encode("utf-8")))
    assert len(event_lines) <= 200, ("Events lines", len(event_lines))
    size = len(report.encode("utf-8"))
    assert size <= 100 * 1024, ("report bytes", size)
    for index in range(5):
        visible = "B27-important-TP-" + str(index) in report
        assert visible, ("missing TP", index)
    scan_visible = "B27-important-SCAN" in report
    assert scan_visible, "the one scan must survive historical-volume compression"
    for kind in KINDS:
        line = _table_line(report, kind)
        assert line, ("missing kind summary", kind)
        assert re.search(r"\b45,?000\b", line), ("wrong kind count", kind, line[:180])
        assert "11:16:" in line, ("missing first UTC time", kind, line[:180])
    unmasked, suffix = cfg.account in report, cfg.account[-3:] in report
    assert not unmasked and suffix, "header must mask, not erase, the account"
    assert _audit_digest(store) == before, "rendering may not modify any historical audit row"


def _measured_report(store, cfg):
    row_count = [0]
    old = store.conn.row_factory
    def counting(cursor, row):
        row_count[0] += 1
        return sqlite3.Row(cursor, row)
    store.conn.row_factory = counting
    tracemalloc.start()
    started = time.perf_counter()
    try:
        report = daily_report(store, cfg, DAY)
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
        store.conn.row_factory = old
    return len(report.encode("utf-8")), elapsed, peak, row_count[0], len(_events(report).splitlines())


def test_B27_old_report_90k_under_five_seconds_and_python_memory_not_linear(
        historical_store, record_property):
    small, small_cfg = historical_store(10000, name="small")
    large, large_cfg = historical_store(90000, name="large")
    a = _measured_report(small, small_cfg)
    b = _measured_report(large, large_cfg)
    for size, value in ((10000, a), (90000, b)):
        record_property("b27_report_" + str(size), json.dumps(dict(zip(
            ("bytes", "seconds", "python_peak_bytes", "materialized_rows", "events_lines"), value)),
            sort_keys=True))
    assert b[1] <= 5.0, ("90k seconds", round(b[1], 4))
    assert b[3] <= 2048, ("materialized rows", b[3])
    assert b[2] <= 2 * a[2] + 2 * 1024 * 1024, ("Python peak bytes 10k/90k", a[2], b[2])
    assert b[0] <= 100 * 1024, ("90k report bytes", b[0])


def test_B27_ordinary_event_budget_reports_exact_omitted_count_without_hiding_critical(historical_store):
    store, cfg = historical_store(0, name="ordinary", important=False)
    # 1000 distinct ordinary records cannot be collapsed as consecutive equals.
    for index in range(1000):
        store.audit(START + timedelta(seconds=index), "SCAN",
                    marker="B27-ordinary-" + str(index), counter=index)
    for index in range(3):
        store.audit(START + timedelta(hours=12, seconds=index), "ANOMALY",
                    code="B27-critical-" + str(index), detail="must survive ordinary truncation")
    report = daily_report(store, cfg, DAY)
    section = _events(report)
    assert len(section.splitlines()) <= 200, ("ordinary Events lines", len(section.splitlines()))
    shown = len(re.findall(r"B27-ordinary-\d+", section))
    omitted = 1000 - shown
    assert omitted > 0, ("shown", shown)
    match = re.search(r"另有\s*(\d+)\s*条", section)
    assert match is not None, "truncated report must explicitly count omitted records"
    assert int(match.group(1)) == omitted, ("omitted", match.group(1), omitted)
    assert "audit" in section, "omitted rows must point to the complete durable audit"
    for index in range(3):
        assert "B27-critical-" + str(index) in section, ("critical lost", index)
    assert len(report.encode("utf-8")) <= 100 * 1024


def test_B27_consecutive_identical_ordinary_rows_have_count_and_first_last(historical_store):
    store, cfg = historical_store(0, name="consecutive", important=False)
    for index in range(7):
        store.audit(START + timedelta(seconds=index), "SCAN", marker="B27-repeated",
                    detail="same payload")
    report = daily_report(store, cfg, DAY)
    section = _events(report)
    assert section.count("B27-repeated") == 1, ("duplicate displayed rows", section.count("B27-repeated"))
    assert re.search(r"[×x]\s*7\b", section), "one row must preserve the repeated count"
    assert "11:16:41" in section and "11:16:47" in section, "compression must retain first and last time"


def test_B27_report_account_mask_does_not_change_internal_identity(historical_store):
    store, cfg = historical_store(0, name="mask", important=False)
    identity = store.identity()
    report = daily_report(store, cfg, DAY)
    unmasked, suffix = cfg.account in report, cfg.account[-3:] in report
    assert not unmasked and suffix, "report must mask the account and retain its last three digits"
    assert store.identity() == identity and identity["account"] == cfg.account

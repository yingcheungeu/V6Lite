"""Output-only mask expansion for a short, supported synthetic diagnostic token.

The actual config/account identity is untouched. U1234 occurs only in synthetic
ordinary diagnostic text and exercises the existing supported account regex.
"""
from __future__ import annotations

from datetime import timedelta
import json
import re

from v6lite.report import daily_report
from v6lite.tests.test_batch27_daily_report_volume import (
    historical_store, _audit_digest, _events, DAY)
from v6lite.tests.test_batch27_observation_audit import START


TOKEN = "U1234"
TAIL = "中文完整尾哨兵"


def test_B27_short_diagnostic_account_mask_keeps_final_event_count_and_omission_notice(
        historical_store, record_property):
    store, cfg = historical_store(0, name="short-account-expansion", important=False)
    identity = store.identity()
    detail = (TOKEN + " ") * 80 + " operator_account=" + cfg.account + " " + TAIL
    sample = "- 11:16:41Z SCAN " + json.dumps({
        "marker": "B27-short-account-0999", "detail": detail}, ensure_ascii=False, sort_keys=True)
    masked_sample = sample.replace(TOKEN, "U******234").replace(cfg.account, "DU******" + cfg.account[-3:])
    source_line_bytes, masked_line_bytes = len(sample.encode("utf-8")), len(masked_sample.encode("utf-8"))
    assert source_line_bytes < masked_line_bytes < 2048, ("individual synthetic event sizes", source_line_bytes, masked_line_bytes)
    for index in range(1000):
        store.audit(START + timedelta(seconds=index), "SCAN",
                    marker="B27-short-account-" + str(index).zfill(4), detail=detail)
    original = _audit_digest(store)
    report = daily_report(store, cfg, DAY)
    section = _events(report)
    size, lines = len(report.encode("utf-8")), len(section.splitlines())
    displayed = len(re.findall(r"^- \d{2}:\d{2}:\d{2}Z SCAN(?:\s|$)", section, flags=re.MULTILINE))
    omitted = re.search(r"另有\s*(\d+)\s*条", section)
    record_property("b27_short_account_report_budget", json.dumps({
        "source_rows": 1000, "source_event_line_bytes": source_line_bytes,
        "masked_event_line_bytes": masked_line_bytes, "main_bytes": size,
        "events_lines": lines, "displayed_event_rows": displayed,
        "omission_notice_present": omitted is not None,
        "omitted_rows": int(omitted.group(1)) if omitted else None}, sort_keys=True))
    assert size <= 100 * 1024 and lines <= 200, ("masked report hard limits", size, lines)
    assert TOKEN not in report and cfg.account not in report
    assert "U******234" in report and cfg.account[-3:] in report
    assert 0 < displayed < 1000, ("actual displayed SCAN event rows", displayed)
    assert omitted is not None, "the final byte guard must retain an exact omission notice"
    assert int(omitted.group(1)) == 1000 - displayed, (
        "omission count must use final displayed events", int(omitted.group(1)), 1000-displayed)
    assert "audit" in section
    assert _audit_digest(store) == original
    assert store.identity() == identity and identity["account"] == cfg.account

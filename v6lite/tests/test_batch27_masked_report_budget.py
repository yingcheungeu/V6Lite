"""Independent report budget guard after account masking expands UTF-8 output."""
from __future__ import annotations

from datetime import timedelta
import json
import re

from v6lite.report import daily_report
from v6lite.tests.test_batch27_daily_report_volume import (
    historical_store, _audit_digest, _events, DAY)
from v6lite.tests.test_batch27_observation_audit import START


TAIL = "中文完整尾哨兵"


def test_B27_mask_expansion_keeps_exact_final_display_count_and_omission_notice(
        historical_store, record_property):
    store, cfg = historical_store(0, name="masked-expansion", important=False)
    detail = (cfg.account + " ") * 80 + TAIL
    sample = "- 11:16:41Z SCAN " + json.dumps({
        "marker": "B27-masked-budget-0999", "detail": detail}, ensure_ascii=False, sort_keys=True)
    # This fixture's source rows and their masked forms each fit 2KiB. Ordinary
    # rows may still be shortened to their remaining allowance. A displayed
    # event is counted from its time/kind prefix, never its clipped JSON fields.
    masked_sample = sample.replace(cfg.account, "DU******" + cfg.account[-3:])
    source_line_bytes, masked_line_bytes = len(sample.encode("utf-8")), len(masked_sample.encode("utf-8"))
    assert source_line_bytes < masked_line_bytes < 2048
    for index in range(1000):
        store.audit(START + timedelta(seconds=index), "SCAN",
                    marker="B27-masked-budget-" + str(index).zfill(4), detail=detail)
    original = _audit_digest(store)
    report = daily_report(store, cfg, DAY)
    section = _events(report)
    size, lines = len(report.encode("utf-8")), len(section.splitlines())
    assert size <= 100 * 1024 and lines <= 200, ("masked report hard limits", size, lines)
    assert cfg.account not in report and cfg.account[-3:] in report
    displayed = len(re.findall(r"^- \d{2}:\d{2}:\d{2}Z SCAN(?:\s|$)", section, flags=re.MULTILINE))
    assert 0 < displayed < 1000, ("actual displayed SCAN event rows", displayed)
    omitted = re.search(r"另有\s*(\d+)\s*条", section)
    assert omitted is not None, "the final byte guard must retain an exact omission notice"
    assert int(omitted.group(1)) == 1000 - displayed, (
        "omission count must use final displayed events", int(omitted.group(1)), 1000-displayed)
    assert "audit" in section
    assert _audit_digest(store) == original
    record_property("b27_masked_report_budget", json.dumps({
        "source_rows": 1000, "source_event_line_bytes": source_line_bytes,
        "masked_event_line_bytes": masked_line_bytes, "main_bytes": size, "events_lines": lines,
        "displayed_event_rows": displayed, "omitted_rows": int(omitted.group(1))}, sort_keys=True))

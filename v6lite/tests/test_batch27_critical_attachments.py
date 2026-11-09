"""Batch27 critical reports: real P0 paths and complete, linked attachments.

Only synthetic test state is used. New report artifacts are observed through
the existing Engine normal/shutdown entry points; no future API is imported.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from v6lite.broker.base import BrokerError
from v6lite.protection_engine import ProtectionEvidenceQueryError
from v6lite.tests.helpers import et
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _safety
from v6lite.tests.test_batch27_observation_audit import observation_rig, _observe, START


def _main_report(rig, *, shutdown=False):
    rig.at(et(17, 10, day=(2026, 10, 15)))
    if shutdown:
        rig.engine.shutdown(grace_seconds=0)
    else:
        rig.engine._end_of_day(rig.clock.now())
    path = rig.engine.report_dir / "2026-10-15.md"
    assert path.is_file(), "existing report entry point must create its actual main file"
    return path, path.read_text(encoding="utf-8")


def _bounded(text):
    section = text.split("## Events", 1)[1].split("\n## ", 1)[0]
    size, lines = len(text.encode("utf-8")), len(section.splitlines())
    assert size <= 100 * 1024 and lines <= 200, ("critical main report cap", size, lines)
    return section, size, lines


def _accounts_normalized(value):
    # Independent comparison ignores only the explicitly permitted account mask.
    value = re.sub(r"(?<![A-Za-z0-9])(?:DU|DFA|DF|U|F|I)(?:\d{4,}|\*+\d{3})(?![A-Za-z0-9])",
                   "<account>", value)
    return value


def _attachment(path, text):
    matches = re.findall(r"\[[^\]]*\]\(([^)\s]*\.critical\.jsonl)\)", text)
    assert matches, "main report must link the actual complete critical attachment"
    relative = Path(matches[0])
    assert not relative.is_absolute()
    actual = (path.parent / relative).resolve()
    assert actual.parent == path.parent.resolve(), "attachment must remain beside this synthetic report"
    assert actual.is_file(), "a reported attachment link must not be fictitious"
    raw = actual.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    digest = hashlib.sha256(raw).hexdigest()
    assert digest in text, "main report must publish the exact complete attachment SHA-256"
    refs = []
    for index, line in enumerate(text.splitlines()):
        if relative.name in line:
            refs.extend(text.splitlines()[index:index + 4])
    metadata = "\n".join(refs).replace(digest, "<sha>").replace(relative.name, "<attachment>")
    assert re.search(r"(?<!\d)" + str(len(records)) + r"(?!\d)", metadata), (
        "attachment metadata must state its actual record count", len(records))
    ids = [item["id"] for item in records]
    assert len(ids) == len(set(ids)), "critical attachment cannot duplicate audit identities"
    for item in records:
        assert type(item["id"]) is int and isinstance(item["ts"], str)
        assert isinstance(item["kind"], str) and isinstance(item["payload"], str)
    return records, digest


@pytest.mark.parametrize("shutdown", [False, True], ids=["normal-report", "shutdown-supplement"])
def test_B27_true_P0_unknown_protection_survives_one_thousand_ordinary_scans(
        observation_rig, monkeypatch, shutdown, record_property):
    rig = observation_rig()
    for index in range(1000):
        rig.store.audit(START, "SCAN", marker="B27-report-ordinary-" + str(index))
    sent = []
    original = rig.alerter.send
    def observe_send(now, level, subject, body="", **kwargs):
        sent.append((level, subject))
        return original(now, level, subject, body, **kwargs)
    monkeypatch.setattr(rig.alerter, "send", observe_send)
    trade = copy.deepcopy(rig._b27_native.ib.snapshot[0])
    trade.order.auxPrice += 1.0
    _observe(rig, trade)
    assert any(level == "P0" for level, _ in sent), "fixture must exercise a real P0, not a made-up audit level"
    pid = rig._b27_pids[0]
    assert rig.store.protection(pid)["state"] == "UNKNOWN"
    unknown = rig.store.conn.execute("SELECT id,ts,kind,payload FROM audit "
        "WHERE kind='PROTECT_STATE' AND json_extract(payload,'$.state')='UNKNOWN' ORDER BY id DESC LIMIT 1").fetchone()
    assert unknown is not None
    path, text = _main_report(rig, shutdown=shutdown)
    section, size, lines = _bounded(text)
    important_visible = any("PROTECT_STATE" in line and "UNKNOWN" in line for line in section.splitlines())
    assert important_visible, "a genuine UNKNOWN/P0 state cannot be treated as an ordinary clipped row"
    records, digest = _attachment(path, text)
    saved = [item for item in records if item["id"] == unknown["id"]]
    assert len(saved) == 1
    assert saved[0]["kind"] == unknown["kind"] and saved[0]["ts"] == unknown["ts"]
    assert _accounts_normalized(saved[0]["payload"]) == _accounts_normalized(unknown["payload"])
    unmasked = rig.cfg.account in text or any(rig.cfg.account in item["payload"] for item in records)
    assert not unmasked, "only report output is masked; source identity remains intact"
    assert rig.store.identity()["account"] == rig.cfg.account
    _safety(rig)
    record_property("b27_true_p0_report", json.dumps({"shutdown": shutdown,
        "main_bytes": size, "events_lines": lines, "critical_records": len(records),
        "critical_sha256": digest}, sort_keys=True))


def test_B27_critical_alone_over_caps_has_complete_payload_attachment_and_bounded_main(
        observation_rig, monkeypatch, record_property):
    rig = observation_rig()
    pid = rig._b27_pids[0]
    counter = [0]
    def unavailable():
        marker = "B27-full-critical-" + str(counter[0]).zfill(3)
        raise BrokerError(marker + " synthetic account=" + rig.cfg.account + "；" + "完整原因" * 180)
    monkeypatch.setattr(rig.broker, "open_orders", unavailable)
    for index in range(300):
        counter[0] = index
        with pytest.raises(ProtectionEvidenceQueryError):
            rig.engine._protection_evidence_fresh(rig.store.protection(pid))
    expected = {row["id"]: dict(row) for row in rig.store.conn.execute(
        "SELECT id,ts,kind,payload FROM audit WHERE kind='BROKER_QUERY_ERROR' ORDER BY id")}
    assert len(expected) == 300
    source_bytes = sum(len(row["payload"].encode("utf-8")) for row in expected.values())
    assert source_bytes > 100 * 1024, ("fixture must exceed the whole-document byte cap", source_bytes)
    # Restore healthy transport before the real report path; no runtime state
    # or gate is repaired, and failures remain durable source evidence.
    monkeypatch.setattr(rig.broker, "open_orders", rig._b27_native.open_orders)
    path, text = _main_report(rig)
    _, size, lines = _bounded(text)
    records, digest = _attachment(path, text)
    by_id = {item["id"]: item for item in records}
    for audit_id, source in expected.items():
        saved = by_id.get(audit_id)
        assert saved is not None, ("missing full critical audit identity", audit_id)
        assert saved["ts"] == source["ts"] and saved["kind"] == source["kind"]
        assert _accounts_normalized(saved["payload"]) == _accounts_normalized(source["payload"]), (
            "critical payload changed beyond account masking", audit_id)
    original = {row["id"]: dict(row) for row in rig.store.conn.execute(
        "SELECT id,ts,kind,payload FROM audit WHERE kind='BROKER_QUERY_ERROR' ORDER BY id")}
    assert original == expected, "report rendering cannot rewrite any original critical record"
    unmasked = rig.cfg.account in text or any(rig.cfg.account in item["payload"] for item in records)
    assert not unmasked
    _safety(rig)
    record_property("b27_critical_overflow", json.dumps({"source_critical_records": len(expected),
        "source_payload_bytes": source_bytes, "main_bytes": size, "events_lines": lines,
        "attachment_records": len(records), "attachment_sha256": digest}, sort_keys=True))

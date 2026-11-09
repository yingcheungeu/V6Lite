"""Batch27 independent observer equivalence and true owed-End controls.

10/15's 46531 / 46147 scale and price payload are disclosed evidence.
The five TP status payloads, scan, economics, identities and replies are
synthetic. Scale replay is not claimed to execute 46k full safety proofs.
The reference changes only the observation audit sink, never any gate.
"""
from __future__ import annotations

import hashlib
import json
import math
import types
import uuid
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import timedelta

import pytest

from v6lite.models import FillEvent, Side
from v6lite.report import daily_report
from v6lite.store import Store
from v6lite.tests.test_batch24_pacing import _opcode
from v6lite.tests.test_batch26_overnight_evidence import (
    _no_network, seed_overnight, _restart, _activate, _attempt_tp, _settle,
    _safety, _writes, _tp_requests, _overnight_p1,
)
from v6lite.tests.test_batch27_observation_audit import (
    observation_rig, _observe, _rows, _count, _summary_items, START, SAMPLE, NATIVE_METADATA,
)
from v6lite.tests.test_batch27_logging_volume import formatted_logs, _multi_native

TARGETS = ("ORDER_PRICE_OBSERVATION", "PROTECT_STATE")
EXCLUDED = (*TARGETS, "OBSERVATION_SUMMARY")


def _stable_uuid(monkeypatch):
    """Deterministic test identities and delivery tokens, not permissions."""
    count = [0]
    def next_uuid():
        count[0] += 1
        return uuid.UUID(hex=f"{count[0]:08x}" + "0" * 24)
    monkeypatch.setattr(uuid, "uuid4", next_uuid)


def _reference_sink(rig, monkeypatch):
    calls = Counter()
    def original_audit(self, now, kind, *, generation=None, changed=None, **payload):
        assert kind in TARGETS, "reference seam is limited to the two approved audit kinds"
        calls[kind] += 1
        return self.audit(now, kind, **payload)
    # B26 has no seam and never calls this added attribute: its original writer
    # is already the reference. No import/AttributeError can create a false red.
    monkeypatch.setattr(rig.store, "observation_audit",
                        types.MethodType(original_audit, rig.store), raising=False)
    return calls


def _capture_cards(rig, monkeypatch):
    cards = []
    def send_card(card):
        plain, embed = card.as_plain_text(), card.as_embed()
        cards.append((plain, embed))
        rig.channel.send(card.title, plain)
    monkeypatch.setattr(rig.channel, "send_card", send_card, raising=False)
    return cards


def _digest(value):
    def encode(item):
        return asdict(item) if is_dataclass(item) else str(item)
    return hashlib.sha256(json.dumps(value, default=encode, sort_keys=True,
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _snapshot(rig):
    tables = ("positions", "fills", "commissions", "intents", "protections",
              "controls", "risk_days", "exit_stages")
    contents = {}
    for name in tables:
        contents[name] = [dict(row) for row in rig.store.conn.execute("SELECT * FROM " + name + " ORDER BY 1")]
    return contents


def _non_target_digest(rig):
    digest = hashlib.sha256()
    count = 0
    for row in rig.store.conn.execute(
            "SELECT ts,kind,payload FROM audit WHERE kind NOT IN (?,?,?) ORDER BY id", EXCLUDED):
        digest.update(json.dumps(tuple(row), ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def _broker_calls(rig):
    return {name: list(getattr(rig.broker, name)) for name in
            ("sent", "sent_protections", "modified_protections", "cancels")}


def _same(left, right, label):
    a, b = _digest(left), _digest(right)
    assert a == b, (label, a, b)


def test_B27_exact_1015_observation_scale_preserves_state_evidence_cards_and_calls(
        observation_rig, monkeypatch, record_property):
    results = []
    for reference in (False, True):
        _stable_uuid(monkeypatch)
        rig = observation_rig()
        cards = _capture_cards(rig, monkeypatch)
        calls = _reference_sink(rig, monkeypatch) if reference else None
        initial = {kind: _count(rig.store, kind) for kind in TARGETS}
        pid = rig._b27_pids[0]
        for index in range(46531):
            rig.at(START + timedelta(seconds=index * (13 * 3600) / 46530))
            _observe(rig)
            if index < 46147:
                rig.store.update_protection(rig.clock.now(), pid, confirmed_at=rig.clock.now())
            if index in (100, 10100, 20100, 30100, 40100):
                rig.engine._tp_record(rig.clock.now(), rig.store.protection(pid), "status",
                    intent_id=rig.store.protection(pid)["intent_id"],
                    source="synthetic-volume-status",
                    marker="B27-scale-status-" + str(index))
            if index == 23100:
                rig.store.audit(rig.clock.now(), "SCAN", marker="B27-scale-SCAN", no_trades=True)
            _safety(rig)
            if not reference and index < 8:
                for kind in TARGETS:
                    amount = _count(rig.store, kind)
                    if amount > 5:
                        record_property("b27_scale_failure_kind", kind)
                        record_property("b27_scale_failure_rows", amount)
                        record_property("b27_scale_completed_rounds", index + 1)
                    assert amount <= 5, ("scale target rows", kind, amount, index)
        first_price = rig.store.conn.execute(
            "SELECT payload FROM audit WHERE kind='ORDER_PRICE_OBSERVATION' ORDER BY id LIMIT 1").fetchone()
        assert first_price is not None and json.loads(first_price["payload"]) == SAMPLE | NATIVE_METADATA
        # Main scale replay has no financial decisions. Real a-d and all write
        # boundaries are checked separately below, not represented by a fake bool.
        assert not rig.broker.modified_protections and not rig.broker.cancels
        rig.engine.shutdown(grace_seconds=0)
        target_counts = {kind: _count(rig.store, kind) - initial[kind] for kind in TARGETS}
        if reference:
            assert target_counts == {"ORDER_PRICE_OBSERVATION": 46531, "PROTECT_STATE": 46147}
            if callable(getattr(Store, "observation_audit", None)):
                assert calls == Counter(target_counts)
        else:
            assert all(_count(rig.store, kind) <= 5 for kind in TARGETS)
            summary_count = _count(rig.store, "OBSERVATION_SUMMARY")
            assert 0 < summary_count <= 30, ("scale summary count", summary_count)
            totals = Counter()
            for item in _summary_items(rig.store):
                totals[item["kind"]] += item["count"]
            for kind, original in (("ORDER_PRICE_OBSERVATION", 46531), ("PROTECT_STATE", 46147)):
                assert target_counts[kind] + totals[kind] == original, ("scale exact counts", kind)
            report = daily_report(rig.store, rig.cfg, START.date())
            section = report.split("## Events", 1)[1].split("\n## ", 1)[0]
            lines, size = len(section.splitlines()), len(report.encode("utf-8"))
            assert lines <= 200 and size <= 100 * 1024, ("scale report", lines, size)
            for index in (100, 10100, 20100, 30100, 40100):
                visible = "B27-scale-status-" + str(index) in report
                assert visible, ("missing synthetic TP status", index)
            scan_visible = "B27-scale-SCAN" in report
            assert scan_visible, "scale scan must remain visible"
            record_property("b27_scale_report", json.dumps({"events_lines": lines, "bytes": size}))
        record_property("b27_scale_" + ("reference" if reference else "candidate"),
            json.dumps({"price_observations": 46531, "confirmed_at_observations": 46147,
                "target_writes": target_counts, "summary_rows": _count(rig.store, "OBSERVATION_SUMMARY"),
                "non_target_audits": _non_target_digest(rig)[0], "actual_cards": len(cards)}, sort_keys=True))
        results.append((_snapshot(rig), _non_target_digest(rig), cards,
                        list(rig.channel.messages), _broker_calls(rig)))
    for index, label in enumerate(("economic and protection rows", "non-target audits",
                                  "actual plain/embed cards", "delivered text", "all broker write arguments")):
        _same(results[0][index], results[1][index], label)


@pytest.mark.parametrize("failure", ["allow", "a", "b", "c", "d"])
def test_B27_actual_overnight_permission_and_refusal_are_unchanged_by_audit_sink(
        seed_overnight, tmp_path, monkeypatch, failure, record_property):
    outputs = []
    for reference in (False, True):
        _stable_uuid(monkeypatch)
        rig, pid = seed_overnight(enabled=True)
        cards = _capture_cards(rig, monkeypatch)
        if reference:
            _reference_sink(rig, monkeypatch)
        ref = rig.store.protection(pid)["intent_id"]
        if failure == "a":
            # Fixture-only absent ledger execution, not an allowed migration.
            rig.store.conn.execute("DELETE FROM fills WHERE exec_id=?", (rig._b26_entry_fill.exec_id,))
        elif failure == "b":
            # Known excess broker position P=4; S=3 remains <=P, but b fails.
            rig.broker.external_positions["AAPL"] = (1.0, 113.0)
        elif failure == "c":
            rig.broker.orders[ref].status = "Cancelled"
        elif failure == "d":
            rig._b26_visible_extra.append(FillEvent("b27-unknown-today.01.01", "", "AAPL",
                Side.BUY, 1, 113.0, rig.clock.now(), con_id=rig._b26_original["con_id"]))
        before = _writes(rig)
        _restart(rig, tmp_path, monkeypatch)
        if failure == "allow":
            tp, same_ref = _activate(rig, pid)
            assert tp.quantity == 1 and same_ref == ref
            assert rig.store.protection(pid)["state"] == "TP_ACTIVE"
            assert rig.broker.orders[ref].remaining == 2
            decision = ("allowed", rig.store.protection(pid)["state"], tp)
        else:
            _attempt_tp(rig, pid); _settle(rig)
            assert not _tp_requests(rig) and _writes(rig) == before
            assert _overnight_p1(rig), ("missing actual overnight refusal", failure)
            decision = ("refused", rig.store.protection(pid)["state"])
        _safety(rig)
        proof = rig.engine._protection_notice_evidence.get(pid)
        # Retain the actual evidence object and decision-produced calls; never
        # inject a permission boolean or replace any a-d authority function.
        outputs.append((_snapshot(rig), _non_target_digest(rig), cards,
            list(rig.channel.messages), _broker_calls(rig), proof, decision))
        record_property("b27_permission_" + ("reference" if reference else "candidate"),
            json.dumps({"scenario": failure, "decision": decision[0], "actual_cards": len(cards),
                        "write_counts": _writes(rig)}, sort_keys=True))
    for index, label in enumerate(("economic/protection state", "non-target audits", "actual cards",
                                  "delivered text", "broker writes", "actual evidence", "actual decision")):
        _same(outputs[0][index], outputs[1][index], failure + ":" + label)


def test_B27_real_owned_End_debt_drains_once_and_is_counted_without_query_semantic_change(
        formatted_logs, tmp_path, monkeypatch, request, record_property):
    _, read = formatted_logs
    adapter, clock, wire = _multi_native(tmp_path, monkeypatch, request, 1)
    ib, client, wrapper = adapter.ib, adapter.ib.client, adapter.ib.wrapper
    original_positions, original_sleep = ib.reqPositions, ib.sleep
    initial_sent, initial_ended = adapter._req_sent["positions"], adapter._req_ended["positions"]
    assert initial_sent == initial_ended, "native bootstrap is complete before measuring new requests"
    error = TimeoutError("synthetic delayed native positionEnd")
    old_future = []
    def time_out_after_native_send():
        old_future.append(wrapper.startReq("positions"))
        client.reqPositions()
        adapter._b25_offline_drain()
        raise error
    monkeypatch.setattr(ib, "reqPositions", time_out_after_native_send)
    clock.advance(seconds=2)
    with pytest.raises(TimeoutError) as captured:
        adapter.positions_fresh()
    assert captured.value is error
    assert adapter._req_sent["positions"] - initial_sent == 1
    assert adapter._req_ended["positions"] - initial_ended == 0
    assert old_future and not old_future[0].done()
    assert "positions" not in adapter._channel_poisoned
    monkeypatch.setattr(ib, "reqPositions", original_positions)
    releases = []
    def late_old_end(seconds):
        original_sleep(seconds)
        if not releases:
            releases.append(clock.now())
            wrapper.positionEnd()
    monkeypatch.setattr(ib, "sleep", late_old_end)
    positions = adapter.positions_fresh()
    assert len(releases) == 1 and old_future[0].done()
    assert len(positions) == 1 and positions[0].quantity == 3
    assert adapter._req_sent["positions"] - initial_sent == 2
    assert adapter._req_ended["positions"] - initial_ended == 2
    assert "positions" not in adapter._channel_poisoned and adapter.is_connected()
    packets = Counter(_opcode(packet) for _, packet in wire)
    assert packets["61"] == 2 and not packets["3"] and not packets["4"]
    final_sent, final_ended = adapter._req_sent["positions"], adapter._req_ended["positions"]
    adapter.disconnect()
    summaries = [line for line in read().splitlines() if "[QUERY_SUMMARY]" in line]
    assert summaries, "normal owed-End recovery must retain final query statistics"
    count = drained = 0
    maximum = 0.0
    for line in summaries:
        payload = json.loads(line[line.index("{"):])
        channel = payload["channels"].get("positions")
        if channel:
            count += channel["count"]
            drained += channel["drained"]
            maximum = max(maximum, channel["max_seconds"])
    assert count == 2 and drained == 1, ("actual query attempts / drained", count, drained)
    assert math.isfinite(maximum) and maximum >= 0
    record_property("b27_actual_drain", json.dumps({"native_positions": packets["61"],
        "sent": final_sent, "ended": final_ended,
        "initial_sent": initial_sent, "initial_ended": initial_ended,
        "new_sent": final_sent-initial_sent, "new_ended": final_ended-initial_ended,
        "summary_count": count, "summary_drained": drained, "max_seconds": maximum}, sort_keys=True))

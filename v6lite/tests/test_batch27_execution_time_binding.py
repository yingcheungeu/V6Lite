"""Independent log-signature guard: native execution time stays bound to execId.

Two real native results have the same economics, raw-time multiset and classes.
Only their execution-to-time association changes. No Engine/Store is replaced.
"""
from __future__ import annotations

from collections import Counter
import json

from v6lite.tests.test_batch24_pacing import _opcode
from v6lite.tests.test_batch26_overnight_evidence import _no_network
from v6lite.tests.test_batch27_logging_volume import formatted_logs
from v6lite.tests.test_batch27_execution_log_aggregation import _native, _normal_summaries


def test_B27_native_execution_time_swap_keeps_binding_and_emits_changed_info(
        formatted_logs, tmp_path, monkeypatch, request, record_property):
    _, read = formatted_logs
    h, fills = _native(tmp_path, monkeypatch, request, 2)
    ids = [fill.exec_id for fill in fills]
    raw = {ids[0]: "20261015 11:16:41 UTC", ids[1]: "20261015 11:16:42 UTC"}
    h.raw = lambda fill: raw[fill.exec_id]
    native_times = []
    original = h.ib.reqExecutions
    def retain_native_result(execution_filter=None):
        result = original(execution_filter)
        native_times.append({fill.execution.execId: fill.execution.time for fill in result})
        return result
    monkeypatch.setattr(h.ib, "reqExecutions", retain_native_result)
    first = h.client_query()
    first_info = len(_normal_summaries(read()))
    assert first_info == 1
    assert set(native_times[0]) == set(ids)
    assert native_times[0][ids[0]] != native_times[0][ids[1]]
    assert {fill.exec_id: fill.time for fill in first} == native_times[0]
    initial_raw = Counter(item[2] for item in h.decoded)
    h.clock.advance(seconds=2)
    raw[ids[0]], raw[ids[1]] = raw[ids[1]], raw[ids[0]]
    second = h.client_query()
    assert set(native_times[1]) == set(ids)
    assert all(native_times[1][exec_id] != native_times[0][exec_id] for exec_id in ids), (
        "fixture must change the real SDK Execution.time for both stable execIds")
    assert native_times[1][ids[0]] == native_times[0][ids[1]]
    assert native_times[1][ids[1]] == native_times[0][ids[0]]
    assert {fill.exec_id: fill.time for fill in second} == native_times[1]
    assert Counter(item[2] for item in h.decoded[2:]) == initial_raw
    assert {(fill.exec_id, fill.quantity, fill.price) for fill in first} == {
        (fill.exec_id, fill.quantity, fill.price) for fill in second}
    summaries = _normal_summaries(read())
    assert all(all(value in line for value in ("returned=2", "zoned=2", "naive=0", "unparseable=0"))
               for line in summaries)
    packets = [packet.split("\0") for packet in h.wire if _opcode(packet) == "7"]
    assert len(packets) == len(h.ends) == 2
    assert [int(packet[2]) for packet in packets] == [item[0] for item in h.ends]
    assert len({packet[2] for packet in packets}) == 2
    assert not [packet for packet in h.wire if _opcode(packet) in {"3", "4"}]
    assert len(summaries) == first_info + 1, (
        "execution-time association changed despite identical raw multiset/class/count", len(summaries))
    record_property("b27_execution_time_binding", json.dumps({
        "native_queries": len(packets), "native_ends": len(h.ends),
        "changed_exec_times": len(ids), "first_or_changed_info": len(summaries)}, sort_keys=True))

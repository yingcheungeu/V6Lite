"""Batch27 execution-log aggregation through actual native decoder/Future/End.

No economic booking or authority helper is replaced. The full-budget replay
uses all three native evidence channels. The local server's receipt lists
are consumed after each round so fixture bookkeeping is not quadratic.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.clock import FixedClock
from v6lite.models import FillEvent, Side
from v6lite.tests.helpers import make_cfg
from v6lite.tests.test_batch24_pacing import _opcode
from v6lite.tests.test_batch26_overnight_evidence import _no_network
from v6lite.tests.test_batch27_logging_volume import formatted_logs
from v6lite.tests.test_batch27_observation_audit import START, SYMBOLS
from v6lite.tests.test_batch27b_raw_execution_time import NativeExecutionHarness


def _native(tmp_path, monkeypatch, request, holdings=1):
    from ib_insync import Order, OrderState, Stock
    cfg = make_cfg(tmp_path)
    clock = FixedClock(START)
    h = NativeExecutionHarness(cfg, clock, monkeypatch, request)
    contracts = [Stock(s, "SMART", "USD", conId=900000+i) for i, s in enumerate(SYMBOLS[:holdings])]
    fills = [FillEvent("b27-log-native-" + str(i) + ".01.01", "v6l-log-native-" + str(i),
        c.symbol, Side.BUY, 3, 342.53, START, broker_order_id=900+i,
        perm_id=90000+i, con_id=c.conId) for i, c in enumerate(contracts)]
    h.rows = lambda: fills
    for c in contracts:
        c.localSymbol = c.symbol
        c.tradingClass = "NMS"
        h.adapter._contracts[c.symbol] = c
    def orders():
        future = h.wrapper.startReq("openOrders")
        h.client.reqAllOpenOrders(); h.loop.drain()
        h.wrapper.lastTime = clock.now()
        for i, c in enumerate(contracts):
            order = Order(orderId=1000+i, clientId=cfg.client_id, permId=100000+i,
                action="SELL", totalQuantity=3, orderType="STP", auxPrice=340.52,
                lmtPrice=0.0, tif="GTC", account=cfg.account,
                orderRef="v6l-log-protect-" + str(i), outsideRth=False)
            h.wrapper.openOrder(order.orderId, c, order, OrderState(status="PreSubmitted"))
        h.wrapper.openOrderEnd()
        assert future.done()
        return future.result()
    def positions():
        future = h.wrapper.startReq("positions")
        h.client.reqPositions(); h.loop.drain()
        for c in contracts:
            h.wrapper.position(cfg.account, c, 3.0, 342.53)
        h.wrapper.positionEnd()
        assert future.done()
        return future.result()
    monkeypatch.setattr(h.ib, "reqAllOpenOrders", orders)
    monkeypatch.setattr(h.ib, "reqPositions", positions)
    return h, fills


def _normal_summaries(text):
    return [line for line in text.splitlines() if " INFO " in line and "[EXECUTION_TIME_SUMMARY]" in line]


def _aggregates(text):
    for line in text.splitlines():
        if "[EXECUTION_TIME_AGGREGATE]" in line:
            yield json.loads(line[line.index("{"):])


def _query_receipt(h, expected_rows):
    executions = [packet for packet in h.wire if _opcode(packet) == "7"]
    assert len(executions) == 1 and len(h.ends) == 1
    fields = executions[0].split("\0")
    assert int(fields[2]) == h.ends[0][0], "the real query reqId must own its matching native End"
    assert len(h.decoded) == expected_rows
    return Counter(_opcode(packet) for packet in h.wire)


@pytest.mark.parametrize("holdings", [1, 10], ids=["one", "ten"])
@pytest.mark.parametrize("seconds", [15, 1], ids=["15s", "1s-pressure"])
def test_B27_full_native_evidence_log_budget_includes_complete_executions(
        formatted_logs, tmp_path, monkeypatch, request, holdings, seconds, record_property):
    _, read = formatted_logs
    h, fills = _native(tmp_path, monkeypatch, request, holdings)
    rounds = 13 * 3600 // seconds
    native = Counter()
    budget = 2000 if holdings == 1 else 10000
    for index in range(rounds):
        h.clock.set(START + timedelta(seconds=index * seconds))
        orders, positions = h.adapter.open_orders(), h.adapter.positions_fresh()
        queried = h.client_query()
        assert len(orders) == len(positions) == len(queried) == holdings
        assert {(f.exec_id, f.quantity, f.price) for f in queried} == {
            (f.exec_id, f.quantity, f.price) for f in fills}
        native.update(_query_receipt(h, holdings))
        h.adapter.drain_events()
        h.wire.clear(); h.filters.clear(); h.ends.clear(); h.decoded.clear()
        if index % 400 == 0:
            normal = [line for line in read().splitlines()
                      if " WARNING " not in line and " ERROR " not in line and " CRITICAL " not in line]
            if len(normal) > budget:
                record_property("b27_exec_budget_failure", json.dumps({
                    "completed_rounds": index+1, "normal_lines": len(normal), "budget": budget}))
            assert len(normal) <= budget, ("full native normal lines", len(normal), budget, index)
    h.adapter.disconnect()
    text = read()
    normal = [line for line in text.splitlines()
              if " WARNING " not in line and " ERROR " not in line and " CRITICAL " not in line]
    aggregates = list(_aggregates(text))
    assert len(normal) <= budget, ("full native normal lines", len(normal), budget)
    assert len(_normal_summaries(text)) == 1, "fresh reqIds/timestamps cannot turn unchanged results into changes"
    assert 0 < len(aggregates) <= 30, ("execution aggregate rows", len(aggregates))
    assert sum(a["suppressed_queries"] for a in aggregates) == rounds - 1
    for field in ("returned", "naive"):
        assert sum(a[field] for a in aggregates) == (rounds - 1) * holdings, field
    for field in ("zoned", "unparseable"):
        assert sum(a[field] for a in aggregates) == 0, field
    assert native["16"] == native["61"] == native["7"] == rounds
    assert not native["3"] and not native["4"]
    record_property("b27_full_native_log_budget", json.dumps({
        "holdings": holdings, "interval_seconds": seconds, "rounds": rounds,
        "actual_native_requests": dict(native), "normal_lines": len(normal), "budget": budget,
        "execution_info_first_or_changed": len(_normal_summaries(text)),
        "aggregate_rows": len(aggregates),
        "suppressed_queries": sum(a["suppressed_queries"] for a in aggregates)}, sort_keys=True))


@pytest.mark.parametrize("changed", ["exec_id", "price"])
def test_B27_same_raw_counts_and_sample_but_economic_identity_change_is_immediate_info(
        formatted_logs, tmp_path, monkeypatch, request, changed):
    _, read = formatted_logs
    h, fills = _native(tmp_path, monkeypatch, request, 2)
    h.client_query()
    assert len(_normal_summaries(read())) == 1
    h.clock.advance(seconds=2); h.client_query()
    assert len(_normal_summaries(read())) == 1
    fields = {"exec_id": "b27-log-changed.02.01"} if changed == "exec_id" else {"price": 342.54}
    fills[1] = replace(fills[1], **fields)
    h.clock.advance(seconds=2)
    actual = h.client_query()
    assert len(actual) == 2
    assert any(getattr(fill, changed) == fields[changed] for fill in actual), "actual decoded result must contain the change"
    assert len(_normal_summaries(read())) == 2, "the signature must include all execution identities/economics"


@pytest.mark.parametrize("raw", ["20261015 11:16:42", "20261015 11:16:41 UTC"])
def test_B27_raw_sample_or_classification_change_keeps_immediate_info(
        formatted_logs, tmp_path, monkeypatch, request, raw):
    _, read = formatted_logs
    h, _ = _native(tmp_path, monkeypatch, request)
    h.client_query()
    h.clock.advance(seconds=2); h.client_query()
    assert len(_normal_summaries(read())) == 1
    h.raw = lambda fill: raw
    h.clock.advance(seconds=2); h.client_query()
    assert len(_normal_summaries(read())) == 2


def test_B27_client_and_scoped_roles_do_not_force_per_query_signature_churn(
        formatted_logs, tmp_path, monkeypatch, request):
    _, read = formatted_logs
    h, _ = _native(tmp_path, monkeypatch, request)
    for _ in range(4):
        h.client_query()
        h.clock.advance(seconds=2)
        scoped = h.scoped_query()
        scope = getattr(scoped, "scope", None)
        assert scope and scope["complete"] is True
        h.clock.advance(seconds=2)
    h.adapter.disconnect()
    text = read()
    assert len(_normal_summaries(text)) == 2, "each role has one first result, not a new result every alternation"
    rows = list(_aggregates(text))
    assert len({row["role"] for row in rows}) == 2
    assert sum(row["suppressed_queries"] for row in rows) == 6


@pytest.mark.parametrize("fault", ["unparseable", "missing-End"])
def test_B27_real_unparseable_or_incomplete_query_is_warning_every_time(
        formatted_logs, tmp_path, monkeypatch, request, fault):
    _, read = formatted_logs
    h, _ = _native(tmp_path, monkeypatch, request)
    if fault == "unparseable":
        h.raw = lambda fill: "bad-native"
    else:
        h.mode = "missing-End"
    for _ in range(2):
        h.clock.advance(seconds=2)
        result = h.scoped_query()
        scope = getattr(result, "scope", None)
        assert scope and scope["complete"] is False
    warnings = [line for line in read().splitlines()
                if " WARNING " in line and "EXECUTION_TIME" in line]
    assert len(warnings) == 2, ("each abnormal query warning", len(warnings))
    assert not _normal_summaries(read()), "abnormal results cannot become a normal repeated INFO"
    assert len([packet for packet in h.wire if _opcode(packet) == "7"]) == 2


def test_B27_execution_generation_flushes_counts_and_first_new_generation_is_info(
        formatted_logs, tmp_path, monkeypatch, request):
    _, read = formatted_logs
    h, _ = _native(tmp_path, monkeypatch, request)
    h.client_query()
    for _ in range(3):
        h.clock.advance(seconds=2); h.client_query()
    before = h.adapter.query_generation
    h.adapter._reset_channels()
    assert h.adapter.query_generation == before + 1
    h.clock.advance(seconds=2); h.client_query()
    text = read()
    assert len(_normal_summaries(text)) == 2
    rows = list(_aggregates(text))
    assert sum(row["suppressed_queries"] for row in rows) == 3

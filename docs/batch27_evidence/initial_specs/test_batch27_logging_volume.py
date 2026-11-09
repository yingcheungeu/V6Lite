"""Batch27 real logging/formatter contracts, local native transport only.

The EXECUTION_TIME_SUMMARY aggregation and critical-overflow rulings are
pending and are deliberately not specified here.  The 13h budget below covers
the actual openOrders/positions/SDK callback sources named in the request.
"""
from __future__ import annotations

import logging
import json
import math
from collections import Counter
from datetime import timedelta

import pytest

from v6lite.__main__ import setup_logging
from v6lite.broker.base import BrokerError
from v6lite.tests.helpers import Rig, make_cfg
from v6lite.tests.test_batch24_pacing import _opcode
from v6lite.tests.test_batch25_restart import _native_server
from v6lite.tests.test_batch26_overnight_evidence import _no_network
from v6lite.tests.test_batch27_observation_audit import START, HOURS, SYMBOLS

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


@pytest.fixture
def formatted_logs(tmp_path):
    root = logging.getLogger()
    wrapper = logging.getLogger("ib_insync.wrapper")
    old_handlers, old_level = list(root.handlers), root.level
    old_wrapper_level, old_wrapper_filters = wrapper.level, list(wrapper.filters)
    old_root_filters = list(root.filters)
    previous = [(h, list(h.filters), h.formatter) for h in old_handlers]
    cfg = make_cfg(tmp_path / "logging")
    setup_logging(cfg)
    added = [h for h in root.handlers if h not in old_handlers]
    files = [h for h in added if isinstance(h, logging.FileHandler)]
    assert len(files) == 1, "fixture must exercise the real configured FileHandler"
    def read():
        for handler in added:
            handler.flush()
        return open(files[0].baseFilename, encoding="utf-8").read()
    yield cfg, read
    for handler in list(root.handlers):
        if handler not in old_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(old_level)
    root.filters[:] = old_root_filters
    wrapper.setLevel(old_wrapper_level)
    wrapper.filters[:] = old_wrapper_filters
    for handler, filters, formatter in previous:
        handler.filters[:] = filters
        handler.setFormatter(formatter)


def _multi_native(tmp_path, monkeypatch, request, holdings):
    from ib_insync import Order, OrderState, Stock
    adapter, clock, trace, wire, _, _, _ = _native_server(tmp_path, monkeypatch, request)
    adapter.connect()
    ib, client, wrapper = adapter.ib, adapter.ib.client, adapter.ib.wrapper
    symbols = SYMBOLS[:holdings]
    contracts = [Stock(s, "SMART", "USD", conId=800000 + i) for i, s in enumerate(symbols)]
    for contract in contracts:
        contract.localSymbol = contract.symbol
        contract.tradingClass = "NMS"
        adapter._contracts[contract.symbol] = contract
    def orders():
        future = wrapper.startReq("openOrders")
        client.reqAllOpenOrders()
        adapter._b25_offline_drain()
        wrapper.lastTime = clock.now()
        for index, contract in enumerate(contracts):
            order = Order(orderId=800 + index, clientId=adapter.client_id, permId=80000 + index,
                action="SELL", totalQuantity=3, orderType="STP", lmtPrice=0.0,
                auxPrice=340.52, tif="GTC", account=adapter.cfg.account,
                orderRef="v6l-b27-log-" + str(index), outsideRth=False)
            wrapper.openOrder(order.orderId, contract, order, OrderState(status="PreSubmitted"))
        wrapper.openOrderEnd()
        assert future.done()
        return future.result()
    def positions():
        future = wrapper.startReq("positions")
        client.reqPositions()
        adapter._b25_offline_drain()
        for contract in contracts:
            wrapper.position(adapter.cfg.account, contract, 3.0, 342.53)
        wrapper.positionEnd()
        assert future.done()
        return future.result()
    monkeypatch.setattr(ib, "reqAllOpenOrders", orders)
    monkeypatch.setattr(ib, "reqPositions", positions)
    wire.clear()
    return adapter, clock, wire


def test_B27_successful_query_details_are_debug_and_not_info_file(
        formatted_logs, tmp_path, monkeypatch, request):
    _, read = formatted_logs
    adapter, clock, wire = _multi_native(tmp_path, monkeypatch, request, 1)
    clock.advance(seconds=2)
    adapter.open_orders()
    adapter.positions_fresh()
    text = read()
    normal = [line for line in text.splitlines() if "[QUERY]" in line]
    assert not normal, ("normal QUERY lines at configured INFO", len(normal))
    packets = Counter(_opcode(packet) for _, packet in wire)
    assert packets["16"] == packets["61"] == 1
    assert not packets["3"] and not packets["4"]
    assert logging.getLogger("ib_insync.wrapper").getEffectiveLevel() >= logging.WARNING


@pytest.mark.parametrize("holdings", [1, 10], ids=["one", "ten"])
@pytest.mark.parametrize("seconds", [15, 1], ids=["15s", "1s-pressure"])
def test_B27_query_and_position_sources_thirteen_hour_info_budget(
        formatted_logs, tmp_path, monkeypatch, request, holdings, seconds, record_property):
    _, read = formatted_logs
    record_property("b27_holdings", holdings)
    record_property("b27_interval_seconds", seconds)
    record_property("b27_simulated_hours", HOURS)
    adapter, clock, wire = _multi_native(tmp_path, monkeypatch, request, holdings)
    start = clock.now() + timedelta(seconds=2)
    rounds = HOURS * 3600 // seconds
    limit = 2000 if holdings == 1 else 10000
    for index in range(rounds):
        clock.set(start + timedelta(seconds=index * seconds))
        orders, positions = adapter.open_orders(), adapter.positions_fresh()
        assert len(orders) == len(positions) == holdings
        adapter.drain_events()  # production consumer; do not retain a fake event backlog
        if index % 200 == 0:
            text = read()
            normal = [line for line in text.splitlines()
                      if " WARNING " not in line and " ERROR " not in line and " CRITICAL " not in line]
            if len(normal) > limit:
                record_property("b27_failure_normal_lines", len(normal))
                record_property("b27_failure_completed_rounds", index + 1)
                record_property("b27_log_line_budget", limit)
            assert len(normal) <= limit, ("normal log lines", len(normal), limit, index)
    packets = Counter(_opcode(packet) for _, packet in wire)
    assert packets["16"] == packets["61"] == rounds, ("actual native queries", packets["16"], packets["61"], rounds)
    assert not packets["3"] and not packets["4"]
    text = read()
    normal = [line for line in text.splitlines()
              if " WARNING " not in line and " ERROR " not in line and " CRITICAL " not in line]
    record_property("b27_log_metrics", json.dumps({
        "rounds": rounds, "holdings": holdings, "interval_seconds": seconds,
        "native_open_orders": packets["16"], "native_positions": packets["61"],
        "normal_lines": len(normal), "budget": limit,
        "bytes": len(text.encode("utf-8"))
    }, sort_keys=True))
    assert len(normal) <= limit, ("normal log lines", len(normal), limit)
    assert adapter.cfg.account not in text
    assert any("QUERY" in line and "SUMMARY" in line for line in normal), "periodic query statistics must not disappear"


def test_B27_query_summary_counts_channels_and_flushes_pending_on_disconnect(
        formatted_logs, tmp_path, monkeypatch, request):
    _, read = formatted_logs
    adapter, clock, wire = _multi_native(tmp_path, monkeypatch, request, 1)
    for method, seconds in (("reqAllOpenOrders", .25), ("reqPositions", .5)):
        original = getattr(adapter.ib, method)
        def timed_reply(*args, _original=original, _seconds=seconds, **kwargs):
            clock.advance(seconds=_seconds)
            return _original(*args, **kwargs)
        monkeypatch.setattr(adapter.ib, method, timed_reply)
    for _ in range(3):
        clock.advance(seconds=15)
        adapter.open_orders(); adapter.positions_fresh(); adapter.drain_events()
    clock.advance(seconds=1800)
    adapter.open_orders(); adapter.positions_fresh(); adapter.drain_events()
    adapter.disconnect()
    summary = [line for line in read().splitlines() if "QUERY" in line and "SUMMARY" in line]
    assert summary, "30-minute and final query totals must be flushed"
    totals, maxima, drains = Counter(), {}, Counter()
    for line in summary:
        assert "[QUERY_SUMMARY]" in line and "{" in line, "query summary uses the agreed JSON envelope"
        payload = json.loads(line[line.index("{"):])
        assert isinstance(payload.get("channels"), dict)
        for channel, value in payload["channels"].items():
            assert type(value["count"]) is int and value["count"] >= 0
            assert type(value["drained"]) is int and value["drained"] >= 0
            assert not isinstance(value["max_seconds"], bool) and math.isfinite(value["max_seconds"])
            totals[channel] += value["count"]
            drains[channel] += value["drained"]
            maxima[channel] = max(maxima.get(channel, 0), value["max_seconds"])
    assert totals["openOrders"] == totals["positions"] == 4, ("summary totals", dict(totals))
    assert maxima["openOrders"] == pytest.approx(.25, abs=1e-6)
    assert maxima["positions"] == pytest.approx(.5, abs=1e-6)
    assert drains["openOrders"] == drains["positions"] == 0
    packets = Counter(_opcode(packet) for _, packet in wire)
    assert packets["16"] == packets["61"] == 4


def test_B27_each_failed_query_remains_warning_with_no_success_claim(
        formatted_logs, tmp_path, monkeypatch, request):
    _, read = formatted_logs
    adapter, clock, _ = _multi_native(tmp_path, monkeypatch, request, 1)
    calls = []
    error = BrokerError("synthetic query failure", maybe_sent=True)
    error.b27_provenance = ("synthetic native", 202)
    def fail():
        # The complete native transport is exercised elsewhere. This tests
        # the exception boundary before an owned request can claim completion.
        calls.append(clock.now())
        raise error
    monkeypatch.setattr(adapter.ib, "reqAllOpenOrders", fail)
    for _ in range(2):
        clock.advance(seconds=2)
        with pytest.raises(BrokerError, match="synthetic query failure") as captured:
            adapter.open_orders()
        assert captured.value is error
        assert error.maybe_sent is True and error.b27_provenance == ("synthetic native", 202)
    warnings = [line for line in read().splitlines()
                if " WARNING " in line and "synthetic query failure" in line]
    assert len(calls) == len(warnings) == 2, ("failed calls / warning lines", len(calls), len(warnings))
    assert not [line for line in read().splitlines()
                if "[QUERY]" in line and " INFO " in line]


@pytest.mark.parametrize("account", ["DU0000000", "U0000000", "DF1234567", "DFA1234567", "F1234567", "I1234567"])
def test_B27_final_formatter_masks_child_logger_args_and_traceback_without_mutating_record(
        formatted_logs, account):
    cfg, read = formatted_logs
    originals = []
    class Capture(logging.Handler):
        def emit(self, record):
            originals.append(record)
    capture = Capture()
    root = logging.getLogger()
    root.addHandler(capture)
    template = "synthetic SDK account=%s configured=%s"
    logger = logging.getLogger("ib_insync.wrapper.b27_child")
    try:
        try:
            raise ValueError("synthetic SDK traceback " + account + " configured " + cfg.account)
        except ValueError:
            logger.warning(template, account, cfg.account, exc_info=True)
    finally:
        root.removeHandler(capture)
    text = read()
    assert account not in text and cfg.account not in text, ("unmasked account family", account[:3])
    assert account[-3:] in text, "mask must retain the last three digits"
    assert "synthetic SDK traceback" in text and "WARNING" in text
    [record] = originals
    assert record.msg == template and record.args == (account, cfg.account)
    assert cfg.account == "DU0000000"


def test_B27_startup_and_heartbeat_log_masks_leave_state_and_cards_unchanged(
        formatted_logs, tmp_path):
    _, read = formatted_logs
    rig = Rig(tmp_path / "startup")
    try:
        rig.start()
        rig.advance(minutes=11)
        rig.engine._heartbeat(rig.clock.now())
        identity = rig.store.identity()
        text = read()
        assert "[STARTUP_SUMMARY]" in text and "[HEARTBEAT]" in text
        assert rig.cfg.account not in text and rig.cfg.account[-3:] in text
        assert identity["account"] == rig.cfg.account
        assert rig.engine.status_snapshot(rig.clock.now())["account"] == rig.cfg.account
        assert rig.channel.messages, "logging must not suppress actual startup cards"
    finally:
        rig.alerter.close(); rig.store.close()


def test_B27_actual_sdk_warning_and_error_survive_wrapper_info_suppression(
        formatted_logs, tmp_path, monkeypatch, request):
    _, read = formatted_logs
    adapter, clock, _ = _multi_native(tmp_path, monkeypatch, request, 1)
    clock.advance(seconds=2)
    adapter.positions_fresh()
    before = read()
    assert "position: Position(" not in before
    adapter.ib.wrapper.error(555, 201, "synthetic native rejection account=" + adapter.cfg.account)
    text = read()
    assert "201" in text and "synthetic native rejection" in text
    assert adapter.cfg.account not in text

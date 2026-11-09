"""Batch 20 execution-clock and wrapper logging regressions, fully offline.

Raw wire messages exercise the installed ib_insync decoder.  Every SQLite
file is synthetic pytest tmp_path state; no gateway, John DB, or secrets.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import pytest
from ib_insync import CommissionReport, LimitOrder, OrderStatus, Stock, Trade

from v6lite.broker.ibkr import IbkrBroker
from v6lite.exits import compute_levels
from v6lite.models import FillEvent, OrderRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import make_cfg

UTC = timezone.utc
OCCURRED = datetime(2026, 10, 8, 14, 58, 9, tzinfo=UTC)
RECEIVED = OCCURRED + timedelta(seconds=1, microseconds=104497)
BAD_LEGACY_TIME = OCCURRED + timedelta(hours=7)
EXEC_ID = "0000b020.00000001.01.01"
INTENT_ID = "v6l-b20-time"
POSITION_ID = "b20-time-position"


def _broker(tmp_path):
    # IB() creates its decoder and Event objects but never opens a socket.
    broker = IbkrBroker(make_cfg(tmp_path))
    # Simulate a connected, API-ready socket; requests stay local/mocked.
    broker.ib.client.isConnected = lambda: True
    broker.ib.client._apiReady = True
    def no_wire(message):
        raise AssertionError("offline execution fixture attempted a network send")
    broker.ib.client.conn.sendMsg = no_wire
    assert broker.ib.isConnected()
    broker._ensure_channel_hooks()
    broker.ib.wrapper.lastTime = RECEIVED
    order = LimitOrder("BUY", 3, 337.74, orderId=77, permId=12345,
                       clientId=broker.client_id, account=broker.cfg.account,
                       orderRef=INTENT_ID)
    trade = Trade(Stock("AAPL", "SMART", "USD"), order,
                  OrderStatus(status="Submitted", orderId=77, permId=12345))
    broker.ib.wrapper.permId2Trade[12345] = trade
    broker.ib.execDetailsEvent += broker._on_exec
    return broker


def _wire_fill(broker, raw_time, *, query=False, exec_id=EXEC_ID):
    req_id = 500 if query else -1
    wrapper = broker.ib.wrapper
    if query:
        # A reqExecutions row is not a callback: wrapper returns a NEW Fill.
        wrapper._futures[req_id] = asyncio.get_event_loop().create_future()
        wrapper._results[req_id] = []
    fields = ["11", str(req_id), "77", "265598", "AAPL", "STK", "", "0", "", "",
              "SMART", "USD", "AAPL", "NMS", exec_id, raw_time,
              broker.cfg.account, "NASDAQ", "BOT", "3", "337.74", "12345",
              str(broker.client_id), "0", "3", "337.74", INTENT_ID, "", "0", "", "1"]
    assert len(fields) == 31
    broker.ib.client.decoder.interpret(fields)
    if query:
        fill = wrapper._results.pop(req_id)[0]
        wrapper._futures.pop(req_id).cancel()
        return fill
    return wrapper.fills[exec_id]


def _callback(broker, raw_time="20261008  14:58:09"):
    fill = _wire_fill(broker, raw_time)
    events = broker.drain_events()
    fills = [event for event in events if isinstance(event, FillEvent)]
    assert len(fills) == 1, "an uncertain clock must still deliver the economic fill"
    return fills[0], fill


def _query(broker, raw_time="20261008 14:58:09 UTC"):
    fill = _wire_fill(broker, raw_time, query=True)
    broker.ib.reqExecutions = lambda execution_filter: [fill]
    events = broker.executions_today()
    assert len(events) == 1
    return events[0]


def _store(tmp_path, *, protection=False):
    cfg = make_cfg(tmp_path)
    store = Store(tmp_path / "synthetic.sqlite3", partial(compute_levels, cfg))
    req = OrderRequest(INTENT_ID, "AAPL", Side.BUY, 3, 337.74,
                       Purpose.ENTRY, POSITION_ID)
    plan = ("v6l-b20-protect", 332.74, None) if protection else None
    store.create_intent(RECEIVED - timedelta(seconds=5), req, atr=2.0,
                        count_entry_day=None, protection_plan=plan)
    return store


def _row(store):
    return dict(store.conn.execute("SELECT * FROM fills WHERE exec_id=?", (EXEC_ID,)).fetchone())


def _economics(store):
    fill = _row(store)
    return (tuple((key, value) for key, value in fill.items() if key != "time"),
            tuple(dict(store.position(POSITION_ID)).items()),
            store.intent_filled_qty(INTENT_ID),
            store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0])


def _audit_count(store):
    return store.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]


@pytest.mark.parametrize("raw_time", ["20261008 14:58:09 UTC",
                                      "20261008 07:58:09 America/Los_Angeles",
                                      "20261008 10:58:09 America/New_York"])
def test_explicit_execution_timezone_keeps_the_actual_instant(tmp_path, raw_time):
    broker = _broker(tmp_path)
    event, _ = _callback(broker, raw_time)
    assert event.time == OCCURRED
    assert event.time.utcoffset() == timedelta(0)
    assert not getattr(event, "time_uncertain", False)
    assert (event.quantity, event.price, event.side, event.symbol) == (3, 337.74, Side.BUY, "AAPL")
    assert (event.broker_order_id, event.perm_id, event.intent_id) == (77, 12345, INTENT_ID)


@pytest.mark.parametrize("raw_time", ["20261008  14:58:09", "20261008  07:58:09"])
def test_unknown_naive_callback_books_receipt_not_implicit_host_timezone(tmp_path, raw_time):
    broker = _broker(tmp_path)
    broker.ib.TimezoneTWS = ""
    event, _ = _callback(broker, raw_time)
    assert event.time == RECEIVED
    assert getattr(event, "time_uncertain", False), "fallback must remain visibly unverified"
    assert getattr(event, "time_source", ""), "persist the time provenance"
    assert (event.quantity, event.price) == (3, 337.74)


@pytest.mark.parametrize("tws_timezone, raw_time", [
    ("UTC", "20261008  14:58:09"),
    ("America/Los_Angeles", "20261008  07:58:09"),
    ("America/New_York", "20261008  10:58:09"),
])
def test_naive_execution_uses_explicit_tws_timezone(tmp_path, tws_timezone, raw_time):
    broker = _broker(tmp_path)
    broker.ib.TimezoneTWS = tws_timezone
    event, _ = _callback(broker, raw_time)
    assert event.time == OCCURRED
    assert not getattr(event, "time_uncertain", False)


def test_explicit_utc_query_is_not_polluted_by_bad_callback_clock(tmp_path):
    broker = _broker(tmp_path)
    callback, original = _callback(broker)
    query = _query(broker)
    assert query.time == OCCURRED
    assert not getattr(query, "time_uncertain", False)
    assert getattr(query, "time_source", "")
    assert callback.time == RECEIVED
    broker._on_exec(None, original)
    replay = [e for e in broker.drain_events() if isinstance(e, FillEvent)][0]
    assert replay.time == RECEIVED and getattr(replay, "time_uncertain", False)


def test_new_unverified_fill_confirms_exact_time_once_without_economic_rebooking(tmp_path):
    broker = _broker(tmp_path)
    callback, original = _callback(broker)
    store = _store(tmp_path)
    try:
        assert store.record_fill(RECEIVED, callback).inserted
        assert store.control(f"execution_time_unverified:{EXEC_ID}") is not None
        before = _economics(store)
        query = _query(broker)
        outcome = store.record_fill(RECEIVED + timedelta(seconds=1), query, recovered=True)
        assert not outcome.inserted
        assert _row(store)["time"] == OCCURRED.isoformat()
        assert store.control(f"execution_time_unverified:{EXEC_ID}") is None
        assert _economics(store) == before
        audit_after = _audit_count(store)
        store.record_fill(RECEIVED + timedelta(seconds=2), query, recovered=True)
        broker._on_exec(None, original)
        replay = [e for e in broker.drain_events() if isinstance(e, FillEvent)][0]
        store.record_fill(RECEIVED + timedelta(seconds=3), replay)
        assert _row(store)["time"] == OCCURRED.isoformat()
        assert _economics(store) == before
        assert _audit_count(store) <= audit_after + 1
    finally:
        store.close()


def test_authoritative_query_first_then_bad_callback_never_regresses_clock(tmp_path):
    broker = _broker(tmp_path)
    query = _query(broker)
    store = _store(tmp_path)
    try:
        store.record_fill(RECEIVED, query, recovered=True)
        before = _economics(store)
        # Deliver another callback object directly, without wrapper's own execId suppression.
        late = SimpleNamespace(execution=SimpleNamespace(
            execId=EXEC_ID, orderRef=INTENT_ID, side="BOT", shares=3.0,
            price=337.74, time=BAD_LEGACY_TIME, orderId=77, permId=12345,
            acctNumber=broker.cfg.account), contract=SimpleNamespace(symbol="AAPL"),
            time=RECEIVED)
        broker._on_exec(None, late)
        event = [e for e in broker.drain_events() if isinstance(e, FillEvent)][0]
        store.record_fill(RECEIVED + timedelta(seconds=1), event)
        assert _row(store)["time"] == OCCURRED.isoformat()
        assert _economics(store) == before
    finally:
        store.close()


def test_time_marker_survives_restart_and_allows_only_new_fill_confirmation(tmp_path):
    broker = _broker(tmp_path)
    callback, _ = _callback(broker)
    store = _store(tmp_path)
    store.record_fill(RECEIVED, callback)
    before = _economics(store)
    path = store.path
    store.close()
    cfg = make_cfg(tmp_path)
    reopened = Store(path, partial(compute_levels, cfg))
    try:
        assert reopened.control(f"execution_time_unverified:{EXEC_ID}") is not None
        restarted_broker = _broker(tmp_path)
        query = _query(restarted_broker)
        reopened.record_fill(RECEIVED + timedelta(seconds=2), query, recovered=True)
        assert _row(reopened)["time"] == OCCURRED.isoformat()
        assert _economics(reopened) == before
    finally:
        reopened.close()


def test_legacy_fill_without_marker_is_reviewed_once_and_never_auto_retimed(tmp_path):
    broker = _broker(tmp_path)
    correct = _query(broker)
    # Model a batch-19 record without the new provenance marker.
    legacy = FillEvent(EXEC_ID, INTENT_ID, "AAPL", Side.BUY, 3, 337.74,
                       BAD_LEGACY_TIME, 77, 12345)
    store = _store(tmp_path)
    try:
        store.record_fill(RECEIVED, legacy)
        assert store.control(f"execution_time_unverified:{EXEC_ID}") is None
        before = _economics(store)
        audits_before = _audit_count(store)
        store.record_fill(RECEIVED + timedelta(seconds=1), correct, recovered=True)
        assert _row(store)["time"] == BAD_LEGACY_TIME.isoformat()
        assert _economics(store) == before
        audits_after = _audit_count(store)
        assert audits_after > audits_before, "legacy mismatch needs operator review evidence"
        store.record_fill(RECEIVED + timedelta(seconds=2), correct, recovered=True)
        assert _audit_count(store) == audits_after, "duplicate polls must not flood review audit"
        assert _row(store)["time"] == BAD_LEGACY_TIME.isoformat()
    finally:
        store.close()


@pytest.mark.parametrize("changes", [
    {"price": 337.75}, {"quantity": 4}, {"symbol": "MSFT"}, {"side": Side.SELL},
    {"intent_id": "v6l-other"}, {"broker_order_id": 78}, {"perm_id": 99999},
    {"time": OCCURRED + timedelta(days=1)},
])
def test_same_exec_with_conflicting_identity_economics_or_day_is_not_retimed(tmp_path, changes):
    broker = _broker(tmp_path)
    callback, _ = _callback(broker)
    store = _store(tmp_path)
    try:
        store.record_fill(RECEIVED, callback)
        before = _economics(store)
        query = replace(_query(broker), **changes)
        store.record_fill(RECEIVED + timedelta(seconds=1), query, recovered=True)
        assert _row(store)["time"] == RECEIVED.isoformat()
        assert _economics(store) == before
        assert store.control(f"execution_time_unverified:{EXEC_ID}") is not None
    finally:
        store.close()


def test_incomplete_execution_query_cannot_confirm_new_fill_clock(tmp_path):
    broker = _broker(tmp_path)
    callback, original = _callback(broker)
    store = _store(tmp_path)
    try:
        store.record_fill(RECEIVED, callback)
        before = _economics(store)
        requests = []
        def incomplete(execution_filter):
            requests.append(execution_filter)
            partial = _wire_fill(broker, "20261008 14:58:09 UTC", query=True)
            assert partial.execution.execId == EXEC_ID
            raise TimeoutError("synthetic reqExecutions incomplete")
        broker.ib.reqExecutions = incomplete
        with pytest.raises(TimeoutError, match="synthetic reqExecutions incomplete"):
            broker.executions_today()
        assert len(requests) == 1
        assert requests[0].clientId == broker.client_id
        assert requests[0].acctCode == broker.cfg.account
        broker._on_exec(None, original)
        replay = [e for e in broker.drain_events() if isinstance(e, FillEvent)][0]
        store.record_fill(RECEIVED + timedelta(seconds=1), replay)
        assert _row(store)["time"] == RECEIVED.isoformat()
        assert _economics(store) == before
        assert store.control(f"execution_time_unverified:{EXEC_ID}") is not None
    finally:
        store.close()


def test_time_confirmation_never_moves_protection_gap_deadline(tmp_path):
    broker = _broker(tmp_path)
    callback, _ = _callback(broker)
    store = _store(tmp_path, protection=True)
    try:
        store.record_fill(RECEIVED, callback)
        before_gap = dict(store.conn.execute(
            "SELECT * FROM protection_gap_a WHERE exec_id=?", (EXEC_ID,)).fetchone())
        before_protection = dict(store.protection(POSITION_ID))
        store.record_fill(RECEIVED + timedelta(seconds=1), _query(broker), recovered=True)
        assert _row(store)["time"] == OCCURRED.isoformat()
        assert dict(store.conn.execute(
            "SELECT * FROM protection_gap_a WHERE exec_id=?", (EXEC_ID,)).fetchone()) == before_gap
        assert dict(store.protection(POSITION_ID)) == before_protection
    finally:
        store.close()


@pytest.mark.parametrize("root_level", [logging.INFO, logging.DEBUG])
def test_wrapper_fill_spam_only_moves_to_debug_and_preserves_diagnostics(tmp_path, root_level):
    from v6lite.broker.ibkr import install_execution_log_filter
    root = logging.getLogger()
    wrapper = logging.getLogger("ib_insync.wrapper")
    query_logger = logging.getLogger("v6lite.ibkr")
    old_handlers, old_root_level = list(root.handlers), root.level
    old_wrapper_level, old_query_level = wrapper.level, query_logger.level
    records = []
    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)
    handler = Capture()
    try:
        root.handlers = [handler]
        root.setLevel(root_level)
        wrapper.setLevel(logging.NOTSET)
        query_logger.setLevel(logging.NOTSET)
        install_execution_log_filter()
        installed_count = len(wrapper.filters)
        install_execution_log_filter()
        assert len(wrapper.filters) == installed_count
        wrapper.info("execDetails Execution(synthetic)")
        wrapper.info("execDetails: Fill(synthetic)")
        wrapper.info("commissionReport: CommissionReport(synthetic)")
        wrapper.info("Warning 202: synthetic order cancelled")
        wrapper.info("position: synthetic account-free summary")
        wrapper.warning("Warning 1100: synthetic connectivity lost")
        wrapper.warning("execDetails diagnostic warning")
        query_logger.info("[QUERY] channel=executions secs=0.123")
        noisy = [r for r in records if r.getMessage().startswith((
            "execDetails Execution", "execDetails: Fill", "commissionReport:"))]
        if root_level == logging.INFO:
            assert not noisy
        else:
            assert len(noisy) == 3
            assert all(r.levelno == logging.DEBUG for r in noisy)
        kept = {r.getMessage(): r.levelno for r in records if r not in noisy}
        assert kept["Warning 202: synthetic order cancelled"] == logging.INFO
        assert kept["position: synthetic account-free summary"] == logging.INFO
        assert kept["Warning 1100: synthetic connectivity lost"] == logging.WARNING
        assert kept["execDetails diagnostic warning"] == logging.WARNING
        assert kept["[QUERY] channel=executions secs=0.123"] == logging.INFO
    finally:
        root.handlers = old_handlers
        root.setLevel(old_root_level)
        wrapper.setLevel(old_wrapper_level)
        query_logger.setLevel(old_query_level)
        handler.close()


def test_verified_sell_time_recomputes_closed_at_without_reapplying_pnl(tmp_path):
    broker = _broker(tmp_path)
    entry, _ = _callback(broker, "20261008 14:58:09 UTC")
    store = _store(tmp_path)
    sell_id = "v6l-b20-time-sell"
    sell_exec = "0000b020.00000002.01.01"
    sell_occurred = OCCURRED + timedelta(minutes=5)
    sell_received = RECEIVED + timedelta(minutes=5)
    try:
        store.record_fill(RECEIVED, entry)
        store.create_intent(sell_occurred - timedelta(seconds=2),
                            OrderRequest(sell_id, "AAPL", Side.SELL, 3, 337.24,
                                         Purpose.STOP, POSITION_ID),
                            atr=None, count_entry_day=None)
        sell_order = LimitOrder("SELL", 3, 337.24, orderId=78, permId=54321,
                               clientId=broker.client_id, account=broker.cfg.account,
                               orderRef=sell_id)
        broker.ib.wrapper.permId2Trade[54321] = Trade(
            Stock("AAPL", "SMART", "USD"), sell_order,
            OrderStatus(status="Submitted", orderId=78, permId=54321))
        broker.ib.wrapper.lastTime = sell_received
        fields = ["11", "-1", "78", "265598", "AAPL", "STK", "", "0", "", "",
                  "SMART", "USD", "AAPL", "NMS", sell_exec, "20261008  15:03:09",
                  broker.cfg.account, "NASDAQ", "SLD", "3", "337.24", "54321",
                  str(broker.client_id), "0", "3", "337.24", sell_id, "", "0", "", "1"]
        broker.ib.client.decoder.interpret(fields)
        sell = [e for e in broker.drain_events() if isinstance(e, FillEvent)][0]
        assert sell.time == sell_received and sell.time_uncertain
        outcome = store.record_fill(sell_received, sell)
        assert outcome.closed_position_id == POSITION_ID
        before = dict(store.position(POSITION_ID))
        assert before["status"] == "CLOSED" and before["closed_at"] == sell_received.isoformat()
        wrapper = broker.ib.wrapper
        wrapper._futures[500] = asyncio.get_event_loop().create_future()
        wrapper._results[500] = []
        fields[1], fields[15] = "500", "20261008 15:03:09 UTC"
        broker.ib.client.decoder.interpret(fields)
        full_query_rows = wrapper._results.pop(500)
        wrapper._futures.pop(500).cancel()
        broker.ib.reqExecutions = lambda execution_filter: full_query_rows
        verified = broker.executions_today()[0]
        store.record_fill(sell_received + timedelta(seconds=1), verified, recovered=True)
        current = dict(store.position(POSITION_ID))
        latest = store.conn.execute("SELECT MAX(time) FROM fills WHERE position_id=?",
                                    (POSITION_ID,)).fetchone()[0]
        assert latest == sell_occurred.isoformat()
        assert current["closed_at"] == latest, "close time must follow the position's latest execution"
        assert {k: v for k, v in current.items() if k != "closed_at"} == {
            k: v for k, v in before.items() if k != "closed_at"}
        assert store.intent_filled_qty(sell_id) == 3
        assert store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 2
    finally:
        store.close()


def test_execution_request_error_after_partial_row_is_not_complete_time_evidence(tmp_path):
    from ib_insync.wrapper import RequestError
    broker = _broker(tmp_path)
    callback, _ = _callback(broker)
    store = _store(tmp_path)
    try:
        store.record_fill(RECEIVED, callback)
        before = _economics(store)
        # The real ib_insync request future must reject an error-terminated query.
        # Its default RaiseRequestErrors=False otherwise returns these partial rows.
        broker.ib.client.getReqId = lambda: 600
        requests, partial_rows, errors = [], [], []
        def observed_error(req_id, code, reason, contract):
            errors.append((req_id, code, reason))
        broker.ib.errorEvent += observed_error
        prior_raise_errors = broker.ib.RaiseRequestErrors
        def send_partial(req_id, execution_filter):
            requests.append((req_id, execution_filter))
            fields = ["11", str(req_id), "77", "265598", "AAPL", "STK", "", "0", "", "",
                      "SMART", "USD", "AAPL", "NMS", EXEC_ID, "20261008 14:58:09 UTC",
                      broker.cfg.account, "NASDAQ", "BOT", "3", "337.74", "12345",
                      str(broker.client_id), "0", "3", "337.74", INTENT_ID, "", "0", "", "1"]
            broker.ib.client.decoder.interpret(fields)
            partial_rows.extend(broker.ib.wrapper._results[req_id])
            broker.ib.wrapper.error(req_id, 322, "synthetic execution query failed", "")
        broker.ib.client.reqExecutions = send_partial
        with pytest.raises(RequestError, match="synthetic execution query failed") as rejected:
            broker.executions_today()
        assert len(requests) == 1 and requests[0][0] == 600
        assert requests[0][1].clientId == broker.client_id
        assert requests[0][1].acctCode == broker.cfg.account
        assert len(partial_rows) == 1 and partial_rows[0].execution.execId == EXEC_ID
        assert partial_rows[0].execution.shares == 3
        assert errors == [(600, 322, "synthetic execution query failed")]
        assert rejected.value.reqId == 600 and rejected.value.code == 322
        assert broker.ib.RaiseRequestErrors is prior_raise_errors
        assert _row(store)["time"] == RECEIVED.isoformat()
        assert _economics(store) == before
        assert store.control(f"execution_time_unverified:{EXEC_ID}") is not None
    finally:
        store.close()

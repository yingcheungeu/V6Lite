"""Batch-8 self-review (adversarial pass before delivery). D1-D3 and A1-A3
failed on the first batch-8 version; S1 pins that a reconnect and quiet
but live ticks keep quotes usable.

D1  an orphan found by the gate's own order query blocks that BUY
D2  a kill switch set during the gate's query blocks that BUY
D3  an anomaly event arriving during the query blocks that BUY
A1  price evidence is keyed by our symbol, not IBKR's spelling
A2  a tick of a cancelled request id never counts for the new subscription
A3  delayed tick types never count as live under a real-time config
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from types import SimpleNamespace

from v6lite.broker.fake import _Order
from v6lite.models import AnomalyEvent, OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, et, make_cfg


def _entries(rig):
    return [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]


def _scan_without_reconcile(rig):
    rig.at(et(10, 0, 30))
    rig.engine._last_reconcile = rig.clock.now()
    rig.engine._reconcile_due = False
    rig.tick()


# --- D1: the gate's own fresh query finds an orphan v6l- order (raises the
#     anomaly + "New entries are blocked" page) and then sends the BUY anyway
def test_D1_orphan_found_by_the_gate_query_is_blocked(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    orphan = OrderRequest("v6l-orphan00001", "TSLA", Side.SELL, 3, 260.0, Purpose.TP1, None)
    rig.broker.orders[orphan.intent_id] = _Order(orphan, 4242)          # working, unknown to this ledger
    _scan_without_reconcile(rig)
    assert any(k.startswith("anomaly:orphan_order:") for k in rig.store.anomalies()), rig.store.anomalies()
    assert not _entries(rig), "BUY sent although the gate itself just raised orphan_order (entries blocked)"


# --- D2: durable kill switch set while the (up to ~19 s) gate query runs
def test_D2_kill_switch_set_during_gate_query_is_blocked(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    real = rig.broker.open_orders

    def slow_query():
        rig.store.kill(rig.clock.now(), "operator kill during query")
        return real()
    rig.broker.open_orders = slow_query
    _scan_without_reconcile(rig)
    assert not _entries(rig), "BUY sent after the kill switch was set (blocks were checked before the query)"


# --- D3: a broker anomaly delivered during the gate query (ib.sleep inside
#     reqAllOpenOrders) is not drained before the BUY
def test_D3_anomaly_event_during_gate_query_is_blocked(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    real = rig.broker.open_orders

    def slow_query():
        rig.broker.events.append(AnomalyEvent("non_integer_execution:x1", "TSLA shares=0.5"))
        return real()
    rig.broker.open_orders = slow_query
    _scan_without_reconcile(rig)
    assert not _entries(rig), "BUY sent while an anomaly event that arrived during the query was still queued"


# ======================================================== adapter (real ib_insync objects, no socket)
FIXED = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)


def _broker(tmp_path, key="AAPL", ib_symbol="AAPL"):
    from ib_insync import Stock
    from v6lite.broker.ibkr import IbkrBroker
    broker = IbkrBroker(make_cfg(tmp_path))
    ib = broker.ib
    assert broker._ensure_channel_hooks()
    req = {"id": 14}
    ib.reqMktData = lambda c, *a: ib.wrapper.startTicker(req["id"], c, "mktData")
    ib.cancelMktData = lambda c: ib.wrapper.endTicker(ib.wrapper.tickers.get(id(c)), "mktData")
    broker._contracts[key] = Stock(ib_symbol, "SMART", "USD", conId=72063691)
    broker.subscribe_quotes([key])
    return broker, ib, req


def _batch(ib):
    ib.wrapper.lastTime = FIXED
    ib.wrapper.tcpDataProcessed()


def _ticks(ib, rid, types=(1, 2, 4), px=(99.95, 100.05, 100.0)):
    for t, p in zip(types, px):
        ib.wrapper.priceSizeTick(rid, t, p, 100)
    _batch(ib)


# A1: the hook keys evidence by Ticker.contract.symbol (IBKR's spelling after
# qualifyContracts), quote() by the engine's key -> any difference = no quote ever
def test_A1_symbol_spelling_difference_does_not_starve_quote(tmp_path):
    broker, ib, req = _broker(tmp_path, key="BRK.B", ib_symbol="BRK B")
    _ticks(ib, 14)
    assert broker.quote("BRK.B") is not None, "live ticks of our own subscription, but quote() is None"


# A2: an in-flight tick of the CANCELLED request id (reqId2Ticker keeps it and
# maps to the same reused Ticker) counts as a price of the NEW subscription
def test_A2_old_request_id_tick_does_not_count_for_new_subscription(tmp_path):
    broker, ib, req = _broker(tmp_path)
    _ticks(ib, 14)
    req["id"] = 15
    ib.cancelMktData(broker._tickers["AAPL"].contract)
    broker._tickers.pop("AAPL")
    broker.subscribe_quotes(["AAPL"])
    _ticks(ib, 14, types=(4,), px=(90.0,))                         # old reqId, after the new request
    assert broker.quote("AAPL") is None, "a tick of the cancelled request revived the quote"


# A3: on the reused Ticker marketDataType is still 1 from the old
# subscription; delayed price ticks (66-68) count as live evidence under a
# live configuration until IBKR's marketDataType message arrives
def test_A3_delayed_tick_types_do_not_count_as_live_under_live_config(tmp_path):
    broker, ib, req = _broker(tmp_path)
    _ticks(ib, 14)
    req["id"] = 15
    broker._tickers.pop("AAPL")
    broker.subscribe_quotes(["AAPL"])
    broker._on_error(15, 10167, "Displaying delayed market data.", None)
    _ticks(ib, 15, types=(66, 67, 68), px=(98.0, 98.1, 98.05))
    assert broker.quote("AAPL") is None, "delayed ticks served as a live quote"


# sanity (expected to PASS): reconnect path -- wrapper.reset() gives a new
# Ticker; the hook (instance attribute) survives; quiet repeated ticks keep it live
def test_S1_reconnect_and_quiet_ticks_keep_quotes(tmp_path):
    broker, ib, req = _broker(tmp_path)
    _ticks(ib, 14)
    ib.wrapper.connectionClosed()                                     # socket gone: reqId2Ticker/tickers reset
    req["id"] = 3
    symbols, broker._tickers = list(broker._tickers), {}
    broker.subscribe_quotes(symbols)
    assert broker.quote("AAPL") is None
    _ticks(ib, 3)
    q = broker.quote("AAPL")
    assert q is not None and q.bid == 99.95
    for _ in range(5):
        _ticks(ib, 3)                                                  # unchanged: ib_insync early-returns
    assert broker.quote("AAPL") is not None

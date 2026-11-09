"""Batch27 audit contracts: synthetic economics, real observer/Store paths.

Only the 10/15 sample payload and scale originate in the sanitized volume note.
Accounts, contracts, fills and native replies below are synthetic.  The 1s case
is an explicit replay pressure cadence; it does not alter the 15s scheduler.
No new production API is imported, so B26 and B27b fail on behavior.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine
from v6lite.models import OrderRequest, ProtectionRequest, Purpose, Side, PriceObservationEvent
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et
from v6lite.tests.test_batch24_stp_echo import NativeIB
from v6lite.tests.test_batch26_overnight_evidence import _no_network, _safety, _writes

SYMBOLS = ("AAPL", "MSFT", "META", "NVDA", "AMZN", "GOOG", "TSLA", "SPY", "QQQ", "AMD")
START = et(7, 16, 41, day=(2026, 10, 15))
HOURS = 13
SAMPLE = {
    "aux_price": 340.52, "broker_echo_lmt": 0.0, "cap_valid": False,
    "error_code": 0, "intent_id": "v6l-protect-163f331d4c894f33a1a8",
    "mkt_cap_price": None, "mkt_cap_price_raw": "0.0",
    "position_id": "pos-99ec110ffc6246e8b7c4", "reason": "", "sent_price": None,
    "source": "openOrders", "symbol": "AAPL", "working_price": None,
}


# Batch31 adds native diagnostics. These values describe this synthetic
# fixture only; SAMPLE above remains the original sanitized 10/15 payload.
NATIVE_METADATA = {
    "status": "PreSubmitted", "total_quantity": 3, "filled": 0.0, "remaining": 3.0,
    "order_id": 1002, "perm_id": 51002, "con_id": 10000, "client_id": 131,
    "account_matches": True, "connection_snapshot": False, "query_snapshot": True,
    "socket_generation": 0, "pending_generation": 0, "pending_trigger": None,
    "pending_qty": None, "confirmed_trigger": 340.52,
}

class _CounterTrace:
    def __init__(self):
        self.counts = Counter()
    def append(self, item):
        self.counts[item[0]] += 1


class _VolumeIB(NativeIB):
    """Local native reply source; real IbkrBroker validates every reply."""
    def __init__(self, clock):
        super().__init__(clock)
        self.queries = _CounterTrace()
        self.connected = True
    def isConnected(self):
        return self.connected
    def disconnect(self):
        self.connected = False


def _rows(store, kind):
    return [json.loads(r["payload"]) for r in store.conn.execute(
        "SELECT payload FROM audit WHERE kind=? ORDER BY id", (kind,))]


def _count(store, kind):
    return store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind=?", (kind,)).fetchone()[0]


def _snapshot(store):
    return {name: [tuple(r) for r in store.conn.execute("SELECT * FROM " + name + " ORDER BY 1")]
            for name in ("positions", "fills", "commissions", "intents", "controls")}


def _trade(rig, row):
    order = SimpleNamespace(orderRef=row["intent_id"], orderId=row["broker_order_id"],
        permId=row["perm_id"], account=rig.cfg.account, clientId=rig.cfg.client_id,
        action="SELL", totalQuantity=3, orderType="STP", tif="GTC",
        auxPrice=row["trigger_price"], lmtPrice=0.0, outsideRth=False)
    status = SimpleNamespace(orderId=order.orderId, permId=order.permId,
        clientId=order.clientId, status="PreSubmitted", filled=0.0, remaining=3.0,
        mktCapPrice=0.0, avgFillPrice=0.0, whyHeld="trigger")
    contract = SimpleNamespace(symbol=row["symbol"], conId=row["con_id"], secType="STK",
        currency="USD", exchange="SMART", primaryExchange="NASDAQ",
        localSymbol=row["symbol"], tradingClass="NMS")
    return SimpleNamespace(order=order, orderStatus=status, contract=contract,
                           log=[], fills=[], advancedError="")


@pytest.fixture
def observation_rig(tmp_path, monkeypatch, request):
    rigs = []
    def make(*, count=1, memory=True, suffix=""):
        symbols = SYMBOLS[:count]
        rig = Rig(tmp_path / ("audit-" + str(len(rigs)) + suffix), start=START,
            protection=ProtectionConfig(enabled=True), scan_symbols=symbols,
            tradable_symbols=symbols, etf_symbols=tuple(s for s in symbols if s in {"SPY", "QQQ"}),
            sectors=tuple((s, s) for s in symbols), max_positions=max(10, count),
            max_per_sector=max(10, count), max_daily_entries=20)
        rigs.append(rig)
        if memory:
            old = rig.store
            rig.store = Store(":memory:", old._levels_fn)
            old.close()
            rig.engine.store = rig.store
            rig.store.report_clock = rig.clock
        rig.store.bind_identity(rig.cfg.account, rig.cfg.mode, client_id=rig.cfg.client_id)
        rig.broker.connect()
        rig.engine.flags.broker_connected = True
        rig._b26_market_calls = []
        pids = []
        for index, symbol in enumerate(symbols):
            pid = SAMPLE["position_id"] if index == 0 else "pos-b27-volume-" + str(index)
            ref = SAMPLE["intent_id"] if index == 0 else "v6l-protect-b27-" + str(index)
            conid = rig.broker.qualify(symbol)
            rig.store.set_control(START, "conid:" + symbol, str(conid))
            req = OrderRequest("v6l-entry-b27-" + str(index), symbol, Side.BUY, 3, 342.53,
                               Purpose.ENTRY, pid)
            rig.store.create_intent(START, req, atr=1.0, count_entry_day=None)
            ack = rig.broker.submit_limit(req)
            rig.store.update_intent(START, req.intent_id, status="SENT",
                broker_order_id=ack.broker_order_id, perm_id=ack.perm_id)
            fill = rig.broker.fill(req.intent_id, qty=3, price=342.53, emit=False)
            rig.store.record_fill(START, fill)
            rig.store.update_intent(START, req.intent_id, status="FILLED")
            rig.store.create_protection_plan(START, pid, ref, symbol, 340.52, None)
            stop = ProtectionRequest(ref, pid, symbol, 3, 340.52, con_id=conid,
                client_id=rig.cfg.client_id, account=rig.cfg.account)
            rig.store.create_intent(START, OrderRequest(ref, symbol, Side.SELL, 3, None,
                Purpose.PROTECT, pid), atr=None, count_entry_day=None)
            stop_ack = rig.broker.submit_stop(stop)
            rig.store.update_intent(START, ref, status="SENT",
                broker_order_id=stop_ack.broker_order_id, perm_id=stop_ack.perm_id)
            rig.store.update_protection(START, pid, account=rig.cfg.account,
                client_id=rig.cfg.client_id, con_id=conid, requested_qty=3,
                confirmed_remaining=3, pending_max_remaining=3, state="WORKING",
                broker_order_id=stop_ack.broker_order_id, perm_id=stop_ack.perm_id,
                confirmed_at=START)
            rig.px(symbol, 342.53)
            pids.append(pid)
        rig.broker.events.clear()
        adapter = IbkrBroker(rig.cfg)
        real_ib = adapter.ib
        request.addfinalizer(real_ib.client.reset)
        adapter.ib = _VolumeIB(rig.clock)
        adapter.ib.snapshot = [_trade(rig, rig.store.protection(pid)) for pid in pids]
        adapter.ib.positions_provider = lambda: [
            SimpleNamespace(account=rig.cfg.account, contract=t.contract, position=3.0, avgCost=342.53)
            for t in adapter.ib.snapshot]
        monkeypatch.setattr("v6lite.broker.ibkr.time.monotonic", lambda: rig.clock.now().timestamp())
        original_drain = rig.broker.drain_events
        def drain():
            return original_drain() + adapter.drain_events()
        monkeypatch.setattr(rig.broker, "open_orders", adapter.open_orders)
        monkeypatch.setattr(rig.broker, "positions_fresh", adapter.positions_fresh)
        monkeypatch.setattr(rig.broker, "drain_events", drain)
        # Query generation is the adapter's actual reset counter, not a
        # ConnectionEvent and not an invented proof token.
        rig.broker._b27_native = adapter
        monkeypatch.setattr(type(rig.broker), "query_generation",
            property(lambda b: b._b27_native.query_generation), raising=False)
        rig._b27_native = adapter
        rig._b27_pids = pids
        rig.channel.messages.clear()
        _safety(rig)
        return rig
    yield make
    for rig in rigs:
        rig.alerter.close()
        rig.store.close()


def _observe(rig, trade=None, *, source="openOrders", error_code=0, reason=""):
    adapter = rig._b27_native
    trade = trade or adapter.ib.snapshot[0]
    adapter._observe_prices(trade, source=source, error_code=error_code, reason=reason)
    events = adapter.drain_events()
    prices = [event for event in events if isinstance(event, PriceObservationEvent)]
    assert len(prices) == 1, "synthetic native reply must decode one real price observation"
    rig.engine._handle_event(rig.clock.now(), prices[0])
    return prices[0]


def _summary_items(store):
    for envelope in _rows(store, "OBSERVATION_SUMMARY"):
        observations = envelope.get("observations")
        assert isinstance(observations, list) and observations, "summary must preserve per-kind observations"
        for item in observations:
            assert set(("kind", "intent_id", "position_id", "count", "first_at", "last_at")) <= set(item)
            assert type(item["count"]) is int and item["count"] > 0
            assert item["first_at"] <= item["last_at"]
            yield item


@pytest.mark.parametrize("holdings", [1, 10], ids=["one", "ten"])
@pytest.mark.parametrize("seconds", [15, 1], ids=["15s", "1s-pressure"])
def test_B27_thirteen_hours_native_observations_are_bounded_and_counted(
        observation_rig, holdings, seconds, record_property):
    rig = observation_rig(count=holdings)
    record_property("b27_holdings", holdings)
    record_property("b27_interval_seconds", seconds)
    record_property("b27_simulated_hours", HOURS)
    baseline = _snapshot(rig.store)
    writes = _writes(rig)
    initial = {kind: _count(rig.store, kind) for kind in ("ORDER_PRICE_OBSERVATION", "PROTECT_STATE")}
    rounds = HOURS * 3600 // seconds
    for index in range(rounds):
        rig.at(START + timedelta(seconds=index * seconds))
        # One account-wide native query returns every synthetic holding.
        orders = rig.broker.open_orders()
        positions = rig.broker.positions_fresh()
        assert len(orders) == len(positions) == holdings
        rig.engine._note_working(rig.clock.now(), orders)
        rig.engine._drain(rig.clock.now())
        for pid in rig._b27_pids:
            # The same real state writer used by a successful steady proof.
            # No authority is granted by this synthetic confirmation refresh.
            rig.store.update_protection(rig.clock.now(), pid, confirmed_at=rig.clock.now())
        _safety(rig)
        if index < 8 or index % 1800 == 0:
            for kind in initial:
                amount = _count(rig.store, kind) - initial[kind]
                if amount > 5 * holdings:
                    record_property("b27_failure_kind", kind)
                    record_property("b27_failure_writes", amount)
                    record_property("b27_completed_rounds", index + 1)
                assert amount <= 5 * holdings, (kind, amount, holdings, index)
    assert rig._b27_native.ib.queries.counts == Counter({"openOrders": rounds, "positions": rounds})
    assert _writes(rig) == writes and _snapshot(rig.store) == baseline
    # Shutdown may spend time on final native queries; it must not invent a
    # newer broker confirmation. Preserve the exact final confirmation time.
    last_confirmed_time = rig.clock.now().isoformat()
    assert all(rig.store.protection(pid)["confirmed_at"] == last_confirmed_time for pid in rig._b27_pids)
    # The real shutdown path must flush the residual, retain GTCs and disconnect.
    rig.engine.shutdown(grace_seconds=0)
    summaries = _rows(rig.store, "OBSERVATION_SUMMARY")
    assert 0 < len(summaries) <= 30 * holdings, ("summary count", len(summaries))
    suppressed = Counter()
    for item in _summary_items(rig.store):
        suppressed[(item["position_id"], item["kind"])] += item["count"]
    for pid in rig._b27_pids:
        price_writes = sum(item.get("position_id") == pid for item in _rows(rig.store, "ORDER_PRICE_OBSERVATION"))
        state_writes = sum(item.get("position_id") == pid for item in _rows(rig.store, "PROTECT_STATE")) - 1
        # Shutdown's final snapshots are queued by this offline adapter; only
        # observations actually handled by Engine count as audit attempts.
        assert suppressed[(pid, "ORDER_PRICE_OBSERVATION")] + price_writes == rounds
        assert suppressed[(pid, "PROTECT_STATE")] + state_writes == rounds
        assert rig.store.protection(pid)["confirmed_at"] == last_confirmed_time
    record_property("b27_audit_metrics", json.dumps({
        "rounds": rounds, "holdings": holdings, "interval_seconds": seconds,
        "last_confirmed_time": last_confirmed_time, "shutdown_completed_at": rig.clock.now().isoformat(),
        "native_queries": dict(rig._b27_native.ib.queries.counts),
        "target_writes": {kind: _count(rig.store, kind) - initial[kind] for kind in initial},
        "summary_rows": len(summaries),
        "suppressed_counts": {pid + ":" + kind: count for (pid, kind), count in suppressed.items()}
    }, sort_keys=True))
    assert not rig.broker.cancels, "shutdown must retain every confirmed GTC"


PROTECTION_CHANGES = {
    "intent_id": "v6l-protect-rebound", "account": "DU0000000", "client_id": 231,
    "con_id": 991234, "trigger_price": 340.53, "limit_price": 340.00,
    "requested_qty": 4, "confirmed_remaining": 2, "pending_max_remaining": 4,
    "pending_qty": 2, "state": "UNKNOWN", "pending_trigger": 340.53,
    "pending_limit": 340.00, "modify_generation": 7, "status_seen_generation": 8,
    "broker_order_id": 90123, "perm_id": 901234, "gap_a_started_at": START,
    "gap_b_started_at": START, "tp_stage": "TP1", "tp_qty": 1,
    "tp_intent_id": "v6l-tp-b27-change",
}


@pytest.mark.parametrize("field", tuple(PROTECTION_CHANGES))
def test_B27_each_protection_field_change_keeps_legacy_payload_then_suppresses_only_unchanged(
        observation_rig, field):
    rig = observation_rig()
    pid = rig._b27_pids[0]
    before = _count(rig.store, "PROTECT_STATE")
    rig.advance(seconds=1)
    value = PROTECTION_CHANGES[field]
    rig.store.update_protection(rig.clock.now(), pid, **{field: value}, confirmed_at=rig.clock.now())
    assert _count(rig.store, "PROTECT_STATE") == before + 1
    expected = value.isoformat() if hasattr(value, "isoformat") else value
    assert _rows(rig.store, "PROTECT_STATE")[-1] == {
        "position_id": pid, field: expected, "confirmed_at": rig.clock.now().isoformat()}
    rig.advance(seconds=1)
    rig.store.update_protection(rig.clock.now(), pid, **{field: value}, confirmed_at=rig.clock.now())
    assert _count(rig.store, "PROTECT_STATE") == before + 1, ("unchanged field", field)
    assert rig.store.protection(pid)["confirmed_at"] == rig.clock.now().isoformat()


def test_B27_confirmed_at_refresh_persists_without_audit(observation_rig):
    rig = observation_rig()
    pid = rig._b27_pids[0]
    before = _count(rig.store, "PROTECT_STATE")
    rig.advance(seconds=15)
    rig.store.update_protection(rig.clock.now(), pid, confirmed_at=rig.clock.now())
    assert rig.store.protection(pid)["confirmed_at"] == rig.clock.now().isoformat()
    assert _count(rig.store, "PROTECT_STATE") == before


@pytest.mark.parametrize("changed", ["source", "aux_price", "broker_echo_lmt", "mkt_cap_price",
    "mkt_cap_price_raw", "cap_valid", "error_code", "reason", "intent_id", "symbol", "position_id"])
def test_B27_price_payload_changes_are_never_lost(observation_rig, changed):
    rig = observation_rig()
    old = _observe(rig)
    first = _rows(rig.store, "ORDER_PRICE_OBSERVATION")[-1]
    assert first == SAMPLE | NATIVE_METADATA
    trade = copy.deepcopy(rig._b27_native.ib.snapshot[0])
    kwargs = {}
    if changed == "source":
        kwargs["source"] = "orderStatus"
    elif changed == "aux_price":
        # Change the local expected trigger too: this tests observation
        # deduplication, not a foreign/native price mismatch.
        rig.store.update_protection(rig.clock.now(), rig._b27_pids[0], trigger_price=340.53)
        trade.order.auxPrice = 340.53
    elif changed == "broker_echo_lmt":
        trade.order.lmtPrice = 12.34
    elif changed in {"mkt_cap_price", "cap_valid"}:
        trade.orderStatus.mktCapPrice = 340.52
    elif changed == "mkt_cap_price_raw":
        trade.orderStatus.mktCapPrice = "0"
    elif changed == "error_code":
        kwargs["error_code"] = 202
    elif changed == "reason":
        kwargs["reason"] = "synthetic native diagnostic"
    elif changed == "intent_id":
        new = "v6l-protect-new-lineage"
        rig.store.conn.execute("UPDATE intents SET intent_id=? WHERE intent_id=?", (new, old.identity.intent_id))
        rig.store.conn.execute("UPDATE protections SET intent_id=? WHERE intent_id=?", (new, old.identity.intent_id))
        trade.order.orderRef = new
    elif changed == "symbol":
        rig.store.conn.execute("UPDATE intents SET symbol='MSFT' WHERE intent_id=?", (old.identity.intent_id,))
        rig.store.conn.execute("UPDATE protections SET symbol='MSFT' WHERE intent_id=?", (old.identity.intent_id,))
        trade.contract.symbol = "MSFT"
    elif changed == "position_id":
        # Synthetic lineage rebinding only; no operational migration is implied.
        new = "pos-b27-rebound"
        rig.store.conn.execute("UPDATE intents SET position_id=? WHERE intent_id=?", (new, old.identity.intent_id))
        rig.store.conn.execute("UPDATE protections SET position_id=? WHERE position_id=?", (new, first["position_id"]))
    before = _count(rig.store, "ORDER_PRICE_OBSERVATION")
    _observe(rig, trade, **kwargs)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == before + 1, changed
    last = _rows(rig.store, "ORDER_PRICE_OBSERVATION")[-1]
    assert first[changed] != last[changed], changed


@pytest.mark.parametrize("kwargs", [{"error_code": 202}, {"reason": "synthetic broker reason"}])
def test_B27_repeated_price_error_or_reason_is_always_written(observation_rig, kwargs):
    rig = observation_rig()
    before = _count(rig.store, "ORDER_PRICE_OBSERVATION")
    for _ in range(7):
        _observe(rig, **kwargs)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == before + 7


def test_B27_price_audit_insert_failure_does_not_publish_dedup_cache(observation_rig):
    rig = observation_rig()
    rig.store.conn.execute("CREATE TEMP TRIGGER b27_fail BEFORE INSERT ON audit "
        "WHEN NEW.kind='ORDER_PRICE_OBSERVATION' BEGIN SELECT RAISE(ABORT,'synthetic insert failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        _observe(rig)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == 0
    rig.store.conn.execute("DROP TRIGGER b27_fail")
    _observe(rig)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == 1
    _observe(rig)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == 1


def test_B27_protection_tx_rollback_never_publishes_uncommitted_state(observation_rig):
    rig = observation_rig()
    pid = rig._b27_pids[0]
    before = dict(rig.store.protection(pid))
    count = _count(rig.store, "PROTECT_STATE")
    rig.store.conn.execute("CREATE TEMP TRIGGER b27_fail BEFORE INSERT ON audit "
        "WHEN NEW.kind='PROTECT_STATE' BEGIN SELECT RAISE(ABORT,'synthetic tx failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        rig.store.update_protection(rig.clock.now(), pid, state="UNKNOWN")
    assert dict(rig.store.protection(pid)) == before
    rig.store.conn.execute("DROP TRIGGER b27_fail")
    rig.store.update_protection(rig.clock.now(), pid, state="UNKNOWN")
    assert _count(rig.store, "PROTECT_STATE") == count + 1
    rig.store.update_protection(rig.clock.now(), pid, state="UNKNOWN")
    assert _count(rig.store, "PROTECT_STATE") == count + 1


def test_B27_actual_socket_generation_flushes_old_counts_and_reobserves(observation_rig):
    rig = observation_rig()
    _observe(rig)
    for _ in range(4):
        rig.advance(seconds=1); _observe(rig)
    before = _count(rig.store, "ORDER_PRICE_OBSERVATION")
    old_generation = rig._b27_native.query_generation
    rig._b27_native._reset_channels()
    assert rig._b27_native.query_generation == old_generation + 1
    rig.advance(seconds=1); _observe(rig)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == before + 1
    items = [item for item in _summary_items(rig.store)
             if item["kind"] == "ORDER_PRICE_OBSERVATION"]
    assert sum(item["count"] for item in items) == 4
    assert min(item["first_at"] for item in items) == (START + timedelta(seconds=1)).isoformat()
    assert max(item["last_at"] for item in items) == (START + timedelta(seconds=4)).isoformat()


def test_B27_restart_first_observation_is_not_suppressed_by_durable_history(
        observation_rig):
    rig = observation_rig(memory=False)
    _observe(rig); _observe(rig)
    before = _count(rig.store, "ORDER_PRICE_OBSERVATION")
    old = rig.engine
    path, levels = rig.store.path, rig.store._levels_fn
    rig.store.close()
    rig.store = Store(path, levels)
    rig.store.report_clock = rig.clock
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=old.report_dir)
    _observe(rig)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == before + 1


def test_B27_price_observation_outer_transaction_rollback_preserves_first_sighting(observation_rig):
    rig = observation_rig()
    with pytest.raises(RuntimeError, match="synthetic outer rollback"):
        with rig.store.tx():
            _observe(rig)
            raise RuntimeError("synthetic outer rollback")
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == 0
    _observe(rig)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == 1
    _observe(rig)
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == 1


def test_B27_actual_fresh_protection_progress_refreshes_row_without_state_audit(observation_rig):
    rig = observation_rig()
    pid = rig._b27_pids[0]
    before, writes = _count(rig.store, "PROTECT_STATE"), _writes(rig)
    economic = _snapshot(rig.store)
    rig.advance(seconds=15)
    rig.engine._protection_progress_one(rig.clock.now(), rig.store.protection(pid), rig.store.position(pid))
    rig.engine._drain(rig.clock.now())
    _safety(rig)
    proof = rig.engine._protection_notice_evidence.get(pid)
    assert proof and proof["full_exec"], "test must reach the actual complete three-source proof"
    assert rig.store.protection(pid)["confirmed_at"] == rig.clock.now().isoformat()
    assert _count(rig.store, "PROTECT_STATE") == before
    assert _writes(rig) == writes and _snapshot(rig.store) == economic


def _tp_observation(rig):
    pid = rig._b27_pids[0]
    row = rig.store.protection(pid)
    # A synthetic established TP workflow: S=2, T=1, P=3. This fixture-only
    # decrease is safe at both boundaries and grants no new engine authority.
    _safety(rig)
    request = replace(rig.broker.orders[row["intent_id"]].req, quantity=2)
    rig.broker.modify_protection(request)
    rig.store.update_protection(rig.clock.now(), pid, requested_qty=2,
        confirmed_remaining=2, pending_max_remaining=2)
    rig.broker.events.clear()
    req = OrderRequest("v6l-tp-b27-diagnostics", "AAPL", Side.SELL, 1, 345.0, Purpose.TP1, pid)
    rig.store.create_intent(rig.clock.now(), req, atr=None, count_entry_day=None)
    ack = rig.broker.submit_limit(req)
    rig.store.update_intent(rig.clock.now(), req.intent_id, status="SENT",
        broker_order_id=ack.broker_order_id, perm_id=ack.perm_id)
    rig.broker.events.clear()
    identity = rig.broker._open_order_snapshot(req.intent_id, rig.broker.orders[req.intent_id])
    _safety(rig)
    return PriceObservationEvent(identity, "openOrders", None, "0.0")


@pytest.mark.parametrize("field", ["sent_price", "working_price"])
def test_B27_tp_limit_payload_fields_are_written_before_any_price_hold(observation_rig, field):
    rig = observation_rig()
    event = _tp_observation(rig)
    rig.engine._handle_event(rig.clock.now(), event)
    before = _count(rig.store, "ORDER_PRICE_OBSERVATION")
    initial = _rows(rig.store, "ORDER_PRICE_OBSERVATION")[-1]
    changed = replace(event.identity, limit_price=345.01)
    if field == "sent_price":
        rig.store.conn.execute("UPDATE intents SET limit_price=? WHERE intent_id=?",
                               (345.01, changed.intent_id))
    rig.engine._handle_event(rig.clock.now(), replace(event, identity=changed))
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == before + 1
    assert _rows(rig.store, "ORDER_PRICE_OBSERVATION")[-1][field] != initial[field]
    _safety(rig)


def test_B27_duplicate_tp_observations_still_execute_following_evidence_path(observation_rig):
    rig = observation_rig()
    event = _tp_observation(rig)
    before, writes = _count(rig.store, "TP_EVIDENCE"), _writes(rig)
    for _ in range(7):
        rig.engine._handle_event(rig.clock.now(), event)
        _safety(rig)
    assert _count(rig.store, "TP_EVIDENCE") == before + 7, "dedup must not return before TP evidence"
    assert _count(rig.store, "ORDER_PRICE_OBSERVATION") == 1
    assert _writes(rig) == writes

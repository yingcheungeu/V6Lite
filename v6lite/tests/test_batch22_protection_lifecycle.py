"""Batch 22 independent STP lifecycle tests: TEMP/FakeBroker, no operator state.

B20-compatible imports ensure baseline failures are behavioural, not ImportError.
"""
from __future__ import annotations
import json
import socket
from dataclasses import replace
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from types import SimpleNamespace
import pytest
from v6lite.alerts import Alerter, MemoryChannel
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine, StartupError
from v6lite.models import IntentStatus, OrderRequest, ProtectionRequest, Purpose, Side
from v6lite.report import daily_report
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et
TERMINAL = {"Cancelled", "ApiCancelled", "Inactive", "Filled"}

@pytest.fixture(autouse=True)
def _no_broker_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Batch22 must never open a network connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)

def _rig(tmp_path, **kwargs):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True), **kwargs)
    rig.market_calls = []
    def forbidden(*args, **kwargs):
        rig.market_calls.append((args, kwargs))
        raise AssertionError("MKT forbidden: only PROTECT may be STP")
    rig.broker.submit_market = forbidden
    return rig

def _writes(rig):
    return tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                         rig.broker.modified_protections, rig.broker.cancels))

def _safety(rig, *, legacy_refs=()):
    """Gross authority, retaining old maximum while a decrease is pending."""
    truth = {p.symbol: int(p.quantity) for p in rig.broker.positions_fresh()}
    totals = {}
    for ref, order in rig.broker.orders.items():
        req = order.req
        if isinstance(req, ProtectionRequest):
            if ref not in legacy_refs:
                assert getattr(req, "order_type", None) == "STP"
                assert req.limit_price is None, "STP must not carry a fabricated limit"
            sell = True
        else:
            assert isinstance(req, OrderRequest)
            assert getattr(req, "order_type", "LMT") == "LMT"
            assert req.limit_price > 0
            sell = req.side is Side.SELL
        if not sell or order.status in TERMINAL:
            continue
        possible = max(0, order.remaining)
        pending = rig.broker.protection_pending_modifications.get(ref)
        if pending is not None:
            possible = max(possible, pending.quantity - order.filled)
            assert pending.limit_price is None
            assert getattr(pending, "order_type", None) == "STP"
        totals[req.symbol] = totals.get(req.symbol, 0) + possible
    for symbol, possible in totals.items():
        assert possible <= truth.get(symbol, 0), (symbol, "S+T+X", possible, "P", truth.get(symbol, 0))
    for order in rig.broker.open_orders():
        if order.intent_id not in legacy_refs:
            intent = rig.store.intent(order.intent_id)
            protect = intent is not None and intent["purpose"] == Purpose.PROTECT
            assert order.order_type == ("STP" if protect else "LMT")
            if protect:
                assert order.limit_price is None
                assert order.tif == "GTC" and order.outside_rth is False
    assert rig.market_calls == []

def _settle(rig):
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()

def _open(rig):
    entry = rig.open_aapl()
    [pos] = rig.store.open_positions()
    row = rig.store.protection(pos["position_id"])
    assert row is not None and row["state"] == "WORKING"
    _safety(rig)
    return entry, pos, dict(row)

def _first_fill(rig, quantity=1):
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    [entry] = [r for r in rig.broker.sent if r.purpose == Purpose.ENTRY]
    _safety(rig)
    fill = rig.broker.fill(entry.intent_id, qty=quantity, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    _safety(rig)
    _settle(rig)
    [pos] = rig.store.open_positions()
    return entry, pos, dict(rig.store.protection(pos["position_id"]))

def _snapshot(rig, ref):
    return next(o for o in rig.broker.open_orders() if o.intent_id == ref)

def _price(rig, price, *, seconds=31):
    rig.advance(seconds=seconds)
    rig.px("AAPL", price)
    rig.tick()
    _safety(rig)

def _tp_orders(rig):
    return [r for r in rig.broker.sent if r.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}]

def _restart(rig, tmp_path):
    path, levels = rig.store.path, rig.store._levels_fn
    rig.store.close()
    rig.store = Store(path, levels)
    rig.channel = MemoryChannel()
    rig.alerter = Alerter([rig.channel], synchronous=True)
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / "restarted-reports")
    rig.engine.startup()

def _pages(rig, level):
    return [(title, body) for title, body in rig.channel.messages if level in title and "止损" in title]

def _cross(rig, row, price=None):
    rig.px(row["symbol"], row["trigger_price"] - .01 if price is None else price)
    rig.engine._monitor_protection_marks(rig.clock.now())
    _safety(rig)

def test_B22_first_partial_fill_submits_STP_in_urgent_callback_with_no_limit(tmp_path):
    rig = _rig(tmp_path)
    entry, pos, row = _first_fill(rig)
    [request] = rig.broker.sent_protections
    order = _snapshot(rig, request.intent_id)
    expected = Decimal(str(pos["stop"])).quantize(Decimal(".01"), rounding=ROUND_FLOOR)
    assert request.quantity == pos["qty_open"] == 1
    assert Decimal(str(request.trigger_price)) == expected
    assert order.order_type == "STP" and order.limit_price is None
    assert order.aux_price == request.trigger_price == row["trigger_price"]
    assert dict(rig.store.protection(pos["position_id"])).get("order_type") == "STP"
    assert rig.store.intent(request.intent_id)["limit_price"] is None
    assert (order.account, order.client_id, order.con_id, order.broker_order_id, order.perm_id) == (
        row["account"], row["client_id"], row["con_id"], row["broker_order_id"], row["perm_id"])
    assert entry.purpose == Purpose.ENTRY and entry.limit_price > 0

def test_B22_partial_entry_growth_updates_same_STP_quantity_and_aux_price(tmp_path):
    rig = _rig(tmp_path)
    entry, pos, before = _first_fill(rig)
    rig.broker.fill(entry.intent_id, qty=1, price=114.0)
    _safety(rig)
    _settle(rig)
    _safety(rig)
    after = dict(rig.store.protection(pos["position_id"]))
    assert len(rig.broker.sent_protections) == 1
    keys = ("intent_id", "broker_order_id", "perm_id")
    assert tuple(after[k] for k in keys) == tuple(before[k] for k in keys)
    assert rig.broker.orders[after["intent_id"]].remaining == 2
    assert rig.broker.modified_protections[-1].trigger_price == after["trigger_price"]
    assert _snapshot(rig, after["intent_id"]).limit_price is None

def test_B22_native_STP_without_limit_is_complete_identity_and_price_proof(tmp_path):
    rig = _rig(tmp_path)
    _, _, row = _open(rig)
    order = _snapshot(rig, row["intent_id"])
    assert order.limit_price is None and rig.engine._protection_order_valid(row, order)
    assert not rig.engine._protection_order_valid(row, replace(order, aux_price=order.aux_price - .01))
    assert not rig.engine._protection_order_valid(row, replace(order, order_type="STP LMT"))
    # A finite STP broker limit echo is diagnostic and does not alter its trigger.
    assert rig.engine._protection_order_valid(row, replace(order, limit_price=1.0))

@pytest.mark.parametrize("mismatch", ["account", "client_id", "con_id", "broker_order_id", "perm_id",
                                       "intent_id", "aux_price", "tif", "outside_rth", "order_type"])
def test_B22_STP_native_identity_and_trigger_mismatch_stays_fail_closed(tmp_path, mismatch):
    rig = _rig(tmp_path)
    _, _, row = _open(rig)
    order = _snapshot(rig, row["intent_id"])
    values = {"account": "DU0000000", "client_id": rig.cfg.client_id + 1, "con_id": row["con_id"] + 1,
              "broker_order_id": row["broker_order_id"] + 1, "perm_id": row["perm_id"] + 1,
              "intent_id": "v6l-wrong-stp", "aux_price": row["trigger_price"] - .01,
              "tif": "DAY", "outside_rth": True, "order_type": "LMT"}
    assert not rig.engine._protection_order_valid(row, replace(order, **{mismatch: values[mismatch]}))
    _safety(rig)


def test_B22_TP_waits_for_STP_decrease_confirmation_then_preserves_gross_capacity(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    held = pos["qty_open"]
    rig.broker.protection_auto_status = False
    _price(rig, pos["tp1"] + 1)
    pending = rig.broker.modified_protections[-1]
    assert pending.quantity == held - max(1, held // 3)
    assert pending.trigger_price == row["trigger_price"] and pending.limit_price is None
    assert _tp_orders(rig) == [] and rig.broker.orders[row["intent_id"]].remaining == held
    rig.broker.confirm_protection_modify(row["intent_id"])
    _safety(rig)
    _settle(rig)
    _safety(rig)
    [tp] = _tp_orders(rig)
    assert tp.purpose == Purpose.TP1
    assert rig.broker.orders[row["intent_id"]].remaining + tp.quantity == held
    rig.broker.fill(tp.intent_id, qty=1)
    _safety(rig)
    _settle(rig)
    _safety(rig)
    assert rig.store.position(pos["position_id"])["qty_open"] == held - 1

def test_B22_TP_timeout_keeps_cancel_pending_reserve_then_restores_same_STP(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    _price(rig, pos["tp1"] + 1)
    [tp] = _tp_orders(rig)
    reduced = rig.broker.orders[row["intent_id"]].remaining
    rig.advance(seconds=10)
    rig.tick()
    _safety(rig)
    assert tp.intent_id in rig.broker.cancels
    assert rig.broker.orders[row["intent_id"]].remaining == reduced
    rig.broker.confirm_cancel(tp.intent_id)
    _safety(rig)
    _settle(rig)
    _safety(rig)
    assert rig.broker.orders[row["intent_id"]].remaining == pos["qty_open"]
    assert len(rig.broker.sent_protections) == 1
    assert rig.broker.modified_protections[-1].limit_price is None

def test_B22_TP1_fill_changes_aux_to_breakeven_without_changing_STP_identity(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    _price(rig, 120.0)
    [tp] = _tp_orders(rig)
    rig.broker.fill(tp.intent_id, price=120.0)
    _safety(rig)
    _settle(rig)
    _safety(rig)
    _price(rig, 118.0, seconds=1)
    after = dict(rig.store.protection(pos["position_id"]))
    assert after["trigger_price"] == pytest.approx(113.53)
    assert rig.store.position(pos["position_id"])["breakeven_stop"] == pytest.approx(113.53)
    keys = ("intent_id", "broker_order_id", "perm_id")
    assert tuple(after[k] for k in keys) == tuple(row[k] for k in keys)
    order = _snapshot(rig, row["intent_id"])
    assert order.aux_price == pytest.approx(113.53) and order.limit_price is None
    assert rig.engine._protection_order_valid(after, order)
    assert len(rig.broker.sent_protections) == 1

def test_B22_last_share_TP_waits_for_exact_STP_cancel_then_LMT_fill_closes(tmp_path):
    rig = _rig(tmp_path, max_order_notional_usd=130.0)
    _, pos, row = _open(rig)
    assert pos["qty_open"] == 1
    _price(rig, pos["tp1"] + 1)
    assert row["intent_id"] in rig.broker.cancels and _tp_orders(rig) == []
    rig.broker.confirm_cancel(row["intent_id"])
    _safety(rig)
    _settle(rig)
    _safety(rig)
    [tp] = _tp_orders(rig)
    assert tp.quantity == 1
    rig.broker.fill(tp.intent_id, price=120.0)
    _safety(rig)
    _settle(rig)
    _safety(rig)
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert not rig.broker.open_orders()

def test_B22_last_share_TP_cancel_retires_old_STP_and_restores_new_STP(tmp_path):
    rig = _rig(tmp_path, max_order_notional_usd=130.0)
    _, pos, row = _open(rig)
    _price(rig, pos["tp1"] + 1)
    rig.broker.confirm_cancel(row["intent_id"])
    _safety(rig)
    _settle(rig)
    _safety(rig)
    [tp] = _tp_orders(rig)
    rig.advance(seconds=10)
    rig.tick()
    _safety(rig)
    rig.broker.confirm_cancel(tp.intent_id)
    _safety(rig)
    _settle(rig)
    _safety(rig)
    after = dict(rig.store.protection(pos["position_id"]))
    assert after["intent_id"] != row["intent_id"] and after["state"] == "WORKING"
    assert _snapshot(rig, after["intent_id"]).remaining == 1
    retired = json.loads(rig.store.control("retired_protection:" + row["intent_id"]))
    assert retired["order_type"] == "STP" and retired["trigger_price"] == row["trigger_price"]
    assert retired["perm_id"] == row["perm_id"] and retired["quantity"] == 1

@pytest.mark.parametrize("phase", ["working", "decrease_pending", "TP_working", "TP_partial",
                                   "TP_cancel_pending", "protection_partial", "submit_pending"])
def test_B22_restart_recognizes_STP_in_each_lifecycle_without_duplicate_sell(tmp_path, phase):
    rig = _rig(tmp_path)
    if phase == "submit_pending":
        rig.broker.protection_auto_status = False
        _, pos, row = _first_fill(rig)
    else:
        _, pos, row = _open(rig)
    if phase in {"decrease_pending", "TP_working", "TP_partial", "TP_cancel_pending"}:
        if phase == "decrease_pending":
            rig.broker.protection_auto_status = False
        _price(rig, pos["tp1"] + 1)
        if phase in {"TP_partial", "TP_cancel_pending"}:
            [tp] = _tp_orders(rig)
            if phase == "TP_partial":
                rig.broker.fill(tp.intent_id, qty=1)
                _settle(rig)
            else:
                rig.advance(seconds=10)
                rig.tick()
            _safety(rig)
    elif phase == "protection_partial":
        rig.broker.fill(row["intent_id"], qty=1, price=row["trigger_price"] - .50)
        _settle(rig)
        _safety(rig)
    before = (len(rig.broker.sent_protections), len(_tp_orders(rig)))
    _restart(rig, tmp_path)
    _safety(rig)
    assert (len(rig.broker.sent_protections), len(_tp_orders(rig))) == before
    assert row["intent_id"] not in rig.broker.cancels
    assert rig.store.protection(pos["position_id"])["intent_id"] == row["intent_id"]

def test_B22_shutdown_preserves_strong_STP_GTC_and_restarts_without_rehang(tmp_path):
    rig = _rig(tmp_path)
    _, _, row = _open(rig)
    rig.engine.shutdown(grace_seconds=0)
    _safety(rig)
    assert row["intent_id"] not in rig.broker.cancels
    order = _snapshot(rig, row["intent_id"])
    assert order.order_type == "STP" and order.tif == "GTC"
    _restart(rig, tmp_path)
    _safety(rig)
    assert len(rig.broker.sent_protections) == 1

def _retired_pair(tmp_path):
    rig = _rig(tmp_path)
    _, pos, old = _open(rig)
    rig.engine._protection_cancel(rig.clock.now(), old, "synthetic verified lifecycle replacement")
    _safety(rig)
    rig.broker.confirm_cancel(old["intent_id"])
    _safety(rig)
    _settle(rig)
    _safety(rig)
    for _ in range(4):
        if rig.store.protection(pos["position_id"])["intent_id"] != old["intent_id"]:
            break
        rig.advance(seconds=1)
        rig.tick()
        _settle(rig)
        _safety(rig)
    new = dict(rig.store.protection(pos["position_id"]))
    assert new["intent_id"] != old["intent_id"] and new["state"] == "WORKING"
    return rig, pos, old, new

def test_B22_retired_STP_identity_trigger_and_type_survive_restart_and_late_fill(tmp_path):
    rig, pos, old, new = _retired_pair(tmp_path)
    meta = json.loads(rig.store.control("retired_protection:" + old["intent_id"]))
    assert meta["order_type"] == "STP" and meta["trigger_price"] == old["trigger_price"]
    _restart(rig, tmp_path)
    _safety(rig)
    assert json.loads(rig.store.control("retired_protection:" + old["intent_id"])) == meta
    before = _writes(rig)
    # Native late fill creates a genuine capacity race. Immediate Fake ACK
    # lets the compensating reduction settle before the gross-capacity check.
    fill = rig.broker.fill(old["intent_id"], qty=5, price=old["trigger_price"] - 1, emit=False)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    _settle(rig)
    if new["intent_id"] in rig.broker.cancels:
        rig.broker.confirm_cancel(new["intent_id"])
        _settle(rig)
    _safety(rig)
    assert rig.store.position(pos["position_id"])["qty_open"] == pos["qty_open"] - 5
    assert rig.store.intent_filled_qty(old["intent_id"]) == 5
    assert rig.store.intent_filled_qty(new["intent_id"]) == 0
    assert all(req.quantity <= pos["qty_open"] - 5 for req in rig.broker.modified_protections[before[2]:])
    writes, held = _writes(rig), rig.store.position(pos["position_id"])["qty_open"]
    rig.engine._ingest_fill(rig.clock.now(), fill)
    _settle(rig)
    _safety(rig)
    assert _writes(rig) == writes and rig.store.position(pos["position_id"])["qty_open"] == held
    events = [json.loads(r["payload"]) for r in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='PROTECT_RETIRED_FILL'")]
    [event] = [e for e in events if e["exec_id"] == fill.exec_id]
    assert event["stop_price"] == old["trigger_price"] and event["slippage_usd"] == pytest.approx(5.0)

@pytest.mark.parametrize("field", ["broker_order_id", "perm_id", "con_id"])
def test_B22_retired_STP_wrong_native_fill_identity_is_quarantined_without_new_writes(tmp_path, field):
    from v6lite.models import FillEvent
    rig, pos, old, new = _retired_pair(tmp_path)
    fill = FillEvent("0000fa4e.000bad22.01.01", old["intent_id"], "AAPL", Side.SELL, 1,
                     old["trigger_price"] - 1, rig.clock.now(), broker_order_id=old["broker_order_id"],
                     perm_id=old["perm_id"], con_id=old["con_id"], commission=0)
    fill = replace(fill, **{field: old[field] + 1})
    before = _writes(rig)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    _safety(rig)
    assert _writes(rig) == before
    assert rig.store.position(pos["position_id"])["qty_open"] == pos["qty_open"]
    assert rig.store.intent_filled_qty(old["intent_id"]) == rig.store.intent_filled_qty(new["intent_id"]) == 0
    assert rig.store.conn.execute("SELECT 1 FROM quarantined_fills WHERE exec_id=?", (fill.exec_id,)).fetchone()


def _manual_flat(rig, row):
    # Cancel exact STP before registering an additive manual SELL. Drain its
    # terminal callback after the external fill, preserving flat evidence.
    rig.broker.status(row["intent_id"], "Cancelled")
    _safety(rig)
    held = rig.store.position(row["position_id"])["qty_open"]
    req = OrderRequest("external-flat-b22", row["symbol"], Side.SELL, held, 100.0, Purpose.TP1)
    rig.broker._register(req)
    _safety(rig)
    rig.broker.fill(req.intent_id, price=100.0, emit=False)
    _safety(rig)
    rig.engine._drain(rig.clock.now())
    rig.store.set_control(rig.clock.now(), "frozen:AAPL", "synthetic verified manual flatten")
    rig.advance(seconds=61)
    assert rig.broker.positions_fresh() == []

def _release(rig):
    rig.store.set_control(rig.clock.now(), "release:AAPL", json.dumps(
        {"operator": "synthetic-operator", "reason": "verified exact STP cancellation and manual flat"}))
    rig.engine._apply_releases(rig.clock.now())

def test_B22_release_accepts_STP_only_after_exact_cancel_and_two_stable_flat_snapshots(tmp_path, monkeypatch):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    _manual_flat(rig, row)
    counts = {"open_orders": 0, "positions_fresh": 0, "account_executions_today": 0}
    for method in counts:
        original = getattr(rig.broker, method)
        def counted(*args, _original=original, _method=method, **kwargs):
            counts[_method] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(rig.broker, method, counted)
    before = _writes(rig)
    _release(rig)
    _safety(rig)
    assert rig.store.position(pos["position_id"])["status"] == "RELEASED"
    assert rig.store.intent(row["intent_id"])["status"] == IntentStatus.CANCELLED
    assert rig.store.control("frozen:AAPL") is None
    assert all(n >= 2 for n in counts.values()), counts
    assert _writes(rig) == before
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='RELEASE_GTC_VERIFIED'").fetchone()

@pytest.mark.parametrize("field", ["perm_id", "account", "client_id", "con_id"])
def test_B22_STP_release_does_not_weaken_missing_or_wrong_identity_gate(tmp_path, field):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    _manual_flat(rig, row)
    values = {"perm_id": None, "account": "DU0000000", "client_id": rig.cfg.client_id + 1, "con_id": None}
    rig.store.conn.execute(f"UPDATE protections SET {field}=? WHERE position_id=?", (values[field], pos["position_id"]))
    before = _writes(rig)
    _release(rig)
    _safety(rig)
    assert rig.store.position(pos["position_id"])["status"] == "OPEN"
    assert rig.store.control("frozen:AAPL") is not None
    assert rig.store.flat_point("AAPL") is None and _writes(rig) == before

def _legacy_contract(rig, pos, row):
    columns = {r["name"] for r in rig.store.conn.execute("PRAGMA table_info(protections)")}
    if "order_type" not in columns:
        rig.store.conn.execute("ALTER TABLE protections ADD COLUMN order_type TEXT")
    limit = float((Decimal(str(row["trigger_price"])) * Decimal(".97")).quantize(Decimal(".01"), rounding=ROUND_FLOOR))
    rig.store.conn.execute("UPDATE protections SET order_type='STP LMT',limit_price=? WHERE position_id=?", (limit, pos["position_id"]))
    rig.store.conn.execute("UPDATE intents SET limit_price=? WHERE intent_id=?", (limit, row["intent_id"]))
    request = rig.broker.orders[row["intent_id"]].req
    kwargs = {"limit_price": limit}
    if hasattr(request, "order_type"):
        kwargs["order_type"] = "STP LMT"
    rig.broker.orders[row["intent_id"]].req = replace(request, **kwargs)
    return dict(rig.store.protection(pos["position_id"]))

def _assert_legacy_rejected(rig, pos, legacy, before):
    try:
        rig.engine.startup()
    except StartupError:
        pass  # Explicit refusal still must produce the mandated P0.
    _safety(rig, legacy_refs=(legacy["intent_id"],))
    after = dict(rig.store.protection(pos["position_id"]))
    fields = ("intent_id", "account", "client_id", "con_id", "broker_order_id", "perm_id",
              "trigger_price", "limit_price", "requested_qty", "order_type")
    assert {k: after.get(k) for k in fields} == {k: legacy.get(k) for k in fields}
    assert _writes(rig) == before, "legacy contract must never be changed/replaced/cancelled automatically"
    assert any("P0" in title and "旧类型保护单" in title and "人工" in body for title, body in rig.channel.messages)
    assert rig.store.control("frozen:AAPL") is not None

def test_B22_existing_STP_LMT_record_is_P0_and_original_contract_is_never_migrated(tmp_path):
    rig = _rig(tmp_path)
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    row = dict(rig.store.protection(pos["position_id"]))
    legacy = _legacy_contract(rig, pos, row)
    before = _writes(rig)
    rig.channel.messages.clear()
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock, report_dir=tmp_path / "legacy-reports")
    _assert_legacy_rejected(rig, pos, legacy, before)

def test_B22_schema4_old_row_gets_STP_LMT_annotation_only_then_P0(tmp_path):
    rig = _rig(tmp_path)
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    row = dict(rig.store.protection(pos["position_id"]))
    legacy = _legacy_contract(rig, pos, row)
    info = [dict(c) for c in rig.store.conn.execute("PRAGMA table_info(protections)") if c["name"] != "order_type"]
    defs = []
    for c in info:
        text = '"' + c["name"] + '" ' + c["type"]
        if c["pk"]:
            text += " PRIMARY KEY"
        elif c["notnull"]:
            text += " NOT NULL"
        if c["dflt_value"] is not None:
            text += " DEFAULT " + c["dflt_value"]
        defs.append(text)
    fields = ",".join('"' + c["name"] + '"' for c in info)
    # TEMP schema-4 table with the annotation absent; every old value retained.
    with rig.store.tx():
        rig.store.conn.execute("ALTER TABLE protections RENAME TO fixture_protections_current")
        rig.store.conn.execute("CREATE TABLE protections (" + ",".join(defs) + ")")
        rig.store.conn.execute("INSERT INTO protections(" + fields + ") SELECT " + fields + " FROM fixture_protections_current")
        rig.store.conn.execute("DROP TABLE fixture_protections_current")
    assert "order_type" not in {r["name"] for r in rig.store.conn.execute("PRAGMA table_info(protections)")}
    path, levels = rig.store.path, rig.store._levels_fn
    rig.store.close()
    rig.store = Store(path, levels)
    after = dict(rig.store.protection(pos["position_id"]))
    assert after.get("order_type") == "STP LMT", "old rows need honest legacy annotation"
    assert all(after[k] == legacy[k] for k in legacy if k != "order_type")
    before = _writes(rig)
    rig.channel.messages.clear()
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock, report_dir=tmp_path / "old-schema-reports")
    _assert_legacy_rejected(rig, pos, legacy, before)

def test_B22_RTH_stop_crossing_is_P1_then_remaining_timeout_P0_without_old_limit_shortcut(tmp_path):
    rig = _rig(tmp_path)
    _, _, row = _open(rig)
    rig.channel.messages.clear()
    before = _writes(rig)
    _cross(rig, row, row["trigger_price"] - 10)
    assert any("已到止损价" in title and "止损市价单" in title for title, _ in _pages(rig, "P1"))
    assert _pages(rig, "P0") == [], "below the removed limit is not an STP timeout"
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds - 1)
    rig.engine._monitor_protection_marks(rig.clock.now())
    _safety(rig)
    assert _pages(rig, "P0") == []
    rig.advance(seconds=1)
    rig.engine._monitor_protection_marks(rig.clock.now())
    _safety(rig)
    [page] = _pages(rig, "P0")
    assert "止损已触发但未成交" in page[0]
    assert any(cause in page[1] for cause in ("停牌", "交易暂停"))
    assert all(step in page[1] for step in ("TWS", "1）", "2）", "3）"))
    assert "限价=" not in page[1] and "低于保护限价" not in page[1]
    assert _writes(rig) == before

def test_B22_partial_stop_fill_does_not_restart_continuing_RTH_remaining_timeout(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    rig.channel.messages.clear()
    _cross(rig, row)
    key = "protect_trigger:" + pos["position_id"]
    first = json.loads(rig.store.control(key))
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds - 1)
    rig.broker.fill(row["intent_id"], qty=1, price=row["trigger_price"] - .50)
    _settle(rig)
    _safety(rig)
    rig.engine._monitor_protection_marks(rig.clock.now())
    after = json.loads(rig.store.control(key))
    assert after["started_at"] == first["started_at"] and after["remaining"] == pos["qty_open"] - 1
    assert _pages(rig, "P0") == []
    rig.advance(seconds=1)
    rig.engine._monitor_protection_marks(rig.clock.now())
    _safety(rig)
    assert len(_pages(rig, "P0")) == 1

def test_B22_stop_timer_ignores_off_RTH_and_new_session_does_not_inherit_overnight_seconds(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    rig.channel.messages.clear()
    rig.at(et(15, 59, 40))
    _cross(rig, row)
    key = "protect_trigger:" + pos["position_id"]
    old = json.loads(rig.store.control(key))
    rig.at(et(16, 1))
    rig.engine._monitor_protection_marks(rig.clock.now())
    rig.engine._protection_on_mark(rig.clock.now(), rig.store.protection(pos["position_id"]), row["trigger_price"] - 1)
    _safety(rig)
    assert _pages(rig, "P0") == [] and json.loads(rig.store.control(key))["started_at"] == old["started_at"]
    rig.at(et(9, 30, day=(2026, 10, 7)))
    rig.engine._monitor_protection_marks(rig.clock.now())
    _safety(rig)
    fresh = json.loads(rig.store.control(key))
    assert fresh["started_at"] == rig.clock.now().isoformat() and _pages(rig, "P0") == []
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds)
    rig.engine._monitor_protection_marks(rig.clock.now())
    _safety(rig)
    assert len(_pages(rig, "P0")) == 1

def test_B22_RTH_trigger_timer_survives_restart_without_duplicate_P1_or_reset(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    _cross(rig, row)
    key = "protect_trigger:" + pos["position_id"]
    started = json.loads(rig.store.control(key))["started_at"]
    rig.advance(seconds=30)
    _restart(rig, tmp_path)
    _safety(rig)
    rig.engine._monitor_protection_marks(rig.clock.now())
    assert json.loads(rig.store.control(key))["started_at"] == started and _pages(rig, "P1") == []
    rig.advance(seconds=30)
    rig.engine._monitor_protection_marks(rig.clock.now())
    _safety(rig)
    assert len(_pages(rig, "P0")) == 1


@pytest.mark.parametrize("kind", ["gap A warning", "gap A overdue", "unknown", "shutdown incomplete",
                                  "trigger crossing", "trigger unfilled", "rejected"])
def test_B22_cards_display_market_stop_price_with_no_protection_limit_field(tmp_path, monkeypatch, kind):
    rig = _rig(tmp_path)
    _, _, row = _open(rig)
    cards = []
    # Renderer-only capture: incident cadence is tested separately in B20.
    monkeypatch.setattr(rig.engine, "_deliver_protection_notice",
                        lambda now, level, event_kind, event_row, body, card: cards.append(card))
    rig.engine._protection_page(rig.clock.now(), "P1" if kind == "trigger crossing" else "P0",
                                kind, row, "synthetic lifecycle evidence")
    [card] = cards
    text = "\n".join(value for _, value in card.fields)
    assert "止损价" in text and "触发后市价成交" in text and f"{row['trigger_price']:.2f}" in text
    assert "限价=" not in text and "低于限价" not in text
    assert all(step in text for step in ("1）", "2）", "3）"))
    if kind == "trigger unfilled":
        assert "止损已触发但未成交" in card.title
        assert "限价未成交" not in text + card.title
        assert any(cause in text for cause in ("停牌", "交易暂停"))
    if kind == "rejected":
        assert "TWS" in text and "限价单平仓" in text

def test_B22_PROTECT_FILL_audits_each_execution_signed_slippage_and_report_exactly_once(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    stop = row["trigger_price"]
    executions = []
    for qty, price in ((1, stop - 1.25), (2, stop + .50), (3, stop - .25)):
        fill = rig.broker.fill(row["intent_id"], qty=qty, price=price, emit=False)
        _safety(rig)
        rig.engine._on_urgent_broker_event(fill)
        _settle(rig)
        _safety(rig)
        executions.append(fill)
    before, held = _writes(rig), rig.store.position(pos["position_id"])["qty_open"]
    for fill in executions:
        rig.engine._on_urgent_broker_event(fill)
    _settle(rig)
    _safety(rig)
    assert _writes(rig) == before and rig.store.position(pos["position_id"])["qty_open"] == held
    audit = [json.loads(r["payload"]) for r in rig.store.conn.execute("SELECT payload FROM audit WHERE kind='PROTECT_FILL'")]
    assert len(audit) == len(executions)
    for fill in executions:
        [event] = [e for e in audit if e["exec_id"] == fill.exec_id]
        assert event["stop_price"] == stop and event["fill_price"] == fill.price
        assert event["quantity"] == event["qty"] == fill.quantity
        assert event["slippage_usd"] == pytest.approx(fill.quantity * (stop - fill.price))
        assert event["slippage_bps"] == pytest.approx((stop - fill.price) / stop * 10000)
    assert next(e for e in audit if e["exec_id"] == executions[1].exec_id)["slippage_usd"] < 0
    text = daily_report(rig.store, rig.cfg, date(2026, 10, 6))
    health = text.partition("- 券商端保护:")[2].partition("- kill switch:")[0]
    assert "滑点" in health and "止损" in health and "限价=" not in health
    for fill in executions:
        assert health.count(fill.exec_id) == 1
        assert f"{fill.price:.2f}" in health and f"{stop:.2f}" in health
        assert f"{fill.quantity * (stop - fill.price):.2f}" in health
    assert "bp" in health.lower() or "基点" in health

def test_B22_protection_final_fill_keeps_audit_stop_and_actual_price_after_close(tmp_path):
    rig = _rig(tmp_path)
    _, pos, row = _open(rig)
    fill = rig.broker.fill(row["intent_id"], price=row["trigger_price"] - 2, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    _settle(rig)
    _safety(rig)
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.broker.positions_fresh() == [] and rig.broker.open_orders() == []
    events = [json.loads(r["payload"]) for r in rig.store.conn.execute("SELECT payload FROM audit WHERE kind='PROTECT_FILL'")]
    [event] = events
    assert event["stop_price"] == row["trigger_price"] and event["fill_price"] == fill.price
    assert event["slippage_usd"] == pytest.approx(fill.quantity * 2)
    text = daily_report(rig.store, rig.cfg, date(2026, 10, 6))
    health = text.partition("- 券商端保护:")[2].partition("- kill switch:")[0]
    assert fill.exec_id in health and "滑点" in health

def test_B22_explicit_STP_rejection_pages_freezes_and_never_rehangs(tmp_path):
    rig = _rig(tmp_path)
    rig.broker.protection_auto_status = False
    _, pos, row = _first_fill(rig)
    request = rig.broker.orders[row["intent_id"]].req
    oid, perm = row["broker_order_id"], row["perm_id"]
    contract = SimpleNamespace(symbol="AAPL", conId=row["con_id"], secType="STK", currency="USD", exchange="SMART")
    order = SimpleNamespace(orderRef=row["intent_id"], orderId=oid, permId=perm, account=rig.cfg.account,
                            clientId=rig.cfg.client_id, action="SELL", orderType="STP", tif="GTC",
                            outsideRth=False, totalQuantity=request.quantity, auxPrice=request.trigger_price)
    status = SimpleNamespace(status="Cancelled", filled=0, remaining=request.quantity,
                             orderId=oid, permId=perm, clientId=rig.cfg.client_id)
    reason = "Order rejected - reason: We cannot accept your stop order."
    trade = SimpleNamespace(order=order, contract=contract, orderStatus=status,
                            log=[SimpleNamespace(time=rig.clock.now(), status="Cancelled", message=reason, errorCode=201)],
                            fills=[], advancedError="")
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = SimpleNamespace(trades=lambda: [trade], openTrades=lambda: [trade], isConnected=lambda: True)
    adapter._trades[row["intent_id"]] = trade
    rig.broker.orders[row["intent_id"]].status = "Cancelled"
    adapter._on_status(trade)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    _safety(rig)
    after = rig.store.protection(pos["position_id"])
    assert after["state"] == "REJECTED" and rig.store.control("frozen:AAPL") is not None
    assert any("P0" in title and "券商已拒绝保护单" in title for title, _ in rig.channel.messages)
    assert any(reason in body and "券商已明确拒绝 STP 请求" in body for _, body in rig.channel.messages)
    before = _writes(rig)
    for seconds in (1, 30, 61, 1801):
        rig.advance(seconds=seconds)
        rig.engine._protection_progress(rig.clock.now())
        rig.engine._hard_protection_deadlines(rig.clock.now())
        _safety(rig)
    assert _writes(rig) == before and rig.store.position(pos["position_id"])["qty_open"] == 1

def test_B22_dry_run_all_protection_lifecycle_entry_points_have_zero_broker_writes(tmp_path, monkeypatch):
    rig = _rig(tmp_path, dry_run=True)
    calls = []
    for method in ("submit_limit", "submit_stop", "submit_stop_limit", "modify_protection", "cancel", "submit_market"):
        if hasattr(rig.broker, method):
            monkeypatch.setattr(rig.broker, method, lambda *args, _name=method, **kwargs: calls.append(_name))
    rig.start()
    rig.broker.external_positions["AAPL"] = (3, 100.0)
    rig.at(et(10, 0, 30))
    rig.tick()
    rig.advance(seconds=61)
    rig.tick()
    rig.engine._protection_progress(rig.clock.now())
    rig.engine._protection_timers(rig.clock.now())
    rig.engine.shutdown(grace_seconds=0)
    assert calls == []
    assert rig.broker.sent == rig.broker.sent_protections == rig.broker.modified_protections == rig.broker.cancels == []
    assert rig.store.protections() == []

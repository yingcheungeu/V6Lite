"""F6: a verified manual flatten must have a safe release path under L7."""

from __future__ import annotations

import json
from datetime import date

import pytest

from v6lite.config import ProtectionConfig
from v6lite.models import FillEvent, IntentStatus, OrderRequest, Purpose, Side, StatusEvent
from v6lite.report import daily_report
from v6lite.tests.helpers import Rig


def _protected_position_flattened_in_tws(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    protection = rig.store.protection(position["position_id"])
    ref = protection["intent_id"]
    assert protection["state"] == "WORKING"
    assert rig.broker.orders[ref].remaining == position["qty_open"]

    # A distinct TWS user sells the actual shares; the V6-Lite ledger remains
    # open until the operator asks for release. This filled outside order is
    # visible in reqExecutions but has no working remainder.
    manual = OrderRequest("external-manual-flat", "AAPL", Side.SELL,
                          position["qty_open"], 100.0, Purpose.TP1)
    rig.broker._register(manual)
    fill = rig.broker.fill(manual.intent_id)
    rig.broker.drain_events()  # release's fresh execution query owns the evidence
    rig.store.set_control(rig.clock.now(), "frozen:AAPL", "manual_flattened_in_TWS")
    rig.advance(seconds=61)  # RELEASE_QUIET after the last real execution
    assert rig.broker.positions_fresh() == []
    assert any(e.exec_id == fill.exec_id for e in rig.broker.executions_today())
    return rig, position["position_id"], ref, fill.exec_id


def _request_release(rig):
    rig.store.set_control(rig.clock.now(), "release:AAPL",
                          json.dumps({"operator": "John", "reason": "flat and no GTC in TWS"}))
    rig.engine._apply_releases(rig.clock.now())


def _release_refusals(rig):
    return rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='RELEASE_REFUSED' ORDER BY ts"
    ).fetchall()


def _two_fresh_flat_snapshots(rig, expected_exec_id):
    for _ in range(2):
        assert not [o for o in rig.broker.open_orders() if o.symbol == "AAPL"]
        assert rig.broker.positions_fresh() == []
        assert any(e.exec_id == expected_exec_id for e in rig.broker.executions_today())
        rig.advance(seconds=1)


def test_B16_F6_release_refuses_working_GTC_then_accepts_verified_cancelled_flat(
        tmp_path, monkeypatch):
    rig, position_id, ref, exec_id = _protected_position_flattened_in_tws(tmp_path)
    _request_release(rig)
    assert rig.store.position(position_id)["status"] == "OPEN"
    assert rig.store.control("frozen:AAPL") is not None
    assert _release_refusals(rig)
    assert ref not in rig.broker.cancels  # release never cancels a protected GTC

    rig.broker.confirm_cancel(ref)  # operator cancels exact GTC in TWS
    rig.engine._drain(rig.clock.now())
    assert rig.store.intent(ref)["status"] == IntentStatus.CANCEL_SEEN
    # Count evidence consumed INSIDE release, not pre-read by the test. Both
    # complete rounds must agree on broker order, position and execution truth.
    seen = {"orders": [], "positions": [], "executions": []}
    originals = {key: getattr(rig.broker, method) for key, method in (
        ("orders", "open_orders"), ("positions", "positions_fresh"),
        ("executions", "executions_today"))}

    def record(key):
        def wrapped():
            rows = originals[key]()
            if key == "orders":
                signature = tuple(sorted((o.intent_id, o.broker_order_id, o.remaining)
                                         for o in rows if o.symbol == "AAPL"))
            elif key == "positions":
                signature = tuple(sorted((p.symbol, p.quantity) for p in rows if p.symbol == "AAPL"))
            else:
                signature = tuple(sorted((e.exec_id, e.side, e.quantity)
                                         for e in rows if e.symbol == "AAPL"))
            seen[key].append(signature)
            return rows
        return wrapped

    for key, method in (("orders", "open_orders"), ("positions", "positions_fresh"),
                        ("executions", "executions_today")):
        monkeypatch.setattr(rig.broker, method, record(key))
    sells_before = tuple(req.intent_id for req in rig.broker.sent if req.side is Side.SELL)
    _request_release(rig)
    for key in seen:
        assert len(seen[key]) >= 2, (key, seen)
        assert seen[key][0] == seen[key][-1], (key, seen)
    assert any(item[0] == exec_id for item in seen["executions"][-1])
    assert rig.store.position(position_id)["status"] == "RELEASED", [r["payload"] for r in _release_refusals(rig)]
    assert rig.store.control("frozen:AAPL") is None
    assert rig.store.release_times()
    assert rig.store.intent(ref)["status"] == IntentStatus.CANCELLED
    assert rig.store.flat_point("AAPL") is not None
    assert "POSITION_RELEASED" in daily_report(rig.store, rig.cfg, date(2026, 10, 6))
    assert ref not in rig.broker.cancels
    assert tuple(req.intent_id for req in rig.broker.sent if req.side is Side.SELL) == sells_before

    # Delayed reports for orders created before verified flat are evidence only.
    # They must not reopen inventory, change P&L, or submit another SELL.
    entry_ref = next(req.intent_id for req in rig.broker.sent if req.side is Side.BUY)
    pnl_before = rig.store.realized_for_day("2026-10-06")
    fills_before = rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    for suffix, intent_id, side in (("buy", entry_ref, Side.BUY),
                                    ("sell", ref, Side.SELL)):
        late = FillEvent(f"0000cafe.{suffix}.01.01", intent_id, "AAPL", side,
                         1, 100.0, rig.clock.now())
        rig.engine._ingest_fill(rig.clock.now(), late)
        assert rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == fills_before
        assert rig.store.realized_for_day("2026-10-06") == pnl_before
        assert not rig.store.open_positions()
        assert rig.store.conn.execute(
            "SELECT 1 FROM quarantined_fills WHERE exec_id=?", (late.exec_id,)
        ).fetchone()
    assert tuple(req.intent_id for req in rig.broker.sent if req.side is Side.SELL) == sells_before


def test_B16_F6_release_refuses_UNKNOWN_GTC_despite_flat_and_empty_snapshots(tmp_path):
    rig, position_id, ref, exec_id = _protected_position_flattened_in_tws(tmp_path)
    # The order disappears without a trusted terminal status. An empty fresh
    # working list alone must not resolve an UNKNOWN broker-side protection.
    rig.broker.orders[ref].status = "Inactive"
    assert rig.store.hold_intent(rig.clock.now(), ref, "synthetic missing terminal status")
    _two_fresh_flat_snapshots(rig, exec_id)
    _request_release(rig)
    assert rig.store.position(position_id)["status"] == "OPEN", [r["payload"] for r in _release_refusals(rig)]
    assert rig.store.control("frozen:AAPL") is not None
    assert _release_refusals(rig)
    assert ref not in rig.broker.cancels


def test_B16_F6_release_refuses_closed_frozen_with_held_protection(tmp_path):
    rig, position_id, ref, exec_id = _protected_position_flattened_in_tws(tmp_path)
    # The local position may already be CLOSED by another booked exit while
    # the GTC status remains unknown. A flat broker snapshot is insufficient.
    rig.store.conn.execute("UPDATE positions SET status='CLOSED', qty_open=0, closed_at=? "
                           "WHERE position_id=?", (rig.clock.now().isoformat(), position_id))
    rig.broker.orders[ref].status = "Inactive"
    assert rig.store.hold_intent(rig.clock.now(), ref, "terminal GTC state unknown")
    _two_fresh_flat_snapshots(rig, exec_id)
    _request_release(rig)
    assert rig.store.control("frozen:AAPL") is not None
    assert rig.store.intent(ref)["status"] == IntentStatus.UNKNOWN
    assert rig.store.intent(ref)["hold_reason"]
    assert rig.store.flat_point("AAPL") is None
    assert _release_refusals(rig)


@pytest.mark.parametrize("purpose,side", [(Purpose.ENTRY, Side.BUY), (Purpose.TP1, Side.SELL)])
def test_B16_F6_release_refuses_nonprotection_held_intent(tmp_path, purpose, side):
    rig, position_id, ref, exec_id = _protected_position_flattened_in_tws(tmp_path)
    rig.broker.confirm_cancel(ref)
    rig.engine._drain(rig.clock.now())
    request = OrderRequest(f"held-{purpose.value.lower()}", "AAPL", side, 1,
                           100.0, purpose, position_id)
    rig.store.create_intent(rig.clock.now(), request, atr=None, count_entry_day=None)
    assert rig.store.hold_intent(rig.clock.now(), request.intent_id,
                                 "manual resolve-intent required")
    _two_fresh_flat_snapshots(rig, exec_id)
    _request_release(rig)
    assert rig.store.position(position_id)["status"] == "OPEN"
    assert rig.store.intent(request.intent_id)["status"] == IntentStatus.UNKNOWN
    assert rig.store.intent(request.intent_id)["hold_reason"]
    assert rig.store.control("frozen:AAPL") is not None
    assert _release_refusals(rig)


@pytest.mark.parametrize("identity", ["wrong", "missing"])
def test_B16_F6_wrong_identity_cancel_hint_cannot_authorize_release(tmp_path, identity):
    rig, position_id, ref, exec_id = _protected_position_flattened_in_tws(tmp_path)
    original = rig.store.intent(ref)
    assert original["broker_order_id"] and original["perm_id"]
    order_id = original["broker_order_id"] + 1 if identity == "wrong" else None
    perm_id = original["perm_id"] + 1 if identity == "wrong" else None
    forged = StatusEvent(ref, "Cancelled", 0, 0, order_id, perm_id)
    # A same-orderRef hint from a different broker order may arrive even when
    # reqAllOpenOrders temporarily lists nothing. It is not proof that the
    # persisted GTC was cancelled by its exact orderId+permId.
    rig.engine._on_status(rig.clock.now(), forged)
    status_before_release = rig.store.intent(ref)["status"]
    rig.broker.orders[ref].status = "Inactive"  # incomplete empty work-list snapshot
    _two_fresh_flat_snapshots(rig, exec_id)
    _request_release(rig)
    assert rig.store.intent(ref)["broker_order_id"] == original["broker_order_id"]
    assert rig.store.intent(ref)["perm_id"] == original["perm_id"]
    assert status_before_release != IntentStatus.CANCEL_SEEN
    assert rig.store.intent(ref)["status"] != IntentStatus.CANCEL_SEEN
    assert rig.store.position(position_id)["status"] == "OPEN"
    assert rig.store.control("frozen:AAPL") is not None
    assert _release_refusals(rig)
    assert ref not in rig.broker.cancels

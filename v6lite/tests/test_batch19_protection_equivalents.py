"""Batch 19: six legacy ledger/risk contracts with real FakeBroker GTC fills.

These are coverage-debt equivalents, not claims that current production is red.
The R6 carried-holdings case uses an existing GTC partial fill because protected
TP deliberately refuses incomplete cross-day execution evidence. It does not
claim that cross-day TP is supported or weaken that fail-closed requirement.
"""

from __future__ import annotations

import pytest

from v6lite.config import ProtectionConfig
from v6lite.engine import Engine
from v6lite.models import IntentStatus, ProtectionRequest, Purpose
from v6lite.risk import day_pnl
from v6lite.tests.helpers import Rig, buy_bars, et

DAY2 = (2026, 10, 7)

# One-to-one original-node mapping for the review report. Old tests stay intact.
EQUIVALENT_MAP = {
    "test_review_round2::test_R3_late_entry_fill_in_two_batches_is_fully_managed":
        "test_R3_late_entry_fill_in_two_batches_is_fully_managed_protection_on",
    "test_review_round2::test_R3_late_entry_replay_after_restart_is_idempotent":
        "test_R3_late_entry_replay_after_restart_is_idempotent_protection_on",
    "test_review_round2::test_R3_three_late_batches_at_different_prices":
        "test_R3_three_late_batches_at_different_prices_protection_on",
    "test_review_round2::test_R6_baseline_uses_prior_close_holdings_not_current_quantity":
        "test_R6_baseline_uses_prior_close_holdings_not_current_quantity_protection_on",
    "test_review_round2::test_R6_full_exit_while_baseline_pending_counts_from_prior_close":
        "test_R6_full_exit_while_baseline_pending_counts_from_prior_close_protection_on",
    "test_review_round2::test_R6_prior_close_unknown_blocks_entries_but_not_exits":
        "test_R6_prior_close_unknown_blocks_entries_but_not_exits_protection_on",
}


def _rig(tmp_path, **overrides):
    return Rig(tmp_path, protection=ProtectionConfig(enabled=True), **overrides)


def _entries(rig):
    return [req for req in rig.broker.sent if req.purpose == Purpose.ENTRY]


def _no_local_stop(rig):
    assert not [req for req in rig.broker.sent if req.purpose == Purpose.STOP]


def _working_gtc(rig, position_id, quantity):
    pos = rig.store.position(position_id)
    row = rig.store.protection(position_id)
    assert pos["status"] == "OPEN" and pos["qty_open"] == quantity
    assert row is not None and row["state"] == "WORKING"
    assert row["confirmed_remaining"] == row["pending_max_remaining"] == quantity
    order = rig.broker.orders[row["intent_id"]]
    assert isinstance(order.req, ProtectionRequest)
    assert rig.store.intent(row["intent_id"])["purpose"] == Purpose.PROTECT
    assert order.req.position_id == position_id
    assert order.remaining == quantity and order.status == "Submitted"
    assert row["broker_order_id"] == order.broker_order_id
    assert row["perm_id"] == order.broker_order_id + 50_000
    _no_local_stop(rig)
    return pos, row


def _open_full(rig):
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    assert pos["qty_open"] == 20 and pos["entry_price"] == pytest.approx(113.47)
    return _working_gtc(rig, pos["position_id"], 20)


def _closed_partial_entry(tmp_path):
    """Rebuild the legacy CLOSED prerequisite with the actual L7 sell order."""
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    [entry] = _entries(rig)
    rig.broker.fill(entry.intent_id, qty=10)
    rig.advance(seconds=61)
    rig.tick()
    rig.broker.confirm_cancel(entry.intent_id)
    for _ in range(2):
        rig.advance(seconds=6)
        rig.tick()
    assert rig.store.intent(entry.intent_id)["status"] == IntentStatus.CANCELLED
    [pos] = rig.store.open_positions()
    _, old = _working_gtc(rig, pos["position_id"], 10)
    rig.px("AAPL", 107.0)
    rig.tick()
    rig.broker.fill(old["intent_id"], price=107.0)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    assert len(rig.broker.sent_protections) == 1
    _no_local_stop(rig)
    return rig, entry, pos, old


def _late_batch(rig, entry, old_position, *, quantity, price, expected_total):
    rig.px("AAPL", 113.0)
    fill = rig.broker.fill(entry.intent_id, qty=quantity, price=price)
    rig.advance(seconds=1)
    rig.tick()
    # The genuine high-priority loop confirms any same-id increase with fresh
    # working-order, position and execution evidence. Never forge WORKING.
    rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    [pos] = rig.store.open_positions()
    assert pos["position_id"] != old_position["position_id"]
    assert rig.store.intent(entry.intent_id)["recovery_position_id"] == pos["position_id"]
    booked = rig.store.conn.execute(
        "SELECT position_id,quantity,price FROM fills WHERE exec_id=?", (fill.exec_id,)).fetchone()
    assert booked["position_id"] == pos["position_id"]
    assert booked["quantity"] == quantity and booked["price"] == pytest.approx(price)
    pos, row = _working_gtc(rig, pos["position_id"], expected_total)
    assert len(rig.broker.sent_protections) == 2
    assert not any(k.startswith("anomaly:duplicate_open_position") for k in rig.store.anomalies())
    return pos, row, fill


def _close_recovery(rig, pos, row, quantity):
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert rig.broker.orders[row["intent_id"]].remaining == quantity
    fill = rig.broker.fill(row["intent_id"], price=107.0)
    assert fill.quantity == quantity
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.store.position(pos["position_id"])["qty_open"] == 0
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    assert rig.broker.orders[row["intent_id"]].remaining == 0
    assert len(rig.broker.sent_protections) == 2
    assert len(_entries(rig)) == 1
    _no_local_stop(rig)


def test_R3_late_entry_fill_in_two_batches_is_fully_managed_protection_on(tmp_path):
    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    first, row, _ = _late_batch(rig, entry, closed, quantity=5, price=113.40, expected_total=5)
    identity = (row["intent_id"], row["broker_order_id"], row["perm_id"])
    final, enlarged, _ = _late_batch(rig, entry, closed, quantity=5, price=113.50, expected_total=10)
    assert final["position_id"] == first["position_id"]
    assert final["entry_price"] == pytest.approx(113.45)
    assert (enlarged["intent_id"], enlarged["broker_order_id"], enlarged["perm_id"]) == identity
    assert enlarged["intent_id"] != old["intent_id"]
    assert rig.store.intent_filled_qty(entry.intent_id) == 20
    _close_recovery(rig, final, enlarged, 10)


def test_R3_late_entry_replay_after_restart_is_idempotent_protection_on(tmp_path):
    rig, entry, closed, _ = _closed_partial_entry(tmp_path)
    first, row, _ = _late_batch(rig, entry, closed, quantity=5, price=113.40, expected_total=5)
    before = rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    identity = (row["intent_id"], row["broker_order_id"], row["perm_id"])
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / "restart-reports")
    rig.engine.startup()  # replay real FakeBroker executions into existing ledger
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == before
    assert len(rig.broker.sent_protections) == 2
    _, replayed = _working_gtc(rig, first["position_id"], 5)
    assert (replayed["intent_id"], replayed["broker_order_id"], replayed["perm_id"]) == identity
    final, row, _ = _late_batch(rig, entry, closed, quantity=5, price=113.50, expected_total=10)
    assert final["position_id"] == first["position_id"]
    assert final["entry_price"] == pytest.approx(113.45)
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == before + 1
    assert (row["intent_id"], row["broker_order_id"], row["perm_id"]) == identity
    _close_recovery(rig, final, row, 10)


def test_R3_three_late_batches_at_different_prices_protection_on(tmp_path):
    rig, entry, closed, _ = _closed_partial_entry(tmp_path)
    cost = 0.0
    quantity = 0
    identity = None
    for qty, price in ((3, 113.40), (3, 113.50), (4, 113.60)):
        quantity += qty
        cost += qty * price
        pos, row, _ = _late_batch(rig, entry, closed, quantity=qty, price=price, expected_total=quantity)
        assert pos["entry_price"] == pytest.approx(cost / quantity)
        current = (row["intent_id"], row["broker_order_id"], row["perm_id"])
        if identity is None:
            identity = current
        assert current == identity
    assert quantity == 10 and pos["qty_open"] == 10
    assert pos["entry_price"] == pytest.approx((3 * 113.40 + 3 * 113.50 + 4 * 113.60) / 10)
    assert rig.store.intent_filled_qty(entry.intent_id) == 20
    _close_recovery(rig, pos, row, 10)


def test_R6_baseline_uses_prior_close_holdings_not_current_quantity_protection_on(tmp_path):
    rig = _rig(tmp_path)
    pos, row = _open_full(rig)
    rig.broker.prev_close["AAPL"] = None
    rig.at(et(9, 45, day=DAY2))
    rig.px("AAPL", 120.0)
    writes = (len(rig.broker.sent_protections), len(rig.broker.modified_protections))
    rig.tick()
    # Cross-day execution completeness is deliberately unavailable. TP must
    # refuse, retain the original GTC and never manufacture execution scope.
    assert not [r for r in rig.broker.sent if r.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}]
    assert (len(rig.broker.sent_protections), len(rig.broker.modified_protections)) == writes
    assert rig.broker.orders[row["intent_id"]].remaining == 20
    assert rig.engine.flags.day_risk_error is not None
    # The broker-side stop can trigger and partly fill on a rebound. This
    # authoritative SELL replaces the blocked TP as the original risk test's
    # 6-share exit carrier; it does not restore cross-day TP support.
    rig.px("AAPL", 107.0)
    rig.advance(seconds=1)
    rig.tick()
    rig.px("AAPL", 120.0)
    fill = rig.broker.fill(row["intent_id"], qty=6, price=120.0)
    rig.advance(seconds=1)
    rig.tick()
    assert fill.quantity == 6
    assert rig.store.position(pos["position_id"])["qty_open"] == 14
    assert rig.store.protection(pos["position_id"])["confirmed_remaining"] == 14
    assert rig.broker.orders[row["intent_id"]].remaining == 14
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 0
    rig.broker.prev_close["AAPL"] = 130.0
    rig.advance(minutes=5)
    rig.px("AAPL", 120.0)
    rig.tick()
    pnl = day_pnl(rig.store, "2026-10-07", marks={"AAPL": 120.0}, prior_close={"AAPL": 130.0})
    assert pnl.pnl == pytest.approx(20 * (120.0 - 130.0) - 1.0)
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 1
    assert not [r for r in rig.broker.sent if r.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}]
    assert (len(rig.broker.sent_protections), len(rig.broker.modified_protections)) == writes
    _no_local_stop(rig)


def test_R6_full_exit_while_baseline_pending_counts_from_prior_close_protection_on(tmp_path):
    rig = _rig(tmp_path)
    pos, row = _open_full(rig)
    rig.broker.prev_close["AAPL"] = None
    rig.at(et(9, 45, day=DAY2))
    rig.px("AAPL", 107.0)
    rig.tick()
    assert rig.engine.flags.day_risk_error is not None
    assert rig.broker.orders[row["intent_id"]].remaining == 20
    rig.broker.fill(row["intent_id"], price=106.0)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.open_positions() == []
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 0
    rig.broker.prev_close["AAPL"] = 115.0
    rig.advance(minutes=5)
    rig.tick()
    pnl = day_pnl(rig.store, "2026-10-07", marks={}, prior_close={"AAPL": 115.0})
    assert pnl.pnl == pytest.approx(20 * (106.0 - 115.0) - 1.0)
    assert rig.store.risk_day("2026-10-07")["daily_stop_latched"] == 1
    assert len(rig.broker.sent_protections) == 1
    _no_local_stop(rig)


def test_R6_prior_close_unknown_blocks_entries_but_not_exits_protection_on(tmp_path):
    rig = _rig(tmp_path)
    pos, row = _open_full(rig)
    rig.broker.prev_close["AAPL"] = None
    rig.broker.bars["TSLA"] = buy_bars()
    rig.px("TSLA", 113.13)
    rig.at(et(10, 0, 30, day=DAY2))
    rig.px("AAPL", 113.2)
    rig.tick()
    blocks = rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    assert any("baseline" in block for block in blocks)
    assert len(_entries(rig)) == 1
    rig.advance(seconds=31)
    rig.px("AAPL", 107.0)
    rig.tick()
    assert rig.broker.orders[row["intent_id"]].remaining == 20
    assert len(rig.broker.sent_protections) == 1
    rig.broker.fill(row["intent_id"], price=106.0)
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    assert rig.store.open_positions() == []
    assert len(_entries(rig)) == 1
    assert rig.engine.flags.day_risk_error is not None
    _no_local_stop(rig)

"""Protection-on F6 regressions against a synthetic broker and clock."""

from __future__ import annotations

from datetime import timedelta

from v6lite.config import ProtectionConfig
from v6lite.models import Side
from v6lite.tests.helpers import Rig
from v6lite.tests.test_review_round2 import _two_open_positions


def test_B16_F6_frozen_steady_contradiction_rechecks_at_bounded_cadence(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    [position] = rig.store.open_positions()
    protection = rig.store.protection(position["position_id"])
    assert protection["state"] == "WORKING"
    assert protection["confirmed_remaining"] == position["qty_open"]

    # Reproduce an unchanged ledger/broker contradiction. This is already P0
    # and frozen; repeating the same failed three-source check every second
    # adds broker load without resolving the uncertainty.
    rig.store.conn.execute("UPDATE positions SET qty_open=10, qty_initial=10")
    for _ in range(4):
        rig.advance(seconds=31)
        rig.tick()
    assert rig.store.control("frozen:AAPL") is not None
    assert any("P0" in title and "AAPL" in body for title, body in rig.channel.messages)
    protection = rig.store.protection(position["position_id"])
    assert protection["state"] == "WORKING"
    assert protection["pending_qty"] is None
    assert protection["gap_a_started_at"] is None
    assert protection["gap_b_started_at"] is None
    confirmed_at = protection["confirmed_at"]
    prior_writes = (len(rig.broker.sent_protections),
                    len(rig.broker.modified_protections), len(rig.broker.cancels))

    order_queries = []
    position_queries = []
    old_orders, old_positions = rig.broker.open_orders, rig.broker.positions_fresh

    def counted_orders():
        order_queries.append(rig.clock.now())
        return old_orders()

    def counted_positions():
        position_queries.append(rig.clock.now())
        return old_positions()

    rig.broker.open_orders = counted_orders
    rig.broker.positions_fresh = counted_positions
    rig.engine._evaluate_exits = lambda *a, **k: None  # isolate protection/reconcile queries
    for _ in range(60):
        rig.advance(seconds=1)
        rig.tick()

    attempts = sorted(set(order_queries))
    assert 1 <= len(attempts) <= 5, (len(order_queries), len(position_queries), attempts)
    assert all(b - a >= timedelta(seconds=14) for a, b in zip(attempts, attempts[1:]))
    assert len(order_queries) <= 10 and len(position_queries) <= 10
    assert rig.store.protection(position["position_id"])["confirmed_at"] == confirmed_at
    assert rig.store.control("frozen:AAPL") is not None
    assert (len(rig.broker.sent_protections), len(rig.broker.modified_protections),
            len(rig.broker.cancels)) == prior_writes
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]


def test_B16_F6_active_B_cancel_pending_keeps_per_tick_evidence(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    [position] = rig.store.open_positions()
    rig.px("AAPL", position["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    [tp] = [req for req in rig.broker.sent if req.side is Side.SELL]
    rig.advance(seconds=10)
    rig.tick()
    row = rig.store.protection(position["position_id"])
    assert row["state"] == "TP_CANCEL_PENDING" and row["gap_b_started_at"]
    assert tp.intent_id in rig.broker.cancels

    query_ticks = []
    original = rig.broker.open_orders

    def counted_orders():
        query_ticks.append(rig.clock.now())
        return original()

    rig.broker.open_orders = counted_orders
    for _ in range(3):
        rig.advance(seconds=1)
        rig.tick()
    assert len(set(query_ticks)) >= 3, query_ticks
    assert rig.store.protection(position["position_id"])["state"] == "TP_CANCEL_PENDING"
    assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]


def test_B16_F6_slow_steady_check_does_not_starve_other_symbol_exit(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    _two_open_positions(rig)
    assert sorted(row["symbol"] for row in rig.store.open_positions()) == ["AAPL", "SPY"]
    assert all(rig.store.protection(row["position_id"])["state"] == "WORKING"
               for row in rig.store.open_positions())

    spy_exit_checks = []
    original_exit = rig.engine._evaluate_position_exit

    def record_exit(now, row, broker_qty):
        if row["symbol"] == "SPY":
            spy_exit_checks.append(rig.clock.now())
        return original_exit(now, row, broker_qty)

    rig.engine._evaluate_position_exit = record_exit
    original_executions = rig.broker.executions_today
    slow_calls = []

    def slow_executions():
        slow_calls.append(rig.clock.now())
        rig.clock.advance(seconds=15)
        return original_executions()

    rig.broker.executions_today = slow_executions
    for tick_index in range(70):
        if tick_index == 35:
            rig.broker.external_positions["AAPL"] = (-5.0, 0.0)
        rig.advance(seconds=1)
        rig.tick()

    gaps = [(b - a).total_seconds() for a, b in zip(spy_exit_checks, spy_exit_checks[1:])]
    assert slow_calls and gaps
    assert max(gaps) <= rig.cfg.exit_check_interval_seconds + 1, gaps
    assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]

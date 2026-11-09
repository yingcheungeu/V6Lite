"""L7 steady broker quantity contradictions block same-tick global BUYs."""

from __future__ import annotations

from dataclasses import replace

import pytest

from v6lite.config import ProtectionConfig
from v6lite.models import Purpose, Side
from v6lite.tests.helpers import Rig, buy_bars, et


@pytest.mark.parametrize("fault", [
    "broker_qty_18", "broker_qty_negative", "broker_qty_fractional",
    "working_sell_remaining_negative", "working_sell_remaining_nan",
])
def test_B16_F6_invalid_steady_protection_evidence_blocks_same_tick_other_symbol_entry(
        tmp_path, monkeypatch, fault):
    rig = Rig(tmp_path, protection=ProtectionConfig(),
              scan_times_et=("10:00", "10:02"))
    try:
        rig.open_aapl()
        row = rig.store.protections()[0]
        assert row["symbol"] == "AAPL" and row["state"] == "WORKING"
        assert row["pending_qty"] is None and row["tp_stage"] is None
        assert row["gap_a_started_at"] is None and row["gap_b_started_at"] is None
        rig.broker.bars["SPY"] = buy_bars()
        rig.at(et(10, 2, 5))
        rig.px("SPY", 113.13)
        rig.engine._last_reconcile = rig.clock.now()
        rig.engine._reconcile_due = False
        before_modifies = len(rig.broker.modified_protections)
        real_orders = rig.broker.open_orders
        state = {"armed": True}

        def orders_then_bad_evidence():
            orders = real_orders()
            if state["armed"]:
                state["armed"] = False
                if fault == "broker_qty_18":
                    rig.broker.external_positions["AAPL"] = (-2.0, 0.0)
                elif fault == "broker_qty_negative":
                    rig.broker.external_positions["AAPL"] = (-21.0, 0.0)
                elif fault == "broker_qty_fractional":
                    rig.broker.external_positions["AAPL"] = (-0.5, 0.0)
                else:
                    bad_remaining = (-1.0 if fault.endswith("negative") else float("nan"))
                    orders = [replace(order, remaining=bad_remaining)
                              if order.intent_id == row["intent_id"] else order
                              for order in orders]
            return orders

        monkeypatch.setattr(rig.broker, "open_orders", orders_then_bad_evidence)
        rig.tick()
        assert not state["armed"]  # the mismatch landed inside steady evidence
        spy_buys = [req.intent_id for req in rig.broker.sent
                    if req.symbol == "SPY" and req.purpose == Purpose.ENTRY]
        assert "AAPL" in rig.engine.flags.quantity_suspect and not spy_buys, (
            rig.engine.flags.quantity_suspect, spy_buys)
        assert len(rig.broker.modified_protections) == before_modifies
        assert not [req for req in rig.broker.sent
                    if getattr(req, "order_type", "") == "MKT"]
    finally:
        rig.store.close()

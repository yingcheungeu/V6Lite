"""A TP decision must be rechecked after slow fresh evidence crosses the stop."""

from __future__ import annotations

import inspect
from datetime import timedelta

import pytest

from v6lite.config import ProtectionConfig
from v6lite.models import Purpose, Side
from v6lite.tests.helpers import Rig


@pytest.mark.parametrize("new_quote", ["below_stop", "below_tp", "missing", "stale"])
def test_protected_queued_TP_aborts_if_latest_quote_invalidates_it_during_query(
        tmp_path, monkeypatch, new_quote):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    try:
        rig.open_aapl()
        row = rig.store.protections()[0]
        ref = row["intent_id"]
        initial_remaining = rig.broker.orders[ref].remaining
        assert row["state"] == "WORKING" and initial_remaining == 20
        rig.advance(seconds=31)
        rig.px("AAPL", 120.0)  # TP1 at 119.47; STOP at 108.47
        real_open_orders = rig.broker.open_orders
        flipped = {"value": False}

        def quote_flips_inside_TP_evidence():
            callers = {frame.function for frame in inspect.stack()[1:14]}
            if "_protection_begin_tp" in callers and not flipped["value"]:
                flipped["value"] = True
                if new_quote == "below_stop":
                    rig.px("AAPL", 107.0)
                elif new_quote == "below_tp":
                    rig.px("AAPL", 115.0)
                elif new_quote == "missing":
                    rig.broker.quotes.pop("AAPL", None)
                else:
                    rig.broker.set_quote("AAPL", bid=119.97, ask=120.03, last=120.0,
                                         ts=rig.clock.now() - timedelta(minutes=5))
            return real_open_orders()

        monkeypatch.setattr(rig.broker, "open_orders", quote_flips_inside_TP_evidence)
        before_modifies = len(rig.broker.modified_protections)
        rig.tick()

        assert flipped["value"], "the price change must occur inside the TP evidence query"
        assert len(rig.broker.modified_protections) == before_modifies
        assert rig.broker.orders[ref].remaining == initial_remaining
        assert rig.store.protection(row["position_id"])["tp_stage"] is None
        assert not [req for req in rig.broker.sent
                    if req.side is Side.SELL and req.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}]
        assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]
    finally:
        rig.store.close()


def test_protected_TP_aborts_if_GTC_fill_arrives_after_evidence_before_write(
        tmp_path, monkeypatch):
    """The final write gate must reject evidence from before an urgent fill."""
    rig = Rig(tmp_path, protection=ProtectionConfig())
    try:
        rig.open_aapl()
        row = rig.store.protections()[0]
        ref = row["intent_id"]
        pid = row["position_id"]
        assert rig.store.position(pid)["qty_open"] == 20
        assert rig.broker.orders[ref].remaining == 20
        rig.px("AAPL", 120.0)  # TP1 at 119.47; still valid after the callback.
        assert rig.store.begin_stage(rig.clock.now(), pid, Purpose.TP1, 6)
        real_quote = rig.broker.quote
        injected = {"value": False}

        def fill_during_final_quote(symbol):
            callers = {frame.function for frame in inspect.stack()[1:14]}
            if symbol == "AAPL" and "_protection_begin_tp" in callers and not injected["value"]:
                injected["value"] = True
                rig.broker.fill(ref, qty=1)
                rig.engine._drain(rig.clock.now())  # urgent execDetails before the TP write
            return real_quote(symbol)

        monkeypatch.setattr(rig.broker, "quote", fill_during_final_quote)
        before_modifies = len(rig.broker.modified_protections)
        rig.engine._protection_begin_tp(rig.clock.now(), pid, Purpose.TP1)

        assert injected["value"]
        assert rig.store.position(pid)["qty_open"] == 19
        assert rig.broker.orders[ref].remaining == 19
        assert rig.store.protection(pid)["state"] == "WORKING"
        assert len(rig.broker.modified_protections) == before_modifies
        assert rig.store.protection(pid)["tp_stage"] is None
        assert not [req for req in rig.broker.sent
                    if req.side is Side.SELL and req.purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3}]
        assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]
    finally:
        rig.store.close()

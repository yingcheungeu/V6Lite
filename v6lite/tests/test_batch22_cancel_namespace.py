"""Batch 22: trustworthy protection refs cannot masquerade as DAY orders.

All broker data are offline doubles. The same LMT/DAY wire payload is used as
a negative protection case and a positive ordinary-order cancellation case.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import LiteConfig, validate

ORDER_ID = 77
PERM_ID = 50077
CON_ID = 265598
ACCOUNT = "DU0000000"


class _OfflineIB:
    def __init__(self):
        self.placed = []
        self.cancelled = []

    def connect(self, *args, **kwargs):
        raise AssertionError("offline tests cannot connect")

    def placeOrder(self, *args, **kwargs):
        raise AssertionError("cancellation must not submit or modify")

    def cancelOrder(self, order):
        self.cancelled.append(copy.deepcopy(order))


@pytest.fixture
def adapter(tmp_path):
    cfg = validate(LiteConfig(
        mode="paper", account=ACCOUNT, client_id=131, port=4002,
        tradable_symbols=("AAPL",), scan_symbols=("AAPL",),
        state_dir=str(tmp_path / "isolated-state"),
    ))
    broker = IbkrBroker(cfg)
    broker.ib = _OfflineIB()
    return broker


def _cached_DAY(ref, side, limit):
    return SimpleNamespace(
        order=SimpleNamespace(
            orderRef=ref, orderId=ORDER_ID, permId=PERM_ID,
            account=ACCOUNT, clientId=131, action=side,
            orderType="LMT", tif="DAY", outsideRth=False,
            totalQuantity=3, auxPrice=0.0, lmtPrice=limit,
        ),
        contract=SimpleNamespace(
            symbol="AAPL", conId=CON_ID, secType="STK", currency="USD"),
    )


def _cancel_exact(adapter, ref):
    adapter.cancel(
        ref, expected_order_id=ORDER_ID, expected_perm_id=PERM_ID,
        expected_con_id=CON_ID, allow_lookup=False,
    )


@pytest.mark.parametrize("side", ["SELL", "BUY"])
@pytest.mark.parametrize("limit", [335.77, None], ids=["finite-limit", "empty-limit"])
def test_B22_trusted_protection_ref_cannot_bypass_tuple_check_as_LMT_DAY(adapter, side, limit):
    ref = "v6l-protect-namespace-b22"
    adapter._trades[ref] = _cached_DAY(ref, side, limit)
    with pytest.raises(BrokerError):
        _cancel_exact(adapter, ref)
    assert adapter.ib.cancelled == []
    assert adapter.ib.placed == []


@pytest.mark.parametrize("side", ["SELL", "BUY"])
@pytest.mark.parametrize("limit", [335.77, None], ids=["finite-limit", "empty-limit"])
def test_B22_ordinary_DAY_ref_with_same_wire_payload_and_ids_remains_cancellable(
        adapter, side, limit):
    ref = "v6l-day-namespace-b22"
    adapter._trades[ref] = _cached_DAY(ref, side, limit)
    _cancel_exact(adapter, ref)
    [order] = adapter.ib.cancelled
    assert (order.orderRef, order.orderType, order.tif, order.action) == (
        ref, "LMT", "DAY", side)
    assert (order.orderId, order.permId, order.clientId, order.account) == (
        ORDER_ID, PERM_ID, 131, ACCOUNT)
    assert order.lmtPrice == limit
    assert adapter.ib.placed == []

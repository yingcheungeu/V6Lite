"""Batch 22 offline snapshot, cancellation, and cent-floor boundary tests.

These tests use no runtime/User configuration or state and cannot connect.
The B20 submission-name fallback invokes its real old API so baseline failures
are behavioral assertions/rejections rather than missing-method/import errors.
"""
from __future__ import annotations

import copy
import sys
from decimal import Decimal, ROUND_FLOOR
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import LiteConfig, validate
from v6lite.models import ProtectionRequest

REF = "v6l-protect-b22-proof"
ACCOUNT = "DU0000000"
ORDER_ID = 77
PERM_ID = 50077
CON_ID = 265598
UNSET_PRICE = sys.float_info.max
_MISSING = object()


class _OfflineIB:
    def __init__(self):
        self.placed = []
        self.cancelled = []
        self.snapshot = []
        self.query_count = 0

    def connect(self, *args, **kwargs):
        raise AssertionError("offline tests cannot connect")

    def isConnected(self):
        return True

    def placeOrder(self, contract, order):
        order.orderId = order.orderId or ORDER_ID
        order.permId = order.permId or PERM_ID
        order.clientId = 131
        self.placed.append((copy.deepcopy(contract), copy.deepcopy(order)))
        trade = SimpleNamespace(
            contract=contract, order=order,
            orderStatus=SimpleNamespace(
                orderId=order.orderId, permId=order.permId, clientId=131,
                status="PreSubmitted", filled=0.0,
                remaining=float(order.totalQuantity)),
            log=[], fills=[], advancedError="",
        )
        return trade

    def cancelOrder(self, order):
        self.cancelled.append(copy.deepcopy(order))

    def openTrades(self):
        return list(self.snapshot)

    def trades(self):
        return list(self.snapshot)

    def reqAllOpenOrders(self):
        self.query_count += 1
        return list(self.snapshot)


@pytest.fixture
def adapter(tmp_path):
    cfg = validate(LiteConfig(
        mode="paper", account=ACCOUNT, client_id=131, port=4002,
        tradable_symbols=("AAPL",), scan_symbols=("AAPL",),
        state_dir=str(tmp_path / "isolated-state"),
    ))
    broker = IbkrBroker(cfg)
    broker.ib = _OfflineIB()
    broker._contracts["AAPL"] = SimpleNamespace(
        symbol="AAPL", conId=CON_ID, secType="STK",
        currency="USD", exchange="SMART")
    return broker


def _trade(**changes):
    order = SimpleNamespace(
        orderRef=REF, orderId=ORDER_ID, permId=PERM_ID, account=ACCOUNT,
        clientId=131, action="SELL", orderType="STP", tif="GTC",
        outsideRth=False, totalQuantity=3, auxPrice=335.77,
        lmtPrice=UNSET_PRICE,
    )
    status = SimpleNamespace(
        orderId=ORDER_ID, permId=PERM_ID, clientId=131,
        status="PreSubmitted", filled=0.0, remaining=3.0,
    )
    contract = SimpleNamespace(
        symbol="AAPL", conId=CON_ID, secType="STK",
        currency="USD", exchange="SMART")
    for name, value in changes.items():
        target_name, field = name.split("__", 1)
        target = {"order": order, "status": status, "contract": contract}[target_name]
        if value is _MISSING:
            delattr(target, field)
        else:
            setattr(target, field, value)
    return SimpleNamespace(
        order=order, orderStatus=status, contract=contract,
        log=[], fills=[], advancedError="",
    )


def _cancel_same_ids(adapter, ref=REF):
    adapter.cancel(
        ref, expected_order_id=ORDER_ID, expected_perm_id=PERM_ID,
        expected_con_id=CON_ID, allow_lookup=False,
    )


def _request(trigger, *, persisted=False):
    return ProtectionRequest(
        REF, "pos-b22-proof", "AAPL", 3, trigger, None,
        ORDER_ID if persisted else None, PERM_ID if persisted else None,
        CON_ID if persisted else None, 131 if persisted else None,
        ACCOUNT if persisted else "", allow_lookup=not persisted,
    )


@pytest.mark.parametrize("value", [
    pytest.param(None, id="unknown-None"),
    pytest.param(0, id="integer-zero-is-not-False"),
    pytest.param(_MISSING, id="missing"),
])
def test_B22_fresh_snapshot_preserves_unknown_RTH_instead_of_fabricating_False(adapter, value):
    adapter.ib.snapshot = [_trade(order__outsideRth=value)]
    [order] = adapter.open_orders()
    assert order.outside_rth is not False
    if value is _MISSING or value is None:
        assert order.outside_rth is None
    else:
        assert type(order.outside_rth) is int and order.outside_rth == 0
    assert (order.intent_id, order.broker_order_id, order.perm_id) == (
        REF, ORDER_ID, PERM_ID)
    assert adapter.ib.placed == adapter.ib.cancelled == []


def test_B22_fresh_snapshot_preserves_explicit_False_as_strong_RTH_evidence(adapter):
    adapter.ib.snapshot = [_trade()]
    [order] = adapter.open_orders()
    assert order.outside_rth is False
    assert (order.order_type, order.account, order.con_id, order.client_id) == (
        "STP", ACCOUNT, CON_ID, 131)
    assert order.limit_price is None
    assert adapter.ib.placed == adapter.ib.cancelled == []


@pytest.mark.parametrize("order_type", ["STP", "STP LMT"])
@pytest.mark.parametrize("changes", [
    pytest.param({"contract__secType": None}, id="secType-None"),
    pytest.param({"contract__secType": ""}, id="secType-empty"),
    pytest.param({"contract__secType": "OPT"}, id="secType-OPT"),
    pytest.param({"contract__secType": _MISSING}, id="secType-missing"),
    pytest.param({"contract__currency": None}, id="currency-None"),
    pytest.param({"contract__currency": ""}, id="currency-empty"),
    pytest.param({"contract__currency": "EUR"}, id="currency-EUR"),
    pytest.param({"contract__currency": _MISSING}, id="currency-missing"),
])
def test_B22_own_protection_snapshot_requires_explicit_STK_USD_even_with_matching_ids(
        adapter, order_type, changes):
    adapter.ib.snapshot = [_trade(order__orderType=order_type, **changes)]
    with pytest.raises(BrokerError):
        adapter.open_orders()
    assert adapter.ib.placed == adapter.ib.cancelled == []
    assert adapter.ib.query_count == 1


@pytest.mark.parametrize("changes", [
    pytest.param({"order__orderRef": "v6l-other-proof"}, id="wire-ref-conflict"),
    pytest.param({"order__orderType": "STP LMT"}, id="legacy-type"),
    pytest.param({"order__action": "BUY"}, id="wrong-side"),
    pytest.param({"order__tif": "DAY"}, id="wrong-tif"),
    pytest.param({"order__outsideRth": True}, id="outside-RTH"),
    pytest.param({"order__outsideRth": None}, id="unknown-RTH"),
    pytest.param({"order__outsideRth": 0}, id="numeric-zero-RTH"),
    pytest.param({"order__outsideRth": _MISSING}, id="missing-RTH"),
    pytest.param({"contract__secType": "OPT"}, id="non-stock"),
    pytest.param({"contract__secType": None}, id="unknown-secType"),
    pytest.param({"contract__secType": _MISSING}, id="missing-secType"),
    pytest.param({"contract__currency": "EUR"}, id="non-USD"),
    pytest.param({"contract__currency": None}, id="unknown-currency"),
    pytest.param({"contract__currency": _MISSING}, id="missing-currency"),
])
def test_B22_cached_protection_cancel_rechecks_wire_tuple_even_when_three_ids_match(
        adapter, changes):
    adapter._trades[REF] = _trade(**changes)
    with pytest.raises(BrokerError):
        _cancel_same_ids(adapter)
    assert adapter.ib.placed == adapter.ib.cancelled == []
    assert adapter.ib.query_count == 0


@pytest.mark.parametrize("echo", [UNSET_PRICE, 340.19000000000005])
def test_B22_correct_typed_STP_tuple_can_cancel_same_ids_without_replacement(adapter, echo):
    adapter._trades[REF] = _trade(order__lmtPrice=echo)
    _cancel_same_ids(adapter)
    [order] = adapter.ib.cancelled
    assert (order.orderRef, order.orderId, order.permId, order.orderType) == (
        REF, ORDER_ID, PERM_ID, "STP")
    assert (order.action, order.tif, order.outsideRth) == ("SELL", "GTC", False)
    assert order.account == ACCOUNT
    assert adapter.ib.placed == []
    assert adapter.ib.query_count == 0


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_B22_DAY_LMT_with_expected_ids_is_not_misclassified_as_protection(adapter, side):
    ref = "v6l-day-b22-proof"
    adapter._trades[ref] = _trade(
        order__orderRef=ref, order__orderType="LMT", order__tif="DAY",
        order__action=side, order__lmtPrice=335.77, order__auxPrice=0.0,
    )
    _cancel_same_ids(adapter, ref)
    [order] = adapter.ib.cancelled
    assert (order.orderRef, order.orderType, order.tif, order.action) == (
        ref, "LMT", "DAY", side)
    assert (order.orderId, order.permId) == (ORDER_ID, PERM_ID)
    assert adapter.ib.placed == []
    assert adapter.ib.query_count == 0


@pytest.mark.parametrize("trigger", [335.779, 335.775, 100.129, 1.009, 335.77])
@pytest.mark.parametrize("operation", ["submit", "modify"])
def test_B22_adapter_auxPrice_is_decimal_cent_floor_on_submit_and_modify(
        adapter, trigger, operation):
    if operation == "modify":
        adapter._trades[REF] = _trade()
        adapter.modify_protection(_request(trigger, persisted=True))
    else:
        method = "submit_stop" if callable(getattr(adapter, "submit_stop", None)) else "submit_stop_limit"
        getattr(adapter, method)(_request(trigger))
    [(contract, order)] = adapter.ib.placed
    expected = float(Decimal(str(trigger)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))
    assert order.auxPrice == expected
    assert (order.orderType, order.action, order.tif, order.outsideRth) == (
        "STP", "SELL", "GTC", False)
    assert order.totalQuantity == 3
    assert (order.orderRef, order.account, contract.conId) == (REF, ACCOUNT, CON_ID)
    assert "lmtPrice" not in order.nonDefaults()
    assert order.lmtPrice == UNSET_PRICE
    assert order.orderType != "MKT"
    if operation == "modify":
        assert (order.orderId, order.permId, order.clientId) == (
            ORDER_ID, PERM_ID, 131)
    assert adapter.ib.cancelled == []
    assert adapter.ib.query_count == 0

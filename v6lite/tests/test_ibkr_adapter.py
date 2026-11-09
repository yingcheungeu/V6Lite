"""IBKR adapter tests with a stand-in for ib_insync.IB (no gateway needed),
plus static guards on the whole package."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.models import CommissionEvent, FillEvent, OrderRequest, Purpose, Side, StatusEvent
from v6lite.tests.helpers import make_cfg

PKG = Path(__file__).resolve().parents[1]


class _Event(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self


class FakeIB:
    def __init__(self):
        self.execDetailsEvent = _Event()
        self.commissionReportEvent = _Event()
        self.orderStatusEvent = _Event()
        self.disconnectedEvent = _Event()
        self.errorEvent = _Event()
        self.accountValueEvent = _Event()
        self.placed = []
        self.connected = False

    def connect(self, *a, **kw):
        self.connected = True
        self.connect_kwargs = kw

    def reqMarketDataType(self, t):
        self.md_type = t

    def isConnected(self):
        return self.connected

    def qualifyContracts(self, contract):
        contract.conId = 265598
        return [contract]

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return SimpleNamespace(order=SimpleNamespace(orderId=77, permId=0, orderRef=order.orderRef),
                               orderStatus=SimpleNamespace(status="PendingSubmit"), contract=contract)


@pytest.fixture
def adapter(tmp_path):
    broker = IbkrBroker(make_cfg(tmp_path))
    broker.ib = FakeIB()
    broker.connect()
    return broker


def _req(**kw):
    base = dict(intent_id="v6l-abc", symbol="AAPL", side=Side.BUY, quantity=10, limit_price=113.47,
                purpose=Purpose.ENTRY, position_id="pos-1")
    base.update(kw)
    return OrderRequest(**base)


def test_connect_binds_account_and_client(adapter):
    kw = adapter.ib.connect_kwargs
    assert kw["clientId"] == 131 and kw["account"] == "DU0000000" and kw["readonly"] is False


def test_submit_is_limit_day_rth_with_order_ref(adapter):
    adapter.qualify("AAPL")
    ack = adapter.submit_limit(_req())
    contract, order = adapter.ib.placed[0]
    assert (order.orderType, order.action, order.totalQuantity, order.lmtPrice) == ("LMT", "BUY", 10, 113.47)
    assert (order.tif, order.outsideRth, order.orderRef, order.account) == ("DAY", False, "v6l-abc", "DU0000000")
    assert ack.broker_order_id == 77


def test_final_boundary_rejects_bad_requests(adapter):
    adapter.qualify("AAPL")
    for bad in (_req(intent_id="manual-1"), _req(quantity=0), _req(limit_price=float("nan")),
                _req(symbol="TSLA")):
        with pytest.raises(BrokerError):
            adapter.submit_limit(bad)
    assert adapter.ib.placed == []


def test_read_only_session_cannot_place_orders(tmp_path):
    broker = IbkrBroker(make_cfg(tmp_path), read_only=True)
    broker.ib = FakeIB()
    broker.connect()
    broker.qualify("AAPL")
    with pytest.raises(BrokerError):
        broker.submit_limit(_req())


def test_place_exception_is_reported_as_maybe_sent(adapter):
    adapter.qualify("AAPL")

    def boom(*a):
        raise ConnectionError("socket reset")
    adapter.ib.placeOrder = boom
    with pytest.raises(BrokerError) as info:
        adapter.submit_limit(_req())
    assert info.value.maybe_sent


def test_callbacks_translate_to_events(adapter):
    t = datetime(2026, 10, 6, 14, 1, tzinfo=timezone.utc)
    execution = SimpleNamespace(execId="0001f4e8.1", orderRef="v6l-abc", side="BOT", shares=10.0, price=113.4,
                                time=t, orderId=77, permId=12345, acctNumber="DU0000000")
    fill = SimpleNamespace(execution=execution, contract=SimpleNamespace(symbol="AAPL"))
    adapter._on_exec(None, fill)
    adapter._on_commission(None, fill, SimpleNamespace(execId="0001f4e8.1", commission=1.0))
    trade = SimpleNamespace(order=SimpleNamespace(orderRef="v6l-abc", orderId=77),
                            orderStatus=SimpleNamespace(status="Filled", filled=10.0, remaining=0.0, permId=12345),
                            log=[])
    adapter._on_status(trade)
    foreign = SimpleNamespace(execution=SimpleNamespace(**{**execution.__dict__, "acctNumber": "DU999"}),
                              contract=fill.contract)
    adapter._on_exec(None, foreign)
    events = adapter.drain_events()
    assert isinstance(events[0], FillEvent) and events[0].intent_id == "v6l-abc" and events[0].side is Side.BUY
    assert isinstance(events[1], CommissionEvent) and events[1].commission == 1.0
    assert isinstance(events[2], StatusEvent) and events[2].raw_status == "Filled"
    assert len(events) == 3  # foreign-account execution ignored


def test_connectivity_error_codes(adapter):
    adapter._on_error(-1, 1100, "Connectivity lost", None)
    adapter._on_error(-1, 1102, "restored", None)
    events = adapter.drain_events()
    assert [e.connected for e in events] == [False, True]


# ---------------------------------------------------------------- static guards
def test_single_physical_order_call_and_no_market_orders():
    sources = {p: p.read_text(encoding="utf-8") for p in PKG.rglob("*.py") if "tests" not in p.parts}
    place_calls = sum(len(re.findall(r"\.placeOrder\(", src)) for src in sources.values())
    assert place_calls == 1
    assert not any("MarketOrder" in src or "orderType=\"MKT\"" in src for src in sources.values())


def test_no_dependency_on_legacy_v6_package():
    import ast
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.split(".")[0] == "trading_bot_v6" for n in names), path

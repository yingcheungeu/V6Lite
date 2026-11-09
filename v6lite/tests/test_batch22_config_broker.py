"""Batch 22 configuration and IBKR protection boundary contracts.

Every broker is an offline double; every mutable database/config is under
pytest tmp_path. The only compatibility helper selects the old B20 submission
name so that the unchanged baseline fails on STP behavior rather than imports.
"""
from __future__ import annotations

import copy
import inspect
import sys
import tomllib
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ConfigError, LiteConfig, ProtectionConfig, load_config, validate
from v6lite.models import BrokerAck, OrderRequest, PriceObservationEvent, ProtectionRequest, Purpose, Side, StatusEvent
from v6lite.tests.helpers import Rig, et

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
REMOVAL_MESSAGE = "保护单已改为 STP，不再使用 limit_offset_pct，请从配置中删除"
UNSET_PRICE = sys.float_info.max
REJECT_REASON = "Order cancelled - reason: We cannot accept your stop order."


def _config_file(tmp_path, protection=""):
    path = tmp_path / "batch22.toml"
    path.write_text(
        'mode="paper"\n[ibkr]\naccount="DU0000000"\nport=4002\n'
        '[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n'
        + protection,
        encoding="utf-8",
    )
    return path


def _config(tmp_path):
    return validate(LiteConfig(
        mode="paper", account="DU0000000", client_id=131, port=4002,
        tradable_symbols=("AAPL",), scan_symbols=("AAPL",),
        state_dir=str(tmp_path / "isolated-state"),
        protection=ProtectionConfig(enabled=True),
    ))


def _stop_method_name(broker):
    # B20 has no submit_stop. Calling its actual old method is deliberately
    # allowed only here: wire assertions below still demand the new contract.
    return "submit_stop" if callable(getattr(broker, "submit_stop", None)) else "submit_stop_limit"


def _submit_stop(broker, request):
    return getattr(broker, _stop_method_name(broker))(request)


def _protect_request(**changes):
    values = dict(
        intent_id="v6l-protect-b22", position_id="pos-b22", symbol="AAPL",
        quantity=3, trigger_price=335.77, limit_price=None,
        broker_order_id=None, perm_id=None, con_id=None, client_id=None,
        account="", allow_lookup=True,
    )
    values.update(changes)
    return ProtectionRequest(**values)


def test_B22_protection_defaults_to_STP_without_legacy_offset(tmp_path):
    cfg = load_config(_config_file(tmp_path))
    assert getattr(cfg.protection, "order_type", None) == "STP"
    assert "limit_offset_pct" not in asdict(cfg.protection)
    assert cfg.protection.enabled is True
    assert cfg.protection.trigger_unfilled_p0_seconds == 60


def test_B22_explicit_STP_and_omitted_default_have_same_fingerprint(tmp_path):
    default = load_config(_config_file(tmp_path))
    explicit = load_config(_config_file(tmp_path, '[protection]\norder_type="STP"\n'))
    assert getattr(explicit.protection, "order_type", None) == "STP"
    assert explicit.fingerprint() == default.fingerprint()


@pytest.mark.parametrize("value", ['"STP LMT"', '"LMT"', '"MKT"', '"stp"', '""', "true", "0"])
def test_B22_only_exact_STP_is_valid_protection_type(tmp_path, value):
    with pytest.raises(ConfigError, match="order_type"):
        load_config(_config_file(tmp_path, "[protection]\norder_type=" + value + "\n"))


@pytest.mark.parametrize("value", ["0.03", "0", "nan", "true"])
def test_B22_removed_offset_is_diagnosed_even_when_protection_disabled(tmp_path, value):
    path = _config_file(
        tmp_path, "[protection]\nenabled=false\nlimit_offset_pct=" + value + "\n")
    with pytest.raises(ConfigError) as failure:
        load_config(path)
    assert REMOVAL_MESSAGE in str(failure.value)


def test_B22_protection_order_type_is_bound_into_fingerprint(tmp_path):
    cfg = load_config(_config_file(tmp_path))
    original = cfg.fingerprint()
    # This is a fingerprint audit, not a valid runtime configuration. B20's
    # dataclass ignores an added non-field; the STP field must change the hash.
    object.__setattr__(cfg.protection, "order_type", "STP LMT")
    assert cfg.fingerprint() != original


def test_B22_trigger_unfilled_timer_remains_configurable_and_fingerprinted(tmp_path):
    default = load_config(_config_file(tmp_path))
    changed = load_config(_config_file(
        tmp_path, "[protection]\ntrigger_unfilled_p0_seconds=73\n"))
    assert changed.protection.trigger_unfilled_p0_seconds == 73
    assert changed.fingerprint() != default.fingerprint()


@pytest.mark.parametrize("filename", [
    "paper.toml", "paper_delayed.toml", "paper_test.toml", "paper_g3n.toml", "live.toml",
])
def test_B22_every_shipped_example_explicitly_uses_STP_without_removed_key(filename):
    # Parse committed templates only. Do not load a Live approval or resolve
    # an operator state directory or credentials.
    raw = tomllib.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
    protection = raw["protection"]
    assert protection.get("order_type") == "STP"
    assert "limit_offset_pct" not in protection
    assert type(protection["trigger_unfilled_p0_seconds"]) is int
    assert protection["trigger_unfilled_p0_seconds"] > 0


class _LocalIB:
    """The full wire tuple is represented without invoking an IB connection."""

    def __init__(self):
        self.placed = []
        self.cancelled = []
        self.snapshot = []
        self.query_count = 0

    def connect(self, *args, **kwargs):
        raise AssertionError("an offline adapter test must never connect")

    def isConnected(self):
        return True

    def placeOrder(self, contract, order):
        order.orderId = order.orderId or 77
        order.permId = order.permId or 50077
        order.clientId = 131
        self.placed.append((copy.deepcopy(contract), copy.deepcopy(order)))
        trade = SimpleNamespace(
            contract=contract, order=order,
            orderStatus=SimpleNamespace(
                orderId=order.orderId, permId=order.permId, clientId=order.clientId,
                status="PreSubmitted", filled=0.0, remaining=float(order.totalQuantity),
            ),
            log=[], fills=[], advancedError="",
        )
        self.snapshot = [trade]
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
    broker = IbkrBroker(_config(tmp_path))
    broker.ib = _LocalIB()
    broker._contracts["AAPL"] = SimpleNamespace(
        symbol="AAPL", conId=265598, secType="STK", currency="USD", exchange="SMART")
    return broker


def _native_trade(*, limit_price=None, **changes):
    order = SimpleNamespace(
        orderRef="v6l-protect-b22", orderId=77, permId=50077,
        account="DU0000000", clientId=131, action="SELL", orderType="STP",
        tif="GTC", outsideRth=False, totalQuantity=3,
        auxPrice=335.77, lmtPrice=limit_price,
    )
    status = SimpleNamespace(
        orderId=77, permId=50077, clientId=131, status="PreSubmitted",
        filled=0.0, remaining=3.0,
    )
    contract = SimpleNamespace(
        symbol="AAPL", conId=265598, secType="STK", currency="USD", exchange="SMART")
    for name, value in changes.items():
        target, field = name.split("__", 1)
        setattr({"order": order, "status": status, "contract": contract}[target], field, value)
    return SimpleNamespace(
        order=order, orderStatus=status, contract=contract, log=[], fills=[], advancedError="")


def _same_order_request(**changes):
    return _protect_request(
        broker_order_id=77, perm_id=50077, con_id=265598, client_id=131,
        account="DU0000000", allow_lookup=False, **changes)


def _assert_stop_wire(order, *, quantity, stop):
    assert (order.action, order.orderType, order.totalQuantity) == ("SELL", "STP", quantity)
    assert (order.tif, order.outsideRth) == ("GTC", False)
    assert order.account == "DU0000000"
    assert order.orderRef == "v6l-protect-b22"
    assert order.auxPrice == pytest.approx(stop)
    assert "lmtPrice" not in order.nonDefaults()
    assert order.lmtPrice == UNSET_PRICE
    assert order.orderType != "MKT"


def test_B22_adapter_submits_sell_STP_GTC_RTH_with_no_limit_price(adapter):
    ack = _submit_stop(adapter, _protect_request())
    [(contract, order)] = adapter.ib.placed
    _assert_stop_wire(order, quantity=3, stop=335.77)
    assert contract.conId == 265598
    assert (ack.broker_order_id, ack.perm_id) == (77, 50077)
    assert adapter.ib.cancelled == []
    assert order.totalQuantity <= 3  # S=3, T=X=0, P=3


def test_B22_adapter_has_no_old_stop_limit_submission_entrypoint(adapter):
    assert callable(getattr(adapter, "submit_stop", None))
    assert not callable(getattr(adapter, "submit_stop_limit", None))


@pytest.mark.parametrize("limit", [0.0, 325.69, float("nan")])
def test_B22_adapter_refuses_any_non_None_protection_limit_without_writing(adapter, limit):
    with pytest.raises(BrokerError):
        _submit_stop(adapter, _protect_request(limit_price=limit))
    assert adapter.ib.placed == adapter.ib.cancelled == []


def test_B22_adapter_refuses_old_request_type_without_writing(adapter):
    request = _protect_request()
    object.__setattr__(request, "order_type", "STP LMT")
    with pytest.raises(BrokerError):
        _submit_stop(adapter, request)
    assert adapter.ib.placed == adapter.ib.cancelled == []


@pytest.mark.parametrize("trigger", [0.0, float("nan"), float("inf")])
def test_B22_invalid_stop_price_remains_fail_closed(adapter, trigger):
    with pytest.raises(BrokerError):
        _submit_stop(adapter, _protect_request(trigger_price=trigger))
    assert adapter.ib.placed == []


@pytest.mark.parametrize("purpose,side", [
    (Purpose.ENTRY, Side.BUY), (Purpose.TP1, Side.SELL),
    (Purpose.TP2, Side.SELL), (Purpose.TP3, Side.SELL),
    (Purpose.STOP, Side.SELL), (Purpose.SHUTDOWN, Side.SELL),
])
def test_B22_every_nonprotection_order_is_still_LMT_with_zero_MKT_calls(adapter, purpose, side):
    request = OrderRequest("v6l-normal-b22", "AAPL", side, 1, 113.47, purpose, "pos-b22")
    adapter.submit_limit(request)
    [(_, order)] = adapter.ib.placed
    assert (order.orderType, order.tif, order.outsideRth) == ("LMT", "DAY", False)
    assert (order.action, order.lmtPrice, order.totalQuantity) == (str(side), 113.47, 1)
    assert sum(wire.orderType == "MKT" for _, wire in adapter.ib.placed) == 0


@pytest.mark.parametrize("quantity,held", [(2, 3), (5, 5)])
def test_B22_quantity_modification_keeps_STP_and_exact_persisted_identity(adapter, quantity, held):
    trade = _native_trade(limit_price=UNSET_PRICE)
    adapter._trades["v6l-protect-b22"] = trade
    ack = adapter.modify_protection(_same_order_request(quantity=quantity))
    [(_, order)] = adapter.ib.placed
    _assert_stop_wire(order, quantity=quantity, stop=335.77)
    assert (order.orderId, order.permId, order.clientId) == (77, 50077, 131)
    assert (ack.broker_order_id, ack.perm_id) == (77, 50077)
    assert order.totalQuantity <= held  # same-id replacement, T=X=0
    assert adapter.ib.cancelled == []


def test_B22_stop_modification_changes_auxPrice_without_adding_a_limit(adapter):
    adapter._trades["v6l-protect-b22"] = _native_trade()
    adapter.modify_protection(_same_order_request(trigger_price=336.12))
    [(_, order)] = adapter.ib.placed
    _assert_stop_wire(order, quantity=3, stop=336.12)
    assert (order.orderId, order.permId, order.clientId) == (77, 50077, 131)
    assert order.totalQuantity <= 3


@pytest.mark.parametrize("changes", [
    {"order__account": ""}, {"order__account": "DU0000000"},
    {"order__clientId": 999}, {"contract__conId": 999},
    {"contract__symbol": "TSLA"}, {"order__orderId": 999},
    {"order__permId": 999}, {"order__orderType": "STP LMT"},
    {"order__orderRef": "v6l-different-protection"}, {"order__action": "BUY"},
    {"order__tif": "DAY"}, {"order__outsideRth": True},
    {"contract__currency": "EUR"}, {"contract__secType": "OPT"},
])
def test_B22_modification_cannot_upgrade_old_type_or_cross_broker_identity(adapter, changes):
    adapter._trades["v6l-protect-b22"] = _native_trade(**changes)
    with pytest.raises(BrokerError):
        adapter.modify_protection(_same_order_request())
    assert adapter.ib.placed == adapter.ib.cancelled == []


@pytest.mark.parametrize("limit_price", [None, UNSET_PRICE])
def test_B22_native_status_preserves_full_STP_identity_without_a_limit(adapter, limit_price):
    trade = _native_trade(limit_price=limit_price)
    adapter._on_status(trade)
    events = adapter.drain_events()
    [event] = [item for item in events if isinstance(item, StatusEvent)]
    [observation] = [item for item in events if isinstance(item, PriceObservationEvent)]
    identity = event.order_identity
    assert identity is not None
    assert (identity.account, identity.client_id, identity.con_id) == ("DU0000000", 131, 265598)
    assert (identity.broker_order_id, identity.perm_id, identity.intent_id) == (
        77, 50077, "v6l-protect-b22")
    assert (identity.order_type, identity.tif, identity.outside_rth) == ("STP", "GTC", False)
    assert identity.aux_price == 335.77
    assert identity.total_quantity == 3
    # The new diagnostic event is separate from the unchanged status authority.
    assert observation.source == "orderStatus" and observation.identity == identity
    assert (observation.identity.account, observation.identity.client_id, observation.identity.con_id,
            observation.identity.broker_order_id, observation.identity.perm_id, observation.identity.intent_id) == (
                "DU0000000", 131, 265598, 77, 50077, "v6l-protect-b22")
    assert observation.identity.order_type == "STP" and observation.identity.aux_price == 335.77
    assert observation.identity.limit_price is None and observation.mkt_cap_price is None


@pytest.mark.parametrize("limit_price", [None, UNSET_PRICE])
def test_B22_fresh_STP_open_order_snapshot_does_not_need_a_limit_price(adapter, limit_price):
    adapter.ib.snapshot = [_native_trade(limit_price=limit_price)]
    [order] = adapter.open_orders()
    assert (order.account, order.con_id, order.client_id) == ("DU0000000", 265598, 131)
    assert (order.order_type, order.tif, order.aux_price) == ("STP", "GTC", 335.77)
    assert order.remaining == 3


def test_B22_STP_snapshot_without_account_is_incomplete_not_assumed_ours(adapter):
    adapter.ib.snapshot = [_native_trade(order__account="")]
    with pytest.raises(BrokerError, match="account"):
        adapter.open_orders()
    assert adapter.ib.placed == adapter.ib.cancelled == []


def test_B22_generic_cancel_still_refuses_STP_without_exact_identity(adapter):
    adapter._trades["v6l-protect-b22"] = _native_trade()
    with pytest.raises(BrokerError):
        adapter.cancel("v6l-protect-b22", allow_lookup=False)
    assert adapter.ib.placed == adapter.ib.cancelled == []


def test_B22_exact_STP_cancel_uses_existing_order_without_replacement(adapter):
    adapter._trades["v6l-protect-b22"] = _native_trade()
    adapter.cancel(
        "v6l-protect-b22", expected_order_id=77, expected_perm_id=50077,
        expected_con_id=265598, allow_lookup=False)
    assert adapter.ib.placed == []
    [cancelled] = adapter.ib.cancelled
    assert (cancelled.orderType, cancelled.orderId, cancelled.permId) == ("STP", 77, 50077)


def test_B22_read_only_boundary_blocks_submit_modify_and_cancel_with_zero_writes(adapter):
    adapter.read_only = True
    adapter._trades["v6l-protect-b22"] = _native_trade()
    calls = [
        lambda: _submit_stop(adapter, _protect_request()),
        lambda: adapter.modify_protection(_same_order_request()),
        lambda: adapter.cancel(
            "v6l-protect-b22", expected_order_id=77, expected_perm_id=50077,
            expected_con_id=265598, allow_lookup=False),
        lambda: adapter.submit_limit(
            OrderRequest("v6l-entry-b22", "AAPL", Side.BUY, 1, 113.47, Purpose.ENTRY)),
    ]
    for call in calls:
        with pytest.raises(BrokerError, match="read-only"):
            call()
    assert adapter.ib.placed == adapter.ib.cancelled == []


def _pending_rig(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.broker.protection_auto_status = False
    name = _stop_method_name(rig.broker)
    original = getattr(rig.broker, name)

    def local_ack(request):
        ack = original(request)
        return BrokerAck(ack.broker_order_id, None)

    monkeypatch.setattr(rig.broker, name, local_ack)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = next(request for request in rig.broker.sent if request.purpose is Purpose.ENTRY)
    fill = rig.broker.fill(entry.intent_id, qty=3, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    [position] = rig.store.open_positions()
    row = rig.store.protection(position["position_id"])
    assert row is not None and row["state"] == "SUBMIT_PENDING"
    assert row["perm_id"] is None
    _share_invariant(rig, position["position_id"])
    return rig, position["position_id"]


def _rig_native(rig, position_id, *, limit_price=None, raw="PreSubmitted", message="", code=0, **changes):
    row = rig.store.protection(position_id)
    local = rig.broker.orders[row["intent_id"]]
    trade = _native_trade(
        limit_price=limit_price, order__orderRef=row["intent_id"],
        order__orderId=local.broker_order_id, status__orderId=local.broker_order_id,
        order__permId=local.broker_order_id + 50000,
        status__permId=local.broker_order_id + 50000,
        contract__conId=row["con_id"], order__auxPrice=row["trigger_price"],
        status__status=raw,
    )
    for name, value in changes.items():
        target, field = name.split("__", 1)
        setattr({"order": trade.order, "status": trade.orderStatus, "contract": trade.contract}[target], field, value)
    trade.log = [SimpleNamespace(
        time=rig.clock.now(), status=raw, message=message, errorCode=code)]
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = _LocalIB()
    adapter.ib.snapshot = [trade]
    adapter._trades[row["intent_id"]] = trade
    return adapter, trade


def _deliver_native(rig, adapter, trade):
    adapter._on_status(trade)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)


def _share_invariant(rig, position_id):
    row = rig.store.protection(position_id)
    held = sum(position.quantity for position in rig.broker.positions_fresh()
               if position.symbol == row["symbol"])
    protection = max(int(row["confirmed_remaining"]), int(row["pending_max_remaining"]))
    tp = external = 0
    for order in rig.broker.open_orders():
        if order.symbol != row["symbol"] or order.side != "SELL" or order.intent_id == row["intent_id"]:
            continue
        if order.ours:
            tp += order.remaining
        else:
            external += order.remaining
    assert protection + tp + external <= held
    assert not [request for request in rig.broker.sent if request.purpose is Purpose.STOP]
    assert not [order for order in rig.broker.open_orders() if order.order_type == "MKT"]
    assert all(order.order_type == "LMT" for order in rig.broker.open_orders()
               if order.intent_id != row["intent_id"])


@pytest.mark.parametrize("limit_price", [None, UNSET_PRICE])
def test_B22_full_native_STP_can_adopt_perm_without_limit_but_not_claim_fresh_confirmation(
        tmp_path, monkeypatch, limit_price):
    rig, position_id = _pending_rig(tmp_path, monkeypatch)
    adapter, trade = _rig_native(rig, position_id, limit_price=limit_price)
    _deliver_native(rig, adapter, trade)
    row = rig.store.protection(position_id)
    assert row["perm_id"] == trade.order.permId
    assert rig.store.intent(row["intent_id"])["perm_id"] == trade.order.permId
    assert rig.store.intent(row["intent_id"])["hold_reason"] in ("", None)
    assert row["confirmed_at"] is None
    assert row["state"] != "WORKING"
    assert len(rig.broker.sent_protections) == 1
    _share_invariant(rig, position_id)


@pytest.mark.parametrize("changes", [
    {"order__account": ""}, {"order__account": "DU0000000"},
    {"order__clientId": 999}, {"contract__conId": 999},
    {"contract__symbol": "TSLA"}, {"contract__currency": "EUR"},
    {"contract__secType": "OPT"}, {"order__orderId": 999},
    {"status__orderId": 999}, {"status__clientId": 999},
    {"status__permId": 0}, {"order__permId": 0},
    {"order__action": "BUY"}, {"order__orderType": "STP LMT"},
    {"order__tif": "DAY"}, {"order__outsideRth": True},
    {"order__auxPrice": 1.0}, {"order__totalQuantity": 4},
    {"status__filled": 0.5, "status__remaining": 2.5},
])
def test_B22_empty_limit_does_not_weaken_any_native_identity_or_price_check(
        tmp_path, monkeypatch, changes):
    rig, position_id = _pending_rig(tmp_path, monkeypatch)
    adapter, trade = _rig_native(rig, position_id, **changes)
    _deliver_native(rig, adapter, trade)
    row = rig.store.protection(position_id)
    assert row["perm_id"] is None
    assert row["confirmed_at"] is None
    assert row["state"] != "WORKING"
    assert len(rig.broker.sent_protections) == 1
    assert rig.broker.modified_protections == []
    _share_invariant(rig, position_id)


def test_B22_explicit_STP_refusal_freezes_entries_and_never_rehangs(tmp_path, monkeypatch):
    rig, position_id = _pending_rig(tmp_path, monkeypatch)
    adapter, trade = _rig_native(
        rig, position_id, raw="Cancelled", message=REJECT_REASON, code=202)
    row = rig.store.protection(position_id)
    rig.broker.orders[row["intent_id"]].status = "Cancelled"
    _deliver_native(rig, adapter, trade)
    rejected = rig.store.protection(position_id)
    assert rejected["state"] == "REJECTED"
    assert rig.store.control("frozen:AAPL")
    assert rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    assert any("P0" in title and "券商已取消保护单" in title for title, _ in rig.channel.messages)
    _share_invariant(rig, position_id)
    writes = (len(rig.broker.sent_protections), len(rig.broker.modified_protections), len(rig.broker.cancels))
    for seconds in (20, 61, 1800):
        rig.advance(seconds=seconds)
        rig.engine._protection_progress(rig.clock.now())
        rig.engine._hard_protection_deadlines(rig.clock.now())
        rig.engine.process_protection_priority()
        assert rig.store.protection(position_id)["state"] == "REJECTED"
        assert (len(rig.broker.sent_protections), len(rig.broker.modified_protections),
                len(rig.broker.cancels)) == writes
        _share_invariant(rig, position_id)


def test_B22_dry_run_scans_and_exercises_protection_boundary_with_zero_broker_writes(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, dry_run=True, protection=ProtectionConfig(enabled=True))
    calls = []

    def forbidden_write(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("dry run invoked a broker write")

    for name in ("submit_limit", _stop_method_name(rig.broker), "modify_protection", "cancel"):
        monkeypatch.setattr(rig.broker, name, forbidden_write)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    rig.engine._protection_submit_first(
        rig.clock.now(),
        {"symbol": "AAPL", "intent_id": "v6l-protect-dry-b22",
         "position_id": "dry-pos-b22", "trigger_price": 335.77, "limit_price": None},
        3,
    )
    assert calls == []
    assert rig.broker.sent == rig.broker.sent_protections == []
    assert rig.broker.modified_protections == rig.broker.cancels == []


def test_B22_protection_request_default_contract_has_STP_and_no_limit():
    parameters = inspect.signature(ProtectionRequest).parameters
    assert parameters["limit_price"].default is None
    assert "order_type" in parameters
    assert parameters["order_type"].default == "STP"
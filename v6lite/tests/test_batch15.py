"""L7 broker-side protection contract; all broker data is synthetic.

These tests intentionally exercise the public broker/engine boundary rather
than a live Gateway. The round-14 baseline must fail before implementation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from types import SimpleNamespace

import pytest

from v6lite.broker.fake import FakeBroker
from v6lite.config import ConfigError, load_config
from v6lite.models import Side
from v6lite.tests.helpers import Rig, et


def _protected_rig(tmp_path, **overrides):
    from v6lite.config import ProtectionConfig

    return Rig(tmp_path, protection=ProtectionConfig(), **overrides)


def _entry_and_fill(rig: Rig, *, quantity: int | None = None):
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    rig.broker.fill(buy.intent_id, qty=quantity)
    rig.advance(seconds=1)
    rig.tick()
    return buy


def _protection_orders(rig: Rig):
    return rig.broker.sent_protections


def test_L7_config_defaults_fingerprint_and_live_cannot_disable(tmp_path, monkeypatch):
    from v6lite.config import ProtectionConfig

    path = tmp_path / "default.toml"
    path.write_text('mode="paper"\n[ibkr]\naccount="DU0000000"\nport=4002\n'
                    '[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.protection == ProtectionConfig()
    assert cfg.protection.enabled is True
    assert cfg.protection.order_type == "STP"
    assert cfg.protection.gap_a_p1_seconds == 5
    assert cfg.protection.gap_a_p0_seconds == 15
    assert cfg.protection.gap_b_cancel_seconds == 10
    assert cfg.protection.gap_b_p0_seconds == 30
    # Fingerprinting must bind the order type even before runtime validation.
    changed = replace(cfg, protection=replace(cfg.protection, order_type="STP LMT"))
    assert changed.fingerprint() != cfg.fingerprint()

    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "synthetic-test-placeholder")
    path = tmp_path / "invalid-live.toml"
    path.write_text('mode="live"\n[ibkr]\naccount="U0000000"\nport=4001\n'
                    '[universe]\ntradable=["AAPL"]\nscan=["AAPL"]\n'
                    '[protection]\nenabled=false\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="(?i)protect|live|enabled"):
        load_config(path)


def test_L7_entry_fill_immediately_submits_gtc_stop_with_broker_identity(tmp_path):
    rig = _protected_rig(tmp_path)
    buy = _entry_and_fill(rig, quantity=1)
    protect = _protection_orders(rig)
    assert len(protect) == 1
    req = protect[0]
    assert req.symbol == buy.symbol and req.quantity == 1
    assert req.intent_id.startswith("v6l-")
    position = rig.store.position(buy.position_id)
    expected_stop = Decimal(str(position["stop"])).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    assert req.order_type == "STP" and req.limit_price is None
    assert Decimal(str(req.trigger_price)) == expected_stop
    assert rig.broker.orders[req.intent_id].broker_order_id is not None


def test_L7_partial_entry_growth_requires_three_evidence_and_same_identity(tmp_path):
    rig = _protected_rig(tmp_path)
    buy = _entry_and_fill(rig, quantity=1)
    protect = _protection_orders(rig)
    assert len(protect) == 1 and protect[0].quantity == 1
    identity = protect[0].intent_id
    rig.broker.fill(buy.intent_id, qty=1)
    rig.advance(seconds=1)
    rig.tick()
    assert len(_protection_orders(rig)) == 1
    assert rig.broker.orders[identity].req.quantity == 2


def test_L7_stop_trigger_does_not_place_a_second_sell_or_market_order(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    assert len(_protection_orders(rig)) == 1
    sent_before = len(rig.broker.sent)
    pos = rig.store.open_positions()[0]
    rig.px("AAPL", pos["stop"] - 1.0)
    rig.advance(seconds=31)
    rig.tick()
    assert len(rig.broker.sent) == sent_before
    assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]


def test_L7_shutdown_preserves_working_gtc_protection(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    rig.engine.shutdown(grace_seconds=0)
    assert req.intent_id not in rig.broker.cancels
    assert req.intent_id in rig.broker.orders


@pytest.mark.parametrize("cause", ["freeze", "kill", "daily_loss", "exit_timeout"])
def test_L7_safety_controls_do_not_cancel_gtc_protection(tmp_path, cause):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    if cause == "freeze":
        rig.engine._freeze(rig.clock.now(), "AAPL", "synthetic mismatch")
        rig.engine._run_queue()
    elif cause == "kill":
        rig.store.kill(rig.clock.now(), "synthetic operator stop")
        rig.tick()
    elif cause == "daily_loss":
        rig.store.latch_daily_stop(rig.clock.now(), "2026-10-06", "synthetic loss")
        rig.tick()
    else:
        rig.advance(seconds=61)
        rig.engine._resolve_intents(rig.clock.now())
    assert req.intent_id not in rig.broker.cancels


def test_L7_dry_run_never_calls_any_broker_write_even_for_position(tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path, dry_run=True)
    calls = []
    for name in ("submit_limit", "submit_stop", "modify_protection", "cancel"):
        if hasattr(rig.broker, name):
            monkeypatch.setattr(rig.broker, name, lambda *a, _name=name, **k: calls.append(_name))
    rig.start()
    rig.broker.external_positions["AAPL"] = (3, 100)
    rig.at(et(10, 0, 30))
    rig.tick()
    rig.advance(seconds=31)
    rig.tick()
    rig.engine.shutdown(grace_seconds=0)
    assert calls == []


def test_L7_broker_port_has_no_market_order_method():
    from v6lite.broker.base import BrokerPort

    assert not hasattr(BrokerPort, "submit_market")
    assert not hasattr(FakeBroker, "submit_market")


@pytest.mark.parametrize("trigger", [108.475, 108.47, 110.31, 100.0, 1.01])
def test_L7_protection_stop_uses_decimal_cent_floor(trigger):
    from v6lite.protection import protection_prices

    stop, limit = protection_prices(trigger)
    expected_stop = Decimal(str(trigger)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    assert Decimal(str(stop)) == expected_stop
    assert limit is None
    assert stop >= 1


@pytest.mark.parametrize("trigger", [0.99, 0.01, 0.0, -1.0])
def test_L7_subdollar_or_nonpositive_stop_is_rejected(trigger):
    from v6lite.protection import protection_prices

    with pytest.raises((ValueError, ConfigError), match="(?i)1|price|stock|trigger"):
        protection_prices(trigger)


def test_L7_protected_client_identity_is_131_formal_132_test(tmp_path):
    from v6lite.config import ProtectionConfig, TestConfig, validate
    from v6lite.tests.helpers import make_cfg

    formal = make_cfg(tmp_path, protection=ProtectionConfig())
    assert formal.client_id == 131
    with pytest.raises(ConfigError, match="(?i)client"):
        validate(replace(formal, client_id=132))
    test = make_cfg(tmp_path, protection=ProtectionConfig(), client_id=132,
                    state_dir=str(tmp_path / "paper-test"), test=TestConfig(enabled=True))
    assert test.client_id == 132
    with pytest.raises(ConfigError, match="(?i)client"):
        validate(replace(test, client_id=131))


def test_L7_ibkr_adapter_submits_server_gtc_stop_and_readonly_refuses_writes(tmp_path):
    from v6lite.broker.base import BrokerError
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.config import ProtectionConfig
    from v6lite.models import ProtectionRequest
    from v6lite.tests.helpers import make_cfg
    from v6lite.tests.test_ibkr_adapter import FakeIB

    cfg = make_cfg(tmp_path, protection=ProtectionConfig())
    broker = IbkrBroker(cfg)
    broker.ib = FakeIB()
    broker.connect()
    broker.qualify("AAPL")
    req = ProtectionRequest("v6l-protect-a", "pos-a", "AAPL", 3, 108.47, None)
    broker.submit_stop(req)
    assert len(broker.ib.placed) == 1
    contract, order = broker.ib.placed[0]
    assert contract.symbol == "AAPL"
    assert (order.orderType, order.action, order.totalQuantity) == ("STP", "SELL", 3)
    assert order.auxPrice == 108.47
    assert "lmtPrice" not in order.nonDefaults()
    assert order.lmtPrice >= 1e308  # ib_insync unset price, never a sell floor
    assert (order.tif, order.outsideRth, order.orderRef, order.account) == (
        "GTC", False, "v6l-protect-a", cfg.account)
    assert broker.ib.connect_kwargs["clientId"] == 131

    read_only = IbkrBroker(cfg, read_only=True)
    read_only.ib = FakeIB()
    read_only.connect()
    read_only.qualify("AAPL")
    for write in (lambda: read_only.submit_stop(req),
                  lambda: read_only.modify_protection(req),
                  lambda: read_only.cancel(req.intent_id)):
        with pytest.raises(BrokerError, match="(?i)read.only"):
            write()
    assert read_only.ib.placed == []


@pytest.mark.parametrize("missing", ["account", "con_id"])
def test_L7_open_order_missing_identity_is_never_fabricated_as_confirmed(tmp_path, monkeypatch, missing):
    from v6lite.broker.base import BrokerError
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.config import ProtectionConfig
    from v6lite.tests.helpers import make_cfg
    from v6lite.tests.test_ibkr_adapter import FakeIB

    cfg = make_cfg(tmp_path, protection=ProtectionConfig())
    broker = IbkrBroker(cfg)
    broker.ib = FakeIB()
    contract = SimpleNamespace(symbol="AAPL", secType="STK", conId=0 if missing == "con_id" else 265598)
    order = SimpleNamespace(orderRef="v6l-protect-identity", account="" if missing == "account" else cfg.account,
                            orderId=77, permId=50077, action="SELL", totalQuantity=3,
                            orderType="STP", tif="GTC", auxPrice=97.5, lmtPrice=None,
                            clientId=131, outsideRth=False)
    status = SimpleNamespace(status="Submitted", filled=0, remaining=3)
    trade = SimpleNamespace(order=order, orderStatus=status, contract=contract)
    monkeypatch.setattr(broker, "_clean_request", lambda *a, **k: [trade])
    monkeypatch.setattr(broker.ib, "reqAllOpenOrders", lambda: [trade], raising=False)
    try:
        rows = broker.open_orders()
    except BrokerError:
        return  # explicit incomplete snapshot is a valid fail-closed answer
    if rows:
        row = rows[0]
        if missing == "account":
            assert row.account != cfg.account
        else:
            assert row.con_id in (None, 0)


def test_L7_entry_callback_submits_protection_before_next_tick(tmp_path):
    rig = _protected_rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    rig.broker.fill(buy.intent_id, qty=1)
    assert rig.broker.sent_protections == []
    # This drains the execDetails event only; no scan/history/reconcile/tick.
    rig.engine._drain(rig.clock.now())
    assert len(rig.broker.sent_protections) == 1
    assert rig.broker.sent_protections[0].quantity == 1


def test_L7_local_submit_ack_does_not_count_as_server_confirmation(tmp_path):
    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    rig.broker.fill(buy.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    req = rig.broker.sent_protections[0]
    pos = rig.store.open_positions()[0]
    before = rig.store.protection(pos["position_id"])
    assert before["confirmed_remaining"] in (None, 0)
    assert before["pending_max_remaining"] >= 1
    rig.broker.status(req.intent_id, "Submitted")
    rig.engine._drain(rig.clock.now())
    after = rig.store.protection(pos["position_id"])
    assert after["confirmed_remaining"] == 1


def test_L7_unconfirmed_entry_gap_pages_at_five_and_fifteen_seconds(tmp_path):
    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    _entry_and_fill(rig, quantity=1)
    assert len(_protection_orders(rig)) == 1
    rig.advance(seconds=4)
    rig.tick()
    assert "未保护" not in rig.messages() or "P1" not in rig.messages()
    rig.advance(seconds=1)
    rig.tick()
    assert "P1" in rig.messages() and "AAPL" in rig.messages()
    rig.advance(seconds=10)
    rig.tick()
    assert "P0" in rig.messages() and "1" in rig.messages()
    ref = _protection_orders(rig)[0].intent_id
    pages = [(subject, body) for subject, body in rig.channel.messages
             if "保护" in subject and ref in body]
    assert any("P1" in subject and "AAPL" in body and "1）" in body and "2）" in body
               for subject, body in pages)
    assert any("P0" in subject and "AAPL" in body and "1" in body and "1）" in body
               for subject, body in pages)


def test_L7_take_profit_waits_for_confirmed_protection_decrease(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    protect = _protection_orders(rig)[0]
    held = rig.store.open_positions()[0]["qty_open"]
    tp_target = max(1, held // 3)
    rig.broker.protection_auto_status = False
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    assert rig.broker.modified_protections
    assert rig.broker.modified_protections[-1].quantity == held - tp_target
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    rig.broker.confirm_protection_modify(protect.intent_id)
    rig.engine._drain(rig.clock.now())
    assert len([req for req in rig.broker.sent if req.side is Side.SELL]) == 1


def test_L7_take_profit_not_filled_by_ten_seconds_is_cancelled_then_reprotected(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    held = rig.store.open_positions()[0]["qty_open"]
    tp_target = max(1, held // 3)
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    protect = _protection_orders(rig)[0]
    assert rig.broker.orders[protect.intent_id].req.quantity == held - tp_target
    rig.advance(seconds=9)
    rig.tick()
    assert tp.intent_id not in rig.broker.cancels
    rig.advance(seconds=1)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    assert rig.broker.orders[protect.intent_id].req.quantity == held - tp_target  # pending cancel
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    assert rig.broker.orders[protect.intent_id].req.quantity == held


def test_L7_unrestored_take_profit_gap_pages_at_thirty_seconds(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    rig.advance(seconds=20)
    rig.tick()
    assert "P0" in rig.messages() and "AAPL" in rig.messages()
    protect = _protection_orders(rig)[0]
    assert any("P0" in subject and "止盈" in subject and "AAPL" in body
               and tp.intent_id in body and protect.intent_id in body
               and "1）" in body and "2）" in body
               for subject, body in rig.channel.messages)


def test_L7_gross_sell_invariant_through_confirm_pending_partial_and_cancel(tmp_path):
    """The broker's maximum possible sells are additive, never OCA-netted."""
    from v6lite.models import OrderRequest, ProtectionRequest, Purpose

    broker = FakeBroker()
    broker.connect()
    broker.protection_auto_status = False
    broker.external_positions["AAPL"] = (5, 100.0)
    protect = ProtectionRequest("v6l-protect-5", "pos-5", "AAPL", 5, 97.50, None)
    broker.submit_stop(protect)
    cases = []

    def check(label, *, pending_tp=0, external=0):
        p = int(next(p.quantity for p in broker.positions_fresh() if p.symbol == "AAPL"))
        s_order = broker.orders[protect.intent_id]
        current = s_order.remaining if s_order.status not in {"Cancelled", "Inactive", "Filled"} else 0
        pending = broker.protection_pending_modifications.get(protect.intent_id)
        s = max(current, pending.quantity - s_order.filled if pending else 0)
        tp = sum(max(0, o.remaining) for o in broker.orders.values()
                 if isinstance(o.req, OrderRequest) and o.req.side is Side.SELL
                 and o.status not in {"Cancelled", "Inactive", "Filled"})
        cases.append((label, p, s, tp, external))
        assert s + tp + pending_tp + external <= p, (label, p, s, tp, external)

    check("A protection submit pending")
    broker.status(protect.intent_id, "Submitted")
    check("full protection confirmed")
    broker.modify_protection(replace(protect, quantity=3))
    check("decrease pending counts old max")
    broker.confirm_protection_modify(protect.intent_id)
    check("decrease confirmed before TP")
    tp = OrderRequest("v6l-tp-5", "AAPL", Side.SELL, 2, 105.0, Purpose.TP1, "pos-5")
    broker.submit_limit(tp)
    check("TP pending/UNKNOWN reserves full")
    broker.fill(tp.intent_id, qty=1)
    check("partial TP fill decreases P and T together")
    broker.cancel(tp.intent_id)
    check("TP cancel pending keeps remaining reserve")
    broker.confirm_cancel(tp.intent_id)
    check("TP cancel confirmed")
    broker.modify_protection(replace(protect, quantity=4))
    check("restore pending counts requested max")
    broker.confirm_protection_modify(protect.intent_id)
    check("restore confirmed")
    assert len(cases) == 10


def test_L7_unknown_modify_must_not_release_take_profit_capacity(tmp_path):
    from v6lite.broker.base import BrokerError
    from v6lite.models import ProtectionRequest

    broker = FakeBroker()
    broker.connect()
    broker.external_positions["AAPL"] = (3, 100.0)
    req = ProtectionRequest("v6l-unknown-protect", "pos-3", "AAPL", 3, 97.5, None)
    broker.submit_stop(req)
    broker.protection_auto_status = False
    broker.fail_next_modify = BrokerError("ack lost", maybe_sent=True)
    with pytest.raises(BrokerError):
        broker.modify_protection(replace(req, quantity=2))
    # A lost modification acknowledgement is not proof the broker reduced S.
    assert broker.orders[req.intent_id].remaining == 3
    assert broker.orders[req.intent_id].status in {"Submitted", "PreSubmitted"}


def test_L7_query_timeout_blocks_protection_increase_after_partial_buy(tmp_path):
    rig = _protected_rig(tmp_path)
    buy = _entry_and_fill(rig, quantity=1)
    req = _protection_orders(rig)[0]
    rig.broker.fail_open_orders = True
    rig.broker.fill(buy.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    assert rig.broker.orders[req.intent_id].req.quantity == 1
    assert not rig.broker.modified_protections


def test_L7_missing_execution_evidence_blocks_protection_increase(tmp_path):
    rig = _protected_rig(tmp_path)
    buy = _entry_and_fill(rig, quantity=1)
    req = _protection_orders(rig)[0]
    rig.broker.fail_executions = True
    rig.broker.fill(buy.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    assert rig.broker.orders[req.intent_id].req.quantity == 1
    assert not rig.broker.modified_protections


def test_L7_modify_refusal_never_sends_take_profit(tmp_path):
    from v6lite.broker.base import BrokerError

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    rig.broker.fail_next_modify = BrokerError("synthetic rejection")
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    assert "P0" in rig.messages()


def test_L7_external_sell_is_detected_frozen_and_reduced_after_detection(tmp_path):
    from v6lite.models import OrderRequest, Purpose

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    held = rig.store.open_positions()[0]["qty_open"]
    assert rig.broker.orders[req.intent_id].req.quantity == held
    external = OrderRequest("external-sell-1", "AAPL", Side.SELL, 1, 110.0, Purpose.TP1)
    rig.broker._register(external)
    # The external order instantly violates 20+1<=20. Client software can
    # only detect it afterward; it must not claim the violation never existed.
    assert rig.broker.orders[req.intent_id].remaining + rig.broker.orders[external.intent_id].remaining > held
    rig.engine._reconcile_due = True
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.control("frozen:AAPL") is not None
    assert "P0" in rig.messages()
    assert rig.broker.modified_protections
    assert rig.broker.modified_protections[-1].quantity <= held - 1
    assert any("P0" in subject and "外部" in subject and "AAPL" in body
               and req.intent_id in body and "1）" in body
               for subject, body in rig.channel.messages)


def test_L7_recovery_claims_existing_gtc_without_second_submission(tmp_path):
    from v6lite.engine import Engine

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    rig.engine.shutdown(grace_seconds=0)
    assert req.intent_id in rig.broker.orders
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "reports")
    resumed.startup()
    assert len(_protection_orders(rig)) == 1
    assert resumed.store.protection(rig.store.open_positions()[0]["position_id"])["intent_id"] == req.intent_id


def test_L7_partial_protection_fill_decreases_position_and_remaining_together(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    before = rig.store.open_positions()[0]["qty_open"]
    rig.broker.fill(req.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    after = rig.store.open_positions()[0]["qty_open"]
    assert after == before - 1
    assert rig.broker.orders[req.intent_id].remaining == after
    assert req.intent_id not in rig.broker.cancels


def test_L7_submitted_stop_at_trigger_pages_after_RTH_timeout_without_market_order(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    assert rig.broker.orders[req.intent_id].status == "Submitted"
    assert req.order_type == "STP" and req.limit_price is None
    rig.px("AAPL", req.trigger_price - 1.0)
    rig.advance(seconds=1)
    rig.tick()
    assert any("P1" in subject and "已到止损价，券商止损市价单应在成交" in subject
               and req.intent_id in body for subject, body in rig.channel.messages)
    assert not any("P0" in subject and "止损已触发但未成交" in subject
                   for subject, _ in rig.channel.messages)
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds - 1)
    rig.tick()
    assert not any("P0" in subject and "止损已触发但未成交" in subject
                   for subject, _ in rig.channel.messages)
    rig.advance(seconds=1)
    rig.tick()
    assert "P0" in rig.messages() and "AAPL" in rig.messages()
    assert any("P0" in subject and "止损已触发但未成交" in subject and req.intent_id in body
               and "AAPL" in body and "1）" in body and "停牌" in body
               for subject, body in rig.channel.messages)
    assert not [order for order in rig.broker.sent if getattr(order, "order_type", "") == "MKT"]


@pytest.mark.parametrize("phase", ["A_pending", "full_protection", "decrease_pending",
                                   "TP_unknown", "TP_partial", "TP_cancel_pending",
                                   "protection_partial"])
def test_L7_crash_restart_each_transition_reclaims_without_extra_sell(tmp_path, phase):
    from v6lite.broker.base import BrokerError
    from v6lite.engine import Engine

    rig = _protected_rig(tmp_path)
    if phase == "A_pending":
        rig.broker.protection_auto_status = False
    _entry_and_fill(rig)
    protect = _protection_orders(rig)[0]
    if phase in {"decrease_pending", "TP_unknown", "TP_partial", "TP_cancel_pending"}:
        if phase == "decrease_pending":
            rig.broker.protection_auto_status = False
        if phase == "TP_unknown":
            rig.broker.fail_next_submit = BrokerError("ack lost", maybe_sent=True)
        rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
        rig.advance(seconds=31)
        rig.tick()
        sells = [req for req in rig.broker.sent if req.side is Side.SELL]
        if phase == "TP_partial":
            assert sells
            rig.broker.fill(sells[0].intent_id, qty=1)
            rig.engine._drain(rig.clock.now())
        elif phase == "TP_cancel_pending":
            assert sells
            rig.advance(seconds=10)
            rig.tick()
            assert sells[0].intent_id in rig.broker.cancels
    elif phase == "protection_partial":
        rig.broker.fill(protect.intent_id, qty=1)
        rig.engine._drain(rig.clock.now())

    count_before = len(_protection_orders(rig))
    sells_before = len([req for req in rig.broker.sent if req.side is Side.SELL])
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "resumed-reports")
    resumed.startup()
    assert len(_protection_orders(rig)) == count_before
    assert len([req for req in rig.broker.sent if req.side is Side.SELL]) == sells_before
    assert protect.intent_id not in rig.broker.cancels

    p = int(next(pos.quantity for pos in rig.broker.positions_fresh() if pos.symbol == "AAPL"))
    protection = rig.broker.orders[protect.intent_id]
    s = protection.remaining if protection.status not in {"Cancelled", "Inactive", "Filled"} else 0
    pending = rig.broker.protection_pending_modifications.get(protect.intent_id)
    if pending:
        s = max(s, pending.quantity - protection.filled)
    t = sum(order.remaining for order in rig.broker.orders.values()
            if getattr(order.req, "side", None) is Side.SELL
            and order.status not in {"Cancelled", "Inactive", "Filled"})
    assert s + t <= p, (phase, p, s, t)


def test_L7_late_tp_fill_after_cancel_and_restore_only_decreases_protection(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    held = rig.store.open_positions()[0]["qty_open"]
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    protect = _protection_orders(rig)[0]
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    assert rig.broker.orders[protect.intent_id].req.quantity == held
    rig.broker.fill(tp.intent_id, qty=1)  # IBKR Cancelled -> execution race
    rig.engine._drain(rig.clock.now())
    assert rig.broker.positions_fresh()[0].quantity == held - 1
    assert rig.broker.modified_protections[-1].quantity <= held - 1
    assert "P0" in rig.messages()
    assert len([req for req in rig.broker.sent if req.side is Side.SELL]) == 1


def test_L7_company_action_gtc_cancel_pages_and_missing_evidence_prevents_replacement(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    rig.broker.fail_executions = True
    rig.broker.confirm_cancel(req.intent_id)
    rig.engine._drain(rig.clock.now())
    assert len(_protection_orders(rig)) == 1
    assert "P0" in rig.messages() and "AAPL" in rig.messages()
    assert not [order for order in rig.broker.sent if getattr(order, "order_type", "") == "MKT"]


def test_L7_next_day_without_complete_execution_history_cannot_recreate_protection(tmp_path):
    from v6lite.engine import Engine

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    # The old process dies before seeing the broker's GTC cancellation.
    # If it saw the cancellation with complete same-day evidence, a timely
    # replacement would be legitimate and this cross-day premise would fail.
    rig.broker.confirm_cancel(req.intent_id)
    rig.at(et(9, 50, day=(2026, 10, 7)))
    rig.broker.fail_executions = True
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "nextday-reports")
    resumed.startup()
    assert len(_protection_orders(rig)) == 1
    assert "P0" in rig.messages()


def test_L7_next_day_empty_today_executions_is_not_full_cross_day_evidence(tmp_path, monkeypatch):
    from v6lite.engine import Engine

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    rig.broker.confirm_cancel(req.intent_id)
    rig.at(et(9, 50, day=(2026, 10, 7)))
    monkeypatch.setattr(rig.broker, "executions_today", lambda: [])
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "crossday-reports")
    resumed.startup()
    assert len(_protection_orders(rig)) == 1
    assert "P0" in rig.messages()


def _one_share_position(tmp_path):
    rig = _protected_rig(tmp_path, max_order_notional_usd=130.0)
    buy = _entry_and_fill(rig)
    assert buy.quantity == 1
    assert rig.store.open_positions()[0]["qty_open"] == 1
    return rig


def _trigger_last_share_tp(rig):
    protect = _protection_orders(rig)[0]
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    assert protect.intent_id in rig.broker.cancels
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    return protect


def test_L7_last_share_tp_waits_for_confirmed_gtc_cancel(tmp_path):
    rig = _one_share_position(tmp_path)
    protect = _trigger_last_share_tp(rig)
    rig.advance(seconds=1)
    rig.tick()
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    assert rig.broker.orders[protect.intent_id].remaining == 1
    rig.broker.confirm_cancel(protect.intent_id)
    rig.engine._drain(rig.clock.now())
    sells = [req for req in rig.broker.sent if req.side is Side.SELL]
    assert len(sells) == 1 and sells[0].quantity == 1
    assert rig.broker.orders[protect.intent_id].status == "Cancelled"


def test_L7_last_share_tp_rejected_restores_new_gtc_after_three_evidence(tmp_path):
    from v6lite.broker.base import BrokerError

    rig = _one_share_position(tmp_path)
    protect = _trigger_last_share_tp(rig)
    rig.broker.fail_next_submit = BrokerError("synthetic definite rejection")
    rig.broker.confirm_cancel(protect.intent_id)
    rig.engine._drain(rig.clock.now())
    rig.tick()
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    protections = _protection_orders(rig)
    assert len(protections) == 2
    assert protections[1].intent_id != protect.intent_id
    assert rig.broker.orders[protections[1].intent_id].req.quantity == 1
    assert "P0" in rig.messages()  # rejection/temporary naked share is paged


def test_L7_last_share_tp_unfilled_ten_seconds_cancel_then_new_gtc(tmp_path):
    rig = _one_share_position(tmp_path)
    protect = _trigger_last_share_tp(rig)
    rig.broker.confirm_cancel(protect.intent_id)
    rig.engine._drain(rig.clock.now())
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    assert len(_protection_orders(rig)) == 1  # pending TP cancel still reserves share
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    rig.tick()
    protections = _protection_orders(rig)
    assert len(protections) == 2
    assert protections[1].intent_id != protect.intent_id
    assert rig.broker.orders[protections[1].intent_id].req.quantity == 1


def test_L7_last_share_late_tp_fill_after_restoration_cancels_new_gtc_immediately(tmp_path):
    rig = _one_share_position(tmp_path)
    old = _trigger_last_share_tp(rig)
    rig.broker.confirm_cancel(old.intent_id)
    rig.engine._drain(rig.clock.now())
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=10)
    rig.tick()
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    rig.tick()
    new = _protection_orders(rig)[-1]
    assert new.intent_id != old.intent_id
    rig.broker.fill(tp.intent_id, qty=1)  # a late fill after Cancelled
    rig.engine._drain(rig.clock.now())
    assert not rig.broker.positions_fresh()
    assert new.intent_id in rig.broker.cancels
    assert "P0" in rig.messages()


@pytest.mark.parametrize("phase", ["protection_cancel_pending", "cancel_confirmed_before_tp",
                                   "tp_submitted", "tp_cancel_pending",
                                   "tp_cancel_confirmed_before_restore"])
def test_L7_last_share_crash_boundaries_never_duplicate_sell(tmp_path, monkeypatch, phase):
    from v6lite.engine import Engine

    rig = _one_share_position(tmp_path)
    protect = _trigger_last_share_tp(rig)
    if phase == "cancel_confirmed_before_tp":
        def crash_before_tp(*_args):
            raise SystemExit("synthetic crash before final-share TP")
        monkeypatch.setattr(rig.engine, "_protection_send_tp", crash_before_tp)
        rig.broker.confirm_cancel(protect.intent_id)
        with pytest.raises(SystemExit):
            rig.engine._drain(rig.clock.now())
    elif phase != "protection_cancel_pending":
        rig.broker.confirm_cancel(protect.intent_id)
        rig.engine._drain(rig.clock.now())
        tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
        if phase in {"tp_cancel_pending", "tp_cancel_confirmed_before_restore"}:
            rig.advance(seconds=10)
            rig.tick()
            assert tp.intent_id in rig.broker.cancels
        if phase == "tp_cancel_confirmed_before_restore":
            monkeypatch.setattr(rig.engine, "_protection_progress",
                                lambda *_args: (_ for _ in ()).throw(SystemExit("synthetic crash before restore")))
            rig.broker.confirm_cancel(tp.intent_id)
            with pytest.raises(SystemExit):
                rig.engine._drain(rig.clock.now())

    before_sells = len([req for req in rig.broker.sent if req.side is Side.SELL])
    before_protect = len(_protection_orders(rig))
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "last-share-restart")
    resumed.startup()
    sells = [req for req in rig.broker.sent if req.side is Side.SELL]
    assert len(sells) <= max(1, before_sells)
    assert len(_protection_orders(rig)) <= before_protect + 1
    working_sell_qty = sum(order.remaining for order in rig.broker.orders.values()
                           if (getattr(order.req, "side", None) is Side.SELL or
                               order.req in _protection_orders(rig))
                           and order.status not in {"Cancelled", "Inactive", "Filled"})
    assert working_sell_qty <= rig.broker.positions_fresh()[0].quantity


def test_L7_ibkr_status_callback_wakes_priority_without_nested_queries(tmp_path, monkeypatch):
    """The real adapter wakes the engine on orderStatus, before a 1s loop wait.

    Confirmation queries run immediately after callback unwinds, not nested
    inside ib_insync's decoder callback.
    """
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.tests.test_ibkr_adapter import FakeIB

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    protect = _protection_orders(rig)[0]
    rig.broker.protection_auto_status = False
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    assert rig.broker.modified_protections[-1].quantity < protect.quantity
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    rig.broker.confirm_protection_modify(protect.intent_id)
    rig.broker.drain_events()  # the synthetic IBKR callback supplies this status instead

    in_callback = {"active": False}
    for name in ("open_orders", "positions_fresh", "executions_today"):
        old = getattr(rig.broker, name)

        def guarded(*args, _old=old, **kwargs):
            assert not in_callback["active"], "nested synchronous broker query in orderStatus callback"
            return _old(*args, **kwargs)

        monkeypatch.setattr(rig.broker, name, guarded)

    adapter = IbkrBroker(rig.cfg)

    class WakeIB(FakeIB):
        def __init__(self):
            super().__init__()
            self.sleep_intervals = []

        def sleep(self, seconds):
            self.sleep_intervals.append(seconds)
            if len(self.sleep_intervals) == 1:
                current = rig.broker.orders[protect.intent_id]
                request = current.req
                oid, perm = current.broker_order_id, current.broker_order_id + 50_000
                # The native adapter now carries a complete broker tuple. Keep
                # this urgency test on that validated path, rather than a bare
                # legacy status hint that cannot verify a native modification.
                trade = SimpleNamespace(
                    contract=SimpleNamespace(symbol=request.symbol,
                                             conId=rig.broker.con_ids[request.symbol],
                                             secType="STK", currency="USD", exchange="SMART"),
                    order=SimpleNamespace(orderRef=protect.intent_id, orderId=oid, permId=perm,
                                          account=rig.cfg.account, clientId=rig.cfg.client_id,
                                          action="SELL", orderType="STP", tif="GTC", outsideRth=False,
                                          totalQuantity=request.quantity, auxPrice=request.trigger_price,
                                          lmtPrice=request.limit_price),
                    orderStatus=SimpleNamespace(status="Submitted", filled=current.filled,
                                                remaining=current.remaining, orderId=oid, permId=perm,
                                                clientId=rig.cfg.client_id),
                    log=[])
                in_callback["active"] = True
                try:
                    adapter._on_status(trade)
                finally:
                    in_callback["active"] = False

    adapter.ib = WakeIB()
    adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
    adapter.pump(1.0)
    assert adapter.ib.sleep_intervals == [pytest.approx(0.05)]
    assert not [req for req in rig.broker.sent if req.side is Side.SELL]
    rig.tick()  # outer loop runs immediately after pump woke; no 1s sleep
    assert len([req for req in rig.broker.sent if req.side is Side.SELL]) == 1


@pytest.mark.parametrize("unverified_reason", ["disconnected", "stale_confirmation"])
def test_L7_daily_health_protection_lists_price_qty_gap_and_unverified_evidence(tmp_path,
                                                                                unverified_reason):
    from v6lite.report import daily_health_card, daily_health_report

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    position = rig.store.open_positions()[0]
    protection = rig.store.protection(position["position_id"])
    assert protection["confirmed_remaining"] == position["qty_open"]
    if unverified_reason == "stale_confirmation":
        rig.store.update_protection(rig.clock.now(), position["position_id"],
                                    confirmed_at=et(10, 0, day=(2026, 10, 5)))
    rig.store.audit(rig.clock.now(), "PROTECT_GAP", gap_kind="A", secs=0.7,
                    symbol="AAPL", intent_id=protection["intent_id"])
    rig.store.audit(rig.clock.now(), "PROTECT_GAP", gap_kind="B", secs=1.25,
                    symbol="AAPL", intent_id=protection["intent_id"])
    day = date(2026, 10, 6)
    text = daily_health_report(
        rig.store, rig.cfg, day,
        status={"broker_connected": unverified_reason != "disconnected",
                "reconcile_ok": True, "alerts_healthy": True,
                "open_positions": [("AAPL", position["qty_open"])], "code_sha": "synthetic"},
        gateway={"replies": 1}, alerts={"sent": 1, "failed": 0},
        quotes={"samples": 1, "total": 1, "usable": 1, "subscribed": 1})
    card = daily_health_card(text, day)
    health = dict(card.fields)["券商端保护"]
    assert all(part in health for part in (
        "AAPL", f"P={position['qty_open']}", f"S={protection['confirmed_remaining']}",
        "类型=STP", f"止损价（触发后市价成交）={protection['trigger_price']:.2f}",
        "A=0.700s", "B（正常）最大 1.250s"))
    assert "限价=" not in health
    assert "AAPL 存在" not in health
    assert any(term in health for term in ("待核实", "未核实", "仍需核实", "过期"))


def test_L7_urgent_entry_local_store_failure_pages_unprotected_fill(tmp_path, monkeypatch):
    """A failed local plan write cannot silently lose the first BUY fill."""
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.models import FillEvent
    from v6lite.tests.test_ibkr_adapter import FakeIB

    rig = _protected_rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    fill = rig.broker.fill(buy.intent_id, qty=1)
    rig.broker.drain_events()  # real-adapter urgent callback below owns this fill
    old_update = rig.store.update_protection
    failed = []

    def fail_once(*args, **kwargs):
        if kwargs.get("state") == "SUBMIT_PENDING" and not failed:
            failed.append(True)
            raise OSError("synthetic SQLite write failure")
        return old_update(*args, **kwargs)

    monkeypatch.setattr(rig.store, "update_protection", fail_once)
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = FakeIB()
    adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
    assert isinstance(fill, FillEvent)
    adapter._dispatch_urgent(fill)
    assert failed == [True]
    assert "P0" in rig.messages() and "AAPL" in rig.messages() and "1" in rig.messages()
    assert rig.store.protection(rig.store.open_positions()[0]["position_id"])["gap_a_started_at"] is not None \
           or rig.store.control("frozen:AAPL") is not None \
           or rig.engine._protection_emergency is True


def test_L7_fractional_external_sell_is_rounded_up_for_worst_case(tmp_path, monkeypatch):
    from v6lite.models import OpenOrder

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    held = rig.store.open_positions()[0]["qty_open"]
    old_open = rig.broker.open_orders

    def with_fractional_external():
        return old_open() + [OpenOrder("foreign-fraction", 9999, "AAPL", "Submitted",
                                       0.0, 0.5, "SELL", False)]

    monkeypatch.setattr(rig.broker, "open_orders", with_fractional_external)
    rig.engine._protection_progress(rig.clock.now())
    assert rig.store.control("frozen:AAPL") is not None
    assert "P0" in rig.messages()
    assert rig.broker.modified_protections[-1].quantity <= held - 1


def test_L7_external_sell_during_tp_cancel_pending_still_reduces_protection(tmp_path):
    from v6lite.models import OrderRequest, Purpose

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    held = rig.store.open_positions()[0]["qty_open"]
    rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    protect = _protection_orders(rig)[0]
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    before = rig.broker.orders[protect.intent_id].req.quantity
    rig.broker._register(OrderRequest("foreign-during-cancel", "AAPL", Side.SELL,
                                     1, 110.0, Purpose.TP1))
    assert before + tp.quantity + 1 > held
    rig.engine._protection_progress(rig.clock.now())
    assert rig.store.control("frozen:AAPL") is not None
    assert "P0" in rig.messages()
    assert rig.broker.modified_protections[-1].quantity <= before - 1


def test_L7_direct_dry_run_last_share_tp_never_calls_broker_cancel(tmp_path, monkeypatch):
    from v6lite.engine import Engine
    from v6lite.models import Purpose

    rig = _one_share_position(tmp_path)
    pos = rig.store.open_positions()[0]
    assert rig.store.begin_stage(rig.clock.now(), pos["position_id"], Purpose.TP1, 1)
    writes = []
    for name in ("cancel", "submit_limit", "submit_stop", "modify_protection"):
        monkeypatch.setattr(rig.broker, name,
                            lambda *args, _name=name, **kwargs: writes.append(_name))
    dry = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock, dry_run=True)
    dry._protection_begin_tp(rig.clock.now(), pos["position_id"], Purpose.TP1)
    assert writes == []


@pytest.mark.parametrize("path", ["direct_cancel", "shutdown"])
def test_L7_orphan_gtc_cannot_be_generically_cancelled_without_exact_ledger_identity(tmp_path, path):
    from v6lite.models import ProtectionRequest

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos = rig.store.open_positions()[0]
    orphan = ProtectionRequest("v6l-orphan-gtc", "unclaimed-position", "AAPL", 1,
                               pos["stop"], None)
    rig.broker.submit_stop(orphan)
    assert rig.store.protection_for_intent(orphan.intent_id) is None
    if path == "direct_cancel":
        rig.engine._request_cancel(rig.clock.now(), orphan.intent_id, "synthetic generic cleanup")
    else:
        rig.engine.shutdown(grace_seconds=0)
    assert orphan.intent_id not in rig.broker.cancels
    assert "P0" in rig.messages() and orphan.intent_id in rig.messages()


@pytest.mark.parametrize("existing_gtc", [False, True])
def test_L7_restart_planned_fill_requires_three_source_before_submit_and_no_duplicate(
        tmp_path, monkeypatch, existing_gtc):
    from v6lite.engine import Engine
    from v6lite.models import ProtectionRequest

    rig = _protected_rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    plan = rig.store.protection(buy.position_id)
    assert plan is not None and plan["state"] == "PLANNED"
    rig.broker.fill(buy.intent_id, qty=1, emit=False)  # crash before execDetails reaches engine
    if existing_gtc:
        rig.broker.submit_stop(ProtectionRequest(
            plan["intent_id"], plan["position_id"], "AAPL", 1,
            plan["trigger_price"], None))
    before = len(_protection_orders(rig))
    evidence = []
    for name in ("positions_fresh", "open_orders", "executions_today"):
        original = getattr(rig.broker, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            evidence.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(rig.broker, name, observed)
    original_submit = rig.broker.submit_stop

    def guarded_submit(req):
        assert {"positions_fresh", "open_orders", "executions_today"} <= set(evidence), evidence
        return original_submit(req)

    monkeypatch.setattr(rig.broker, "submit_stop", guarded_submit)
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "planned-restart")
    resumed.startup()
    if existing_gtc:
        assert len(_protection_orders(rig)) == before
    else:
        assert len(_protection_orders(rig)) <= before + 1
        assert "P0" in rig.messages() or len(_protection_orders(rig)) == before + 1


def test_L7_direct_entry_send_without_atr_plan_refuses_buy_and_pages(tmp_path):
    from v6lite.models import OrderRequest, Purpose
    from v6lite.store import new_id

    rig = _protected_rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    req = OrderRequest(new_id("v6l"), "AAPL", Side.BUY, 1, 113.47,
                       Purpose.ENTRY, new_id("pos"))
    sent_before = len(rig.broker.sent)
    messages_before = len(rig.channel.messages)
    result = rig.engine._send(rig.clock.now(), req, atr=None, count_entry_day="2026-10-06")
    assert result == "refused:invalid_protection_price"
    assert len(rig.broker.sent) == sent_before
    assert req.intent_id not in rig.broker.orders
    assert rig.store.intent(req.intent_id) is None
    assert not _protection_orders(rig)
    cards = [(title, body) for title, body in rig.channel.messages[messages_before:]
             if "无法计算保护价，买单已拦截" in title]
    assert len(cards) == 1
    title, body = cards[0]
    assert title == "🟠 P1 · 无法计算保护价，买单已拦截" and "AAPL" in body
    assert not any("P0" in title for title, _ in cards)


def test_L7_unconfirmed_first_fill_blocks_other_symbol_at_final_buy_boundary(tmp_path):
    from v6lite.models import OrderRequest, Purpose
    from v6lite.store import new_id

    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    first = _entry_and_fill(rig, quantity=1)
    row = rig.store.protection(first.position_id)
    assert row is not None and row["confirmed_remaining"] in (None, 0)
    assert row["gap_a_started_at"] is not None
    sent_before = [req for req in rig.broker.sent if req.side is Side.BUY]
    assert len(sent_before) == 1
    second = OrderRequest(new_id("v6l"), "SPY", Side.BUY, 1, 500.0,
                          Purpose.ENTRY, new_id("pos"))
    rig.engine._send(rig.clock.now(), second, atr=2.0, count_entry_day="2026-10-06")
    assert [req for req in rig.broker.sent if req.side is Side.BUY] == sent_before


def test_L7_gap_deadlines_preempt_each_slow_evidence_call_across_positions(tmp_path, monkeypatch):
    from v6lite.tests.helpers import buy_bars

    rig = _protected_rig(tmp_path, max_positions=3)
    deadline_handlers = []
    monkeypatch.setattr(rig.broker, "set_protection_deadline_handler",
                        lambda handler: deadline_handlers.append(handler), raising=False)
    rig.broker.protection_auto_status = False
    for symbol in ("SPY", "TSLA"):
        rig.broker.bars[symbol] = buy_bars()
        rig.px(symbol, 113.13)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buys = [req for req in rig.broker.sent if req.side is Side.BUY]
    assert len(buys) >= 2
    for buy in buys[:2]:
        rig.broker.fill(buy.intent_id, qty=1)
    rig.advance(seconds=1)
    rig.tick()
    symbols = [buy.symbol for buy in buys[:2]]
    assert len(rig.store.protections()) >= 2
    from datetime import datetime

    started = {row["symbol"]: datetime.fromisoformat(row["gap_a_started_at"])
               for row in rig.store.protections() if row["symbol"] in symbols}
    assert deadline_handlers and callable(deadline_handlers[-1])
    rig.advance(seconds=3)  # gap A is below its 5s P1 threshold
    alerts = []
    original_send = rig.alerter.send

    def capture(now, level, subject, body="", **kwargs):
        alerts.append((rig.clock.now(), level, subject))
        return original_send(now, level, subject, body, **kwargs)

    monkeypatch.setattr(rig.alerter, "send", capture)
    inside_query = {"yes": False}
    original_queries = {name: getattr(rig.broker, name)
                        for name in ("open_orders", "positions_fresh", "executions_today")}

    def guarded(name, *args, **kwargs):
        assert not inside_query["yes"], f"nested broker query from deadline callback: {name}"
        return original_queries[name](*args, **kwargs)

    for name in ("open_orders", "positions_fresh", "executions_today"):
        monkeypatch.setattr(rig.broker, name,
                            lambda *args, _name=name, **kwargs: guarded(_name, *args, **kwargs))
    original_open = rig.broker.open_orders
    did_block = {"yes": False}

    def slow_open(*args, **kwargs):
        if not did_block["yes"]:
            did_block["yes"] = True
            inside_query["yes"] = True
            try:
                for _ in range(64):  # a single 16s evidence wait, with 250ms loop callbacks
                    rig.advance(milliseconds=250)
                    deadline_handlers[-1]()
            finally:
                inside_query["yes"] = False
        return original_open(*args, **kwargs)

    monkeypatch.setattr(rig.broker, "open_orders", slow_open)
    rig.tick()
    assert did_block["yes"]
    for symbol in symbols:
        p1 = [at for at, level, subject in alerts
              if level == "P1" and "protection gap A warning" in subject and symbol in subject]
        p0 = [at for at, level, subject in alerts
              if level == "P0" and "protection gap A overdue" in subject and symbol in subject]
        assert p1 and p1[0] <= started[symbol] + timedelta(seconds=5.25), (symbol, alerts)
        assert p0 and p0[0] <= started[symbol] + timedelta(seconds=15.25), (symbol, alerts)

    # A separate held position reaches gap B while a broker query occupies the
    # main engine loop. The 250ms callback may cancel its DAY TP from cache,
    # but it must never start another synchronous evidence request.
    b_dir = tmp_path / "gap-b"
    b_dir.mkdir()
    b = _protected_rig(b_dir)
    b_handlers = []
    monkeypatch.setattr(b.broker, "set_protection_deadline_handler",
                        lambda handler: b_handlers.append(handler), raising=False)
    _entry_and_fill(b)
    b.px("AAPL", b.store.open_positions()[0]["tp1"] + 1)
    b.advance(seconds=31)
    b.tick()
    tp = next(req for req in b.broker.sent if req.side is Side.SELL)
    b_row = b.store.protection(b.store.open_positions()[0]["position_id"])
    b_started = datetime.fromisoformat(b_row["gap_b_started_at"])
    assert b_handlers and callable(b_handlers[-1])
    b.advance(seconds=9)
    b_alerts = []
    original_b_send = b.alerter.send

    def capture_b(now, level, subject, body="", **kwargs):
        b_alerts.append((b.clock.now(), level, subject))
        return original_b_send(now, level, subject, body, **kwargs)

    monkeypatch.setattr(b.alerter, "send", capture_b)
    b_cancelled_at = []
    original_cancel = b.broker.cancel

    def timed_cancel(intent_id, **kwargs):
        b_cancelled_at.append((b.clock.now(), intent_id))
        return original_cancel(intent_id, **kwargs)

    monkeypatch.setattr(b.broker, "cancel", timed_cancel)
    b_in_query = {"yes": False}
    for name in ("positions_fresh", "executions_today"):
        original = getattr(b.broker, name)

        def no_nested(*args, _name=name, _original=original, **kwargs):
            assert not b_in_query["yes"], f"nested broker query from deadline callback: {_name}"
            return _original(*args, **kwargs)

        monkeypatch.setattr(b.broker, name, no_nested)
    original_b_open = b.broker.open_orders

    def b_slow_open():
        assert not b_in_query["yes"], "nested open-orders query from deadline callback"
        b_in_query["yes"] = True
        try:
            for _ in range(88):  # elapsed 9s -> 31s, spanning both B deadlines
                b.advance(milliseconds=250)
                b_handlers[-1]()
        finally:
            b_in_query["yes"] = False
        return original_b_open()

    b_slow_open()
    assert any(iid == tp.intent_id and at <= b_started + timedelta(seconds=10.25)
               for at, iid in b_cancelled_at), b_cancelled_at
    assert any(level == "P0" and "protection gap B overdue" in subject
               and at <= b_started + timedelta(seconds=30.25)
               for at, level, subject in b_alerts), b_alerts


@pytest.mark.parametrize("gap_kind", ["A", "B"])
def test_L7_restart_arms_persisted_gap_timer_before_blocking_recovery_query(tmp_path,
                                                                              monkeypatch,
                                                                              gap_kind):
    from datetime import datetime
    from v6lite.engine import Engine

    rig = _protected_rig(tmp_path)
    if gap_kind == "A":
        rig.broker.protection_auto_status = False
    _entry_and_fill(rig, quantity=1 if gap_kind == "A" else None)
    if gap_kind == "B":
        rig.px("AAPL", rig.store.open_positions()[0]["tp1"] + 1)
        rig.advance(seconds=31)
        rig.tick()
        tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    started = datetime.fromisoformat(row[f"gap_{gap_kind.lower()}_started_at"])
    rig.at(started + timedelta(seconds=4 if gap_kind == "A" else 9))

    deadline_handlers = []
    monkeypatch.setattr(rig.broker, "set_protection_deadline_handler",
                        lambda handler: deadline_handlers.append(handler), raising=False)
    alerts = []
    old_send = rig.alerter.send

    def capture(now, level, subject, body="", **kwargs):
        alerts.append((rig.clock.now(), level, subject))
        return old_send(now, level, subject, body, **kwargs)

    monkeypatch.setattr(rig.alerter, "send", capture)
    cancellations = []
    old_cancel = rig.broker.cancel

    def capture_cancel(intent_id, **kwargs):
        cancellations.append((rig.clock.now(), intent_id, blocked["active"]))
        return old_cancel(intent_id, **kwargs)

    monkeypatch.setattr(rig.broker, "cancel", capture_cancel)
    original_open = rig.broker.open_orders
    blocked = {"yes": False, "active": False}

    def slow_recovery_open():
        assert not blocked["active"], "deadline callback may not nest broker evidence query"
        if not blocked["yes"]:
            blocked["yes"] = True
            blocked["active"] = True
            try:
                for _ in range(48 if gap_kind == "A" else 88):
                    rig.advance(milliseconds=250)
                    assert deadline_handlers and callable(deadline_handlers[-1]), \
                        "persisted gap timer must be armed before recovery evidence wait"
                    deadline_handlers[-1]()
                assert not cancellations, "preflight timer may alert but cannot cancel before identity check"
            finally:
                blocked["active"] = False
        return original_open()

    monkeypatch.setattr(rig.broker, "open_orders", slow_recovery_open)
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                     report_dir=tmp_path / "restart-deadline")
    resumed.startup()
    assert blocked["yes"]
    if gap_kind == "A":
        assert any(level == "P1" and "protection gap A warning" in subject
                   and at <= started + timedelta(seconds=5.25)
                   for at, level, subject in alerts), alerts
        assert any(level == "P0" and "protection gap A overdue" in subject
                   and at <= started + timedelta(seconds=15.25)
                   for at, level, subject in alerts), alerts
    else:
        assert any(level == "P0" and "protection preflight B overdue" in subject
                   and at <= started + timedelta(seconds=30.25)
                   for at, level, subject in alerts), alerts
        assert all(intent_id == tp.intent_id and not preflight
                   for _, intent_id, preflight in cancellations), cancellations


@pytest.mark.parametrize("end_phase", ["tp_filled", "cancel_then_restore"])
def test_L7_only_one_account_wide_take_profit_protection_workflow_at_a_time(tmp_path, end_phase):
    from v6lite.models import Purpose
    from v6lite.tests.helpers import buy_bars

    rig = _protected_rig(tmp_path, max_positions=3)
    rig.broker.bars["SPY"] = buy_bars()
    rig.px("SPY", 113.13)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    buys = [req for req in rig.broker.sent if req.side is Side.BUY]
    assert {req.symbol for req in buys} >= {"AAPL", "SPY"}
    for req in buys:
        rig.broker.fill(req.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    by_symbol = {pos["symbol"]: pos for pos in rig.store.open_positions()}
    assert {"AAPL", "SPY"} <= by_symbol.keys()
    for symbol in ("AAPL", "SPY"):
        pos = by_symbol[symbol]
        assert rig.store.begin_stage(rig.clock.now(), pos["position_id"], Purpose.TP1, 1)
        rig.px(symbol, 120.0)  # Current mark must still justify TP1 after broker evidence.

    rig.broker.protection_auto_status = False
    aapl_id = by_symbol["AAPL"]["position_id"]
    spy_id = by_symbol["SPY"]["position_id"]
    rig.engine._protection_begin_tp(rig.clock.now(), aapl_id, Purpose.TP1)
    assert rig.store.protection(aapl_id)["state"] == "REDUCE_PENDING"
    modified_before = len(rig.broker.modified_protections)
    rig.engine._protection_begin_tp(rig.clock.now(), spy_id, Purpose.TP1)
    assert len(rig.broker.modified_protections) == modified_before
    assert rig.store.protection(spy_id)["tp_stage"] is None

    aapl_ref = rig.store.protection(aapl_id)["intent_id"]
    rig.broker.confirm_protection_modify(aapl_ref)
    rig.engine._drain(rig.clock.now())
    aapl_tp = next(req for req in rig.broker.sent if req.side is Side.SELL and req.symbol == "AAPL")
    rig.engine._protection_begin_tp(rig.clock.now(), spy_id, Purpose.TP1)
    assert not [req for req in rig.broker.sent if req.side is Side.SELL and req.symbol == "SPY"]
    assert rig.store.protection(spy_id)["tp_stage"] is None

    if end_phase == "tp_filled":
        rig.broker.fill(aapl_tp.intent_id, qty=aapl_tp.quantity)
        rig.engine._drain(rig.clock.now())
    else:
        rig.advance(seconds=10)
        rig.tick()
        assert aapl_tp.intent_id in rig.broker.cancels
        rig.engine._protection_begin_tp(rig.clock.now(), spy_id, Purpose.TP1)
        assert rig.store.protection(spy_id)["tp_stage"] is None
        rig.broker.protection_auto_status = True
        rig.broker.confirm_cancel(aapl_tp.intent_id)
        rig.engine._drain(rig.clock.now())
        rig.tick()
        assert rig.store.protection(aapl_id)["tp_stage"] is None
    rig.engine._protection_begin_tp(rig.clock.now(), spy_id, Purpose.TP1)
    assert rig.store.protection(spy_id)["state"] == "REDUCE_PENDING"
    assert rig.broker.modified_protections[-1].symbol == "SPY"


@pytest.mark.parametrize("other_kind", ["owned_tp", "external_sell"])
@pytest.mark.parametrize("pending_increase", [False, True])
def test_L7_urgent_late_sell_counts_other_working_sell_and_pending_protection_max(
        tmp_path, other_kind, pending_increase):
    from v6lite.models import IntentStatus, OrderRequest, Purpose
    from v6lite.store import new_id

    rig = _protected_rig(tmp_path, max_order_notional_usd=400.0)
    buy = _entry_and_fill(rig)
    assert buy.quantity == 3
    pos = rig.store.open_positions()[0]
    pid = pos["position_id"]
    protect = _protection_orders(rig)[0]

    if other_kind == "owned_tp":
        assert rig.store.begin_stage(rig.clock.now(), pid, Purpose.TP1, 1)
        rig.px("AAPL", 120.0)  # Keep the latest quote at a genuine TP1 trigger.
        rig.engine._protection_begin_tp(rig.clock.now(), pid, Purpose.TP1)
        rig.engine._drain(rig.clock.now())  # server status confirms S=2, then TP is sent
        tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
        assert rig.broker.orders[tp.intent_id].remaining == 1
    else:
        foreign = OrderRequest("external-open-sell", "AAPL", Side.SELL, 1,
                               110.0, Purpose.TP1)
        rig.broker._register(foreign)
        rig.engine._protection_progress(rig.clock.now())
        rig.engine._drain(rig.clock.now())  # server status confirms S=2
        assert rig.broker.orders[foreign.intent_id].remaining == 1

    row = rig.store.protection(pid)
    assert pos["qty_open"] == 3
    assert row["confirmed_remaining"] == 2
    assert rig.broker.orders[protect.intent_id].remaining == 2
    if pending_increase:
        # Fault injection: an older increase may have reached IB while its
        # status is still UNKNOWN. A new only-decrease request cannot erase
        # the possible S=3 until a fresh server status proves the reduction.
        rig.store.update_protection(rig.clock.now(), pid,
                                    pending_max_remaining=3, pending_qty=3)
        rig.broker.protection_auto_status = False

    late = OrderRequest(new_id("v6l"), "AAPL", Side.SELL, 1, 110.0,
                        Purpose.TP2, pid)
    rig.store.create_intent(rig.clock.now(), late, atr=None, count_entry_day=None)
    rig.store.update_intent(rig.clock.now(), late.intent_id, status=IntentStatus.CANCELLED)
    rig.broker._register(late).status = "Cancelled"
    prior_modifications = len(rig.broker.modified_protections)
    rig.broker.fill(late.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())  # same urgent event, before the next tick/query
    assert rig.store.open_positions()[0]["qty_open"] == 2
    assert len(rig.broker.modified_protections) > prior_modifications
    assert rig.broker.modified_protections[-1].quantity <= 1  # P2 - (T or X)1
    assert "P0" in rig.messages()
    if pending_increase:
        row = rig.store.protection(pid)
        assert row["pending_max_remaining"] >= 3  # only server confirmation releases worst S


def test_L7_progress_gross_sell_uses_pending_max_not_only_fresh_gtc_remaining(tmp_path):
    from v6lite.models import Purpose

    rig = _protected_rig(tmp_path, max_order_notional_usd=400.0)
    buy = _entry_and_fill(rig)
    assert buy.quantity == 3
    pid = rig.store.open_positions()[0]["position_id"]
    assert rig.store.begin_stage(rig.clock.now(), pid, Purpose.TP1, 1)
    rig.px("AAPL", 120.0)  # Keep the latest quote at a genuine TP1 trigger.
    rig.engine._protection_begin_tp(rig.clock.now(), pid, Purpose.TP1)
    rig.engine._drain(rig.clock.now())
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    protection = _protection_orders(rig)[0]
    assert rig.broker.orders[tp.intent_id].remaining == 1
    assert rig.broker.orders[protection.intent_id].remaining == 2
    assert rig.store.protection(pid)["confirmed_remaining"] == 2

    # Old unknown increase can still land at S=3 even when the last broker
    # snapshot reports S=2. The gross budget must count max(2,3)+T1>P3.
    rig.store.update_protection(rig.clock.now(), pid,
                                pending_max_remaining=3, pending_qty=3)
    rig.broker.protection_auto_status = False
    prior_modifications = len(rig.broker.modified_protections)
    rig.engine._protection_progress(rig.clock.now())
    assert len(rig.broker.modified_protections) > prior_modifications
    assert rig.broker.modified_protections[-1].quantity <= 2
    assert rig.store.protection(pid)["pending_max_remaining"] >= 3
    assert "P0" in rig.messages()


def test_L7_unconfirmed_entry_card_does_not_claim_possible_gtc_as_confirmed_cover(tmp_path):
    rig = _protected_rig(tmp_path)
    rig.broker.protection_auto_status = False
    _entry_and_fill(rig, quantity=1)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    assert row["confirmed_remaining"] in (None, 0)
    assert row["pending_max_remaining"] == 1
    rig.advance(seconds=5)
    rig.tick()
    card = next(body for title, body in rig.channel.messages if "入场后保护单尚未确认" in title)
    assert "保护单已确认=0 股" in card
    assert "保护单可能=1 股" in card
    assert "未保护=1 股" in card or "未保护=待核实" in card


def test_L7_definitely_rejected_gtc_card_shows_unprotected_held_shares(tmp_path):
    from v6lite.broker.base import BrokerError

    rig = _protected_rig(tmp_path)
    rig.broker.fail_next_protection = BrokerError("synthetic definite rejection", maybe_sent=False)
    _entry_and_fill(rig)
    held = rig.store.open_positions()[0]["qty_open"]
    card = next(body for title, body in rig.channel.messages
                if "券商拒绝了保护单，该持仓目前没有券商端保护" in title)
    assert "保护单已确认=0 股" in card
    assert f"未保护={held} 股" in card or "未保护=待核实" in card
    assert "未保护=0 股" not in card


def test_L7_external_sell_card_shows_gross_oversell_not_only_uncovered(tmp_path):
    from v6lite.models import OrderRequest, Purpose

    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    held = rig.store.open_positions()[0]["qty_open"]
    rig.broker._register(OrderRequest("external-oversell-card", "AAPL", Side.SELL, 1,
                                     110.0, Purpose.TP1))
    rig.engine._protection_progress(rig.clock.now())
    card = next(body for title, body in rig.channel.messages if "托管标的出现外部卖单" in title)
    assert f"持仓数量={held} 股" in card and "外部卖单在途=1 股" in card
    assert "可能超卖=1 股" in card


def test_L7_shutdown_missing_gtc_card_does_not_reuse_old_verified_snapshot(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    ref = _protection_orders(rig)[0].intent_id
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    assert row["confirmed_remaining"] == rig.store.open_positions()[0]["qty_open"]
    # A fresh shutdown broker list omits the expected GTC. The previous
    # confirmation is historical evidence, not proof it exists now.
    rig.broker.orders[ref].status = "Cancelled"
    rig.engine.shutdown(grace_seconds=0)
    card = next(body for title, body in rig.channel.messages if "停机时保护单未核实" in title)
    assert "当前保护状态待核实" in card
    assert "未保护=待核实" in card
    assert "当前快照已核实" not in card


@pytest.mark.parametrize("mismatch", ["perm_id", "aux_price", "order_type", "outside_rth", "status"])
def test_L7_card_current_verification_requires_exact_gtc_identity_and_terms(tmp_path, mismatch):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    order = next(o for o in rig.broker.open_orders() if o.intent_id == row["intent_id"])
    changes = {
        "perm_id": {"perm_id": order.perm_id + 1},
        "aux_price": {"aux_price": order.aux_price + 0.01},
        "order_type": {"order_type": "STP LMT"},  # B24: a STP lmt echo is not an identity mismatch
        "outside_rth": {"outside_rth": True},
        "status": {"status": "PendingCancel"},
    }
    # Synthetic all-client working snapshot: every field except one matches
    # the Store's GTC identity. Ordinary card rendering must use the same
    # strict predicate as the protection order coordinator.
    rig.engine._working = {order.intent_id: replace(order, **changes[mismatch])}
    rig.engine._working_at = rig.clock.now()
    rig.engine._protection_page(rig.clock.now(), "P0", "trigger suspected", row,
                                "synthetic mismatched GTC snapshot")
    card = next(body for title, body in reversed(rig.channel.messages)
                if "疑似触发或保护状态不明" in title)
    assert "当前保护状态待核实" in card
    assert "未保护=待核实" in card
    assert "当前快照已核实" not in card



@pytest.mark.parametrize("echo", [340.19000000000005, 1.0])
def test_B24_L7_card_current_verification_accepts_finite_STP_echo(tmp_path, echo):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    row = rig.store.protection(rig.store.open_positions()[0]["position_id"])
    order = next(o for o in rig.broker.open_orders() if o.intent_id == row["intent_id"])
    # An external older OpenOrder payload can still carry the echo in the old
    # limit field. The exact STP validator ignores it and preserves authority.
    rig.engine._working = {order.intent_id: replace(order, limit_price=echo)}
    rig.engine._working_at = rig.clock.now()
    rig.engine._protection_page(rig.clock.now(), "P0", "trigger suspected", row,
                                "synthetic complete GTC with finite echo")
    card = next(body for title, body in reversed(rig.channel.messages)
                if "疑似触发或保护状态不明" in title)
    assert "当前快照已核实" in card and "未保护=0" in card
    assert rig.store.protection(row["position_id"])["limit_price"] is None

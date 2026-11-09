"""Batch 20: protection incident identity, rejection evidence and paging.

All broker objects are local doubles and every store is under pytest tmp_path.
These tests never connect to IBKR or read/write an operator runtime database.
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine
from v6lite.models import BrokerAck, IntentStatus, OrderRequest, Purpose, Side, StatusEvent
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et

REJECT_ZH = "委托单被取消 - 原因：我们无法接纳您的止损市价单，请在 TWS 人工核对并处理。"
REJECT_EN = "Order canceled - reason: We cannot accept your stop order."


def _pending_protection(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.broker.protection_auto_status = False
    submit = rig.broker.submit_stop

    def early_local_ack(req):
        ack = submit(req)
        return BrokerAck(ack.broker_order_id, None)

    monkeypatch.setattr(rig.broker, "submit_stop", early_local_ack)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    entry = next(req for req in rig.broker.sent if req.purpose == Purpose.ENTRY)
    fill = rig.broker.fill(entry.intent_id, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    [pos] = rig.store.open_positions()
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "SUBMIT_PENDING"
    assert row["perm_id"] is None and rig.store.intent(row["intent_id"])["perm_id"] is None
    rig.channel.messages.clear()
    return rig, pos["position_id"]


def _wire(rig, position_id, *, raw="PreSubmitted", message="", code=0, **changes):
    """Represent the same complete order/contract/status tuple IB emits."""
    row = rig.store.protection(position_id)
    order = rig.broker.orders[row["intent_id"]]
    perm = order.broker_order_id + 50_000
    contract = SimpleNamespace(symbol=row["symbol"], conId=row["con_id"], secType="STK",
                               currency="USD", exchange="SMART")
    wire_order = SimpleNamespace(orderRef=row["intent_id"], orderId=order.broker_order_id,
                                 permId=perm, account=rig.cfg.account, clientId=rig.cfg.client_id,
                                 action="SELL", orderType="STP", tif="GTC", outsideRth=False,
                                 totalQuantity=order.req.quantity, auxPrice=order.req.trigger_price,
                                 lmtPrice=order.req.limit_price)
    status = SimpleNamespace(status=raw, filled=order.filled, remaining=order.remaining,
                             orderId=order.broker_order_id, permId=perm, clientId=rig.cfg.client_id)
    for name, value in changes.items():
        target, field = name.split("__", 1)
        setattr({"contract": contract, "order": wire_order, "status": status}[target], field, value)
    log = [SimpleNamespace(time=rig.clock.now(), status=raw, message=message, errorCode=code)]
    trade = SimpleNamespace(order=wire_order, contract=contract, orderStatus=status, log=log,
                            fills=[], advancedError="")
    adapter = IbkrBroker(rig.cfg)
    adapter.ib = SimpleNamespace(trades=lambda: [trade], openTrades=lambda: [trade],
                                 isConnected=lambda: True)
    adapter._trades[row["intent_id"]] = trade
    adapter._contracts[row["symbol"]] = contract
    return adapter, trade


def _deliver_wire(rig, adapter, trade):
    adapter._on_status(trade)
    events = adapter.drain_events()
    assert events, "the native status callback must preserve the event"
    for event in events:
        rig.engine._on_urgent_broker_event(event)


def _release(rig):
    rig.store.set_control(rig.clock.now(), "release:AAPL", json.dumps(
        {"operator": "test-operator", "reason": "verified manual limit flatten"}))
    rig.engine._apply_releases(rig.clock.now())


def _manual_flat(rig, position_id):
    pos = rig.store.position(position_id)
    req = OrderRequest("external-manual-flat", "AAPL", Side.SELL, pos["qty_open"],
                       100.0, Purpose.TP1)
    rig.broker._register(req)
    rig.broker.fill(req.intent_id, emit=False)
    rig.store.set_control(rig.clock.now(), "frozen:AAPL", "operator verified manual flatten")
    rig.advance(seconds=61)
    assert rig.broker.positions_fresh() == []


def _pages(rig, level=None):
    return [(title, body) for title, body in rig.channel.messages
            if ("P0" in title or "P1" in title) and (level is None or level in title)]


def _uncertain_working(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    rig.store.update_protection(rig.clock.now(), pos["position_id"], state="UNKNOWN")
    rig.channel.messages.clear()
    return rig, pos["position_id"]


def _page_unknown(rig, position_id, detail="identity evidence incomplete"):
    rig.engine._protection_page(rig.clock.now(), "P0", "unknown",
                                rig.store.protection(position_id), detail)


def _restart(rig, tmp_path):
    db_path = rig.cfg.resolved_state_dir() / "v6lite.sqlite3"
    levels = rig.store._levels_fn
    rig.store.close()
    rig.store = Store(db_path, levels)
    rig.channel = MemoryChannel()
    rig.alerter = Alerter([rig.channel], synchronous=True)
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        report_dir=tmp_path / "reports")


@pytest.mark.parametrize("echo", [None, 340.19000000000005])
def test_B20_full_native_status_recovers_protection_identity_before_terminal_cancel(tmp_path, monkeypatch, echo):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid, order__lmtPrice=echo)
    _deliver_wire(rig, adapter, trade)
    row = rig.store.protection(pid)
    intent = rig.store.intent(row["intent_id"])
    assert row["perm_id"] == intent["perm_id"] == trade.order.permId
    assert row["broker_order_id"] == intent["broker_order_id"]
    # A synchronous/status callback identity is not a fresh working-order proof.
    assert row["confirmed_at"] is None
    assert row["state"] != "WORKING"


@pytest.mark.parametrize("mismatch", [
    {"order__account": "wrong-account"}, {"order__clientId": 999},
    {"contract__conId": 999}, {"contract__symbol": "TSLA"},
    {"order__orderId": 999}, {"order__action": "BUY"},
    {"order__orderType": "LMT"}, {"order__orderType": "STP LMT"}, {"order__tif": "DAY"},
    {"order__auxPrice": 1.0},  # B24: finite STP echo moved to the positive native fixture
    {"order__permId": 999}, {"status__permId": 0},
    {"status__orderId": 999}, {"status__clientId": 999},
])
def test_B20_wrong_full_status_identity_cannot_repair_protection_or_authorize_release(
        tmp_path, monkeypatch, mismatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid, **mismatch)
    _deliver_wire(rig, adapter, trade)
    assert rig.store.protection(pid)["perm_id"] is None
    rig.broker.orders[rig.store.protection(pid)["intent_id"]].status = "Cancelled"
    _manual_flat(rig, pid)
    _release(rig)
    assert rig.store.position(pid)["status"] == "OPEN"
    assert rig.store.flat_point("AAPL") is None


def test_B20_bare_status_perm_is_not_strict_protection_identity_evidence(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    row = rig.store.protection(pid)
    ref, oid = row["intent_id"], row["broker_order_id"]
    rig.engine._on_urgent_broker_event(StatusEvent(ref, "PreSubmitted", 0, 20, oid, oid + 50_000))
    assert rig.store.protection(pid)["perm_id"] is None
    rig.broker.orders[ref].status = "Cancelled"
    rig.engine._on_urgent_broker_event(StatusEvent(ref, "Cancelled", 0, 20, oid, oid + 50_000))
    _manual_flat(rig, pid)
    _release(rig)
    assert rig.store.position(pid)["status"] == "OPEN"
    assert rig.store.control("frozen:AAPL") is not None
    assert rig.store.flat_point("AAPL") is None


def test_B20_recovered_exact_identity_release_still_requires_manual_flat(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid)
    _deliver_wire(rig, adapter, trade)
    row = rig.store.protection(pid)
    rig.broker.orders[row["intent_id"]].status = "Cancelled"
    trade.orderStatus.status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    _release(rig)
    assert rig.store.position(pid)["status"] == "OPEN"  # real shares remain
    _manual_flat(rig, pid)
    sells_before = len(rig.broker.sent)
    _release(rig)
    assert rig.store.position(pid)["status"] == "RELEASED"
    assert rig.store.intent(row["intent_id"])["status"] == IntentStatus.CANCELLED
    assert len(rig.broker.sent) == sells_before
    assert rig.broker.cancels == []


@pytest.mark.parametrize("reason", [REJECT_ZH, REJECT_EN, REJECT_ZH.encode("unicode_escape").decode()],
                         ids=["zh-rejection", "en-rejection", "escaped-zh-rejection"])
def test_B20_202_explicit_stop_refusal_is_rejected_unprotected_and_never_rehung(
        tmp_path, monkeypatch, reason):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid)
    _deliver_wire(rig, adapter, trade)
    trade.orderStatus.status = "Cancelled"
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    trade.log.append(SimpleNamespace(time=rig.clock.now(), status="Cancelled", message=reason, errorCode=202))
    adapter._on_error(trade.order.orderId, 202, reason, trade.contract)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    row = rig.store.protection(pid)
    assert row["state"] == "REJECTED"
    pages = _pages(rig, "P0")
    assert any("券商已取消保护单" in title for title, _ in pages)
    assert any("TWS" in body and "限价" in body and "平仓" in body and "release AAPL" in body
               for title, body in pages if "券商已取消保护单" in title)
    assert row["confirmed_remaining"] == 0 and row["pending_max_remaining"] == 0
    sent = (len(rig.broker.sent), len(rig.broker.sent_protections), len(rig.broker.modified_protections))
    rig.advance(seconds=30)
    rig.engine._protection_progress(rig.clock.now())
    assert (len(rig.broker.sent), len(rig.broker.sent_protections), len(rig.broker.modified_protections)) == sent
    assert rig.store.position(pid)["qty_open"] > 0


@pytest.mark.parametrize("reason", ["Order canceled", "委托单被取消", "", "202: order canceled by user"])
def test_B20_202_normal_cancel_is_not_definite_stop_rejection(tmp_path, monkeypatch, reason):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid, raw="Cancelled", message=reason, code=202)
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    adapter._on_error(trade.order.orderId, 202, reason, trade.contract)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    assert rig.store.protection(pid)["state"] != "REJECTED"
    assert not any("券商拒绝了保护单" in title for title, _ in _pages(rig))


def test_B20_refusal_text_with_unmatched_request_cannot_change_protection(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid)
    adapter._on_error(trade.order.orderId + 999, 202, REJECT_ZH, trade.contract)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    assert rig.store.protection(pid)["state"] != "REJECTED"
    assert rig.store.protection(pid)["perm_id"] is None


def test_B20_unchanged_incident_first_P0_then_only_30minute_P1(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1
    for minutes in (5, 5, 5, 5, 9):
        rig.advance(minutes=minutes)
        # Changing receive-time, prices and diagnostic prose is not a new incident.
        rig.px("AAPL", 113.13 + minutes)
        _page_unknown(rig, pid, f"evidence checked at {rig.clock.now().isoformat()}")
    assert len(_pages(rig, "P0")) == 1
    assert _pages(rig, "P1") == []
    rig.advance(minutes=1)
    _page_unknown(rig, pid, "another complete check of the same unresolved identity")
    assert len(_pages(rig, "P0")) == 1
    assert len(_pages(rig, "P1")) == 1
    assert "仍未解决" in " ".join(_pages(rig, "P1")[0])
    rig.advance(minutes=29)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P1")) == 1
    rig.advance(minutes=1)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P1")) == 2


def test_B20_successful_incident_delivery_persists_across_store_and_engine_restart(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1
    rig.advance(minutes=5)
    _restart(rig, tmp_path)
    _page_unknown(rig, pid, "same condition after process restart")
    assert _pages(rig) == []
    rig.advance(minutes=25)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P1")) == 1
    assert _pages(rig, "P0") == []


def test_B20_failed_incident_delivery_does_not_suppress_retry_or_persist_false_success(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    rig.channel.fail = True
    _page_unknown(rig, pid)
    assert _pages(rig) == []
    rig.advance(seconds=1)
    _restart(rig, tmp_path)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1
    rig.advance(seconds=1)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1


def test_B20_changed_held_quantity_is_new_incident_but_duplicate_execution_is_not(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    row = rig.store.protection(pid)
    _page_unknown(rig, pid)
    fill = rig.broker.fill(row["intent_id"], qty=1, emit=False)
    rig.engine._on_urgent_broker_event(fill)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 2
    qty = rig.store.position(pid)["qty_open"]
    rig.engine._on_urgent_broker_event(fill)
    _page_unknown(rig, pid, "duplicate exec replay")
    assert rig.store.position(pid)["qty_open"] == qty
    assert len(_pages(rig, "P0")) == 2


def test_B20_strict_working_recovery_then_same_failure_is_new_P0(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    _page_unknown(rig, pid)
    row = rig.store.protection(pid)
    # Re-enter an existing same-order confirmation phase; this test does not
    # grant UNKNOWN the right to replace or increase an order automatically.
    rig.store.update_protection(rig.clock.now(), pid, state="MODIFY_PENDING",
                                 pending_qty=row["requested_qty"], pending_trigger=row["trigger_price"],
                                 pending_limit=row["limit_price"], status_seen_generation=row["modify_generation"])
    rig.engine._protection_progress(rig.clock.now())
    assert rig.store.protection(pid)["state"] == "WORKING"
    rig.store.update_protection(rig.clock.now(), pid, state="UNKNOWN")
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 2


def test_B20_terminal_cancel_while_tp_cancel_pending_is_not_immediate_rejection(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    row = rig.store.protection(pos["position_id"])
    rig.store.update_protection(rig.clock.now(), pos["position_id"], state="PROTECT_CANCEL_PENDING")
    rig.channel.messages.clear()
    rig.engine._on_urgent_broker_event(StatusEvent(row["intent_id"], "Cancelled", 0, 20,
                                                 row["broker_order_id"], row["perm_id"], "Order canceled"))
    assert rig.store.protection(pos["position_id"])["state"] == "PROTECT_CANCEL_PENDING"
    assert not any("拒绝" in title for title, _ in _pages(rig))


def test_B20_failed_first_delivery_retries_immediately_in_same_process(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    rig.channel.fail = True
    _page_unknown(rig, pid)
    assert _pages(rig) == []
    rig.channel.fail = False
    _page_unknown(rig, pid, "same failed page retried")
    assert len(_pages(rig, "P0")) == 1


def test_B20_pending_delivery_is_not_queued_twice_before_result(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    arrived, release = threading.Event(), threading.Event()

    class BlockingChannel(MemoryChannel):
        def send(self, subject, body):
            arrived.set()
            assert release.wait(5), "test must release the fake delivery promptly"
            super().send(subject, body)

    channel = BlockingChannel()
    rig.channel = channel
    rig.alerter = Alerter([channel], synchronous=False)
    rig.engine.alerter = rig.alerter
    try:
        _page_unknown(rig, pid)
        assert arrived.wait(2)
        rig.advance(minutes=5)
        _page_unknown(rig, pid, "changed timestamp while remote page is still in flight")
        assert rig.alerter.delivery_stats("2026-10-06")["sent"] == 1
    finally:
        release.set()
        rig.alerter._queue.put(None)
        rig.alerter._worker.join(timeout=2)
        assert not rig.alerter._worker.is_alive()


def test_B20_tp_identity_change_is_material_and_not_hidden_by_previous_P0(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    _page_unknown(rig, pid)
    req = OrderRequest("v6l-tp-b20", "AAPL", Side.SELL, 1, 120.0, Purpose.TP1, pid)
    rig.store.create_intent(rig.clock.now(), req, atr=None, count_entry_day=None)
    rig.store.update_intent(rig.clock.now(), req.intent_id, status=IntentStatus.SENT,
                            broker_order_id=123456, perm_id=123457)
    rig.store.update_protection(rig.clock.now(), pid, tp_intent_id=req.intent_id,
                                tp_stage=str(Purpose.TP1), tp_qty=1)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 2


def test_B20_missing_perm_rejected_text_stays_conservative_for_release(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid, raw="Cancelled", message=REJECT_ZH, code=202,
                           order__permId=0, status__permId=0)
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    adapter._on_error(trade.order.orderId, 202, REJECT_ZH, trade.contract)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    _manual_flat(rig, pid)
    _release(rig)
    assert rig.store.position(pid)["status"] == "OPEN"
    assert rig.store.protection(pid)["perm_id"] is None
    assert rig.store.flat_point("AAPL") is None
    assert rig.store.control("frozen:AAPL") is not None


def test_B20_operator_hold_is_not_cleared_by_matching_native_terminal_snapshot(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    row = rig.store.protection(pid)
    rig.store.hold_intent(rig.clock.now(), row["intent_id"], "operator must resolve conflicting evidence")
    adapter, trade = _wire(rig, pid, raw="Cancelled", message=REJECT_ZH, code=202)
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    _manual_flat(rig, pid)
    _release(rig)
    intent = rig.store.intent(row["intent_id"])
    assert intent["hold_reason"]
    assert rig.store.position(pid)["status"] == "OPEN"
    assert rig.store.flat_point("AAPL") is None


def test_B20_bare_inactive_without_refusal_evidence_is_unknown_not_rejected_card(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    row = rig.store.protection(pos["position_id"])
    rig.channel.messages.clear()
    rig.engine._on_urgent_broker_event(StatusEvent(row["intent_id"], "Inactive", 0, 20,
                                                 row["broker_order_id"], row["perm_id"]))
    assert rig.store.protection(pos["position_id"])["state"] == "UNKNOWN"
    assert not any("拒绝" in title for title, _ in _pages(rig))
    assert any("无法核实" in title for title, _ in _pages(rig, "P0"))


def test_B20_explicit_202_with_wrong_native_account_is_not_trusted_rejection(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid, raw="Cancelled", message=REJECT_ZH, code=202,
                           order__account="wrong-account")
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    adapter._on_error(trade.order.orderId, 202, REJECT_ZH, trade.contract)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    assert rig.store.protection(pid)["state"] != "REJECTED"
    assert rig.store.protection(pid)["perm_id"] is None
    assert not any("券商拒绝了保护单" in title for title, _ in _pages(rig))


def test_B20_requested_normal_cancel_202_cannot_become_rejection(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    pid = pos["position_id"]
    row = rig.store.protection(pid)
    rig.store.update_protection(rig.clock.now(), pid, state="CANCEL_PENDING")
    rig.store.update_intent(rig.clock.now(), row["intent_id"], status=IntentStatus.CANCEL_REQUESTED)
    adapter, trade = _wire(rig, pid, raw="Cancelled", message="Order canceled", code=202)
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    rig.channel.messages.clear()
    _deliver_wire(rig, adapter, trade)
    adapter._on_error(trade.order.orderId, 202, "Order canceled", trade.contract)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    assert rig.store.protection(pid)["state"] != "REJECTED"
    assert not any("拒绝" in title for title, _ in _pages(rig))


@pytest.mark.parametrize("mismatch", [
    {"contract__currency": "EUR"}, {"contract__currency": None},
    {"contract__secType": "OPT"}, {"order__totalQuantity": 21},
    {"order__totalQuantity": 19},
    {"status__filled": 0.5, "status__remaining": 19.5},
], ids=["foreign-currency", "missing-currency", "non-stock", "oversized-total", "undersized-total", "fractional-status"])
def test_B20_review_native_contract_and_quantity_must_match_persisted_request(tmp_path, monkeypatch, mismatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid, **mismatch)
    _deliver_wire(rig, adapter, trade)
    assert rig.store.protection(pid)["perm_id"] is None
    assert rig.store.protection(pid)["state"] != "WORKING"
    assert rig.store.protection(pid)["confirmed_at"] is None


def test_B20_review_pending_remote_delivery_after_restart_must_retry_P0(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    arrived, release = threading.Event(), threading.Event()

    class BlockingChannel(MemoryChannel):
        def send(self, subject, body):
            arrived.set()
            assert release.wait(5)
            super().send(subject, body)

    old_channel = BlockingChannel()
    old_alerter = Alerter([old_channel], synchronous=False)
    rig.channel = old_channel
    rig.alerter = old_alerter
    rig.engine.alerter = old_alerter
    try:
        _page_unknown(rig, pid)
        assert arrived.wait(2)
        assert old_channel.messages == []
        rig.advance(seconds=1)
        _restart(rig, tmp_path)
        _page_unknown(rig, pid, "previous process died before delivery was acknowledged")
        assert len(_pages(rig, "P0")) == 1
        assert _pages(rig, "P1") == []
    finally:
        release.set()
        old_alerter._queue.put(None)
        old_alerter._worker.join(timeout=2)
        assert not old_alerter._worker.is_alive()


def test_B20_review_failed_reminder_retries_as_P1_and_preserves_successful_P0(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1
    rig.advance(minutes=30)
    rig.channel.fail = True
    _page_unknown(rig, pid)
    assert _pages(rig, "P1") == []
    rig.channel.fail = False
    rig.advance(seconds=1)
    _page_unknown(rig, pid, "retry the failed reminder")
    assert len(_pages(rig, "P0")) == 1
    assert len(_pages(rig, "P1")) == 1
    assert "仍未解决" in " ".join(_pages(rig, "P1")[0])
    rig.advance(minutes=29)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P1")) == 1


def test_B20_review_REJECTED_absence_and_all_deadlines_preserve_one_incident_and_gap_history(
        tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid)
    _deliver_wire(rig, adapter, trade)
    trade.orderStatus.status = "Cancelled"
    trade.log.append(SimpleNamespace(time=rig.clock.now(), status="Cancelled", message=REJECT_ZH, errorCode=202))
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    row = rig.store.protection(pid)
    assert row["state"] == "REJECTED"
    assert len(_pages(rig, "P0")) == 1
    assert "未保护=待核实" in _pages(rig, "P0")[0][1]
    original_gap = row["gap_a_started_at"]
    assert original_gap
    rig.advance(seconds=20)
    rig.engine._protection_progress(rig.clock.now())
    rig.engine._hard_protection_deadlines(rig.clock.now())
    rig.engine._preflight_protection_deadlines(rig.clock.now())
    assert rig.store.protection(pid)["state"] == "REJECTED"
    assert rig.store.protection(pid)["gap_a_started_at"] == original_gap
    # Complete three-source quantity proof is material; clock-only checks are not.
    assert len(_pages(rig, "P0")) == 2
    held = rig.store.position(pid)["qty_open"]
    verified = _pages(rig, "P0")[-1][1]
    assert f"持仓数量={held} 股" in verified
    assert "保护单已确认=0 股" in verified and f"未保护={held} 股" in verified
    assert "未保护=待核实" not in verified
    assert "券商已取消保护单" in _pages(rig, "P0")[-1][0]
    assert _pages(rig, "P1") == []
    assert rig.store.control("frozen:AAPL") is not None
    for _ in range(3):
        rig.engine._protection_progress(rig.clock.now())
        rig.engine._hard_protection_deadlines(rig.clock.now())
        rig.engine._preflight_protection_deadlines(rig.clock.now())
        assert len(_pages(rig, "P0")) == 2
        assert _pages(rig, "P1") == []
    rig.advance(minutes=30)
    rig.engine._protection_progress(rig.clock.now())
    assert len(_pages(rig, "P0")) == 2
    assert _pages(rig, "P1") == []
    assert _pages(rig, "P0")[-1][1] == verified
    assert rig.store.protection(pid)["state"] == "REJECTED"
    assert rig.store.protection(pid)["gap_a_started_at"] == original_gap


def test_B20_review_lost_external_cache_after_restart_is_not_evidence_of_resolution(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    external = OrderRequest("external-seller-b20", "AAPL", Side.SELL, 1, 120.0, Purpose.TP1)
    rig.broker._register(external)
    rig.engine._note_working(rig.clock.now(), rig.broker.open_orders())
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1
    rig.advance(seconds=1)
    _restart(rig, tmp_path)
    assert rig.engine._working_at is None
    _page_unknown(rig, pid, "external order cache was lost at process restart")
    assert _pages(rig) == []
    # Only a completed fresh snapshot may report the external order gone.
    rig.broker.orders[external.intent_id].status = "Cancelled"
    rig.engine._note_working(rig.clock.now(), rig.broker.open_orders())
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1


def test_B20_review_recovery_is_durable_and_same_failure_after_restart_is_new_P0(tmp_path):
    rig, pid = _uncertain_working(tmp_path)
    _page_unknown(rig, pid)
    row = rig.store.protection(pid)
    rig.store.update_protection(rig.clock.now(), pid, state="MODIFY_PENDING",
                                 pending_qty=row["requested_qty"], pending_trigger=row["trigger_price"],
                                 pending_limit=row["limit_price"], status_seen_generation=row["modify_generation"])
    rig.engine._protection_progress(rig.clock.now())
    assert rig.store.protection(pid)["state"] == "WORKING"
    assert rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='PROTECTION_INCIDENT_RESOLVED'").fetchone()
    _restart(rig, tmp_path)
    rig.store.update_protection(rig.clock.now(), pid, state="UNKNOWN")
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1
    assert _pages(rig, "P1") == []


def test_B20_review_UNKNOWN_already_paged_P0_cannot_emit_early_gap_warning_P1(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid)
    _deliver_wire(rig, adapter, trade)
    trade.orderStatus.status = "Cancelled"
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    assert rig.store.protection(pid)["state"] == "UNKNOWN"
    assert len(_pages(rig, "P0")) == 1
    original_gap = rig.store.protection(pid)["gap_a_started_at"]
    rig.advance(seconds=20)
    rig.engine._hard_protection_deadlines(rig.clock.now())
    rig.engine._preflight_protection_deadlines(rig.clock.now())
    assert len(_pages(rig, "P0")) == 1
    assert _pages(rig, "P1") == []
    assert rig.store.protection(pid)["gap_a_started_at"] == original_gap
    rig.advance(minutes=30)
    _page_unknown(rig, pid)
    assert len(_pages(rig, "P0")) == 1
    assert _pages(rig, "P1") == []
    assert rig.store.protection(pid)["state"] == "UNKNOWN"
    assert rig.store.protection(pid)["gap_a_started_at"] == original_gap


@pytest.mark.parametrize("mismatch", [
    {"order__account": "wrong-account"}, {"contract__currency": "EUR"},
    {"order__totalQuantity": 21}, {"status__orderId": 999},
    {"status__clientId": 999}, {"contract__conId": 999},
], ids=["native-account-conflict", "native-currency-conflict", "native-quantity-conflict",
        "native-orderStatus-orderId-conflict", "native-orderStatus-clientId-conflict", "native-contract-conflict"])
def test_B20_review_known_IDs_do_not_turn_conflicting_native_tuple_into_release_proof(tmp_path, mismatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    pid = pos["position_id"]
    row = rig.store.protection(pid)
    assert row["broker_order_id"] and row["perm_id"]
    assert rig.store.intent(row["intent_id"])["status"] != IntentStatus.CANCEL_SEEN
    adapter, trade = _wire(rig, pid, raw="Cancelled", **mismatch)
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    # orderId+permId equality cannot override contradictory native metadata.
    assert rig.store.intent(row["intent_id"])["status"] != IntentStatus.CANCEL_SEEN
    _manual_flat(rig, pid)
    _release(rig)
    assert rig.store.position(pid)["status"] == "OPEN"
    assert rig.store.control("frozen:AAPL") is not None
    assert rig.store.flat_point("AAPL") is None


def test_B20_review_bare_exact_cancel_hint_retains_existing_verified_release_contract(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    pid = pos["position_id"]
    row = rig.store.protection(pid)
    rig.broker.orders[row["intent_id"]].status = "Cancelled"
    event = StatusEvent(row["intent_id"], "Cancelled", 0, pos["qty_open"],
                        row["broker_order_id"], row["perm_id"])
    rig.engine._on_urgent_broker_event(event)
    assert rig.store.intent(row["intent_id"])["status"] == IntentStatus.CANCEL_SEEN
    _manual_flat(rig, pid)
    _release(rig)
    assert rig.store.position(pid)["status"] == "RELEASED"


def test_B20_review_partial_invalid_price_evidence_still_pages_P0_and_blocks_new_entries(
        tmp_path, monkeypatch):
    """Candidate regression: the real invalid-price path passes a partial dict."""
    from v6lite.tests.test_batch17_l3_recovery import _closed_partial_entry, _late_callback

    rig, entry, closed, old = _closed_partial_entry(tmp_path)
    original_record = rig.store.record_fill
    original_page = rig.engine._protection_page
    partial_rows = []

    def invalidate_booked_recovery(now, fill, **kwargs):
        outcome = original_record(now, fill, **kwargs)
        if fill.intent_id == entry.intent_id and outcome.inserted:
            pid = rig.store.intent(entry.intent_id)["recovery_position_id"]
            rig.store.conn.execute("UPDATE positions SET stop=0 WHERE position_id=?", (pid,))
        return outcome

    def observe_real_partial_row(now, level, kind, row, detail):
        if kind == "invalid price":
            assert isinstance(row, dict) and "state" not in row
            partial_rows.append(row)
        return original_page(now, level, kind, row, detail)

    monkeypatch.setattr(rig.store, "record_fill", invalidate_booked_recovery)
    monkeypatch.setattr(rig.engine, "_protection_page", observe_real_partial_row)
    gtc_before = len(rig.broker.sent_protections)
    messages_before = len(rig.channel.messages)
    fill = _late_callback(rig, entry)
    [pos] = rig.store.open_positions()
    assert partial_rows and partial_rows[0]["position_id"] == pos["position_id"]
    assert pos["qty_open"] == fill.quantity == 5
    assert pos["position_id"] != closed["position_id"]
    assert any("P0" in title and "成交后无法建立有效保护价" in title
               for title, _ in rig.channel.messages[messages_before:])
    assert rig.store.protection(pos["position_id"]) is None
    assert len(rig.broker.sent_protections) == gtc_before
    assert rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    rig.engine.process_protection_priority()
    assert len(rig.broker.sent_protections) == gtc_before


def test_B20_review_explicit_202_before_terminal_status_survives_blank_Cancelled_log_entry(
        tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid)
    _deliver_wire(rig, adapter, trade)
    # Native warning may precede the terminal orderStatus. The same Trade
    # retains its broker reason even if the last status-log entry is blank.
    trade.log.append(SimpleNamespace(time=rig.clock.now(), status="PreSubmitted",
                                     message=REJECT_ZH, errorCode=202))
    adapter._on_error(trade.order.orderId, 202, REJECT_ZH, trade.contract)
    for event in adapter.drain_events():
        rig.engine._on_urgent_broker_event(event)
    assert rig.store.protection(pid)["state"] != "REJECTED"  # no terminal proof yet
    trade.orderStatus.status = "Cancelled"
    trade.log.append(SimpleNamespace(time=rig.clock.now(), status="Cancelled", message="", errorCode=0))
    rig.broker.orders[trade.order.orderRef].status = "Cancelled"
    _deliver_wire(rig, adapter, trade)
    assert rig.store.protection(pid)["state"] == "REJECTED"
    assert any("券商已取消保护单" in title
               for title, _ in _pages(rig, "P0"))
    assert not any("保护单状态无法核实" in title for title, _ in _pages(rig, "P0"))
    assert len(rig.broker.sent_protections) == 1
    assert rig.broker.modified_protections == []

"""Batch 22: durable stop-crossing incidents, TEMP/FakeBroker only.

Imports and fixture setup exist in B20.  A baseline failure must describe the
missing behaviour, rather than depend on a new production class or STP field.
No test writes an episode, incident, operator control, or broker status directly.
"""
from __future__ import annotations

import json
import socket
from dataclasses import replace
from datetime import timedelta

import pytest

from v6lite.config import ProtectionConfig
from v6lite.models import OrderRequest, ProtectionRequest, Purpose, Side
from v6lite.tests.helpers import Rig


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("trigger incident tests may not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def _open(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.broker.submit_market = lambda *args, **kwargs: pytest.fail("MKT is forbidden")
    rig.open_aapl()
    [pos] = rig.store.open_positions()
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "WORKING"
    assert row["confirmed_remaining"] == pos["qty_open"] == 20
    assert all(row[field] for field in ("intent_id", "broker_order_id", "perm_id", "con_id"))
    rig.channel.messages.clear()
    _capacity(rig)
    return rig, pos["position_id"], dict(row)


def _writes(rig):
    return tuple(len(items) for items in (rig.broker.sent, rig.broker.sent_protections,
                                         rig.broker.modified_protections, rig.broker.cancels))


def _capacity(rig):
    """S+T+X <= P, retaining an unacknowledged modification's old maximum.

    B20 may use STP LMT for PROTECT; this file isolates incident behaviour.
    Non-PROTECT orders still have to be limit orders with positive prices.
    """
    held = {p.symbol: int(p.quantity) for p in rig.broker.positions_fresh()}
    sells = {}
    for ref, order in rig.broker.orders.items():
        req = order.req
        if isinstance(req, ProtectionRequest):
            is_sell = True
        else:
            assert isinstance(req, OrderRequest)
            assert getattr(req, "order_type", "LMT") == "LMT"
            assert req.limit_price is not None and req.limit_price > 0
            is_sell = req.side is Side.SELL
        if not is_sell or order.status in {"Cancelled", "ApiCancelled", "Inactive", "Filled"}:
            continue
        maximum = max(0, order.remaining)
        pending = rig.broker.protection_pending_modifications.get(ref)
        if pending is not None:
            maximum = max(maximum, pending.quantity - order.filled)
        sells[req.symbol] = sells.get(req.symbol, 0) + maximum
    assert all(qty <= held.get(symbol, 0) for symbol, qty in sells.items()), (sells, held)
    assert all(order.order_type == "LMT" for order in rig.broker.open_orders()
               if rig.store.intent(order.intent_id)["purpose"] != Purpose.PROTECT)


def _observe(rig, row, price=None):
    # This price is just below the stop, above B20's old stop-limit floor.
    # B20 must fail the requested episode behaviour, not its below-limit branch.
    rig.px(row["symbol"], row["trigger_price"] - .01 if price is None else price)
    rig.engine._monitor_protection_marks(rig.clock.now())
    _capacity(rig)


def _pages(rig, level):
    return [(title, body) for title, body in rig.channel.messages
            if level in title and "未成交" in title]


def _recoveries(rig):
    return [(title, body) for title, body in rig.channel.messages
            if "券商保护事件已解除" in title]


def _incident_key(pid):
    return f"protect_incident:{pid}:trigger unfilled"


def _episode(rig, pid):
    raw = rig.store.control(f"protect_trigger:{pid}")
    return json.loads(raw) if raw is not None else None


def _reach_timeout(rig, row):
    _observe(rig, row)
    assert not _pages(rig, "P0"), "a first crossing is not an immediate timeout"
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds)
    _observe(rig, row)


def test_B22_trigger_rebound_does_not_erase_first_crossing_or_timeout(tmp_path):
    rig, pid, row = _open(tmp_path)
    before = _writes(rig)
    started_at = rig.clock.now().isoformat()
    _observe(rig, row)
    assert not _pages(rig, "P0")
    rig.advance(seconds=10)
    _observe(rig, row, row["trigger_price"] + .50)
    assert not _pages(rig, "P0")
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds - 10)
    _observe(rig, row, row["trigger_price"] + .50)
    assert len(_pages(rig, "P0")) == 1, "a triggered STP remainder stays unfilled after a rebound"
    episode = _episode(rig, pid)
    assert episode is not None and episode["started_at"] == started_at
    assert rig.store.position(pid)["qty_open"] == rig.store.protection(pid)["confirmed_remaining"] == 20
    assert _recoveries(rig) == [] and _writes(rig) == before


def test_B22_trigger_failed_P0_retries_actual_delivery_then_30_minute_P1(tmp_path):
    rig, pid, row = _open(tmp_path)
    before = _writes(rig)
    _observe(rig, row)
    rig.channel.fail = True
    failures_before = rig.alerter.delivery_stats(rig.clock.now().date())["failed"]
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds)
    _observe(rig, row)
    assert not _pages(rig, "P0"), "failed delivery must not count as an operator notification"
    assert rig.alerter.delivery_stats(rig.clock.now().date())["failed"] > failures_before
    raw = rig.store.control(_incident_key(pid))
    assert raw is not None and not json.loads(raw).get("p0_sent", False)

    rig.channel.fail = False
    rig.advance(seconds=1)
    _observe(rig, row)
    assert len(_pages(rig, "P0")) == 1, "the first successful P0 must retry after channel recovery"
    raw = rig.store.control(_incident_key(pid))
    assert raw is not None and json.loads(raw)["p0_sent"] is True
    rig.advance(seconds=1)
    _observe(rig, row)
    rig.advance(minutes=29, seconds=58)
    _observe(rig, row)
    assert len(_pages(rig, "P0")) == 1 and _pages(rig, "P1") == []
    rig.advance(seconds=1)
    _observe(rig, row)
    [reminder] = _pages(rig, "P1")
    assert "仍未解决" in reminder[0] and "20" in reminder[1]
    rig.advance(seconds=1)
    _observe(rig, row)
    assert len(_pages(rig, "P0")) == len(_pages(rig, "P1")) == 1
    assert _recoveries(rig) == [] and _writes(rig) == before


def test_B22_fresh_full_coverage_cannot_resolve_triggered_unfilled_remainder(tmp_path):
    rig, pid, row = _open(tmp_path)
    before = _writes(rig)
    _reach_timeout(rig, row)
    assert len(_pages(rig, "P0")) == 1
    incident = rig.store.control(_incident_key(pid))
    assert incident is not None
    started_at = _episode(rig, pid)["started_at"]
    for _ in range(2):
        current = rig.store.protection(pid)
        held, own, others, complete = rig.engine._protection_evidence(current)
        assert complete and not others and len(own) == 1
        assert own[0].remaining == held == current["confirmed_remaining"] == 20
        assert rig.engine._protection_order_valid(current, own[0])
        rig.engine._protection_resolve_if_covered(rig.clock.now(), current, own[0], held, others, complete)
        _capacity(rig)
    assert rig.store.control(_incident_key(pid)) == incident, "coverage is not execution of a triggered STP"
    assert _episode(rig, pid)["started_at"] == started_at
    assert _recoveries(rig) == []
    assert not rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='PROTECTION_INCIDENT_RESOLVED'").fetchone()
    assert len(_pages(rig, "P0")) == 1 and _writes(rig) == before


def test_B22_actual_full_stop_execution_resolves_episode_once_without_rebooking(tmp_path):
    rig, pid, row = _open(tmp_path)
    _reach_timeout(rig, row)
    assert len(_pages(rig, "P0")) == 1 and rig.store.control(_incident_key(pid)) is not None
    before = _writes(rig)
    fill = rig.broker.fill(row["intent_id"], price=row["trigger_price"] - .50, duplicate=True)
    _capacity(rig)
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    rig.tick()
    _capacity(rig)
    pos = rig.store.position(pid)
    assert pos["status"] == "CLOSED" and pos["qty_open"] == 0
    assert rig.broker.positions_fresh() == []
    assert rig.store.intent_filled_qty(row["intent_id"]) == fill.quantity == 20
    assert rig.store.control(_incident_key(pid)) is None, "an actual fully executed stop may resolve the incident"
    assert _episode(rig, pid) is None
    assert len([(title, body) for title, body in rig.channel.messages if "止损已成交" in title]) == 1
    assert len(_recoveries(rig)) <= 1
    audit = rig.store.conn.execute("SELECT payload FROM audit WHERE kind='PROTECT_FILL'").fetchall()
    assert len([event for event in audit if json.loads(event["payload"])["exec_id"] == fill.exec_id]) == 1
    realized = pos["realized_pnl"]
    messages = list(rig.channel.messages)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    rig.engine._protection_timers(rig.clock.now())
    assert rig.store.position(pid)["realized_pnl"] == realized
    assert rig.channel.messages == messages and len(_pages(rig, "P0")) == 1
    assert _writes(rig) == before


def _lose_quote(rig, symbol, mode):
    if mode == "missing":
        rig.broker.quotes.pop(symbol, None)
    else:
        quote = rig.broker.quotes[symbol]
        old = rig.clock.now() - timedelta(minutes=10)
        rig.broker.quotes[symbol] = replace(quote, ts=old, last_ts=old)


@pytest.mark.parametrize("quote_state", ["missing", "stale"])
def test_B22_existing_trigger_deadline_advances_without_fresh_quote_but_cannot_invent_crossing(tmp_path, quote_state):
    rig, pid, row = _open(tmp_path)
    before = _writes(rig)
    # A stored below-stop price is not proof of a new crossing if the quote is
    # missing or stale. Rig.advance refreshes quotes, so invalidate AFTER it.
    rig.px(row["symbol"], row["trigger_price"] - .01)
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds)
    _lose_quote(rig, row["symbol"], quote_state)
    rig.tick()
    assert _episode(rig, pid) is None and _pages(rig, "P0") == []

    _observe(rig, row)  # a genuinely fresh quote establishes the episode
    started_at = rig.clock.now().isoformat()
    episode = _episode(rig, pid)
    assert episode is not None and episode["started_at"] == started_at
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds)
    _lose_quote(rig, row["symbol"], quote_state)
    rig.tick()  # existing public loop; no new mark or direct alert call
    assert len(_pages(rig, "P0")) == 1, "loss of quotes cannot suspend an existing RTH deadline"
    assert _episode(rig, pid)["started_at"] == started_at
    assert rig.store.position(pid)["qty_open"] == rig.store.protection(pid)["confirmed_remaining"] == 20
    assert _recoveries(rig) == []
    _capacity(rig)
    assert _writes(rig) == before

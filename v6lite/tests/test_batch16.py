"""Batch 16 broker-protection regressions; all broker events are synthetic."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.models import OrderRequest, Purpose, Side
from v6lite.tests.helpers import Rig, make_cfg
from v6lite.tests.test_batch15 import _entry_and_fill, _protected_rig, _protection_orders


def _position_and_protection(rig):
    position = rig.store.open_positions()[0]
    return position, rig.store.protection(position["position_id"])


def _assert_gross_without_external(rig, *, own_tp_remaining=0):
    positions = rig.store.open_positions()
    held = int(positions[0]["qty_open"]) if positions else 0
    row = rig.store.protections()[0]
    possible = max(int(row["confirmed_remaining"]), int(row["pending_max_remaining"]))
    assert possible + own_tp_remaining <= held, (dict(row), held, own_tp_remaining)
    assert not [r for r in rig.broker.sent if getattr(r, "order_type", "") == "MKT"]


def _external_sell_pages(rig):
    return [subject for subject, _ in rig.channel.messages if "托管标的出现外部卖单" in subject]


@pytest.mark.parametrize("stale", [False, True], ids=["fresh_snapshot", "stale_snapshot"])
@pytest.mark.parametrize("fill_qty", [5, None], ids=["partial_5", "full_20"])
def test_B16_F1_own_protection_fill_never_cancels_or_modifies_itself(tmp_path, stale, fill_qty):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    held_before = int(rig.store.open_positions()[0]["qty_open"])
    assert held_before == req.quantity
    if stale:
        rig.engine._working_at = rig.clock.now() - timedelta(seconds=6)
    cancels_before = len(rig.broker.cancels)
    modifications_before = len(rig.broker.modified_protections)
    external_before = len(_external_sell_pages(rig))

    rig.broker.fill(req.intent_id, qty=fill_qty)
    rig.engine._drain(rig.clock.now())

    row = rig.store.protections()[0]
    remaining = held_before - (fill_qty or held_before)
    assert int(row["confirmed_remaining"]) == remaining
    assert int(row["pending_max_remaining"]) == remaining
    assert len(rig.broker.cancels) == cancels_before
    assert len(rig.broker.modified_protections) == modifications_before
    assert len(_external_sell_pages(rig)) == external_before
    assert rig.broker.orders[req.intent_id].remaining == remaining
    if remaining == 0:
        assert row["state"] == "FILLED"
    assert any("止损已成交" in subject and str(fill_qty or held_before) in body
               and str(remaining) in body for subject, body in rig.channel.messages)
    _assert_gross_without_external(rig)


@pytest.mark.parametrize("stale", [False, True], ids=["fresh_snapshot", "stale_snapshot"])
def test_B16_F1_normal_full_TP1_fill_keeps_remaining_GTC(tmp_path, stale):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos, row = _position_and_protection(rig)
    held = int(pos["qty_open"])
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    req = _protection_orders(rig)[0]
    assert rig.broker.orders[req.intent_id].remaining == held - tp.quantity
    _assert_gross_without_external(rig, own_tp_remaining=tp.quantity)
    if stale:
        rig.engine._working_at = rig.clock.now() - timedelta(seconds=6)
    cancels_before = len(rig.broker.cancels)
    modifications_before = len(rig.broker.modified_protections)
    external_before = len(_external_sell_pages(rig))

    rig.broker.fill(tp.intent_id, qty=tp.quantity)
    rig.engine._drain(rig.clock.now())

    assert int(rig.store.position(pos["position_id"])["qty_open"]) == held - tp.quantity
    assert rig.broker.orders[req.intent_id].remaining == held - tp.quantity
    assert len(rig.broker.cancels) == cancels_before
    assert len(rig.broker.modified_protections) == modifications_before
    assert len(_external_sell_pages(rig)) == external_before
    _assert_gross_without_external(rig)


@pytest.mark.parametrize("stale", [False, True], ids=["fresh_snapshot", "stale_snapshot"])
def test_B16_F1_GTC_fills_completely_while_TP_remains_working(tmp_path, stale):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos, _ = _position_and_protection(rig)
    held = int(pos["qty_open"])
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    protect = _protection_orders(rig)[0]
    protected_qty = held - tp.quantity
    assert rig.broker.orders[protect.intent_id].remaining == protected_qty
    _assert_gross_without_external(rig, own_tp_remaining=tp.quantity)
    if stale:
        rig.engine._working_at = rig.clock.now() - timedelta(seconds=6)
    cancels_before = len(rig.broker.cancels)
    modifications_before = len(rig.broker.modified_protections)
    external_before = len(_external_sell_pages(rig))

    rig.broker.fill(protect.intent_id, qty=protected_qty)
    rig.engine._drain(rig.clock.now())

    row = rig.store.protection(pos["position_id"])
    assert int(rig.store.position(pos["position_id"])["qty_open"]) == tp.quantity
    assert row["state"] == "FILLED"
    assert int(row["confirmed_remaining"]) == int(row["pending_max_remaining"]) == 0
    assert rig.broker.orders[tp.intent_id].remaining == tp.quantity
    assert len(rig.broker.cancels) == cancels_before
    assert len(rig.broker.modified_protections) == modifications_before
    assert len(_external_sell_pages(rig)) == external_before
    _assert_gross_without_external(rig, own_tp_remaining=tp.quantity)

    # Filled protection no longer exists as a working order, but B remains
    # open while the DAY TP can still sell the six remaining shares.
    rig.advance(seconds=10)
    rig.tick()
    assert rig.broker.cancels == [tp.intent_id]
    assert len(_protection_orders(rig)) == 1  # no replacement before TP cancel proof
    assert rig.store.intent(protect.intent_id)["status"] != "CANCELLED"
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    rig.px("AAPL", pos["entry_price"])
    rig.tick()
    replacements = _protection_orders(rig)
    assert len(replacements) == 2
    assert replacements[1].intent_id != protect.intent_id
    assert rig.broker.orders[replacements[1].intent_id].remaining == tp.quantity
    assert rig.store.intent(protect.intent_id)["status"] != "CANCELLED"
    _assert_gross_without_external(rig)


def test_B16_F1_GTC_and_TP_both_fill_without_replacement(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos, _ = _position_and_protection(rig)
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    protect = _protection_orders(rig)[0]
    rig.broker.fill(protect.intent_id, qty=rig.broker.orders[protect.intent_id].remaining)
    rig.engine._drain(rig.clock.now())
    assert rig.store.protection(pos["position_id"])["state"] == "FILLED"
    rig.broker.fill(tp.intent_id, qty=tp.quantity)
    rig.engine._drain(rig.clock.now())
    assert rig.store.position(pos["position_id"])["status"] == "CLOSED"
    rig.advance(seconds=11)
    rig.tick()
    assert len(_protection_orders(rig)) == 1
    assert not rig.broker.cancels
    _assert_gross_without_external(rig)


def test_B16_F1_normal_TP_fill_ignores_stale_fresh_open_order_remaining(tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    pos, _ = _position_and_protection(rig)
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    protect = _protection_orders(rig)[0]
    stale_tp = next(o for o in rig.broker.open_orders() if o.intent_id == tp.intent_id)
    original_open_orders = rig.broker.open_orders
    def delayed_open_orders():
        # reqAllOpenOrders can lag execDetails; ledger has the completed TP.
        return original_open_orders() + [stale_tp]
    monkeypatch.setattr(rig.broker, "open_orders", delayed_open_orders)
    before_modifies = len(rig.broker.modified_protections)
    before_cancels = len(rig.broker.cancels)
    before_external = len(_external_sell_pages(rig))
    rig.broker.fill(tp.intent_id, qty=tp.quantity)
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    row = rig.store.protection(pos["position_id"])
    assert int(rig.store.position(pos["position_id"])["qty_open"]) == 14
    assert int(row["confirmed_remaining"]) == 14
    assert rig.store.intent_filled_qty(tp.intent_id) == tp.quantity
    assert len(rig.broker.modified_protections) == before_modifies
    assert len(rig.broker.cancels) == before_cancels
    assert len(_external_sell_pages(rig)) == before_external
    _assert_gross_without_external(rig)


def _reduced_before_tp(rig):
    _entry_and_fill(rig)
    pos, row = _position_and_protection(rig)
    held = int(pos["qty_open"])
    rig.broker.protection_auto_status = False
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    req = _protection_orders(rig)[0]
    assert rig.store.protection(pos["position_id"])["state"] == "REDUCE_PENDING"
    reduced = rig.broker.protection_pending_modifications[req.intent_id].quantity
    assert 0 < reduced < held
    return pos, req, held, reduced


@pytest.mark.parametrize("cause", ["no_quote", "stale_quote", "non_marketable_limit", "definite_rejection"])
def test_B16_F2_definitely_unsent_nonfinal_TP_restores_same_GTC(tmp_path, monkeypatch, cause):
    rig = _protected_rig(tmp_path)
    pos, req, held, reduced = _reduced_before_tp(rig)
    if cause == "no_quote":
        rig.broker.quotes.pop("AAPL")
    elif cause == "stale_quote":
        q = rig.broker.quotes["AAPL"]
        rig.broker.set_quote("AAPL", bid=q.bid, ask=q.ask, last=q.last,
                             ts=rig.clock.now() - timedelta(minutes=5))
    elif cause == "non_marketable_limit":
        monkeypatch.setattr("v6lite.exits.sell_limit", lambda bid, band: bid + 0.01)
    else:
        rig.broker.fail_next_submit = BrokerError("synthetic definite rejection", maybe_sent=False)

    rig.broker.confirm_protection_modify(req.intent_id)
    rig.engine._drain(rig.clock.now())
    row = rig.store.protection(pos["position_id"])
    assert row["state"] in {"TP_NEEDS_RESTORE", "RESTORE_PENDING", "WORKING"}
    assert int(row["confirmed_remaining"]) == reduced
    assert not [r for r in rig.broker.sent if r.side is Side.SELL]
    _assert_gross_without_external(rig)

    # The no-quote case needs a fresh quote for later independent checks.
    rig.px("AAPL", pos["tp1"] + 1)
    rig.broker.protection_auto_status = True
    rig.engine.process_protection_priority()
    rig.tick()
    assert len(_protection_orders(rig)) == 1
    assert rig.broker.orders[req.intent_id].req.quantity == held
    assert int(rig.store.protection(pos["position_id"])["confirmed_remaining"]) == held
    assert not [r for r in rig.broker.sent if r.side is Side.SELL]
    audits = [json.loads(a["payload"]) for a in rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='PROTECT_GAP'").fetchall()]
    assert any(a.get("gap_kind") == "B" and a.get("outcome") == "tp_not_sent"
               and a.get("secs", 31) < 30 for a in audits), audits
    _assert_gross_without_external(rig)


def test_B16_F2_maybe_sent_TP_must_not_increase_protection(tmp_path):
    rig = _protected_rig(tmp_path)
    pos, req, held, reduced = _reduced_before_tp(rig)
    rig.broker.fail_next_submit = BrokerError("synthetic unknown acknowledgement", maybe_sent=True)
    rig.broker.confirm_protection_modify(req.intent_id)
    rig.engine._drain(rig.clock.now())
    rig.tick()
    assert rig.broker.orders[req.intent_id].req.quantity == reduced
    assert not [m for m in rig.broker.modified_protections if m.quantity == held]
    _assert_gross_without_external(rig, own_tp_remaining=held - reduced)


def test_B16_F3_real_Submitted_status_pages_P1_once_per_crossing(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    assert rig.broker.orders[req.intent_id].status == "Submitted"
    mark = round(req.trigger_price - 0.01, 2)
    rig.px("AAPL", mark)
    rig.advance(seconds=1)
    rig.tick()
    title = "已到止损价，券商止损市价单应在成交"
    once = sum(title in subject for subject, _ in rig.channel.messages)
    assert once == 1
    rig.advance(seconds=1)
    rig.tick()
    assert sum(title in subject for subject, _ in rig.channel.messages) == 1
    assert not [r for r in rig.broker.sent if r.side is Side.SELL]
    assert req.intent_id not in rig.broker.cancels


def test_B16_F3_Submitted_below_stop_waits_for_unfilled_timeout_P0(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    rig.px("AAPL", req.trigger_price - 10.0)  # STP has no lower limit-price shortcut.
    rig.advance(seconds=1)
    rig.tick()
    assert not any("止损已触发但未成交" in subject for subject, _ in rig.channel.messages)
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds - 1)
    rig.tick()
    assert not any("止损已触发但未成交" in subject for subject, _ in rig.channel.messages)
    rig.advance(seconds=1)
    rig.tick()
    assert any("止损已触发但未成交" in subject for subject, _ in rig.channel.messages)
    assert not [r for r in rig.broker.sent if r.side is Side.SELL]


def test_B16_F3_unfilled_P0_timeout_is_configurable(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(trigger_unfilled_p0_seconds=3))
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    rig.px("AAPL", round(req.trigger_price - 0.01, 2))
    rig.advance(seconds=1)
    rig.tick()
    assert not any("止损已触发但未成交" in s for s, _ in rig.channel.messages)
    rig.advance(seconds=3)
    rig.tick()
    assert any("止损已触发但未成交" in s for s, _ in rig.channel.messages)


def test_B16_F4_steady_evidence_uses_periodic_queries_and_single_failure_is_not_P0(tmp_path, monkeypatch):
    cfg = ProtectionConfig(steady_check_seconds=15, evidence_fail_p0_seconds=60)
    rig = Rig(tmp_path, protection=cfg)
    _entry_and_fill(rig)
    counters = {name: 0 for name in ("open_orders", "positions_fresh", "executions_today")}
    originals = {name: getattr(rig.broker, name) for name in counters}
    # FakeBroker's streamed positions() helper delegates to positions_fresh();
    # keep that cache read outside the fresh reqPositions counter.
    monkeypatch.setattr(rig.broker, "positions", originals["positions_fresh"])
    for name in counters:
        def counted(*args, _name=name, **kwargs):
            counters[_name] += 1
            return originals[_name](*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, counted)
    for _ in range(10):
        rig.advance(seconds=1)
        rig.tick()
    assert all(count < 10 for count in counters.values()), counters
    rig.broker.fail_open_orders = True
    rig.advance(seconds=15)
    rig.tick()
    rig.broker.fail_open_orders = False
    assert not any("保护单三项证据不完整" in s for s, _ in rig.channel.messages)


def test_B16_F4_new_evidence_and_trigger_thresholds_change_config_fingerprint(tmp_path):
    cfg = make_cfg(tmp_path, protection=ProtectionConfig())
    for name in ("steady_check_seconds", "evidence_fail_p0_seconds", "trigger_unfilled_p0_seconds"):
        changed = replace(cfg, protection=replace(cfg.protection,
                                                  **{name: getattr(cfg.protection, name) + 1}))
        assert changed.fingerprint() != cfg.fingerprint(), name


def test_B16_F4_continuous_steady_evidence_loss_escalates_only_after_threshold(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(
        steady_check_seconds=15, evidence_fail_p0_seconds=60))
    _entry_and_fill(rig)
    rig.broker.fail_open_orders = True
    for _ in range(3):
        rig.advance(seconds=15)
        rig.tick()
    assert not any("保护单三项证据不完整" in s for s, _ in rig.channel.messages)
    for _ in range(2):
        rig.advance(seconds=15)
        rig.tick()
    assert any("保护单三项证据不完整" in s for s, _ in rig.channel.messages)


def test_B16_F4_active_reduction_still_checks_three_sources_every_tick(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(steady_check_seconds=15))
    _reduced_before_tp(rig)
    count = {name: 0 for name in ("open_orders", "executions_today")}
    for name in count:
        original = getattr(rig.broker, name)
        def counted(*args, _name=name, _original=original, **kwargs):
            count[_name] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, counted)
    for _ in range(3):
        rig.advance(seconds=1)
        rig.tick()
    assert all(n >= 3 for n in count.values()), count


def test_B16_F5_protection_cards_have_distinct_titles_actions_and_chinese_quantities(tmp_path, monkeypatch):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    _, row = _position_and_protection(rig)
    kinds = ("gap A warning", "gap A overdue", "gap B overdue", "external sell",
             "rejected", "unknown", "trigger crossing", "trigger unfilled", "invalid price",
             "evidence incomplete", "shutdown incomplete")
    # This is a copy/renderer contract. Each synthetic kind must be rendered
    # independently; Batch 20 incident tests cover durable paging schedules.
    def capture_card(now, level, kind, evidence, body, card):
        rig.channel.send(card.title, card.as_plain_text())

    monkeypatch.setattr(rig.engine, "_deliver_protection_notice", capture_card)
    titles, actions, samples = [], [], []
    for kind in kinds:
        level = "P1" if kind in {"gap A warning", "trigger crossing"} else "P0"
        rig.engine._protection_page(rig.clock.now(), level, kind, row, "synthetic test detail")
        title, rendered = rig.channel.messages[-1]
        titles.append(title)
        action = rendered.split("你要做什么：", 1)[1]
        actions.append(action)
        samples.append((kind, title, rendered))
    assert len(samples) == 11
    assert any(kind == "trigger crossing" and "P1" in title
               and "已到止损价，券商止损市价单应在成交" in title
               for kind, title, _ in samples)
    for kind, _, rendered in samples:
        action = rendered.split("你要做什么：", 1)[1]
        what = rendered.split("发生了什么：", 1)[1].split("\n影响：", 1)[0]
        for label in ("持仓", "保护单", "止盈在途", "外部卖单", "未保护"):
            assert label in what, (kind, label, what)
        assert "1）" in action and "2）" in action and "不要" in action
    assert len(set(titles)) == len(kinds)
    assert len(set(actions)) == len(kinds)


def test_B16_F5_stop_unfilled_card_guides_STP_status_and_halt_check(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    req = _protection_orders(rig)[0]
    rig.px("AAPL", req.trigger_price - 0.01)
    rig.advance(seconds=1)
    rig.tick()
    assert not any("止损已触发但未成交" in subject for subject, _ in rig.channel.messages)
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds)
    rig.tick()
    matching = [body for subject, body in rig.channel.messages if "止损已触发但未成交" in subject]
    assert matching
    body = matching[-1]
    action = body.split("你要做什么：", 1)[1]
    assert str(rig.broker.orders[req.intent_id].broker_order_id) in body
    assert "停牌" in action and "交易暂停" in action and "核对" in action
    assert "原 STP" in action and "不要另发" in action and "自动重挂" in action
    assert action.index("1）") < action.index("2）") < action.index("3）")


def test_B16_F5_external_sell_card_names_real_order_and_freeze_limitation(tmp_path):
    rig = _protected_rig(tmp_path)
    _entry_and_fill(rig)
    external = OrderRequest("external-sell-b16", "AAPL", Side.SELL, 1, 110.0, Purpose.TP1)
    rig.broker._register(external)
    rig.engine._reconcile_due = True
    rig.advance(seconds=1)
    rig.tick()
    matching = [body for subject, body in rig.channel.messages if "托管标的出现外部卖单" in subject]
    assert matching
    body = matching[-1]
    action = body.split("你要做什么：", 1)[1]
    assert external.intent_id in body and "1 股" in body
    assert "冻结" in body and "撤" in action and "不要" in action
    assert "release AAPL" in action


def test_B16_F5_B_gap_card_orders_TP_cancel_before_protection_increase(tmp_path):
    rig = _protected_rig(tmp_path)
    pos, protect, held, reduced = _reduced_before_tp(rig)
    rig.broker.confirm_protection_modify(protect.intent_id)
    rig.engine._drain(rig.clock.now())
    tp = next(r for r in rig.broker.sent if r.side is Side.SELL)
    row = rig.store.protection(pos["position_id"])
    rig.engine._protection_page(rig.clock.now(), "P0", "gap B overdue", row, "synthetic overdue")
    body = rig.channel.messages[-1][1]
    action = body.split("你要做什么：", 1)[1]
    tp_order_id = rig.broker.orders[tp.intent_id].broker_order_id
    assert tp.intent_id in body and str(tp_order_id) in body
    assert str(tp.quantity) in body and str(reduced) in body
    assert "撤" in action and "TP" in action and "保护单" in action
    assert action.index("撤") < action.rindex("保护单")


def test_B16_F6_fixture_switch_enables_protection_for_paper(tmp_path, monkeypatch):
    monkeypatch.delenv("V6LITE_TEST_PROTECTION", raising=False)
    assert make_cfg(tmp_path).protection.enabled is False
    monkeypatch.setenv("V6LITE_TEST_PROTECTION", "1")
    assert make_cfg(tmp_path).protection.enabled is True


def test_B16_preflight_overdue_B_card_and_immediate_post_takeover_cancel(tmp_path):
    rig = _protected_rig(tmp_path)
    pos, protect, held, reduced = _reduced_before_tp(rig)
    rig.broker.confirm_protection_modify(protect.intent_id)
    rig.engine._drain(rig.clock.now())
    tp = next(r for r in rig.broker.sent if r.side is Side.SELL)
    tp_order_id = rig.broker.orders[tp.intent_id].broker_order_id
    row = rig.store.protection(pos["position_id"])
    assert row["state"] == "TP_ACTIVE" and row["gap_b_started_at"]
    rig.engine.took_over = False
    rig.advance(seconds=31)
    rig.engine._hard_protection_deadlines(rig.clock.now())
    assert tp.intent_id not in rig.broker.cancels
    cards = [body for subject, body in rig.channel.messages if "P0" in subject and tp.intent_id in body]
    assert cards
    assert any("5 分钟" in body and "TWS" in body and "不要另下卖单" in body
               and str(tp_order_id) in body and str(tp.quantity) in body
               and str(reduced) in body for body in cards)
    rig.engine.took_over = True
    rig.engine._hard_protection_deadlines(rig.clock.now())
    assert rig.broker.cancels == [tp.intent_id]
    assert rig.store.protection(pos["position_id"])["state"] == "TP_CANCEL_PENDING"
    _assert_gross_without_external(rig, own_tp_remaining=tp.quantity)


def _test_tp_timeout_config(tmp_path, *, test_enabled=True):
    from v6lite.tests.test_batch14 import _toml

    state_dir = tmp_path / ("paper-test" if test_enabled else "paper")
    path = tmp_path / ("paper-test.toml" if test_enabled else "paper.toml")
    content = _toml(state_dir, section=test_enabled)
    if test_enabled:
        content = content.replace("[ibkr]\n", "[ibkr]\nclient_id = 132\n")
    path.write_text(content, encoding="utf-8")
    return path, state_dir


@pytest.mark.parametrize("gate", ["formal", "bad_symbol", "blank_operator"])
def test_B16_G3_one_shot_TP_timeout_CLI_refuses_unsafe_requests(tmp_path, capsys, gate):
    from v6lite.__main__ import main

    path, _ = _test_tp_timeout_config(tmp_path, test_enabled=gate != "formal")
    symbol = "MSTR" if gate == "bad_symbol" else "AAPL"
    operator = " " if gate == "blank_operator" else "John"
    assert main(["test-tp-timeout", symbol, "--operator", operator,
                 "--config", str(path)]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_B16_G3_one_shot_TP_timeout_consumes_once_and_never_places_MKT(tmp_path, monkeypatch, capsys):
    from unittest.mock import patch

    from v6lite import __main__ as cli
    from v6lite.config import TestConfig
    from v6lite.tests.helpers import Rig, et

    rig = Rig(tmp_path, state_dir=str(tmp_path / "paper-test"), client_id=132,
              test=TestConfig(enabled=True, qty=3, stop_mult=0.3,
                              tp1_mult=0.3, tp2_mult=0.5, tp3_mult=0.8),
              protection=ProtectionConfig(), scan_times_et=("13:30",))
    rig.start()
    path, _ = _test_tp_timeout_config(tmp_path)
    before = (len(rig.broker.sent), len(rig.broker.sent_protections),
              len(rig.broker.modified_protections), len(rig.broker.cancels))
    # B29 still refuses without an open Lite position and confirmed STP.
    with patch.object(cli, "_now", rig.clock.now):
        for _ in range(2):
            assert cli.main(["test-tp-timeout", "AAPL", "--operator", "John",
                             "--config", str(path)]) == 2
    key = "test_tp_nonmarketable_once:AAPL"
    assert rig.store.control(key) is None
    captured = capsys.readouterr()
    assert "position" in captured.err + captured.out
    assert (len(rig.broker.sent), len(rig.broker.sent_protections),
            len(rig.broker.modified_protections), len(rig.broker.cancels)) == before == (0, 0, 0, 0)
    assert not [r for r in rig.broker.sent if getattr(r, "order_type", "") == "MKT"]


def test_B16_G3_one_shot_nonmarketable_TP_runs_safe_B_timeout_and_restore(tmp_path):
    from decimal import Decimal, ROUND_UP
    from unittest.mock import patch

    from v6lite import __main__ as cli
    from v6lite.config import TestConfig
    from v6lite.tests.helpers import Rig, et

    rig = Rig(tmp_path, state_dir=str(tmp_path / "paper-test"), client_id=132,
              test=TestConfig(enabled=True, qty=3, stop_mult=0.3,
                              tp1_mult=0.3, tp2_mult=0.5, tp3_mult=0.8),
              protection=ProtectionConfig(), scan_times_et=("13:30",))
    rig.start()
    path, _ = _test_tp_timeout_config(tmp_path)
    rig.at(et(10, 0))
    with patch.object(cli, "_now", rig.clock.now):
        assert cli.main(["test-entry", "AAPL", "--operator", "John",
                         "--config", str(path)]) == 0
    rig.at(et(10, 1))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    assert buy.quantity == 3
    rig.broker.fill(buy.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    pos, row = _position_and_protection(rig)
    protect = _protection_orders(rig)[0]
    assert int(pos["qty_open"]) == rig.broker.orders[protect.intent_id].remaining == 3

    before = (len(rig.broker.sent), len(rig.broker.sent_protections),
              len(rig.broker.modified_protections), len(rig.broker.cancels))
    with patch.object(cli, "_now", rig.clock.now):
        assert cli.main(["test-tp-timeout", "AAPL", "--operator", "John",
                         "--config", str(path)]) == 0
    key = "test_tp_nonmarketable_once:AAPL"
    assert rig.store.control(key) is not None
    assert (len(rig.broker.sent), len(rig.broker.sent_protections),
            len(rig.broker.modified_protections), len(rig.broker.cancels)) == before
    # The one accepted B29 request changes this exact DAY TP only;
    # physical reduction confirmation and all normal gates still apply.
    rig.px("AAPL", pos["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()

    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    quote = rig.broker.quote("AAPL")
    assert quote is not None and quote.bid is not None and quote.ask is not None
    assert quote.age_seconds(rig.clock.now()) == 0
    expected = (Decimal(str(quote.ask)) * Decimal("1.01")).quantize(Decimal(".01"), rounding=ROUND_UP)
    assert tp.quantity == 1 and Decimal(str(tp.limit_price)) == expected
    assert tp.limit_price > quote.ask
    working = next(o for o in rig.broker.open_orders() if o.intent_id == tp.intent_id)
    assert working.order_type == "LMT" and working.tif == "DAY"
    assert rig.broker.orders[protect.intent_id].remaining == 2
    assert int(rig.store.protection(pos["position_id"])["confirmed_remaining"]) == 2
    assert rig.store.control(key) is None
    _assert_gross_without_external(rig, own_tp_remaining=1)

    rig.advance(seconds=10)
    rig.tick()
    assert rig.broker.cancels == [tp.intent_id]
    assert rig.broker.orders[protect.intent_id].remaining == 2
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    assert rig.broker.orders[protect.intent_id].remaining == 3
    assert int(rig.store.protection(pos["position_id"])["confirmed_remaining"]) == 3
    rig.px("AAPL", pos["entry_price"])  # No new TP signal on the verification tick.
    rig.advance(seconds=1)
    rig.tick()
    assert len([req for req in rig.broker.sent if req.side is Side.SELL]) == 1
    assert protect.intent_id not in rig.broker.cancels
    assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]
    _assert_gross_without_external(rig)

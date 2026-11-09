"""F5 protection-card copy is readable and specific to each safety event."""

from __future__ import annotations

import re

import pytest

from v6lite.config import ProtectionConfig
from v6lite.tests.helpers import Rig


PROTECTION_KINDS = (
    "gap A warning", "gap A overdue", "gap B overdue", "preflight B overdue",
    "external sell", "rejected", "unknown", "trigger crossing",
    "trigger unfilled", "trigger suspected", "invalid price", "invalid entry price",
    "evidence incomplete", "shutdown incomplete", "late tp fill",
)

CORE_KINDS = {
    "gap A warning", "gap A overdue", "gap B overdue", "external sell",
    "rejected", "unknown", "trigger crossing", "trigger unfilled",
    "invalid price", "evidence incomplete", "shutdown incomplete",
}

IMPACT_CUES = {
    "gap A warning": ("确认", "未保护"),
    "gap A overdue": ("裸仓", "未保护"),
    "gap B overdue": ("止盈", "TP"),
    "preflight B overdue": ("接管", "连接"),
    "external sell": ("外部", "超卖"),
    "rejected": ("拒", "无效"),
    "unknown": ("未知", "核实"),
    "trigger crossing": ("止损", "触发"),
    "trigger unfilled": ("停牌", "交易暂停"),
    "trigger suspected": ("疑似", "触发"),
    "invalid price": ("价格", "保护价"),
    "invalid entry price": ("买单", "开仓"),
    "evidence incomplete": ("三项", "证据"),
    "shutdown incomplete": ("停机", "重启"),
    "late tp fill": ("迟到", "止盈"),
}


def _capture_renderer(rig, monkeypatch):
    # These two all-kinds tests examine the produced card, independently of
    # the persistent incident delivery policy covered in Batch 20.
    def capture_card(now, level, kind, evidence, body, card):
        rig.channel.send(card.title, card.as_plain_text())

    monkeypatch.setattr(rig.engine, "_deliver_protection_notice", capture_card)


def _card(rig, row, kind):
    level = "P1" if kind in {"gap A warning", "trigger crossing", "trigger suspected"} else "P0"
    rig.engine._protection_page(rig.clock.now(), level, kind, row, "synthetic evidence")
    title, rendered = rig.channel.messages[-1]
    what = rendered.split("发生了什么：", 1)[1].split("\n影响：", 1)[0]
    impact = rendered.split("\n影响：", 1)[1].split("\n你要做什么：", 1)[0]
    return title, what, impact


@pytest.mark.parametrize("kind", PROTECTION_KINDS)
def test_B16_F5_card_uses_chinese_share_labels_without_PSTXU_codewords(tmp_path, kind):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    row = rig.store.protection(position["position_id"])
    title, what, impact = _card(rig, row, kind)
    assert "AAPL" in title or "AAPL" in what
    assert "持仓" in what and "保护单" in what and "股" in what
    assert "止损价（触发后市价成交）" in what and "限价=" not in what
    assert re.search(r"\d+\s*股", what), (kind, what)
    if kind in CORE_KINDS:
        assert all(label in what for label in ("止盈在途", "外部卖单", "未保护")), (kind, what)
    # Order references such as orderId/permId stay; the five risk variables
    # must be written out as Chinese share labels for a phone notification.
    assert not re.search(r"(?<![A-Za-z])(?:P|S已确认|S可能|T|X|U)=", what), (kind, what)
    assert impact and any(cue in impact for cue in IMPACT_CUES[kind]), (kind, impact)


def test_B16_F5_every_protection_kind_has_its_own_consequence(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    row = rig.store.protection(position["position_id"])
    _capture_renderer(rig, monkeypatch)
    impacts = {kind: _card(rig, row, kind)[2] for kind in PROTECTION_KINDS}
    assert len(impacts) == len(set(impacts.values())), impacts


def test_B16_F5_every_protection_kind_has_unique_numbered_actions(tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    row = rig.store.protection(position["position_id"])
    _capture_renderer(rig, monkeypatch)
    actions = {}
    for kind in PROTECTION_KINDS:
        rig.engine._protection_page(rig.clock.now(), "P0", kind, row, "synthetic evidence")
        rendered = rig.channel.messages[-1][1]
        action = rendered.split("\n你要做什么：", 1)[1]
        assert action.index("1）") < action.index("2）") < action.index("3）"), (kind, action)
        assert "不要" in action, (kind, action)
        actions[kind] = action
    assert len(actions) == len(set(actions.values())), actions


def _assert_independent_card_uses_chinese_evidence(body, order_ref):
    what = body.split("发生了什么：", 1)[1].split("\n影响：", 1)[0]
    action = body.split("\n你要做什么：", 1)[1]
    assert order_ref in what and "待核实" in what
    assert "持仓" in what and any(label in what for label in ("保护单", "卖单", "订单")), what
    # A callback or unclaimed order may have no trustworthy share count yet;
    # say that each Chinese quantity is unknown instead of inventing shares.
    assert "股" in what or ("数量" in what and "待核实" in what), what
    assert not re.search(r"(?<![A-Za-z])(?:P|S|T|X|U)(?:已确认|可能)?=", what), what
    assert "P/S/T/X/U" not in what
    assert "remaining=" not in what
    assert action.index("1）") < action.index("2）") < action.index("3）")
    return action


def test_B16_F5_urgent_callback_failure_card_requires_restart_to_restore_entries(
        tmp_path, monkeypatch):
    from v6lite.models import Side

    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.start()
    from v6lite.tests.helpers import et
    rig.at(et(10, 0, 30))
    rig.tick()
    buy = next(req for req in rig.broker.sent if req.side is Side.BUY)
    fill = rig.broker.fill(buy.intent_id, qty=1)
    rig.broker.drain_events()
    original = rig.store.update_protection

    def fail_first_submission(*args, **kwargs):
        if kwargs.get("state") == "SUBMIT_PENDING":
            raise OSError("synthetic local write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(rig.store, "update_protection", fail_first_submission)
    with pytest.raises(OSError, match="synthetic local write failure"):
        rig.engine._on_urgent_broker_event(fill)
    body = next(body for title, body in reversed(rig.channel.messages)
                if "成交回调处理失败" in title)
    action = _assert_independent_card_uses_chinese_evidence(body, buy.intent_id)
    assert "修复后需要重启 Lite，才能恢复开仓" in action
    assert rig.engine._protection_emergency is True


@pytest.mark.parametrize("path,title_fragment", [
    ("direct_cancel", "未认领订单禁止泛型撤单"),
    ("shutdown", "停机发现未认领券商保护单"),
])
def test_B16_F5_orphan_GTC_cards_use_chinese_evidence_and_keep_order(
        tmp_path, path, title_fragment):
    from v6lite.models import ProtectionRequest

    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    orphan = ProtectionRequest("v6l-orphan-card", "unclaimed-position", "AAPL", 1,
                               position["stop"], None)
    rig.broker.submit_stop(orphan)
    if path == "direct_cancel":
        rig.engine._request_cancel(rig.clock.now(), orphan.intent_id, "synthetic generic cleanup")
    else:
        rig.engine.shutdown(grace_seconds=0)
    body = next(body for title, body in reversed(rig.channel.messages)
                if title_fragment in title)
    _assert_independent_card_uses_chinese_evidence(body, orphan.intent_id)
    assert orphan.intent_id not in rig.broker.cancels


def _assert_no_risk_variable_codewords(text):
    assert not re.search(r"(?<![A-Za-z])(?:P|S|T|X|U)(?:已确认|可能|最多)?=", text), text
    assert "P/S/T/X/U" not in text and "T+X" not in text and "S最多" not in text, text


def test_B16_F5_real_external_sell_details_are_chinese_not_risk_formulas(tmp_path, monkeypatch):
    from v6lite.models import OrderRequest, Purpose, Side

    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    details = []
    original_page = rig.engine._protection_page

    def capture(now, level, kind, row, detail):
        if kind == "external sell":
            details.append(detail)
        return original_page(now, level, kind, row, detail)

    monkeypatch.setattr(rig.engine, "_protection_page", capture)
    external = OrderRequest("external-sell-copy", "AAPL", Side.SELL, 1, 110.0, Purpose.TP1)
    rig.broker._register(external)
    rig.engine._reconcile_due = True
    rig.advance(seconds=1)
    rig.tick()
    assert len(details) >= 2, details  # external identity and gross-capacity branches
    assert any(external.intent_id in detail for detail in details)
    for detail in details:
        _assert_no_risk_variable_codewords(detail)
    rendered = [body for title, body in rig.channel.messages if "托管标的出现外部卖单" in title]
    assert rendered
    for body in rendered:
        _assert_no_risk_variable_codewords(body.split("发生了什么：", 1)[1].split("\n影响：", 1)[0])


def test_B16_F5_real_late_TP_detail_is_chinese_not_risk_formula(tmp_path, monkeypatch):
    from v6lite.models import Side

    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    rig.px("AAPL", position["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())

    details = []
    original_page = rig.engine._protection_page

    def capture(now, level, kind, row, detail):
        if kind == "late tp fill":
            details.append(detail)
        return original_page(now, level, kind, row, detail)

    monkeypatch.setattr(rig.engine, "_protection_page", capture)
    rig.broker.fill(tp.intent_id, qty=1)  # confirmed cancel may race a late execDetails
    rig.engine._drain(rig.clock.now())
    assert details
    for detail in details:
        _assert_no_risk_variable_codewords(detail)
    rendered = [body for title, body in rig.channel.messages
                if "迟到" in title and "保护" in title]
    assert rendered
    for body in rendered:
        _assert_no_risk_variable_codewords(body.split("发生了什么：", 1)[1].split("\n影响：", 1)[0])


@pytest.mark.parametrize("modify_uncertain", [False, True])
def test_B16_F5_late_TP_card_never_claims_protection_reduced_before_broker_write(
        tmp_path, monkeypatch, modify_uncertain):
    from v6lite.broker.base import BrokerError
    from v6lite.models import Side

    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    rig.px("AAPL", position["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())
    before_modifications = len(rig.broker.modified_protections)
    before_messages = len(rig.channel.messages)
    if modify_uncertain:
        rig.broker.fail_next_modify = BrokerError("synthetic uncertain modify", maybe_sent=True)

    emitted = []
    original_page = rig.engine._protection_page

    def capture(now, level, kind, row, detail):
        submitted_before_card = len(rig.broker.modified_protections) > before_modifications
        result = original_page(now, level, kind, row, detail)
        if kind == "late tp fill":
            title, body = rig.channel.messages[-1]
            emitted.append((submitted_before_card, title, body))
        return result

    monkeypatch.setattr(rig.engine, "_protection_page", capture)
    rig.broker.fill(tp.intent_id, qty=1)  # a late execDetails after confirmed cancel
    rig.engine._drain(rig.clock.now())
    assert emitted  # late-fill card must use a distinct, truthful title
    for submitted, title, body in emitted:
        if "已减量" in title:
            assert submitted, (title, body)
            assert "已获券商确认" in body, (title, body)
    if modify_uncertain:
        assert rig.store.protection(position["position_id"])["state"] == "UNKNOWN"
        assert any("保护单状态无法核实" in title
                   for title, _ in rig.channel.messages[before_messages:])
        assert all("已减量" not in title for title, _ in rig.channel.messages[before_messages:])
    else:
        assert len(rig.broker.modified_protections) > before_modifications
    assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]


def test_B16_F5_late_TP_card_distinguishes_requested_and_broker_confirmed_reduction(
        tmp_path):
    """The exact-title '已减量' is reserved for same-generation broker proof."""
    from v6lite.models import Side

    rig = Rig(tmp_path, protection=ProtectionConfig())
    rig.open_aapl()
    position = rig.store.open_positions()[0]
    protection_ref = rig.store.protection(position["position_id"])["intent_id"]
    rig.px("AAPL", position["tp1"] + 1)
    rig.advance(seconds=31)
    rig.tick()
    tp = next(req for req in rig.broker.sent if req.side is Side.SELL)
    rig.advance(seconds=10)
    rig.tick()
    assert tp.intent_id in rig.broker.cancels
    rig.broker.confirm_cancel(tp.intent_id)
    rig.engine._drain(rig.clock.now())

    rig.broker.protection_auto_status = False
    first_message = len(rig.channel.messages)
    rig.broker.fill(tp.intent_id, qty=1)
    rig.engine._drain(rig.clock.now())
    assert protection_ref in rig.broker.protection_pending_modifications
    assert rig.store.protection(position["position_id"])["state"] == "MODIFY_PENDING"
    unconfirmed = rig.channel.messages[first_message:]
    assert any("止盈迟到成交，保护单减量待券商确认" in title
               and "旧保护" in body and "不要另下卖单" in body
               for title, body in unconfirmed), unconfirmed
    assert all("止盈迟到成交，保护单已减量" not in title
               for title, _ in unconfirmed)

    # The broker now applies the same-ID change and returns status. Only a
    # subsequent fresh exact-identity/price/quantity snapshot can promote
    # the warning from pending to confirmed.
    rig.broker.confirm_protection_modify(protection_ref)
    rig.engine._drain(rig.clock.now())
    rig.engine.process_protection_priority()
    row = rig.store.protection(position["position_id"])
    assert row["state"] == "WORKING" and row["pending_qty"] is None
    confirmed = rig.channel.messages[first_message:]
    assert any("止盈迟到成交，保护单已减量" in title
               for title, _ in confirmed), confirmed
    assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]

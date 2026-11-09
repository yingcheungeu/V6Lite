"""The success card is specific only after the existing flat release proof."""
from v6lite.tests.test_batch16_f6_release import _protected_position_flattened_in_tws,_request_release
from v6lite.models import Side

def test_B32_release_title_names_frozen_symbol_and_verified_zero_shares(tmp_path,record_property):
    rig,pid,ref,exec_id=_protected_position_flattened_in_tws(tmp_path)
    _request_release(rig)
    assert not [title for title,body in rig.channel.messages if '已解除冻结' in title]
    assert rig.store.control('frozen:AAPL') is not None
    rig.broker.confirm_cancel(ref);rig.engine._drain(rig.clock.now())
    before=tuple(req.intent_id for req in rig.broker.sent if req.side is Side.SELL)
    _request_release(rig)
    assert rig.store.position(pid)['status']=='RELEASED' and rig.store.control('frozen:AAPL') is None
    [(title,body)]=[(title,body) for title,body in rig.channel.messages if '已解除冻结' in title]
    assert title=='✅ 已解除冻结 · AAPL · 持仓 0 股'
    assert '运行状态有新变化' not in body and 'John' not in body
    assert ref not in rig.broker.cancels and tuple(req.intent_id for req in rig.broker.sent if req.side is Side.SELL)==before
    record_property('b32_release_card_title',title)
    record_property('b32_release_card_body',body)


def test_B32_offline_cancel_refusal_card_names_missing_evidence_without_faking_CANCEL_SEEN(tmp_path):
    rig,pid,ref,exec_id=_protected_position_flattened_in_tws(tmp_path)
    oid=rig.store.protection(pid)['broker_order_id']
    # TWS cancellation is missed while Lite is offline: broker is flat with
    # no live order, but the durable intent never received the cancel event.
    rig.broker.confirm_cancel(ref);rig.broker.drain_events()
    before_intent=dict(rig.store.intent(ref));before_protection=dict(rig.store.protection(pid))
    before_position=dict(rig.store.position(pid))
    _request_release(rig)
    assert dict(rig.store.intent(ref))==before_intent
    assert dict(rig.store.protection(pid))==before_protection
    assert dict(rig.store.position(pid))==before_position
    assert rig.store.control('frozen:AAPL') is not None
    [(title,body)]=[(t,b) for t,b in rig.channel.messages if '人工释放被拒' in t]
    assert '具体原因' in body and f'保护单 {oid}' in body
    assert 'completed orders 原生查询能力' in body
    assert not rig.store.conn.execute("SELECT 1 FROM audit WHERE kind='PROTECT_CANCEL_PROVEN_COMPLETED'").fetchone()
    assert not [t for t,b in rig.channel.messages if '已解除冻结' in t]


def test_B32_release_refusal_reason_does_not_copy_untrusted_broker_error():
    from v6lite.alerts import _page_card
    secret='https://example.invalid/webhook/SECRET-TOKEN'
    card=_page_card('P1','release refused AAPL','broker evidence unavailable ('+secret+'); retry later')
    text=card.as_plain_text()
    assert secret not in text and 'SECRET' not in text
    assert '缺少完整快照' in text


def test_B32_release_refusal_card_distinguishes_execution_change_from_cancel_gap():
    from v6lite.alerts import _page_card
    text=_page_card('P1','release refused AAPL','execution evidence changed during protected release').as_plain_text()
    assert '两次快照之间出现新成交或成交签名改变' in text
    assert '撤单证据未被核实' not in text

"""Batch20 card provenance through real synthetic execution/status paths."""
from v6lite.tests.test_batch20_protection_equivalents import _retired_gtc_pair, _retired_native_fill
from v6lite.tests.test_batch20_protection_incident import _pending_protection, _wire, _deliver_wire, REJECT_ZH


def test_retired_gtc_execution_is_never_presented_as_a_take_profit(tmp_path):
    rig, pos, old, new = _retired_gtc_pair(tmp_path)
    rig.channel.messages.clear()
    fill = _retired_native_fill(rig, old, 5)
    rig.engine._ingest_fill(rig.clock.now(), fill)
    text = rig.messages()
    assert '迟到止盈' not in text and '止盈迟到' not in text
    assert '旧 GTC' in text and '持仓=15 股' in text
    assert any('P0' in title for title, _ in rig.channel.messages)
    assert rig.store.position(pos['position_id'])['qty_open'] == 15
    assert rig.store.protection(pos['position_id'])['pending_max_remaining'] == 20


def test_definite_rejection_body_states_known_refusal_not_unknown_status(tmp_path, monkeypatch):
    rig, pid = _pending_protection(tmp_path, monkeypatch)
    adapter, trade = _wire(rig, pid, raw='Cancelled', message=REJECT_ZH, code=202)
    _deliver_wire(rig, adapter, trade)
    text = rig.channel.messages[-1][1]
    assert '券商已回报取消保护请求' in text
    assert '券商已明确拒绝该保护请求' not in text and '券商已明确拒绝 STP 请求' not in text
    assert REJECT_ZH in text
    assert '当前保护状态待核实' not in text
    assert 'Ctrl+C' in text and 'bid' in text and '限价单平仓' in text
    assert '核对持仓' in text and '工作' in text
    assert '不要点击 TWS 的重新提交/IBALGO 建议' in text
    assert 'release AAPL' in text and '0 股' in text and '无在途卖单' in text
    assert '成交静默期' in text and '身份或证据不足保持拒绝' in text
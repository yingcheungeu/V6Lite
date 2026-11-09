"""Actual TP stage completion cards identify purpose, symbol, shares and fill price."""
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network
from v6lite.tests.test_batch28c_price_confirmation import (
    price_run, _be_pending, _finish_tp2_and_tp3)


def test_B28d_actual_TP1_TP2_TP3_cards_name_stage_and_booked_fill(price_run):
    rig, pid, wire = price_run()
    _be_pending(rig, pid, wire)
    wire.ack(only_open_order=True)
    _finish_tp2_and_tp3(rig, pid, wire)
    facts=rig.store.conn.execute(
        "SELECT i.purpose,SUM(f.quantity) AS qty,SUM(f.quantity*f.price)/SUM(f.quantity) AS avg "
        "FROM fills f JOIN intents i ON f.intent_id=i.intent_id "
        "WHERE i.position_id=? AND i.purpose IN ('TP1','TP2','TP3') GROUP BY i.purpose",(pid,)).fetchall()
    assert len(facts)==3 and rig.store.position(pid)['status']=='CLOSED'
    titles=[title for title,body in rig.channel.messages]
    expected=[f"✅ {r['purpose']} 已成交 · AAPL · {r['qty']} 股 @{r['avg']:.2f}" for r in facts]
    assert all(titles.count(title)==1 for title in expected), (expected,titles)
    rig.engine._refresh_fills(rig.clock.now(),force=True)
    refreshed=[title for title,body in rig.channel.messages]
    assert all(refreshed.count(title)==1 for title in expected), 'execution-time verification is not a second TP fill notice'

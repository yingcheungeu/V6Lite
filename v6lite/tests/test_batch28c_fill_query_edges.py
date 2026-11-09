"""An unbooked extra execution in query rows is not excused by our TP race."""
from v6lite.broker.fake import FakeBroker
from v6lite.models import OrderRequest, Purpose, Side
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network
from v6lite.tests.test_batch28c_fill_race import _activate_tp1


def test_B28c_own_TP_race_does_not_hide_extra_unbooked_SELL_in_native_query(overnight_run, monkeypatch):
    rig, pid, harness = overnight_run()
    tp = _activate_tp1(rig, pid)
    old = rig.store.protection(pid)
    original = rig.broker.positions_fresh
    injected = []
    def snapshot_then_two_fills():
        result = original()
        if rig.engine._protection_query_position == pid and not injected:
            injected.append(rig.clock.now())
            assert next(p.quantity for p in result if p.symbol == 'AAPL') == 3
            rig._b28b_callback_fill(tp.intent_id, quantity=1, price=344.04, exec_id='b28c-query-owned.01.01')
            # Exogenous unsafe broker activity, deliberately without an
            # engine callback. The real native query will expose its fill.
            req = OrderRequest('external-b28c-query-seller','AAPL',Side.SELL,1,344.,Purpose.TP1)
            FakeBroker.submit_limit(rig.broker,req)
            FakeBroker.fill(rig.broker,req.intent_id,qty=1,price=344.,
                            exec_id='b28c-query-unknown.01.01',emit=False)
        return result
    monkeypatch.setattr(rig.broker,'positions_fresh',snapshot_then_two_fills)
    rig.engine._protection_progress_one(rig.clock.now(),old,rig.store.position(pid))
    assert injected and rig.store.position(pid)['qty_open'] == 2
    assert next(p.quantity for p in FakeBroker.positions_fresh(rig.broker) if p.symbol=='AAPL') == 1
    assert rig.store.conn.execute('SELECT 1 FROM fills WHERE exec_id=?',('b28c-query-unknown.01.01',)).fetchone() is None
    assert any(level=='P0' for level,_ in rig._b26_alerts), 'known query-row contradiction must not be silently superseded'
    assert len([r for r in rig.broker.sent if r.position_id==pid and r.purpose==Purpose.TP1]) == 1

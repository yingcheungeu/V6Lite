"""A stop callback changing the binding cannot hide independent fills."""
import pytest
from v6lite.broker.fake import FakeBroker
from v6lite.models import OrderRequest, Purpose, Side
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network
from v6lite.tests.test_batch28b_callback_edges import (
    _stale_positions_with_stop_callback, _start_at_price_refresh)


@pytest.mark.parametrize('side', [Side.SELL, Side.BUY])
def test_B28c_stop_fill_and_unknown_execution_cannot_suppress_same_round_P0(overnight_run, monkeypatch, side):
    rig, pid, harness = overnight_run()
    ref, chronology = _stale_positions_with_stop_callback(rig, pid, monkeypatch, quantity=1)
    stop_snapshot = rig.broker.positions_fresh
    injected = []
    def snapshot_with_independent_fill():
        result = stop_snapshot()
        if chronology and not injected:
            injected.append(rig.clock.now())
            req = OrderRequest('external-b28c-stop-race', 'AAPL', side, 1, 344., Purpose.TP1)
            FakeBroker.submit_limit(rig.broker, req)
            rig._b28b_callback_fill(req.intent_id, quantity=1, price=344.,
                exec_id='b28c-stop-unknown.01.01')
        return result
    monkeypatch.setattr(rig.broker, 'positions_fresh', snapshot_with_independent_fill)
    _start_at_price_refresh(rig)
    assert injected and len(chronology) == 3
    assert rig.store.position(pid)['qty_open'] == 2
    actual = next(p.quantity for p in FakeBroker.positions_fresh(rig.broker) if p.symbol=='AAPL')
    assert actual == (1 if side is Side.SELL else 3)
    unknown = rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?', ('b28c-stop-unknown.01.01',)).fetchone()
    assert unknown is not None and unknown['position_id'] is None
    assert any(level=='P0' for level, _ in rig._b26_alerts), 'independent contradiction must survive a binding change'
    assert not [r for r in rig.broker.sent if r.position_id==pid and r.purpose==Purpose.TP1]


@pytest.mark.parametrize('shape', ['missing', 'duplicate'])
def test_B28c_stop_fill_cannot_hide_missing_or_duplicate_original_STP(overnight_run, monkeypatch, shape):
    rig, pid, harness = overnight_run()
    ref, chronology = _stale_positions_with_stop_callback(rig, pid, monkeypatch, quantity=1)
    original = rig.broker.open_orders
    def broken_book():
        result = original()
        if rig.engine._protection_query_position == pid:
            own = [o for o in result if o.intent_id==ref]
            assert len(own)==1
            return [o for o in result if o.intent_id!=ref] + ([] if shape=='missing' else own*2)
        return result
    monkeypatch.setattr(rig.broker, 'open_orders', broken_book)
    _start_at_price_refresh(rig)
    assert len(chronology)==3 and rig.store.position(pid)['qty_open']==2
    assert any(level=='P0' for level, _ in rig._b26_alerts), 'independent STP contradiction must not be discarded'
    assert not [r for r in rig.broker.sent if r.position_id==pid and r.purpose==Purpose.TP1]

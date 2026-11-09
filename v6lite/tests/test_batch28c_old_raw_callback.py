"""A delayed old wrapper closure cannot become a new socket observation."""
import pytest
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _no_network
from v6lite.tests.test_batch28c_price_confirmation import price_run, _be_pending


@pytest.mark.parametrize('source', ['live', 'owned-round'])
def test_B28c_old_raw_openOrder_closure_is_rejected_after_native_reset(price_run, monkeypatch, source):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    value = wire.apply_pending()
    wrapper, adapter = wire.ib.wrapper, wire.adapter
    old_callback = wrapper.openOrder
    contract, order, state = wire.native(value)
    adapter._reset_channels()
    rig.broker.query_generation = adapter.query_generation
    rig.advance(microseconds=1000)
    if source == 'live':
        old_callback(order.orderId, contract, order, state)
        rig.engine._drain(rig.clock.now())
    else:
        def only_old_callback():
            future = wrapper.startReq('openOrders')
            wire.ib.client.reqAllOpenOrders(); wire.harness.loop.drain()
            old_callback(order.orderId, contract, order, state)
            wrapper.openOrderEnd()
            assert future.done(), 'the current round must finish with its own End'
            return future.result()
        monkeypatch.setattr(wire.ib, 'reqAllOpenOrders', only_old_callback)
        result = adapter.open_orders()
        assert result == [], 'old raw rows must never enter a new generation own round'
    current = rig.store.protection(pid)
    assert current['state'] == 'PRICE_PENDING', 'an old callback cannot acknowledge the pending write'
    assert current['pending_qty'] == 2 and current['trigger_price'] == pending['trigger_price']

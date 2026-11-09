"""Candidate-only reset isolation: observer SQL failure cannot keep native proof."""
from __future__ import annotations

import sqlite3
import pytest

from v6lite.broker.base import BrokerError
from v6lite.tests.test_batch27_observation_audit import observation_rig, _observe
from v6lite.tests.test_batch27_logging_volume import _multi_native
from v6lite.tests.test_batch26_overnight_evidence import _no_network


def test_B27_new_reset_finishes_native_invalidation_even_when_observer_flush_fails(
        observation_rig, tmp_path, monkeypatch, request, record_property):
    rig = observation_rig()
    _observe(rig)
    rig.advance(seconds=1); _observe(rig)
    adapter, clock, _ = _multi_native(tmp_path, monkeypatch, request, 1)
    adapter.executions_today()
    adapter.open_orders()
    assert adapter._nonorder_req_ids and adapter._owned_open_orders_started
    wrapper, client = adapter.ib.wrapper, adapter.ib.client
    wrapper.openOrderEnd()  # Real unsolicited End after the owned query.
    assert "openOrders" in adapter._channel_poisoned
    pending = wrapper.startReq("positions")
    client.reqPositions()
    adapter._b25_offline_drain()
    with pytest.raises(BrokerError, match="earlier request"):
        adapter.positions_fresh()
    assert not pending.done()
    assert adapter._req_sent["positions"] > adapter._req_ended["positions"]
    assert adapter._channel_stuck_since
    old_end = wrapper.positionEnd
    old_generation = adapter.query_generation
    # The actual generation hook invokes the real pending-summary writer.
    adapter.set_observation_generation_handler(
        lambda generation: rig.store.observation_generation(rig.clock.now(), generation))
    rig.store.conn.execute("CREATE TEMP TRIGGER b27_reset_abort BEFORE INSERT ON audit "
        "WHEN NEW.kind='OBSERVATION_SUMMARY' BEGIN SELECT RAISE(ABORT,'synthetic reset observer failure'); END")
    try:
        with pytest.raises(sqlite3.DatabaseError, match="synthetic reset observer failure"):
            adapter._reset_channels()
        assert adapter.query_generation == old_generation + 1
        assert all(value == 0 for value in adapter._req_sent.values())
        assert all(value == 0 for value in adapter._req_ended.values())
        assert not adapter._channel_stuck_since and not adapter._channel_poisoned
        assert not adapter._nonorder_req_ids and not adapter._owned_open_orders_started
        assert adapter._fixed_hook_generation == adapter.query_generation
        old_end()
        assert all(value == 0 for value in adapter._req_ended.values()), "old bound End cannot validate the new generation"
        with pytest.raises(BrokerError, match="generation changed"):
            adapter._assert_query_generation(old_generation, adapter.ib)
    finally:
        rig.store.conn.execute("DROP TRIGGER IF EXISTS b27_reset_abort")
    record_property("b27_reset_isolation", "native invalidation completed despite observer SQLite failure")

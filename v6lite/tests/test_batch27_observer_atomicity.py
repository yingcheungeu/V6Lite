"""Candidate-only regression: multi-position summary retry is exactly-once.

This tests the newly introduced flush interface, not a legacy B26 API.
ROOT must label its first failure candidate_red, never B26 natural red.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import pytest

from v6lite.tests.test_batch27_observation_audit import (
    observation_rig, _observe, _count, _summary_items,
)


def test_B27_new_multi_position_flush_abort_then_retry_has_no_lost_or_duplicate_counts(
        observation_rig, record_property):
    rig = observation_rig(count=2)
    pids = rig._b27_pids
    for trade in rig._b27_native.ib.snapshot:
        _observe(rig, trade)
    for _ in range(4):
        rig.advance(seconds=1)
        for pid, trade in zip(pids, rig._b27_native.ib.snapshot):
            _observe(rig, trade)
            rig.store.update_protection(rig.clock.now(), pid, confirmed_at=rig.clock.now())
    assert _count(rig.store, "OBSERVATION_SUMMARY") == 0
    second = pids[1].replace("'", "''")
    rig.store.conn.execute(
        "CREATE TEMP TRIGGER b27_second_summary_abort BEFORE INSERT ON audit "
        "WHEN NEW.kind='OBSERVATION_SUMMARY' "
        "AND json_extract(NEW.payload,'$.position_id')='" + second + "' "
        "BEGIN SELECT RAISE(ABORT,'synthetic second-position summary failure'); END")
    assert not rig.store.conn.in_transaction, "exercise the real outside-transaction flush boundary"
    with pytest.raises(sqlite3.DatabaseError, match="synthetic second-position summary failure"):
        rig.store.flush_observations(rig.clock.now(), reason="test-boundary")
    partial_rows = _count(rig.store, "OBSERVATION_SUMMARY")
    rig.store.conn.execute("DROP TRIGGER b27_second_summary_abort")
    rig.store.flush_observations(rig.clock.now(), reason="test-boundary")
    counts = Counter()
    for item in _summary_items(rig.store):
        counts[(item["position_id"], item["kind"])] += item["count"]
    expected = Counter({(pid, kind): 4 for pid in pids
                        for kind in ("ORDER_PRICE_OBSERVATION", "PROTECT_STATE")})
    record_property("b27_new_flush_retry", json.dumps({
        "partial_rows_after_failure": partial_rows,
        "after_retry_rows": _count(rig.store, "OBSERVATION_SUMMARY"),
        "counts": {pid + ":" + kind: count for (pid, kind), count in counts.items()},
    }, sort_keys=True))
    assert counts == expected, ("exact retry counts", dict(counts), dict(expected))
    before = _count(rig.store, "OBSERVATION_SUMMARY")
    rig.store.flush_observations(rig.clock.now(), reason="test-boundary")
    assert _count(rig.store, "OBSERVATION_SUMMARY") == before, "second successful flush must be empty"

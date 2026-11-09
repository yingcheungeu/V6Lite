"""L7 separates steady GTC health probes from refused TP submission retries."""

from __future__ import annotations

import inspect
from datetime import timedelta

from v6lite.broker.base import BrokerError
from v6lite.config import ProtectionConfig
from v6lite.models import Side
from v6lite.tests.helpers import Rig


def test_protected_TP_evidence_timeout_honors_30_second_retry_backoff(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig())
    try:
        rig.open_aapl()
        row = rig.store.protections()[0]
        assert row["state"] == "WORKING" and row["tp_stage"] is None
        rig.advance(seconds=31)
        rig.px("AAPL", 120.0)  # genuine TP1, above the 119.47 target; not STOP
        tp_queries = []  # (request start, failed request end)
        steady_queries = []

        def unavailable_orders():
            callers = {frame.function for frame in inspect.stack()[1:14]}
            started = rig.clock.now()
            rig.clock.advance(seconds=15)  # one blocked broker request
            if "_protection_begin_tp" in callers:
                tp_queries.append((started, rig.clock.now()))
            elif "_protection_progress" in callers:
                steady_queries.append((started, rig.clock.now()))
            raise BrokerError("open-orders timeout")

        monkeypatch.setattr(rig.broker, "open_orders", unavailable_orders)
        for _ in range(3):
            rig.tick()
            rig.advance(seconds=1)
            rig.px("AAPL", 120.0)

        assert tp_queries, "the real TP path was not exercised"
        assert steady_queries, "steady protection evidence is a separate F4 retry"
        assert all(next_start - failed_at >= timedelta(seconds=30)
                   for (_, failed_at), (next_start, _) in zip(tp_queries, tp_queries[1:])), tp_queries
        assert rig.store.protection(row["position_id"])["tp_stage"] is None
        assert not [req for req in rig.broker.sent if req.side is Side.SELL]
        assert not [req for req in rig.broker.sent if getattr(req, "order_type", "") == "MKT"]
    finally:
        rig.store.close()

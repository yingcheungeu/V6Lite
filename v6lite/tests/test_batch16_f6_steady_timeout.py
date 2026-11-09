"""A failed steady GTC health query must not starve normal trading work."""

from __future__ import annotations

from datetime import timedelta

from v6lite.config import ProtectionConfig
from v6lite.tests.helpers import Rig


def test_B16_F6_repeated_steady_query_timeout_keeps_exits_and_scans_scheduled(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(
        steady_check_seconds=15, evidence_fail_p0_seconds=60),
        scan_times_et=("10:00", "10:01"))
    try:
        rig.open_aapl()
        row = rig.store.protections()[0]
        assert row["state"] == "WORKING" and row["pending_qty"] is None
        assert row["tp_stage"] is None
        assert row["gap_a_started_at"] is None and row["gap_b_started_at"] is None
        exits, scans = [], []
        real_exit = rig.engine._evaluate_exits
        real_scan = rig.engine._run_scheduled_scans

        def observed_exit(now, *, regular=False):
            if regular:
                exits.append(now)
            return real_exit(now, regular=regular)

        def observed_scan(now):
            scans.append(now)
            return real_scan(now)

        monkeypatch.setattr(rig.engine, "_evaluate_exits", observed_exit)
        monkeypatch.setattr(rig.engine, "_run_scheduled_scans", observed_scan)
        rig.broker.fail_open_orders = True
        rig.advance(seconds=16)  # confirmed_at is now older than steady 15s
        first_failure = rig.clock.now()
        observations = []
        for i, elapsed in enumerate((0, 1, 2, 13, 59, 60), start=1):
            rig.at(first_failure + timedelta(seconds=elapsed))
            rig.tick()
            pages = [title for title, _ in rig.channel.messages
                     if "保护单三项证据不完整" in title]
            observations.append((elapsed, len(exits), len(scans), len(pages)))
            if elapsed < 60:
                assert pages == [], (elapsed, pages)
            else:
                assert len(pages) == 1, pages
        assert all(exit_count == i and scan_count == i
                   for i, (_, exit_count, scan_count, _) in enumerate(observations, start=1)), observations
        assert rig.store.control(f"protect_evidence_failure:{row['position_id']}")
        assert not [req for req in rig.broker.sent
                    if getattr(req, "order_type", "") == "MKT"]
    finally:
        rig.store.close()

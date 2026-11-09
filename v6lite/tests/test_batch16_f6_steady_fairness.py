"""A slow steady protection check gets one fair slot per tick across symbols."""

from __future__ import annotations

from datetime import timedelta

from v6lite.config import ProtectionConfig
from v6lite.protection_engine import ProtectionEvidenceQueryError
from v6lite.tests.helpers import Rig
from v6lite.tests.test_review_round2 import _two_open_positions


def test_B16_F6_two_slow_steady_positions_are_round_robin_without_exit_starvation(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, protection=ProtectionConfig(steady_check_seconds=15))
    try:
        _two_open_positions(rig)
        rows = rig.store.protections()
        assert {row["symbol"] for row in rows} == {"AAPL", "SPY"}
        assert all(row["state"] == "WORKING" and row["pending_qty"] is None and
                   row["tp_stage"] is None and row["gap_a_started_at"] is None and
                   row["gap_b_started_at"] is None for row in rows)
        real_evidence = rig.engine._protection_evidence
        real_exit = rig.engine._evaluate_position_exit
        attempts = []
        spy_exits = []
        round_index = [0]

        def slow_three_source(row):
            attempts.append((round_index[0], row["symbol"]))
            rig.clock.advance(seconds=14)  # one bounded but slow broker query
            if row["symbol"] == "AAPL":
                # A failing first symbol must not monopolize every next slot.
                raise ProtectionEvidenceQueryError("synthetic AAPL timeout")
            return real_evidence(row)

        def observed_exit(now, row, broker_qty):
            if row["symbol"] == "SPY":
                spy_exits.append(rig.clock.now())
            return real_exit(now, row, broker_qty)

        monkeypatch.setattr(rig.engine, "_protection_evidence", slow_three_source)
        monkeypatch.setattr(rig.engine, "_evaluate_position_exit", observed_exit)
        rig.advance(seconds=16)
        durations = []
        for tick_number in (1, 2, 3, 4):
            round_index[0] = tick_number
            rig.advance(seconds=1)
            started = rig.clock.now()
            rig.tick()
            durations.append((rig.clock.now() - started).total_seconds())
        per_round = {tick: [symbol for seen_tick, symbol in attempts if seen_tick == tick]
                     for tick in (1, 2, 3, 4)}
        assert all(len(symbols) <= 1 for symbols in per_round.values()), per_round
        assert {per_round[1][0], per_round[2][0]} == {"AAPL", "SPY"}, per_round
        assert max(durations) <= 15, durations
        assert len(spy_exits) >= 2, spy_exits
        gaps = [(b - a).total_seconds() for a, b in zip(spy_exits, spy_exits[1:])]
        assert max(gaps) <= rig.cfg.exit_check_interval_seconds + 1, gaps
        assert not [req for req in rig.broker.sent
                    if getattr(req, "order_type", "") == "MKT"]
    finally:
        rig.store.close()

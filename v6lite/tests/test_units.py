"""Unit tests: config, strategy, sizing, exits, calendar, approval gate, lock."""

from __future__ import annotations

import math
import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from v6lite.approval import AccountLock, GateError, check_live_gate, write_approval
from v6lite.config import ConfigError, validate
from v6lite.exits import PositionView, compute_levels, decide_exit, sell_limit
from v6lite.market_calendar import early_closes, holidays, is_rth, session_for
from v6lite.models import Bar, Purpose
from v6lite.sizing import size_entry, sizing_equity
from v6lite.strategy import clean_bars, effective_min_score, generate_signals, score_symbol
from v6lite.tests.helpers import buy_bars, down_bars, et, make_cfg


# ---------------------------------------------------------------- config
def test_paper_requires_du_account_and_paper_port(tmp_path):
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, account="U0000000")
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, port=4001)


def test_live_rejects_paper_account_and_requires_live_port(tmp_path):
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, mode="live", account="DU0000000", port=4001)
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, mode="live", account="U0000000", port=4002)
    cfg = make_cfg(tmp_path, mode="live", account="U0000000", port=4001)
    assert cfg.is_live


def test_legacy_client_id_and_protected_overlap_rejected(tmp_path):
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, client_id=120)
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, protected_symbols=("AAPL",))


def test_risk_per_trade_hard_cap(tmp_path):
    with pytest.raises(ConfigError):
        make_cfg(tmp_path, risk_per_trade_pct=0.05)


def test_fingerprint_changes_with_any_parameter(tmp_path):
    a = make_cfg(tmp_path)
    b = make_cfg(tmp_path, daily_loss_limit_usd=151.0)
    c = make_cfg(tmp_path, state_dir=str(tmp_path / "elsewhere"))
    assert a.fingerprint() != b.fingerprint()
    assert a.fingerprint() == c.fingerprint()  # state location is not behaviour


# ---------------------------------------------------------------- strategy
def test_uptrend_fixture_scores_buy_like_legacy_scanner():
    res = score_symbol("AAPL", buy_bars(), is_etf=False)
    assert (res.signal, res.score, res.price, res.atr) == ("BUY", 78, 113.13, 2.0)


def test_downtrend_produces_no_signal():
    signals, _ = generate_signals({"TSLA": down_bars()}, etf_symbols=(), min_score=60)
    assert signals == []


def test_non_finite_rows_fail_closed_beyond_cap():
    bars = buy_bars()
    polluted = [Bar(b.day, b.open, float("nan"), b.low, b.close, b.volume) if i % 10 == 0 else b
                for i, b in enumerate(bars)]
    kept, status = clean_bars(polluted)
    assert kept == [] and status.startswith("non_finite_rows")
    assert score_symbol("AAPL", polluted, is_etf=False).signal == "NEUTRAL"


def test_small_pollution_is_dropped_not_propagated():
    bars = buy_bars()
    bars[40] = Bar("x", 1, float("inf"), 1, 1, 1)
    res = score_symbol("AAPL", bars, is_etf=False)
    assert math.isfinite(res.atr) and res.atr > 0


def test_unknown_vix_uses_strict_threshold():
    assert effective_min_score(None, min_score=60, high_vix_threshold=30, high_vix_min_score=75) == 75
    assert effective_min_score(35, min_score=60, high_vix_threshold=30, high_vix_min_score=75) == 75
    assert effective_min_score(18, min_score=60, high_vix_threshold=30, high_vix_min_score=75) == 60


# ---------------------------------------------------------------- sizing
def test_sizing_is_risk_based_and_capped(tmp_path):
    cfg = make_cfg(tmp_path)
    equity = sizing_equity(cfg, 1_000_000)
    assert equity == 10_000  # capital cap, not paper NLV
    res = size_entry(cfg, equity=equity, price=113.47, atr=2.0, deployed_notional=0)
    assert res.quantity == 20 and res.risk_usd == pytest.approx(100.0)


def test_sizing_never_floors_to_one_share(tmp_path):
    cfg = make_cfg(tmp_path)
    res = size_entry(cfg, equity=10_000, price=900.0, atr=60.0, deployed_notional=0)
    assert res.quantity == 0 and res.reason.startswith("size_below_one_share")
    res = size_entry(cfg, equity=10_000, price=100.0, atr=1.0, deployed_notional=9_950)
    assert res.quantity == 0 and "capital_cap" in res.reason


def test_sizing_rejects_bad_inputs(tmp_path):
    cfg = make_cfg(tmp_path)
    assert size_entry(cfg, equity=float("nan"), price=10, atr=1, deployed_notional=0).quantity == 0
    assert sizing_equity(cfg, None) is None and sizing_equity(cfg, -5) is None


# ---------------------------------------------------------------- exits
def _pos(**kw):
    base = dict(position_id="p", symbol="AAPL", entry_price=100.0, qty_initial=20, qty_open=20,
                stop=95.0, tp1=106.0, tp2=110.0, tp3=116.0, tp1_done=False, tp2_done=False, breakeven_stop=None)
    base.update(kw)
    return PositionView(**base)


def test_exit_ladder():
    assert decide_exit(_pos(), 101.0) is None
    d = decide_exit(_pos(), 106.5)
    assert (d.stage, d.quantity) == (Purpose.TP1, 6)
    d = decide_exit(_pos(tp1_done=True, qty_open=14, breakeven_stop=100.05), 110.0)
    assert (d.stage, d.quantity) == (Purpose.TP2, 6)
    d = decide_exit(_pos(tp1_done=True, tp2_done=True, qty_open=8, breakeven_stop=100.05), 116.0)
    assert (d.stage, d.quantity) == (Purpose.TP3, 8)
    d = decide_exit(_pos(), 94.9)
    assert (d.stage, d.quantity, d.reason) == (Purpose.STOP, 20, "atr_stop")


def test_breakeven_stop_after_tp1():
    d = decide_exit(_pos(tp1_done=True, qty_open=14, breakeven_stop=100.05), 100.0)
    assert d.stage is Purpose.STOP and d.trigger_level == 100.05 and d.reason == "breakeven_stop"


def test_levels_and_limits(tmp_path):
    cfg = make_cfg(tmp_path)
    lv = compute_levels(cfg, 113.47, 2.0)
    assert (lv.stop, lv.tp1, lv.tp2, lv.tp3) == (108.47, 119.47, 123.47, 129.47)
    assert sell_limit(100.0, 0.01) == 99.0
    assert sell_limit(100.003, 0.01) == 99.01  # rounded UP to the cent
    assert sell_limit(float("nan"), 0.01) is None


# ---------------------------------------------------------------- calendar
def test_calendar_holidays_and_early_close():
    assert date(2026, 11, 26) in holidays(2026)       # Thanksgiving
    assert date(2026, 11, 27) in early_closes(2026)   # day after
    s = session_for(date(2026, 11, 27))
    assert s.close_utc.hour == 18                     # 13:00 ET
    assert session_for(date(2026, 10, 10)) is None    # Saturday
    assert is_rth(et(10, 0)) and not is_rth(et(16, 0)) and not is_rth(et(9, 29))


# ---------------------------------------------------------------- live gate & lock
def _live_cfg(tmp_path, **kw):
    return make_cfg(tmp_path, mode="live", account="U0000000", port=4001, **kw)


def test_live_gate_requires_flag_and_matching_unexpired_approval(tmp_path):
    now = datetime(2026, 10, 6, 14, tzinfo=timezone.utc)
    cfg = _live_cfg(tmp_path)
    with pytest.raises(GateError, match="--live"):
        check_live_gate(cfg, live_flag=False, now=now)
    with pytest.raises(GateError, match="no live approval"):
        check_live_gate(cfg, live_flag=True, now=now)
    write_approval(cfg, operator="John", days=7, note="pilot", now=now)
    assert check_live_gate(cfg, live_flag=True, now=now)["approved_by"] == "John"
    changed = _live_cfg(tmp_path, max_positions=3)
    with pytest.raises(GateError, match="fingerprint"):
        check_live_gate(changed, live_flag=True, now=now)
    with pytest.raises(GateError, match="expired"):
        check_live_gate(cfg, live_flag=True, now=now + timedelta(days=8))
    with pytest.raises(GateError):
        write_approval(cfg, operator="John", days=31, note="", now=now)


def test_paper_config_refuses_live_flag(tmp_path):
    with pytest.raises(GateError):
        check_live_gate(make_cfg(tmp_path), live_flag=True, now=datetime.now(timezone.utc))


def test_account_lock_blocks_second_instance_and_foreign_v6_lock(tmp_path):
    locks = tmp_path / "locks"
    lock = AccountLock("DU0000000", lock_dir=locks)
    lock.acquire()
    with pytest.raises(GateError, match="held by another"):
        AccountLock("DU0000000", lock_dir=locks).acquire()
    lock.release()
    foreign = tmp_path / "trading_bot_v6.lock"
    foreign.write_text(f"{os.getpid()}\n2026-10-06T00:00:00\n")
    with pytest.raises(GateError, match="another trading process"):
        AccountLock("DU0000000", lock_dir=locks).acquire((str(foreign),))
    foreign.write_text("999999\nstale\n")
    AccountLock("DU0000000", lock_dir=locks).acquire((str(foreign),))
    foreign.write_text("garbage")
    with pytest.raises(GateError):                      # unreadable -> fail closed
        AccountLock("DU0000000", lock_dir=locks).acquire((str(foreign),))

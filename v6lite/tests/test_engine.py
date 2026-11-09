"""Engine safety tests against the FakeBroker.

Each test names the real V6 incident or rule it protects.
"""

from __future__ import annotations

import pytest

from v6lite.broker.base import BrokerError
from v6lite.engine import Engine, StartupError
from v6lite.models import IntentStatus, Side
from v6lite.store import StoreError
from v6lite.tests.helpers import Rig, et


def _intent(rig, intent_id):
    return rig.store.intent(intent_id)


# ---------------------------------------------------------------- startup / identity
def test_account_mismatch_aborts_startup_without_orders(tmp_path):
    rig = Rig(tmp_path)
    rig.broker.accounts = ["DU0000000"]
    with pytest.raises(StartupError):
        rig.engine.startup()
    assert rig.broker.sent == []


def test_state_file_is_bound_to_one_account(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    with pytest.raises(StoreError):
        rig.store.bind_account("DU0000000", "paper")


# ---------------------------------------------------------------- entry path
def test_scan_enters_with_risk_based_size_and_protected_limit(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(9, 59))
    rig.tick()
    assert rig.broker.sent == []                    # before the ET scan slot
    rig.at(et(10, 0, 30))
    rig.tick()
    assert len(rig.broker.sent) == 1
    req = rig.broker.sent[0]
    assert (req.symbol, req.side, req.quantity, req.limit_price) == ("AAPL", Side.BUY, 20, 113.47)
    day = rig.store.risk_day("2026-10-06")
    assert day["entries_submitted"] == 1            # counted before dispatch
    rig.broker.fill(req.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    pos = rig.store.open_positions()[0]
    assert (pos["symbol"], pos["qty_open"], pos["stop"], pos["tp1"]) == ("AAPL", 20, 108.47, 119.47)
    assert _intent(rig, req.intent_id)["status"] == IntentStatus.FILLED


def test_scan_slot_runs_once_per_day(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    rig.broker.confirm_cancel(rig.broker.sent[0].intent_id)
    rig.advance(seconds=10)
    rig.tick()
    rig.advance(minutes=5)
    rig.tick()
    assert len(rig.broker.sent) == 1


def test_duplicate_execution_callbacks_are_applied_once(tmp_path):
    """CAT 220->110 regression: a replayed fill must not double the position."""
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.broker.fill(req.intent_id, duplicate=True)
    rig.advance(seconds=1)
    rig.tick()
    rig.engine._recover(rig.clock.now())            # executions replay on reconnect
    assert rig.store.open_positions()[0]["qty_open"] == 20


def test_submitted_with_zero_fill_is_not_a_failure(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.broker.status(req.intent_id, "Submitted")
    rig.advance(seconds=5)
    rig.tick()
    assert _intent(rig, req.intent_id)["status"] == IntentStatus.SENT
    assert len(rig.broker.sent) == 1


def test_cancelled_then_filled_race_counts_the_fill(tmp_path):
    """GOOGL TP1 incident: IBKR sent Cancelled and the order then filled."""
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.advance(seconds=61)                          # entry fill timeout
    rig.tick()
    assert req.intent_id in rig.broker.cancels
    rig.broker.confirm_cancel(req.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    assert _intent(rig, req.intent_id)["status"] == IntentStatus.CANCEL_SEEN
    # the execution is only visible via reqExecutions, not as a live callback
    rig.broker.fill(req.intent_id, emit=False)
    rig.advance(seconds=6)                           # past cancel grace -> settle
    rig.tick()
    assert _intent(rig, req.intent_id)["status"] == IntentStatus.FILLED
    assert rig.store.open_positions()[0]["qty_open"] == 20


def test_cancelled_without_fill_settles_cancelled(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.broker.confirm_cancel(req.intent_id)
    rig.advance(seconds=1)
    rig.tick()                                       # hint recorded, grace starts
    assert _intent(rig, req.intent_id)["status"] == IntentStatus.CANCEL_SEEN
    rig.advance(seconds=6)
    rig.tick()                                       # grace over -> settle from executions
    assert _intent(rig, req.intent_id)["status"] == IntentStatus.CANCELLED
    assert rig.store.open_positions() == []


# ---------------------------------------------------------------- exits
def _to_price(rig, price, seconds=31):
    rig.advance(seconds=seconds)
    rig.px("AAPL", price)
    rig.tick()


def test_tp1_is_one_shot_across_ticks_and_restart(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    _to_price(rig, 120.0)
    tp1 = rig.broker.sent[-1]
    assert (tp1.side, tp1.quantity, tp1.purpose) == (Side.SELL, 6, "TP1")
    _to_price(rig, 120.5)
    _to_price(rig, 121.0)
    assert [r.purpose for r in rig.broker.sent].count("TP1") == 1
    rig.broker.fill(tp1.intent_id)
    _to_price(rig, 120.0)
    pos = rig.store.open_positions()[0]
    assert pos["qty_open"] == 14 and pos["tp1_done"] == 1 and pos["breakeven_stop"] == 113.53
    # restart: a new engine on the same durable state must not re-sell TP1
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()
    rig.advance(seconds=31)
    engine2.tick()
    assert [r.purpose for r in rig.broker.sent].count("TP1") == 1


def test_breakeven_stop_after_tp1_sells_remaining(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    _to_price(rig, 120.0)
    rig.broker.fill(rig.broker.sent[-1].intent_id)
    _to_price(rig, 118.0)
    _to_price(rig, 113.50)                             # below breakeven 113.53
    stop = rig.broker.sent[-1]
    assert (stop.purpose, stop.quantity) == ("STOP", 14)


def test_stop_cancels_a_working_take_profit(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    _to_price(rig, 120.0)
    tp1 = rig.broker.sent[-1]
    _to_price(rig, 105.0)                              # crash through the stop
    assert tp1.intent_id in rig.broker.cancels


def test_exit_retries_then_escalates(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.open_aapl()
    _to_price(rig, 107.0)                              # ATR stop 108.47
    for _ in range(3):
        stop = rig.broker.sent[-1]
        assert stop.purpose == "STOP"
        rig.advance(seconds=46)
        rig.tick()                                     # timeout -> cancel requested
        rig.broker.confirm_cancel(stop.intent_id)
        rig.advance(seconds=6)
        rig.tick()                                     # settle -> cancelled
        _to_price(rig, 107.0)                          # retry or escalate
    stops = [r for r in rig.broker.sent if r.purpose == "STOP"]
    assert len(stops) == 3
    assert any(k.startswith("anomaly:exit_failed:AAPL") for k in rig.store.anomalies())
    assert "P0" in rig.messages() and "AAPL" in rig.messages()
    assert "NOT FILLED" in caplog.text


def test_exits_never_sell_more_than_broker_holds(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.open_aapl()
    # someone sold 15 shares manually in TWS
    rig.broker.external_positions["AAPL"] = (-15, 0.0)
    rig.advance(seconds=301)
    rig.tick()                                         # reconcile detects the diff
    _to_price(rig, 105.0)
    assert [r for r in rig.broker.sent if r.side is Side.SELL] == []
    assert "持仓账本与券商不符" in rig.messages()
    assert "broker/runtime mismatch" in caplog.text


# ---------------------------------------------------------------- controls
def test_kill_blocks_entries_but_exits_still_run(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.store.kill(rig.clock.now(), "operator test")
    _to_price(rig, 105.0)
    assert rig.broker.sent[-1].purpose == "STOP"
    snap = rig.engine.status_snapshot(rig.clock.now())
    assert not snap["entries_allowed"] and any(b.startswith("kill_switch") for b in snap["entry_blocks"])


def test_kill_blocks_new_entries_at_scan_time(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.store.kill(rig.clock.now(), "hold")
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.broker.sent == []


def test_daily_loss_latch_cannot_be_resumed_same_day(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.open_aapl()
    _to_price(rig, 100.0)                             # stop hit hard
    stop = rig.broker.sent[-1]
    rig.broker.fill(stop.intent_id, price=100.0)      # (100-113.47)*20 = -269 loss
    rig.advance(seconds=1)
    rig.tick()
    assert rig.store.risk_day("2026-10-06")["daily_stop_latched"] == 1
    assert "当日亏损门槛触发" in rig.messages()
    assert "daily loss stop latched" in caplog.text
    rig.store.kill(rig.clock.now(), "after latch")
    with pytest.raises(StoreError):
        rig.store.resume(rig.clock.now(), "try", "John")


def test_consecutive_losses_start_cooldown(tmp_path):
    from datetime import timedelta
    from v6lite.risk import cooldown_until, loss_state
    from v6lite.tests.helpers import seed_closed_trade
    rig = Rig(tmp_path)
    rig.start()
    now = rig.clock.now()
    seed_closed_trade(rig.store, "pos-a", -10, now - timedelta(minutes=3))
    assert loss_state(rig.store, rig.cfg).streak == 1
    seed_closed_trade(rig.store, "pos-b", 0, now - timedelta(minutes=2))     # zero does not reset
    assert loss_state(rig.store, rig.cfg).streak == 1
    seed_closed_trade(rig.store, "pos-c", -5, now - timedelta(minutes=1))
    assert cooldown_until(rig.store, rig.cfg) > now
    blocks = rig.engine.status_snapshot(now)["entry_blocks"]
    assert any(b.startswith("loss_cooldown_until") for b in blocks)


def test_max_daily_entries_counts_rejected_submissions(tmp_path):
    rig = Rig(tmp_path, max_daily_entries=1)
    rig.start()
    rig.broker.fail_next_submit = BrokerError("rejected by broker", maybe_sent=False)
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.store.risk_day("2026-10-06")["entries_submitted"] == 1
    assert "max_daily_entries" in rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]


# ---------------------------------------------------------------- broker truth
def test_unmanaged_holding_blocks_entries_until_ignored(tmp_path):
    rig = Rig(tmp_path)
    rig.broker.external_positions["NVDA"] = (50, 120.0)   # legacy V6 holding
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.broker.sent == []
    rig.store.set_disposition(rig.clock.now(), "NVDA", "ignore", "John")
    rig.engine._reconcile(rig.clock.now())
    assert rig.engine.flags.reconcile_ok


def test_v1_has_no_adopt_unmanaged_holdings_stay_unmanaged(tmp_path):
    """Batch 3 scope decision: v1 never takes over holdings it did not open."""
    rig = Rig(tmp_path)
    rig.broker.external_positions["AAPL"] = (30, 110.0)
    rig.start()
    with pytest.raises(StoreError):
        rig.store.set_disposition(rig.clock.now(), "AAPL", "adopt", "John")
    rig.engine._reconcile(rig.clock.now())
    assert rig.store.open_positions() == [] and not rig.engine.flags.reconcile_ok


def test_protected_symbol_is_never_managed_or_blocking(tmp_path):
    rig = Rig(tmp_path)
    rig.broker.external_positions["MSTR"] = (58, 300.0)
    rig.start()
    assert rig.engine.flags.reconcile_ok
    assert all(r.symbol != "MSTR" for r in rig.broker.sent)


def test_foreign_fill_latches_anomaly(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.broker.external_fill("AAPL", Side.BUY, 5, 113.0)
    rig.advance(seconds=1)
    rig.tick()
    assert any(k.startswith("anomaly:foreign_fill") for k in rig.store.anomalies())
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.broker.sent == []


def test_unknown_submit_outcome_blocks_then_recovers(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.broker.fail_next_submit = BrokerError("socket closed mid-send", maybe_sent=True)
    rig.at(et(10, 0, 30))
    rig.tick()
    intent_id = next(iter(rig.broker.orders))
    assert _intent(rig, intent_id)["status"] == IntentStatus.UNKNOWN
    assert any(b.startswith("unknown_order_state") for b in rig.engine.status_snapshot(rig.clock.now())["entry_blocks"])
    rig.advance(seconds=61)
    rig.tick()                                         # settle: order is working at the broker
    assert _intent(rig, intent_id)["status"] == IntentStatus.SENT


def test_disconnect_blocks_and_reconnect_recovers_missed_fills(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.broker.drop_connection()
    rig.broker.refuse_connect = True
    rig.broker.fill(req.intent_id, emit=False)        # filled while we were offline
    rig.advance(seconds=1)
    rig.tick()
    assert not rig.engine.flags.broker_connected
    assert "IBKR 连接断开" in rig.messages()
    assert "IBKR disconnected" in caplog.text
    assert "broker_disconnected" in rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]
    rig.broker.refuse_connect = False
    rig.advance(seconds=10)
    rig.tick()                                         # reconnect is rate-limited (30s)
    assert not rig.engine.flags.broker_connected
    rig.advance(seconds=31)
    rig.tick()                                         # reconnect -> recover -> reconcile
    assert rig.engine.flags.broker_connected and rig.engine.flags.reconcile_ok
    assert rig.store.open_positions()[0]["qty_open"] == 20


def test_restart_recovers_intent_persisted_before_crash(tmp_path):
    """Crash between writing the intent and handing it to the broker."""
    from v6lite.models import OrderRequest, Purpose
    from v6lite.store import new_id
    rig = Rig(tmp_path)
    rig.start()
    req = OrderRequest(new_id("v6l"), "AAPL", Side.BUY, 5, 113.5, Purpose.ENTRY, new_id("pos"))
    rig.store.create_intent(rig.clock.now(), req, atr=2.0, count_entry_day="2026-10-06")
    engine2 = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    engine2.startup()
    assert _intent(rig, req.intent_id)["status"] == IntentStatus.CANCELLED


# ---------------------------------------------------------------- modes / lifecycle
def test_dry_run_never_sends_orders(tmp_path):
    rig = Rig(tmp_path, dry_run=True)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.broker.sent == []
    assert rig.store.conn.execute("SELECT COUNT(*) FROM audit WHERE kind='DRY_RUN'").fetchone()[0] == 1


def test_live_mode_blocks_entries_when_alerts_are_down(tmp_path, monkeypatch):
    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "https://example.invalid/webhook")
    from v6lite.config import AlertConfig
    rig = Rig(tmp_path, alerts_fail=True, mode="live", account="U0000000", port=4001,
              alerts=AlertConfig(require_remote_in_live=True))
    rig.broker.accounts = ["U0000000"]
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    assert rig.broker.sent == []
    assert "alerts_unavailable" in rig.engine.status_snapshot(rig.clock.now())["entry_blocks"]


def test_shutdown_cancels_working_orders(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.tick()
    req = rig.broker.sent[0]
    rig.engine.shutdown(grace_seconds=0)
    assert req.intent_id in rig.broker.cancels


def test_end_of_day_report_is_written_once(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.at(et(16, 6))
    rig.tick()
    rig.advance(minutes=1)
    rig.tick()
    report = tmp_path / "reports" / "2026-10-06.md"
    assert report.exists() and "AAPL" in report.read_text()
    assert sum("每日健康报告" in s or "⚠️ 需关注" in s
               for s, b in rig.channel.messages if "行情可用率：" in b) == 1


def test_unpriced_position_escalates_and_blocks_entries(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.open_aapl()
    rig.broker.quotes.pop("AAPL")
    rig.advance(seconds=10)
    rig.tick()
    assert any(b.startswith("unpriced_positions") for b in rig.engine.status_snapshot(rig.clock.now())["entry_blocks"])
    rig.clock.advance(minutes=3)
    rig.tick()
    assert "持仓报价过期" in rig.messages()
    assert "positions without fresh prices" in caplog.text


def test_entry_skipped_when_live_price_drifted_from_signal(tmp_path):
    rig = Rig(tmp_path)
    rig.start()
    rig.at(et(10, 0, 30))
    rig.px("AAPL", 120.0)                              # 6% above the scanned price
    rig.tick()
    assert rig.broker.sent == []


def test_overnight_unexpected_gateway_disconnect_is_paged_once(tmp_path):
    rig = Rig(tmp_path, start=et(20, 0))
    rig.engine.startup()
    rig.tick()
    before = len(rig.channel.messages)
    rig.broker.drop_connection()
    rig.advance(seconds=1)
    rig.tick()
    assert len(rig.channel.messages) == before + 1
    title, _ = rig.channel.messages[-1]
    assert "P1" in title and "IBKR 连接断开" in title


def test_gateway_server_link_loss_waits_for_restore(tmp_path):
    """Code 1100 keeps the API socket up; trading must stay paused until 1101/1102."""
    from v6lite.models import ConnectionEvent
    rig = Rig(tmp_path)
    rig.start()
    rig.broker.events.append(ConnectionEvent(False, "1100:Connectivity between IB and TWS has been lost"))
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=45)
    rig.tick()
    assert not rig.engine.flags.broker_connected
    rig.broker.events.append(ConnectionEvent(True, "1102:restored, data maintained"))
    rig.advance(seconds=1)
    rig.tick()
    assert rig.engine.flags.broker_connected and rig.engine.flags.reconcile_ok

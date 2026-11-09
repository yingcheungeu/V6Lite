"""G3 test mode: configuration isolation, one-shot entry, and normal risk gates.

All accounts, paths, and market prices here are synthetic. FakeBroker only.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch
import json

import pytest

from v6lite.config import ConfigError, load_config
from v6lite.models import Side
from v6lite.tests.helpers import Rig, et


def _toml(state_dir, *, mode="paper", account="DU0000000", enabled=True,
          section=True):
    test = ("\n[test]\n" + f"enabled = {str(enabled).lower()}\n"
            "qty = 3\nstop_mult = 0.3\ntp1_mult = 0.3\n"
            "tp2_mult = 0.5\ntp3_mult = 0.8\n") if section else ""
    return (f'mode = "{mode}"\n\n[ibkr]\nport = {4001 if mode == "live" else 4002}\n'
            f'account = "{account}"\n\n[universe]\ntradable = ["AAPL"]\n'
            'scan = ["AAPL"]\n\n[paths]\n'
            f'state_dir = "{state_dir.as_posix()}"\n' + test)


def _load(tmp_path, *, directory="paper-test", **kwargs):
    path = tmp_path / "config.toml"
    path.write_text(_toml(tmp_path / directory, **kwargs), encoding="utf-8")
    return load_config(path)


@pytest.mark.parametrize("enabled", [False, True])
def test_T2_live_rejects_even_disabled_test_section_before_broker(tmp_path, monkeypatch, enabled):
    monkeypatch.setenv("V6LITE_DISCORD_WEBHOOK", "synthetic-test-placeholder")
    with pytest.raises(ConfigError, match="(?i)live.*test|test.*live"):
        _load(tmp_path, mode="live", account="U0000000", enabled=enabled)


def test_T2_paper_test_requires_DU_account(tmp_path):
    with pytest.raises(ConfigError, match="(?i)DU"):
        _load(tmp_path, account="U0000000")


@pytest.mark.parametrize("directory", ["paper", "paper/dryrun", "paper-delayed"])
def test_T2_paper_test_rejects_shared_or_rehearsal_state(tmp_path, directory):
    with pytest.raises(ConfigError, match="(?i)state|directory|isolat"):
        _load(tmp_path, directory=directory)


def test_T2_paper_test_accepts_isolated_directory_and_fingerprints_parameters(tmp_path):
    enabled = _load(tmp_path)
    assert enabled.test.enabled is True
    assert enabled.test.qty == 3
    assert (enabled.test.stop_mult, enabled.test.tp1_mult,
            enabled.test.tp2_mult, enabled.test.tp3_mult) == (0.3, 0.3, 0.5, 0.8)
    disabled = _load(tmp_path, enabled=False)
    assert disabled.fingerprint() != enabled.fingerprint()
    assert not _load(tmp_path, section=False).test.enabled


def test_T2_empty_custom_formal_style_directory_is_not_a_test_directory(tmp_path):
    with pytest.raises(ConfigError, match="(?i)test|isolat|state"):
        _load(tmp_path, directory="custom-empty")


def test_T1_test_quantity_is_locked_to_Johns_three_shares(tmp_path):
    path = tmp_path / "bad-qty.toml"
    path.write_text(_toml(tmp_path / "paper-test").replace("qty = 3", "qty = 4"),
                    encoding="utf-8")
    with pytest.raises(ConfigError, match="(?i)qty|3"):
        load_config(path)


def _rig(tmp_path, **overrides):
    from v6lite.config import TestConfig

    config = dict(state_dir=str(tmp_path / "paper-test"), scan_times_et=("13:30",),
                  test=TestConfig(enabled=True, qty=3, stop_mult=0.3,
                                  tp1_mult=0.3, tp2_mult=0.5, tp3_mult=0.8))
    config.update(overrides)
    return Rig(tmp_path, **config)


def _request(rig, tmp_path, *, operator="John"):
    from v6lite import __main__ as cli

    path = tmp_path / "test.toml"
    path.write_text(_toml(rig.cfg.resolved_state_dir()), encoding="utf-8")
    with patch.object(cli, "_now", rig.clock.now):
        return cli.main(["test-entry", "AAPL", "--operator", operator,
                         "--config", str(path)])


def _entry_tick(rig, tmp_path):
    rig.start()
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 0
    rig.at(et(10, 1))
    rig.tick()


def test_T3_fixed_three_shares_and_persisted_test_levels(tmp_path):
    rig = _rig(tmp_path)
    _entry_tick(rig, tmp_path)
    buys = [req for req in rig.broker.sent if req.side is Side.BUY]
    assert len(buys) == 1 and buys[0].quantity == 3
    rig.broker.fill(buys[0].intent_id)
    rig.advance(seconds=1)
    rig.tick()
    pos = rig.store.open_positions()[0]
    assert pos["qty_initial"] == pos["qty_open"] == 3
    assert pos["stop"] == round(pos["entry_price"] - 0.3 * pos["atr"], 2)
    for stage, multiplier in (("tp1", 0.3), ("tp2", 0.5), ("tp3", 0.8)):
        assert pos[stage] == round(pos["entry_price"] + multiplier * pos["atr"], 2)
    # The saved plan does not follow later quote or daily-bar changes.
    original = tuple(pos[key] for key in ("stop", "tp1", "tp2", "tp3"))
    rig.px("AAPL", 113.2)
    rig.advance(seconds=1)
    rig.tick()
    assert tuple(rig.store.open_positions()[0][key] for key in
                 ("stop", "tp1", "tp2", "tp3")) == original


def test_T3_fixed_quantity_still_obeys_available_funds(tmp_path):
    rig = _rig(tmp_path)
    rig.broker.available = 100.0  # below three shares plus commission reserve
    _entry_tick(rig, tmp_path)
    assert not rig.broker.sent
    assert not rig.store.open_positions()


def test_T3_fixed_quantity_does_not_shrink_to_circumvent_order_cap(tmp_path):
    rig = _rig(tmp_path, max_order_notional_usd=250.0)
    _entry_tick(rig, tmp_path)
    assert not rig.broker.sent


def test_T4_formal_config_refuses_test_entry_without_touching_control(tmp_path, capsys):
    from v6lite.__main__ import main

    rig = Rig(tmp_path, state_dir=str(tmp_path / "paper"), scan_times_et=("13:30",))
    rig.start()
    path = tmp_path / "formal.toml"
    path.write_text(_toml(rig.cfg.resolved_state_dir(), section=False), encoding="utf-8")
    result = main(["test-entry", "AAPL", "--operator", "John", "--config", str(path)])
    assert result == 2
    assert "REFUSED" in capsys.readouterr().err
    assert rig.store.controls_with_prefix("test_entry:") == {}
    assert rig.broker.sent == []


def test_T4_one_control_is_consumed_once_on_next_tick(tmp_path):
    rig = _rig(tmp_path)
    _entry_tick(rig, tmp_path)
    assert len([req for req in rig.broker.sent if req.side is Side.BUY]) == 1
    rig.advance(seconds=1)
    rig.tick()
    assert len([req for req in rig.broker.sent if req.side is Side.BUY]) == 1
    assert rig.store.controls_with_prefix("test_entry:") == {}


def test_T4_manual_entry_still_obeys_kill(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    rig.store.kill(rig.clock.now(), "test")
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 0
    rig.at(et(10, 1))
    rig.tick()
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}


def test_T4_manual_entry_still_obeys_daily_loss_latch(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    rig.store.latch_daily_stop(rig.clock.now(), "2026-10-06", "synthetic loss")
    assert rig.store.risk_day("2026-10-06")["daily_stop_latched"] == 1
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 0
    rig.at(et(10, 1))
    rig.tick()
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}


def test_T4_unstarted_test_state_refuses_request(tmp_path, capsys):
    rig = _rig(tmp_path)
    assert _request(rig, tmp_path) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert rig.store.controls_with_prefix("test_entry:") == {}


@pytest.mark.parametrize("tick_time", [et(10, 6), et(10, 1, day=(2026, 10, 7))])
def test_T4_stale_or_next_day_request_is_consumed_without_order(tmp_path, tick_time):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 0
    rig.at(tick_time)
    rig.tick()
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}


def test_T4_future_dated_request_is_consumed_without_order(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 5))
    assert _request(rig, tmp_path) == 0
    rig.at(et(10, 1))  # synthetic clock skew
    rig.tick()
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}


def test_T4_slow_history_cannot_submit_after_request_TTL(tmp_path, monkeypatch):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 0
    old_bars = rig.broker.daily_bars

    def delayed_bars(symbol, lookback):
        rig.advance(minutes=6)  # quote stream advances during the stalled history request
        return old_bars(symbol, lookback)

    monkeypatch.setattr(rig.broker, "daily_bars", delayed_bars)
    rig.at(et(10, 1))
    rig.tick()
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}
    results = [json.loads(row["payload"])["outcome"] for row in
               rig.store.conn.execute("SELECT payload FROM audit WHERE kind='TEST_ENTRY_RESULT'")]
    assert any("expired" in outcome for outcome in results)


@pytest.mark.parametrize("slow_step", ["open_orders", "available_funds"])
def test_T4_slow_final_broker_step_cannot_submit_after_request_TTL(tmp_path, monkeypatch,
                                                                    slow_step):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 0
    old_step = getattr(rig.broker, slow_step)
    shifted = []
    in_entry_gate = {"flag": False}
    if slow_step == "available_funds":
        old_gate = rig.engine._entry_gate

        def marked_gate(*args, **kwargs):
            in_entry_gate["flag"] = True
            try:
                return old_gate(*args, **kwargs)
            finally:
                in_entry_gate["flag"] = False

        monkeypatch.setattr(rig.engine, "_entry_gate", marked_gate)

    def delayed_step(*args, **kwargs):
        at_entry_boundary = (slow_step != "available_funds" or
                             in_entry_gate["flag"])
        if at_entry_boundary and not shifted:
            shifted.append(True)
            rig.advance(minutes=6)
        return old_step(*args, **kwargs)

    monkeypatch.setattr(rig.broker, slow_step, delayed_step)
    rig.at(et(10, 1))
    rig.engine._reconcile_due = False
    rig.engine._last_reconcile = rig.clock.now()
    rig.tick()
    assert shifted == [True]
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}


def test_T4_restart_discards_crash_replay_before_broker_connect(tmp_path, monkeypatch, capsys):
    from v6lite.engine import Engine

    rig = _rig(tmp_path)
    rig.start()
    rig.engine.shutdown()
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 2  # orderly shutdown invalidates the ready marker
    assert "REFUSED" in capsys.readouterr().err
    with rig.store.tx():
        rig.store.set_control(rig.clock.now(), "test_entry:crash-replay",
                              json.dumps({"symbol": "AAPL", "operator": "John",
                                          "at": rig.clock.now().isoformat()}))
    assert len(rig.store.controls_with_prefix("test_entry:")) == 1
    old_connect = rig.broker.connect
    saw_connect = []

    def connect_only_after_cleanup():
        saw_connect.append(True)
        assert rig.store.controls_with_prefix("test_entry:") == {}
        return old_connect()

    monkeypatch.setattr(rig.broker, "connect", connect_only_after_cleanup)
    resumed = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock)
    resumed.startup()
    assert saw_connect == [True]
    rig.at(et(10, 1))
    resumed.tick()
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}
    rows = rig.store.conn.execute("SELECT kind, payload FROM audit WHERE kind='TEST_ENTRY_RESULT'").fetchall()
    assert any(json.loads(row["payload"])["outcome"].startswith("refused:test_entry_startup")
               for row in rows)


def test_T4_request_during_startup_connect_cannot_execute_first_tick(tmp_path, monkeypatch):
    rig = _rig(tmp_path)
    rig.at(et(10, 0))
    old_connect = rig.broker.connect
    requested = []

    def request_while_connecting():
        requested.append(_request(rig, tmp_path))
        return old_connect()

    monkeypatch.setattr(rig.broker, "connect", request_while_connecting)
    rig.engine.startup()
    assert requested in ([0], [2])  # a startup-time refusal is also safe
    rig.at(et(10, 1))
    rig.tick()
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}


def test_T4_disconnected_next_tick_consumes_request_before_reconnect(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    rig.at(et(10, 0))
    assert _request(rig, tmp_path) == 0
    rig.broker.refuse_connect = True
    rig.broker.drop_connection()
    rig.at(et(10, 1))
    rig.tick()  # reconnect fails; this is still the next tick
    assert rig.broker.sent == []
    assert rig.store.controls_with_prefix("test_entry:") == {}
    rig.broker.refuse_connect = False
    rig.advance(seconds=31)
    rig.tick()
    assert rig.broker.sent == []


def test_T2_invalid_test_config_never_reaches_gateway_connect(tmp_path, monkeypatch, capsys):
    from v6lite.__main__ import main
    from v6lite.broker.ibkr import IbkrBroker

    calls = []
    monkeypatch.setattr(IbkrBroker, "connect", lambda self: calls.append("connected"))
    path = tmp_path / "invalid.toml"
    path.write_text(_toml(tmp_path / "paper"), encoding="utf-8")
    assert main(["run", "--config", str(path)]) == 2
    assert "CONFIG ERROR" in capsys.readouterr().err
    assert calls == []


def test_T2_existing_formal_state_is_rejected_before_gateway(tmp_path, monkeypatch, capsys):
    from v6lite.__main__ import main
    from v6lite.broker.ibkr import IbkrBroker

    shared = tmp_path / "custom-test-shared"
    formal = Rig(tmp_path, state_dir=str(shared), scan_times_et=("13:30",))
    formal.start()
    calls = []
    monkeypatch.setattr(IbkrBroker, "connect", lambda self: calls.append("connected"))
    path = tmp_path / "test.toml"
    path.write_text(_toml(shared), encoding="utf-8")
    assert main(["run", "--config", str(path)]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert calls == []


def test_T5_startup_card_and_report_distinguish_test_mode(tmp_path):
    from v6lite.report import daily_report

    rig = _rig(tmp_path)
    rig.start()
    card = [body for subject, body in rig.channel.messages if "已启动" in subject or "started" in subject]
    rendered = "\n".join(card + [rig.messages()])
    assert "TEST" in rendered
    assert "🧪 测试模式（仅 Paper）" in rendered
    assert all(x in rendered for x in ("3", "0.3", "0.5", "0.8"))
    assert "test=1" in daily_report(rig.store, rig.cfg, date(2026, 10, 6))


def test_T5_P0_P1_alert_titles_have_TEST_prefix(tmp_path):
    rig = _rig(tmp_path)
    rig.start()
    before = len(rig.channel.messages)
    rig.alerter.send(rig.clock.now(), "P0", "synthetic stop AAPL", "synthetic test only")
    rig.alerter.send(rig.clock.now(), "P1", "synthetic order AAPL", "synthetic test only")
    pages = rig.channel.messages[before:]
    assert len(pages) == 2
    assert all("TEST" in subject for subject, _ in pages)


def test_T5_WOULD_BUY_and_entry_exit_logs_carry_test_flag(tmp_path, caplog):
    caplog.set_level("INFO")
    dry = _rig(tmp_path / "dry", dry_run=True, scan_times_et=("10:00",))
    dry.start()
    dry.at(et(10, 0, 30))
    dry.tick()
    assert any("WOULD_BUY" in line and "test=1" in line
               for line in caplog.text.splitlines())

    rig = _rig(tmp_path / "formal")
    _entry_tick(rig, tmp_path / "formal")
    buy = rig.broker.sent[-1]
    assert any("[ORDER_SENT]" in line and "ENTRY" in line and "test=1" in line
               for line in caplog.text.splitlines())
    rig.broker.fill(buy.intent_id)
    rig.advance(seconds=1)
    rig.tick()
    rig.advance(seconds=31)
    rig.px("AAPL", 120.0)
    rig.tick()
    assert any("[ORDER_SENT]" in line and "TP1" in line and "test=1" in line
               for line in caplog.text.splitlines())


def test_T6_formal_paper_regression_keeps_old_size_and_levels(tmp_path):
    rig = Rig(tmp_path)
    rig.open_aapl()
    entry = rig.broker.sent[0]
    pos = rig.store.open_positions()[0]
    assert entry.quantity == 20
    assert (pos["stop"], pos["tp1"], pos["tp2"], pos["tp3"]) == (
        108.47, 119.47, 123.47, 129.47)

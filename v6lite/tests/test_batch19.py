"""Batch 19 phone mode labels and daily quote evidence; TEMP FakeBroker only.

No CLI, AccountLock, Gateway, network, runtime configuration, operator state,
or real secret values are used. Existing tests and production code stay intact.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import os
from pathlib import Path
import re
import tomllib
import smtplib
import socket
import urllib.request

import pytest

from v6lite.config import ProtectionConfig, TestConfig as LiteTestConfig, load_config
from v6lite.report import daily_health_card, daily_health_report, daily_report
from v6lite.tests.helpers import Rig, et

MARKERS = ("演练（不下单）", "真实下单（Paper）", "真实下单（Live）")
CASES = (
    ("paper", False, True, MARKERS[0]),
    ("paper", True, True, MARKERS[0]),
    ("paper", False, False, MARKERS[1]),
    ("paper", True, False, MARKERS[1]),
    ("live", False, True, MARKERS[0]),
    ("live", False, False, MARKERS[2]),
)
DAY = date(2026, 10, 6)


@pytest.fixture(autouse=True)
def no_external_boundaries_or_secret_reads(monkeypatch):
    from ib_insync import IB

    def forbidden(*args, **kwargs):
        raise AssertionError("Batch 19 tests use only TEMP FakeBroker/Store")

    monkeypatch.setattr(IB, "connect", forbidden)
    monkeypatch.setattr(IB, "connectAsync", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    # Config validation may check whether a remote channel is configured.
    # Synthetic email configuration is sufficient; never read a real secret.
    original_get = os.environ.get

    def secretless_get(key, default=None):
        if key in {"V6LITE_DISCORD_WEBHOOK", "V6LITE_SMTP_PASSWORD"}:
            return default
        return original_get(key, default)

    monkeypatch.setattr(os.environ, "get", secretless_get)


@contextmanager
def scenario(tmp_path, *, mode="paper", test=False, dry=True):
    overrides = dict(
        mode=mode, account="U0000000" if mode == "live" else "DU0000000",
        port=4001 if mode == "live" else 4002, client_id=132 if test else 131,
        state_dir=str(tmp_path / ("paper-test" if test else "synthetic-state")),
        test=LiteTestConfig(enabled=test),
        protection=ProtectionConfig(enabled=True),
        scan_times_et=("13:30",),
    )
    rig = Rig(
        tmp_path, start=et(9), dry_run=dry,
        approval_checker=(lambda now: True), **overrides,
    )
    try:
        rig.start()
        yield rig
    finally:
        rig.alerter.close()
        rig.store.close()


def unique_marker(text, expected):
    assert {marker for marker in MARKERS if marker in text} == {expected}, text
    assert text.count(expected) == 1, text


def daily_message(rig):
    found = [
        (subject, body) for subject, body in rig.channel.messages
        if "行情可用率：" in body and "code_sha：" in body
    ]
    assert len(found) == 1
    return found[0]


def report_market_line(rig, day):
    report = rig.engine.report_dir / f"{day.isoformat()}.md"
    assert report.is_file()
    return next(
        line for line in report.read_text(encoding="utf-8").splitlines()
        if line.startswith("- market data:")
    )


def phone_market_line(rig):
    _, body = daily_message(rig)
    return next(line for line in body.splitlines() if line.startswith("行情可用率："))


def finish_day(rig, *, day=(2026, 10, 6)):
    # A real post-close heartbeat refreshes the instantaneous health snapshot.
    # The accumulated RTH sample evidence must survive this stale observation.
    rig.clock.set(et(16, 6, day=day))
    rig.engine._heartbeat(rig.clock.now())
    rig.engine._end_of_day(rig.clock.now())


@pytest.mark.parametrize("mode,test,dry,expected", CASES)
def test_startup_title_and_mode_field_have_unique_execution_marker(
        tmp_path, mode, test, dry, expected):
    with scenario(tmp_path, mode=mode, test=test, dry=dry) as rig:
        card = rig.engine._startup_card(
            rig.clock.now(), rig.engine.status_snapshot(rig.clock.now())
        )
        unique_marker(card.title, expected)
        unique_marker(dict(card.fields)["模式"], expected)
        delivered = [
            (subject, body) for subject, body in rig.channel.messages if "已启动" in body
        ]
        assert len(delivered) == 1
        unique_marker(delivered[0][0], expected)
        mode_line = next(
            line for line in delivered[0][1].splitlines() if line.startswith("模式：")
        )
        unique_marker(mode_line, expected)
        if test:
            assert "测试模式（仅 Paper）" in dict(card.fields)["模式"]
            assert all(value in dict(card.fields)["模式"] for value in ("3", "0.3", "0.5", "0.8"))
        assert rig.broker.sent == [] and rig.broker.cancels == []


@pytest.mark.parametrize("mode,test,dry,expected", CASES)
def test_daily_title_has_unique_execution_marker_through_engine_delivery(
        tmp_path, mode, test, dry, expected):
    with scenario(tmp_path, mode=mode, test=test, dry=dry) as rig:
        rig.at(et(10, 5))
        rig.engine._heartbeat(rig.clock.now())
        finish_day(rig)
        subject, body = daily_message(rig)
        unique_marker(subject, expected)
        unique_marker(body.splitlines()[0], expected)
        if test:
            assert "TEST" in subject
        assert rig.broker.sent == [] and rig.broker.cancels == []


@pytest.mark.parametrize("test_mode", (False, True))
def test_daily_health_card_legacy_test_mode_call_remains_compatible(tmp_path, test_mode):
    with scenario(tmp_path, dry=False) as rig:
        remote = daily_health_report(
            rig.store, rig.cfg, DAY, status=rig.engine.status_snapshot(rig.clock.now()),
            gateway=rig.engine.gateway_heartbeat_stats(),
            alerts=rig.alerter.delivery_stats(DAY),
            quotes={"samples": 2, "usable": 6, "total": 6, "subscribed": 3},
        )
        card = daily_health_card(remote, DAY, test_mode=test_mode)
        assert {name for name, _ in card.fields} >= {"行情可用率", "code_sha", "扫描"}
        unique_marker(card.title, MARKERS[1])
        assert ("TEST" in card.title) == test_mode


@pytest.mark.parametrize("second_sample_usable,expected_percent,expected_ratio", (
    (True, "100%", "6/6"), (False, "50%", "3/6"),
))
def test_local_market_data_uses_same_rth_samples_as_phone_after_close(
        tmp_path, second_sample_usable, expected_percent, expected_ratio):
    with scenario(tmp_path) as rig:
        rig.at(et(10, 5))
        rig.engine._heartbeat(rig.clock.now())
        if second_sample_usable:
            rig.at(et(10, 6))
        else:
            rig.clock.set(et(10, 6))  # prior quotes age beyond the freshness gate
        rig.engine._heartbeat(rig.clock.now())
        expected_usable = 6 if second_sample_usable else 3
        assert rig.engine.quote_availability_stats(DAY) == {
            "samples": 2, "usable": expected_usable, "total": 6, "subscribed": 3,
        }
        finish_day(rig)
        assert rig.engine._market_data_status()["usable_quotes"] == 0
        local, phone = report_market_line(rig, DAY), phone_market_line(rig)
        assert expected_percent in phone and expected_ratio in phone
        assert expected_percent in local and expected_ratio in local
        assert "no usable quotes 3/3" not in local
        assert re.search(r"\b2\s+(?:samples|次采样)", local)
        assert rig.broker.sent == [] and rig.broker.cancels == []


@pytest.mark.parametrize("previous_day_samples", (False, True))
def test_local_report_does_not_invent_rth_availability_or_reuse_previous_day(
        tmp_path, previous_day_samples):
    with scenario(tmp_path) as rig:
        if previous_day_samples:
            rig.at(et(10, 5))
            rig.engine._heartbeat(rig.clock.now())
            assert rig.engine.quote_availability_stats(DAY)["usable"] == 3
            report_day = date(2026, 10, 7)
        else:
            report_day = DAY
        finish_day(rig, day=(report_day.year, report_day.month, report_day.day))
        stats = rig.engine.quote_availability_stats(report_day)
        assert stats["samples"] == stats["total"] == stats["usable"] == 0
        local, phone = report_market_line(rig, report_day), phone_market_line(rig)
        assert "暂无数据" in phone
        assert not re.search(r"\d+(?:\.\d+)?%", local)
        assert any(text in local for text in (
            "n/a", "no RTH quote samples", "暂无数据", "无常规交易时段样本", "无样本",
        ))
        assert "no usable quotes 3/3" not in local
        assert rig.broker.sent == [] and rig.broker.cancels == []


def test_daily_report_legacy_call_remains_readable_without_runtime_stats(tmp_path):
    with scenario(tmp_path) as rig:
        report = daily_report(rig.store, rig.cfg, DAY)
        assert report.startswith("# V6-Lite daily report")
        assert "- market data:" in report and "- account:" in report
        assert "## Fills" in report and "## Events" in report

def test_g3n_example_loads_complete_formal_identity_without_creating_state(monkeypatch):
    """Read repository examples only; never expand or visit operator state."""
    def no_directory_creation(*args, **kwargs):
        raise AssertionError("Configuration loading must not create any directory")

    monkeypatch.setattr(Path, "mkdir", no_directory_creation)
    monkeypatch.setattr(os, "mkdir", no_directory_creation)
    examples = Path(__file__).resolve().parents[1] / "examples"
    path = examples / "paper_g3n.toml"
    cfg = load_config(path)  # natural baseline red: the requested example is absent
    assert cfg.mode == "paper" and cfg.account == "DU0000000"
    assert cfg.host in {"127.0.0.1", "localhost", "::1"}
    assert cfg.port == 7497 and cfg.client_id == 131
    assert cfg.market_data_type == 1 and cfg.test.enabled is False
    assert cfg.protection == ProtectionConfig(
        enabled=True, order_type="STP", gap_a_p1_seconds=5,
        gap_a_p0_seconds=15, gap_b_cancel_seconds=10, gap_b_p0_seconds=30,
        trigger_unfilled_p0_seconds=60, steady_check_seconds=15,
        evidence_fail_p0_seconds=60,
    )
    assert cfg.scan_times_et == ("10:00", "13:30")
    assert cfg.state_dir == "~/.v6lite/paper-g3n"
    # Compare raw example strings; avoid test-mode path expansion / resolve.
    existing_dirs = {
        tomllib.loads((examples / name).read_text(encoding="utf-8"))["paths"]["state_dir"]
        for name in ("paper.toml", "paper_test.toml")
    }
    assert cfg.state_dir not in existing_dirs
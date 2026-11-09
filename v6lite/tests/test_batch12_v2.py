"""Batch 12 v2: Chinese Discord cards, old-V6 gate and ET scheduling.

All broker paths use FakeBroker; HTTP, SMTP and process enumeration are fakes.
"""

from __future__ import annotations

import json
import re
import smtplib
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from v6lite.alerts import Alerter, DiscordWebhookChannel, EmailChannel, MemoryChannel
from v6lite.config import AlertConfig, load_config
from v6lite.market_calendar import ET, et_time_on, session_for
from v6lite.tests.helpers import Rig, et


PT = ZoneInfo("America/Los_Angeles")
FAKE_URL = "https://discord.com/api/webhooks/000/FAKE-DO-NOT-SEND"


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    from ib_insync import IB

    def forbidden(*args, **kwargs):
        raise AssertionError("Batch 12 v2 tests must not connect to Gateway")

    monkeypatch.setattr(IB, "connect", forbidden)
    monkeypatch.setattr(IB, "connectAsync", forbidden)


class _HTTP204:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _capture_transports(monkeypatch):
    payloads, mails = [], []

    def urlopen(req, timeout):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _HTTP204()

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def send_message(self, msg):
            mails.append(msg)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    return payloads, mails


def _card_rig(tmp_path, monkeypatch, **kwargs):
    payloads, mails = _capture_transports(monkeypatch)
    rig = Rig(tmp_path, dry_run=True, alerts=AlertConfig(
        email_to="ops@example.invalid", email_from="bot@example.invalid",
        smtp_host="smtp.example.invalid"), **kwargs)
    rig.alerter = Alerter([DiscordWebhookChannel(FAKE_URL), EmailChannel(rig.cfg.alerts, "")],
                          synchronous=True)
    rig.engine.alerter = rig.alerter
    return rig, payloads, mails


def _close(rig):
    rig.alerter.close()
    rig.store.close()


def _find_embed(payloads, title_part):
    embeds = [embed for payload in payloads for embed in payload.get("embeds", [])]
    found = [embed for embed in embeds if title_part in embed.get("title", "")]
    assert found, f"no Discord embed title containing {title_part!r}"
    return found[-1]


def _mail_text(mails, heading):
    found = [mail.get_content() for mail in mails if heading in mail.get_content()]
    assert found, f"no plaintext mail containing {heading!r}"
    return found[-1]


def _rgb(color):
    assert isinstance(color, int) and 0 <= color <= 0xFFFFFF
    return (color >> 16) & 255, (color >> 8) & 255, color & 255


def test_G1_chinese_startup_embed_has_six_fields_and_mail_plaintext(tmp_path, monkeypatch):
    rig, payloads, mails = _card_rig(tmp_path, monkeypatch, market_data_type=3,
                                     scan_times_et=("10:00", "13:30"))
    try:
        rig.start()
        embed = _find_embed(payloads, "已启动")
        assert "V6-Lite" in embed["title"]
        red, green, blue = _rgb(embed["color"])
        assert green > red and green > blue  # INFO green
        names = [field["name"] for field in embed["fields"]]
        assert names == ["启动时间", "模式", "状态", "监控", "扫描时间", "手动指令（本机命令行）"]
        values = "\n".join(field["value"] for field in embed["fields"])
        assert "PT" in values and "ET" in values
        assert "06:50" in values and "09:50" in values
        assert "每 60 秒" in values and "每 300 秒" in values
        assert "10:00 ET / 07:00 PT" in values and "13:30 ET / 10:30 PT" in values
        assert "不能在 Discord" in values and "code_sha" not in values
        text = _mail_text(mails, "启动时间")
        assert "手动指令" in text and "监控" in text and "PT" in text and "ET" in text
        assert "\"embeds\"" not in text and "```" not in text
    finally:
        _close(rig)


def test_G3_chinese_daily_health_embed_and_plaintext_mail(tmp_path, monkeypatch):
    rig, payloads, mails = _card_rig(tmp_path, monkeypatch, start=et(9, 50))
    try:
        rig.start()
        rig.at(et(10, 0, 30))
        rig.tick()
        rig.at(et(16, 6))
        rig.tick()
        embed = _find_embed(payloads, "健康")
        assert embed["title"].startswith(("✅ 正常", "⚠️ 需关注"))
        names = [field["name"] for field in embed["fields"]]
        assert names == ["扫描", "下单/撤单", "持仓与漂移", "券商端保护", "IBKR 心跳",
                         "告警送达", "行情可用率", "code_sha"]
        values = "\n".join(field["value"] for field in embed["fields"])
        assert "WOULD_BUY" in values and "gate=" in values
        assert re.search(r"\b0\b.*\b0\b", embed["fields"][1]["value"])
        assert (tmp_path / "reports" / "2026-10-06.md").read_text(encoding="utf-8").startswith(
            "# V6-Lite daily report")
        text = _mail_text(mails, "行情可用率")
        assert "扫描" in text and "告警送达" in text and "code_sha" in text
        assert "\"embeds\"" not in text
    finally:
        _close(rig)


@pytest.mark.parametrize("dry_run,market_data_type", [(True, 3), (False, 1)])
def test_G3_daily_card_translates_status_values_but_preserves_operator_tokens(
        tmp_path, monkeypatch, dry_run, market_data_type):
    payloads, _ = _capture_transports(monkeypatch)
    rig = Rig(tmp_path, dry_run=dry_run, start=et(9, 50), market_data_type=market_data_type)
    rig.alerter = Alerter([DiscordWebhookChannel(FAKE_URL)], synchronous=True)
    rig.engine.alerter = rig.alerter
    if market_data_type == 3:
        rig.broker.market_data_status = lambda: {
            "requested": "DELAYED", "delayed_symbols": [], "no_subscription": []}
    try:
        rig.start()
        rig.at(et(10, 0, 30))
        rig.tick()
        rig.at(et(16, 6))
        rig.tick()
        embed = _find_embed(payloads, "健康")
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        scan = fields["扫描"]
        position = fields["持仓与漂移"]
        quotes = fields["行情可用率"]
        if dry_run:
            assert "WOULD_BUY" in scan
        else:
            assert "not persisted" not in scan and "full log" not in scan
        assert "gate=" in scan
        assert "signals=" not in scan and "entered=" not in scan
        assert "none" not in position.lower()
        assert "无" in position
        assert ("延迟行情" if market_data_type == 3 else "实时行情") in quotes
        assert "DELAYED" not in quotes and "LIVE" not in quotes
        assert "code_sha" in fields
    finally:
        _close(rig)


def test_G3_multiline_scan_error_cannot_break_daily_card_delivery(tmp_path, monkeypatch):
    payloads, _ = _capture_transports(monkeypatch)
    rig = Rig(tmp_path, dry_run=True, start=et(9, 50))
    rig.alerter = Alerter([DiscordWebhookChannel(FAKE_URL)], synchronous=True)
    rig.engine.alerter = rig.alerter
    try:
        rig.start()
        rig.store.set_control(rig.clock.now(), "scan:2026-10-06:10:00", "error:line1\nline2")
        rig.at(et(16, 6))
        rig.tick()
        daily = [embed for payload in payloads for embed in payload.get("embeds", [])
                 if any(field["name"] == "行情可用率" for field in embed.get("fields", []))]
        assert len(daily) == 1
        assert any(field["name"] == "扫描" for field in daily[0]["fields"])
        assert (tmp_path / "reports" / "2026-10-06.md").exists()
        assert str(rig.store.control("report:2026-10-06")).startswith("sent:")
        assert not rig.broker.sent and not rig.broker.cancels
    finally:
        _close(rig)


def test_G3_scan_exception_detail_stays_local_not_in_phone_card(tmp_path, monkeypatch, caplog):
    payloads, _ = _capture_transports(monkeypatch)
    rig = Rig(tmp_path, dry_run=True, start=et(9, 50))
    rig.alerter = Alerter([DiscordWebhookChannel(FAKE_URL)], synchronous=True)
    rig.engine.alerter = rig.alerter
    raw_error = "error:foo; bar"
    try:
        rig.start()
        def fail_scan(_now):
            raise RuntimeError("foo; bar")

        rig.engine.scan_and_enter = fail_scan
        rig.at(et(10, 0, 30))
        rig.tick()
        assert rig.store.control("scan:2026-10-06:10:00") == raw_error
        rig.at(et(16, 6))
        rig.tick()
        daily = [embed for payload in payloads for embed in payload.get("embeds", [])
                 if any(field["name"] == "行情可用率" for field in embed.get("fields", []))]
        assert len(daily) == 1
        scan = next(field["value"] for field in daily[0]["fields"] if field["name"] == "扫描")
        assert "扫描失败（详见日志）" in scan
        card_text = json.dumps(daily[0], ensure_ascii=False)
        assert "foo" not in card_text and "bar" not in card_text
        assert "foo; bar" in caplog.text
        assert (tmp_path / "reports" / "2026-10-06.md").read_text(encoding="utf-8").startswith(
            "# V6-Lite daily report")
        assert not rig.broker.sent and not rig.broker.cancels
    finally:
        _close(rig)


@pytest.mark.parametrize("level,subject,dominant", [
    ("P1", "no delayed market data", "orange"),
    ("P0", "exit blocked AAPL STOP", "red"),
])
def test_G4_chinese_page_embed_has_three_action_fields_and_level_color(
        tmp_path, monkeypatch, level, subject, dominant):
    payloads, mails = _capture_transports(monkeypatch)
    cfg = AlertConfig(email_to="ops@example.invalid", email_from="bot@example.invalid",
                      smtp_host="smtp.example.invalid")
    alerter = Alerter([DiscordWebhookChannel(FAKE_URL), EmailChannel(cfg, "")], synchronous=True)
    try:
        alerter.send(et(10), level, subject, "AAPL: check the underlying condition in TWS")
        embeds = [embed for payload in payloads for embed in payload.get("embeds", [])]
        assert len(embeds) == 1
        embed = embeds[0]
        assert [f["name"] for f in embed["fields"]] == ["发生了什么", "影响", "你要做什么"]
        red, green, blue = _rgb(embed["color"])
        assert red > blue and (red > green if dominant == "red" else green > blue)
        assert "AAPL" in " ".join(f["value"] for f in embed["fields"])
        text = _mail_text(mails, "发生了什么")
        assert "影响" in text and "你要做什么" in text and "\"embeds\"" not in text
    finally:
        alerter.close()


def test_G4_webhook_failure_never_logs_its_address(tmp_path, monkeypatch, caplog):
    import io
    import urllib.error

    def reject(req, timeout):
        raise urllib.error.HTTPError(FAKE_URL, 403, "Forbidden", {},
                                     io.BytesIO(("rejected " + FAKE_URL).encode()))

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    alerter = Alerter([DiscordWebhookChannel(FAKE_URL)], synchronous=True)
    try:
        alerter.send(et(10), "P1", "no delayed market data", "AAPL unavailable")
        assert not alerter.healthy(et(10))
        assert FAKE_URL not in caplog.text and "FAKE-DO-NOT-SEND" not in caplog.text
    finally:
        alerter.close()


@pytest.mark.parametrize("level", ["P0", "P1", "INFO", "P2"])
def test_G4_generic_card_translates_subject_but_local_log_keeps_original(
        monkeypatch, caplog, level):
    caplog.set_level("INFO")
    payloads, _ = _capture_transports(monkeypatch)
    alerter = Alerter([DiscordWebhookChannel(FAKE_URL)], synchronous=True)
    subject = "opaque queue incoherence AAPL"
    try:
        alerter.send(et(10), level, subject, "diagnostic-correlation-42")
        assert len(payloads) == 1 and len(payloads[0].get("embeds", [])) == 1
        card_json = json.dumps(payloads[0]["embeds"][0], ensure_ascii=False)
        assert "opaque queue incoherence" not in card_json.lower()
        assert "AAPL" in card_json
        assert subject in caplog.text
    finally:
        alerter.close()


def test_G1_oversized_card_stays_within_discord_limits_and_marks_truncation():
    from v6lite.alerts import AlertCard

    card = AlertCard(
        title="V6-Lite " + "启动" * 200,
        color=0x2ECC71,
        description="说明" * 3000,
        fields=tuple((f"项目 {index}", "行情" * 300) for index in range(30)),
    )
    embed = card.as_embed()
    assert len(embed["fields"]) <= 25
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += sum(len(field["name"]) + len(field["value"]) for field in embed["fields"])
    assert total <= 6000
    assert len(embed["title"]) <= 256
    assert all(len(field["name"]) <= 256 and len(field["value"]) <= 1024
               for field in embed["fields"])
    rendered = json.dumps(embed, ensure_ascii=False) + card.as_plain_text()
    assert "详见日志" in rendered
    assert "项目 29" not in card.as_plain_text()


def test_G1_embed_limits_count_utf16_units_for_emoji():
    from v6lite.alerts import AlertCard

    units = lambda value: len(value.encode("utf-16-le")) // 2
    card = AlertCard(
        title="😀" * 200,
        color=0x2ECC71,
        fields=tuple(("😀" * 200, "😀" * 600) for _ in range(20)),
    )
    embed = card.as_embed()
    assert units(embed["title"]) <= 256
    assert len(embed["fields"]) <= 25
    assert all(units(field["name"]) <= 256 and units(field["value"]) <= 1024
               for field in embed["fields"])
    total = units(embed["title"]) + units(embed.get("description", ""))
    total += sum(units(field["name"]) + units(field["value"]) for field in embed["fields"])
    assert total <= 6000
    assert "详见日志" in json.dumps(embed, ensure_ascii=False)


def test_G8_foreign_lock_paths_resolve_from_config_directory_not_cwd(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    elsewhere = tmp_path / "elsewhere"
    config_dir.mkdir()
    elsewhere.mkdir()
    config_path = config_dir / "paper.toml"
    config_path.write_text("""mode = "paper"
[ibkr]
account = "DU0000000"
port = 4002
client_id = 131
[universe]
scan = ["AAPL"]
tradable = ["AAPL"]
[paths]
foreign_lock_paths = ["../legacy/trading_bot.lock"]
""", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    cfg = load_config(config_path)
    assert Path(cfg.foreign_lock_paths[0]) == (tmp_path / "legacy" / "trading_bot.lock").resolve()


def test_G8_detect_only_python_old_v6_entrypoints_without_pid_reuse(tmp_path, monkeypatch):
    from v6lite import approval

    processes = [
        SimpleNamespace(info={"pid": 101, "name": "python.exe",
                              "cmdline": ["python.exe", "trading_bot_v6_main.py"]}),
        SimpleNamespace(info={"pid": 102, "name": "pythonw.exe",
                              "cmdline": ["pythonw.exe", "auto_trading_runner.py"]}),
        SimpleNamespace(info={"pid": 103, "name": "python.exe",
                              "cmdline": ["python.exe", "discord_bot_runner.py"]}),
        SimpleNamespace(info={"pid": 104, "name": "python.exe",
                              "cmdline": ["python.exe", "unrelated.py", "trading_bot_v6_main.py.bak"]}),
        SimpleNamespace(info={"pid": 105, "name": "not-python.exe",
                              "cmdline": ["not-python.exe", "auto_trading_runner.py"]}),
    ]
    fake_psutil = SimpleNamespace(process_iter=lambda *args, **kwargs: iter(processes))
    monkeypatch.setattr(approval, "psutil", fake_psutil, raising=False)
    assert approval.old_v6_processes() == (101, 102, 103)


def test_G8_pytest_process_with_inaccessible_cmdline_is_not_python_launcher(monkeypatch):
    from v6lite import approval

    processes = [SimpleNamespace(info={"pid": 88888, "name": "pytest.exe", "cmdline": None})]
    monkeypatch.setattr(approval.psutil, "process_iter", lambda *args, **kwargs: iter(processes))
    assert approval.old_v6_processes() == ()


def test_G8_python_process_with_empty_cmdline_is_unknown(monkeypatch):
    from v6lite import approval

    processes = [SimpleNamespace(info={"pid": 88888, "name": "python.exe", "cmdline": []})]
    monkeypatch.setattr(approval.psutil, "process_iter", lambda *args, **kwargs: iter(processes))
    with pytest.raises(approval.GateError, match="cannot verify"):
        approval.old_v6_processes()


def test_G8_formal_refuses_old_v6_but_dry_run_pages_and_reports_pid(tmp_path, monkeypatch, capsys):
    from v6lite import approval, alerts
    from v6lite.tests.test_batch10 import _fake_cli

    monkeypatch.setattr(approval, "old_v6_processes", lambda: (4321,), raising=False)
    formal_capture = {}
    cli, formal_broker = _fake_cli(tmp_path / "formal", monkeypatch, capture=formal_capture)
    assert cli.main(["run", "--config", str(tmp_path / "formal" / "fake.toml")]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert not formal_broker.connected and not formal_broker.sent

    dry_capture = {}
    cli, dry_broker = _fake_cli(tmp_path / "dry", monkeypatch, capture=dry_capture)
    channel = MemoryChannel()
    monkeypatch.setattr(alerts, "build_channels", lambda cfg: [channel])
    assert cli.main(["run", "--dry-run", "--config", str(tmp_path / "dry" / "fake.toml")]) == 0
    assert not dry_broker.sent and not dry_broker.cancels
    assert any("P1" in subject and "旧 V6" in subject for subject, _ in channel.messages)
    started = [body for subject, body in channel.messages if "已启动" in subject]
    assert started and "旧 V6：运行中" in started[-1] and "4321" in started[-1]


def test_G8_process_check_failure_refuses_formal_and_pages_dry_run(tmp_path, monkeypatch, capsys):
    from v6lite import approval, alerts
    from v6lite.tests.test_batch10 import _fake_cli

    def inaccessible():
        raise OSError("process enumeration unavailable")

    monkeypatch.setattr(approval, "old_v6_processes", inaccessible, raising=False)
    cli, formal_broker = _fake_cli(tmp_path / "formal", monkeypatch)
    assert cli.main(["run", "--config", str(tmp_path / "formal" / "fake.toml")]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert not formal_broker.connected and not formal_broker.sent

    cli, dry_broker = _fake_cli(tmp_path / "dry", monkeypatch, capture={})
    channel = MemoryChannel()
    monkeypatch.setattr(alerts, "build_channels", lambda cfg: [channel])
    assert cli.main(["run", "--dry-run", "--config", str(tmp_path / "dry" / "fake.toml")]) == 0
    assert not dry_broker.sent and not dry_broker.cancels
    assert any("P1" in subject and "旧 V6" in subject for subject, _ in channel.messages)
    started = [body for subject, body in channel.messages if "已启动" in subject]
    assert started and "旧 V6：无法核实" in started[-1]


@pytest.mark.parametrize("day", [date(2026, 11, 2), date(2027, 3, 15)])
def test_G9_et_scan_slots_keep_pt_mapping_after_dst_change(day, tmp_path):
    for hhmm, pt_hhmm in (("10:00", "07:00"), ("13:30", "10:30")):
        instant = et_time_on(day, hhmm)
        assert instant.astimezone(ET).strftime("%H:%M") == hhmm
        assert instant.astimezone(PT).strftime("%H:%M") == pt_hhmm
    rig = Rig(tmp_path, start=et(9, 55, day=(day.year, day.month, day.day)),
              scan_times_et=("10:00", "13:30"))
    called = []
    rig.engine.scan_and_enter = lambda now: (called.append(now), "synthetic")[1]
    try:
        rig.start()
        assert called == []
        for hhmm in ("10:00", "13:30"):
            rig.at(et_time_on(day, hhmm))
            rig.tick()
        assert [when.astimezone(ET).strftime("%H:%M") for when in called] == ["10:00", "13:30"]
        assert [when.astimezone(PT).strftime("%H:%M") for when in called] == ["07:00", "10:30"]
    finally:
        _close(rig)


def test_G9_black_friday_daily_report_is_close_plus_five_in_both_zones(tmp_path):
    day = date(2026, 11, 27)
    session = session_for(day)
    assert session is not None and session.close_utc.astimezone(ET).strftime("%H:%M") == "13:00"
    rig = Rig(tmp_path, start=et(12, 59, day=(2026, 11, 27)))
    try:
        rig.start()
        rig.at(et(13, 4, day=(2026, 11, 27)))
        rig.tick()
        assert not any("每日健康报告" in subject for subject, _ in rig.channel.messages)
        rig.at(et(13, 5, day=(2026, 11, 27)))
        rig.tick()
        assert any("每日健康报告" in subject for subject, _ in rig.channel.messages)
        assert rig.clock.now().astimezone(PT).strftime("%H:%M") == "10:05"
    finally:
        _close(rig)

"""Batch 12 alert acceptance tests. All broker evidence is fake and offline."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest

from v6lite.alerts import Alerter, MemoryChannel
from v6lite.tests.helpers import Rig, et


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    from ib_insync import IB

    def forbidden(*args, **kwargs):
        raise AssertionError("Batch 12 tests must not connect to Gateway")

    monkeypatch.setattr(IB, "connect", forbidden)
    monkeypatch.setattr(IB, "connectAsync", forbidden)


def _message(rig, fragment):
    matches = [(subject, body) for subject, body in rig.channel.messages if fragment in subject]
    if fragment == "每日健康报告" and not matches:
        matches = [(subject, body) for subject, body in rig.channel.messages
                   if "行情可用率：" in body and "code_sha：" in body]
    assert matches, f"missing remote message containing {fragment!r}"
    return matches[-1]


def _close(rig):
    rig.alerter.close()
    rig.store.close()


def test_G1_startup_is_six_short_dynamic_sections_and_keeps_full_audit(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    # Black Friday closes at 13:00 ET: the health report must move with it.
    day = (2026, 11, 27)
    rig = Rig(tmp_path, start=et(9, day=day), dry_run=True, market_data_type=3,
              reconcile_interval_seconds=420, scan_times_et=("10:15", "12:00"))
    rig.broker.market_data_status = lambda: {
        "requested": "DELAYED", "delayed_symbols": [], "no_subscription": []}
    try:
        rig.start()
        _, body = _message(rig, "已启动")
        for section in ("启动时间", "模式", "状态", "监控", "扫描时间", "手动指令（本机命令行）"):
            assert section in body, section
        assert "PT" in body and "ET" in body
        assert "演练" in body and "延迟行情" in body
        assert "420 秒" in body
        assert "10:15 ET" in body and "12:00 ET" in body
        assert "10:05 PT" in body  # early-close 13:00 ET + 5 minutes
        assert "status" in body and "report" in body and "kill --reason" in body
        assert "不能在 Discord" in body
        assert "config_fingerprint" not in body
        audit = rig.store.conn.execute("SELECT payload FROM audit WHERE kind='STARTUP'").fetchone()
        assert "config_fingerprint" in json.loads(audit["payload"])
        assert any("[STARTUP_SUMMARY]" in rec.getMessage() and "config_fingerprint" in rec.getMessage()
                   for rec in caplog.records)
    finally:
        _close(rig)


def test_G1_attention_names_abnormal_quotes_and_ignored_holdings(tmp_path):
    rig = Rig(tmp_path, dry_run=True)
    now = rig.clock.now()
    rig.store.set_disposition(now, "ABT", "ignore", "test")
    summary = {"broker_connected": True, "reconcile_ok": False, "alerts_healthy": True,
               "entry_blocks": ["broker_runtime_mismatch:AAPL"], "open_positions": [],
               "market_data": {"quote_symbols": 2, "usable_quotes": 0}}
    try:
        body = rig.engine._startup_remote_message(now, summary)
        assert "Status    ATTENTION" in body
        assert "AAPL" in body and "reconcile ATTENTION" in body
        assert "Quotes 0/2 usable" in body
        assert "Ignored holdings: ABT" in body
        assert "approve-live" not in body and "revoke-live" not in body
        assert len(body.splitlines()) <= 15
    finally:
        _close(rig)


def test_G1_live_only_commands_appear_only_for_live_configuration(tmp_path):
    rig = Rig(tmp_path, mode="live", account="U0000000", port=4001)
    summary = {"broker_connected": True, "reconcile_ok": True, "alerts_healthy": True,
               "entry_blocks": [], "open_positions": [],
               "market_data": {"quote_symbols": 3, "usable_quotes": 3}}
    try:
        body = rig.engine._startup_remote_message(rig.clock.now(), summary)
        assert "Mode      LIVE" in body
        assert "approve-live" in body and "revoke-live" in body
        assert "DRY-RUN" not in body
        assert len(body.splitlines()) <= 15
    finally:
        _close(rig)


class _TimeReplies:
    """Fake IB's fire-and-forget current-time path; no actual socket or sleep."""

    def __init__(self, rig):
        self.rig = rig
        self.requests = []
        self.replies = 0
        self.reply_times = []
        rig.broker.gateway_heartbeat_request = self.request
        rig.broker.gateway_heartbeat_reply_count = lambda: self.replies
        rig.broker.gateway_heartbeat_replies = lambda: tuple(self.reply_times)

    def request(self):
        self.requests.append(self.rig.clock.now())

    def answer(self, server_time=None):
        self.reply_times.append(server_time or self.rig.clock.now())
        self.replies += 1


def _heartbeat_rig(tmp_path, *, start=None):
    rig = Rig(tmp_path, start=start or et(9), dry_run=True)
    replies = _TimeReplies(rig)
    rig.start()
    return rig, replies


def _gateway_pages(rig):
    return [subject for subject, _ in rig.channel.messages
            if "P1" in subject and "IBKR 心跳无应答" in subject]


def test_G2_normal_response_has_no_page_and_requests_every_minute(tmp_path):
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        assert len(fake.requests) == 1
        fake.answer()
        rig.advance(seconds=1)
        rig.tick()
        rig.advance(seconds=59)
        rig.tick()
        assert len(fake.requests) == 2
        fake.answer()
        rig.advance(seconds=1)
        rig.tick()
        assert _gateway_pages(rig) == []
    finally:
        _close(rig)


def test_G2_one_timeout_does_not_page(tmp_path):
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        assert len(fake.requests) == 1
        rig.advance(seconds=31)
        rig.tick()
        assert _gateway_pages(rig) == []
        rig.advance(seconds=29)
        rig.tick()
        assert len(fake.requests) == 2 and _gateway_pages(rig) == []
    finally:
        _close(rig)


def test_G2_two_timeouts_page_and_fresh_reply_resolves(tmp_path):
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        assert len(fake.requests) == 1
        for seconds in (31, 29, 31):
            rig.advance(seconds=seconds)
            rig.tick()
        assert len(_gateway_pages(rig)) == 1
        assert len(fake.requests) == 2
        # Both expired requests are debt: neither old callback proves recovery.
        fake.answer(server_time=fake.requests[0])
        rig.advance(seconds=1)
        rig.tick()
        assert "P1|IB Gateway not responding" in rig.alerter._recent
        rig.advance(seconds=28)
        rig.tick()
        fake.answer(server_time=fake.requests[1])
        rig.advance(seconds=1)
        rig.tick()
        assert "P1|IB Gateway not responding" in rig.alerter._recent
        # Keep sending new timely replies; a conservative recovery may need
        # more than one after the two old callbacks have been quarantined.
        for _ in range(5):
            target = max(rig.clock.now() + timedelta(seconds=1),
                         fake.requests[-1] + timedelta(seconds=60))
            rig.at(target)
            rig.tick()
            fake.answer()
            rig.advance(seconds=1)
            rig.tick()
            if "P1|IB Gateway not responding" not in rig.alerter._recent:
                break
        assert "P1|IB Gateway not responding" not in rig.alerter._recent
        assert any("IBKR 已恢复" in subject for subject, _ in rig.channel.messages)
        assert len(_gateway_pages(rig)) == 1
    finally:
        _close(rig)


def test_G2_heartbeat_respects_calendar_and_early_close(tmp_path):
    weekend = Rig(tmp_path / "weekend", start=et(9, day=(2026, 10, 10)), dry_run=True)
    weekend_fake = _TimeReplies(weekend)
    try:
        weekend.start()
        assert weekend_fake.requests == []
    finally:
        _close(weekend)

    early = Rig(tmp_path / "early", start=et(8, 59, day=(2026, 11, 27)), dry_run=True)
    early_fake = _TimeReplies(early)
    try:
        early.start()
        assert early_fake.requests == []
        early.at(et(9, day=(2026, 11, 27)))  # 06:00 PT
        early.tick()
        assert len(early_fake.requests) == 1
        early.at(et(13, 31, day=(2026, 11, 27)))  # early close + 31 min
        early.tick()
        assert len(early_fake.requests) == 1
    finally:
        _close(early)


def test_G2_pending_current_time_never_blocks_tick(tmp_path):
    import time

    rig = Rig(tmp_path, start=et(9), dry_run=True)
    fake = _TimeReplies(rig)  # no response, ever
    try:
        start = time.monotonic()
        rig.start()
        elapsed = time.monotonic() - start
        assert fake.requests and elapsed < 3.0, "tick waited for reqCurrentTime response"
    finally:
        _close(rig)


def test_G2_two_late_replies_cannot_impersonate_a_new_probe(tmp_path):
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        rig.advance(seconds=31)
        rig.tick()                         # R1 timeout
        rig.advance(seconds=29)
        rig.tick()                         # R2 sent at t=60
        rig.advance(seconds=31)
        rig.tick()                         # R2 timeout; page
        assert len(_gateway_pages(rig)) == 1
        fake.answer(server_time=fake.requests[0])  # R1's late reply at t=92
        rig.advance(seconds=1)
        rig.tick()
        rig.advance(seconds=28)
        rig.tick()                         # t=120, a new probe would be unsafe
        fake.answer(server_time=fake.requests[1])  # R2's late reply at t=121
        rig.advance(seconds=1)
        rig.tick()
        assert "P1|IB Gateway not responding" in rig.alerter._recent
    finally:
        _close(rig)


def test_G2_third_old_reply_cannot_resolve_after_two_late_replies(tmp_path):
    """QA: R1@0, R2@60, R1 reply@92, R3@120, R2 reply@121,
    R4@180, R3 reply@181. All three replies are late; none proves recovery.
    """
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        for seconds in (31, 29, 31):
            rig.advance(seconds=seconds)
            rig.tick()
        assert len(fake.requests) == 2 and len(_gateway_pages(rig)) == 1
        fake.answer(server_time=fake.requests[0])  # R1 at t=92
        rig.advance(seconds=1)
        rig.tick()
        rig.advance(seconds=28)
        rig.tick()                          # R3 at t=120
        assert len(fake.requests) == 3
        fake.answer(server_time=fake.requests[1])  # R2 at t=121
        rig.advance(seconds=1)
        rig.tick()
        assert "P1|IB Gateway not responding" in rig.alerter._recent
        rig.advance(seconds=59)
        rig.tick()                          # R4 at t=180
        assert len(fake.requests) == 4
        fake.answer(server_time=fake.requests[2])  # R3 at t=181, still late
        rig.advance(seconds=1)
        rig.tick()
        assert "P1|IB Gateway not responding" in rig.alerter._recent
        assert not any("IBKR 已恢复" in s for s, _ in rig.channel.messages)
    finally:
        _close(rig)


def test_G2_old_reply_with_server_clock_fast_fifty_seconds_is_not_recovery(tmp_path):
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        for seconds in (31, 29, 31):
            rig.advance(seconds=seconds)
            rig.tick()
        assert len(_gateway_pages(rig)) == 1
        fake.answer(server_time=fake.requests[0])  # R1 at t=92
        rig.advance(seconds=1)
        rig.tick()
        rig.advance(seconds=28)
        rig.tick()                                  # R3 at t=120
        fake.answer(server_time=fake.requests[1])  # R2 at t=121
        rig.advance(seconds=1)
        rig.tick()
        rig.advance(seconds=59)
        rig.tick()                                  # R4 at t=180
        # R3 was generated at t=120 on a server clock 50s ahead: stamped 170.
        fake.answer(server_time=fake.requests[2] + timedelta(seconds=50))
        rig.advance(seconds=1)
        rig.tick()                                  # old callback at t=181
        assert "P1|IB Gateway not responding" in rig.alerter._recent
    finally:
        _close(rig)


def test_G2_old_reply_after_a_recovered_miss_does_not_poison_new_fresh_replies(tmp_path):
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        rig.advance(seconds=31)
        rig.tick()                            # R1 missed once, no P1
        rig.advance(seconds=29)
        rig.tick()                            # R2 at t=60
        fake.answer(server_time=fake.requests[1])
        rig.advance(seconds=1)
        rig.tick()                            # R2 timely: the streak clears
        assert _gateway_pages(rig) == []
        rig.advance(seconds=59)
        rig.tick()                            # R3 at t=120
        fake.answer(server_time=fake.requests[0])  # R1 finally arrives at t=121
        rig.advance(seconds=1)
        rig.tick()
        fake.answer(server_time=rig.clock.now())    # R3 really responds at t=122
        rig.advance(seconds=1)
        rig.tick()
        assert not rig.engine.gateway_heartbeat_stats().get("clock_skew_suspected")
        rig.at(et(9, 3))
        rig.tick()                            # R4 at t=180
        fake.answer(server_time=rig.clock.now())
        rig.advance(seconds=1)
        rig.tick()
        rig.at(et(9, 3, 31))
        rig.tick()                            # no fabricated second miss/P1
        assert _gateway_pages(rig) == []
    finally:
        _close(rig)


def test_G2_previous_day_unanswered_reply_cannot_resolve_new_day_page(tmp_path):
    old_day = (2026, 10, 6)
    new_day = (2026, 10, 7)
    rig, fake = _heartbeat_rig(tmp_path, start=et(16, 29, day=old_day))
    try:
        assert len(fake.requests) == 1  # old-day request still in flight
        rig.at(et(9, day=new_day))
        rig.tick()                          # day-2 R1
        for when in (et(9, 0, 31, day=new_day), et(9, 1, day=new_day),
                     et(9, 1, 31, day=new_day)):
            rig.at(when)
            rig.tick()
        assert len(_gateway_pages(rig)) >= 1
        fake.answer(server_time=fake.requests[1])  # day-2 R1 late
        rig.at(et(9, 1, 32, day=new_day))
        rig.tick()
        fake.answer(server_time=fake.requests[2])  # day-2 R2 late, at 09:01:59
        rig.at(et(9, 1, 59, day=new_day))
        rig.tick()
        rig.at(et(9, 2, day=new_day))
        rig.tick()                          # day-2 R3
        fake.answer(server_time=fake.requests[0])  # old-day reply arrives after R3
        rig.at(et(9, 2, 1, day=new_day))
        rig.tick()
        assert "P1|IB Gateway not responding" in rig.alerter._recent
        assert not any("IBKR 已恢复" in s for s, _ in rig.channel.messages)
    finally:
        _close(rig)


def test_G2_disconnect_and_reconnect_reset_consecutive_misses(tmp_path):
    rig, fake = _heartbeat_rig(tmp_path)
    try:
        rig.advance(seconds=31)
        rig.tick()
        assert rig.engine.gateway_heartbeat_stats()["misses"] == 1
        rig.broker.refuse_connect = True
        rig.broker.disconnect()
        rig.advance(seconds=1)
        rig.tick()
        assert not rig.engine.flags.broker_connected
        rig.broker.refuse_connect = False
        rig.advance(seconds=30)
        rig.tick()
        assert rig.engine.flags.broker_connected
        assert len(fake.requests) == 2
        rig.advance(seconds=31)
        rig.tick()
        assert _gateway_pages(rig) == [], "miss before a new socket counted toward the new streak"
    finally:
        _close(rig)


def test_G2_direct_tick_returns_with_unanswered_probe(tmp_path):
    import time

    rig, fake = _heartbeat_rig(tmp_path)
    try:
        rig.advance(seconds=60)
        started = time.monotonic()
        rig.tick()
        elapsed = time.monotonic() - started
        assert len(fake.requests) == 2
        assert elapsed < 3.0, "engine.tick waited for a current-time reply"
    finally:
        _close(rig)


def test_G2_ibkr_adapter_uses_raw_read_only_current_time_and_separate_channels(tmp_path, monkeypatch):
    from v6lite.broker.ibkr import IbkrBroker
    from v6lite.tests.helpers import make_cfg

    broker = IbkrBroker(make_cfg(tmp_path), read_only=True)
    raw_calls = []
    monkeypatch.setattr(broker.ib, "isConnected", lambda: True)
    monkeypatch.setattr(broker.ib, "reqCurrentTime", lambda: pytest.fail("blocking ib.reqCurrentTime called"))
    monkeypatch.setattr(broker.ib.client, "reqCurrentTime", lambda: raw_calls.append("reqCurrentTime"))
    before_sent, before_ended = broker._req_sent.copy(), broker._req_ended.copy()
    broker.gateway_heartbeat_request()
    assert raw_calls == ["reqCurrentTime"]
    assert broker.gateway_heartbeat_reply_count() == 0
    broker.ib.wrapper.currentTime(1_700_000_000)  # decoder callback without a network connection
    assert broker.gateway_heartbeat_reply_count() == 1
    assert broker.gateway_heartbeat_replies() == (
        datetime.fromtimestamp(1_700_000_000, tz=timezone.utc),)
    assert broker._req_sent == before_sent and broker._req_ended == before_ended
    assert not broker._channel_poisoned


def test_G3_daily_remote_report_is_one_screen_but_local_report_is_complete(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    rig = Rig(tmp_path, dry_run=True, start=et(9, 50))
    replies = _TimeReplies(rig)

    def answer_immediately():
        replies.request()
        replies.answer()

    rig.broker.gateway_heartbeat_request = answer_immediately
    try:
        rig.start()
        rig.at(et(10, 0, 30))
        rig.tick()
        assert any("WOULD_BUY AAPL" in rec.getMessage() for rec in caplog.records)
        assert not rig.broker.sent and not rig.broker.cancels
        rig.at(et(16, 6))
        rig.tick()
        _, body = _message(rig, "每日健康报告")
        full = (tmp_path / "reports" / "2026-10-06.md").read_text(encoding="utf-8")
        assert full.startswith("# V6-Lite daily report") and "## Events" in full
        assert body != full and len(body.splitlines()) <= 18
        lowered = body.lower()
        for fragment in ("正常", "扫描", "would_buy", "gate", "下单", "撤单", "持仓",
                         "漂移", "ibkr", "告警", "行情", "code_sha"):
            assert fragment in lowered, fragment
        assert "0" in body  # dry-run order and cancel counts
        # Totals exclude time before this process and have an ET-day boundary.
        assert lowered.count("本进程今日截至报告时") == 2
    finally:
        _close(rig)


def _rth_quote_line(rig):
    _, body = _message(rig, "每日健康报告")
    return next(line for line in body.splitlines() if line.startswith("行情可用率"))


def _sample_count(line):
    match = re.search(r"\b(\d+)\s+次采样", line)
    assert match, f"RTH quote sample count missing: {line}"
    return int(match.group(1))


def test_G3_quote_availability_uses_rth_samples_not_close_plus_five_snapshot(tmp_path):
    rig = Rig(tmp_path, dry_run=True, start=et(9, 50))
    try:
        rig.start()
        for when in (et(10, 5), et(10, 6)):
            rig.at(when)                    # fresh subscribed quotes during RTH
            rig.tick()
        rig.clock.set(et(16, 6))            # let the same quotes age naturally
        assert rig.engine._market_data_status()["usable_quotes"] == 0
        rig.engine._end_of_day(rig.clock.now())
        line = _rth_quote_line(rig)
        assert "100%" in line and "0/3" not in line
        assert _sample_count(line) >= 2
    finally:
        _close(rig)


def test_G3_rth_availability_denominator_is_subscribed_not_scan_universe(tmp_path):
    rig = Rig(tmp_path, dry_run=True, start=et(10, 5), market_data_type=3,
              scan_symbols=("AAPL", "SPY", "TSLA"), tradable_symbols=("AAPL",))
    rig.broker.market_data_status = lambda: {
        "requested": "DELAYED", "delayed_symbols": [], "no_subscription": []}
    rig.broker.quotes.pop("SPY")
    rig.broker.quotes.pop("TSLA")
    try:
        rig.start()
        rig.at(et(10, 6))
        rig.tick()
        assert rig.engine._market_data_status()["quote_symbols"] == 1
        rig.clock.set(et(16, 6))
        assert rig.engine._market_data_status()["usable_quotes"] == 0
        rig.engine._end_of_day(rig.clock.now())
        line = _rth_quote_line(rig)
        assert "100%" in line and ("1/1" in line or "已订阅 1" in line)
        assert _sample_count(line) >= 2
    finally:
        _close(rig)


def test_G3_failed_rth_market_data_snapshot_counts_as_unavailable(tmp_path):
    rig = Rig(tmp_path, dry_run=True, start=et(9))
    healthy_status = rig.broker.market_data_status
    try:
        rig.start()                           # before RTH, no quote sample
        rig.at(et(10, 5))
        rig.tick()                            # one healthy 3/3 RTH sample

        def unavailable_status():
            raise RuntimeError("synthetic market-data status outage")

        rig.broker.market_data_status = unavailable_status
        rig.at(et(10, 6))
        rig.tick()                            # one failed snapshot: count 0/3
        rig.broker.market_data_status = healthy_status
        rig.clock.set(et(16, 6))             # do not refresh stale closing quotes
        rig.engine._end_of_day(rig.clock.now())
        _, body = _message(rig, "每日健康报告")
        line = _rth_quote_line(rig)
        assert body.splitlines()[0].startswith("⚠️ 需关注")
        assert "50%" in line and "3/6 标的样本" in line
        assert _sample_count(line) == 2
    finally:
        _close(rig)


@pytest.mark.parametrize("level", ["P0", "P1"])
def test_G4_page_has_three_actionable_lines_while_log_retains_details(level, caplog):
    from v6lite.tests.helpers import et

    caplog.set_level(logging.ERROR)
    channel = MemoryChannel()
    alerter = Alerter([channel], synchronous=True)
    marker = "diagnostic-correlation-42"
    try:
        alerter.send(et(10), level, "no delayed market data",
                     f"IBKR refused AAPL; delayed quote unavailable; check subscription; {marker}")
        assert len(channel.messages) == 1
        _, body = channel.messages[0]
        lines = body.splitlines()
        assert len(lines) == 4
        assert lines[0].startswith(("🔴 P0", "🟠 P1"))
        assert lines[1].startswith("发生了什么：")
        assert lines[2].startswith("影响：")
        assert lines[3].startswith("你要做什么：")
        assert marker not in body
        assert marker in caplog.text
    finally:
        alerter.close()


def test_G5_delayed_health_counts_only_subscribed_symbols(tmp_path):
    rig = Rig(tmp_path, start=et(10, 5), dry_run=True, market_data_type=3,
              scan_symbols=("AAPL", "SPY", "TSLA"), tradable_symbols=("AAPL",))
    rig.broker.market_data_status = lambda: {
        "requested": "DELAYED", "delayed_symbols": [], "no_subscription": []}
    rig.broker.quotes.pop("SPY")
    rig.broker.quotes.pop("TSLA")
    try:
        rig.start()
        status = rig.engine._market_data_status()
        assert status["quote_symbols"] == status["usable_quotes"] == 1
        rig.advance(seconds=121)
        rig.tick()
        assert not any("延迟行情不可用" in subject for subject, _ in rig.channel.messages)
        assert not rig.broker.sent and not rig.broker.cancels
    finally:
        _close(rig)


@pytest.mark.parametrize("subject,detail,what_words,impact_words,action_words", [
    ("daily loss stop latched", "loss 160.00 >= 150.00. No new entries today; exits continue.",
     ("亏损",), ("暂停新入场", "退出继续"), ("下一交易日",)),
    ("positions without fresh prices", "AAPL: stops cannot be evaluated. Check market data.",
     ("持仓", "价格"), ("退出保护", "延迟"), ("行情", "报价")),
])
def test_G4_page_impact_and_action_match_the_actual_condition(
        subject, detail, what_words, impact_words, action_words, caplog):
    channel = MemoryChannel()
    alerter = Alerter([channel], synchronous=True)
    try:
        alerter.send(et(10), "P1", subject, detail)
        _, body = channel.messages[0]
        _, what, impact, action = body.splitlines()
        assert all(word in what for word in what_words), body
        assert all(word in impact for word in impact_words), body
        assert all(word in action for word in action_words), body
        assert subject in caplog.text
    finally:
        alerter.close()


def test_G4_late_exit_fill_warns_of_oversell_and_tws_working_sell():
    channel = MemoryChannel()
    alerter = Alerter([channel], synchronous=True)
    try:
        alerter.send(et(10), "P1", "late exit fill AAPL",
                     "An exit fill arrived after the position was marked closed; another SELL may be working.")
        _, body = channel.messages[0]
        title, what, impact, action = body.splitlines()
        assert "退出成交迟到" in title and "AAPL" in what
        assert "超卖" in impact or "空头" in impact
        assert "TWS" in action and "券商持仓" in action and "在途 SELL" in action
    finally:
        alerter.close()

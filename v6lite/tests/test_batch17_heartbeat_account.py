"""Batch 17 monitoring regressions use scripted read-only heartbeat callbacks."""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import pytest

from v6lite.config import ProtectionConfig
from v6lite.tests.helpers import Rig, et


class _TimeReplies:
    """Script server times and their precise local callback receive times."""

    def __init__(self, rig):
        self.rig = rig
        self.requests = []
        self.times = []
        self.samples = []
        rig.broker.gateway_heartbeat_request = self.request
        rig.broker.gateway_heartbeat_reply_count = lambda: len(self.times)
        rig.broker.gateway_heartbeat_replies = lambda: tuple(self.times)
        rig.broker.gateway_heartbeat_samples = lambda: tuple(self.samples)

    def request(self):
        self.requests.append(self.rig.clock.now())

    def answer(self, *, skew_seconds=0):
        received = self.rig.clock.now()
        server = received + timedelta(seconds=skew_seconds)
        self.times.append(server)
        self.samples.append((server, received))
        return server, received


def _heartbeat_rig(tmp_path):
    rig = Rig(tmp_path, start=et(9), protection=ProtectionConfig(enabled=True))
    replies = _TimeReplies(rig)
    rig.start()
    assert len(replies.requests) == 1
    assert not rig.broker.sent and not rig.broker.sent_protections
    return rig, replies


def _answer_probe(rig, replies, *, skew_seconds=0, next_probe=False):
    if next_probe:
        rig.at(replies.requests[-1] + timedelta(seconds=60))
        rig.engine._gateway_heartbeat(rig.clock.now())
    result = replies.answer(skew_seconds=skew_seconds)
    rig.engine._gateway_heartbeat(rig.clock.now())
    return result


def _skew_pages(rig):
    return [title for title, _ in rig.channel.messages
            if "P1" in title and "IBKR 时钟偏差" in title and "已恢复" not in title]


def _skew_recoveries(rig):
    return [title for title, _ in rig.channel.messages if "IBKR 时钟偏差已恢复" in title]


def _no_reply_pages(rig):
    return [title for title, _ in rig.channel.messages if "P1" in title and "心跳无应答" in title]


def _reply_logs(caplog):
    return [record.getMessage() for record in caplog.records
            if "[GATEWAY_TIME]" in record.getMessage()]


@pytest.mark.parametrize("skew_seconds", [-120, 120])
def test_every_reply_logs_signed_actual_server_host_seconds(tmp_path, caplog, skew_seconds):
    caplog.set_level(logging.INFO)
    rig, replies = _heartbeat_rig(tmp_path)
    server, received = replies.answer(skew_seconds=skew_seconds)
    # The outer engine handles an already-received callback later. Report the
    # actual callback's host clock, not the outer-loop processing clock.
    rig.advance(seconds=20)
    rig.engine._gateway_heartbeat(rig.clock.now())
    logs = _reply_logs(caplog)
    assert len(logs) == 1, logs
    assert f"server_time={server.isoformat()}" in logs[0]
    assert f"host_time={received.isoformat()}" in logs[0]
    match = re.search(r"signed_skew_seconds=([+-]?\d+(?:\.\d+)?)", logs[0])
    assert match and float(match.group(1)) == pytest.approx(skew_seconds), logs
    assert rig.engine.gateway_heartbeat_stats()["clock_skew_seconds"] == pytest.approx(skew_seconds)
    assert not _skew_pages(rig)


def test_reply_without_outstanding_request_still_logs_actual_clock_difference(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    rig, replies = _heartbeat_rig(tmp_path)
    rig.engine._gateway_last_request = None
    server, received = replies.answer(skew_seconds=-17)
    rig.engine._gateway_heartbeat(rig.clock.now())
    logs = _reply_logs(caplog)
    assert len(logs) == 1
    assert f"server_time={server.isoformat()}" in logs[0]
    assert f"host_time={received.isoformat()}" in logs[0]
    match = re.search(r"signed_skew_seconds=([+-]?\d+(?:\.\d+)?)", logs[0])
    assert match and float(match.group(1)) == pytest.approx(-17)


def test_one_old_time_after_reconnect_is_log_only_then_normal_reply_is_healthy(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    rig, replies = _heartbeat_rig(tmp_path)
    rig.engine.flags.broker_connected = False
    rig.engine._gateway_heartbeat(rig.clock.now())
    rig.engine.flags.broker_connected = True
    rig.engine._gateway_heartbeat(rig.clock.now())
    _answer_probe(rig, replies, skew_seconds=-3600)
    assert len(_reply_logs(caplog)) == 1
    assert not _skew_pages(rig) and not _no_reply_pages(rig)
    _answer_probe(rig, replies, next_probe=True)
    stats = rig.engine.gateway_heartbeat_stats()
    assert not stats["clock_skew_suspected"]
    assert stats["misses"] == 0 and not stats["not_responding"]
    assert not _skew_pages(rig) and not _no_reply_pages(rig)
    assert not _skew_recoveries(rig), "a log-only sample has no alert episode to recover"
    assert len(_reply_logs(caplog)) == 2


@pytest.mark.parametrize("skew_seconds", [-120, 120])
def test_three_consecutive_bad_samples_page_once_for_long_lived_same_episode(tmp_path, skew_seconds):
    rig, replies = _heartbeat_rig(tmp_path)
    _answer_probe(rig, replies, skew_seconds=skew_seconds)
    assert not _skew_pages(rig)
    _answer_probe(rig, replies, skew_seconds=skew_seconds, next_probe=True)
    assert not _skew_pages(rig)
    _answer_probe(rig, replies, skew_seconds=skew_seconds, next_probe=True)
    assert len(_skew_pages(rig)) == 1
    assert rig.engine.gateway_heartbeat_stats()["clock_skew_suspected"]
    # Cross both short duplicate and long repeat windows. An unchanged clock
    # problem is one episode, not a new notification every half-hour.
    for _ in range(40):
        _answer_probe(rig, replies, skew_seconds=skew_seconds, next_probe=True)
    assert len(_skew_pages(rig)) == 1
    assert not _no_reply_pages(rig)
    assert rig.engine.gateway_heartbeat_stats()["misses"] == 0


def test_three_consecutive_normal_samples_resolve_clock_episode_and_notify_once(tmp_path):
    rig, replies = _heartbeat_rig(tmp_path)
    for i in range(3):
        _answer_probe(rig, replies, skew_seconds=120, next_probe=i > 0)
    assert len(_skew_pages(rig)) == 1
    for _ in range(2):
        _answer_probe(rig, replies, next_probe=True)
        assert rig.engine.gateway_heartbeat_stats()["clock_skew_suspected"]
        assert not _skew_recoveries(rig)
    _answer_probe(rig, replies, next_probe=True)
    assert not rig.engine.gateway_heartbeat_stats()["clock_skew_suspected"]
    assert len(_skew_recoveries(rig)) == 1
    for _ in range(4):
        _answer_probe(rig, replies, next_probe=True)
    assert len(_skew_recoveries(rig)) == 1
    # A cleared episode can report a genuinely new consecutive clock failure.
    for _ in range(3):
        _answer_probe(rig, replies, skew_seconds=-120, next_probe=True)
    assert len(_skew_pages(rig)) == 2
    assert not _no_reply_pages(rig)


def test_normal_sample_breaks_bad_streak_and_bad_sample_breaks_recovery_streak(tmp_path):
    rig, replies = _heartbeat_rig(tmp_path)
    for delta in (120, 120, 0, 120, 120):
        _answer_probe(rig, replies, skew_seconds=delta, next_probe=bool(replies.times))
    assert not _skew_pages(rig)
    _answer_probe(rig, replies, skew_seconds=120, next_probe=True)
    assert len(_skew_pages(rig)) == 1
    for delta in (0, 0, 120, 0, 0):
        _answer_probe(rig, replies, skew_seconds=delta, next_probe=True)
        assert not _skew_recoveries(rig)
    _answer_probe(rig, replies, next_probe=True)
    assert len(_skew_recoveries(rig)) == 1


def test_clock_skew_replies_do_not_create_127_permanent_no_reply_misses(tmp_path):
    rig, replies = _heartbeat_rig(tmp_path)
    for i in range(130):
        _answer_probe(rig, replies, skew_seconds=-120, next_probe=i > 0)
    stats = rig.engine.gateway_heartbeat_stats()
    assert stats["replies"] == 130
    assert stats["misses"] == 0 and not stats["not_responding"]
    assert not _no_reply_pages(rig)
    assert len(_skew_pages(rig)) == 1
    for _ in range(3):
        _answer_probe(rig, replies, next_probe=True)
    assert not rig.engine.gateway_heartbeat_stats()["clock_skew_suspected"]
    assert len(_skew_recoveries(rig)) == 1


def test_genuinely_missing_callbacks_keep_original_no_response_alert(tmp_path):
    rig, replies = _heartbeat_rig(tmp_path)
    rig.advance(seconds=31)
    rig.engine._gateway_heartbeat(rig.clock.now())
    assert rig.engine.gateway_heartbeat_stats()["misses"] == 1
    assert not _no_reply_pages(rig)
    rig.advance(seconds=29)
    rig.engine._gateway_heartbeat(rig.clock.now())
    assert len(replies.requests) == 2
    rig.advance(seconds=31)
    rig.engine._gateway_heartbeat(rig.clock.now())
    stats = rig.engine.gateway_heartbeat_stats()
    assert stats["misses"] == 2 and stats["not_responding"]
    assert len(_no_reply_pages(rig)) == 1 and not _skew_pages(rig)
    # A stale callback for the old first request cannot clear the real outage.
    replies.times.append(replies.requests[0])
    replies.samples.append((replies.requests[0], rig.clock.now()))
    rig.engine._gateway_heartbeat(rig.clock.now())
    assert rig.engine.gateway_heartbeat_stats()["not_responding"]
    assert not any("IBKR 已恢复" in title for title, _ in rig.channel.messages)


def test_confirmed_flat_silent_account_stream_is_debug_without_faking_freshness(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start()
    assert rig.engine.flags.reconcile_ok and rig.broker.positions_fresh() == []
    assert not rig.store.open_positions() and not rig.store.active_intents()
    rig.at(et(10))
    rig.engine._reconcile(rig.clock.now())  # current successful all-account flat proof
    old = rig.clock.now() - timedelta(seconds=211)
    rig.broker.funds_as_of = rig.broker.funds_seen_at = old
    rig.engine._refresh_equity(rig.clock.now(), force=True)
    rig.engine._refresh_equity(rig.clock.now(), force=True)
    records = [record for record in caplog.records if "[ACCOUNT_STREAM]" in record.getMessage()]
    assert records and all(record.levelno == logging.DEBUG for record in records), records
    # Batch 19 changes confirmed-flat refresh to a funds-only request.
    assert getattr(rig.broker, "account_refreshes", 0) == 0
    assert rig.broker.funds_refreshes == 1
    assert rig.broker.available_funds().seen_at == old
    assert not rig.broker.sent and not rig.broker.sent_protections


@pytest.mark.parametrize("holding", ["managed", "broker_only"])
def test_held_account_silent_stream_uses_debug_funds_snapshot(tmp_path, caplog, holding):
    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    if holding == "managed":
        rig.open_aapl()
        assert rig.store.open_positions() and rig.store.active_intents()
    else:
        # A protected legacy holding is present in all-account broker truth,
        # even though Lite deliberately never manages this symbol.
        rig.broker.external_positions["MSTR"] = (5, 100.0)
        rig.start()
        rig.at(et(10))
        assert not rig.store.open_positions() and not rig.store.active_intents()
        assert rig.broker.positions_fresh()
    old = rig.clock.now() - timedelta(seconds=211)
    rig.broker.funds_as_of = rig.broker.funds_seen_at = old
    before = getattr(rig.broker, "account_refreshes", 0)
    funds_before = getattr(rig.broker, "funds_refreshes", 0)
    rig.engine._refresh_equity(rig.clock.now(), force=True)
    rig.engine._refresh_equity(rig.clock.now(), force=True)
    records = [record for record in caplog.records if "[ACCOUNT_STREAM]" in record.getMessage()]
    assert records and records[-1].levelno == logging.DEBUG
    assert getattr(rig.broker, "account_refreshes", 0) == before
    assert rig.broker.funds_refreshes == funds_before + 1
    assert rig.broker.available_funds().seen_at == old


def test_unverified_flat_account_stream_keeps_warning(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start()
    rig.at(et(10))
    rig.engine.flags.reconcile_ok = False
    old = rig.clock.now() - timedelta(seconds=211)
    rig.broker.funds_as_of = rig.broker.funds_seen_at = old
    rig.engine._refresh_equity(rig.clock.now(), force=True)
    records = [record for record in caplog.records if "[ACCOUNT_STREAM]" in record.getMessage()]
    assert records and records[-1].levelno == logging.WARNING
    assert rig.broker.available_funds().seen_at == old

@pytest.mark.parametrize("boundary", ["stale_snapshot", "clock_backwards", "reconcile_not_ok",
                                      "new_fill_after_snapshot", "same_timestamp_fill", "active_intent", "query_failed"])
def test_account_silence_logging_is_independent_of_cached_flat_position_evidence(
        tmp_path, caplog, monkeypatch, boundary):
    from v6lite.broker.base import BrokerError
    from v6lite.models import OrderRequest, Purpose, Side

    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start()
    rig.at(et(10))
    rig.engine._reconcile(rig.clock.now())
    assert rig.engine.flags.reconcile_ok
    flat_at = rig.engine._account_stream_flat_at
    assert flat_at == rig.clock.now()
    assert not rig.store.open_positions() and not rig.store.active_intents()
    if boundary == "stale_snapshot":
        rig.advance(seconds=rig.cfg.reconcile_interval_seconds + 1)
    elif boundary == "clock_backwards":
        rig.clock.set(flat_at - timedelta(seconds=1))
    elif boundary == "reconcile_not_ok":
        rig.engine.flags.reconcile_ok = False
    elif boundary in {"new_fill_after_snapshot", "same_timestamp_fill"}:
        # Open then fully close a one-share holding entirely via callbacks,
        # without another position snapshot. No active intent or open ledger
        # position remains, but an old flat snapshot predates both executions.
        req = OrderRequest("v6l-post-flat-buy", "AAPL", Side.BUY, 1, 113.47,
                           Purpose.ENTRY, "post-flat-position")
        assert rig.engine._entry_evidence() is None
        assert rig.engine._send(rig.clock.now(), req, atr=2.0,
                                count_entry_day="2026-10-06") == "sent"
        if boundary == "new_fill_after_snapshot":
            rig.advance(seconds=1)
        rig.broker.fill(req.intent_id)
        rig.engine._drain(rig.clock.now())
        protection = rig.store.protection(req.position_id)
        assert protection is not None
        if boundary == "new_fill_after_snapshot":
            rig.advance(seconds=1)
        rig.broker.fill(protection["intent_id"], price=113.50)
        rig.engine._drain(rig.clock.now())
        assert not rig.store.open_positions() and not rig.store.active_intents()
        if boundary == "same_timestamp_fill":
            assert rig.engine._last_fill_at == flat_at
        else:
            assert rig.engine._last_fill_at > flat_at
    elif boundary == "active_intent":
        req = OrderRequest("v6l-pending-after-flat", "AAPL", Side.BUY, 1, 113.47,
                           Purpose.ENTRY, "pending-position")
        rig.store.create_intent(rig.clock.now(), req, atr=2.0,
                                count_entry_day="2026-10-06")
        assert rig.store.active_intents() and not rig.store.open_positions()
    else:
        def unavailable_positions():
            raise BrokerError("synthetic failed all-account server snapshot")
        monkeypatch.setattr(rig.broker, "positions_fresh", unavailable_positions)
        with pytest.raises(BrokerError, match="synthetic failed"):
            rig.engine._fresh_positions()
        assert rig.engine._account_stream_flat_at is None
        assert rig.engine.flags.reconcile_ok  # query failure invalidates cache itself
    caplog.clear()
    old = rig.clock.now() - timedelta(seconds=211)
    rig.broker.funds_as_of = rig.broker.funds_seen_at = old
    rig.engine._refresh_equity(rig.clock.now(), force=True)
    records = [record for record in caplog.records if "[ACCOUNT_STREAM]" in record.getMessage()]
    # B28: unchanged funds are not a position-stream heartbeat. A stale
    # flat cache still cannot authorize trading; only telemetry changes.
    expected = logging.WARNING if boundary == "reconcile_not_ok" else logging.DEBUG
    assert len(records) == 1 and records[0].levelno == expected, (boundary, records)
    assert rig.broker.available_funds().seen_at == old


def test_real_ibkr_wrapper_samples_exact_local_callback_time_without_connection(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import v6lite.broker.ibkr as ibkr
    from v6lite.tests.helpers import make_cfg

    received = [et(10) + timedelta(seconds=7, microseconds=125000)]
    class CallbackDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return received[0]
    monkeypatch.setattr(ibkr, "datetime", CallbackDateTime)
    broker = ibkr.IbkrBroker(make_cfg(tmp_path), read_only=True)
    assert not broker.ib.isConnected()
    assert broker._ensure_channel_hooks()
    before = (broker._req_sent.copy(), broker._req_ended.copy())
    server = datetime.fromtimestamp(int(et(10).timestamp()), timezone.utc)
    first_received = received[0]
    broker.ib.wrapper.currentTime(int(server.timestamp()))
    assert broker.gateway_heartbeat_samples() == ((server, first_received),)
    received[0] += timedelta(seconds=40)
    assert broker.gateway_heartbeat_samples() == ((server, first_received),)
    second_server = server + timedelta(seconds=60)
    second_received = received[0]
    broker.ib.wrapper.currentTime(int(second_server.timestamp()))
    assert broker.gateway_heartbeat_samples() == (
        (server, first_received), (second_server, second_received))
    assert broker.gateway_heartbeat_reply_count() == 2
    assert broker.gateway_heartbeat_replies() == (server, second_server)
    assert (broker._req_sent, broker._req_ended) == before
    assert not broker.ib.isConnected() and not broker._channel_poisoned


def test_clock_confirmation_N_defaults_three_and_is_adjustable_without_config_change(tmp_path, monkeypatch):
    import v6lite.engine as engine_module

    assert engine_module.GATEWAY_CLOCK_SKEW_CONFIRMATIONS == 3
    monkeypatch.setattr(engine_module, "GATEWAY_CLOCK_SKEW_CONFIRMATIONS", 2)
    rig, replies = _heartbeat_rig(tmp_path)
    fingerprint = rig.cfg.fingerprint()
    _answer_probe(rig, replies, skew_seconds=120)
    assert not _skew_pages(rig)
    _answer_probe(rig, replies, skew_seconds=120, next_probe=True)
    assert len(_skew_pages(rig)) == 1
    _answer_probe(rig, replies, next_probe=True)
    assert rig.engine.gateway_heartbeat_stats()["clock_skew_suspected"]
    assert not _skew_recoveries(rig)
    _answer_probe(rig, replies, next_probe=True)
    assert not rig.engine.gateway_heartbeat_stats()["clock_skew_suspected"]
    assert len(_skew_recoveries(rig)) == 1
    assert rig.cfg.fingerprint() == fingerprint

def test_excluded_external_stock_fill_invalidates_cached_flat_telemetry_without_booking_or_orders(
        tmp_path, caplog):
    from v6lite.models import Side

    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    rig.start()
    rig.at(et(10))
    rig.engine._reconcile(rig.clock.now())
    assert rig.engine.flags.reconcile_ok and rig.broker.positions_fresh() == []
    flat_at = rig.engine._account_stream_flat_at
    assert flat_at == rig.clock.now()
    fills_before = rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    writes_before = (len(rig.broker.sent), len(rig.broker.sent_protections),
                     len(rig.broker.modified_protections), len(rig.broker.cancels))
    # An excluded legacy stock actually fills at the broker in the same clock
    # tick. Lite records only exclusion audit evidence, not a managed trade.
    fill = rig.broker.external_fill("MSTR", Side.BUY, 5, 100.0)
    assert fill.intent_id == "" and fill.time == flat_at
    rig.engine._drain(rig.clock.now())
    assert rig.engine.flags.reconcile_ok
    assert [(p.symbol, p.quantity) for p in rig.broker.positions_fresh()] == [("MSTR", 5)]
    assert not rig.store.open_positions() and not rig.store.active_intents()
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == fills_before
    assert rig.store.conn.execute(
        "SELECT COUNT(*) FROM audit WHERE kind='EXCLUDED_SYMBOL_FILL'").fetchone()[0] == 1
    assert (len(rig.broker.sent), len(rig.broker.sent_protections),
            len(rig.broker.modified_protections), len(rig.broker.cancels)) == writes_before
    caplog.clear()
    old = rig.clock.now() - timedelta(seconds=211)
    rig.broker.funds_as_of = rig.broker.funds_seen_at = old
    rig.engine._refresh_equity(rig.clock.now(), force=True)
    records = [record for record in caplog.records if "[ACCOUNT_STREAM]" in record.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.DEBUG, records
    assert rig.broker.available_funds().seen_at == old
    assert (len(rig.broker.sent), len(rig.broker.sent_protections),
            len(rig.broker.modified_protections), len(rig.broker.cancels)) == writes_before
"""Batch 19 funds evidence: no socket or operator state is accessed.

Engine tests use isolated FakeBroker/SQLite state. Adapter tests exercise the
installed ib_insync raw wrapper with all outgoing messages recorded locally.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.tests.helpers import Rig, et, make_cfg


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    from ib_insync import IB
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("batch19 funds tests cannot connect to a broker or network")

    monkeypatch.setattr(IB, "connect", forbidden)
    monkeypatch.setattr(IB, "connectAsync", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def raw_adapter(tmp_path, monkeypatch):
    import v6lite.broker.ibkr as module

    broker = IbkrBroker(make_cfg(tmp_path))
    ib = broker.ib
    clock = SimpleNamespace(now=datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc), mono=100.0)

    class ReceivedAt(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz is not None else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", ReceivedAt)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.mono))
    outgoing = []
    ids = itertools.count(700)
    monkeypatch.setattr(ib.client, "send", lambda *fields, **kwargs: outgoing.append(fields))
    monkeypatch.setattr(ib.client, "getReqId", lambda: next(ids))
    monkeypatch.setattr(ib, "isConnected", lambda: True)
    ib.accountValueEvent += broker._on_account_value
    ib.errorEvent += broker._on_error

    def blocked(*args, **kwargs):
        raise AssertionError("funds refresh cannot use blocking requests or cached account summaries")

    monkeypatch.setattr(ib, "reqAccountUpdates", blocked)
    monkeypatch.setattr(ib, "reqAccountSummary", blocked)
    monkeypatch.setattr(ib, "accountSummary", blocked)

    def advance(seconds):
        clock.now += timedelta(seconds=seconds)
        clock.mono += seconds

    def sleep(seconds=0):
        advance(seconds)

    monkeypatch.setattr(ib, "sleep", sleep)
    yield SimpleNamespace(broker=broker, ib=ib, clock=clock, outgoing=outgoing, advance=advance)
    broker._on_disconnect()  # local cleanup only; never open a socket


def _requests(adapter):
    return [fields for fields in adapter.outgoing if fields[0] == 62]


def _cancels(adapter):
    return [fields for fields in adapter.outgoing if fields[0] == 63]


def _stream(adapter, tag, value, currency="USD", account="DU0000000"):
    adapter.ib.wrapper.updateAccountValue(tag, str(value), currency, account)


def test_B19_confirmed_flat_refreshes_funds_without_resetting_account_stream(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    try:
        rig.start()
        rig.at(et(10))
        rig.engine._reconcile(rig.clock.now())
        assert rig.engine.flags.reconcile_ok
        assert not rig.store.open_positions() and not rig.store.active_intents()
        old = rig.clock.now() - timedelta(seconds=211)
        rig.broker.funds_as_of = rig.broker.funds_seen_at = old
        before = getattr(rig.broker, "account_refreshes", 0)
        rig.engine._refresh_equity(rig.clock.now(), force=True)
        rig.engine._refresh_equity(rig.clock.now(), force=True)
        assert getattr(rig.broker, "account_refreshes", 0) == before, \
            "confirmed flat must not unsubscribe/resubscribe the classic account stream"
        assert getattr(rig.broker, "funds_refreshes", 0) == 1
        assert rig.broker.available_funds().seen_at == old, "a request is not fresh funds evidence"
        assert not rig.broker.sent and not rig.broker.sent_protections
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B19_funds_request_is_nonblocking_unique_and_never_resets_account_stream(raw_adapter):
    adapter = raw_adapter
    _stream(adapter, "AvailableFunds", 5000)
    _stream(adapter, "NetLiquidation", 100000)
    before = adapter.broker.available_funds()
    adapter.advance(211)
    adapter.broker.refresh_funds()
    adapter.broker.refresh_funds()
    requests = _requests(adapter)
    assert len(requests) == 1, "one pending funds download must not allocate another subscription"
    assert requests[0][3] == "All"
    assert set(requests[0][4].split(",")) == {"AvailableFunds", "NetLiquidation"}
    assert not any(fields[0] == 6 for fields in adapter.outgoing)
    assert adapter.broker.available_funds() == before, "sending a request cannot refresh funds timestamps"

def _download(adapter, req_id, *, funds=6000, netliq=120000, currency="USD", account="DU0000000"):
    adapter.ib.wrapper.accountSummary(req_id, account, "AvailableFunds", str(funds), currency)
    adapter.ib.wrapper.accountSummary(req_id, account, "NetLiquidation", str(netliq), currency)


def _start_download(adapter):
    _stream(adapter, "AvailableFunds", 5000)
    _stream(adapter, "NetLiquidation", 100000)
    old = adapter.broker.available_funds()
    adapter.advance(211)
    adapter.broker.refresh_funds()
    requests = _requests(adapter)
    assert len(requests) == 1
    return requests[0][2], old


@pytest.mark.parametrize("currency", ["USD", "BASE"])
def test_B19_complete_summary_accepts_actual_field_receive_times_not_end_time(raw_adapter, currency):
    adapter = raw_adapter
    req_id, old = _start_download(adapter)
    adapter.advance(2)
    funds_at = adapter.clock.now
    adapter.ib.wrapper.accountSummary(req_id, "DU0000000", "AvailableFunds", "6000", currency)
    assert adapter.broker.available_funds() == old, "partial download cannot publish new funds"
    adapter.advance(3)
    adapter.ib.wrapper.accountSummary(req_id, "DU0000000", "NetLiquidation", "120000", currency)
    assert adapter.broker.available_funds() == old, "download must be proved complete by matching End"
    adapter.advance(4)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    after = adapter.broker.available_funds()
    assert after.value == 6000
    assert after.seen_at == after.as_of == funds_at
    assert after.seen_at != adapter.clock.now
    assert adapter.broker.net_liquidation() == 120000
    assert [fields[2] for fields in _cancels(adapter)] == [req_id]
    assert not any(fields[0] == 6 for fields in adapter.outgoing)


def test_B19_same_value_summary_updates_seen_at_but_preserves_change_time(raw_adapter):
    adapter = raw_adapter
    req_id, old = _start_download(adapter)
    adapter.advance(2)
    funds_at = adapter.clock.now
    adapter.ib.wrapper.accountSummary(req_id, "DU0000000", "AvailableFunds", "5000", "USD")
    adapter.advance(1)
    adapter.ib.wrapper.accountSummary(req_id, "DU0000000", "NetLiquidation", "100000", "USD")
    adapter.advance(1)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    after = adapter.broker.available_funds()
    assert after.value == old.value and after.as_of == old.as_of
    assert after.seen_at == funds_at and after.seen_at > old.seen_at


@pytest.mark.parametrize("fault", ["wrong_req", "wrong_account", "wrong_currency", "missing_funds",
                                   "missing_netliq", "nan_funds", "infinite_funds", "nan_netliq"])
def test_B19_unproved_summary_never_refreshes_funds(raw_adapter, fault):
    adapter = raw_adapter
    req_id, old = _start_download(adapter)
    adapter.advance(2)
    callback_id = req_id + 900 if fault == "wrong_req" else req_id
    account = "DU0000000" if fault == "wrong_account" else "DU0000000"
    currency = "EUR" if fault == "wrong_currency" else "USD"
    if fault != "missing_funds":
        funds = "nan" if fault == "nan_funds" else "inf" if fault == "infinite_funds" else "6000"
        adapter.ib.wrapper.accountSummary(callback_id, account, "AvailableFunds", funds, currency)
    if fault != "missing_netliq":
        netliq = "nan" if fault == "nan_netliq" else "120000"
        adapter.ib.wrapper.accountSummary(callback_id, account, "NetLiquidation", netliq, currency)
    adapter.advance(1)
    adapter.ib.wrapper.accountSummaryEnd(callback_id)
    assert adapter.broker.available_funds() == old
    assert adapter.broker.net_liquidation() == 100000
    if fault == "wrong_req":
        assert _cancels(adapter) == [], "foreign End must not cancel our live request"
        adapter.broker.refresh_funds()
        assert len(_requests(adapter)) == 1
    else:
        assert [fields[2] for fields in _cancels(adapter)] == [req_id]


@pytest.mark.parametrize("trigger", ["funds_read", "pump", "field", "end"])
def test_B19_timeout_cancels_single_subscription_and_old_reply_cannot_revive_it(raw_adapter, trigger):
    adapter = raw_adapter
    req_id, old = _start_download(adapter)
    adapter.advance(16)
    if trigger == "funds_read":
        assert adapter.broker.available_funds() == old
    elif trigger == "pump":
        adapter.broker.pump(0)
    elif trigger == "field":
        _download(adapter, req_id)
    else:
        adapter.ib.wrapper.accountSummaryEnd(req_id)
    assert [fields[2] for fields in _cancels(adapter)] == [req_id]
    assert adapter.broker.available_funds() == old
    adapter.broker.refresh_funds()
    next_id = _requests(adapter)[-1][2]
    assert next_id != req_id and len(_requests(adapter)) == 2
    _download(adapter, req_id, funds=999999)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    assert adapter.broker.available_funds() == old
    _download(adapter, next_id, funds=7000)
    adapter.ib.wrapper.accountSummaryEnd(next_id)
    assert adapter.broker.available_funds().value == 7000
    assert [fields[2] for fields in _cancels(adapter)] == [req_id, next_id]


@pytest.mark.parametrize("reset", ["disconnect", "data_lost"])
def test_B19_old_summary_cannot_revive_funds_after_connection_generation_reset(raw_adapter, reset):
    adapter = raw_adapter
    req_id, _ = _start_download(adapter)
    adapter.advance(1)
    adapter.ib.wrapper.accountSummary(req_id, "DU0000000", "AvailableFunds", "6000", "USD")
    if reset == "disconnect":
        adapter.broker._on_disconnect()
    else:
        adapter.broker.refresh_subscriptions()
        classic = [fields for fields in adapter.outgoing if fields[0] == 6]
        assert [fields[2] for fields in classic] == [False, True], "real data loss still resubscribes"
    assert adapter.broker.available_funds().value is None
    assert adapter.broker.net_liquidation() is None
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    assert adapter.broker.available_funds().value is None
    assert adapter.broker.net_liquidation() is None
    adapter.broker.refresh_funds()
    next_id = _requests(adapter)[-1][2]
    assert next_id != req_id
    _download(adapter, next_id, funds=7000)
    adapter.ib.wrapper.accountSummaryEnd(next_id)
    assert adapter.broker.available_funds().value == 7000


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_B19_older_summary_cannot_overwrite_a_later_stream_message_even_at_equal_time(raw_adapter, same_timestamp):
    adapter = raw_adapter
    req_id, _ = _start_download(adapter)
    adapter.advance(1)
    _download(adapter, req_id, funds=6000, netliq=120000)
    if not same_timestamp:
        adapter.advance(1)
    stream_at = adapter.clock.now
    _stream(adapter, "AvailableFunds", 7000)
    _stream(adapter, "NetLiquidation", 130000)
    adapter.advance(1)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    after = adapter.broker.available_funds()
    assert after.value == 7000 and after.seen_at == after.as_of == stream_at
    assert adapter.broker.net_liquidation() == 130000


def test_B19_complete_summary_does_not_unlock_accountready_false(raw_adapter):
    adapter = raw_adapter
    _stream(adapter, "AccountReady", "false", "")
    req_id, _ = _start_download(adapter)
    adapter.advance(1)
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    after = adapter.broker.available_funds()
    assert after.value == 6000 and not after.ready


def test_B19_cancel_failure_disconnects_instead_of_leaking_subscription_or_trusting_funds(raw_adapter, monkeypatch):
    adapter = raw_adapter
    req_id, _ = _start_download(adapter)
    disconnected = []

    def failed_cancel(_req_id):
        raise OSError("synthetic subscription cancellation failure")

    def local_disconnect():
        disconnected.append(True)
        adapter.broker._on_disconnect()

    monkeypatch.setattr(adapter.ib.client, "cancelAccountSummary", failed_cancel)
    monkeypatch.setattr(adapter.ib, "disconnect", local_disconnect)
    adapter.advance(1)
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    assert disconnected == [True]
    assert adapter.broker.available_funds().value is None
    assert adapter.broker.net_liquidation() is None
    from v6lite.broker.base import BrokerError
    try:
        adapter.broker.refresh_funds()
    except BrokerError:
        pass  # explicit refusal is allowed; no extra physical subscription is allowed
    assert len(_requests(adapter)) == 1, "failed cancellation must quarantine later funds requests"
    assert adapter.broker.available_funds().value is None
    assert adapter.broker.net_liquidation() is None


def test_B19_stale_flat_funds_remain_blocked_until_an_actual_fake_reply(tmp_path, caplog):
    from v6lite.models import Signal
    import logging

    caplog.set_level(logging.INFO)
    rig = Rig(tmp_path, dry_run=True, protection=ProtectionConfig(enabled=True))
    try:
        rig.start()
        rig.at(et(10, 5))
        rig.engine._reconcile(rig.clock.now())
        old = rig.clock.now() - timedelta(seconds=271)
        rig.broker.funds_as_of = rig.broker.funds_seen_at = old
        rig.engine._refresh_equity(rig.clock.now(), force=True)
        assert getattr(rig.broker, "account_refreshes", 0) == 0
        assert getattr(rig.broker, "funds_refreshes", 0) == 1
        refused = rig.engine._try_enter(rig.clock.now(), Signal("AAPL", 85, 113.13, 2.0))
        assert "funds_stale" in refused
        assert not rig.broker.sent and not rig.broker.sent_protections
        assert rig.broker.available_funds().seen_at == old
        rig.broker.funds_seen_at = rig.clock.now()  # simulate a real matching AvailableFunds callback
        rig.engine._try_enter(rig.clock.now(), Signal("AAPL", 85, 113.13, 2.0))
        rows = [record.getMessage() for record in caplog.records if "WOULD_BUY AAPL " in record.getMessage()]
        assert "gate=refused:funds_stale" in rows[-2]
        assert "gate=ok" in rows[-1]
        assert not rig.broker.sent and not rig.broker.cancels
        assert rig.store.conn.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
    finally:
        rig.alerter.close()
        rig.store.close()


@pytest.mark.parametrize("proof", ["held", "broker_only", "unknown", "expired", "clock_backwards", "active", "new_fill"])
def test_B19_unproved_flat_uses_snapshot_without_faking_funds_freshness(tmp_path, proof):
    from v6lite.models import OrderRequest, Purpose, Side

    rig = Rig(tmp_path, protection=ProtectionConfig(enabled=True))
    try:
        rig.start()
        rig.at(et(10))
        rig.engine._reconcile(rig.clock.now())
        if proof == "held":
            rig.open_aapl()
        elif proof == "broker_only":
            rig.broker.external_positions["MSTR"] = (5.0, 100.0)
            rig.engine._reconcile(rig.clock.now())
        elif proof == "unknown":
            rig.engine.flags.reconcile_ok = False
        elif proof == "expired":
            rig.advance(seconds=rig.cfg.reconcile_interval_seconds + 1)
        elif proof == "clock_backwards":
            rig.clock.advance(seconds=-1)
        elif proof == "active":
            req = OrderRequest("v6l-b19-pending", "AAPL", Side.BUY, 1, 113.47, Purpose.ENTRY, "b19-pending")
            rig.store.create_intent(rig.clock.now(), req, atr=2.0, count_entry_day="2026-10-06")
        else:
            # A real fill invalidates flat proof before any special-purpose early return.
            from v6lite.models import FillEvent
            fill = FillEvent(exec_id="b19-evidence", intent_id="v6l-b19-unknown",
                             symbol="AAPL", side=Side.BUY, quantity=1, price=113.13,
                             time=rig.clock.now(), broker_order_id=999)
            rig.engine._ingest_fill(rig.clock.now(), fill)
        old = rig.clock.now() - timedelta(seconds=271)
        rig.broker.funds_as_of = rig.broker.funds_seen_at = old
        before = getattr(rig.broker, "account_refreshes", 0)
        funds_before = getattr(rig.broker, "funds_refreshes", 0)
        rig.engine._refresh_equity(rig.clock.now(), force=True)
        # B28 probes unchanged fields independently of the position stream.
        assert getattr(rig.broker, "account_refreshes", 0) == before
        assert getattr(rig.broker, "funds_refreshes", 0) == funds_before + 1
        assert rig.broker.available_funds().seen_at == old
    finally:
        rig.alerter.close()
        rig.store.close()

@pytest.mark.parametrize("matching", [True, False])
def test_B19_request_error_cancels_only_the_affected_download(raw_adapter, matching):
    adapter = raw_adapter
    req_id, old = _start_download(adapter)
    adapter.ib.wrapper.error(req_id if matching else req_id + 900,
                             321, "synthetic funds download error", "")
    assert adapter.broker.available_funds() == old
    if matching:
        assert [fields[2] for fields in _cancels(adapter)] == [req_id]
        adapter.broker.refresh_funds()
        assert len(_requests(adapter)) == 2
    else:
        assert _cancels(adapter) == []
        adapter.broker.refresh_funds()
        assert len(_requests(adapter)) == 1


def test_B19_monotonic_timeout_survives_host_clock_going_backwards(raw_adapter):
    adapter = raw_adapter
    req_id, old = _start_download(adapter)
    adapter.clock.now -= timedelta(hours=1)
    adapter.clock.mono += 16
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    assert adapter.broker.available_funds() == old
    assert [fields[2] for fields in _cancels(adapter)] == [req_id]


def test_B19_final_entry_gate_keeps_270_second_ready_and_two_dollar_fee_checks(tmp_path):
    from v6lite.models import OrderRequest, Purpose, Side

    rig = Rig(tmp_path, dry_run=True, protection=ProtectionConfig(enabled=True))
    try:
        rig.start()
        rig.at(et(10, 5))
        req = OrderRequest("v6l-b19-fee", "AAPL", Side.BUY, 5, 100.0, Purpose.ENTRY, "b19-fee")
        rig.broker.available = 501.99
        rig.broker.funds_as_of = rig.broker.funds_seen_at = rig.clock.now()
        assert rig.engine._entry_evidence() is None
        assert "insufficient_funds" in rig.engine._entry_gate(rig.clock.now(), req)
        rig.broker.available = 502.0
        assert rig.engine._entry_evidence() is None
        assert rig.engine._entry_gate(rig.clock.now(), req) is None
        rig.broker.funds_ready = False
        assert rig.engine._entry_evidence() is None
        assert rig.engine._entry_gate(rig.clock.now(), req) == "refused:funds_not_ready"
        rig.broker.funds_ready = True
        rig.broker.funds_as_of = rig.broker.funds_seen_at = rig.clock.now() - timedelta(seconds=271)
        assert rig.engine._entry_evidence() is None
        assert "funds_stale" in rig.engine._entry_gate(rig.clock.now(), req)
        assert not rig.broker.sent and not rig.broker.sent_protections
    finally:
        rig.alerter.close()
        rig.store.close()

"""Independent review edges: TEMP and intercepted raw IB only."""
from datetime import date

import pytest

from v6lite.broker.base import BrokerError
from v6lite.report import daily_health_card
from v6lite.tests.test_batch19_funds import (  # imported local fixtures
    no_network, raw_adapter, _start_download, _stream, _download, _requests, _cancels,
)


def test_execution_marker_survives_long_daily_warning_title():
    lines = ["ATTENTION: RTH quotes " + "3/6 " * 100] + ["unused"] * 8 + ["code_sha synthetic"]
    card = daily_health_card("\n".join(lines), date(2026, 10, 6), mode="live", dry_run=True)
    visible = card.as_embed()["title"]
    assert "演练（不下单）" in visible
    assert "真实下单（Live）" not in visible
    assert visible.startswith("⚠️ 需关注")


@pytest.mark.parametrize("lost,restored", [(1100, 1101), (2110, 1102)])
def test_backend_disconnect_cancels_summary_while_api_socket_stays_connected(raw_adapter, lost, restored):
    adapter = raw_adapter
    req_id, _ = _start_download(adapter)
    adapter.broker._on_error(-1, lost, "synthetic backend disconnect", None)
    assert [fields[2] for fields in _cancels(adapter)] == [req_id]
    assert adapter.broker.available_funds().value is None
    adapter.broker._on_error(-1, restored, "synthetic backend reconnect", None)
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    assert adapter.broker.available_funds().value is None
    assert [fields[2] for fields in _cancels(adapter)] == [req_id]
    adapter.broker.refresh_funds()
    new_id = _requests(adapter)[-1][2]
    assert new_id != req_id
    _download(adapter, new_id, funds=7000)
    adapter.ib.wrapper.accountSummaryEnd(new_id)
    assert adapter.broker.available_funds().value == 7000


def test_failed_cancel_and_failed_disconnect_cannot_be_unlocked_by_old_stream(raw_adapter, monkeypatch):
    adapter = raw_adapter
    req_id, _ = _start_download(adapter)
    def fail(*args, **kwargs):
        raise OSError("synthetic local failure")
    monkeypatch.setattr(adapter.ib.client, "cancelAccountSummary", fail)
    monkeypatch.setattr(adapter.ib, "disconnect", fail)
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    adapter.advance(1)
    _stream(adapter, "AvailableFunds", 8000)
    _stream(adapter, "NetLiquidation", 150000)
    _stream(adapter, "AccountReady", "true", "")
    after = adapter.broker.available_funds()
    assert after.value is None and after.seen_at is None and after.as_of is None
    assert not after.ready and adapter.broker.net_liquidation() is None
    with pytest.raises(BrokerError):
        adapter.broker.refresh_funds()
    assert len(_requests(adapter)) == 1
    # A reconnect attempt must not claim a new connection while the old
    # socket cannot be closed. All methods here are intercepted local stubs.
    connect_calls = []
    monkeypatch.setattr(adapter.ib, "connect", lambda *a, **k: connect_calls.append(True))
    with pytest.raises((BrokerError, OSError)):
        adapter.broker.connect()
    assert connect_calls == [] and adapter.broker.available_funds().value is None


def test_new_successful_socket_retains_actual_handshake_funds_receive_time(raw_adapter, monkeypatch):
    adapter = raw_adapter
    req_id, _ = _start_download(adapter)
    connected = [True]
    def failed_cancel(*args, **kwargs):
        raise OSError("synthetic cancel failure")
    def disconnect():
        connected[0] = False
        adapter.broker._on_disconnect()
    monkeypatch.setattr(adapter.ib, "isConnected", lambda: connected[0])
    monkeypatch.setattr(adapter.ib.client, "cancelAccountSummary", failed_cancel)
    monkeypatch.setattr(adapter.ib, "disconnect", disconnect)
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    assert adapter.broker.available_funds().value is None
    received = []
    def connect(*args, **kwargs):
        connected[0] = True
        received.append(adapter.clock.now)
        _stream(adapter, "AvailableFunds", 7000)
        _stream(adapter, "NetLiquidation", 140000)
        _stream(adapter, "AccountReady", "true", "")
        adapter.advance(2)
    monkeypatch.setattr(adapter.ib, "connect", connect)
    monkeypatch.setattr(adapter.ib, "reqMarketDataType", lambda kind: None)
    adapter.broker.connect()
    after = adapter.broker.available_funds()
    assert after.value == 7000 and after.seen_at == after.as_of == received[0]
    assert after.seen_at != adapter.clock.now
    assert after.ready and adapter.broker.net_liquidation() == 140000


@pytest.mark.parametrize("funds", [0, -100])
def test_verified_nonpositive_funds_replace_old_spendable_balance(raw_adapter, funds):
    adapter = raw_adapter
    req_id, old = _start_download(adapter)
    assert old.value == 5000
    adapter.advance(1)
    received = adapter.clock.now
    _download(adapter, req_id, funds=funds)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    after = adapter.broker.available_funds()
    assert after.value == funds
    assert after.as_of == after.seen_at == received
    assert after.value <= 0  # existing amount + fee gate cannot permit a BUY
    assert [fields[2] for fields in _cancels(adapter)] == [req_id]


@pytest.mark.parametrize("code", [1100, 1101, 1102])
@pytest.mark.parametrize("reply_after_reset", [False, True])
def test_handshake_reset_discards_prior_generation_fields(raw_adapter, monkeypatch, code, reply_after_reset):
    adapter = raw_adapter
    req_id, _ = _start_download(adapter)
    connected = [True]
    def failed_cancel(*args, **kwargs):
        raise OSError("synthetic cancel failure")
    def disconnect():
        connected[0] = False
        adapter.broker._on_disconnect()
    monkeypatch.setattr(adapter.ib, "isConnected", lambda: connected[0])
    monkeypatch.setattr(adapter.ib.client, "cancelAccountSummary", failed_cancel)
    monkeypatch.setattr(adapter.ib, "disconnect", disconnect)
    _download(adapter, req_id)
    adapter.ib.wrapper.accountSummaryEnd(req_id)
    received = []
    def connect(*args, **kwargs):
        connected[0] = True
        _stream(adapter, "AvailableFunds", 7000)
        _stream(adapter, "NetLiquidation", 140000)
        _stream(adapter, "AccountReady", "true", "")
        adapter.advance(1)
        adapter.broker._on_error(-1, code, "synthetic handshake generation reset", None)
        if reply_after_reset:
            adapter.advance(1)
            received.append(adapter.clock.now)
            _stream(adapter, "AvailableFunds", 9000)
            _stream(adapter, "NetLiquidation", 160000)
            _stream(adapter, "AccountReady", "true", "")
        adapter.advance(1)
    monkeypatch.setattr(adapter.ib, "connect", connect)
    monkeypatch.setattr(adapter.ib, "reqMarketDataType", lambda kind: None)
    adapter.broker.connect()
    after = adapter.broker.available_funds()
    if reply_after_reset:
        assert after.value == 9000 and after.seen_at == after.as_of == received[0]
        assert adapter.broker.net_liquidation() == 160000
    else:
        assert after.value is None and after.seen_at is None and after.as_of is None
        assert adapter.broker.net_liquidation() is None

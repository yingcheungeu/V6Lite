"""B28 lifecycle contracts: real SDK disconnect callbacks, synthetic holdings only.

The frozen core replay uses public interfaces present since B25. The broker
portfolio is deterministic; its first pump runs the installed ib_insync client,
wrapper, globalErrorEvent and util.run, with no transport or network connection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import replace

import pytest

from v6lite.__main__ import run_engine
from v6lite.broker.base import BrokerError
from v6lite.broker.ibkr import IbkrBroker
from v6lite.config import ProtectionConfig
from v6lite.models import Purpose
from v6lite.tests.helpers import Rig, buy_bars, et

DAY = (2026, 10, 20)
NOTICES = (2105, 2106, 2103, 2104, 2157, 2158, 2105, 2106, 2103, 2104)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    def forbidden(*args, **kwargs):
        raise AssertionError('B28 lifecycle replay must never connect to a network or TWS')
    async def forbidden_async(*args, **kwargs):
        forbidden(*args, **kwargs)
    from ib_insync import IB
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden_async)


def _audit(rig, kind):
    return [json.loads(row['payload']) for row in rig.store.conn.execute(
        'SELECT payload FROM audit WHERE kind=? ORDER BY rowid', (kind,))]


def _writes(rig):
    return tuple(len(x) for x in (rig.broker.sent, rig.broker.sent_protections,
                                 rig.broker.modified_protections, rig.broker.cancels))


def _holding(tmp_path, monkeypatch):
    rig = Rig(tmp_path, start=et(9, 50, day=DAY),
              protection=ProtectionConfig(enabled=True), risk_per_trade_pct=.0015)
    rig.broker._next_id = 704
    levels = rig.store._levels_fn
    rig.store._levels_fn = lambda price, atr: replace(
        levels(price, atr), stop=338.62, tp1=343.93, tp2=344.60, tp3=345.60)
    rig.start()
    rig.at(et(10, 0, 30, day=DAY))
    rig.px('AAPL', 342.60)
    rig.broker.bars['AAPL'] = buy_bars(base=329.47)
    rig.tick()
    [entry] = [req for req in rig.broker.sent if req.purpose is Purpose.ENTRY]
    assert entry.quantity == 3
    rig.broker.fill(entry.intent_id, qty=3, price=342.60)
    rig.advance(seconds=1)
    rig.tick()
    [position] = rig.store.open_positions()
    pid = position['position_id']
    protection = rig.store.protection(pid)
    assert protection['state'] == 'WORKING'
    assert protection['broker_order_id'] == 706
    assert protection['trigger_price'] == 338.62
    rig.at(et(14, 16, 51, day=DAY))
    rig.px('AAPL', 342.60)
    # run_engine owns startup in production. This fixture has already executed
    # the real startup and entry path so it resumes the exact running engine.
    monkeypatch.setattr(rig.engine, 'startup', lambda: None)
    rig.channel.messages.clear()
    return rig, pid


def _wire_disconnect(rig, monkeypatch, request, message, *, raw_oserror=False):
    """Installed SDK callback executes while actual IB.sleep/util.run waits."""
    adapter = IbkrBroker(rig.cfg)
    ib = adapter.ib
    client = ib.client
    client.connState = client.CONNECTED
    client._apiReady = True
    client._serverVersion = 176
    client._accounts = [rig.cfg.account]
    ib.disconnectedEvent += adapter._on_disconnect
    def lost():
        rig.broker.connected = False
    ib.disconnectedEvent += lost
    request.addfinalizer(client.reset)
    drain = rig.broker.drain_events
    monkeypatch.setattr(rig.broker, 'drain_events', lambda: drain() + adapter.drain_events())
    errors = []
    client.apiError += errors.append
    pumps = []
    def pump(seconds):
        pumps.append(rig.clock.now())
        if len(pumps) == 1:
            for code in NOTICES:
                adapter._on_error(-1, code, 'synthetic market-data farm notification', None)
                rig.advance(seconds=1)
            assert rig.clock.now() == et(14, 17, 1, day=DAY)
            if raw_oserror:
                rig.broker.connected = False
                raise OSError(64, 'The specified network name is no longer available')
            if not message:
                native_error = None
            elif message.startswith('[WinError 64]'):
                native_error = OSError(64, 'The specified network name is no longer available', None, 64)
            else:
                assert message.startswith('[Errno 10054]'), 'unsupported native socket replay variant'
                native_error = ConnectionResetError(
                    10054, 'An existing connection was forcibly closed by the remote host')
            # The native Protocol converts the OSError to text and emits its
            # existing disconnected event; Client installs that wiring itself.
            asyncio.get_event_loop_policy().get_event_loop().call_soon(
                client.conn.connection_lost, native_error)
            IbkrBroker.pump(adapter, seconds)
        else:
            rig.advance(seconds=seconds)
    monkeypatch.setattr(rig.broker, 'pump', pump)
    return adapter, pumps, errors


@pytest.mark.parametrize('message', [
    '[WinError 64] The specified network name is no longer available',
    '',
    str(ConnectionResetError(10054, 'An existing connection was forcibly closed by the remote host')),
], ids=['WinError64', 'Peer-closed-connection', 'ConnectionResetError'])
def test_B28_real_socket_disconnect_reenters_loop_then_fresh_takeover(
        tmp_path, monkeypatch, request, message):
    rig, pid = _holding(tmp_path, monkeypatch)
    before_position = dict(rig.store.position(pid))
    before_writes = _writes(rig)
    adapter, pumps, errors = _wire_disconnect(rig, monkeypatch, request, message)
    reconnects, fresh, snapshots, shutdowns = [], [], [], []
    connect = rig.broker.connect
    def reconnect():
        reconnects.append(rig.clock.now())
        connect()
    monkeypatch.setattr(rig.broker, 'connect', reconnect)
    for name in ('open_orders', 'positions_fresh', 'executions_today'):
        original = getattr(rig.broker, name)
        def query(*args, _name=name, _original=original, **kwargs):
            fresh.append((_name, rig.clock.now()))
            return _original(*args, **kwargs)
        monkeypatch.setattr(rig.broker, name, query)
    def snapshot(stage, payload):
        snapshots.append((stage, rig.engine.flags.reconcile_ok,
                          rig.engine.flags.broker_connected, len(fresh)))
        assert not rig.engine.flags.reconcile_ok, 'handshake snapshot must never authorize takeover'
    rig.broker.connect_snapshot_hook = snapshot
    shutdown = rig.engine.shutdown
    def stop(*args, **kwargs):
        shutdowns.append((args, kwargs))
        return shutdown(grace_seconds=0, **kwargs)
    monkeypatch.setattr(rig.engine, 'shutdown', stop)
    checked = []
    def should_stop():
        assert len(pumps) < 45, 'offline replay must recover after 30 seconds, not spin forever'
        if not reconnects:
            if pumps:
                assert not _audit(rig, 'SHUTDOWN')
                assert not shutdowns
                assert not rig.engine.flags.reconcile_ok
                assert any('P1' in title and 'IBKR' in title and '断开' in title
                           for title, _ in rig.channel.messages)
                if len(pumps) < 30:
                    assert not rig.engine.flags.broker_connected
            return False
        assert reconnects == [et(14, 17, 31, day=DAY)]
        assert snapshots and snapshots[0][0] == 'openOrder'
        assert all(name in {item[0] for item in fresh} for name in
                   ('open_orders', 'positions_fresh', 'executions_today'))
        assert rig.engine.flags.broker_connected and rig.engine.flags.reconcile_ok
        assert dict(rig.store.position(pid)) == before_position
        assert rig.store.protection(pid)['broker_order_id'] == 706
        assert rig.store.protection(pid)['state'] == 'WORKING'
        assert _writes(rig) == before_writes
        assert not [x for x in rig.channel.messages if 'P0' in x[0]]
        assert not shutdowns and not _audit(rig, 'SHUTDOWN')
        checked.append(True)
        return True
    try:
        run_engine(rig.engine, rig.broker, should_stop=should_stop)
        assert checked and len(shutdowns) == 1
        assert errors and ('Peer closed connection' in errors[0] if not message else errors[0] == message)
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_native_pump_raw_network_OSError_is_recoverable(tmp_path, monkeypatch, request):
    rig, pid = _holding(tmp_path, monkeypatch)
    _wire_disconnect(rig, monkeypatch, request, '', raw_oserror=True)
    before = _writes(rig)
    reconnects = []
    connect = rig.broker.connect
    def reconnect():
        reconnects.append(rig.clock.now())
        connect()
    monkeypatch.setattr(rig.broker, 'connect', reconnect)
    checked = []
    def should_stop():
        if not reconnects:
            return False
        assert reconnects == [et(14, 17, 31, day=DAY)]
        assert rig.engine.flags.reconcile_ok
        assert rig.store.position(pid)['qty_open'] == 3
        assert _writes(rig) == before
        checked.append(True)
        return True
    try:
        run_engine(rig.engine, rig.broker, should_stop=should_stop)
        assert checked
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_repeat_disconnect_budget_keeps_process_heartbeat_alive(
        tmp_path, monkeypatch, request, caplog):
    rig, pid = _holding(tmp_path, monkeypatch)
    _wire_disconnect(rig, monkeypatch, request, '[WinError 64] synthetic reset')
    before = _writes(rig)
    attempts = []
    def refused():
        attempts.append(rig.clock.now())
        raise BrokerError('synthetic same reconnect failure')
    monkeypatch.setattr(rig.broker, 'connect', refused)
    caplog.set_level(logging.INFO)
    checked = []
    def should_stop():
        elapsed = (rig.clock.now() - et(14, 17, 1, day=DAY)).total_seconds()
        if elapsed < 1220:
            assert not _audit(rig, 'SHUTDOWN')
            return False
        assert attempts == [et(14, 17, 31, day=DAY), et(14, 18, 31, day=DAY)]
        assert rig.engine._reconnect_stopped
        assert len([x for x in rig.channel.messages if 'P0' in x[0] and '连接反复重建，已停止自动重连' in x[0]]) == 1
        assert any('自动重连仍已停止，等待人工处理' in title for title, _ in rig.channel.messages)
        assert rig.store.position(pid)['qty_open'] == 3
        assert _writes(rig) == before
        assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
        assert sum('[HEARTBEAT]' in row.getMessage() for row in caplog.records) >= 2
        checked.append(True)
        return True
    shutdown = rig.engine.shutdown
    monkeypatch.setattr(rig.engine, 'shutdown', lambda *args, **kwargs: shutdown(grace_seconds=0, **kwargs))
    try:
        run_engine(rig.engine, rig.broker, should_stop=should_stop)
        assert checked
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_fatal_error_pages_held_stop_and_records_exit_source(tmp_path, monkeypatch, caplog):
    rig, pid = _holding(tmp_path, monkeypatch)
    error = RuntimeError('synthetic invariant failure for ' + rig.cfg.account)
    monkeypatch.setattr(rig.broker, 'pump', lambda _: (_ for _ in ()).throw(error))
    caplog.set_level(logging.WARNING)
    try:
        with pytest.raises(RuntimeError, match='synthetic invariant failure'):
            run_engine(rig.engine, rig.broker, should_stop=lambda: False)
        [requested] = _audit(rig, 'SHUTDOWN_REQUESTED')
        assert requested['source'] == 'fatal_error'
        assert 'RuntimeError' in json.dumps(requested)
        assert 'synthetic invariant failure' in json.dumps(requested)
        assert rig.cfg.account not in json.dumps(requested)
        [shutdown] = _audit(rig, 'SHUTDOWN')
        assert shutdown['source'] == 'fatal_error'
        [page] = [(title, body) for title, body in rig.channel.messages
                  if 'P0' in title and '引擎已退出' in title]
        assert all(value in page[1] for value in ('AAPL', '3', '706', '338.62', 'g3t'))
        assert '券商端止损保护' in page[0] + page[1]
        assert not rig.broker.connected
        assert rig.broker.orders[rig.store.protection(pid)['intent_id']].status in {'Submitted', 'PreSubmitted'}
        assert caplog.records[-1].levelno >= logging.ERROR
        assert 'synthetic invariant failure' in caplog.records[-1].getMessage()
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_operator_stop_records_SIGINT_without_engine_exit_P0(tmp_path, monkeypatch, caplog):
    rig, pid = _holding(tmp_path, monkeypatch)
    caplog.set_level(logging.WARNING)
    try:
        run_engine(rig.engine, rig.broker, should_stop=lambda: True)
        [requested] = _audit(rig, 'SHUTDOWN_REQUESTED')
        assert requested['source'] == 'SIGINT'
        [shutdown] = _audit(rig, 'SHUTDOWN')
        assert shutdown['source'] == 'SIGINT'
        assert any(row.levelno == logging.WARNING and 'SHUTDOWN_REQUESTED' in row.getMessage()
                   and 'SIGINT' in row.getMessage() for row in caplog.records)
        assert not [x for x in rig.channel.messages if 'P0' in x[0] and '引擎已退出' in x[0]]
        assert not rig.broker.connected
        assert rig.store.position(pid)['qty_open'] == 3
        assert rig.broker.cancels == []
    finally:
        rig.alerter.close()
        rig.store.close()


@pytest.mark.parametrize('failure', [
    ConnectionResetError(10054, 'synthetic connection reset'),
    BrokenPipeError(32, 'synthetic broken socket'),
    None,
], ids=['native-ConnectionResetError-object', 'native-BrokenPipeError-object', 'quiet-socket-loss'])
def test_B28_direct_transport_failure_or_quiet_socket_loss_keeps_running(
        tmp_path, monkeypatch, failure):
    rig, pid = _holding(tmp_path, monkeypatch)
    started = rig.clock.now()
    before = _writes(rig)
    pumps, reconnects, checked = [], [], []
    def pump(seconds):
        pumps.append(rig.clock.now())
        if len(pumps) == 1:
            rig.broker.connected = False
            if failure is not None:
                raise failure
        else:
            rig.advance(seconds=seconds)
    monkeypatch.setattr(rig.broker, 'pump', pump)
    connect = rig.broker.connect
    def reconnect():
        reconnects.append(rig.clock.now())
        connect()
    monkeypatch.setattr(rig.broker, 'connect', reconnect)
    def should_stop():
        assert len(pumps) < 45
        if not reconnects:
            return False
        assert (reconnects[0] - started).total_seconds() == 30
        assert len(reconnects) == 1
        assert rig.engine.flags.reconcile_ok
        assert not _audit(rig, 'SHUTDOWN')
        assert rig.store.position(pid)['qty_open'] == 3
        assert _writes(rig) == before
        assert not [x for x in rig.channel.messages if 'P0' in x[0]]
        checked.append(True)
        return True
    try:
        run_engine(rig.engine, rig.broker, should_stop=should_stop)
        assert checked
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_KeyboardInterrupt_is_operator_SIGINT(tmp_path, monkeypatch):
    rig, pid = _holding(tmp_path, monkeypatch)
    def interrupted(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(rig.broker, 'pump', interrupted)
    try:
        assert run_engine(rig.engine, rig.broker, should_stop=lambda: False) == 0
        [requested] = _audit(rig, 'SHUTDOWN_REQUESTED')
        assert requested['source'] == 'SIGINT'
        assert _audit(rig, 'SHUTDOWN')[-1]['source'] == 'SIGINT'
        assert not [x for x in rig.channel.messages if 'P0' in x[0] and '引擎已退出' in x[0]]
        assert rig.store.position(pid)['qty_open'] == 3
        assert not rig.broker.cancels
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_disconnected_disk_error_is_fatal_not_transport_retry(tmp_path, monkeypatch):
    import errno
    rig, pid = _holding(tmp_path, monkeypatch)
    error = OSError(errno.ENOSPC, 'synthetic no space left on device')
    def disk_failure(_):
        rig.broker.connected = False
        raise error
    monkeypatch.setattr(rig.broker, 'pump', disk_failure)
    try:
        with pytest.raises(OSError, match='no space left'):
            run_engine(rig.engine, rig.broker, should_stop=lambda: False)
        assert not _audit(rig, 'RECONNECT_ATTEMPT')
        assert _audit(rig, 'SHUTDOWN_REQUESTED')[-1]['source'] == 'fatal_error'
        assert len([x for x in rig.channel.messages if 'P0' in x[0] and '引擎已退出' in x[0]]) == 1
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_cmd_run_fatal_returns_nonzero_and_last_console_record_ERROR(
        tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace
    import v6lite.__main__ as cli
    import v6lite.alerts as alerts
    import v6lite.broker.ibkr as ibkr
    import v6lite.engine as engine_mod
    rig, pid = _holding(tmp_path, monkeypatch)
    closed, released = [], []
    close_store = rig.store.close
    monkeypatch.setattr(rig.store, 'close', lambda: closed.append(True))
    monkeypatch.setattr(cli, 'preflight_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, 'setup_logging', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.approval_ops, 'old_v6_processes', lambda: ())
    monkeypatch.setattr(cli, 'check_live_gate', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, 'AccountLock', lambda *_: SimpleNamespace(
        acquire=lambda *_: None, release=lambda: released.append(True)))
    monkeypatch.setattr(cli, 'open_store', lambda *args, **kwargs: rig.store)
    monkeypatch.setattr(ibkr, 'IbkrBroker', lambda *args, **kwargs: rig.broker)
    monkeypatch.setattr(alerts, 'build_channels', lambda *_: [rig.channel])
    monkeypatch.setattr(alerts, 'Alerter', lambda *args, **kwargs: rig.alerter)
    monkeypatch.setattr(engine_mod, 'Engine', lambda *args, **kwargs: rig.engine)
    monkeypatch.setattr(cli.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(cli, '_now', rig.clock.now)
    def fatal(_):
        raise RuntimeError('synthetic unrecoverable internal failure')
    monkeypatch.setattr(rig.broker, 'pump', fatal)
    caplog.set_level(logging.WARNING)
    try:
        result = cli.cmd_run(SimpleNamespace(dry_run=False, live=False), rig.cfg)
        assert result != 0
        assert closed == [True] and released == [True]
        assert _audit(rig, 'SHUTDOWN_REQUESTED')[-1]['source'] == 'fatal_error'
        assert len([x for x in rig.channel.messages if 'P0' in x[0] and '引擎已退出' in x[0]]) == 1
        assert caplog.records[-1].levelno >= logging.ERROR
        assert 'synthetic unrecoverable internal failure' in caplog.records[-1].getMessage()
    finally:
        rig.alerter.close()
        close_store()

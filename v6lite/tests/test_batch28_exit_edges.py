"""B28 independent exit-boundary edges; temp config/state and no sockets.

These contracts exercise the CLI signal registration, cleanup ownership and
real engine loop. Production functions are retained except external-resource
construction, which is bound to the existing deterministic synthetic Rig.
"""
from __future__ import annotations

import json
import logging
import signal
import sqlite3
from types import SimpleNamespace

import pytest

from v6lite.__main__ import run_engine
from v6lite.approval import GateError
from v6lite.tests.helpers import make_cfg
from v6lite.tests.test_batch28_lifecycle import _audit, _holding, _writes, no_network


def _exit_pages(rig):
    return [(title, body) for title, body in rig.channel.messages
            if 'P0' in title and '引擎已退出' in title]


def _saved_audit(rig, kind):
    # Explicit temp database only: cleanup may have closed the original handle.
    with sqlite3.connect(f'file:{rig.store.path.as_posix()}?mode=ro', uri=True) as conn:
        return [json.loads(row[0]) for row in conn.execute(
            'SELECT payload FROM audit WHERE kind=? ORDER BY rowid', (kind,))]


def _cli_resources(rig, monkeypatch):
    import v6lite.__main__ as cli
    import v6lite.alerts as alerts
    import v6lite.broker.ibkr as ibkr
    import v6lite.engine as engine_mod
    handlers, cleanup, senders = {}, [], []
    store_close = rig.store.close
    alert_close = rig.alerter.close
    alerter_class = alerts.Alerter
    def close_store():
        cleanup.append('store.close')
        store_close()
    def release():
        cleanup.append('lock.release')
    def close_alerts(*args, **kwargs):
        cleanup.append('alerter.close')
        alert_close(*args, **kwargs)
    def sender(channels, **kwargs):
        senders.append(kwargs)
        # A close failure must get a fresh actual synchronous fallback sender,
        # rather than silently reusing a failed/closed original test double.
        return rig.alerter if len(senders) == 1 else alerter_class(channels, **kwargs)
    lock = SimpleNamespace(acquire=lambda *_: None, release=release)
    monkeypatch.setattr(rig.store, 'close', close_store)
    monkeypatch.setattr(rig.alerter, 'close', close_alerts)
    monkeypatch.setattr(cli, 'load_config', lambda *_: rig.cfg)
    monkeypatch.setattr(cli, 'preflight_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, 'setup_logging', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.approval_ops, 'old_v6_processes', lambda: ())
    monkeypatch.setattr(cli, 'check_live_gate', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, 'AccountLock', lambda *_: lock)
    monkeypatch.setattr(cli, 'open_store', lambda *args, **kwargs: rig.store)
    monkeypatch.setattr(ibkr, 'IbkrBroker', lambda *args, **kwargs: rig.broker)
    monkeypatch.setattr(alerts, 'build_channels', lambda *_: [rig.channel])
    monkeypatch.setattr(alerts, 'Alerter', sender)
    monkeypatch.setattr(engine_mod, 'Engine', lambda *args, **kwargs: rig.engine)
    monkeypatch.setattr(cli.signal, 'signal', lambda signum, handler: handlers.__setitem__(signum, handler))
    monkeypatch.setattr(cli, '_now', rig.clock.now)
    return SimpleNamespace(cli=cli, handlers=handlers, cleanup=cleanup,
                           lock=lock, senders=senders, store_close=store_close,
                           alert_close=alert_close)


def _press_SIGINT(rig, resources, monkeypatch):
    pumps = []
    def pump(_):
        pumps.append(True)
        assert len(pumps) == 1, 'registered SIGINT must lead to one normal stop'
        resources.handlers[signal.SIGINT](signal.SIGINT, None)
    monkeypatch.setattr(rig.broker, 'pump', pump)
    return pumps


def test_B28_SystemExit_zero_in_running_engine_becomes_nonzero_exit(
        tmp_path, monkeypatch, caplog):
    rig, pid = _holding(tmp_path, monkeypatch)
    resources = _cli_resources(rig, monkeypatch)
    def accidental_exit(_):
        raise SystemExit(0)
    monkeypatch.setattr(rig.broker, 'pump', accidental_exit)
    caplog.set_level(logging.WARNING)
    try:
        result = resources.cli.main(['run', '--config', str(tmp_path / 'synthetic.toml')])
        assert result != 0, 'unexpected SystemExit(0) must never look successful'
        assert rig.engine._shutdown_request['source'] == 'fatal_error'
        assert rig.engine._shutdown_request['exception_type'] == 'SystemExit'
        assert len(_exit_pages(rig)) == 1
        assert caplog.records[-1].levelno >= logging.ERROR
        assert 'SystemExit' in caplog.records[-1].getMessage()
    finally:
        resources.alert_close()
        resources.store_close()


def test_B28_actual_cmd_run_registered_SIGINT_stops_once_without_exit_P0(
        tmp_path, monkeypatch, caplog):
    rig, pid = _holding(tmp_path, monkeypatch)
    resources = _cli_resources(rig, monkeypatch)
    pumps = _press_SIGINT(rig, resources, monkeypatch)
    caplog.set_level(logging.WARNING)
    try:
        assert resources.cli.cmd_run(SimpleNamespace(dry_run=False, live=False), rig.cfg) == 0
        assert pumps == [True]
        assert signal.SIGINT in resources.handlers
        assert resources.cleanup == ['store.close', 'lock.release', 'alerter.close']
        assert [row['source'] for row in _saved_audit(rig, 'SHUTDOWN_REQUESTED')] == ['SIGINT']
        assert [row['source'] for row in _saved_audit(rig, 'SHUTDOWN')] == ['SIGINT']
        assert not _exit_pages(rig)
        assert not rig.broker.connected and not rig.broker.cancels
    finally:
        resources.alert_close()
        resources.store_close()


@pytest.mark.parametrize('failure_at', ['store.close', 'lock.release', 'alerter.close'])
def test_B28_operator_stop_cleanup_failure_upgrades_to_fatal_and_preserves_stop_details(
        tmp_path, monkeypatch, caplog, failure_at):
    rig, pid = _holding(tmp_path, monkeypatch)
    resources = _cli_resources(rig, monkeypatch)
    _press_SIGINT(rig, resources, monkeypatch)
    error = RuntimeError('synthetic cleanup failure at ' + failure_at)
    if failure_at == 'store.close':
        def fail():
            resources.cleanup.append('store.close')
            resources.store_close()  # exercise P0 construction with a genuinely closed DB
            raise error
        monkeypatch.setattr(rig.store, 'close', fail)
    elif failure_at == 'lock.release':
        def fail():
            resources.cleanup.append('lock.release')
            raise error
        resources.lock.release = fail
    else:
        def fail(*args, **kwargs):
            resources.cleanup.append('alerter.close')
            resources.alert_close(*args, **kwargs)
            raise error
        monkeypatch.setattr(rig.alerter, 'close', fail)
    caplog.set_level(logging.WARNING)
    try:
        with pytest.raises(RuntimeError, match='engine cleanup failed'):
            resources.cli.cmd_run(SimpleNamespace(dry_run=False, live=False), rig.cfg)
        assert resources.cleanup == ['store.close', 'lock.release', 'alerter.close']
        assert rig.engine._shutdown_request['source'] == 'fatal_error'
        [page] = _exit_pages(rig)
        assert all(value in page[1] for value in ('AAPL', '706', '338.62', 'g3t'))
        assert failure_at in page[1]
        assert caplog.records[-1].levelno >= logging.ERROR
        assert failure_at in caplog.records[-1].getMessage()
        assert any(row.levelno == logging.WARNING and 'SHUTDOWN_REQUESTED' in row.getMessage()
                   and 'fatal_error' in row.getMessage() for row in caplog.records)
        if failure_at == 'alerter.close':
            assert len(resources.senders) == 2 and resources.senders[-1].get('synchronous') is True
    finally:
        resources.alert_close()
        resources.store_close()


def test_B28_early_preflight_refusal_pages_without_opening_store_or_broker(
        tmp_path, monkeypatch, caplog):
    import v6lite.__main__ as cli
    import v6lite.alerts as alerts
    import v6lite.broker.ibkr as ibkr
    cfg = make_cfg(tmp_path)
    channel = alerts.MemoryChannel()
    touched = []
    def forbidden(name):
        def call(*args, **kwargs):
            touched.append(name)
            raise AssertionError('preflight refusal must not enter ' + name)
        return call
    def refuse(*args, **kwargs):
        raise GateError('synthetic identity mismatch for ' + cfg.account)
    monkeypatch.setattr(cli, 'load_config', lambda *_: cfg)
    monkeypatch.setattr(cli, 'preflight_state', refuse)
    monkeypatch.setattr(cli, 'open_store', forbidden('Store open'))
    monkeypatch.setattr(cli, 'setup_logging', forbidden('logging/state setup'))
    monkeypatch.setattr(cli, 'AccountLock', forbidden('account lock'))
    monkeypatch.setattr(ibkr, 'IbkrBroker', forbidden('broker construction'))
    monkeypatch.setattr(alerts, 'build_channels', lambda *_: [channel])
    caplog.set_level(logging.WARNING)
    assert cli.main(['run', '--config', str(tmp_path / 'synthetic.toml')]) != 0
    [page] = [(title, body) for title, body in channel.messages if 'P0' in title and '引擎已退出' in title]
    assert 'config' in page[1] and 'identity mismatch' in page[1]
    assert cfg.account not in page[1]
    assert touched == [] and not cfg.resolved_state_dir().exists()
    assert caplog.records[-1].levelno >= logging.ERROR
    assert 'config' in caplog.records[-1].getMessage()


def test_B28_invalid_config_has_nonzero_ERROR_without_untrusted_remote_or_state(
        tmp_path, monkeypatch, caplog):
    import v6lite.__main__ as cli
    import v6lite.alerts as alerts
    import v6lite.broker.ibkr as ibkr
    invalid = tmp_path / 'invalid.toml'
    invalid.write_text('this is not valid TOML !!!', encoding='utf-8')
    touched = []
    def forbidden(*args, **kwargs):
        touched.append(True)
        raise AssertionError('invalid config must not open state, broker, or alert destinations')
    monkeypatch.setattr(cli, 'open_store', forbidden)
    monkeypatch.setattr(cli, 'setup_logging', forbidden)
    monkeypatch.setattr(alerts, 'build_channels', forbidden)
    monkeypatch.setattr(ibkr, 'IbkrBroker', forbidden)
    caplog.set_level(logging.WARNING)
    assert cli.main(['run', '--config', str(invalid)]) != 0
    assert touched == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ['invalid.toml']
    assert caplog.records[-1].levelno >= logging.ERROR
    assert 'source=config' in caplog.records[-1].getMessage()


@pytest.mark.parametrize('raw,secrets', [
    ('Authorization: Bearer SYNTH_BEARER_SECRET', ('SYNTH_BEARER_SECRET',)),
    ("'Authorization': 'Basic SYNTH_BASIC_SECRET'", ('SYNTH_BASIC_SECRET',)),
    ("'token': 'SYNTH_QUOTED_SECRET with spaces'", ('SYNTH_QUOTED_SECRET', 'with spaces')),
    ('"password": "SYNTH_DOUBLE_QUOTED with spaces"', ('SYNTH_DOUBLE_QUOTED', 'with spaces')),
    ('api_key=SYNTH_API_KEY; webhook=https://example.invalid/SYNTH_URL', ('SYNTH_API_KEY', 'SYNTH_URL')),
])
def test_B28_exit_reason_masks_authorization_and_quoted_secrets(raw, secrets):
    from v6lite.lifecycle import safe_reason
    text = safe_reason(RuntimeError('DU0000000 ' + raw), 'DU0000000')
    assert 'DU0000000' not in text
    assert all(secret not in text for secret in secrets)
    assert '<redacted' in text


def test_B28_repeated_pump_and_tick_network_errors_remain_in_recovery_loop(
        tmp_path, monkeypatch, caplog):
    rig, pid = _holding(tmp_path, monkeypatch)
    started, before = rig.clock.now(), _writes(rig)
    pumps, ticks, attempts, checked = [], [], [], []
    tick = rig.engine.tick
    connect = rig.broker.connect
    def pump(seconds):
        pumps.append(rig.clock.now())
        rig.advance(seconds=seconds)
        if len(pumps) <= 2:
            rig.broker.connected = False
            raise ConnectionError('synthetic consecutive pump reset')
    def resetting_tick():
        ticks.append(rig.clock.now())
        if len(ticks) <= 2:
            raise ConnectionError('synthetic consecutive tick reset')
        tick()
    def reconnect():
        attempts.append(rig.clock.now())
        connect()
    monkeypatch.setattr(rig.broker, 'pump', pump)
    monkeypatch.setattr(rig.engine, 'tick', resetting_tick)
    monkeypatch.setattr(rig.broker, 'connect', reconnect)
    caplog.set_level(logging.INFO)
    def should_stop():
        if (rig.clock.now() - started).total_seconds() < 660:
            return False
        assert len(pumps) >= 660 and len(ticks) >= 660
        assert len(attempts) == 1
        assert (attempts[0] - started).total_seconds() == 31
        assert rig.engine.flags.broker_connected and rig.engine.flags.reconcile_ok
        assert rig.store.position(pid)['qty_open'] == 3
        assert not _audit(rig, 'SHUTDOWN') and not _exit_pages(rig)
        assert _writes(rig) == before
        assert sum('[HEARTBEAT]' in row.getMessage() for row in caplog.records) >= 2
        checked.append(True)
        return True
    try:
        assert run_engine(rig.engine, rig.broker, should_stop=should_stop) == 0
        assert checked
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_stale_connected_flag_is_retired_before_reconnect(tmp_path, monkeypatch):
    rig, pid = _holding(tmp_path, monkeypatch)
    started, before = rig.clock.now(), _writes(rig)
    pumps, disconnects, attempts, checked = [], [], [], []
    disconnect, connect = rig.broker.disconnect, rig.broker.connect
    def pump(seconds):
        pumps.append(rig.clock.now())
        if len(pumps) == 1:
            assert rig.broker.is_connected(), 'fixture must retain stale API-ready flag'
            raise ConnectionResetError(10054, 'synthetic API reset before flag changes')
        rig.advance(seconds=seconds)
    def retire():
        disconnects.append(rig.clock.now())
        disconnect()
    def reconnect():
        attempts.append(rig.clock.now())
        connect()
    monkeypatch.setattr(rig.broker, 'pump', pump)
    monkeypatch.setattr(rig.broker, 'disconnect', retire)
    monkeypatch.setattr(rig.broker, 'connect', reconnect)
    def should_stop():
        assert len(pumps) < 45
        if not attempts:
            return False
        assert disconnects == [started]
        assert (attempts[0] - started).total_seconds() == 30
        assert rig.engine.flags.reconcile_ok
        assert not _audit(rig, 'SHUTDOWN') and not _exit_pages(rig)
        assert _writes(rig) == before and rig.store.position(pid)['qty_open'] == 3
        checked.append(True)
        return True
    try:
        assert run_engine(rig.engine, rig.broker, should_stop=should_stop) == 0
        assert checked
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_shutdown_internal_failure_upgrades_SIGINT_to_fatal_before_exit(
        tmp_path, monkeypatch, caplog):
    rig, pid = _holding(tmp_path, monkeypatch)
    resources = _cli_resources(rig, monkeypatch)
    _press_SIGINT(rig, resources, monkeypatch)
    def fail_shutdown(*args, **kwargs):
        raise RuntimeError('synthetic shutdown invariant failure')
    monkeypatch.setattr(rig.engine, 'shutdown', fail_shutdown)
    caplog.set_level(logging.WARNING)
    try:
        result = resources.cli.cmd_run(SimpleNamespace(dry_run=False, live=False), rig.cfg)
        assert result != 0
        assert rig.engine._shutdown_request['source'] == 'fatal_error'
        assert [row['source'] for row in _saved_audit(rig, 'SHUTDOWN_REQUESTED')] == ['SIGINT', 'fatal_error']
        [page] = _exit_pages(rig)
        assert 'shutdown invariant failure' in page[1]
        assert all(value in page[1] for value in ('AAPL', '706', '338.62'))
        assert caplog.records[-1].levelno >= logging.ERROR
        assert 'shutdown invariant failure' in caplog.records[-1].getMessage()
    finally:
        resources.alert_close()
        resources.store_close()


def test_B28_queued_fatal_page_is_resent_synchronously_after_alert_worker_close_failure(
        tmp_path, monkeypatch, caplog):
    """A queued delivery is not evidence that any exit page actually arrived.

    The first sender accepts the P0 into a synthetic dead-worker queue. Its
    close then fails. The fallback must independently deliver the original
    fatal page using a fresh real synchronous Alerter and preserve provenance.
    """
    rig, pid = _holding(tmp_path, monkeypatch)
    resources = _cli_resources(rig, monkeypatch)
    queued = []
    primary_reason = 'synthetic primary fatal awaiting alert worker delivery'
    cleanup_reason = 'synthetic alert worker already stopped during close'
    def queue_only(now, level, subject, body='', **kwargs):
        queued.append((level, subject, body, kwargs.get('card')))
        return 'synthetic-queued-undelivered-1'
    def failed_close(*args, **kwargs):
        resources.cleanup.append('alerter.close')
        raise RuntimeError(cleanup_reason)
    def fatal(_):
        raise RuntimeError(primary_reason)
    monkeypatch.setattr(rig.alerter, 'send', queue_only)
    monkeypatch.setattr(rig.alerter, 'close', failed_close)
    monkeypatch.setattr(rig.broker, 'pump', fatal)
    caplog.set_level(logging.WARNING)
    try:
        with pytest.raises(RuntimeError, match='engine cleanup failed'):
            resources.cli.cmd_run(SimpleNamespace(dry_run=False, live=False), rig.cfg)
        assert resources.cleanup == ['store.close', 'lock.release', 'alerter.close']
        assert len(queued) == 1 and queued[0][0] == 'P0'
        assert primary_reason in queued[0][2]
        # This channel counts only real delivery, never the queued acceptance.
        [page] = _exit_pages(rig)
        assert primary_reason in page[1]
        assert cleanup_reason not in page[1]
        assert all(value in page[1] for value in ('AAPL', '706', '338.62', 'g3t'))
        assert len(resources.senders) == 2
        assert resources.senders[-1].get('synchronous') is True
        assert rig.engine._shutdown_request['source'] == 'fatal_error'
        assert rig.engine._shutdown_request['exception_type'] == 'RuntimeError'
        assert rig.engine._shutdown_request['reason'] == primary_reason
        assert [row['source'] for row in _saved_audit(rig, 'SHUTDOWN_REQUESTED')] == ['fatal_error']
        assert caplog.records[-1].levelno >= logging.ERROR
        assert primary_reason in caplog.records[-1].getMessage()
        assert cleanup_reason not in caplog.records[-1].getMessage()
    finally:
        resources.alert_close()
        resources.store_close()


def _physical_loss_inside_tick(tmp_path, monkeypatch, *, backend_loss_first):
    """Real tick drains the loss before reaching its connection maintenance."""
    from v6lite.models import ConnectionEvent
    rig, pid = _holding(tmp_path, monkeypatch)
    started, before = rig.clock.now(), _writes(rig)
    pumps, ticks, physical_at, attempts, checked = [], [], [], [], []
    tick, connect = rig.engine.tick, rig.broker.connect
    def pump(seconds):
        pumps.append(rig.clock.now())
        rig.advance(seconds=seconds)
    def tick_with_native_event():
        ticks.append(rig.clock.now())
        if backend_loss_first and len(ticks) == 1:
            assert rig.broker.is_connected()
            rig.broker.events.append(ConnectionEvent(False, '1100 synthetic backend connection lost'))
        drop_on = 2 if backend_loss_first else 1
        if len(ticks) == drop_on:
            assert rig.broker.is_connected()
            if backend_loss_first:
                assert rig.engine._reconnect_episode and rig.engine._link_down
            rig.broker.connected = False
            physical_at.append(rig.clock.now())
            # The actual _tick -> _drain -> _handle_event consumes this before
            # the actual _maintain_connection later in this same tick.
            rig.broker.events.append(ConnectionEvent(False, 'synthetic physical socket lost inside tick'))
        tick()
    def reconnect():
        attempts.append(rig.clock.now())
        connect()
    monkeypatch.setattr(rig.broker, 'pump', pump)
    monkeypatch.setattr(rig.engine, 'tick', tick_with_native_event)
    monkeypatch.setattr(rig.broker, 'connect', reconnect)
    def should_stop():
        assert len(pumps) < 45, 'physical socket loss must recover after its initial 30-second gate'
        if not attempts:
            if physical_at:
                assert not _audit(rig, 'SHUTDOWN')
                assert not rig.engine.flags.reconcile_ok
            return False
        assert len(physical_at) == len(attempts) == 1
        assert (attempts[0] - physical_at[0]).total_seconds() == 30
        assert rig.engine.flags.broker_connected and rig.engine.flags.reconcile_ok
        assert rig.store.position(pid)['qty_open'] == 3
        assert rig.store.protection(pid)['broker_order_id'] == 706
        assert _writes(rig) == before
        assert not _audit(rig, 'SHUTDOWN') and not _exit_pages(rig)
        checked.append(True)
        return True
    try:
        assert run_engine(rig.engine, rig.broker, should_stop=should_stop) == 0
        assert checked
        expected_drop_seconds = 2 if backend_loss_first else 1
        assert (physical_at[0] - started).total_seconds() == expected_drop_seconds
    finally:
        rig.alerter.close()
        rig.store.close()


def test_B28_disconnect_event_drained_inside_tick_still_waits_first_30_seconds(
        tmp_path, monkeypatch):
    _physical_loss_inside_tick(tmp_path, monkeypatch, backend_loss_first=False)


def test_B28_backend_1100_then_physical_loss_still_waits_first_30_seconds(
        tmp_path, monkeypatch):
    _physical_loss_inside_tick(tmp_path, monkeypatch, backend_loss_first=True)


def test_B28_queued_fatal_page_undelivered_after_quiet_close_gets_sync_fallback(
        tmp_path, monkeypatch, caplog):
    """close returning normally does not prove that a queued P0 was delivered."""
    rig, pid = _holding(tmp_path, monkeypatch)
    resources = _cli_resources(rig, monkeypatch)
    queued = []
    reason = 'synthetic fatal still undelivered after quiet alert close'
    def queue_only(now, level, subject, body='', **kwargs):
        queued.append((level, subject, body))
        return 'synthetic-quiet-close-undelivered-1'
    def quiet_close(*args, **kwargs):
        resources.cleanup.append('alerter.close')
        resources.alert_close(*args, **kwargs)
        # The queued delivery remains unknown/unconfirmed despite no exception.
    def fatal(_):
        raise RuntimeError(reason)
    monkeypatch.setattr(rig.alerter, 'send', queue_only)
    monkeypatch.setattr(rig.alerter, 'delivery', lambda _: None)
    monkeypatch.setattr(rig.alerter, 'close', quiet_close)
    monkeypatch.setattr(rig.broker, 'pump', fatal)
    caplog.set_level(logging.WARNING)
    try:
        result = resources.cli.cmd_run(SimpleNamespace(dry_run=False, live=False), rig.cfg)
        assert result != 0
        assert resources.cleanup == ['store.close', 'lock.release', 'alerter.close']
        assert len(queued) == 1 and queued[0][0] == 'P0'
        [page] = _exit_pages(rig)
        assert reason in page[1]
        assert all(value in page[1] for value in ('AAPL', '706', '338.62', 'g3t'))
        assert len(resources.senders) == 2 and resources.senders[-1].get('synchronous') is True
        assert rig.engine._shutdown_request['source'] == 'fatal_error'
        assert rig.engine._shutdown_request['reason'] == reason
        assert [row['source'] for row in _saved_audit(rig, 'SHUTDOWN_REQUESTED')] == ['fatal_error']
        assert caplog.records[-1].levelno >= logging.ERROR
        assert reason in caplog.records[-1].getMessage()
    finally:
        resources.alert_close()
        resources.store_close()

"""B26 independent shutdown-report contracts: real FakeBroker and temp SQLite.

No broker sockets or operator files.  The production shutdown and report
scheduler are always called directly; spies delegate to their real dependencies.
The final two cases inject real report-render/write failures rather than making
_end_of_day or shutdown pretend to succeed.  Existing B25 files are untouched.
"""
from __future__ import annotations

import json
import socket
from datetime import timedelta

import pytest

from v6lite.alerts import Alerter
from v6lite.config import ProtectionConfig
from v6lite.engine import Engine
from v6lite.market_calendar import session_for
from v6lite.models import IntentStatus, OrderRequest, Purpose, Side
from v6lite.store import Store
from v6lite.tests.helpers import Rig, et

DAY = (2026, 10, 14)
DAY_KEY = '2026-10-14'
EARLY_DAY = (2026, 11, 27)  # Black Friday, 13:00 ET close.


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Windows asyncio initializes its local socketpair using connect.  Complete
    # that SDK/local-loop initialization before forbidding broker/network I/O.
    import asyncio
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        policy.set_event_loop(loop)
    from ib_insync import IB
    from ib_insync.util import getLoop
    assert getLoop() is loop

    def forbidden(*args, **kwargs):
        raise AssertionError('B26 shutdown contracts must never use a network or IB connection')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(IB, 'connect', forbidden)
    monkeypatch.setattr(IB, 'connectAsync', forbidden)


@pytest.fixture
def rig_factory(tmp_path):
    made = []

    def make(*, dry_run=False, day=DAY, protection=False):
        rig = Rig(tmp_path / ('rig-' + str(len(made))),
                  dry_run=dry_run, start=et(9, 50, day=day),
                  protection=ProtectionConfig(enabled=protection))
        made.append(rig)
        return rig

    yield make
    # Do not call shutdown from cleanup: that would conceal the tested number
    # of report attempts and add broker/order actions after a failed assertion.
    for rig in made:
        rig.alerter.close()
        rig.store.close()


def _remote_reports(rig):
    # Inspect the actual daily_health_card delivered through Alerter to the
    # real MemoryChannel, rather than mocking Alerter.send or report scheduling.
    return [(subject, body) for subject, body in rig.channel.messages
            if '行情可用率：' in body and 'code_sha：' in body]


def _assert_report(rig, day_key, *, remote=1):
    path = rig.engine.report_dir / (day_key + '.md')
    assert path.is_file(), 'shutdown after market close must write the real local daily report'
    text = path.read_text(encoding='utf-8')
    assert text.startswith('# V6-Lite daily report') and day_key in text
    assert len(_remote_reports(rig)) == remote
    state = rig.store.control('report:' + day_key)
    assert state and state.startswith('sent:'), 'successful delivery must have a durable sent marker'
    return path, text


def _assert_shutdown_audited_and_disconnected(rig):
    rows = rig.store.conn.execute(
        "SELECT payload FROM audit WHERE kind='SHUTDOWN' ORDER BY id").fetchall()
    assert rows, 'normal shutdown audit must survive report generation'
    assert rig.broker.is_connected() is False
    return [json.loads(row['payload']) for row in rows]


def _reopen(rig, tmp_path):
    """Real SQLite close/reopen and a new Engine/Alerter on the same account."""
    state_path, levels_fn = rig.store.path, rig.store._levels_fn
    report_dir = rig.engine.report_dir
    rig.alerter.close()
    rig.store.close()
    rig.store = Store(state_path, levels_fn)
    rig.alerter = Alerter([rig.channel], synchronous=True)
    rig.engine = Engine(rig.cfg, rig.broker, rig.store, rig.alerter, rig.clock,
                        dry_run=rig.engine.dry_run, report_dir=report_dir)
    rig.engine.startup()


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
@pytest.mark.parametrize('seconds_after_close', [1, 120, 299], ids=['close+1s', 'close+2m', 'close+4m59s'])
def test_B26_shutdown_after_close_before_scheduled_report_writes_and_delivers_once(
        rig_factory, dry_run, seconds_after_close):
    rig = rig_factory(dry_run=dry_run)
    rig.start()
    assert _remote_reports(rig) == [] and rig.store.control('report:' + DAY_KEY) is None
    rig.at(et(16, day=DAY) + timedelta(seconds=seconds_after_close))
    rig.engine.shutdown(grace_seconds=0)
    _assert_report(rig, DAY_KEY)
    _assert_shutdown_audited_and_disconnected(rig)
    assert rig.broker.sent == [] and rig.broker.sent_protections == [] and rig.broker.cancels == []


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
@pytest.mark.parametrize('seconds_before_close', [1, 120], ids=['close-1s', 'close-2m'])
def test_B26_shutdown_before_regular_close_does_not_emit_a_daily_report(
        rig_factory, dry_run, seconds_before_close):
    rig = rig_factory(dry_run=dry_run)
    rig.start()
    rig.at(et(16, day=DAY) - timedelta(seconds=seconds_before_close))
    rig.engine.shutdown(grace_seconds=0)
    assert _remote_reports(rig) == []
    assert rig.store.control('report:' + DAY_KEY) is None
    assert not (rig.engine.report_dir / (DAY_KEY + '.md')).exists()
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
@pytest.mark.parametrize('day', [(2026, 10, 17), (2026, 11, 26)], ids=['Saturday', 'Thanksgiving'])
def test_B26_shutdown_on_a_nontrading_day_does_not_invent_a_daily_report(
        rig_factory, dry_run, day):
    rig = rig_factory(dry_run=dry_run)
    rig.start()
    rig.at(et(17, day=day))
    assert session_for(rig.clock.now().date()) is None
    rig.engine.shutdown(grace_seconds=0)
    day_key = '-'.join((str(day[0]), f'{day[1]:02}', f'{day[2]:02}'))
    assert _remote_reports(rig) == [] and rig.store.control('report:' + day_key) is None
    assert not list(rig.engine.report_dir.glob('*.md'))
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
@pytest.mark.parametrize('seconds_after_close', [1, 299], ids=['close+1s', 'close+4m59s'])
def test_B26_shutdown_supplement_uses_the_actual_early_close(
        rig_factory, dry_run, seconds_after_close):
    rig = rig_factory(dry_run=dry_run, day=EARLY_DAY)
    rig.start()
    close = et(13, day=EARLY_DAY)
    assert session_for(close.date()).close_utc == close
    rig.at(close + timedelta(seconds=seconds_after_close))
    rig.engine.shutdown(grace_seconds=0)
    _assert_report(rig, '2026-11-27')
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
def test_B26_shutdown_just_before_early_close_does_not_report(rig_factory, dry_run):
    rig = rig_factory(dry_run=dry_run, day=EARLY_DAY)
    rig.start()
    rig.at(et(12, 59, 59, day=EARLY_DAY))
    rig.engine.shutdown(grace_seconds=0)
    assert _remote_reports(rig) == []
    assert rig.store.control('report:2026-11-27') is None
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
def test_B26_normal_scheduled_report_is_not_resent_at_shutdown_or_sqlite_reopen(
        rig_factory, tmp_path, dry_run):
    rig = rig_factory(dry_run=dry_run)
    rig.start()
    rig.at(et(16, 5, 1, day=DAY))
    rig.engine._end_of_day(rig.clock.now())
    path, text = _assert_report(rig, DAY_KEY)
    original_state = rig.store.control('report:' + DAY_KEY)
    rig.engine.shutdown(grace_seconds=0)
    assert path.read_text(encoding='utf-8') == text
    assert rig.store.control('report:' + DAY_KEY) == original_state
    assert len(_remote_reports(rig)) == 1
    _reopen(rig, tmp_path)
    rig.engine.shutdown(grace_seconds=0)
    assert rig.store.control('report:' + DAY_KEY) == original_state
    assert len(_remote_reports(rig)) == 1 and path.read_text(encoding='utf-8') == text
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
def test_B26_supplement_delivery_is_durable_across_real_sqlite_close_and_engine_reopen(
        rig_factory, tmp_path, dry_run):
    rig = rig_factory(dry_run=dry_run)
    rig.start()
    rig.at(et(16, 2, day=DAY))
    rig.engine.shutdown(grace_seconds=0)
    path, text = _assert_report(rig, DAY_KEY)
    original_state = rig.store.control('report:' + DAY_KEY)
    _reopen(rig, tmp_path)
    rig.at(et(16, 3, day=DAY))
    rig.engine.shutdown(grace_seconds=0)
    assert rig.store.control('report:' + DAY_KEY) == original_state
    assert len(_remote_reports(rig)) == 1 and path.read_text(encoding='utf-8') == text
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
def test_B26_written_local_report_marker_also_prevents_duplicate_rendering(
        rig_factory, tmp_path, monkeypatch, dry_run):
    import v6lite.report as report_module
    original_render = report_module.daily_report
    calls = []

    def render(*args, **kwargs):
        text = original_render(*args, **kwargs)
        calls.append(text)
        return text

    monkeypatch.setattr(report_module, 'daily_report', render)
    rig = rig_factory(dry_run=dry_run)
    rig.alerter.close()
    rig.alerter = Alerter([], synchronous=True)
    rig.engine.alerter = rig.alerter
    rig.start()
    rig.at(et(16, 5, 1, day=DAY))
    rig.engine._end_of_day(rig.clock.now())
    assert len(calls) == 1 and calls[0].startswith('# V6-Lite daily report')
    assert rig.store.control('report:' + DAY_KEY) == 'written_local'
    rig.engine.shutdown(grace_seconds=0)
    _reopen(rig, tmp_path)
    rig.engine.shutdown(grace_seconds=0)
    assert len(calls) == 1 and rig.store.control('report:' + DAY_KEY) == 'written_local'
    assert _remote_reports(rig) == []
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
@pytest.mark.parametrize('reopen', [False, True], ids=['known-delivery', 'unknown-after-SQLite-reopen'])
def test_B26_already_generated_pending_report_is_not_retried_by_shutdown(
        rig_factory, tmp_path, monkeypatch, dry_run, reopen):
    import v6lite.report as report_module
    original_render = report_module.daily_report
    calls = []

    def render(*args, **kwargs):
        text = original_render(*args, **kwargs)
        calls.append(text)
        return text

    monkeypatch.setattr(report_module, 'daily_report', render)
    rig = rig_factory(dry_run=dry_run)
    rig.start()
    rig.at(et(16, 5, 1, day=DAY))
    # Real failed delivery: local Markdown was generated; Alerter returned a
    # real delivery id with False outcome, and _end_of_day persisted pending.
    rig.channel.fail = True
    rig.engine._end_of_day(rig.clock.now())
    pending = rig.store.control('report:' + DAY_KEY)
    assert pending and pending.startswith('pending:')
    delivery_id = pending.split(':', 2)[1]
    assert rig.alerter.delivery(delivery_id) is False
    report_path = rig.engine.report_dir / (DAY_KEY + '.md')
    assert report_path.read_text(encoding='utf-8').startswith('# V6-Lite daily report')
    assert len(calls) == 1 and _remote_reports(rig) == []
    rig.channel.fail = False
    if reopen:
        _reopen(rig, tmp_path)
        assert rig.alerter.knows(delivery_id) is False
    else:
        assert rig.alerter.knows(delivery_id) is True
    rig.at(et(16, 21, day=DAY))  # Beyond normal retry delay; shutdown is not a retry loop.
    rig.engine.shutdown(grace_seconds=0)
    assert len(calls) == 1 and _remote_reports(rig) == []
    assert rig.store.control('report:' + DAY_KEY) == pending
    _assert_shutdown_audited_and_disconnected(rig)


@pytest.mark.parametrize('dry_run', [False, True], ids=['Paper', 'dry-run'])
def test_B26_normal_end_of_day_still_retries_a_failed_pending_delivery(
        rig_factory, dry_run):
    rig = rig_factory(dry_run=dry_run)
    rig.start()
    rig.at(et(16, 5, 1, day=DAY))
    rig.channel.fail = True
    rig.engine._end_of_day(rig.clock.now())
    assert rig.store.control('report:' + DAY_KEY).startswith('pending:')
    assert _remote_reports(rig) == []
    rig.channel.fail = False
    rig.at(et(16, 21, day=DAY))
    rig.engine._end_of_day(rig.clock.now())
    _assert_report(rig, DAY_KEY)
    rig.engine.shutdown(grace_seconds=0)
    assert len(_remote_reports(rig)) == 1
    _assert_shutdown_audited_and_disconnected(rig)


def _working_gtc_and_day(rig_factory, monkeypatch):
    """A real booked position/GTC plus a separately submitted unfilled DAY BUY."""
    rig = rig_factory(day=(2026, 10, 6), protection=True)
    rig.open_aapl()  # Real ENTRY fill, ledger booking and broker-confirmed GTC.
    [pos] = rig.store.open_positions()
    protection = dict(rig.store.protection(pos['position_id']))
    gtc_ref = protection['intent_id']
    day_ref = 'v6l-entry-b26-shutdown-day'
    req = OrderRequest(day_ref, 'SPY', Side.BUY, 1, 500.0, Purpose.ENTRY)
    rig.store.create_intent(rig.clock.now(), req, atr=2.0, count_entry_day=None)
    ack = rig.broker.submit_limit(req)
    rig.store.update_intent(rig.clock.now(), day_ref, status=IntentStatus.SENT,
                           broker_order_id=ack.broker_order_id, perm_id=ack.perm_id,
                           sent_at=rig.clock.now())
    trace = []
    original_cancel = rig.broker.cancel
    original_orders = rig.broker.open_orders
    original_audit = rig.store.audit
    original_disconnect = rig.broker.disconnect

    def cancel(ref, **kwargs):
        trace.append(('cancel', ref))
        original_cancel(ref, **kwargs)
        if ref == day_ref:
            rig.broker.confirm_cancel(ref)  # Actual scripted broker terminal callback.

    def orders():
        result = original_orders()
        if any(o.intent_id == gtc_ref for o in result):
            trace.append(('gtc_present', gtc_ref))
        return result

    def audit(now, kind, **payload):
        original_audit(now, kind, **payload)
        if kind == 'SHUTDOWN':
            trace.append(('SHUTDOWN', kind))

    def disconnect():
        trace.append(('disconnect', 'broker'))
        original_disconnect()

    monkeypatch.setattr(rig.broker, 'cancel', cancel)
    monkeypatch.setattr(rig.broker, 'open_orders', orders)
    monkeypatch.setattr(rig.store, 'audit', audit)
    monkeypatch.setattr(rig.broker, 'disconnect', disconnect)
    rig.at(et(16, 2))
    return rig, gtc_ref, day_ref, protection, trace


def _assert_original_safe_shutdown_sequence(rig, gtc_ref, day_ref, protection, trace):
    names = [name for name, _ in trace]
    assert ('cancel', day_ref) in trace and ('cancel', gtc_ref) not in trace
    assert names.index('cancel') < names.index('SHUTDOWN') < names.index('disconnect')
    assert names.index('gtc_present') < names.index('SHUTDOWN')
    assert rig.broker.orders[day_ref].status == 'Cancelled'
    assert rig.broker.orders[gtc_ref].status in {'Submitted', 'PreSubmitted'}
    [working] = [o for o in rig.broker.open_orders() if o.intent_id == gtc_ref]
    assert rig.engine._protection_order_valid(rig.store.protection(protection['position_id']), working)
    assert working.remaining == rig.store.position(protection['position_id'])['qty_open']
    assert working.perm_id == protection['perm_id'] and working.broker_order_id == protection['broker_order_id']
    [shutdown] = _assert_shutdown_audited_and_disconnected(rig)
    assert shutdown['unresolved'] == [] and shutdown['broker_working'] == []


def test_B26_supplement_preserves_DAY_cancel_GTC_retention_SHUTDOWN_disconnect_order(
        rig_factory, monkeypatch):
    rig, gtc_ref, day_ref, protection, trace = _working_gtc_and_day(rig_factory, monkeypatch)
    rig.engine.shutdown(grace_seconds=2)
    _assert_original_safe_shutdown_sequence(rig, gtc_ref, day_ref, protection, trace)
    _assert_report(rig, '2026-10-06')


@pytest.mark.parametrize('failure', ['render', 'local_write'])
def test_B26_report_failure_does_not_interrupt_safe_order_shutdown(
        rig_factory, monkeypatch, tmp_path, failure):
    import v6lite.report as report_module
    rig, gtc_ref, day_ref, protection, trace = _working_gtc_and_day(rig_factory, monkeypatch)
    original_render = report_module.daily_report
    attempts = []

    def render(*args, **kwargs):
        text = original_render(*args, **kwargs)
        assert text.startswith('# V6-Lite daily report')
        attempts.append(text)
        if failure == 'render':
            raise OSError('synthetic B26 renderer failure after actual ledger read')
        return text

    monkeypatch.setattr(report_module, 'daily_report', render)
    if failure == 'local_write':
        blocker = tmp_path / 'report-directory-is-a-file'
        blocker.write_text('synthetic report write blocker', encoding='utf-8')
        rig.engine.report_dir = blocker
    rig.engine.shutdown(grace_seconds=2)
    _assert_original_safe_shutdown_sequence(rig, gtc_ref, day_ref, protection, trace)
    assert len(attempts) == 1, 'shutdown must attempt the real after-close report despite the injected failure'
    assert _remote_reports(rig) == []
    state = rig.store.control('report:2026-10-06')
    assert state is None or not state.startswith(('sent', 'written_local'))

"""B24 genuine wrapper 2110 keeps loss authority and a single readable diagnostic."""
import logging

from v6lite.broker.ibkr import IbkrBroker
from v6lite.models import ConnectionEvent
from v6lite.tests.test_batch23_contracts import _working, _writes
from v6lite.tests.test_batch24_presentation import _audit_payloads, _contains_value
from v6lite.tests.test_batch24_stp_echo import _no_network


def test_B24_native_2110_has_one_readable_log_raw_audit_and_unchanged_loss_gate(tmp_path, caplog):
    rig, pid = _working(tmp_path)
    row = dict(rig.store.protection(pid))
    intent = dict(rig.store.intent(row['intent_id']))
    assert rig.engine.flags.broker_connected
    before = _writes(rig)
    adapter = IbkrBroker(rig.cfg)
    adapter._ensure_channel_hooks()
    adapter.ib.errorEvent += adapter._on_error
    adapter.set_urgent_handler(rig.engine._on_urgent_broker_event)
    raw = r'IBKR server: \u8fde\u63a5\u4e2d\u65ad；等待恢复'
    caplog.set_level(logging.INFO)
    caplog.clear()
    adapter.ib.wrapper.error(-1, 2110, raw, '')
    events = adapter.drain_events()
    losses = [event for event in events if isinstance(event, ConnectionEvent)]
    assert len(losses) == 1 and losses[0].connected is False
    assert losses[0].reason == '2110:' + raw
    for event in events:
        rig.engine._handle_event(rig.clock.now(), event)
    assert not rig.engine.flags.broker_connected and not rig.engine.flags.reconcile_ok
    assert rig.engine._link_down
    snapshot = rig.engine.status_snapshot(rig.clock.now())
    assert snapshot['entries_allowed'] is False and snapshot['entry_blocks']
    assert _writes(rig) == before
    assert rig.store.protection(pid)['state'] == row['state']
    assert rig.store.intent(row['intent_id'])['hold_reason'] == intent['hold_reason']
    records = [record for record in caplog.records
               if record.name in {'ib_insync.wrapper','v6lite.ibkr'} and '2110' in record.getMessage()]
    assert len(records) == 1, [record.getMessage() for record in records]
    assert records[0].levelno >= logging.WARNING, '2110 connectivity loss must retain warning severity'
    assert '连接中断；等待恢复' in records[0].getMessage()
    assert r'\u8fde' not in records[0].getMessage()
    notices = _audit_payloads(rig, 'IBKR_NOTICE')
    assert len(notices) == 1 and _contains_value(notices[0][1], raw)
    assert _contains_value(notices[0][1], 2110)

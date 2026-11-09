"""Complete own-query ACK rejects newer-round, stale and malformed evidence."""
from dataclasses import replace
from datetime import timedelta
import pytest
from v6lite.models import PriceObservationEvent
from v6lite.tests.test_batch27_observation_audit import observation_rig, _observe, _count
from v6lite.tests.test_batch26_overnight_evidence import _no_network
from v6lite.tests.test_batch31_evidence_replay import evidence_run
from v6lite.tests.test_batch31_overnight import pending_be
from v6lite.tests.test_batch28b_overnight_refresh import _audits, overnight_run


def _capture_complete(wire):
    snapshots=[]
    deliver=wire.deliver
    def capture(event):
        if isinstance(event,PriceObservationEvent) and event.owned_open_orders is not None:
            snapshots.append(event)
        else:
            deliver(event)
    wire.adapter.set_urgent_handler(capture)
    return snapshots,deliver


@pytest.mark.parametrize('newer',['in-flight','complete','unexpected-End'])
def test_B31_completed_snapshot_cannot_confirm_after_newer_same_socket_request(evidence_run,newer):
    rig,pid,wire=evidence_run
    pending_be(rig,pid,wire);wire.apply_pending();rig.advance(microseconds=1)
    snapshots,deliver=_capture_complete(wire)
    rig.broker.open_orders();old=snapshots[-1]
    assert rig.store.protection(pid)['state']=='PRICE_PENDING'
    if newer=='complete':
        rig.advance(microseconds=1);rig.broker.open_orders()
        assert snapshots[-1].query_request_id>old.query_request_id
    elif newer=='unexpected-End':
        wire.ib.wrapper.openOrderEnd()
    else:
        wire.ib.wrapper.startReq('openOrders')
        wire.ib.client.reqAllOpenOrders();wire.harness.loop.drain()
    deliver(old)
    assert rig.store.protection(pid)['state']=='PRICE_PENDING','an older own round cannot confirm after a new request'
    assert not _audits(rig,'PROTECT_PRICE_CONFIRMED')


@pytest.mark.parametrize('old_day',['2026-10-27','2026-10-26'])
def test_B31_legacy_unknown_freeze_cannot_acquire_pending_only_cause_later(evidence_run,old_day):
    rig,pid,wire=evidence_run
    pending_be(rig,pid,wire)
    row=rig.store.protection(pid)
    marker='overnight_tp_frozen_at_price:'+pid+':'+old_day
    rig.store.set_control(rig.clock.now(),marker,rig.clock.now().isoformat())
    rig.store.set_control(rig.clock.now(),'frozen:AAPL','pending BE broker evidence mismatch')
    rig.engine._overnight_failure(rig.clock.now(),row,'修改待确认')
    rig.engine._overnight_record_frozen(rig.clock.now(),row,'TP2')
    wire.ack(only_open_order=True)
    for _ in range(3):rig.advance(seconds=16);rig.tick()
    assert rig.store.control(marker) is not None
    assert rig.store.control('frozen:AAPL') is not None
    assert not _audits(rig,'OVERNIGHT_TP_FREEZE_CLEARED')


@pytest.mark.parametrize('fault',['old-socket','sync','partial','no-request-id',
    'query-before-write','stale','duplicate','nonworking','quantity','permId','aux'])
def test_B31_complete_snapshot_still_requires_exact_working_request(evidence_run,fault):
    rig,pid,wire=evidence_run
    pending_be(rig,pid,wire);wire.apply_pending();rig.advance(microseconds=1)
    snapshots,deliver=_capture_complete(wire)
    rig.broker.open_orders();event=snapshots[-1]
    request=rig.engine._protection_price_requests[pid][1]
    if fault=='old-socket':event=replace(event,socket_generation=event.socket_generation-1)
    elif fault=='sync':event=replace(event,connection_snapshot=True)
    elif fault=='partial':event=replace(event,owned_open_orders=None)
    elif fault=='no-request-id':event=replace(event,query_request_id=None)
    elif fault=='query-before-write':event=replace(event,query_started_at=request)
    elif fault=='stale':rig.advance(seconds=6)
    elif fault=='duplicate':event=replace(event,owned_open_orders=event.owned_open_orders+(event.identity,))
    else:
        values={'nonworking':{'status':'PendingSubmit'},
            'quantity':{'total_quantity':3,'remaining':3.},
            'permId':{'perm_id':event.identity.perm_id+1},'aux':{'aux_price':341.}}
        identity=replace(event.identity,**values[fault])
        event=replace(event,identity=identity,owned_open_orders=(identity,))
    deliver(event)
    assert rig.store.protection(pid)['state']=='PRICE_PENDING'
    assert not _audits(rig,'PROTECT_PRICE_CONFIRMED')


def test_B31_observed_timestamp_does_not_turn_identical_native_price_into_audit_spam(observation_rig):
    rig=observation_rig();event=_observe(rig)
    before=_count(rig.store,'ORDER_PRICE_OBSERVATION')
    for _ in range(7):
        rig.advance(seconds=1)
        rig.engine._handle_event(rig.clock.now(),replace(event,observed_at=rig.clock.now()))
    assert _count(rig.store,'ORDER_PRICE_OBSERVATION')==before
    rig.store.flush_observations(rig.clock.now())
    rows=rig.store.conn.execute("SELECT payload FROM audit WHERE kind='OBSERVATION_SUMMARY'").fetchall()
    import json
    assert sum(item['count'] for row in rows for item in json.loads(row['payload'])['observations'] if item['kind']=='ORDER_PRICE_OBSERVATION')==7

"""A proven replacement terminates the retired ID's trigger episode only."""
from __future__ import annotations
import json
from dataclasses import replace
import pytest
from v6lite.config import ProtectionConfig
from v6lite.models import IntentStatus
from v6lite.tests.helpers import Rig

def _settle(rig):
    rig.engine._drain(rig.clock.now());rig.engine.process_protection_priority()
    rig.engine._drain(rig.clock.now());rig.engine.process_protection_priority()

def _trigger(rig,row):
    rig.px('AAPL',row['trigger_price']-.01)
    rig.engine._monitor_protection_marks(rig.clock.now())
    rig.advance(seconds=rig.cfg.protection.trigger_unfilled_p0_seconds)
    rig.engine._monitor_protection_marks(rig.clock.now())
    key='protect_trigger:'+row['position_id']
    assert rig.store.control(key) is not None
    assert rig.store.control('protect_incident:'+row['position_id']+':trigger unfilled') is not None
    return key

def test_B22_confirmed_old_cancel_and_strong_new_working_proof_end_only_old_trigger(tmp_path):
    rig=Rig(tmp_path,protection=ProtectionConfig());rig.open_aapl()
    pos=rig.store.open_positions()[0];old=dict(rig.store.protection(pos['position_id']))
    key=_trigger(rig,old);before=rig.store.control(key)
    rig.engine._protection_cancel(rig.clock.now(),old,'synthetic operator confirmed replacement')
    assert rig.store.control(key)==before
    rig.px('AAPL',old['trigger_price']+1)
    rig.broker.confirm_cancel(old['intent_id']);_settle(rig)
    for _ in range(4):
        rig.advance(seconds=1);rig.tick();_settle(rig)
        new=dict(rig.store.protection(pos['position_id']))
        if new['intent_id']!=old['intent_id'] and new['state']=='WORKING':break
    new=dict(rig.store.protection(pos['position_id']))
    assert new['intent_id']!=old['intent_id'] and new['state']=='WORKING'
    prior=rig.store.intent(old['intent_id'])
    assert prior['status']==IntentStatus.CANCELLED
    assert prior['last_raw_status'] in ('Cancelled','ApiCancelled') and prior['cancel_seen_at']
    lineage=json.loads(rig.store.control('retired_protection:'+old['intent_id']))
    assert lineage['broker_order_id']==old['broker_order_id'] and lineage['perm_id']==old['perm_id']
    assert rig.store.control(key) is None,'new identity must not inherit the retired stop clock'
    assert rig.store.control('protect_incident:'+pos['position_id']+':trigger unfilled') is None
    rig.engine._monitor_protection_marks(rig.clock.now())
    assert rig.store.control(key) is None,'above stop does not trigger new order'
    rig.px('AAPL',new['trigger_price']-.01);rig.engine._monitor_protection_marks(rig.clock.now())
    episode=json.loads(rig.store.control(key))
    assert episode.get('intent_id')==new['intent_id'] and episode['started_at']==rig.clock.now().isoformat()
    assert rig.store.intent_filled_qty(old['intent_id'])==0,'cancellation is not execution'
    assert len(rig.broker.sent_protections)==2
    assert sum(o.remaining for o in rig.broker.open_orders() if o.side=='SELL')==20
    assert next(p.quantity for p in rig.broker.positions_fresh() if p.symbol=='AAPL')==20
    assert all(o.order_type=='LMT' for o in rig.broker.open_orders() if o.intent_id!=new['intent_id'])

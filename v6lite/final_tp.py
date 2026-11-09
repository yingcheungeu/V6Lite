"""Final-share cancellation continuation and narrow own-TP close provenance."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta

from v6lite.market_calendar import trading_day
from v6lite.models import IntentStatus, OrderBookChangeEvent, PriceObservationEvent, Purpose, StatusEvent
from v6lite.protection import same_cent_price

_CANCELLED = {'Cancelled', 'ApiCancelled'}
_TP = {Purpose.TP1, Purpose.TP2, Purpose.TP3}
_BIND = ('position_id','intent_id','symbol','broker_order_id','perm_id','con_id',
         'account','client_id','modify_generation','order_type','trigger_price','limit_price')


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class FinalTpMixin:
    def _final_cancel_terminal_status(self, now, event):
        """A bound final-exit receipt cannot silence a later native contradiction."""
        row = self.store.protection_for_intent(event.intent_id)
        fact = self._final_cancel_fact(row) if row is not None else None
        if (not fact or not fact.get('confirmed_at') or
                event.connection_snapshot or event.query_snapshot or
                event.observed_at is None or event.observed_at.tzinfo is None or
                event.socket_generation != getattr(self.broker, 'query_generation', None) or
                not datetime.fromisoformat(fact['confirmed_at']) < event.observed_at <= now):
            return
        identity = event.order_identity
        if (event.raw_status in _CANCELLED and event.error_code in (0, 202) and
                identity is not None and event.identity_checked and
                event.broker_order_id == row['broker_order_id'] and event.perm_id == row['perm_id'] and
                self._protection_order_valid(row, replace(identity, status='PreSubmitted')) and
                identity.status == event.raw_status and identity.total_quantity == fact['total_quantity'] and
                identity.filled == event.filled == fact['filled'] and
                identity.remaining == event.remaining and event.remaining in (0, fact['target'])):
            return  # an exact duplicate cancellation is harmless
        self._final_cancel_contradiction(now, row, 'native ' + event.raw_status)

    def _final_cancel_contradiction(self, now, row, source):
        self.store.hold_intent(now, row['intent_id'], 'confirmed cancelled original STP reappeared or contradicted')
        self.store.update_protection(now, row['position_id'], state='UNKNOWN')
        self.store.set_control(now, 'frozen:' + row['symbol'], 'confirmed cancellation contradicted')
        self._protection_emergency = True
        self._protection_final_cancel_proofs.pop(row['position_id'], None)
        self.store.audit(now, 'PROTECT_FINAL_CANCEL_CONTRADICTION', position_id=row['position_id'],
            intent_id=row['intent_id'], source=source)
        self._protection_page(now, 'P0', 'unknown', self.store.protection(row['position_id']),
            '已确认由本系统撤销的原 STP 又出现工作中或矛盾回报；冻结止盈与自动恢复，请核对券商原单')

    def _final_cancel_conflicting_orders(self, row, orders, started_at):
        fact = self._final_cancel_fact(row)
        if (not fact or not fact.get('confirmed_at') or
                started_at < datetime.fromisoformat(fact['confirmed_at'])):
            return False
        if any(order.intent_id == row['intent_id'] and order.status in
               {'Submitted', 'PreSubmitted', 'PendingSubmit', 'PendingCancel'} for order in orders):
            self._final_cancel_contradiction(self.clock.now(), row, 'complete own working-order query')
            return True
        return False

    def _final_cancel_fact(self, row):
        try:
            fact=json.loads(self.store.control('final_tp_cancel:'+row['position_id']) or '{}')
            if (not isinstance(fact, dict) or fact.get('binding')!={k:row[k] for k in _BIND} or
                    not isinstance(fact.get('workflow_id'), str) or not fact['workflow_id'] or
                    fact.get('stage') not in _TP or type(fact.get('target')) is not int or fact['target']<=0 or
                    type(fact.get('total_quantity')) is not int or fact['total_quantity']<fact['target'] or
                    type(fact.get('filled')) is not int or not 0<=fact['filled']<=fact['total_quantity'] or
                    'requested_at' not in fact or 'tp_intent_id' not in fact or
                    (fact['tp_intent_id'] is not None and not isinstance(fact['tp_intent_id'], str))):
                return None
            stamps = [datetime.fromisoformat(fact[k]) for k in
                      ('requested_at', 'confirmed_at', 'closed_at') if fact.get(k) is not None]
            if any(stamp.tzinfo is None or stamp > self.clock.now() for stamp in stamps):
                return None
            if any(a > b for a, b in zip(stamps, stamps[1:])):
                return None
            return fact
        except (TypeError,ValueError,KeyError):
            return None

    def _final_cancel_request(self, now, row):
        """Record our request before the physical cancel; no ACK authority."""
        workflow=self._tp_workflow(row)
        intent=self.store.intent(row['intent_id'])
        if row['tp_stage'] not in _TP or not row['tp_qty'] or intent is None:
            return
        fact={'binding':{k:row[k] for k in _BIND},'workflow_id':workflow.get('workflow_id'),
              'stage':row['tp_stage'],'target':int(row['tp_qty']),
              'requested_at':now.isoformat(),'request_socket':getattr(self.broker,'query_generation',None),
              'total_quantity':int(intent['quantity']),
              'filled':self.store.intent_filled_qty(row['intent_id']),
              'confirmed_at':None,'tp_intent_id':None}
        self.store.update_intent(now,row['intent_id'],cancel_requested_at=now)
        self.store.set_control(now,'final_tp_cancel:'+row['position_id'],json.dumps(fact,sort_keys=True))
        proof=self._protection_notice_evidence.get(row['position_id'])
        pos=self.store.position(row['position_id'])
        if (type(fact['request_socket']) is not int or not proof or proof.get('others') or len(proof.get('own',()))!=1 or pos is None or
                pos['status']!='OPEN' or row['state']!='PROTECT_CANCEL_PENDING' or
                intent['hold_reason'] or intent['status']==IntentStatus.UNKNOWN or
                self.store.anomalies() or self._protection_emergency or
                proof.get('generation')!=row['modify_generation'] or
                proof.get('socket_generation')!=fact['request_socket'] or
                not timedelta(0)<=now-proof['at']<=timedelta(seconds=60) or
                trading_day(proof['at'])!=trading_day(now) or
                not (proof.get('full_exec') or proof.get('overnight_tp_scope')) or
                proof['held']!=pos['qty_open'] or fact['target']!=proof['held'] or
                max(row['confirmed_remaining'],row['pending_max_remaining'])!=proof['held'] or
                not self._protection_order_valid(row,proof['own'][0]) or
                proof['own'][0].status not in {'Submitted','PreSubmitted'} or
                proof['own'][0].remaining!=proof['held'] or row['pending_qty'] is not None):
            return
        self._protection_final_cancel_proofs[row['position_id']]={
            'fact':fact,'proof':proof.copy(),'requested_at':now,
            'binding':self._protection_verification_binding(row),
            'fingerprint':self._protection_reduction_fingerprint(row)}

    def _final_cancel_wait(self, now, row):
        token=self._protection_final_cancel_proofs.get(row['position_id'])
        if token is None:
            return False
        deadline=min(token['requested_at']+timedelta(seconds=2),token['proof']['at']+timedelta(seconds=60))
        if row['state']=='PROTECT_CANCEL_PENDING' and token['requested_at']<=now<deadline:
            return True
        self._protection_final_cancel_proofs.pop(row['position_id'],None)
        return False

    def _final_cancel_invalidate(self, event):
        for pid,token in list(self._protection_final_cancel_proofs.items()):
            row=self.store.protection(pid)
            fact=token['fact']
            if row is None:
                self._protection_final_cancel_proofs.pop(pid,None);continue
            current=self.store.intent(row['intent_id'])
            if current is None or current['hold_reason'] or current['status']==IntentStatus.UNKNOWN:
                self._protection_final_cancel_proofs.pop(pid,None);continue
            if isinstance(event,StatusEvent) and event.query_snapshot:
                self._protection_final_cancel_proofs.pop(pid,None);continue
            if isinstance(event,OrderBookChangeEvent):
                expected=(row['intent_id'],row['broker_order_id'],row['perm_id'],row['client_id'],
                    row['account'],row['symbol'],row['con_id'],'STK','USD','SELL','STP','GTC',False,
                    fact['total_quantity'],row['trigger_price'],None)
                if (not event.connection_snapshot and not event.query_snapshot and
                        event.socket_generation==fact['request_socket'] and event.observed_at is not None and
                        token['requested_at']<event.observed_at and event.contract_echo==expected):
                    continue  # contract corroboration only, never a cancellation ACK
            elif isinstance(event,(StatusEvent,PriceObservationEvent)):
                identity=event.order_identity if isinstance(event,StatusEvent) else event.identity
                status=event.raw_status if isinstance(event,StatusEvent) else identity.status
                if (identity is not None and identity.intent_id==row['intent_id'] and
                        not event.connection_snapshot and not event.error_code and
                        status in {'Submitted','PreSubmitted','PendingCancel'}|_CANCELLED and
                        self._protection_order_valid(row,replace(identity,status='PreSubmitted')) and
                        identity.total_quantity==fact['total_quantity'] and
                        same_cent_price(identity.aux_price,row['trigger_price'])):
                    continue  # exact own pending/cancel event is validated after normal adoption
                if (isinstance(event,StatusEvent) and event.error_code==202 and identity is not None and
                        identity.intent_id==row['intent_id'] and event.raw_status in _CANCELLED and
                        self._protection_order_valid(row,replace(identity,status='PreSubmitted'))):
                    continue  # normal own-cancel notification, still needs the strict ACK gate
            self._protection_final_cancel_proofs.pop(pid,None)

    def _final_cancel_confirm(self, now, row, source):
        fact=self._final_cancel_fact(row)
        if fact is None:
            # A restarted pre-28d workflow can only arrive here after the
            # existing two complete absence rounds. It never gets a fast token.
            flow=self._overnight_flow(row)
            if not flow or not flow.get('original_cancel_requested'):
                return False
            intent=self.store.intent(row['intent_id'])
            fact={'binding':{k:row[k] for k in _BIND},'workflow_id':flow['workflow_id'],
                  'stage':row['tp_stage'],'target':row['tp_qty'],'requested_at':None,
                  'request_socket':None,'total_quantity':int(intent['quantity']),
                  'filled':self.store.intent_filled_qty(row['intent_id']),'tp_intent_id':None}
        fact.update(confirmed_at=now.isoformat(),confirmation_source=source,
                    confirmation_socket=getattr(self.broker,'query_generation',None))
        self.store.set_control(now,'final_tp_cancel:'+row['position_id'],json.dumps(fact,sort_keys=True))
        return True

    def _final_cancel_ack(self, now, row, event):
        token=self._protection_final_cancel_proofs.get(row['position_id'])
        if token is None or not self._final_cancel_wait(now,row):
            return False
        fact,proof,order=token['fact'],token['proof'],event.order_identity
        intent=self.store.intent(row['intent_id']);pos=self.store.position(row['position_id'])
        if (event.raw_status not in _CANCELLED or event.connection_snapshot or event.query_snapshot or
                event.observed_at is None or not token['requested_at']<event.observed_at<=now or
                event.socket_generation!=fact['request_socket'] or
                getattr(self.broker,'query_generation',None)!=fact['request_socket'] or
                event.error_code not in (0,202) or not event.identity_checked or
                intent is None or intent['hold_reason'] or order is None or
                not self._protection_order_valid(row,replace(order,status='PreSubmitted')) or order.status!=event.raw_status or
                event.broker_order_id!=row['broker_order_id'] or event.perm_id!=row['perm_id'] or
                order.total_quantity!=fact['total_quantity'] or
                order.filled!=fact['filled'] or event.filled!=fact['filled'] or
                self.store.intent_filled_qty(row['intent_id'])!=fact['filled'] or
                event.remaining not in (0,proof['held']) or order.remaining!=event.remaining or
                not same_cent_price(order.aux_price,row['trigger_price']) or
                self._protection_verification_binding(row)!=token['binding'] or
                self._protection_reduction_fingerprint(row)!=token['fingerprint'] or
                self._final_cancel_fact(row)!=fact or row['tp_intent_id'] or
                pos is None or pos['status']!='OPEN' or pos['qty_open']!=proof['held'] or
                fact['target']!=proof['held'] or self._tp_paused(row['position_id'],row['symbol'])):
            self._protection_final_cancel_proofs.pop(row['position_id'],None)
            return False
        self._protection_final_cancel_proofs.pop(row['position_id'],None)
        self._final_cancel_confirm(now,row,'bound native cancellation ACK')
        self.store.update_intent(now,row['intent_id'],status=IntentStatus.CANCELLED,
                                 note='confirmed GTC cancellation before final TP')
        self.store.update_protection(now,row['position_id'],requested_qty=0,confirmed_remaining=0,
            pending_max_remaining=0,pending_qty=None,state='WORKING',
            gap_b_started_at=row['gap_b_started_at'] or now)
        self._working.pop(row['intent_id'],None)
        current=self.store.protection(row['position_id'])
        projected=dict(proof,own=[],others=[],authority_binding=self._protection_verification_binding(current),
                       cancel_continuation=True,cancellation_ack_at=now)
        self._protection_notice_evidence[row['position_id']]=projected
        self._tp_record(now,current,'reduce_confirm')
        self.store.audit(now,'PROTECT_FINAL_CANCEL_CONTINUATION',position_id=row['position_id'],
            intent_id=row['intent_id'],proof_started_at=proof['started_at'].isoformat(),
            proof_completed_at=proof['at'].isoformat(),generation=row['modify_generation'])
        if self._overnight_flow(current) and not self._overnight_flow_authority(now,current,proof['held'],[],[]):
            return False
        self._protection_send_tp(now,current,reduction_proof=projected)
        return True

    def _final_cancel_bind_tp(self, now, row, intent_id):
        fact=self._final_cancel_fact(row)
        if (fact and fact.get('confirmed_at') and fact.get('stage')==row['tp_stage'] and
                fact.get('workflow_id')==self._tp_workflow(row).get('workflow_id') and
                fact.get('tp_intent_id') in (None,intent_id)):
            fact['tp_intent_id']=intent_id
            self.store.set_control(now,'final_tp_cancel:'+row['position_id'],json.dumps(fact,sort_keys=True))

    def _final_tp_close_fact(self, now, row):
        fact=self._final_cancel_fact(row);pos=self.store.position(row['position_id'])
        original=self.store.intent(row['intent_id'])
        tp=self.store.intent(row['tp_intent_id']) if row['tp_intent_id'] else None
        if (not fact or not fact.get('confirmed_at') or not fact.get('workflow_id') or
                fact.get('tp_intent_id')!=row['tp_intent_id'] or fact.get('stage')!=row['tp_stage'] or
                pos is None or pos['status']!='CLOSED' or pos['qty_open']!=0 or
                max(row['confirmed_remaining'],row['pending_max_remaining'])!=0 or
                original is None or original['status']!=IntentStatus.CANCELLED or
                original['last_raw_status'] not in _CANCELLED or original['hold_reason'] or
                original['cancel_requested_at']!=fact.get('requested_at') or
                self.store.intent_filled_qty(original['intent_id'])!=fact['filled'] or
                tp is None or tp['status']!=IntentStatus.FILLED or tp['hold_reason'] or
                tp['position_id']!=row['position_id'] or tp['purpose']!=fact['stage'] or
                tp['quantity']!=fact['target'] or self.store.intent_filled_qty(tp['intent_id'])!=fact['target'] or
                sum(f['quantity'] if f['side']=='BUY' else -f['quantity']
                    for f in self.store.position_fills(row['position_id']))!=0):
            return False
        fact.update(closed_at=now.isoformat(),economic_digest=_digest(self._protection_economic_fills()))
        self.store.set_control(now,'final_tp_cancel:'+row['position_id'],json.dumps(fact,sort_keys=True))
        return True

    def _protection_closed_by_final_tp(self, row):
        fact=self._final_cancel_fact(row);current=self.store.protection(row['position_id'])
        pos=self.store.position(row['position_id'])
        if (self._protection_emergency or self.store.anomalies() or not fact or not fact.get('closed_at') or
                not fact.get('confirmed_at') or current is None or current['state']!='CANCELLED' or
                current['confirmed_remaining']!=0 or current['pending_max_remaining']!=0 or
                current['pending_qty'] is not None or pos is None or pos['status']!='CLOSED' or pos['qty_open']!=0 or
                fact.get('economic_digest')!=_digest(self._protection_economic_fills())):
            return False
        original=self.store.intent(row['intent_id']);tp=self.store.intent(fact.get('tp_intent_id'))
        if (original is None or original['hold_reason'] or original['status']!=IntentStatus.CANCELLED or
                original['last_raw_status'] not in _CANCELLED or
                original['cancel_requested_at']!=fact.get('requested_at') or
                self.store.intent_filled_qty(original['intent_id'])!=fact['filled'] or
                tp is None or tp['hold_reason'] or tp['status']!=IntentStatus.FILLED or
                tp['purpose']!=fact['stage'] or tp['position_id']!=row['position_id'] or
                tp['quantity']!=fact['target'] or self.store.intent_filled_qty(tp['intent_id'])!=fact['target'] or
                (row['tp_intent_id'] and row['tp_intent_id']!=tp['intent_id'])):
            return False
        for order in self._working.values():
            if order.symbol not in (row['symbol'],'*') or order.side not in ('SELL',''):
                continue
            if not math.isfinite(order.remaining) or order.remaining<0:
                return False
            if (order.intent_id==tp['intent_id'] and order.ours and
                    self._tp_hard_identity(tp,order,prices=True) and self._protection_other_remaining(order)==0):
                continue
            if order.remaining>0:
                return False
        if any(i['side']=='SELL' and i['symbol']==row['symbol'] and
               i['intent_id'] not in (original['intent_id'],tp['intent_id']) for i in self.store.active_intents()):
            return False
        return sum(f['quantity'] if f['side']=='BUY' else -f['quantity']
                   for f in self.store.position_fills(row['position_id']))==0

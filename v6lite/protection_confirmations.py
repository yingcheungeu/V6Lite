"""Narrow confirmations of our pending STP writes; no blocking query here."""
from __future__ import annotations

import math
import json
from dataclasses import replace
from datetime import datetime, timedelta

from v6lite.market_calendar import trading_day
from v6lite.models import IntentStatus, Purpose
from v6lite.protection import same_cent_price

WORKING = {'Submitted', 'PreSubmitted'}


class ProtectionConfirmationMixin:
    def _protection_economic_fills(self):
        return tuple(tuple(f) for f in self.store.conn.execute(
            "SELECT exec_id,intent_id,position_id,symbol,side,quantity,price,broker_order_id,perm_id FROM fills ORDER BY rowid"))

    def _protection_own_tp_query_delta(self, row, before, before_fills, before_anomalies, held, orders, queried_fills):
        from v6lite.models import Side
        tp_id = row["tp_intent_id"]
        pos = self.store.position(row["position_id"])
        intent = self.store.intent(tp_id) if tp_id else None
        closed = bool(pos is not None and pos['status'] == 'CLOSED' and pos['qty_open'] == 0)
        if closed and not self._protection_closed_by_final_tp(row):
            return False
        if (self._protection_emergency or before is None or pos is None or intent is None or
                before["status"] != "OPEN" or (not closed and (pos["status"] != "OPEN" or pos["qty_open"] <= 0)) or
                intent["position_id"] != row["position_id"] or intent["symbol"] != row["symbol"] or
                intent["purpose"] not in {Purpose.TP1, Purpose.TP2, Purpose.TP3} or
                intent["side"] != Side.SELL or intent["hold_reason"] or
                not intent["broker_order_id"] or not intent["perm_id"]):
            return False
        if before_anomalies != tuple(tuple(a) for a in self.store.anomalies()):
            return False
        all_economics = self._protection_economic_fills()
        old = set(before_fills)
        if not old.issubset(set(all_economics)):
            return False  # old economic rows changed, rather than appended executions
        new_ids = {f[0] for f in all_economics if f not in old}
        records = self.store.position_fills(row["position_id"])
        by_id = {f["exec_id"]: f for f in records}
        delta = [by_id[f[0]] for f in all_economics if f[0] in new_ids and f[0] in by_id]
        if (not delta or len(delta) != len(new_ids) or
                any(f["intent_id"] != tp_id or f["side"] != "SELL" or
                type(f["quantity"]) is not int or f["quantity"] <= 0 or
                f["broker_order_id"] != intent["broker_order_id"] or
                f["perm_id"] != intent["perm_id"] for f in delta)):
            return False
        # Explicit contradictions already visible in this query must not
        # be hidden by the unrelated old position snapshot. Only known matching
        # current economic rows can accompany the own-TP supersession.
        if not self._protection_queried_fills_booked(row, queried_fills):
            return False
        sold = sum(f["quantity"] for f in delta)
        permitted_p = {before["qty_open"]}
        running_p = before["qty_open"]
        for fill in delta:
            running_p -= fill["quantity"]
            permitted_p.add(running_p)
        if (before["qty_open"] - pos["qty_open"] != sold or
                held not in permitted_p or
                sum(f["quantity"] if f["side"] == "BUY" else -f["quantity"] for f in records) != pos["qty_open"] or
                self.store.intent_filled_qty(tp_id) > intent["quantity"]):
            return False
        own_stops = [o for o in orders if o.intent_id == row["intent_id"]]
        if len(own_stops) != 1 or not self._protection_order_valid(row, own_stops[0]):
            original = self.store.intent(row["intent_id"])
            final_cancel = bool(not own_stops and row["state"] == "TP_ACTIVE" and
                (closed or row["tp_stage"] == Purpose.TP3) and original and not original["hold_reason"] and
                original["last_raw_status"] in {"Cancelled", "ApiCancelled"} and
                row["confirmed_remaining"] == row["pending_max_remaining"] == 0 and
                int(intent["quantity"])-self.store.intent_filled_qty(tp_id) == pos["qty_open"])
            if not final_cancel:
                return False
        for order in list(orders) + list(self._working.values()):
            if (order.symbol in (row["symbol"], "*") and order.side in ("SELL", "") and
                    (not math.isfinite(order.remaining) or order.remaining < 0)):
                return False
            if order.symbol not in (row["symbol"], "*") or order.side not in ("SELL", "") or order.remaining <= 0:
                continue
            if order.intent_id == row["intent_id"] and self._protection_order_valid(row, order):
                continue
            if (order.intent_id == tp_id and order.ours and
                    self._tp_hard_identity(intent, order, prices=True)):
                continue
            return False  # wildcard/foreign/second SELL cannot be excused by our TP
        return True

    def _protection_queried_fills_booked(self, row, queried_fills):
        for fill in queried_fills:
            if fill.symbol != row["symbol"]:
                continue
            booked = self.store.conn.execute("SELECT * FROM fills WHERE exec_id=?", (fill.exec_id,)).fetchone()
            if booked is None and fill.time.tzinfo is not None and not fill.time_uncertain and \
                    trading_day(fill.time) < trading_day(self.clock.now()):
                continue  # unscoped historical rows do not claim today's scope
            try:
                if (booked is None or fill.time.tzinfo is None or fill.time > self.clock.now() or
                        self._overnight_visible_economics(fill) != self._overnight_fill_economics(booked) or
                        (fill.intent_id and fill.intent_id != booked["intent_id"]) or
                        (fill.con_id is not None and fill.con_id != row["con_id"])):
                    return False
            except (ValueError, TypeError, KeyError, OverflowError):
                return False
        return True

    def _protection_own_stop_query_delta(self, row, before, before_fills, before_anomalies,
                                          held, orders, queried_fills):
        # Preserve the existing quiet stale-P discard for precisely our GTC
        # executions. A binding change alone never excuses a contradiction.
        pos = self.store.position(row['position_id'])
        intent = self.store.intent(row['intent_id'])
        if (self._protection_emergency or before is None or pos is None or intent is None or
                before['status'] != 'OPEN' or intent['purpose'] != Purpose.PROTECT or
                intent['hold_reason'] or intent['position_id'] != row['position_id'] or
                intent['symbol'] != row['symbol'] or intent['side'] != 'SELL' or
                not intent['broker_order_id'] or not intent['perm_id'] or
                before_anomalies != tuple(tuple(a) for a in self.store.anomalies())):
            return False
        all_economics = self._protection_economic_fills()
        old = set(before_fills)
        if not old.issubset(set(all_economics)):
            return False
        added = [f for f in all_economics if f not in old]
        if not added or any(f[1] != row['intent_id'] or f[2] != row['position_id'] or
                f[3] != row['symbol'] or f[4] != 'SELL' or type(f[5]) is not int or f[5] <= 0 or
                f[7] != intent['broker_order_id'] or f[8] != intent['perm_id'] for f in added):
            return False
        running = before['qty_open']
        prefix = {running}
        for fill in added:
            running -= fill[5]
            prefix.add(running)
        if (running != pos['qty_open'] or running < 0 or held not in prefix or
                self.store.intent_filled_qty(intent['intent_id']) > intent['quantity'] or
                sum(f['quantity'] if f['side'] == 'BUY' else -f['quantity']
                    for f in self.store.position_fills(row['position_id'])) != pos['qty_open'] or
                not self._protection_queried_fills_booked(row, queried_fills)):
            return False
        own = [o for o in orders if o.intent_id == row['intent_id']]
        if len(own) > 1 or (pos['qty_open'] > 0 and
                (len(own) != 1 or not self._protection_order_valid(row, own[0]))):
            return False
        for order in list(orders) + list(self._working.values()):
            if order.symbol not in (row['symbol'], '*') or order.side not in ('SELL', ''):
                continue
            if not math.isfinite(order.remaining) or order.remaining < 0:
                return False
            if order.remaining > 0 and (order.intent_id != row['intent_id'] or
                    not self._protection_order_valid(row, order)):
                return False
        return True

    def _protection_price_pending(self, row, pos):
        return bool(row is not None and pos is not None and pos['status'] == 'OPEN' and
            row['state'] == 'PRICE_PENDING' and row['pending_qty'] is not None and
            row['pending_qty'] == row['requested_qty'] and row['pending_limit'] is None and
            row['limit_price'] is None and row['tp_stage'] is None and
            row['confirmed_remaining'] == row['pending_max_remaining'] == pos['qty_open'] and
            row['pending_qty'] == pos['qty_open'] + self.store.intent_filled_qty(row['intent_id']))

    def _protection_waiting_price(self, now, row, stage):
        if row is None or row['state'] != 'PRICE_PENDING':
            return False
        key = f"protect_price_wait:{row['position_id']}:{trading_day(now).isoformat()}"
        if self.store.control(key) is None:
            self.store.set_control(now, key, now.isoformat())
            self.store.audit(now, 'PROTECT_PRICE_CONFIRMATION_WAIT', position_id=row['position_id'],
                symbol=row['symbol'], stage=str(stage), reason='等待保本修改确认')
        return True

    def _protection_price_request_valid(self, row, pos):
        if not self._overnight_position(row):
            return True
        # ACK of an already-issued request is a broker fact, not TP authority.
        # A provisional today's TP receipt must not require an unrelated fresh
        # account query inside a native callback. The exact durable request and
        # original historical prefix remain bound; a-d is re-run after ACK.
        records = [dict(f) for f in self.store.conn.execute(
            "SELECT * FROM fills WHERE position_id=? ORDER BY rowid", (row['position_id'],))]
        return self._overnight_price_confirmation(row, pos, pos['qty_open'], {'records': records}, request_only=True)

    def _protection_pending_price_order(self, row, pos, order):
        intent = self.store.intent(row['intent_id'])
        return bool(self._protection_price_pending(row, pos) and intent is not None and
            not intent['hold_reason'] and intent['status'] != IntentStatus.UNKNOWN and
            row['account'] == self.cfg.account and row['client_id'] == self.cfg.client_id and
            row['broker_order_id'] and row['perm_id'] and
            self._protection_order_valid(row, order) and order.status in WORKING and
            order.total_quantity == row['pending_qty'] and
            order.filled == self.store.intent_filled_qty(row['intent_id']) and
            order.remaining == pos['qty_open'])

    def _protection_old_price_wait(self, now, row):
        key = f"protect_price_requested:{row['position_id']}"
        try:
            saved = json.loads(self.store.control(key) or 'null')
            requested = datetime.fromisoformat(saved['at']) if saved and (
                saved['generation'] == row['modify_generation']) else datetime.fromisoformat(row['updated_at'])
            elapsed = (now-requested).total_seconds()
        except (ValueError, TypeError, KeyError):
            return  # an invalid clock cannot claim an overdue broker modification
        self._protection_waiting_price(now, row, Purpose.TP2)
        notice = f"protect_price_delayed:{row['position_id']}:{row['modify_generation']}"
        if elapsed >= 30 and self.store.control(notice) is None:
            from v6lite.alerts import AlertCard, P1_ORANGE
            detail = (f"{row['symbol']}：保护修改未生效，已等待 {elapsed:.1f} 秒；"
                f"券商仍以旧止损价 {row['trigger_price']:.2f} 保护 {row['confirmed_remaining']} 股，"
                f"待生效价 {row['pending_trigger']:.2f}；不撤改原 STP，继续查询。")
            self.alerter.send(now, 'P1', notice, detail,
                card=AlertCard('🟠 P1 · 保护修改未生效 · '+row['symbol'], P1_ORANGE,
                    (('发生了什么', detail), ('影响', '原止损继续保护；等待修改确认后再执行后续止盈。'))))
            self.store.set_control(now, notice, now.isoformat())
            self.store.audit(now, 'PROTECT_PRICE_MODIFY_DELAYED', position_id=row['position_id'],
                generation=row['modify_generation'], old_trigger=row['trigger_price'],
                pending_trigger=row['pending_trigger'], elapsed_seconds=elapsed)

    def _protection_commit_price(self, now, row, order, source):
        pending = int(row['pending_qty'])
        self.store.update_protection(now, row['position_id'], requested_qty=pending,
            confirmed_remaining=int(order.remaining), pending_max_remaining=int(order.remaining),
            pending_qty=None, pending_trigger=None, pending_limit=None,
            trigger_price=row['pending_trigger'], limit_price=None, state='WORKING',
            status_seen_generation=int(row['modify_generation']), confirmed_at=now)
        self.store.update_protection_intent_quantity(now, row['intent_id'], pending)
        self.store.delete_control(f"overnight_be_flow:{row['position_id']}")
        self._protection_price_requests.pop(row['position_id'], None)
        self.store.delete_control(f"protect_price_requested:{row['position_id']}")
        self.store.audit(now, 'PROTECT_PRICE_CONFIRMED', position_id=row['position_id'],
            intent_id=row['intent_id'], generation=int(row['modify_generation']), source=source,
            trigger_price=row['pending_trigger'], quantity=pending)
        self._protection_notice_evidence.pop(row['position_id'], None)
        self._protection_priority_due = True

    def _protection_price_ack_deferred(self, now, row, event, failed_checks):
        payload = dict(position_id=row['position_id'], intent_id=row['intent_id'],
            modify_generation=int(row['modify_generation']), source=event.source,
            failed_checks=failed_checks, status=event.identity.status,
            order_id=event.identity.broker_order_id, perm_id=event.identity.perm_id,
            con_id=event.identity.con_id, client_id=event.identity.client_id,
            account_matches=event.identity.account == self.cfg.account,
            total_quantity=event.identity.total_quantity, filled=event.identity.filled,
            remaining=event.identity.remaining, pending_qty=row['pending_qty'],
            aux_price=event.identity.aux_price, pending_trigger=row['pending_trigger'])
        generation = getattr(self.broker, 'query_generation', None)
        key = f"protect_price_ack_deferred:{row['position_id']}:{row['modify_generation']}:{generation}"
        signature = json.dumps(payload, sort_keys=True)
        if self.store.control(key) == signature:
            return
        self.store.audit(now, 'PROTECT_PRICE_ACK_DEFERRED', generation=generation, **payload)
        self.store.set_control(now, key, signature)

    def _protection_on_open_price(self, now, event):
        # Individual query rows never confirm. Only our complete normalized
        # result, emitted after its matching native End, is a snapshot ACK.
        own_snapshot = bool(event.source == 'openOrders' and event.query_snapshot and
            event.owned_open_orders is not None and event.query_request_id is not None and
            event.query_request_id > 0 and event.query_started_at is not None and
            event.query_started_at.tzinfo is not None)
        if own_snapshot and self._shutting_down:
            return  # shutdown inventory is left for restart's full takeover
        live = event.source == 'openOrder' and not event.query_snapshot
        if (not (live or own_snapshot) or event.connection_snapshot or
                event.observed_at is None or event.observed_at.tzinfo is None or
                event.socket_generation != getattr(self.broker, 'query_generation', None)):
            return
        order = event.identity
        row = self.store.protection_for_intent(order.intent_id)
        pos = self.store.position(row['position_id']) if row else None
        if not self._protection_price_pending(row, pos):
            if row is not None and row['state'] == 'PRICE_PENDING':
                self._protection_price_ack_deferred(now, row, event, ['pending_shape'])
            return
        intent = self.store.intent(row['intent_id'])
        issued = self._protection_price_requests.get(row['position_id'])
        if issued is None or issued[0] != int(row['modify_generation']):
            self._protection_price_ack_deferred(now, row, event, ['restart_or_request_generation'])
            return  # a restart must first complete its own three-source round
        requested = issued[1]
        current_snapshot = getattr(self.broker, 'open_orders_snapshot_current', None)
        if own_snapshot and (current_snapshot is None or not current_snapshot(
                event.socket_generation, event.query_request_id)):
            self._protection_price_ack_deferred(now, row, event, ['superseded_or_incomplete_owned_round'])
            return
        if own_snapshot and (not requested < event.query_started_at <= event.observed_at <= now or
                now-event.observed_at > timedelta(seconds=5) or
                sum(o.intent_id == row['intent_id'] for o in event.owned_open_orders) != 1 or
                order not in event.owned_open_orders):
            self._protection_price_ack_deferred(now, row, event, ['owned_snapshot_request_binding_or_unique_order'])
            return
        checks = {
            'observation_after_request': requested < event.observed_at <= now,
            'intent_usable': intent is not None and not intent['hold_reason'] and intent['status'] != IntentStatus.UNKNOWN,
            'working_pending_contract_and_quantity': self._protection_pending_price_order(row, pos, order),
            'pending_aux': same_cent_price(order.aux_price, row['pending_trigger'])}
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            self._protection_price_ack_deferred(now, row, event, failed)
            return
        # This records the broker's acknowledgement only. No hold/freeze is
        # released and the changed binding requires new TP authority.
        if not self._protection_price_request_valid(row, pos):
            self._protection_price_ack_deferred(now, row, event, ['durable_request_binding_or_history_prefix'])
            return
        self._protection_commit_price(now, row, order,
            'own complete openOrders snapshot' if own_snapshot else 'live openOrder')

    def _protection_reduction_fingerprint(self, row):
        # Economic rows and every account SELL/hold are bound to this attempt.
        # last_raw_status/updated_at of the very STP being modified are allowed
        # to change as its native PendingSubmit -> PreSubmitted arrives.
        fills = tuple(tuple(r) for r in self.store.conn.execute(
            'SELECT exec_id,intent_id,position_id,symbol,side,quantity,price,broker_order_id,perm_id,time FROM fills ORDER BY rowid'))
        intents = tuple((r['intent_id'], r['quantity'], r['hold_reason'],
                         None if r['intent_id'] == row['intent_id'] else r['status'])
                        for r in self.store.conn.execute('SELECT * FROM intents ORDER BY intent_id'))
        pos = self.store.position(row['position_id'])
        return (fills, intents, pos['qty_open'] if pos else None,
                tuple(sorted(self._working.items())), self._working_at,
                self._fresh_held.get(row['symbol']),
                tuple(sorted((k, v) for k, v in self.store.controls_with_prefix('').items()
                    if k.startswith(('frozen:', 'tp_pause:', 'price_reissue_block:',
                        'protect_evidence_failure:', 'overnight_tp_flow:')))),
                bool(self.store.anomalies()), self._protection_emergency,
                self.flags.broker_connected, getattr(self.broker, 'query_generation', None))

    def _protection_arm_reduction(self, now, row, original_proof):
        pid = row['position_id']
        self._protection_reduction_proofs.pop(pid, None)
        if (not self._overnight_flow(row) or row['state'] != 'REDUCE_PENDING' or
                row['pending_qty'] is None or row['tp_intent_id'] or
                row['pending_qty'] >= row['requested_qty'] or
                not original_proof or original_proof.get('others') or
                len(original_proof.get('own', ())) != 1 or
                not original_proof.get('overnight_tp_scope') or
                original_proof.get('generation') != int(row['modify_generation'])-1 or
                original_proof.get('socket_generation') != getattr(self.broker, 'query_generation', None) or
                not timedelta(0) <= now-original_proof['at'] <= timedelta(seconds=60) or
                not same_cent_price(row['pending_trigger'], row['trigger_price'])):
            return
        self._protection_reduction_proofs[pid] = {
            'binding': self._protection_verification_binding(row),
            'proof': original_proof.copy(),
            'requested_at': now,
            'fingerprint': self._protection_reduction_fingerprint(row)}

    def _protection_invalidate_reduction(self, event):
        from v6lite.models import StatusEvent, PriceObservationEvent, OrderBookChangeEvent
        for pid, token in list(self._protection_reduction_proofs.items()):
            row = self.store.protection(pid)
            if row is not None and isinstance(event, OrderBookChangeEvent):
                expected = (row['intent_id'], row['broker_order_id'], row['perm_id'],
                    row['client_id'], row['account'], row['symbol'], row['con_id'],
                    'STK', 'USD', 'SELL', 'STP', 'GTC', False, row['pending_qty'], row['pending_trigger'], None)
                if (not event.connection_snapshot and not event.query_snapshot and
                        event.socket_generation == getattr(self.broker, 'query_generation', None) and
                        event.observed_at is not None and token['requested_at'] < event.observed_at and
                        event.contract_echo == expected):
                    if not self._protection_wait_reduction_pair(self.clock.now(), row):
                        continue  # an overdue native callback cannot revive this attempt
                    token['native_echo'] = event
                    token.setdefault('first_component_at', event.observed_at)
                    status = token.get('ack_status')
                    if status is not None:
                        self._protection_reduce_ack(self.clock.now(), row, status)
                    continue  # native contract echo only; counts still require working ACK
                self._protection_reduction_proofs.pop(pid, None)
                continue
            if (row is not None and isinstance(event, StatusEvent) and
                    event.intent_id == row['intent_id'] and event.raw_status == 'PendingSubmit' and
                    event.broker_order_id == row['broker_order_id'] and event.perm_id == row['perm_id'] and
                    not event.connection_snapshot):
                continue  # not authority; wait for the exact native working ACK
            identity = (event.order_identity if isinstance(event, StatusEvent) else
                        event.identity if isinstance(event, PriceObservationEvent) else None)
            status = event.raw_status if isinstance(event, StatusEvent) else identity.status if identity else ''
            if (row is None or identity is None or identity.intent_id != row['intent_id'] or
                    status not in WORKING | {'PendingSubmit'} or
                    getattr(event, 'connection_snapshot', False) or
                    not self._protection_order_valid(row, replace(identity, status='PreSubmitted'))):
                self._protection_reduction_proofs.pop(pid, None)

    def _protection_wait_reduction_pair(self, now, row):
        token = self._protection_reduction_proofs.get(row['position_id'])
        if token is None or row['state'] != 'REDUCE_PENDING':
            return False
        # Collect a genuine pair without starting a query over its second
        # component. Duplicates never extend the first-component deadline.
        requested = token['requested_at']
        first = token.get('first_component_at', requested)
        deadline = min(first + timedelta(seconds=1), requested + timedelta(seconds=2),
                       token['proof']['at'] + timedelta(seconds=60))
        if requested <= now < deadline:
            return True
        self._protection_reduction_proofs.pop(row['position_id'], None)
        return False

    def _protection_reduce_ack(self, now, row, event):
        pid = row['position_id']
        token = self._protection_reduction_proofs.get(pid)
        order = event.order_identity
        if token is None or order is None:
            return False
        if not self._protection_wait_reduction_pair(now, row):
            return False  # check the old deadline before accepting any component
        if (event.connection_snapshot or event.observed_at is None or
                event.socket_generation != getattr(self.broker, 'query_generation', None) or
                not token['requested_at'] < event.observed_at <= now or event.raw_status not in WORKING or
                event.filled != self.store.intent_filled_qty(row['intent_id']) or
                event.remaining != int(row['pending_qty'])-event.filled):
            self._protection_reduction_proofs.pop(pid, None)
            return False
        token['ack_status'] = event
        token.setdefault('first_component_at', event.observed_at)
        proof = token['proof']
        echo = token.get('native_echo')
        if echo is None:
            return True  # a non-authoritative pair is waiting, no broker write
        # orderStatus has no totalQuantity. Only the immutable raw openOrder
        # echo establishes that field, in either native arrival order.
        order = replace(order, total_quantity=int(row['pending_qty']))
        if (event.connection_snapshot or row['state'] != 'REDUCE_PENDING' or
                echo is None or event.observed_at is None or
                event.socket_generation != getattr(self.broker, 'query_generation', None) or
                not token['requested_at'] < echo.observed_at <= now or
                event.raw_status not in WORKING or row['tp_intent_id'] or
                token['binding'] != self._protection_verification_binding(row) or
                token['fingerprint'] != self._protection_reduction_fingerprint(row) or
                proof['socket_generation'] != getattr(self.broker, 'query_generation', None) or
                trading_day(proof['at']) != trading_day(now) or
                not timedelta(0) <= now-proof['at'] <= timedelta(seconds=60) or
                not self._protection_order_valid(row, order) or
                order.total_quantity != row['pending_qty'] or
                order.filled != self.store.intent_filled_qty(row['intent_id']) or
                order.remaining != row['pending_qty']-order.filled or
                event.remaining != order.remaining or event.filled != order.filled or
                not same_cent_price(order.aux_price, row['pending_trigger']) or
                order.remaining + int(row['tp_qty']) != proof['held'] or
                proof['held'] != self.store.position(pid)['qty_open'] or
                self._tp_paused(pid, row['symbol'])):
            self._protection_reduction_proofs.pop(pid, None)
            return False
        self._protection_reduction_proofs.pop(pid, None)
        pending = int(row['pending_qty'])
        self.store.update_protection(now, pid, state='WORKING', requested_qty=pending,
            confirmed_remaining=int(order.remaining), pending_max_remaining=int(order.remaining),
            pending_qty=None, pending_trigger=None, pending_limit=None,
            status_seen_generation=int(row['modify_generation']), confirmed_at=now,
            gap_b_started_at=row['gap_b_started_at'] or now)
        self.store.update_protection_intent_quantity(now, row['intent_id'], pending)
        current = self.store.protection(pid)
        # Apply exactly our own ACK to the old complete round. Keep original
        # per-source clocks/scope; this is a one-use reduction continuation,
        # not a newly downloaded full snapshot or restart authority.
        projected = dict(proof, generation=int(current['modify_generation']), own=[order],
            authority_binding=self._protection_verification_binding(current),
            reduction_ack_at=now, reduction_continuation=True)
        self._protection_notice_evidence[pid] = projected
        self._working[order.intent_id] = order
        self._tp_record(now, current, 'reduce_confirm')
        self.store.audit(now, 'PROTECT_REDUCTION_CONTINUATION', position_id=pid,
            intent_id=row['intent_id'], proof_started_at=proof['started_at'].isoformat(),
            proof_completed_at=proof['at'].isoformat(), generation=int(current['modify_generation']))
        if not self._overnight_flow_authority(now, current, proof['held'], [order], []):
            return False
        self._protection_send_tp(now, current, reduction_proof=projected)
        return True

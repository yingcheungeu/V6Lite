"""TP evidence, scoped pauses and price diagnostics; never broker authority."""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta

from v6lite.alerts import AlertCard, P0_RED, P1_ORANGE
from v6lite.exits import sell_limit
from v6lite.market_calendar import trading_day
from v6lite.models import IntentStatus, Purpose
from v6lite.store import StoreError, new_id
from v6lite.protection import same_cent_price

log = logging.getLogger("v6lite.engine")
TP_PURPOSES = {Purpose.TP1, Purpose.TP2, Purpose.TP3}
TERMINAL = {"Cancelled", "ApiCancelled", "Inactive"}


def price_rejection(code, reason):
    """Price refusal needs an affirmative constraint, never a generic reject."""
    if not isinstance(reason, str):
        return False
    text = reason.lower()
    if any(phrase in text for phrase in ("unrelated to price", "not due to price", "not a price",
                                         "与价格无关", "非价格原因", "不是价格")):
        return False
    english = bool(re.search(
        r"(?:price|limit).*(?:outside|out of|not within|too far|exceed|beyond|constraint|minimum.*variation)|"
        r"(?:invalid|incorrect|unacceptable)\s+(?:limit\s+)?price|"
        r"(?:price|limit).*(?:range|band).*(?:violat|fail|reject)|"
        r"(?:outside|exceed|beyond).*(?:price range|price band|price constraint)", text))
    chinese = bool(re.search(r"价格.*(?:超出|偏离|不符合|无效|不合法|范围限制|限制范围)|"
                             r"(?:无效|不合法|错误).*价格|"
                             r"价格不在.*允许范围内|限价偏离.*太多", text))
    return english or chinese


def valid_resume_binding(request, pause, pos, row, pid):
    """Shared typed episode binding; this alone grants no broker permission."""
    if not isinstance(request, dict) or not isinstance(pause, dict) or pos is None or row is None:
        return False
    fields = ("position_id", "symbol", "pause_id")
    if any(not isinstance(value.get(field), str) or not value[field].strip()
           for value in (request, pause) for field in fields):
        return False
    return bool(isinstance(pid, str) and pid.strip() and
        all(request[field] == pause[field] for field in fields) and
        request["position_id"] == pid == pos["position_id"] == row["position_id"] and
        request["symbol"] == pos["symbol"] == row["symbol"] and
        request.get("operator") == "John" and isinstance(request.get("reason"), str) and request["reason"].strip())


def chunks(reason):
    result, chars, size = [], [], 0
    for char in reason:
        width = 2 if ord(char) > 0xFFFF else 1
        if size + width > 900:
            result.append("".join(chars)); chars, size = [], 0
        chars.append(char); size += width
    result.append("".join(chars))
    return result


from v6lite.test_tp_timeout import TestTpTimeoutMixin


class TpEvidenceMixin(TestTpTimeoutMixin):
    def _tp_json(self, key):
        raw = self.store.control(key)
        if raw is None:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid TP evidence object")
        return value

    def _tp_paused(self, pid, symbol):
        # Presence (including corrupt JSON) is fail closed.
        return (self.store.control(f"tp_pause:{pid}") is not None or
                self.store.control(f"price_reissue_block:{symbol}") is not None)

    def _tp_context(self, intent):
        """An ordinary TP has diagnostic context, never a synthetic STP row."""
        row = self.store.protection(intent["position_id"])
        if row is not None:
            return row
        return {"position_id": intent["position_id"], "symbol": intent["symbol"],
                "intent_id": None, "tp_intent_id": intent["intent_id"],
                "tp_stage": intent["purpose"], "tp_qty": intent["quantity"],
                "gap_a_started_at": None, "gap_b_started_at": None,
                "confirmed_remaining": None, "pending_max_remaining": None,
                "diagnostic_only": True}

    def _tp_workflow(self, row):
        try:
            return self._tp_json(f"tp_workflow:{row['position_id']}") or {}
        except (ValueError, TypeError):
            return {}

    def _tp_start_workflow(self, now, row, stage):
        value = {"workflow_id": new_id("tp-flow"), "position_id": row["position_id"],
                 "symbol": row["symbol"], "stage": str(stage), "started_at": now.isoformat(),
                 "intent_id": None}
        self.store.set_control(now, f"tp_workflow:{row['position_id']}", json.dumps(value))
        return value

    def _tp_record(self, now, row, phase, *, intent_id=None, quote=None, source="engine", persist=True,
                   pricing_snapshot=None, **extra):
        if self.dry_run:
            return
        workflow = self._tp_workflow(row)
        ref = intent_id or row["tp_intent_id"] or workflow.get("intent_id")
        if ref:
            try:
                linked = self._tp_json(f"tp_send:{ref}")
            except (ValueError, TypeError):
                linked = None
            if linked:
                workflow = linked
        pos = self.store.position(row["position_id"])
        q = (quote if quote is not None else self.broker.quote(row["symbol"])) if pricing_snapshot is None else None
        try:
            age = (now - q.ts).total_seconds() if q is not None and q.ts and q.ts.tzinfo else None
        except (TypeError, ValueError):
            age = None
        def finite(value):
            return value if isinstance(value, (int, float)) and math.isfinite(value) else None
        t = sum(max(0, int(i["quantity"]) - self.store.intent_filled_qty(i["intent_id"]))
                for i in self.store.active_intents_for_position(row["position_id"])
                if i["side"] == "SELL" and i["purpose"] != Purpose.PROTECT)
        x = sum(self._protection_other_remaining(o) for o in self._working.values()
                if not o.ours and o.symbol in {row["symbol"], "*"})
        evidence = self._protection_notice_evidence.get(row["position_id"])
        payload = {"workflow_id": workflow.get("workflow_id"), "position_id": row["position_id"],
            "symbol": row["symbol"], "stage": row["tp_stage"] or workflow.get("stage"),
            "intent_id": ref, "phase": phase, "source": source, "receive_time": now.isoformat(),
            "quantity": row["tp_qty"] if row["tp_qty"] is not None else workflow.get("quantity"), "bid": finite(q.bid) if q else None,
            "ask": finite(q.ask) if q else None, "quote_age_seconds": age,
            "bid_raw": str(q.bid) if q else "missing", "ask_raw": str(q.ask) if q else "missing",
            "quote_time": q.ts.isoformat() if q and q.ts else None,
            "market_data_type": getattr(q, "market_data_type", None) if q else None,
            "configured_market_data_type": self.cfg.market_data_type,
            "calculated_price": workflow.get("calculated_price"), "sent_price": workflow.get("sent_price"),
            "working_price": None, "aux_price": None, "mkt_cap_price": None,
            "error_code": 0, "reason": "", "gap_a_started_at": row["gap_a_started_at"],
            "gap_b_started_at": row["gap_b_started_at"],
            "P": pos["qty_open"] if pos is not None and pos["status"] == "OPEN" else 0,
            "S": (max(row["confirmed_remaining"], row["pending_max_remaining"])
                  if row["confirmed_remaining"] is not None and row["pending_max_remaining"] is not None else None), "T": t, "X": x,
            "three_source_complete": bool(evidence and evidence["full_exec"]),
            "overnight_tp_scope": evidence.get("overnight_tp_scope") if evidence else None,
            "proof_started_at": evidence["started_at"].isoformat() if evidence else None,
            "proof_at": evidence["at"].isoformat() if evidence else None}
        if pricing_snapshot is not None:
            payload.update({field: pricing_snapshot.get(field) for field in
                ("bid", "ask", "bid_raw", "ask_raw", "quote_time", "market_data_type", "configured_market_data_type")})
            try:
                payload["quote_age_seconds"] = (now - datetime.fromisoformat(pricing_snapshot["quote_time"])).total_seconds()
            except (ValueError, TypeError, KeyError):
                payload["quote_age_seconds"] = None
        payload.update(extra)
        if persist:
            self.store.audit(now, "TP_EVIDENCE", **payload)
            self._test_timeout_event(now, row, phase, ref)
        return payload

    def _tp_quote_ok(self, now, quote):
        try:
            if quote is None or not quote.ts or quote.ts.tzinfo is None:
                return False
            if any(not isinstance(value, (int, float)) or isinstance(value, bool) or
                   not math.isfinite(value) or value <= 0 for value in (quote.bid, quote.ask)):
                return False
            if quote.ask < quote.bid:
                return False
            return 0 <= (now - quote.ts).total_seconds() <= self.cfg.tp_max_quote_age_seconds
        except (ValueError, TypeError, OverflowError):
            return False

    def _tp_pricing_snapshot(self, now, quote, price):
        return {"bid": quote.bid, "ask": quote.ask, "bid_raw": str(quote.bid), "ask_raw": str(quote.ask),
                "quote_time": quote.ts.isoformat(), "quote_age_seconds": (now - quote.ts).total_seconds(),
                "pricing_at": now.isoformat(),
                "market_data_type": getattr(quote, "market_data_type", None),
                "configured_market_data_type": self.cfg.market_data_type,
                "calculated_price": price, "sent_price": price}

    def _tp_pricing_ok(self, now, snapshot, req):
        """Recheck the exact priced book, never a newer quote or automatic reprice."""
        if not isinstance(snapshot, dict):
            return False
        try:
            bid, ask = snapshot["bid"], snapshot["ask"]
            if any(not isinstance(value, (int, float)) or isinstance(value, bool) or
                   not math.isfinite(value) or value <= 0 for value in (bid, ask)) or ask < bid:
                return False
            # Numeric book, its original raw values and the pricing clock must
            # be one coherent snapshot. Never repair damaged evidence by repricing.
            if any(not isinstance(snapshot.get(field), str) for field in ("bid_raw", "ask_raw")):
                return False
            if float(snapshot["bid_raw"]) != bid or float(snapshot["ask_raw"]) != ask:
                return False
            at = datetime.fromisoformat(snapshot["quote_time"])
            priced_at = datetime.fromisoformat(snapshot.get("pricing_at") or snapshot["receive_time"])
            recorded_age = snapshot["quote_age_seconds"]
            if (at.tzinfo is None or priced_at.tzinfo is None or
                    not isinstance(recorded_age, (int, float)) or isinstance(recorded_age, bool) or
                    not math.isfinite(recorded_age) or
                    not math.isclose(recorded_age, (priced_at - at).total_seconds(), abs_tol=1e-6) or
                    not 0 <= recorded_age <= self.cfg.tp_max_quote_age_seconds or not priced_at <= now or
                    snapshot.get("configured_market_data_type") != self.cfg.market_data_type):
                return False
            expected = (self._test_timeout_expected(snapshot, req)
                        if snapshot.get("test_timeout_request_id") else
                        sell_limit(bid, self.cfg.exit_limit_band_pct))
            return bool(0 <= (now - at).total_seconds() <= self.cfg.tp_max_quote_age_seconds and
                        expected is not None and
                        snapshot["sent_price"] == snapshot["calculated_price"] == req.limit_price == expected)
        except (ValueError, TypeError, KeyError, OverflowError):
            return False

    def _tp_definitely_unsent(self, req, *, atr, count_entry_day, reason):
        if self.dry_run:
            return "refused:dry_run"
        now = self.clock.now()
        if self.store.intent(req.intent_id) is None:
            self.store.create_intent(now, req, atr=atr, count_entry_day=count_entry_day)
        intent = self.store.intent(req.intent_id)
        if intent["status"] != IntentStatus.NEW or intent["hold_reason"]:
            # Only this known pre-submit NEW request is definitely unsent.
            # Existing uncertainty/hold remains reserved and cannot be released.
            self.store.audit(now, "TP_SUBMIT_GATE_BLOCKED", intent_id=req.intent_id, reason=reason,
                             preserved_status=intent["status"], hold_reason=intent["hold_reason"])
            return "refused:tp_existing_uncertainty"
        self.store.update_intent(now, req.intent_id, status=IntentStatus.REJECTED, note=reason)
        self.store.audit(now, "TP_DEFINITELY_UNSENT", intent_id=req.intent_id, position_id=req.position_id,
                         symbol=req.symbol, reason=reason, limit_price=req.limit_price)
        self._tp_record(now, self._tp_context(self.store.intent(req.intent_id)), "status",
                        intent_id=req.intent_id, source="pre_submit_gate", reason=reason,
                        raw_status="DefinitelyUnsent", sent_price=req.limit_price)
        return "refused:tp_definitely_unsent"

    def _tp_skip_quote(self, now, row, quote):
        if self.dry_run:
            return
        key = f"tp_quote_skip:{trading_day(now).isoformat()}:{row['position_id']}"
        previous = self._tp_json(key) or {"count": 0}
        previous["count"] += 1
        with self.store.tx():
            self.store.set_control(now, key, json.dumps(previous))
            self._tp_record(now, row, "quote_skip", quote=quote, skip_count=previous["count"])
        logs = getattr(self, "_tp_skip_log_at", {})
        prior = logs.get(row["position_id"])
        if prior is None or now - prior >= timedelta(minutes=1):
            log.info("[TP_QUOTE_SKIP] position=%s bid/ask 缺失、无效或超过 %ss；本轮保留完整保护",
                     row["position_id"], self.cfg.tp_max_quote_age_seconds)
            logs[row["position_id"]] = now
        self._tp_skip_log_at = logs

    def _tp_hard_identity(self, intent, identity, *, prices=False):
        row = self.store.protection(intent["position_id"]) if intent["position_id"] else None
        con_id = row["con_id"] if row is not None else int(self.store.control(f"conid:{intent['symbol']}") or 0)
        return bool(identity is not None and identity.ours and identity.intent_id == intent["intent_id"] and
            identity.symbol == intent["symbol"] and identity.account == self.cfg.account and
            identity.client_id == self.cfg.client_id and identity.con_id == con_id and
            intent["broker_order_id"] in (None, identity.broker_order_id) and
            intent["perm_id"] in (None, identity.perm_id) and
            identity.broker_order_id and identity.perm_id and identity.side == intent["side"] and
            identity.order_type == "LMT" and identity.tif == "DAY" and identity.outside_rth is False and
            identity.total_quantity == intent["quantity"] and
            math.isfinite(identity.filled) and math.isfinite(identity.remaining) and
            float(identity.filled).is_integer() and float(identity.remaining).is_integer() and
            min(identity.filled, identity.remaining) >= 0 and
            identity.filled + identity.remaining <= identity.total_quantity and
            (not prices or identity.limit_price == intent["limit_price"]))

    def _tp_validate_status(self, now, event, intent):
        if event.identity_checked or event.order_identity is not None or event.terminal_identity is not None:
            identity = event.order_identity
            valid = bool(self._tp_hard_identity(intent, identity, prices=event.raw_status in TERMINAL) and
                         identity.status == event.raw_status and
                         identity.broker_order_id == event.broker_order_id and identity.perm_id == event.perm_id and
                         identity.filled == event.filled and identity.remaining == event.remaining)
        else:
            # Legacy bare status remains a hint with exact persisted IDs.
            valid = bool(intent["broker_order_id"] and intent["perm_id"] and
                         intent["broker_order_id"] == event.broker_order_id and intent["perm_id"] == event.perm_id)
        if not valid:
            if event.raw_status in {"PendingSubmit", "ApiPending"} and event.order_identity is None:
                return False  # a local initial permId=0 is not a terminal authority or permanent hold
            self.store.hold_intent(now, intent["intent_id"], "TP native status identity mismatch; manual verification required")
            self._protection_emergency = True
            return False
        if not intent["hold_reason"] and event.order_identity is not None and (
                intent["broker_order_id"] is None or intent["perm_id"] is None):
            self.store.update_intent(now, intent["intent_id"], broker_order_id=event.broker_order_id,
                                     perm_id=event.perm_id)
        return not intent["hold_reason"]

    def _tp_price_rejected(self, now, intent, code, reason):
        pos = self.store.position(intent["position_id"])
        if (intent["purpose"] not in TP_PURPOSES or pos is None or pos["status"] != "OPEN" or
                pos["symbol"] != intent["symbol"]):
            return  # old/closed positions cannot pause a later position or stage
        row = self._tp_context(intent)
        if row["tp_intent_id"] != intent["intent_id"]:
            return
        stage = self.store.stage(intent["position_id"], Purpose(intent["purpose"]))
        if stage is None or stage["status"] not in {"PENDING", "ACTIVE"}:
            return
        key = f"tp_rejection:{intent['intent_id']}"
        rejection = self._tp_json(key)
        if rejection is None:
            send = self._tp_json(f"tp_send:{intent['intent_id']}") or {}
            rejection = {**send, "pause_id": new_id("tp-pause"), "position_id": row["position_id"],
                "symbol": row["symbol"], "intent_id": intent["intent_id"], "stage": intent["purpose"],
                "at": now.isoformat(), "error_code": code, "reason": reason, "quantity": intent["quantity"],
                "order_id": intent["broker_order_id"], "perm_id": intent["perm_id"],
                "sent_price": intent["limit_price"], "automatic_protection_enabled": self.cfg.protection.enabled,
                **self._tp_rejection_quantities(now, row)}
            with self.store.tx():
                self.store.set_control(now, key, json.dumps(rejection))
                self.store.set_control(now, f"tp_pause:{row['position_id']}", json.dumps(rejection))
                self.store.audit(now, "TP_AUTO_PAUSED", **rejection)
        self._tp_rejection_page(now, row, rejection)

    def _tp_rejection_quantities(self, now, row):
        pos = self.store.position(row["position_id"])
        held = int(pos["qty_open"]) if pos is not None and pos["status"] == "OPEN" else None
        proof = self._protection_notice_evidence.get(row["position_id"])
        confirmed = None
        if (proof and proof["intent_id"] == row["intent_id"] and
                proof.get("generation") == int(row["modify_generation"]) and proof["full_exec"] and
                proof.get("row_context") == tuple(row[name] for name in
                    ("intent_id", "modify_generation", "state", "updated_at")) and
                proof.get("socket_generation") == getattr(self.broker, "query_generation", None) and
                timedelta(0) <= now - proof["at"] <= timedelta(seconds=5) and proof["held"] == held):
            own = proof["own"]
            intent = self.store.intent(row["intent_id"])
            if (len(own) <= 1 and intent is not None and not intent["hold_reason"] and
                    all(self._protection_order_valid(row, order) and
                        self._protection_native_quantity_valid(row, intent, order) for order in own)):
                confirmed = sum(int(order.remaining) for order in own)
        return {"held_quantity": held, "protected_quantity": confirmed,
                "unprotected_quantity": max(0, held - confirmed)
                    if held is not None and confirmed is not None else None}

    def _tp_rejection_page(self, now, row, record):
        reason = record["reason"]
        protected = bool(record.get("automatic_protection_enabled", self.cfg.protection.enabled))
        protection_text = ("保护恢复仍需完整三项证据。" if protected else
                           "未启用自动 STP；券商保护须人工核验，本诊断不会启用 STP。")
        def quantity(name):
            value = record.get(name)
            return f"{value} 股" if value is not None else "待核实"
        counts = (f"持仓 P={quantity('held_quantity')} 已保护 S={quantity('protected_quantity')} "
                  f"未保护={quantity('unprotected_quantity')}；")
        body = (counts + f"{record['symbol']} TP数量={record['quantity']} 股；发送限价={record['sent_price']:.2f}；"
                f"bid={record.get('bid')} ask={record.get('ask')} 行情年龄={record.get('quote_age_seconds')}s；"
                f"orderRef={record['intent_id']} orderId={record.get('order_id')} permId={record.get('perm_id')} "
                f"broker code={record['error_code']}；自动止盈暂停 position={record['position_id']} "
                f"stage={record['stage']} pause_id={record['pause_id']}；{protection_text}")
        parts = chunks(reason)
        cards = []
        for n, part in enumerate(parts, 1):
            fields = (("发生了什么", body), (f"券商原因原文 {n}/{len(parts)}", part))
            if n == 1:
                verification = ("2）确认 STP 已恢复到全部持仓；" if protected else
                                "2）由 John 在 TWS 人工核对保护，或按 bid 用限价单平仓，避免重复卖单；")
                fields += (("影响", "本持仓自动止盈已暂停；券商保护与可用卖出数量仍须核验。"),
                    ("你要做什么", "1）核对 TWS 持仓和工作卖单；" + verification +
                     f"3）仅在原强核验门槛满足后由 John 执行 resume-tp {record['symbol']} --position-id {record['position_id']} "
                     f"--pause-id {record['pause_id']} --operator John --reason 明确核验原因。"
                     "没有合法 STP 完整证据时保持暂停。不要自动改价重发或点击 IBALGO。"))
            cards.append(AlertCard("🔴 P0 · 止盈价格被券商拒绝 · " + record["symbol"] +
                                   (f" · 原因 {n}/{len(parts)}" if n > 1 else ""), P0_RED, fields))
        self._deliver_protection_notice(now, "P0", "tp price rejected", row, body + "；" + reason, cards[0],
            extra_cards=cards[1:], event_key="tp price rejected:" + record["intent_id"],
            fingerprint=record["pause_id"], p0_once=True)

    def _tp_status_evidence(self, now, event, intent):
        self._tp_record(now, self._tp_context(intent), "status", intent_id=intent["intent_id"], source="orderStatus",
            error_code=event.error_code, reason=event.message,
            broker_order_id=event.broker_order_id, perm_id=event.perm_id,
            raw_status=event.raw_status,
            working_price=event.order_identity.limit_price if event.order_identity else None,
            aux_price=event.order_identity.aux_price if event.order_identity else None,
            mkt_cap_price=event.mkt_cap_price)

    def _on_price_observation(self, now, event):
        if self.dry_run:
            return
        intent = self.store.intent(event.identity.intent_id)
        if intent is None:
            return
        identity = event.identity
        protect = intent["purpose"] == Purpose.PROTECT
        row = self.store.protection_for_intent(intent["intent_id"]) if protect else None
        if protect:
            hard = bool(row is not None and identity.ours and identity.intent_id == row["intent_id"] and
                identity.symbol == row["symbol"] and identity.account == self.cfg.account and
                identity.client_id == self.cfg.client_id and identity.con_id == row["con_id"] and
                identity.broker_order_id == intent["broker_order_id"] == row["broker_order_id"] and
                identity.perm_id == intent["perm_id"] == row["perm_id"] and identity.broker_order_id and identity.perm_id)
        else:
            hard = self._tp_hard_identity(intent, identity)
        if not hard:
            return  # price diagnostics cannot adopt a foreign populated tuple
        cap = event.mkt_cap_price
        payload = {"source": event.source, "intent_id": intent["intent_id"], "symbol": intent["symbol"],
                   "position_id": intent["position_id"], "sent_price": intent["limit_price"],
                   "status": identity.status, "total_quantity": identity.total_quantity,
                   "filled": identity.filled, "remaining": identity.remaining,
                   "order_id": identity.broker_order_id, "perm_id": identity.perm_id,
                   "con_id": identity.con_id, "client_id": identity.client_id,
                   "account_matches": identity.account == self.cfg.account,
                   "connection_snapshot": event.connection_snapshot,
                   "query_snapshot": event.query_snapshot,
                   "socket_generation": event.socket_generation,
                   "pending_generation": row['modify_generation'] if protect else None,
                   "pending_trigger": row['pending_trigger'] if protect else None,
                   "pending_qty": row['pending_qty'] if protect else None,
                   "confirmed_trigger": row['trigger_price'] if protect else None,
                   "working_price": None if protect else identity.limit_price, "aux_price": identity.aux_price,
                   "broker_echo_lmt": identity.broker_echo_lmt,
                   "mkt_cap_price": cap, "mkt_cap_price_raw": event.mkt_cap_price_raw,
                   "cap_valid": cap is not None, "error_code": event.error_code, "reason": event.reason}
        self.store.observation_audit(now, "ORDER_PRICE_OBSERVATION",
                                     generation=getattr(self.broker, "query_generation", None), **payload)
        if intent["purpose"] in TP_PURPOSES:
            tp_row = self.store.protection(intent["position_id"])
            if tp_row is not None:
                self._tp_record(now, tp_row, "status", **{k:v for k,v in payload.items() if k not in {"position_id","symbol"}})
        if identity.status in TERMINAL | {"Filled"}:
            return  # clearing terminal prices is not working-price drift
        if protect:
            expected = [row["trigger_price"]]
            if row["pending_qty"] is not None:
                expected.append(row["pending_trigger"])
            mismatch = (not any(same_cent_price(identity.aux_price, price) for price in expected) or
                        identity.order_type != "STP" or identity.side != "SELL" or identity.tif != "GTC" or
                        identity.outside_rth is not False)
        else:
            mismatch = identity.limit_price != intent["limit_price"] or (cap is not None and cap != intent["limit_price"])
        if not mismatch:
            return
        if protect:
            if row["state"] == "PRICE_PENDING" and self._overnight_position(row, now):
                self._overnight_failure(now, row, "保本修改回报的工作价格与本代待确认价格不一致")
            self.store.hold_intent(now, intent["intent_id"], "protection working price mismatch")
            self.store.update_protection(now, row["position_id"], state="UNKNOWN")
            self.store.set_control(now, f"frozen:{intent['symbol']}", "protection working price mismatch")
            self._protection_page(now, "P0", "unknown", self.store.protection(row["position_id"]),
                "工作中的保护合同价格与已确认或本代待确认价格不一致；不跟随券商价格改写账本")
        else:
            key = f"price_reissue_block:{intent['symbol']}"
            if self.store.control(key) is None:
                self.store.set_control(now, key, json.dumps(payload))
                def price(value):
                    return f"{value:.2f}" if value is not None else "待核实"
                card = AlertCard("🟠 P1 · 券商工作价格不一致 · " + intent["symbol"], P1_ORANGE,
                    (("发生了什么", f"orderRef={intent['intent_id']} 发送价={price(intent['limit_price'])} "
                      f"工作价={price(identity.limit_price)} cap={price(cap)}"),
                     ("影响", "该标的自动重发已阻止；原账本价格保留，STP 恢复继续独立核验。"),
                     ("你要做什么", "1）核对 TWS 原订单价格；2）由 John 处置，禁止自动跟随 cap 改价。")))
                self.alerter.send(now, "P1", "order price mismatch " + intent["symbol"], str(payload), card=card)

    def _tp_consume_resume(self, now):
        if self.dry_run or self._in_urgent_callback or not self.took_over or not self.flags.broker_connected:
            return
        for key, raw in self.store.controls_with_prefix("resume_tp:").items():
            pid = key.split(":", 1)[1]
            try:
                request = json.loads(raw)
                pause_raw = self.store.control(f"tp_pause:{pid}")
                pause = json.loads(pause_raw) if pause_raw else None
                row, pos = self.store.protection(pid), self.store.position(pid)
                if not valid_resume_binding(request, pause, pos, row, pid):
                    continue
                if (pos["status"] != "OPEN" or row["state"] != "WORKING" or row["pending_qty"] is not None or
                    row["tp_stage"] is not None or self.store.control(f"frozen:{row['symbol']}") is not None or
                    self.store.control(f"price_reissue_block:{row['symbol']}") is not None or
                    any(i["hold_reason"] or i["status"] == IntentStatus.UNKNOWN
                        for i in self.store.active_intents() if i["symbol"] == row["symbol"])):
                    continue
                if self._protection_verification_wait(self.clock.now(), row):
                    continue
                try:
                    held, own, others, complete = self._protection_evidence(row)
                except Exception as exc:
                    current = self.store.protection(pid)
                    self._protection_verification_failure(self.clock.now(), current or row, workflow="resume")
                    log.info("[TP_RESUME_DEFER] position=%s evidence=%s", pid, exc)
                    continue
                current = self.store.protection(pid)
                if (not complete or others or len(own) != 1 or not self._protection_order_valid(current, own[0]) or
                    own[0].status not in {"Submitted", "PreSubmitted"} or own[0].remaining != held or
                    held != pos["qty_open"] or current["confirmed_remaining"] != held or
                    current["pending_max_remaining"] != held or current["pending_qty"] is not None or
                    self.store.control(f"tp_pause:{pid}") != pause_raw or
                    self.store.control(key) != raw or
                    not valid_resume_binding(request, pause, self.store.position(pid), current, pid) or
                    current["state"] != "WORKING" or
                    self.store.position(pid)["status"] != "OPEN" or self.store.position(pid)["qty_open"] != held or
                    self.store.control(f"frozen:{row['symbol']}") is not None or
                    self.store.control(f"price_reissue_block:{row['symbol']}") is not None or
                    any(i["hold_reason"] or i["status"] == IntentStatus.UNKNOWN
                        for i in self.store.active_intents() if i["symbol"] == row["symbol"])):
                    # A withdrawn/replaced operator request is not a failed
                    # retry of the old request. Real incomplete evidence for
                    # this still-pending workflow shares the position timer.
                    if (self.store.control(f"tp_pause:{pid}") == pause_raw and
                            self.store.control(key) == raw):
                        self._protection_verification_failure(self.clock.now(), current or row, workflow="resume")
                    continue
                with self.store.tx():
                    self.store.delete_control(f"tp_pause:{pid}")
                    self.store.delete_control(key)
                    self.store.audit(self.clock.now(), "TP_RESUMED_BY_OPERATOR", **request)
                self._protection_verification_success(current, workflow="resume")
                # This tick may finish recovery, never immediately resend TP.
                self._tp_resume_skip_at = self.clock.now()
            except (ValueError, TypeError, KeyError, StoreError):
                continue
            except Exception as exc:
                log.info("[TP_RESUME_DEFER] position=%s evidence=%s", pid, exc)

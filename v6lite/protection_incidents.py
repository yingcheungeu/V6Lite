"""Durable protection incidents: material state, acknowledged delivery, recovery."""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from dataclasses import replace
from v6lite.alerts import AlertCard, INFO_GREEN, P1_ORANGE

class ProtectionIncidentMixin:
    @staticmethod
    def _protection_notice_group(kind):
        return "coverage" if kind in {"unknown", "rejected", "broker cancelled", "gap A overdue"} else kind

    def _protection_order_has_p0(self, intent_id):
        row = self.store.protection_for_intent(intent_id)
        if row is None:
            return False
        records = self.store.controls_with_prefix(f"protect_incident:{row['position_id']}:")
        for raw in records.values():
            try:
                state = json.loads(raw)
                bound = state.get("intent_id")
                if bound is None and state.get("signature"):
                    bound = json.loads(state["signature"])[2][1]
                if bound == intent_id and (state.get("p0_attempted") or state.get("p0_sent") or
                        state.get("pending_level") == "P0" or state.get("batch_level") == "P0"):
                    return True
            except (ValueError, TypeError, KeyError, IndexError):
                continue
        return False

    def _deliver_protection_notice(self, now, level, kind, row, body, card, *, extra_cards=(),
                                   event_key=None, fingerprint=None, p0_once=False):
        terminal = self._protection_terminal_notice(row)
        if level == "P1" and kind == "gap A warning":
            raw = self.store.control(f"protect_incident:{row['position_id']}:coverage")
            try:
                coverage = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                coverage = {}
            if (coverage.get("p0_sent") or coverage.get("p0_attempted") or
                    coverage.get("pending_level") == "P0" or
                    (terminal and terminal.get("p0_attempted"))):
                return None
        if level != "P0" or not row["position_id"]:
            return self.alerter.send(now, level, f"protection {kind} {row['symbol']}", body, card=card)
        pid, symbol = row["position_id"], row["symbol"]
        group = event_key or self._protection_notice_group(kind)
        key = f"protect_incident:{pid}:{group}"
        pos = self.store.position(pid)
        position = [pos["status"], pos["qty_open"]] if pos is not None else None
        raw = self.store.control(key)
        try:
            previous = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            previous = {}
        orders = sorted((o.intent_id, o.broker_order_id, o.perm_id, o.side, o.remaining)
                        for o in self._working.values() if not o.ours and o.symbol in (symbol, "*"))
        if self._working_at is None and previous.get("signature"):
            try:
                orders = json.loads(previous["signature"])[3]
            except (ValueError, TypeError, IndexError):
                pass
        intents = sorted((i["intent_id"], i["broker_order_id"], i["perm_id"],
                          max(0, i["quantity"] - self.store.intent_filled_qty(i["intent_id"])))
                         for i in self.store.active_intents() if i["symbol"] == symbol and i["side"] == "SELL")
        fields = ("state", "intent_id", "broker_order_id", "perm_id", "con_id", "account", "client_id",
                  "requested_qty", "confirmed_remaining", "pending_max_remaining", "pending_qty",
                  "order_type", "trigger_price", "limit_price", "tp_intent_id", "tp_stage", "tp_qty")
        def value(name):
            try:
                return row[name]
            except (KeyError, IndexError):
                return None
        basis = [group, position, [value(f) for f in fields], orders, intents]
        if terminal and group == "coverage":
            basis.append([terminal.get(field) for field in
                          ("intent_id", "raw_status", "error_code", "reason", "complete_identity", "quantity_proof")])
        signature = fingerprint or json.dumps(basis, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        cards = [card, *extra_cards]
        def snapshot(items):
            return [{"title": item.title, "color": item.color, "fields": item.fields,
                     "description": item.description} for item in items]
        def flush(state):
            """Retry immutable unACKed parts, then start the ACK reminder clock."""
            parts, payloads = state["delivery_parts"], state.get("delivery_cards")
            if payloads is None:
                # Pre-release development records did not snapshot cards.
                # Preserve every unACKed slot; never index a changed renderer.
                if len(parts) != len(cards):
                    self.store.audit(now, "PROTECTION_DELIVERY_BATCH_INCOMPLETE", position_id=pid)
                    return False, None
                payloads = state["delivery_cards"] = snapshot(cards)
                state["delivery_body"] = body
                state["delivery_subject"] = f"protection {kind} {symbol}"
            if len(parts) != len(payloads):
                self.store.audit(now, "PROTECTION_DELIVERY_BATCH_INCOMPLETE", position_id=pid)
                return False, None
            waiting = False
            for part in parts:
                if part.get("delivered"):
                    continue
                ticket = part.get("ticket")
                result = self.alerter.delivery(ticket) if self.alerter.knows(ticket) else False
                if result is None:
                    waiting = True
                elif result is True:
                    part["delivered"] = True
                else:
                    part.pop("ticket", None)
            self.store.set_control(now, key, json.dumps(state))
            if waiting:
                return False, None
            first_ticket = None
            for index, part in enumerate(parts):
                if part.get("delivered"):
                    continue
                saved = payloads[index]
                part_card = AlertCard(saved["title"], saved["color"],
                                      tuple(tuple(field) for field in saved["fields"]), saved.get("description", ""))
                ticket = self.alerter.send(now, state["batch_level"],
                    state["delivery_subject"] + (f" reason {index+1}/{len(parts)}" if index else ""),
                    state["delivery_body"], card=part_card, dedupe=False)
                first_ticket = first_ticket or ticket
                if ticket:
                    part["ticket"] = ticket
                    part["delivered"] = self.alerter.delivery(ticket) is True
                self.store.set_control(now, key, json.dumps(state))
            complete = all(part.get("delivered") for part in parts)
            if complete:
                if state["batch_level"] == "P0":
                    state["p0_sent"] = True
                # The successful retry/final observed ACK starts 30 minutes,
                # not the failed first attempt or original queue timestamp.
                state["last_notice"] = now.isoformat()
                for field in ("delivery_parts", "delivery_cards", "delivery_body", "delivery_subject",
                              "batch_level", "batch_at"):
                    state.pop(field, None)
                self.store.set_control(now, key, json.dumps(state))
            return complete, first_ticket
        # A material change cannot discard an older partially delivered reason.
        # Finish that frozen batch before delivering the current material card.
        if previous.get("delivery_parts"):
            complete, ticket = flush(previous)
            if not complete:
                return ticket
        ticket = previous.get("pending_id")
        if ticket:
            result = self.alerter.delivery(ticket) if self.alerter.knows(ticket) else False
            if result is None:
                return None
            if result is True:
                if previous.get("pending_level") == "P0":
                    previous["p0_sent"] = True
                previous["last_notice"] = now.isoformat()
            for field in ("pending_id", "pending_level", "pending_at"):
                previous.pop(field, None)
            self.store.set_control(now, key, json.dumps(previous))
        state = (previous if previous.get("signature") == signature else
                 {"signature": signature, "first_at": now.isoformat(), "p0_sent": False})
        if p0_once or (terminal and group == "coverage"):
            state["p0_only"] = True
        reminder = bool(state.get("p0_sent"))
        if reminder:
            # One acknowledged P0 per unchanged terminal episode. Keep the
            # durable incident, but never turn it into a timed repeat card.
            if state.get("p0_only"):
                return None
            if now - datetime.fromisoformat(state["last_notice"]) < timedelta(minutes=30):
                return None
            level = "P0" if state.get("p0_only") else "P1"
            prefix, color = ("🔴 P0", card.color) if level == "P0" else ("🟠 P1", P1_ORANGE)
            cards = [replace(item, title=prefix + " · 仍未解决 · " + item.title.split(" · ", 1)[-1], color=color)
                     for item in cards]
        state.update(delivery_parts=[{"delivered": False} for _ in cards], delivery_cards=snapshot(cards),
                     delivery_body=body, delivery_subject=f"protection {'still unresolved' if reminder else kind} {symbol}",
                     batch_level=level, batch_at=now.isoformat())
        state["intent_id"] = row["intent_id"]
        if level == "P0":
            state["p0_attempted"] = True
            if terminal and group == "coverage":
                terminal["p0_attempted"] = True
                self.store.set_control(now, f"protect_terminal:{pid}", json.dumps(terminal))
        # Highest queued/attempted severity is durable before remote I/O.
        self.store.set_control(now, key, json.dumps(state))
        _, ticket = flush(state)
        return ticket

    def _protection_resolve_incidents(self, now, row, reason="新鲜三项证据已确认保护覆盖", *,
                                     groups=None, exclude_groups=()):
        prefix = f"protect_incident:{row['position_id']}:"
        records = self.store.controls_with_prefix(prefix)
        records = {key: value for key, value in records.items()
                   if (key[len(prefix):] in groups if groups is not None else
                       key[len(prefix):] != "trigger unfilled" and
                       not key[len(prefix):].startswith("tp price rejected:")) and
                       key[len(prefix):] not in exclude_groups}
        if not records:
            return
        with self.store.tx():
            for key in records:
                self.store.delete_control(key)
            if prefix + "coverage" in records:
                self.store.delete_control(f"protect_terminal:{row['position_id']}")
                self.store.audit(now, "PROTECT_TERMINAL_EPISODE_RESOLVED", position_id=row["position_id"], reason=reason)
            self.store.audit(now, "PROTECTION_INCIDENT_RESOLVED", position_id=row["position_id"], reason=reason)
        symbol = row["symbol"]
        card = AlertCard("🟢 券商保护事件已解除 · " + symbol, INFO_GREEN,
                         (("发生了什么", reason), ("影响", "本保护事件已解除；其他交易门控继续独立核验。"),
                          ("你要做什么", "1）在同一 TWS 核对持仓和订单；2）不要绕过其他门控。")))
        self.alerter.send(now, "INFO", f"protection recovered {symbol}", reason, card=card, dedupe=False)

    def _protection_resolve_if_covered(self, now, row, order, held, others, full_exec):
        current = self.store.protection(row["position_id"])
        if (full_exec and current["state"] == "WORKING" and current["pending_qty"] is None and
                not current["gap_a_started_at"] and not current["gap_b_started_at"] and not others and
                order is not None and self._protection_order_valid(current, order) and
                order.remaining == held == current["confirmed_remaining"]):
            self._protection_resolve_retired_trigger(now, current)
            retry = self._protection_verification_retry.get(row["position_id"])
            unfinished = bool(retry and set(retry.get("pending_workflows", {"progress"})) - {"progress"})
            self._protection_resolve_incidents(now, current,
                exclude_groups={"evidence incomplete"} if unfinished else ())

    def _protection_resolve_retired_trigger(self, now, current):
        # Called only after full fresh three-source proof of the NEW identity.
        key = f"protect_trigger:{current['position_id']}"
        raw = self.store.control(key)
        try:
            episode = json.loads(raw) if raw else {}
            old_id = episode.get("intent_id")
            prior = self.store.intent(old_id) if old_id else None
            lineage_raw = self.store.control(f"retired_protection:{old_id}") if old_id else None
            lineage = json.loads(lineage_raw) if lineage_raw else {}
        except (ValueError, TypeError):
            return
        if (not old_id or old_id == current["intent_id"] or prior is None or prior["hold_reason"] or
                prior["status"] != "CANCELLED" or prior["last_raw_status"] not in {"Cancelled", "ApiCancelled"} or
                not prior["cancel_seen_at"] or lineage.get("order_type") != "STP" or
                lineage.get("intent_id") != old_id or lineage.get("position_id") != current["position_id"] or
                lineage.get("symbol") != current["symbol"] or lineage.get("account") != self.cfg.account or
                lineage.get("client_id") != self.cfg.client_id or lineage.get("con_id") != current["con_id"] or
                not lineage.get("broker_order_id") or lineage["broker_order_id"] != prior["broker_order_id"] or
                not lineage.get("perm_id") or lineage["perm_id"] != prior["perm_id"] or
                lineage.get("quantity") != prior["quantity"]):
            return
        self.store.delete_control(key)
        self._protection_resolve_incidents(now, current,
            "原 STP 已确认取消并退休，新保护已强证据确认；旧止损穿越事件结束（不是成交）",
            groups={"trigger unfilled"})

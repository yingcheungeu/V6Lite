"""Broker-terminal diagnostics are distinct from order execution authority."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from v6lite.store import StoreError
from v6lite.protection import same_cent_price


class ProtectionCancellationMixin:
    def _protection_terminal_notice(self, row):
        try:
            raw = self.store.control(f"protect_terminal:{row['position_id']}")
            notice = json.loads(raw) if raw else {}
            return notice if notice.get("intent_id") == row["intent_id"] else None
        except (ValueError, TypeError, KeyError, IndexError):
            return None

    def _protection_bind_native_contract(self, now, row, identity):
        # This record is DISPLAY provenance only. Never reconstruct an order
        # from it, or use it to authorize release, modification or restoration.
        fields = ("intent_id", "broker_order_id", "perm_id", "symbol", "account", "client_id",
                  "con_id", "side", "order_type", "tif", "outside_rth", "aux_price",
                  "limit_price", "total_quantity")
        binding = {field: getattr(identity, field) for field in fields}
        if identity.order_type == "STP":
            binding["limit_price"] = None
        binding["generation"] = int(row["modify_generation"])
        self.store.set_control(now, f"protect_native_contract:{row['intent_id']}", json.dumps(binding))

    def _protection_terminal_tuple_matches(self, row, event):
        """Attribute a terminal diagnostic; missing fields never grant authority."""
        identity = getattr(event, "terminal_identity", None)
        intent = self.store.intent(event.intent_id)
        try:
            raw = self.store.control(f"protect_native_contract:{event.intent_id}")
            bound = json.loads(raw) if raw else {}
            if identity is None or intent is None or not identity.ours or identity.status != event.raw_status:
                return False
            if identity.intent_id != event.intent_id or event.intent_id != row["intent_id"]:
                return False
            expected = {"symbol": row["symbol"], "account": self.cfg.account,
                        "client_id": self.cfg.client_id, "con_id": row["con_id"]}
            for key, value in expected.items():
                actual = getattr(identity, key)
                if actual not in (None, "") and actual != value:
                    return False
            for key in ("broker_order_id", "perm_id"):
                actual = getattr(identity, key)
                status_id = getattr(event, key)
                known = {row[key], intent[key], bound.get(key)} - {None, 0}
                if (actual and any(actual != value for value in known)) or (status_id and actual and status_id != actual):
                    return False
                if status_id and any(status_id != value for value in known):
                    return False
            if bound and any(bound.get(key) != value for key, value in expected.items()):
                return False
            prices = {None, 0, row["trigger_price"], row["pending_trigger"], bound.get("aux_price")}
            limits = {None, 0, row["limit_price"], row["pending_limit"], bound.get("limit_price")}
            quantities = {0, int(intent["quantity"]), row["pending_qty"], bound.get("total_quantity")}
            if (identity.side not in ("", "SELL") or identity.order_type not in ("", row["order_type"]) or
                    identity.tif not in ("", "GTC") or identity.outside_rth not in (None, False) or
                    identity.total_quantity not in quantities or
                    not (identity.aux_price in {None, 0} or any(same_cent_price(identity.aux_price, price) for price in prices)) or
                    (identity.order_type != "STP" and not (identity.limit_price in limits or
                         (identity.limit_price is not None and identity.limit_price >= 1e308)))):
                return False
            return True
        except (ValueError, TypeError, KeyError, IndexError):
            return False

    def _protection_capture_terminal(self, now, event, *, native_valid):
        row = self.store.protection_for_intent(event.intent_id)
        intent = self.store.intent(event.intent_id)
        if (row is None or intent is None or event.raw_status not in {"Cancelled", "ApiCancelled", "Inactive"} or
                intent["cancel_requested_at"] or row["state"] in {"CANCEL_PENDING", "PROTECT_CANCEL_PENDING"}):
            return False
        same_terminal = self._protection_terminal_tuple_matches(row, event)
        bare_exact = (not event.identity_checked and event.order_identity is None and
                      getattr(event, "terminal_identity", None) is None and
                      bool(row["broker_order_id"] and row["perm_id"]) and
                      event.broker_order_id == row["broker_order_id"] == intent["broker_order_id"] and
                      event.perm_id == row["perm_id"] == intent["perm_id"])
        previous = self._protection_terminal_notice(row) or {}
        notice = {"intent_id": event.intent_id, "first_at": previous.get("first_at", now.isoformat()),
                  "raw_status": event.raw_status, "error_code": event.error_code or previous.get("error_code", 0),
                  "reason": event.message or previous.get("reason", ""),
                  "broker_order_id": event.broker_order_id, "perm_id": event.perm_id,
                  "complete_identity": bool(native_valid),
                  "cancel_class": bool((native_valid or same_terminal or bare_exact) and
                                       (event.error_code or event.message or previous.get("reason"))),
                  "p0_only": True, "p0_attempted": previous.get("p0_attempted", False),
                  "quantity_proof": previous.get("quantity_proof")}
        # Persist diagnosis and the stop state together. A restart between
        # diagnosis and paging must never leave an apparently pending order.
        with self.store.tx():
            self.store.set_control(now, f"protect_terminal:{row['position_id']}", json.dumps(notice))
            if row["state"] != "REJECTED":
                # update_protection owns a transaction; use the same explicit
                # state write and audit inside this already-open atomic unit.
                result = self.store.conn.execute(
                    "UPDATE protections SET state=?,updated_at=? WHERE position_id=?",
                    ("UNKNOWN", now.isoformat(), row["position_id"]))
                if result.rowcount != 1:
                    raise StoreError(f"unknown protection position {row['position_id']}")
                self.store.audit(now, "PROTECT_STATE", position_id=row["position_id"], state="UNKNOWN")
            if not native_valid and not bare_exact and not intent["hold_reason"]:
                # hold_intent/update_intent own transactions. Revoke the old
                # hint's authority inside this same terminal-stop commit.
                reason = ("protection terminal metadata incomplete; manual verification required"
                          if same_terminal else "protection native status identity mismatch"
                          if event.identity_checked or event.order_identity is not None else
                          "protection cancel status identity mismatch")
                result = self.store.conn.execute(
                    "UPDATE intents SET status=?,hold_reason=?,note=?,updated_at=? "
                    "WHERE intent_id=? AND (hold_reason IS NULL OR hold_reason='')",
                    ("UNKNOWN", reason, reason, now.isoformat(), event.intent_id))
                if result.rowcount != 1:
                    raise StoreError(f"cannot persist protection terminal hold {event.intent_id}")
                self.store.audit(now, "INTENT_STATUS", intent_id=event.intent_id,
                                 status="UNKNOWN", note=reason)
            self.store.set_control(now, f"frozen:{row['symbol']}", "broker terminal requires manual verification")
            if any(notice.get(key) != previous.get(key) for key in
                   ("raw_status", "error_code", "reason", "complete_identity", "cancel_class")):
                self.store.audit(now, "PROTECT_BROKER_TERMINAL", position_id=row["position_id"], **notice)
        self._protection_emergency = True
        self._protection_priority_due = True
        # Missing native fields or an existing hold can establish the DIAGNOSTIC,
        # never a CANCEL_SEEN hint or terminal execution authority.
        return bool(same_terminal and not native_valid)

    def _protection_hold_terminal_metadata(self, now, event):
        row = self.store.protection_for_intent(event.intent_id)
        intent = self.store.intent(event.intent_id)
        if not intent["hold_reason"]:
            self.store.hold_intent(now, event.intent_id,
                                   "protection terminal metadata incomplete; manual verification required")
        self.store.update_protection(now, row["position_id"], state="UNKNOWN")
        self.store.set_control(now, f"frozen:{row['symbol']}", "broker terminal metadata requires manual verification")
        audit_kind = "PROTECT_TERMINAL_HOLD_PRESERVED" if intent["hold_reason"] else "PROTECT_TERMINAL_METADATA_INCOMPLETE"
        self.store.audit(now, audit_kind, intent_id=event.intent_id,
                         raw_status=event.raw_status, reason=event.message)
        self._protection_emergency = True
        self._protection_priority_due = True
        self._protection_page(now, "P0", "unknown", self.store.protection(row["position_id"]),
                              "券商终态诊断已关联本请求；原生合同或人工核验门控未完成，仅诊断不授予释放或撤改单权限")

    def _protection_cancel_quantity_proof(self, now, row):
        notice = self._protection_terminal_notice(row)
        proof = self._protection_notice_evidence.get(row["position_id"])
        intent = self.store.intent(row["intent_id"])
        if (not notice or not notice["cancel_class"] or not notice["complete_identity"] or
                not proof or not intent or intent["hold_reason"]):
            return None
        if (proof["intent_id"] != row["intent_id"] or
                proof.get("generation") != int(row["modify_generation"]) or
                proof.get("socket_generation") != getattr(self.broker, "query_generation", None) or
                proof.get("contract_identity") != tuple(row[name] for name in
                    ("broker_order_id", "perm_id", "con_id", "account", "client_id")) or
                row["state"] not in {"UNKNOWN", "REJECTED"} or
                not proof["full_exec"] or proof["own"] or
                proof["started_at"] < datetime.fromisoformat(notice["first_at"]) or
                not timedelta(0) <= now - proof["at"] <= timedelta(seconds=5) or
                notice["broker_order_id"] != row["broker_order_id"] or notice["perm_id"] != row["perm_id"] or
                intent["broker_order_id"] != row["broker_order_id"] or intent["perm_id"] != row["perm_id"]):
            return None
        pos = self.store.position(row["position_id"])
        if pos is None or proof["held"] != (pos["qty_open"] if pos["status"] == "OPEN" else 0):
            return None
        quantities = [proof["held"], 0,
                      sum(self._protection_other_remaining(o) for o in proof["others"] if o.ours),
                      sum(self._protection_other_remaining(o) for o in proof["others"] if not o.ours)]
        # A gain in verified quantity evidence is material; a clock expiry or
        # restart losing its cache is not a fresh broker event.
        if notice.get("quantity_proof") != quantities:
            notice["quantity_proof"] = quantities
            self.store.set_control(now, f"protect_terminal:{row['position_id']}", json.dumps(notice))
        return quantities

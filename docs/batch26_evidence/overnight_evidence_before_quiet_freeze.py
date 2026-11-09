"""Scoped overnight TP/BE proof, never full execution-history authority.

Trusts persisted economic records within John's accepted visible-evidence
scope. Same-net manual round trips and consistent whole-DB rollback remain
accepted risks. These helpers never book account-query rows or clear holds.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timedelta
from v6lite.alerts import AlertCard, INFO_GREEN, P1_ORANGE
from v6lite.market_calendar import trading_day
from v6lite.models import IntentStatus, Purpose

_WORKING = {"Submitted", "PreSubmitted"}
_TERMINAL_CANCEL = {"Cancelled", "ApiCancelled", "Inactive"}
_CONSTRAINTS = (
    "依据运行约束 A、B（John 明确接受的剩余风险，不是技术证明）："
    "A 账户独占：Lite 管理的标的只由 Lite 交易，人工处置一律按停机→撤单→平仓→release；"
    "B 账本不回滚：不手动替换、恢复、编辑 .v6lite 状态库。"
    "仍不能技术排除停机期间等量手动卖出又买回，以及状态库整体回滚后恰与券商一致。")
_BE_BINDING_FIELDS = frozenset(("position_id", "intent_id", "broker_order_id", "perm_id", "con_id",
    "account", "client_id", "modify_generation", "pending_qty", "pending_trigger"))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


class OvernightEvidenceMixin:
    def _overnight_position(self, row, now=None):
        now = now or self.clock.now()
        pos = self.store.position(row["position_id"])
        if pos is None:
            return False
        try:
            day = trading_day(now)
            if trading_day(datetime.fromisoformat(pos["opened_at"])) < day:
                return True
            if self.store.conn.execute(
                    "SELECT 1 FROM fills WHERE position_id=? AND trading_day<? LIMIT 1",
                    (row["position_id"], day.isoformat())).fetchone():
                return True
            # A prior-day ENTRY intent that fills for the first time today is
            # not itself an overnight holding; actual position/fill dates are.
            return False
        except (TypeError, ValueError):
            return True  # corrupt dates must not become today's empty history

    def _overnight_needs_visible_query(self, row):
        return self._overnight_position(row) and (
            self.cfg.protection.overnight_tp == "constraint" or
            self.store.control(f"overnight_tp_flow:{row['position_id']}") is not None)

    def _overnight_visible_query(self, row):
        """Current API visibility, without changing Master or claiming all clients."""
        if not self._overnight_needs_visible_query(row):
            return {"account_fills": None, "visible_scope": "client-scoped executions_today"}
        query = getattr(self.broker, "account_executions_today", None)
        if not callable(query):
            return {"account_fills": None, "visible_scope": "client-scoped executions_today"}
        try:
            if self._protection_steady_evidence_budget:
                self._protect("query")
            rows = query()
            if rows is None:
                raise ValueError("account execution response missing")
            return {"account_fills": tuple(rows),
                    "visible_scope": "client + currently API-visible account execution queries"}
        except Exception as exc:
            return {"account_fills": None, "visible_scope": "account query unavailable",
                    "overnight_visible_error": type(exc).__name__}

    def _overnight_history(self, row, now):
        """Reconcile persisted fills, intents, net shares and booked average cost."""
        pid = row["position_id"]
        pos = self.store.position(pid)
        if pos is None or pos["status"] != "OPEN":
            raise ValueError("账本持仓不是 OPEN")
        records = [dict(f) for f in self.store.conn.execute(
            "SELECT rowid AS ledger_rowid,* FROM fills WHERE position_id=? ORDER BY rowid", (pid,))]
        if not records:
            raise ValueError("账本缺少已持久化的入场成交 execId")
        bought, quantity, average = 0, 0, 0.0
        historical, entries = [], []
        for fill in records:
            iid = fill["intent_id"]
            intent = self.store.intent(iid) if iid else None
            if (not fill["exec_id"] or intent is None or intent["symbol"] != row["symbol"] or
                    fill["symbol"] != row["symbol"] or fill["side"] != intent["side"] or
                    pid not in (intent["position_id"], intent["recovery_position_id"])):
                raise ValueError("账本入场/退出成交的 intent、标的或持仓归属不一致")
            if (not fill["broker_order_id"] or not fill["perm_id"] or
                    fill["broker_order_id"] != intent["broker_order_id"] or
                    fill["perm_id"] != intent["perm_id"]):
                raise ValueError("账本成交 orderId/permId 与持久化订单身份不一致")
            qty, price = fill["quantity"], fill["price"]
            if (type(qty) is not int or qty <= 0 or not isinstance(price, (int, float)) or
                    not math.isfinite(price) or price <= 0):
                raise ValueError("账本成交数量或价格无效")
            occurred = datetime.fromisoformat(fill["time"])
            if (occurred.tzinfo is None or occurred > now or
                    trading_day(occurred).isoformat() != fill["trading_day"]):
                raise ValueError("账本成交时间或交易日不一致")
            if self.store.control(f"execution_time_unverified:{fill['exec_id']}") is not None:
                raise ValueError("账本成交时间尚未核实")
            if fill["side"] == "BUY" and intent["purpose"] == Purpose.ENTRY:
                average = (average * quantity + price * qty) / (quantity + qty)
                quantity += qty
                bought += qty
                entries.append({"exec_id": fill["exec_id"], "day": fill["trading_day"], "quantity": qty})
            elif fill["side"] == "SELL" and intent["purpose"] != Purpose.ENTRY:
                quantity -= qty
                if quantity < 0:
                    raise ValueError("账本成交顺序的净数量不能解释持仓")
            else:
                raise ValueError("账本入场/退出成交方向与用途不一致")
            if fill["trading_day"] < trading_day(now).isoformat():
                historical.append({k: v for k, v in fill.items() if k != "ledger_rowid"})
        if not entries:
            raise ValueError("账本缺少 ENTRY 入场成交 execId")
        if bought != int(pos["qty_initial"]) or quantity != int(pos["qty_open"]):
            raise ValueError("账本成交 BUY/SELL 净数量或入场数量与持仓不一致")
        if not math.isclose(average, float(pos["entry_price"]), rel_tol=0, abs_tol=1e-7):
            raise ValueError("账本入场成交价格与持仓成本不一致")
        return {"entries": entries, "historical_digest": _digest(historical),
                "fill_ids": [f["exec_id"] for f in records], "records": records}

    def _overnight_failure(self, now, row, reason):
        """One durable explanation per holding, independent of real protection P0s."""
        key = f"overnight_tp_failure:{row['position_id']}"
        if self.store.control(key) is not None:
            return
        fingerprint = _digest([reason, row["intent_id"], row["broker_order_id"], row["perm_id"]])
        detail = self._overnight_display(now, row, reason)
        card = AlertCard("🟠 P1 · 过夜持仓止盈核验未通过 · " + row["symbol"], P1_ORANGE,
                         (("核验结果", detail),
                          ("影响", "冻结本持仓新的自动止盈/保本；保留现有券商止损，不自动加量或重建。"),
                          ("你要做什么", "1）在 TWS 核对持仓及 GTC STP；2）核对本机账本与当日可见成交；"
                           "3）保留冻结，由 John 处理差异，不要盲目新增卖单。")))
        self.alerter.send(now, "P1", "overnight TP evidence " + row["position_id"], detail, card=card)
        self.store.set_control(now, key, fingerprint)

    def _overnight_display(self, now, row, reason, history=None):
        pos = self.store.position(row["position_id"])
        proof = self._protection_notice_evidence.get(row["position_id"], {})
        ledger = pos["qty_open"] if pos else "未知"
        fresh = self._fresh_held.get(row["symbol"])
        fresh_display = (fresh[0] if fresh and trading_day(fresh[1]) == trading_day(now) and
                         timedelta(0) <= now - fresh[1] <= timedelta(seconds=60) else "未知")
        held = proof.get("held", fresh_display)
        entries = (history or {}).get("entries", [])
        if not entries:
            entries = [dict(f) for f in self.store.conn.execute(
                "SELECT f.exec_id,f.trading_day AS day FROM fills f JOIN intents i ON i.intent_id=f.intent_id "
                "WHERE f.position_id=? AND i.purpose='ENTRY'", (row["position_id"],))]
        source = "、".join(f"{f['exec_id']}@{f['day']}" for f in entries) or "缺少入场 execId"
        return (f"{row['symbol']}：{reason}；券商 P={held} 股，账本={ledger} 股，"
                f"STP S={row['confirmed_remaining']} 股；GTC orderId={row['broker_order_id'] or '-'} "
                f"permId={row['perm_id'] or '-'}；历史账本入场={source}；"
                f"当日可见成交={proof.get('overnight_visible_count', '未完成')} 条，"
                f"未知项={proof.get('overnight_unknown', '未完成核验')}；"
                f"范围={proof.get('visible_scope', '当日可见，未宣称全账户')}；核验时间={now.isoformat()}；"
                + _CONSTRAINTS)

    def _overnight_stop_stable(self, now, row, held, own, others):
        """Fresh stop health only; this never grants TP/BE or clears failure facts."""
        pos = self.store.position(row["position_id"])
        proof = self._protection_notice_evidence.get(row["position_id"])
        intent = self.store.intent(row["intent_id"])
        return bool(pos is not None and pos["status"] == "OPEN" and
            proof and proof.get("authority_binding") == self._protection_verification_binding(row) and
            proof.get("socket_generation") == getattr(self.broker, "query_generation", None) and
            trading_day(proof["started_at"]) == trading_day(now) and
            trading_day(proof["at"]) == trading_day(now) and
            timedelta(0) <= now - proof["at"] <= timedelta(seconds=60) and
            self._protection_steady(row) and intent is not None and not intent["hold_reason"] and
            held == int(pos["qty_open"]) == row["confirmed_remaining"] == row["pending_max_remaining"] and
            len(own) == 1 and not others and self._protection_order_valid(row, own[0]) and
            own[0].status in _WORKING and own[0].remaining == held)

    def _overnight_authority(self, now, row, held, own, others, *, workflow=False, announce=False):
        """Consume the current fresh snapshot only; never manufacture full_exec."""
        try:
            pos = self.store.position(row["position_id"])
            if not workflow and self.cfg.protection.overnight_tp != "constraint":
                raise ValueError("overnight_tp=freeze：默认只保留止损，过夜自动止盈/保本受限制")
            history = self._overnight_history(row, now)
            proof = self._protection_notice_evidence.get(row["position_id"])
            if (not proof or proof["intent_id"] != row["intent_id"] or
                    proof["generation"] != int(row["modify_generation"]) or
                    proof["socket_generation"] != getattr(self.broker, "query_generation", None) or
                    proof.get("authority_binding") != self._protection_verification_binding(row) or
                    not (timedelta(0) <= now - proof["at"] <= timedelta(seconds=60)) or
                    trading_day(proof["started_at"]) != trading_day(now) or
                    trading_day(proof["at"]) != trading_day(now)):
                raise ValueError("当日新鲜三项核验已过期、跨交易日、换代或持仓/订单状态变化")
            if held != int(pos["qty_open"]):
                raise ValueError("券商持仓 P 与账本 qty_open 不一致")
            if proof.get("overnight_visible_error"):
                raise ValueError("当日可见成交查询失败：" + proof["overnight_visible_error"])
            if not proof.get("current_day_executions_complete"):
                raise ValueError("当日账本成交未全部出现在新鲜成交查询中")
            visible = {}
            today = trading_day(now)
            for fill in tuple(proof.get("client_fills") or ()) + tuple(proof.get("account_fills") or ()):
                if fill.symbol != row["symbol"]:
                    continue
                if fill.time.tzinfo is None or trading_day(fill.time) > today:
                    raise ValueError("当日可见成交时间无效")
                if trading_day(fill.time) != today:
                    continue
                economic = (fill.symbol, str(fill.side), fill.quantity, fill.price,
                            fill.broker_order_id, fill.perm_id, fill.time)
                if fill.exec_id in visible and visible[fill.exec_id] != economic:
                    raise ValueError("当日同 execId 的可见成交经济字段冲突")
                visible[fill.exec_id] = economic
                booked = self.store.conn.execute("SELECT * FROM fills WHERE exec_id=?", (fill.exec_id,)).fetchone()
                if (booked is None or not booked["intent_id"] or
                        self.store.intent(booked["intent_id"]) is None or
                        (fill.intent_id and fill.intent_id != booked["intent_id"]) or
                        (booked["symbol"], booked["side"], booked["quantity"], booked["price"],
                         booked["broker_order_id"], booked["perm_id"], datetime.fromisoformat(booked["time"])) != economic):
                    proof["overnight_unknown"] = fill.exec_id
                    raise ValueError("当日可见成交无法归属或与账本经济身份不一致：" + fill.exec_id)
            proof["overnight_visible_count"] = len(visible)
            proof["overnight_unknown"] = "0"
            if self.store.control(f"frozen:{row['symbol']}") is not None or self._tp_paused(
                    row["position_id"], row["symbol"]):
                raise ValueError("既有持仓冻结或止盈人工暂停仍有效")
            if self.store.control(f"protect_evidence_failure:{row['position_id']}") is not None:
                raise ValueError("既有三项证据失败计时仍有效，不能由历史账本解除")
            if row["position_id"] in self._protection_verification_retry:
                raise ValueError("既有证据核验重试仍有效，不能由历史账本解除")
            if any(i["hold_reason"] or i["status"] == IntentStatus.UNKNOWN for i in self.store.conn.execute(
                    "SELECT * FROM intents WHERE symbol=?", (row["symbol"],))):
                raise ValueError("既有订单 hold/UNKNOWN 仍有效，不能由历史账本解除")
            if self.store.anomalies():
                raise ValueError("既有人工异常仍有效，不能由历史账本解除")
            if len(own) > 1 or any(not self._protection_order_valid(row, o) for o in own):
                raise ValueError("GTC STP 保护工作单强身份核验失败")
            if not workflow:
                price_confirmation = self._overnight_price_confirmation(row, pos, held, history)
                if ((not price_confirmation and (row["state"] != "WORKING" or row["pending_qty"] is not None)) or
                        row["tp_stage"] is not None or row["gap_a_started_at"] or row["gap_b_started_at"] or
                        len(own) != 1 or others or
                        own[0].status not in _WORKING or own[0].remaining != held or
                        row["confirmed_remaining"] != held or row["pending_max_remaining"] != held):
                    raise ValueError("GTC STP 工作状态/保护数量与持仓不一致，或存在其他工作中卖单")
            else:
                self._overnight_check_flow(now, row, held, own, others, history)
            proof["overnight_tp_scope"] = {"history_digest": history["historical_digest"],
                "entry_exec_ids": [entry["exec_id"] for entry in history["entries"]],
                "workflow": workflow, "full_execution_history": False,
                "price_confirmation": bool(not workflow and price_confirmation)}
            if announce:
                key = f"overnight_tp_restored:{row['position_id']}"
                if self.store.control(key) is None:
                    detail = self._overnight_display(now, row,
                        "依据运行约束 A、B，历史账本 + 当日可见三项核验符合，自动止盈恢复", history)
                    card = AlertCard("🟢 INFO · 过夜持仓自动止盈恢复 · " + row["symbol"], INFO_GREEN,
                        (("证据", detail), ("权限", "仅止盈、保本及本次止盈自身恢复；不提供完整历史成交权限。"),
                         ("你要做什么", "1）核对标的、股数与 GTC 编号；2）观察后续止盈/保本结果；"
                          "3）出现差异时保持冻结并在同一 TWS 核查。")))
                    delivery_id = self.alerter.send(now, "INFO", "overnight TP restored " + row["position_id"],
                                                   detail, card=card)
                    self.store.set_control(now, key, json.dumps({"at": now.isoformat(),
                        "historical_digest": history["historical_digest"], "delivery_id": delivery_id}))
            return True
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self._overnight_failure(now, row, str(exc))
            return False

    def _overnight_begin_flow(self, now, row, stage, workflow):
        history = self._overnight_history(row, now)
        value = {"workflow_id": workflow["workflow_id"], "position_id": row["position_id"],
            "stage": str(stage), "day": trading_day(now).isoformat(), "started_at": now.isoformat(),
            "starting_quantity": int(self.store.position(row["position_id"])["qty_open"]),
            "historical_digest": history["historical_digest"], "initial_fill_ids": history["fill_ids"],
            "original_identity": {k: row[k] for k in ("intent_id", "broker_order_id", "perm_id", "con_id",
                "account", "client_id", "symbol", "trigger_price")}, "tp_intent_id": None,
            "replacement_intent_id": None, "original_cancel_requested": False}
        self.store.set_control(now, f"overnight_tp_flow:{row['position_id']}", json.dumps(value, sort_keys=True))

    def _overnight_flow(self, row):
        raw = self.store.control(f"overnight_tp_flow:{row['position_id']}")
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except (ValueError, TypeError):
            return None

    def _overnight_check_flow(self, now, row, held, own, others, history):
        token = self._overnight_flow(row)
        workflow = self._tp_workflow(row)
        if (not token or token["position_id"] != row["position_id"] or
                token["workflow_id"] != workflow.get("workflow_id") or token["stage"] != row["tp_stage"] or
                token["day"] != trading_day(now).isoformat() or
                token["historical_digest"] != history["historical_digest"] or
                not 0 <= held <= token["starting_quantity"]):
            raise ValueError("本次过夜止盈恢复 token、历史账本或数量边界不一致")
        identity = token["original_identity"]
        allowed_ref = token.get("replacement_intent_id") or identity["intent_id"]
        if row["intent_id"] != allowed_ref or any(row[k] != identity[k] for k in (
                "con_id", "account", "client_id", "symbol")):
            raise ValueError("本次止盈原 GTC 保护身份已变化")
        if not token.get("replacement_intent_id") and any(row[k] != identity[k] for k in (
                "broker_order_id", "perm_id", "trigger_price")):
            raise ValueError("本次止盈原 GTC orderId/permId/止损价已变化")
        tp_id = token.get("tp_intent_id")
        if row["tp_intent_id"] != tp_id:
            raise ValueError("本次止盈订单与恢复 token 不匹配")
        for fill in history["records"]:
            if fill["exec_id"] not in token["initial_fill_ids"] and (
                    fill["side"] != "SELL" or fill["intent_id"] not in (tp_id, identity["intent_id"])):
                raise ValueError("本次止盈期间出现额外买入或不相关成交")
        if others and any(o.intent_id != tp_id or not o.ours for o in others):
            raise ValueError("本次止盈期间存在其他工作中卖单")
        if not own:
            original = self.store.intent(identity["intent_id"])
            if (token.get("replacement_intent_id") or original is None or
                    not token.get("original_cancel_requested") or original["last_raw_status"] not in _TERMINAL_CANCEL):
                raise ValueError("原 GTC 未获本次止盈的明确撤单证据，禁止重建")
        possible_s = max([math.ceil(o.remaining) for o in own] + [0])
        possible_t = sum(self._protection_other_remaining(o) for o in others)
        if possible_s + possible_t > held:
            raise ValueError("本次止盈的 S+T+X 超过券商持仓")

    def _overnight_restore_previous_total(self, row, intent):
        """Recognize the old native quantity while our scoped restore awaits ACK.

        This is an identity candidate, not write authority or confirmation.
        The current native generation, pending quantity, price and fresh scoped
        proof must still agree before RESTORE_PENDING can become WORKING.
        """
        if row["state"] != "RESTORE_PENDING" or row["pending_qty"] is None or intent["hold_reason"]:
            return None
        try:
            token = self._overnight_flow(row)
            workflow = self._tp_workflow(row)
            pos = self.store.position(row["position_id"])
            now = self.clock.now()
            if (not token or not workflow or pos is None or pos["status"] != "OPEN" or
                    token["position_id"] != row["position_id"] or
                    token["workflow_id"] != workflow.get("workflow_id") or
                    workflow.get("position_id") != row["position_id"] or
                    token["stage"] != row["tp_stage"] or token["stage"] != workflow.get("stage") or
                    token["day"] != trading_day(now).isoformat() or
                    token["tp_intent_id"] != row["tp_intent_id"] or
                    token.get("replacement_intent_id") is not None):
                return None
            identity = token["original_identity"]
            fields = ("intent_id", "broker_order_id", "perm_id", "con_id", "account", "client_id",
                      "symbol", "trigger_price")
            if any(identity[k] != row[k] for k in fields):
                return None
            history = self._overnight_history(row, now)
            if token["historical_digest"] != history["historical_digest"]:
                return None
            filled = self.store.intent_filled_qty(row["intent_id"])
            previous = int(row["requested_qty"])
            if (not 0 < previous < int(row["pending_qty"]) or
                    previous != int(row["confirmed_remaining"]) + filled or
                    int(row["pending_qty"]) != int(pos["qty_open"]) + filled or
                    not 0 <= int(pos["qty_open"]) <= token["starting_quantity"] or
                    row["pending_trigger"] != row["trigger_price"] or row["pending_limit"] is not None):
                return None
            return previous
        except (ValueError, TypeError, KeyError, OverflowError):
            return None

    def _overnight_flow_authority(self, now, row, held, own, others):
        return bool(self._overnight_flow(row) and self._overnight_authority(
            now, row, held, own, others, workflow=True))

    def _overnight_bind_tp(self, now, row, intent_id):
        token = self._overnight_flow(row)
        if token is None:
            return
        if token.get("tp_intent_id") not in (None, intent_id):
            raise ValueError("本次过夜止盈 token 不允许第二张止盈单")
        token["tp_intent_id"] = intent_id
        self.store.set_control(now, f"overnight_tp_flow:{row['position_id']}", json.dumps(token, sort_keys=True))


    def _overnight_begin_price(self, now, row):
        """Bind only our already-authorized same-quantity BE modification."""
        history = self._overnight_history(row, now)
        value = {"day": trading_day(now).isoformat(), "historical_digest": history["historical_digest"],
            "binding": {k: row[k] for k in ("position_id", "intent_id", "broker_order_id", "perm_id",
                "con_id", "account", "client_id", "modify_generation", "pending_qty", "pending_trigger")}}
        self.store.set_control(now, f"overnight_be_flow:{row['position_id']}", json.dumps(value, sort_keys=True))

    def _overnight_price_confirmation(self, row, pos, held, history):
        raw = self.store.control(f"overnight_be_flow:{row['position_id']}")
        if raw is None:
            return False
        try:
            value = json.loads(raw)
            return (row["state"] == "PRICE_PENDING" and
                value["day"] == trading_day(self.clock.now()).isoformat() and
                value["historical_digest"] == history["historical_digest"] and
                isinstance(value["binding"], dict) and set(value["binding"]) == _BE_BINDING_FIELDS and
                all(row[k] == value["binding"][k] for k in _BE_BINDING_FIELDS) and
                row["pending_qty"] == held + self.store.intent_filled_qty(row["intent_id"]) and
                row["pending_trigger"] == pos["breakeven_stop"] and pos["tp1_done"] and
                row["confirmed_remaining"] == row["pending_max_remaining"] == held)
        except (ValueError, TypeError, KeyError):
            return False

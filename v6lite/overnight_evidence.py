"""Scoped overnight TP/BE proof, never full execution-history authority.

Trusts persisted economic records within John's accepted visible-evidence
scope. Same-net manual round trips and consistent whole-DB rollback remain
accepted risks. These helpers never book account-query rows or clear holds.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, time, timedelta, timezone
from v6lite.alerts import AlertCard, INFO_GREEN, P1_ORANGE
from v6lite.market_calendar import ET, trading_day
from v6lite.models import IntentStatus, Purpose
from v6lite.protection import same_cent_price
from v6lite.execution_time import historical_receipt_before, receipt_identity

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
        query = getattr(self.broker, "account_executions_today_scoped", None)
        scope_required = callable(query)
        if not scope_required:
            query = getattr(self.broker, "account_executions_today", None)
        if not callable(query):
            return {"account_fills": None, "visible_scope": "client-scoped executions_today"}
        try:
            if self._protection_steady_evidence_budget:
                self._protect("query")
            rows = query()
            if rows is None:
                return {"account_fills": None, "visible_scope": "account query unavailable",
                        "account_execution_scope_required": scope_required,
                        "overnight_visible_error": "account execution response missing"}
            return {"account_fills": tuple(rows),
                    "account_execution_scope_required": scope_required,
                    "account_execution_scope": getattr(rows, "scope", None),
                    "visible_scope": "client + currently API-visible account execution queries"}
        except Exception as exc:
            return {"account_fills": None, "visible_scope": "account query unavailable",
                    "account_execution_scope_required": scope_required,
                    "overnight_visible_error": type(exc).__name__,
                    "overnight_visible_request_failed": True,
                    "overnight_visible_request_exception": exc}

    def _overnight_current_scope(self, row, now):
        """Validate this proof's explicit account-visible UTC-day request only."""
        proof = self._protection_notice_evidence.get(row["position_id"], {})
        scope = proof.get("account_execution_scope")
        if not isinstance(scope, dict) or scope.get("complete") is not True:
            return None
        day = trading_day(now).isoformat()
        since = datetime.combine(trading_day(now), time(0), tzinfo=ET).astimezone(timezone.utc)
        started, ended = scope.get("started_at"), scope.get("completed_at")
        if (scope.get("role") != "account_visible" or scope.get("day") != day or
                scope.get("since_utc") != since or scope.get("time_filter") != since.strftime("%Y%m%d-%H:%M:%S") or
                scope.get("generation") != getattr(self.broker, "query_generation", None) or
                proof.get("socket_generation") != getattr(self.broker, "query_generation", None) or
                proof.get("account_fills") is None or proof.get("overnight_visible_error") or
                not isinstance(started, datetime) or not isinstance(ended, datetime) or
                started.tzinfo is None or ended.tzinfo is None or
                not (proof["started_at"] <= started <= ended <= now) or
                trading_day(started).isoformat() != day or trading_day(ended).isoformat() != day or
                not (timedelta(0) <= now - ended <= timedelta(seconds=60))):
            return None
        return scope

    @staticmethod
    def _overnight_fill_economics(fill):
        return (fill["symbol"], fill["side"], fill["quantity"], fill["price"],
                fill["broker_order_id"], fill["perm_id"])

    @staticmethod
    def _overnight_visible_economics(fill):
        if (type(fill.quantity) is not int or fill.quantity <= 0 or
                type(fill.price) not in (int, float) or not math.isfinite(fill.price) or fill.price <= 0 or
                type(fill.broker_order_id) is not int or fill.broker_order_id <= 0 or
                type(fill.perm_id) is not int or fill.perm_id <= 0):
            raise ValueError("当日可见成交经济字段类型或数值无效")
        return (fill.symbol, str(fill.side), fill.quantity, fill.price, fill.broker_order_id, fill.perm_id)

    def _overnight_today_receipt(self, row, fill, marker, now):
        received = receipt_identity(fill, marker, row["con_id"],
            pinned_con_id=self.store.control(f"conid:{fill['symbol']}"))
        if (not self._overnight_position(row, now) or fill["trading_day"] != trading_day(now).isoformat() or
                trading_day(received) != trading_day(now) or received > now):
            raise ValueError("当日成交 provisional 时间不属于今天或晚于核验时间")
        scope = self._overnight_current_scope(row, now)
        if scope is None:
            raise ValueError("缺少本轮完整、新鲜、同代且显式 UTC 当日范围的可见成交查询")
        proof = self._protection_notice_evidence[row["position_id"]]
        matches = [item for item in proof["account_fills"] if item.exec_id == fill["exec_id"]]
        if not matches:
            raise ValueError("本轮当日可见成交查询没有该 execId：" + fill["exec_id"])
        if any(self._overnight_visible_economics(item) != self._overnight_fill_economics(fill) or
               (item.con_id is not None and item.con_id != row["con_id"]) for item in matches):
            raise ValueError("本轮当日可见成交经济身份与账本不一致：" + fill["exec_id"])
        return "当日成交时间按当日成交查询判定为今天（精确时间未核实）"

    def _overnight_record_today_basis(self, now, row, history):
        ids = sorted(history["today_time_ids"])
        records = [item for item in history["records"] if item["exec_id"] in ids]
        snapshots = {item["exec_id"]: [item["time"], item["trading_day"], item["intent_id"],
            item["symbol"], item["side"], item["quantity"], item["price"], item["broker_order_id"], item["perm_id"]]
            for item in records}
        key = f"overnight_today_time_basis:{row['position_id']}:{trading_day(now).isoformat()}:{_digest(snapshots)}"
        if self.store.control(key) is not None:
            return
        note = "当日成交时间按当日成交查询判定为今天（精确时间未核实）"
        scope = self._overnight_current_scope(row, now)
        self.store.audit(now, "OVERNIGHT_CURRENT_DAY_TIME_BASIS", position_id=row["position_id"],
            day=trading_day(now).isoformat(), exec_ids=ids, snapshots=snapshots, note=note,
            query_request_id=scope["request_id"], query_generation=scope["generation"],
            query_since=scope["since_utc"].isoformat(), query_completed=scope["completed_at"].isoformat())
        detail = row["symbol"] + "：" + note + "；execId=" + "、".join(ids) + "；" + _CONSTRAINTS
        card = AlertCard("🟢 INFO · 过夜持仓当日成交时间依据 · " + row["symbol"], INFO_GREEN,
            (("证据", detail), ("权限", "仅本轮止盈/保本日归属；不是精确时间核实，也不是完整历史成交权限。"),
             ("你要做什么", "1）核对标的、股数和订单编号；2）保留未核时标记，等待后续精确时间查询核实。")))
        self.alerter.send(now, "INFO", "overnight current-day execution basis " + row["position_id"], detail, card=card)
        self.store.set_control(now, key, now.isoformat())

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
        historical, entries, receipt_notes, today_time_ids = [], [], [], []
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
            marker = self.store.control(f"execution_time_unverified:{fill['exec_id']}")
            if marker is not None:
                try:
                    if fill["trading_day"] == trading_day(now).isoformat():
                        note = self._overnight_today_receipt(row, fill, marker, now)
                        today_time_ids.append(fill["exec_id"])
                    else:
                        note = historical_receipt_before(fill, marker, row["con_id"], now,
                            pinned_con_id=self.store.control(f"conid:{fill['symbol']}"))
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    reason = str(exc)
                    if not reason.startswith("账本成交时间尚未核实"):
                        reason = "账本成交时间尚未核实：" + reason
                    raise ValueError(reason) from exc
                receipt_notes.append(note)
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
                "fill_ids": [f["exec_id"] for f in records], "records": records,
                "receipt_time_notes": sorted(set(receipt_notes)), "today_time_ids": today_time_ids}

    def _overnight_failure(self, now, row, reason):
        """One durable explanation per holding, independent of real protection P0s."""
        cause_key = f"overnight_tp_freeze_causes:{row['position_id']}:{trading_day(now).isoformat()}"
        try:
            provenance = json.loads(self.store.control(cause_key) or 'null')
        except (ValueError, TypeError):
            provenance = None
        binding = {k: row[k] for k in _BE_BINDING_FIELDS}
        cause = 'price_confirmation_pending' if reason in {'保本修改待确认', '修改待确认'} else 'real_failure'
        if provenance is None:
            legacy_marker = self.store.control(
                f"overnight_tp_frozen_at_price:{row['position_id']}:{trading_day(now).isoformat()}")
            # Later observations cannot rewrite the unknown cause of an old freeze.
            provenance = {'version': 31, 'binding': binding,
                'causes': ['unclassified'] if legacy_marker is not None or
                    self.store.control(f"frozen:{row['symbol']}") is not None else []}
            if provenance['causes']:
                provenance['reasons'] = ['既有冻结来源不明；后续修改待确认不能重分类旧冻结']
        if (not isinstance(provenance, dict) or provenance.get('version') != 31 or
                not isinstance(provenance.get('causes'), list)):
            provenance = {'version': 31, 'binding': binding, 'causes': ['unclassified']}
        if provenance.get('binding') != binding:
            cause = 'real_failure'  # a different request cannot inherit the old exception
        reasons = provenance.get('reasons')
        if not isinstance(reasons, list):
            reasons = provenance['reasons'] = []
        if reason not in reasons:
            reasons.append(reason)
        if cause not in provenance['causes']:
            provenance['causes'].append(cause)
        self.store.set_control(now, cause_key, json.dumps(provenance, sort_keys=True))
        key = f"overnight_tp_failure:{row['position_id']}"
        if self.store.control(key) is not None:
            return
        fingerprint = _digest([reason, row["intent_id"], row["broker_order_id"], row["perm_id"]])
        try:
            self._overnight_history(row, now)
            a_failed = False
        except (ValueError, TypeError, KeyError, OverflowError):
            a_failed = True
        detail = self._overnight_display(now, row, reason, a_failed=a_failed)
        card = AlertCard("🟠 P1 · 过夜持仓止盈核验未通过 · " + row["symbol"], P1_ORANGE,
                         (("核验结果", detail),
                          ("影响", "冻结本持仓新的自动止盈/保本；不自动撤改或补建保护单，当前保护按新鲜证据及独立 P0 处理。"),
                          ("你要做什么", "1）在 TWS 核对持仓及 GTC STP；2）核对本机账本与当日可见成交；"
                           "3）保留冻结，由 John 处理差异，不要盲目新增卖单。")))
        self.alerter.send(now, "P1", "overnight TP evidence " + row["position_id"], detail, card=card)
        self.store.set_control(now, key, fingerprint)

    def _overnight_display(self, now, row, reason, history=None, *, a_failed=False):
        pos = self.store.position(row["position_id"])
        proof = self._protection_notice_evidence.get(row["position_id"], {})
        ledger = pos["qty_open"] if pos else "未知"
        current_stop = "未知"
        try:
            complete_fresh = bool(proof and "own" in proof and "others" in proof and
                proof.get("authority_binding") == self._protection_verification_binding(row) and
                proof.get("socket_generation") == getattr(self.broker, "query_generation", None) and
                trading_day(proof["started_at"]) == trading_day(now) and
                trading_day(proof["at"]) == trading_day(now) and
                timedelta(0) <= now - proof["at"] <= timedelta(seconds=60))
            if complete_fresh:
                own = proof["own"]
                if not own:
                    current_stop = 0
                elif (len(own) == 1 and own[0].status in _WORKING and
                        self._protection_order_valid(row, own[0])):
                    current_stop = math.ceil(own[0].remaining)
        except (ValueError, TypeError, KeyError, OverflowError):
            pass  # incomplete display proof never turns old local S into current S
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
        visible = ("未执行（a 未通过）" if a_failed else
                   f"{proof.get('overnight_visible_count', '未完成')} 条")
        unknown = "未执行（a 未通过）" if a_failed else proof.get("overnight_unknown", "未完成核验")
        notes = "；".join((history or {}).get("receipt_time_notes", ()))
        return (f"{row['symbol']}：{reason}；券商 P={held} 股，账本={ledger} 股，"
                f"当前工作 STP S={current_stop} 股；本地上次确认 STP S={row['confirmed_remaining']} 股；"
                f"GTC orderId={row['broker_order_id'] or '-'} "
                f"permId={row['perm_id'] or '-'}；历史账本入场={source}；"
                f"当日可见成交={visible}，未知项={unknown}；"
                + (notes + "；" if notes else "") +
                f"范围={proof.get('visible_scope', '当日可见，未宣称全账户')}；核验时间={now.isoformat()}；"
                + _CONSTRAINTS)

    def _overnight_proof_fresh(self, now, row):
        """Missing/in-flight/expired proof is pending, never a failed a-d check."""
        proof = self._protection_notice_evidence.get(row["position_id"])
        try:
            return bool(proof and proof["intent_id"] == row["intent_id"] and
                proof["generation"] == int(row["modify_generation"]) and
                proof["socket_generation"] == getattr(self.broker, "query_generation", None) and
                proof.get("authority_binding") == self._protection_verification_binding(row) and
                trading_day(proof["started_at"]) == trading_day(now) and
                trading_day(proof["at"]) == trading_day(now) and
                timedelta(0) <= now - proof["at"] <= timedelta(seconds=60))
        except (ValueError, TypeError, KeyError, OverflowError):
            return False

    def _overnight_refresh_due(self, now, row):
        # This extra pre-stage refresh shares the normal steady round's clock.
        # ACK/fill/restore proofs keep their existing immediate safety schedule.
        prior = self._protection_evidence_attempted_at.get(row["position_id"])
        return bool(not self._protection_verification_wait(now, row) and
            (prior is None or now - prior >= timedelta(seconds=15)))

    def _overnight_record_frozen(self, now, row, stage):
        self._overnight_tp_refresh_pending.pop(row["position_id"], None)
        key = f"overnight_tp_frozen_at_price:{row['position_id']}:{trading_day(now).isoformat()}"
        if self.store.control(key) is not None:
            return  # never reinterpret an unclassified b30 marker
        cause_key = f"overnight_tp_freeze_causes:{row['position_id']}:{trading_day(now).isoformat()}"
        try:
            provenance = json.loads(self.store.control(cause_key) or 'null')
        except (ValueError, TypeError):
            provenance = None
        if not isinstance(provenance, dict) or not isinstance(provenance.get('causes'), list):
            provenance = {'version': 31, 'causes': ['unclassified'],
                'binding': {k: row[k] for k in _BE_BINDING_FIELDS},
                'reasons': ['缺少可验证的冻结原因，保持冻结等待核查']}
        reasons = provenance.get('reasons') or ['冻结原因未分类，保持冻结等待核查']
        pending_only = provenance['causes'] == ['price_confirmation_pending']
        if pending_only and (provenance.get('binding') != {k: row[k] for k in _BE_BINDING_FIELDS} or
                not self._protection_price_pending(row, self.store.position(row['position_id'])) or
                not self._protection_price_request_valid(row, self.store.position(row['position_id']))):
            provenance['causes'].append('unclassified')
            reasons = list(reasons) + ['修改待确认的请求绑定无效，保持冻结']
            provenance['reasons'] = reasons
            pending_only = False
        reason = '修改待确认' if pending_only else '；'.join(reasons)
        self.store.set_control(now, cause_key, json.dumps(provenance, sort_keys=True))
        self.store.set_control(now, key, now.isoformat())
        proof = self._protection_notice_evidence.get(row['position_id'], {})
        own = [{'order_id': o.broker_order_id, 'perm_id': o.perm_id, 'con_id': o.con_id,
            'client_id': o.client_id, 'account_matches': o.account == self.cfg.account,
            'order_ref': o.intent_id, 'status': o.status, 'total_quantity': o.total_quantity,
            'filled': o.filled, 'remaining': o.remaining, 'aux_price': o.aux_price,
            'type': o.order_type, 'side': o.side, 'tif': o.tif, 'outside_rth': o.outside_rth}
            for o in proof.get('own', ())]
        self.store.audit(now, "OVERNIGHT_TP_FROZEN", position_id=row["position_id"],
            symbol=row["symbol"], stage=str(stage), day=trading_day(now).isoformat(),
            reason=reason, reason_codes=provenance['causes'], failure_reasons=reasons,
            modify_generation=row['modify_generation'], pending_qty=row['pending_qty'],
            confirmed_trigger=row['trigger_price'], pending_trigger=row['pending_trigger'],
            own_orders=own, own_orders_fresh=self._overnight_proof_fresh(now, row))

    def _overnight_clear_price_freeze(self, now, row, held, own, others):
        pid = row['position_id']
        day = trading_day(now).isoformat()
        key = f"overnight_tp_frozen_at_price:{pid}:{day}"
        cause_key = f"overnight_tp_freeze_causes:{pid}:{day}"
        if self.store.control(key) is None:
            return False
        try:
            provenance = json.loads(self.store.control(cause_key) or 'null')
            binding = provenance['binding'] if isinstance(provenance, dict) else {}
            causes = provenance.get('causes') if isinstance(provenance, dict) else None
        except (ValueError, TypeError, KeyError):
            return False
        frozen = self.store.control(f"frozen:{row['symbol']}")
        if (not isinstance(provenance, dict) or provenance.get('version') != 31 or
                causes != ['price_confirmation_pending'] or not isinstance(binding, dict) or
                frozen not in (None, 'pending BE broker evidence mismatch') or
                set(binding) != _BE_BINDING_FIELDS or
                any(row[k] != binding[k] for k in _BE_BINDING_FIELDS - {'pending_qty', 'pending_trigger'}) or
                row['requested_qty'] != binding['pending_qty'] or row['trigger_price'] != binding['pending_trigger'] or
                row['state'] != 'WORKING' or row['pending_qty'] is not None or
                not self._overnight_stop_stable(now, row, held, own, others)):
            return False
        ack = self.store.conn.execute(
            "SELECT ts,payload FROM audit WHERE kind='PROTECT_PRICE_CONFIRMED' AND "
            "json_extract(payload,'$.position_id')=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        proof = self._protection_notice_evidence[pid]
        if (ack is None or json.loads(ack['payload'])['generation'] != row['modify_generation'] or
                proof['started_at'] < datetime.fromisoformat(ack['ts'])):
            return False
        if not self._overnight_authority(now, row, held, own, others, explain=False,
                clearing_price_freeze=True):
            return False
        with self.store.tx():
            self.store.delete_control(key)
            self.store.delete_control(cause_key)
            self.store.delete_control(f"overnight_tp_failure:{pid}")
            if frozen is not None:
                self.store.delete_control(f"frozen:{row['symbol']}")
            self.store.audit(now, 'OVERNIGHT_TP_FREEZE_CLEARED', position_id=pid,
                symbol=row['symbol'], day=day, reason='仅保本修改待确认；已确认并通过新一轮完整 a-d 核验')
        detail = f"{row['symbol']}：保本修改已确认，新一轮完整 a-d 核验通过；当天仅因修改待确认的自动止盈冻结已解除。"
        self.alerter.send(now, 'INFO', 'overnight TP freeze cleared '+pid+' '+day, detail,
            card=AlertCard('🟢 INFO · 过夜止盈冻结已解除 · '+row['symbol'], INFO_GREEN,
                (('核验结果', detail),)))
        return True

    def _overnight_refresh_tp_proof(self, position_id, stage):
        """Queued read-only refresh; never nest a query inside exit evaluation."""
        row = self.store.protection(position_id)
        pos = self.store.position(position_id)
        if (row is None or pos is None or pos["status"] != "OPEN" or
                row["tp_stage"] is not None or not self._overnight_position(row)):
            self._overnight_tp_refresh_pending.pop(position_id, None)
            return
        if self._protection_query_position is not None:
            return  # reuse the in-flight round; a later fast pass retries if needed
        now = self.clock.now()
        if self._overnight_proof_fresh(now, row):
            return
        if not self._overnight_refresh_due(now, row):
            return
        previous_budget = self._protection_steady_evidence_budget
        try:
            self._protection_steady_evidence_budget = True
            try:
                held, own, others, full_exec = self._protection_evidence(row)
            finally:
                self._protection_steady_evidence_budget = previous_budget
        except Exception as exc:
            now = self.clock.now()
            if self._protection_discard_superseded_evidence(now, row, exc, steady=True):
                return
            self._protection_query_failure(now, row, exc, steady=True)
            if not self._protection_query_unavailable(exc):
                self._overnight_failure(now, row, "当日新鲜持仓/工作单/成交核验失败：" + str(exc))
                self._overnight_record_frozen(now, row, stage)
            return
        now = self.clock.now()
        row = self.store.protection(position_id)
        if row is None or not self._overnight_proof_fresh(now, row):
            return  # a callback changed the binding; obtain a new round, no false failure
        proof = self._protection_notice_evidence[position_id]
        if (self._overnight_stop_stable(now, row, held, own, others) and
                (full_exec or (proof.get("prior_day_execution_history") and
                               proof.get("current_day_executions_complete")))):
            self._protection_finish_evidence_episode(now, row,
                "当日新鲜持仓、工作单、成交查询恢复；原 STP 强身份及足额覆盖已确认，未授予完整历史权限")
            self.store.update_protection(now, position_id, confirmed_at=now)
            row = self.store.protection(position_id)
        if not self._overnight_authority(now, row, held, own, others):
            self._overnight_record_frozen(now, row, stage)

    def _overnight_refresh_decision_due(self, now):
        # A completed requested round must get a decision before the next
        # steady query invalidates it; the two 15s cadences can otherwise align.
        if self._protection_query_position is not None:
            return False
        return any(row is not None and self._overnight_proof_fresh(now, row)
            for row in (self.store.protection(pid)
                        for pid in self._overnight_tp_refresh_pending))

    def _overnight_before_tp_stage(self, now, row, stage):
        if not self._overnight_position(row, now) or row["tp_stage"] is not None:
            return True
        pid = row["position_id"]
        if self.cfg.protection.overnight_tp == "constraint" and (
                self._protection_query_position is not None or
                not self._overnight_proof_fresh(now, row)):
            self._overnight_tp_refresh_pending[pid] = str(stage)
            if self._protection_query_position is None and self._overnight_refresh_due(now, row):
                self._enqueue("overnight proof " + pid,
                    lambda: self._overnight_refresh_tp_proof(pid, stage))
            return False
        proof = self._protection_notice_evidence.get(pid, {})
        allowed = self._overnight_authority(now, row, proof.get("held", -1),
            proof.get("own", []), proof.get("others", []), announce=False)
        if allowed:
            self._overnight_tp_refresh_pending.pop(pid, None)
            return True
        self._overnight_record_frozen(now, row, stage)
        return False

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

    def _overnight_authority(self, now, row, held, own, others, *, workflow=False, announce=False,
                             explain=True, clearing_price_freeze=False, pending_observation=False):
        """Consume the current fresh snapshot only; never manufacture full_exec."""
        try:
            pos = self.store.position(row["position_id"])
            if not workflow and self.cfg.protection.overnight_tp != "constraint":
                raise ValueError("overnight_tp=freeze：默认只保留止损，过夜自动止盈/保本受限制")
            if not self._overnight_proof_fresh(now, row):
                return False  # wait for a complete current round, including BE/flow consumers
            history = self._overnight_history(row, now)
            proof = self._protection_notice_evidence[row["position_id"]]
            if held != int(pos["qty_open"]):
                raise ValueError("券商持仓 P 与账本 qty_open 不一致")
            if proof.get("overnight_visible_error"):
                raise ValueError("当日可见成交查询失败：" + proof["overnight_visible_error"])
            if not proof.get("current_day_executions_complete"):
                raise ValueError("当日账本成交未全部出现在新鲜成交查询中")
            visible = {}
            today = trading_day(now)
            scoped = self._overnight_current_scope(row, now) is not None
            if (proof.get("account_execution_scope_required") or
                    proof.get("account_execution_scope") is not None) and not scoped:
                raise ValueError("本轮当日可见成交查询缺少完整、新鲜、同代且显式 UTC 当日范围的证明")
            if scoped:
                # The complete, fresh, explicitly scoped account query proves
                # its row count even if a conflicting legacy row rejects d below.
                proof["overnight_visible_count"] = len({
                    fill.exec_id for fill in proof.get("account_fills") or ()
                    if fill.symbol == row["symbol"]})
            rows = []
            for source, source_rows in (("account", proof.get("account_fills") or ()),
                                        ("client", proof.get("client_fills") or ())):
                for fill in source_rows:
                    if fill.symbol != row["symbol"]:
                        continue
                    booked = self.store.conn.execute("SELECT * FROM fills WHERE exec_id=?", (fill.exec_id,)).fetchone()
                    # Day-only/receipt-time exceptions do not erase explicit
                    # native ownership or contract contradictions, even in a
                    # wide historical result. Missing optional conId stays missing.
                    pinned_conid = self.store.control(f"conid:{row['symbol']}")
                    intent_conflict = bool(booked is not None and fill.intent_id and
                                           fill.intent_id != booked["intent_id"])
                    contract_conflict = bool(type(fill.con_id) is int and fill.con_id > 0 and
                        ((row["con_id"] is not None and fill.con_id != row["con_id"]) or
                         (pinned_conid is not None and str(fill.con_id) != pinned_conid)))
                    if intent_conflict or contract_conflict:
                        proof["overnight_unknown"] = fill.exec_id
                        raise ValueError("当日可见成交存在明确的原生 intent/合约身份冲突：" + fill.exec_id)
                    if (source == "client" and scoped and fill.time_uncertain and booked is not None and
                            booked["trading_day"] < today.isoformat() and booked["intent_id"] and
                            self.store.intent(booked["intent_id"]) is not None and
                            self._overnight_visible_economics(fill) == self._overnight_fill_economics(booked)):
                        marker = self.store.control(f"execution_time_unverified:{fill.exec_id}")
                        if marker is not None:
                            historical_receipt_before(booked, marker, row["con_id"], now,
                                pinned_con_id=self.store.control(f"conid:{booked['symbol']}"))
                        continue
                    if fill.time.tzinfo is None or trading_day(fill.time) > today:
                        raise ValueError("当日可见成交时间无效")
                    if trading_day(fill.time) != today:
                        continue
                    rows.append((fill, booked))
            if not scoped:
                proof["overnight_visible_count"] = len({item.exec_id for item, _ in rows})
            for fill, booked in rows:
                economics = self._overnight_visible_economics(fill)
                day_only = bool(booked is not None and fill.exec_id in history["today_time_ids"])
                economic = economics + (None if day_only else fill.time,)
                if fill.exec_id in visible and visible[fill.exec_id] != economic:
                    proof["overnight_unknown"] = fill.exec_id
                    raise ValueError("当日同 execId 的可见成交经济字段冲突")
                visible[fill.exec_id] = economic
                expected = (self._overnight_fill_economics(booked) +
                            (None if day_only else datetime.fromisoformat(booked["time"]),)
                            if booked is not None else None)
                if (booked is None or not booked["intent_id"] or
                        self.store.intent(booked["intent_id"]) is None or
                        (fill.intent_id and fill.intent_id != booked["intent_id"]) or expected != economic):
                    proof["overnight_unknown"] = fill.exec_id
                    raise ValueError("当日可见成交无法归属或与账本经济身份不一致：" + fill.exec_id)
            proof["overnight_unknown"] = "0"
            if (self.store.control(f"frozen:{row['symbol']}") is not None and not clearing_price_freeze) or self._tp_paused(
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
                price_confirmation = (self._protection_price_request_valid(row, pos) if pending_observation
                    else self._overnight_price_confirmation(row, pos, held, history))
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
                "price_confirmation": bool(not workflow and not pending_observation and price_confirmation),
                "pending_observation": pending_observation}
            if history["today_time_ids"]:
                self._overnight_record_today_basis(now, row, history)
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
            if explain:
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

    def _overnight_price_confirmation(self, row, pos, held, history, *, request_only=False):
        raw = self.store.control(f"overnight_be_flow:{row['position_id']}")
        if raw is None:
            return False
        try:
            value = json.loads(raw)
            # The persisted request is a fact from its original day. On a
            # later day recheck that original prefix, while current history
            # and all current-day sources are independently proved above.
            request_day = datetime.fromisoformat(value["day"]).date()
            original_history = [{k: v for k, v in fill.items() if k != "ledger_rowid"}
                for fill in history["records"] if fill["trading_day"] < value["day"]]
            proof = self._protection_notice_evidence.get(row["position_id"], {})
            if not request_only and row["position_id"] not in self._protection_price_requests and (
                    len(proof.get("own", ())) != 1 or
                    not same_cent_price(proof["own"][0].aux_price, row["pending_trigger"])):
                return False
            return (row["state"] == "PRICE_PENDING" and
                request_day <= trading_day(self.clock.now()) and
                value["historical_digest"] == _digest(original_history) and
                isinstance(value["binding"], dict) and set(value["binding"]) == _BE_BINDING_FIELDS and
                all(row[k] == value["binding"][k] for k in _BE_BINDING_FIELDS) and
                row["pending_qty"] == held + self.store.intent_filled_qty(row["intent_id"]) and
                row["pending_trigger"] == pos["breakeven_stop"] and pos["tp1_done"] and
                row["confirmed_remaining"] == row["pending_max_remaining"] == held)
        except (ValueError, TypeError, KeyError):
            return False

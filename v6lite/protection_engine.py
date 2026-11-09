"""Conservative GTC stop-market coordination for managed V6-Lite holdings.

Only the first protective submission may run in an IB execution callback: it
uses the just-booked execution and a pre-persisted order plan, and makes no
blocking broker query.  Every increase and every TP requires fresh positions,
all-client working orders, and execution evidence outside the callback.
"""

from __future__ import annotations

import json
import logging
import math
import time
import re
from dataclasses import replace
from datetime import datetime, timedelta

from v6lite.alerts import AlertCard, P0_RED, P1_ORANGE
from v6lite.broker.base import BrokerError
from v6lite.market_calendar import is_rth, trading_day
from v6lite.models import IntentStatus, OrderRequest, ProtectionRequest, Purpose, Side
from v6lite.protection import protection_prices, same_cent_price
from v6lite.protection_incidents import ProtectionIncidentMixin
from v6lite.protection_confirmations import ProtectionConfirmationMixin
from v6lite.final_tp import FinalTpMixin
from v6lite.overnight_evidence import OvernightEvidenceMixin
from v6lite.protection_cancellations import ProtectionCancellationMixin
from v6lite.tp_evidence import TpEvidenceMixin, price_rejection
from v6lite.store import new_id

log = logging.getLogger("v6lite.protection")

_WORKING = {"Submitted", "PreSubmitted"}
_TERMINAL = {"Cancelled", "ApiCancelled", "Inactive", "Filled"}


class ProtectionEvidenceSuperseded(BrokerError):
    """A strict own TP delta invalidated this round; never write from it."""


class ProtectionEvidenceContradiction(BrokerError):
    """A same-round TP delta also contained an independent unsafe fact."""


class ProtectionEvidenceQueryError(BrokerError):
    """A broker request failed, distinct from contradictory broker evidence."""


class ProtectionMixin(FinalTpMixin, ProtectionConfirmationMixin, OvernightEvidenceMixin, ProtectionIncidentMixin, ProtectionCancellationMixin, TpEvidenceMixin):
    def _preflight_protection_deadlines(self, now: datetime) -> None:
        """Startup/reconnect timer: alert only, no broker mutations.

        Account/client/order identity has not yet been fully reverified, so a
        10-second TP deadline here becomes an explicit crash-window P0.
        """
        if not self.cfg.protection.enabled or self.dry_run:
            return
        for row in self.store.protections():
            pos = self.store.position(row["position_id"])
            if pos is None or pos["status"] != "OPEN":
                continue
            if row["state"] == "REJECTED":
                continue
            for gap, warning, critical in (("A", self.cfg.protection.gap_a_p1_seconds,
                                            self.cfg.protection.gap_a_p0_seconds),
                                           ("B", self.cfg.protection.gap_b_cancel_seconds,
                                            self.cfg.protection.gap_b_p0_seconds)):
                started = row[f"gap_{gap.lower()}_started_at"]
                if not started:
                    continue
                secs = (now - datetime.fromisoformat(started)).total_seconds()
                for tag, threshold, level in (("P1", warning, "P1" if gap == "A" else "P0"),
                                              ("P0", critical, "P0")):
                    key = (row["position_id"], f"preflight-{gap}", started, tag)
                    if secs >= threshold and key not in self._protection_deadline_fired:
                        self._protection_page(now, level,
                                              "gap A warning" if gap == "A" and tag == "P1" else
                                              "gap A overdue" if gap == "A" else "preflight B overdue",
                                              row, f"未经核实的接管期间 {gap} 空窗 {secs:.1f} 秒；"
                                              "身份核验前禁止自动券商写入")
                        self._protection_deadline_fired.add(key)

    @staticmethod
    def _protection_terminal_display_fact(raw_status, error_code, reason):
        """Native terminal facts for presentation only; never execution authority."""
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), reason or "").lower()
        cancelled = bool(re.search(r"\bcancell?ed\b|已(?:被)?(?:撤销|撤单|取消)|系统(?:撤销|撤单|取消)|"
                                  r"订单.*(?:被取消|被撤销|被撤单)", text))
        # A refusal explanation (cannot accept/无法接纳) can explain a real
        # cancellation. Only an explicit rejected-order fact contradicts it.
        rejected = bool(re.search(r"\border\s+(?:was\s+|has been\s+)?rejected\b|"
                                 r"(?:明确|已)(?:被)?拒绝|被拒绝|订单.*(?:被拒|已拒)", text))
        if error_code == 202:
            return "cancelled"  # IB's cancellation code takes precedence over Inactive
        if cancelled and (rejected or error_code == 201):
            return "unknown"
        if error_code == 201 or rejected:
            return "rejected"
        if cancelled or raw_status in {"Cancelled", "ApiCancelled"}:
            return "cancelled"
        return "unknown"

    def _protection_page(self, now: datetime, level: str, kind: str, row, detail: str) -> None:
        notice = self._protection_terminal_notice(row)
        if (level == "P1" and notice and notice.get("p0_attempted") and
                kind in {"gap A warning", "trigger crossing", "unknown", "rejected", "broker cancelled"}):
            return None
        if notice and notice.get("cancel_class") and kind in {
                "unknown", "rejected", "gap A overdue", "evidence incomplete", "shutdown incomplete"}:
            kind = "broker cancelled"
        if kind not in {"unknown", "rejected", "broker cancelled", "gap A overdue"}:
            notice = None  # independent SELL/trigger incidents do not inherit cancel reasons
        cancel_proof = self._protection_cancel_quantity_proof(now, row) if kind == "broker cancelled" else None
        terminal_fact = (self._protection_terminal_display_fact(notice.get("raw_status"),
                         notice.get("error_code"), notice.get("reason"))
                         if kind == "broker cancelled" and notice else None)
        titles = {
            "gap A warning": "入场后保护单尚未确认",
            "gap A overdue": "入场持仓未获券商保护",
            "gap B overdue": "止盈后保护缺口未恢复",
            "preflight B overdue": "接管前止盈超时，卖单待人工核对",
            "external sell": "托管标的出现外部卖单",
            "rejected": "券商拒绝了保护单，该持仓目前没有券商端保护",
            "broker cancelled": "券商已取消保护单",
            "unknown": "保护单状态无法核实",
            "legacy type": "旧类型保护单，需人工处理",
            "tp not sent": "止盈单未发出，正在恢复保护",
            "trigger crossing": "已到止损价，券商止损市价单应在成交",
            "trigger unfilled": "止损已触发但未成交",
            "trigger suspected": "疑似触发或保护状态不明",
            "invalid price": "成交后无法建立有效保护价",
            "invalid entry price": "无法计算保护价，买单已拦截",
            "evidence incomplete": "保护单三项证据不完整",
            "shutdown incomplete": "停机时保护单未核实",
            "late tp fill": "止盈迟到成交，保护单已减量",
        }
        actions = {
            "legacy type": "1）在同一 TWS 人工核对旧保护单与持仓；2）不要把 STP LMT 改认成 STP，不自动撤单、改价或重挂；3）由 John 处置并保留旧状态证据，未经复核保持入场冻结。",
            "gap A warning": (f"1）在 TWS 核对保护单 orderId 与剩余量；2）不要新开仓或另发卖单；"
                              f"3）从空窗起点累计 {self.cfg.protection.gap_a_p0_seconds} 秒仍无券商确认，"
                              "再按 P0 处置。"),
            "gap A overdue": "1）在 TWS 核对持仓与保护单是否真实存在；2）不要另下卖单；3）由 John 处理裸仓并保持新开仓冻结。",
            "gap B overdue": "1）先撤销在途 TP 并核实撤单及成交；2）不要另下卖单；3）再核对并恢复同标的保护单剩余量。",
            "preflight B overdue": "1）如果 Lite 在 5 分钟内没有恢复连接，在 TWS 手动撤销 TP 单并核对成交；2）不要另下卖单；3）接管后先核实 TP 已撤，再恢复保护单。",
            "external sell": "1）先在 TWS 核对外部卖单来源和成交：不需要就撤掉，需要就保留；2）保留时不要再下任何卖单，Lite 已冻结该标的，持仓未平前不再做止盈，原保护单仍可能在途；3）平仓并核实无工作单后，由 John 对账并执行 release AAPL（把 AAPL 换成卡片标的）。",
            "rejected": "1）在同一 TWS 用限价单平仓；2）确认该标的 0 股、无在途卖单，并等待成交静默期；3）在本机执行 release，身份或新鲜证据不足时保持拒绝；不要手改状态库、新开仓或自动重挂保护单。",
            "unknown": "1）在 TWS 用 orderRef、orderId、permId 核对真实订单；2）不要另下卖单或加量；3）取得持仓、工作单、成交三项证据后再决定。",
            "broker cancelled": "1）先按 Ctrl+C 停止 Lite；2）在同一 TWS 核对持仓和所有工作卖单，再按当前 bid 用限价单平仓或人工建立保护，避免重复卖单；3）重新核对持仓和工作单。不要点击 TWS 的重新提交/IBALGO 建议；不要自动改价重挂保护单。只有确认 0 股、无在途卖单、成交静默期和原 release 强门槛均满足后才执行 release；身份或证据不足保持拒绝，不要手改状态库、新开仓或自动重挂保护单。",
            "tp not sent": ("1）目前等待 Lite 用新鲜三项证据自动恢复保护；2）不要另下卖单或手动增加保护股数；"
                            f"3）从 B 空窗起点累计 {self.cfg.protection.gap_b_p0_seconds} 秒仍未恢复，按 P0 卡片核对 TP 和保护单。"),
            "trigger crossing": ("1）在 TWS 观察同一保护单的成交及剩余量；2）不要另下卖单；"
                                 f"3）常规交易时段累计 {self.cfg.protection.trigger_unfilled_p0_seconds} 秒仍有剩余，"
                                 "按 P0 立即处置。"),
            "trigger unfilled": "1）在同一 TWS 核对本卡 orderId 的成交、剩余量及是否停牌或交易暂停；2）不要另发卖单或自动重挂，先核实原 STP 状态；3）由 John 联系券商处理异常，由 John 暂停新入场并完成订单和持仓对账。",
            "trigger suspected": "1）在 TWS 核对价格、保护单和成交；2）不要另下卖单；3）确认券商状态后由 John 处置。",
            "invalid price": "1）在 TWS 核对真实持仓和可用保护价格；2）不要另下卖单；3）由 John 人工处理并维持新开仓冻结。",
            "invalid entry price": "1）确认买单已被 Lite 拦截且没有券商订单；2）不要绕过门控下单；3）修复 ATR 或价格证据后重新申请。",
            "evidence incomplete": "1）在 TWS 分别核对持仓、所有工作单、成交；2）不要另下卖单或增加保护数量；3）证据齐全且一致后再解除冻结。",
            "shutdown incomplete": "1）在 TWS 确认 GTC 保护单是否保留及其剩余量；2）不要另下卖单；3）重启后完成三源核对，异常时由 John 人工处理。",
            "late tp fill": "1）在 TWS 核对迟到 TP 成交及当前保护单剩余量；2）不要另下卖单；3）确认 Lite 已只减保护数量并重新对账。",
        }
        def field(name, default=None):
            try:
                return row[name]
            except (KeyError, IndexError):
                return default

        symbol = field("symbol", "待核实")
        pos = self.store.position(field("position_id")) if field("position_id") else None
        held = int(pos["qty_open"]) if pos is not None and pos["status"] == "OPEN" else 0
        confirmed = int(field("confirmed_remaining", 0) or 0)
        possible = max(confirmed, int(field("pending_max_remaining", 0) or 0))
        if cancel_proof:
            held, confirmed = cancel_proof[:2]
            possible = confirmed
        working_at = getattr(self, "_working_at", None)
        fresh = working_at is not None and timedelta(0) <= now - working_at <= timedelta(seconds=5)
        own_order = self._working.get(field("intent_id")) if fresh else None
        broker_confirmed = bool(kind not in {"shutdown incomplete", "unknown", "rejected", "broker cancelled",
                                             "evidence incomplete"} and
                                field("state") == "WORKING" and field("pending_qty") is None and
                                field("confirmed_at") and own_order is not None and
                                self._protection_order_valid(row, own_order) and
                                math.ceil(own_order.remaining) == confirmed)
        by_ref: dict[str, tuple[int, bool, object]] = {}
        if fresh:
            for order in self._working.values():
                if order.intent_id == field("intent_id") or order.symbol not in (symbol, "*") or \
                        order.side not in ("SELL", ""):
                    continue
                qty = self._protection_other_remaining(order)
                by_ref[order.intent_id] = (qty, order.ours, order)
            for intent in self.store.active_intents():
                if intent["symbol"] != symbol or intent["side"] != Side.SELL or \
                        intent["intent_id"] == field("intent_id"):
                    continue
                remaining = max(0, int(intent["quantity"]) -
                                self.store.intent_filled_qty(intent["intent_id"]))
                old, _, order = by_ref.get(intent["intent_id"], (0, True, None))
                # A booked fill caps the possible remainder of the same DAY TP.
                if order is not None and order.ours:
                    remaining = min(old, remaining)
                by_ref[intent["intent_id"]] = (remaining, True, order)
        t = sum(q for q, ours, _ in by_ref.values() if ours) if fresh else None
        x = sum(q for q, ours, _ in by_ref.values() if not ours) if fresh else None
        if cancel_proof:
            t, x = cancel_proof[2:]
        other_text = (f"止盈在途={t} 股 外部卖单在途={x} 股" if t is not None and x is not None
                      else "止盈在途=待核实 外部卖单在途=待核实")
        if t is None or x is None:
            missing_text = oversell_text = "待核实"
        else:
            over = max(0, possible + t + x - held)
            oversell_text = str(over) if broker_confirmed else f"待核实（可能上界{over}）"
            missing_text = (str(max(0, held - confirmed - t - x)) if broker_confirmed
                            else f"待核实（可能范围 {max(0, held - possible - t - x)}–"
                                 f"{max(0, held - t - x)}）")
            if kind == "rejected" and field("state") == "REJECTED" and possible == 0:
                # Definite refusal proves this order supplies no coverage;
                # only a fresh other-SELL snapshot permits an exact count.
                missing_text = str(max(0, held - t - x))
        if kind == "broker cancelled":
            # T/X reserve SELL capacity; they do not supply stop protection S.
            missing_text = str(max(0, held - confirmed)) if cancel_proof else "待核实"
            oversell_text = str(max(0, possible + t + x - held)) if cancel_proof else "待核实"
        tp_id = field("tp_intent_id")
        tp_intent = self.store.intent(tp_id) if tp_id else None
        tp_broker_id = tp_intent["broker_order_id"] if tp_intent is not None else None
        tp_remaining = (max(0, int(tp_intent["quantity"]) - self.store.intent_filled_qty(tp_id))
                        if tp_intent is not None else None)
        external = ", ".join(f"{iid} orderId={getattr(order, 'broker_order_id', '-') or '-'} {qty} 股"
                             for iid, (qty, ours, order) in by_ref.items() if not ours)
        evidence_at = working_at.isoformat() if fresh else "过期或缺失"
        confirmed_at = field("confirmed_at") or "未获券商确认"
        current_text = "当前快照已核实" if broker_confirmed else "当前保护状态待核实"
        if kind == "rejected":
            current_text = "券商已明确拒绝该保护请求"
        elif kind == "broker cancelled":
            fact_text = {"rejected": "券商已拒绝该保护请求", "cancelled": "券商已回报取消",
                         "unknown": "券商终态事实待核实：回报类型与原因不明或矛盾"}[terminal_fact]
            current_text = (fact_text + "；完整终态身份已核验" if notice.get("complete_identity") else
                            fact_text + "；终态身份尚未获完整授权核验或原人工 hold 未解除；保持人工核验，不授予释放权限")
        confirmed_text = str(confirmed)
        if kind == "broker cancelled" and not cancel_proof and field("state") != "REJECTED":
            confirmed_text = f"待核实（上次确认 {confirmed}）"
        if notice:
            detail += (f"；券商状态={notice['raw_status']} code={notice['error_code']}；"
                       "终态诊断与执行权限独立核验")
        stop_label = ("止损价（触发后市价成交）" if field("order_type", "STP") == "STP" else
                      "旧合同止损价（STP LMT，仅供人工核对）")
        body = (f"{symbol} 持仓数量={held} 股 保护单已确认={confirmed_text} 股 保护单可能={possible} 股 "
                f"{other_text} 未保护={missing_text} 股 可能超卖={oversell_text} 股；{current_text}；"
                f"orderRef={field('intent_id') or '-'} orderId={field('broker_order_id') or '-'} "
                f"permId={field('perm_id') or '-'} TP={tp_id or '-'} "
                f"TP orderId={tp_broker_id or '-'} TP剩余={tp_remaining if tp_remaining is not None else '待核实'} 股；"
                f"外部订单={external or '无券商新快照'}；{stop_label}={float(field('trigger_price', 0) or 0):.2f}；"
                f"上次确认={confirmed_at} "
                f"工作单证据时间={evidence_at}；{detail}")
        impacts = {
            "legacy type": "旧类型保护单不能作为当前 STP 保护证据；必须人工处理，全部入场保持阻断。",
            "gap A warning": (f"保护确认仍未到达，未保护 {missing_text} 股；"
                              f"从空窗起点累计 {self.cfg.protection.gap_a_p0_seconds} 秒才升级 P0。"),
            "gap A overdue": f"持仓存在裸仓风险，未保护 {missing_text} 股；新开仓继续冻结。",
            "gap B overdue": f"止盈流程尚未恢复保护，止盈在途 {tp_remaining if tp_remaining is not None else '待核实'} 股；超时须核实撤单。",
            "preflight B overdue": "接管前连接和订单身份未验证，不能把超时 TP 当成已撤单。",
            "external sell": f"外部卖单与 Lite 卖单可能叠加，可能超卖 {oversell_text} 股。",
            "rejected": f"被拒的保护请求无效，未保护 {missing_text} 股不能据此当作已覆盖。",
            "unknown": f"保护订单身份或状态未知，未保护 {missing_text} 股仍须券商核实。",
            "broker cancelled": f"有效 STP 保护 S={confirmed_text} 股，未保护 P−S={missing_text} 股；止盈和外部卖单仅列卖出容量，不能当作止损保护。新开仓继续冻结，Lite 不自动重挂。",
            "tp not sent": ("止盈单确定没有发出，Lite 正在按三项证据恢复保护；"
                            f"原 B 空窗持续计时，累计 {self.cfg.protection.gap_b_p0_seconds} 秒仍未恢复则升级 P0。"),
            "trigger crossing": "止损价格已触发本地监测；券商是否已触发及成交仍须核对。",
            "trigger unfilled": "止损市价单超过时限仍有剩余，可能停牌、交易暂停或券商异常；持仓仍暴露于继续下跌。",
            "trigger suspected": "疑似触发止损，但现有证据不足以认定券商已执行。",
            "invalid price": "成交后保护价格无效，现有持仓不能凭该价格获得保护。",
            "invalid entry price": "保护价格无法建立，买单已拦截且本次不开仓。",
            "evidence incomplete": "持仓、工作单和成交三项证据不完整，不能安全增加保护数量。",
            "shutdown incomplete": "停机时保护单存在性未核实；重启前不能把旧快照当成仍在途。",
            "late tp fill": f"迟到止盈成交可能使保护单超额，可能超卖 {oversell_text} 股；减量待券商确认。",
        }
        impact = impacts.get(kind, impacts["unknown"])
        action = actions.get(kind, actions["unknown"])
        if kind == "external sell":
            action = action.replace("release AAPL", f"release {symbol}").replace("（把 AAPL 换成卡片标的）", "")
        if kind == "preflight B overdue":
            action = action.replace("TP 单并", f"TP 单 {tp_broker_id or tp_id or '待核实'} 并")
        title = ({"rejected": "券商已拒绝保护单", "cancelled": "券商已取消保护单",
                  "unknown": "券商保护终态待核实"}[terminal_fact]
                 if terminal_fact is not None else titles.get(kind, "券商端保护异常"))
        if kind == "late tp fill":
            if field("state") in {"UNKNOWN", "REJECTED"}:
                title = "止盈迟到成交，保护单状态无法核实"
                impact = f"迟到止盈成交可能使保护单超额，可能超卖 {oversell_text} 股；保护状态未知，不能认定减量成功。"
            elif field("state") == "CANCEL_PENDING":
                title = "止盈迟到成交，保护单撤销待券商确认"
                impact = f"迟到止盈成交可能使保护单超额，可能超卖 {oversell_text} 股；撤单尚待券商确认。"
            elif field("state") in {"MODIFY_PENDING", "REDUCE_PENDING"}:
                title = "止盈迟到成交，保护单减量待券商确认"
            else:
                impact = f"迟到止盈成交后的保护数量已获券商确认；当前可能超卖 {oversell_text} 股，仍须核对其余卖单。"
        card = AlertCard(("🔴 P0 · " if level == "P0" else "🟠 P1 · ") + title,
                         P0_RED if level == "P0" else P1_ORANGE,
                         (("发生了什么", body), ("影响", impact), ("你要做什么", action)))
        if kind in {"rejected", "broker cancelled"}:
            card = replace(card, fields=tuple((name, value.replace("执行 release，", f"执行 release {symbol}，").replace(
                "才执行 release；", f"才执行 release {symbol}；")) for name, value in card.fields))
        cards = [card]
        if notice:
            reason = notice.get("reason") or "券商未提供原因"
            # Original diagnostic bytes remain durable and in the full alert
            # log. UTF-16-sized segments avoid Discord's 1024-field clipping.
            chunks, chunk, units = [], [], 0
            for char in reason:
                width = 2 if ord(char) > 0xFFFF else 1
                if units + width > 900:
                    chunks.append("".join(chunk))
                    chunk, units = [], 0
                chunk.append(char)
                units += width
            chunks.append("".join(chunk))
            first = ("券商原因原文" if len(chunks) == 1 else f"券商原因原文 1/{len(chunks)}")
            cards[0] = replace(card, fields=(card.fields[0], (first, chunks[0]), *card.fields[1:]))
            for number, text in enumerate(chunks[1:], 2):
                cards.append(AlertCard(card.title + f" · 原因 {number}/{len(chunks)}", card.color,
                    (("事件", f"{symbol} orderRef={field('intent_id') or '-'}"),
                     (f"券商原因原文 {number}/{len(chunks)}", text))))
            body += "；券商原因原文=" + reason
        if len(cards) == 1:
            ticket = self._deliver_protection_notice(now, level, kind, row, body, cards[0])
        else:
            ticket = self._deliver_protection_notice(now, level, kind, row, body, cards[0], extra_cards=cards[1:])
        if level == "P0":
            try:
                self._test_timeout_event(now,row,"P0")
            except Exception:
                log.exception("test timeout P0 diagnostic failed after protection notice")
        return ticket

    def _protection_type_allowed(self, now, row) -> bool:
        if row["order_type"] == "STP":
            return True
        self.store.set_control(now, f"frozen:{row['symbol']}", "legacy protection type requires manual treatment")
        self._protection_page(now, "P0", "legacy type", row,
                              "STP LMT 属于历史保护证据；禁止自动转换、撤单或替换")
        self._protection_emergency = True
        return False

    def _protection_plan_for_entry(self, req: OrderRequest, atr: float | None):
        if not self.cfg.protection.enabled or self.dry_run or atr is None:
            return None
        levels = self.store._levels_fn(req.limit_price, atr)
        trigger, limit = protection_prices(levels.stop)
        return new_id("v6l-protect"), trigger, limit

    def _protection_on_entry_fill(self, now: datetime, position_id: str) -> None:
        if not self.cfg.protection.enabled or self.dry_run:
            return
        pos = self.store.position(position_id)
        if pos is None or pos["status"] != "OPEN":
            return
        row = self.store.protection(position_id)
        if row is None:
            try:
                trigger, limit = protection_prices(pos["stop"])
                self.store.create_protection_plan(now, position_id, new_id("v6l-protect"),
                                                  pos["symbol"], trigger, limit)
                self.store.update_protection(now, position_id, account=self.cfg.account,
                                             client_id=self.cfg.client_id,
                                             con_id=int(self.store.control(f"conid:{pos['symbol']}") or 0))
            except Exception as exc:
                self.store.raise_anomaly(now, f"protect_plan:{position_id}", str(exc)[:160])
                missing = {"position_id": position_id, "symbol": pos["symbol"], "confirmed_remaining": 0,
                           "pending_max_remaining": 0, "tp_intent_id": None,
                           "intent_id": "unavailable", "broker_order_id": None, "perm_id": None,
                           "trigger_price": float(pos["stop"]), "limit_price": 0.0}
                self._protection_page(now, "P0", "invalid price", missing, type(exc).__name__)
                return
            row = self.store.protection(position_id)
        if not self._protection_type_allowed(now, row):
            return
        if row["state"] == "PLANNED":
            if getattr(self, "_recovering", False):
                self._protection_priority_due = True
                return
            try:
                actual_trigger, actual_limit = protection_prices(pos["stop"])
            except Exception as exc:
                self._protection_page(now, "P0", "invalid price", row, type(exc).__name__)
                return
            if (actual_trigger, actual_limit) != (row["trigger_price"], row["limit_price"]):
                self.store.update_protection(now, position_id, trigger_price=actual_trigger,
                                             limit_price=actual_limit)
                self.store.audit(now, "PROTECT_PREPLAN_ADJUSTED", position_id=position_id,
                                 original_trigger=row["trigger_price"], actual_trigger=actual_trigger)
                row = self.store.protection(position_id)
            # This branch is deliberately free of broker queries.  execDetails
            # is the first authoritative execution; the order plan and unique
            # orderRef were committed before the BUY left this process.
            self._protection_submit_first(now, row, int(pos["qty_open"]))
        elif int(pos["qty_open"]) > int(row["confirmed_remaining"]):
            # A later partial BUY is not enough evidence to increase a GTC.
            # Its clock starts now; the high-priority outer loop takes three
            # fresh sources before a same-id modification.
            if row["gap_a_started_at"] is None:
                self.store.update_protection(now, position_id, gap_a_started_at=now)
            self._protection_priority_due = True

    def _protection_submit_first(self, now: datetime, row, quantity: int) -> None:
        if self.dry_run:
            log.info("[DRY_RUN] WOULD_PROTECT %s qty=%d", row["symbol"], quantity)
            return
        if quantity <= 0 or not self._protection_type_allowed(now, row):
            return
        req = ProtectionRequest(row["intent_id"], row["position_id"], row["symbol"], quantity,
                                float(row["trigger_price"]), None)
        intent = OrderRequest(req.intent_id, req.symbol, Side.SELL, quantity,
                              req.limit_price, Purpose.PROTECT, req.position_id)
        if self.store.intent(req.intent_id) is None:
            self.store.create_intent(now, intent, atr=None, count_entry_day=None)
        self.store.update_protection(now, req.position_id, requested_qty=quantity,
                                     pending_qty=quantity, pending_max_remaining=quantity,
                                     pending_trigger=req.trigger_price, pending_limit=req.limit_price,
                                     state="SUBMIT_PENDING", gap_a_started_at=now,
                                     account=self.cfg.account, client_id=self.cfg.client_id,
                                     con_id=int(self.store.control(f"conid:{req.symbol}") or 0))
        try:
            ack = self.broker.submit_stop(req)
        except BrokerError as exc:
            self.store.audit(self.clock.now(), "PROTECT_BROKER_ERROR", source="submit_stop",
                intent_id=req.intent_id, position_id=req.position_id, symbol=req.symbol,
                full_error=str(exc), error_type=type(exc).__name__, maybe_sent=exc.maybe_sent)
            self.store.update_intent(now, req.intent_id, status=IntentStatus.UNKNOWN,
                                     note="protection submit uncertain" if exc.maybe_sent else "protection rejected")
            self.store.update_protection(now, req.position_id,
                                         state="UNKNOWN" if exc.maybe_sent else "REJECTED",
                                         pending_max_remaining=quantity if exc.maybe_sent else 0,
                                         pending_qty=quantity if exc.maybe_sent else None)
            self._protection_page(now, "P0", "unknown" if exc.maybe_sent else "rejected",
                                  self.store.protection(req.position_id), type(exc).__name__)
            return
        # A synchronous placeOrder return is NEVER server confirmation.
        self.store.update_intent(now, req.intent_id, status=IntentStatus.SENT, sent_at=now,
                                 broker_order_id=ack.broker_order_id, perm_id=ack.perm_id)
        self.store.update_protection(now, req.position_id, broker_order_id=ack.broker_order_id,
                                     perm_id=ack.perm_id)
        log.info("[PROTECT_SENT] %s qty=%d type=STP stop=%.2f ref=%s",
                 req.symbol, quantity, req.trigger_price, req.intent_id)
        if self._in_urgent_callback and self._urgent_received_mono is not None:
            log.info("[PROTECT_DISPATCH] kind=A callback_to_submit_ms=%.3f symbol=%s ref=%s",
                     (time.monotonic() - self._urgent_received_mono) * 1000,
                     req.symbol, req.intent_id)
        self._protection_priority_due = True

    def _protection_reject_status_identity(self, now, event):
        row = self.store.protection_for_intent(event.intent_id)
        self.store.hold_intent(now, event.intent_id, "protection native status identity mismatch")
        if row is not None:
            self.store.update_protection(now, row["position_id"], state="UNKNOWN")
            self.store.set_control(now, f"frozen:{row['symbol']}", "unverified protection native identity")
            self._protection_page(now, "P0", "unknown", self.store.protection(row["position_id"]),
                                  "原生账户、客户端、合约、类型、价格或数量身份无效；不授予释放权限")
        self.store.audit(now, "PROTECT_STATUS_IDENTITY_MISMATCH", intent_id=event.intent_id,
                         raw_status=event.raw_status)
        self._protection_emergency = True

    def _protection_native_quantity_valid(self, row, intent, identity):
        if intent is None:
            return False
        quantities = {int(intent["quantity"])}
        if row["pending_qty"] is not None:
            quantities.add(int(row["pending_qty"]))
        previous = self._overnight_restore_previous_total(row, intent)
        if previous is not None:
            quantities.add(previous)  # scoped own-TP pre-ACK snapshot only
        return (identity.total_quantity in quantities and
                math.isfinite(identity.filled) and math.isfinite(identity.remaining) and
                float(identity.filled).is_integer() and float(identity.remaining).is_integer() and
                min(identity.filled, identity.remaining) >= 0 and
                identity.filled + identity.remaining <= identity.total_quantity)

    def _protection_adopt_status_identity(self, now, event) -> bool:
        row = self.store.protection_for_intent(event.intent_id)
        intent = self.store.intent(event.intent_id)
        identity = event.order_identity
        if row is None or intent is None or intent["hold_reason"] or identity is None:
            return False
        if (identity.intent_id != event.intent_id or identity.status != event.raw_status or
                identity.broker_order_id != event.broker_order_id or identity.perm_id != event.perm_id or
                (intent["broker_order_id"] is not None and intent["broker_order_id"] != identity.broker_order_id) or
                (intent["perm_id"] is not None and intent["perm_id"] != identity.perm_id) or
                not self._protection_native_quantity_valid(row, intent, identity) or
                not self._protection_order_valid(row, replace(identity, status="PreSubmitted"))):
            return False
        if row["broker_order_id"] is None or row["perm_id"] is None:
            with self.store.tx():
                self.store.conn.execute("UPDATE protections SET broker_order_id=?,perm_id=?,updated_at=? WHERE position_id=?",
                                        (identity.broker_order_id, identity.perm_id, now.isoformat(), row["position_id"]))
                self.store.conn.execute("UPDATE intents SET broker_order_id=?,perm_id=?,updated_at=? WHERE intent_id=?",
                                        (identity.broker_order_id, identity.perm_id, now.isoformat(), event.intent_id))
                self.store.audit(now, "PROTECT_STATUS_IDENTITY_VERIFIED", intent_id=event.intent_id,
                                 broker_order_id=identity.broker_order_id, perm_id=identity.perm_id)
        self._protection_bind_native_contract(now, self.store.protection(row["position_id"]), identity)
        return True

    def _protection_explicit_refusal(self, event, row, intent):
        if (event.error_code not in {201, 202} or event.raw_status not in {"Cancelled", "ApiCancelled", "Inactive"} or
                intent is None or intent["hold_reason"] or intent["cancel_requested_at"] or
                row["state"] in {"CANCEL_PENDING", "PROTECT_CANCEL_PENDING"} or
                event.order_identity is None):
            return False
        identity = event.order_identity
        if (identity.intent_id != row["intent_id"] or identity.broker_order_id != row["broker_order_id"] or
                identity.perm_id != row["perm_id"] or
                not self._protection_native_quantity_valid(row, intent, identity) or not self._protection_order_valid(
                    row, replace(identity, status="PreSubmitted"))):
            return False
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1),16)), event.message).lower()
        return bool((event.error_code == 201 and text.strip()) or
                    ("无法接纳" in text and "止损" in text) or
                    ("cannot accept" in text and "stop" in text))

    def _protection_on_status(self, now: datetime, event, *, previous_raw=None) -> None:
        if self.dry_run:
            return
        row = self.store.protection_for_intent(event.intent_id)
        if row is None:
            return
        if not self._protection_type_allowed(now, row):
            return
        intent = self.store.intent(event.intent_id)
        identity = event.order_identity
        repeated_working = bool(previous_raw == event.raw_status and event.raw_status in _WORKING and
            self._protection_steady(row) and identity is not None and
            self._protection_order_valid(row, identity) and
            int(row["status_seen_generation"]) == int(row["modify_generation"]) and
            identity.remaining == row["confirmed_remaining"] == row["pending_max_remaining"] and
            identity.filled == self.store.intent_filled_qty(row["intent_id"]) and
            type(event.filled) in (int, float) and type(event.remaining) in (int, float) and
            event.filled == identity.filled and event.remaining == identity.remaining)
        if self._protection_explicit_refusal(event, row, intent):
            self.store.update_protection(now, row["position_id"], state="REJECTED",
                                         confirmed_remaining=0, pending_max_remaining=0, pending_qty=None)
            self.store.set_control(now, f"frozen:{row['symbol']}", "broker explicitly rejected protection")
            self.store.audit(now, "PROTECT_BROKER_REJECTED", intent_id=event.intent_id,
                             error_code=event.error_code, reason=event.message)
            terminal_fact = {"cancelled": "券商已回报取消保护请求", "rejected": "券商已明确拒绝 STP 请求",
                             "unknown": "券商终态事实待核实：回报类型与原因不明或矛盾"}[
                                 self._protection_terminal_display_fact(event.raw_status, event.error_code, event.message)]
            self._protection_page(now, "P0", "rejected", self.store.protection(row["position_id"]),
                                  terminal_fact + "；禁止自动重挂")
        elif event.raw_status in _TERMINAL and event.raw_status != "Filled" and row["state"] not in {
                "PROTECT_CANCEL_PENDING", "CANCEL_PENDING"}:
            rejected = row["state"] == "REJECTED"
            if not rejected:
                self.store.update_protection(now, row["position_id"], state="UNKNOWN")
            self._protection_page(now, "P0", "rejected" if rejected else "unknown",
                                  self.store.protection(row["position_id"]),
                                  f"券商回报状态={event.raw_status}；禁止未经核实重挂")
        elif event.raw_status in _WORKING:
            self.store.update_protection(now, row["position_id"],
                                         status_seen_generation=int(row["modify_generation"]))
            if row["state"] == "REDUCE_PENDING" and row["gap_b_started_at"] is None:
                self.store.update_protection(now, row["position_id"], gap_b_started_at=now)
            if row["state"] == "REDUCE_PENDING" and self._in_urgent_callback:
                self._protection_b_status_mono[row["position_id"]] = time.monotonic()
            if row["state"] == "REDUCE_PENDING" and self._protection_reduce_ack(now, row, event):
                return
            if not repeated_working:
                self._protection_priority_due = True
        elif event.raw_status in _TERMINAL:
            self._protection_priority_due = True
        if row["state"] == "PROTECT_CANCEL_PENDING" and event.raw_status in _TERMINAL and \
                row["gap_b_started_at"] is None:
            self.store.update_protection(now, row["position_id"], gap_b_started_at=now)
        if row["state"] == "PROTECT_CANCEL_PENDING" and event.raw_status in {"Cancelled", "ApiCancelled"}:
            if self._in_urgent_callback:
                self._protection_b_status_mono[row["position_id"]] = time.monotonic()
            if self._final_cancel_ack(now, self.store.protection(row["position_id"]), event):
                return
        if not self._in_urgent_callback and not repeated_working:
            self._protection_progress(self.clock.now())

    def _protection_evidence(self, row):
        previous = self._protection_query_position
        self._protection_query_position = row["position_id"]
        self._protection_evidence_attempted_at[row["position_id"]] = self.clock.now()
        try:
            return self._protection_evidence_fresh(row)
        finally:
            self._protection_query_position = previous

    def _protection_evidence_fresh(self, row):
        """Fresh three-source snapshot; absence or cross-day gap is UNKNOWN.

        This function is forbidden in the ib_insync callback because these
        requests are blocking.  The caller must run before history/scan work.
        """
        if self._in_urgent_callback:
            raise BrokerError("blocking protection evidence requested inside callback")
        self._protection_notice_evidence.pop(row["position_id"], None)
        started_at = self.clock.now()
        before_position = self.store.position(row["position_id"])
        before_fills = self._protection_economic_fills()
        before_anomalies = tuple(tuple(a) for a in self.store.anomalies())
        socket_generation = getattr(self.broker, "query_generation", None)
        row_context = tuple(row[name] for name in ("intent_id", "modify_generation", "state", "updated_at"))
        if self._protection_steady_evidence_budget:
            self._protect("query")
        try:
            orders = self.broker.open_orders()
        except Exception as exc:
            self.store.audit(self.clock.now(), "BROKER_QUERY_ERROR", source="open_orders",
                intent_id=row["intent_id"], position_id=row["position_id"], symbol=row["symbol"],
                full_error=str(exc), error_type=type(exc).__name__, maybe_sent=getattr(exc, "maybe_sent", None))
            raise ProtectionEvidenceQueryError("open_orders query unavailable") from exc
        self._note_working(self.clock.now(), orders)
        if self._protection_steady_evidence_budget:
            self._protect("query")
        try:
            positions = self.broker.positions_fresh()
        except Exception as exc:
            self.store.audit(self.clock.now(), "BROKER_QUERY_ERROR", source="positions_fresh",
                intent_id=row["intent_id"], position_id=row["position_id"], symbol=row["symbol"],
                full_error=str(exc), error_type=type(exc).__name__, maybe_sent=getattr(exc, "maybe_sent", None))
            raise ProtectionEvidenceQueryError("positions_fresh query unavailable") from exc
        if self._protection_steady_evidence_budget:
            self._protect("query")
        try:
            fills = self.broker.executions_today()
        except Exception as exc:
            self.store.audit(self.clock.now(), "BROKER_QUERY_ERROR", source="executions_today",
                intent_id=row["intent_id"], position_id=row["position_id"], symbol=row["symbol"],
                full_error=str(exc), error_type=type(exc).__name__, maybe_sent=getattr(exc, "maybe_sent", None))
            raise ProtectionEvidenceQueryError("executions_today query unavailable") from exc
        self._verify_queried_execution_times(self.clock.now(), fills)
        visible = self._overnight_visible_query(row)
        if visible.get("overnight_visible_request_failed"):
            raise ProtectionEvidenceQueryError("account_visible_utc query unavailable: " +
                visible["overnight_visible_error"]) from visible["overnight_visible_request_exception"]
        if socket_generation != getattr(self.broker, "query_generation", None):
            raise ProtectionEvidenceQueryError("three-source query socket generation changed")
        if self._final_cancel_conflicting_orders(row, orders, started_at):
            raise ProtectionEvidenceContradiction('confirmed cancelled original STP returned in complete query')
        self._fresh_seq += 1
        at = self.clock.now()
        matching = [p for p in positions if p.symbol == row["symbol"]]
        if len(matching) > 1 or any(p.account != self.cfg.account or p.con_id != row["con_id"]
                                    for p in matching):
            self._suspect(row["symbol"])
            if self.store.control(f"frozen:{row['symbol']}") is None:
                self._reconcile_due = True
            self._protection_emergency = True
            raise BrokerError("fresh position account/conId identity mismatch")
        held = matching[0].quantity if matching else 0.0
        self._fresh_held[row["symbol"]] = (held, at)
        if not math.isfinite(held) or held != int(held) or held < 0:
            self._protection_emergency = True
            raise BrokerError("broker stock position fractional/short")
        pos = self.store.position(row["position_id"])
        if self._protection_own_tp_query_delta(row, before_position, before_fills, before_anomalies,
                held, orders, tuple(fills or ()) + tuple(visible.get("account_fills") or ())):
            self._fresh_held.pop(row["symbol"], None)
            raise ProtectionEvidenceSuperseded("own TP fill superseded this round's position snapshot")
        old_ids = {f[0] for f in before_fills}
        if row["tp_intent_id"] and any(f[0] not in old_ids and f[1] == row["tp_intent_id"]
                for f in self._protection_economic_fills()):
            raise ProtectionEvidenceContradiction("same-round TP fill does not explain all broker/ledger/order facts")
        if pos is None or int(pos["qty_open"] if pos["status"] == "OPEN" else 0) != held:
            self._suspect(row["symbol"])
            if self.store.control(f"frozen:{row['symbol']}") is None:
                self._reconcile_due = True
            queried = tuple(fills or ()) + tuple(visible.get("account_fills") or ())
            if not self._protection_own_stop_query_delta(row, before_position, before_fills,
                    before_anomalies, held, orders, queried):
                raise ProtectionEvidenceContradiction("broker position does not match ledger")
            raise BrokerError("own GTC fill superseded this round's position snapshot")
        # reqExecutions may only cover today.  A missing historical exec
        # forbids an increase/replacement; a decrease can still use fresh P.
        from v6lite.market_calendar import trading_day
        ledger_execs = self.store.conn.execute(
            "SELECT exec_id FROM fills WHERE position_id=? AND trading_day<?",
            (row["position_id"], trading_day(at).isoformat())).fetchall()
        # A successful today's download cannot cover prior-day executions.
        # Keep that limitation for every write/replacement/release authority,
        # but distinguish it from a failed download or missing today's fill.
        today_required = {r["exec_id"] for r in self.store.conn.execute(
            "SELECT exec_id FROM fills WHERE position_id=? AND trading_day>=?",
            (row["position_id"], trading_day(at).isoformat()))}
        today_exec_complete = fills is not None and today_required.issubset({f.exec_id for f in fills})
        if ledger_execs or fills is None or self._overnight_position(row, at):
            full_exec = False
        else:
            required = {r["exec_id"] for r in self.store.conn.execute(
                "SELECT exec_id FROM fills WHERE position_id=?", (row["position_id"],)).fetchall()}
            full_exec = required.issubset({f.exec_id for f in fills})
        own = [o for o in orders if o.intent_id == row["intent_id"]]
        other_sells = [o for o in orders if o.intent_id != row["intent_id"] and
                       (o.symbol in (row["symbol"], "*")) and o.side in ("SELL", "")]
        if any(not math.isfinite(o.remaining) or o.remaining < 0 for o in own + other_sells):
            self._protection_emergency = True
            raise BrokerError("working SELL remaining is not finite and nonnegative")
        self._protection_notice_evidence[row["position_id"]] = {
            "intent_id": row["intent_id"], "generation": int(row["modify_generation"]),
            "started_at": started_at, "at": at,
            "held": int(held), "own": own, "others": other_sells, "full_exec": full_exec,
            "prior_day_execution_history": bool(ledger_execs),
            "current_day_executions_complete": today_exec_complete,
            "row_context": row_context, "socket_generation": socket_generation,
            "authority_binding": self._protection_verification_binding(row),
            "client_fills": tuple(fills or ()), **visible,
            "contract_identity": tuple(row[name] for name in
                ("broker_order_id", "perm_id", "con_id", "account", "client_id"))}
        return int(held), own, other_sells, full_exec

    def _protection_other_remaining(self, order) -> int:
        """Booked own TP fills cap an asynchronous openOrders remainder hint."""
        raw = math.ceil(order.remaining)
        if not order.ours:
            return raw
        intent = self.store.intent(order.intent_id)
        if intent is None or intent["purpose"] not in {Purpose.TP1, Purpose.TP2, Purpose.TP3}:
            return raw
        ledger_remaining = max(0, int(intent["quantity"]) -
                               self.store.intent_filled_qty(order.intent_id))
        return min(raw, ledger_remaining)

    def _protection_order_valid(self, row, order) -> bool:
        return bool(row["order_type"] == "STP" and order.intent_id == row["intent_id"] and
                    order.ours and order.symbol == row["symbol"] and order.side == "SELL" and
                    order.account == self.cfg.account and order.client_id == self.cfg.client_id and
                    order.con_id == row["con_id"] and order.con_id and
                    order.order_type == "STP" and order.tif == "GTC" and
                    self._protection_native_quantity_valid(row, self.store.intent(row["intent_id"]), order) and
                    order.outside_rth is False and order.broker_order_id and
                    (row["broker_order_id"] is None or order.broker_order_id == row["broker_order_id"]) and
                    order.perm_id and (row["perm_id"] is None or order.perm_id == row["perm_id"]) and
                    row["limit_price"] is None and row["pending_limit"] is None and
                    any(same_cent_price(order.aux_price, expected) for expected in
                        (row["trigger_price"], row["pending_trigger"])) and
                    order.status in (_WORKING | ({"PendingCancel"} if row["state"] in {
                        "CANCEL_PENDING", "PROTECT_CANCEL_PENDING"} else set())))

    @staticmethod
    def _protection_steady(row) -> bool:
        return (row["state"] == "WORKING" and row["pending_qty"] is None and
                row["tp_stage"] is None and not row["gap_a_started_at"] and
                not row["gap_b_started_at"])

    def _protection_rate_notice(self, now, row):
        # Wire pacing or an UNKNOWN row is not a failed verification.
        # Only an actual recorded retry event may justify this P1.
        if row["position_id"] not in self._protection_verification_retry:
            return
        ready = getattr(self.broker, "is_synchronized", None)
        if callable(ready) and not ready():
            return
        if row["gap_a_started_at"] and int(row["confirmed_remaining"]) == 0:
            try:
                age = (now - datetime.fromisoformat(row["gap_a_started_at"])).total_seconds()
            except (ValueError, TypeError):
                age = float("inf")
            if 0 <= age <= self.cfg.protection.gap_a_p1_seconds:
                return  # initial ACK wait, without altering A/F4 deadlines
        key = f"protect_query_limited:{row['position_id']}"
        if self.store.control(key) is not None:
            return
        detail = ("同一持仓核验未通过，查询按 1、2、4、8、16、30 秒退避；"
                  "等待期间不提供新的完整证据，原 UNKNOWN、人工核验 hold 和冻结继续保留。")
        self.store.set_control(now, key, now.isoformat())
        self.store.audit(now, "PROTECT_QUERY_LIMITED", position_id=row["position_id"],
                         intent_id=row["intent_id"], symbol=row["symbol"])
        card = AlertCard("🟠 P1 · 券商查询已限速 · " + row["symbol"], P1_ORANGE,
            (("发生了什么", detail), ("影响", "本持仓继续等待核验；其他持仓和连接心跳继续运行。"),
             ("你要做什么", "在 TWS 核对原保护单与持仓；保持冻结，不增加卖单或绕过核验。")))
        self.alerter.send(now, "P1", "broker queries limited " + row["position_id"], detail, card=card)

    def _protection_query_throttled(self, channel):
        pid = self._protection_query_position
        row = self.store.protection(pid) if pid else None
        if row is None:
            return
        intent = self.store.intent(row["intent_id"])
        terminal = self._protection_terminal_notice(row)
        if (terminal and terminal.get("complete_identity") and terminal.get("cancel_class") and
                intent is not None and not intent["hold_reason"]):
            return  # a known broker cancellation has its own P0, not an identity retry incident
        if (pid in self._protection_verification_retry or row["state"] == "UNKNOWN" or
                (intent is not None and intent["hold_reason"])):
            self._protection_rate_notice(self.clock.now(), row)

    @staticmethod
    def _protection_verification_context(row):
        return (row["intent_id"], int(row["modify_generation"]), row["state"], row["tp_intent_id"])

    @staticmethod
    def _protection_verification_binding(row):
        if row is None:
            return None
        # Authority fields only: repeated legitimate status timestamps do not
        # change an order, its quantity, or the TP workflow being proved.
        return tuple(row[name] for name in (
            "intent_id", "modify_generation", "state", "tp_intent_id", "order_type",
            "broker_order_id", "perm_id", "con_id", "account", "client_id",
            "trigger_price", "pending_trigger", "limit_price", "pending_limit",
            "requested_qty", "confirmed_remaining", "pending_max_remaining", "pending_qty",
            "tp_stage", "tp_qty"))

    def _protection_verification_wait(self, now, row):
        state = self._protection_verification_retry.get(row["position_id"])
        context = self._protection_verification_context(row)
        if state is not None and state["context"] == context and now < state["until"]:
            return True  # retained display proof keeps its original timestamp; no fresh authority
        if state is not None and state["context"] != context:
            self._protection_verification_retry.pop(row["position_id"], None)
        return False

    def _protection_verification_failure(self, now, row, *, invalidate=True, notify=True,
                                         workflow="progress"):
        pid = row["position_id"]
        current = self.store.protection(pid)
        if current is None or current["intent_id"] != row["intent_id"]:
            self._protection_notice_evidence.pop(pid, None)
            return  # an old query cannot install retry evidence for a new order
        row = current
        context = self._protection_verification_context(row)
        prior = self._protection_verification_retry.get(pid)
        same_context = prior is not None and prior["context"] == context
        failures = min(6, prior["failures"] + 1) if same_context else 1
        pending = set(prior.get("pending_workflows", {"progress"})) if same_context else set()
        pending.add(workflow)
        delay = min(30, 2 ** (failures - 1))
        self._protection_verification_retry[pid] = {
            "context": context, "failures": failures, "until": now + timedelta(seconds=delay),
            "pending_workflows": pending}
        if invalidate:
            self._protection_notice_evidence.pop(pid, None)
        self.store.audit(now, "PROTECT_VERIFICATION_BACKOFF", position_id=pid,
                         intent_id=row["intent_id"], seconds=delay, failures=failures, workflow=workflow)
        if notify:
            self._protection_rate_notice(now, row)

    def _protection_verification_success(self, row, *, workflow="progress"):
        pid = row["position_id"]
        state = self._protection_verification_retry.get(pid)
        if state is not None:
            if state["context"] != self._protection_verification_context(row):
                return  # proof from another phase cannot clear this phase's failure
            pending = set(state.get("pending_workflows", {"progress"}))
            pending.discard(workflow)
            if pending:
                state["pending_workflows"] = pending
                return  # a healthy STP check cannot erase an unfinished resume proof
            self._protection_verification_retry.pop(pid, None)
        key = f"protect_evidence_failure:{pid}"
        if self.store.control(key) is not None:
            self.store.delete_control(key)

    def _protection_evidence_deadline(self, now: datetime, row) -> None:
        # The F4 alert clock is independent of the next permitted query. It
        # consumes durable failure facts, never creates fresh broker proof.
        key = f"protect_evidence_failure:{row['position_id']}"
        raw = self.store.control(key)
        if raw is None:
            return
        try:
            state = json.loads(raw)
            since = datetime.fromisoformat(state["since"])
            if since.tzinfo is None or since > now or not isinstance(state["paged"], bool):
                raise ValueError("invalid evidence failure clock")
        except (ValueError, KeyError, TypeError):
            self._protection_page(now, "P0", "evidence incomplete", row,
                                  "持久证据失败计时无法核实")
            return
        elapsed = (now - since).total_seconds()
        if elapsed >= self.cfg.protection.evidence_fail_p0_seconds and not state["paged"]:
            self._protection_page(now, "P0", "evidence incomplete", row,
                                  f"持仓、工作单、执行三项证据查询已连续失败 {elapsed:.1f} 秒")
            state["paged"] = True
            self.store.set_control(now, key, json.dumps(state, sort_keys=True))

    def _protection_finish_evidence_episode(self, now, row, reason, *, closed=False):
        pid = row["position_id"]
        retry = self._protection_verification_retry.get(pid)
        if not closed and retry is not None and set(retry.get("pending_workflows", {"progress"})) - {"progress"}:
            return  # OPEN stop health never erases an unfinished resume/write proof
        self._protection_verification_retry.pop(pid, None)
        self.store.delete_control(f"protect_evidence_failure:{pid}")
        self._protection_resolve_incidents(now, row, reason, groups={"evidence incomplete"})

    def _protection_closed_by_own_fill(self, row):
        if self._protection_closed_by_final_tp(row):
            return True
        current = self.store.protection(row["position_id"])
        pos = self.store.position(row["position_id"])
        intent = self.store.intent(row["intent_id"])
        if (self._protection_emergency or current is None or pos is None or intent is None or
                current["intent_id"] != row["intent_id"] or pos["status"] != "CLOSED" or
                pos["qty_open"] != 0 or current["state"] != "FILLED" or
                current["confirmed_remaining"] != 0 or current["pending_max_remaining"] != 0 or
                intent["purpose"] != Purpose.PROTECT or intent["status"] != IntentStatus.FILLED or
                intent["hold_reason"] or self.store.intent_filled_qty(intent["intent_id"]) != intent["quantity"]):
            return False
        if any(o.intent_id != row["intent_id"] and o.symbol in (row["symbol"], "*") and
               o.side in ("SELL", "") and o.remaining > 0 for o in self._working.values()):
            return False  # another live SELL is not excused by this own close
        fills = self.store.position_fills(row["position_id"])
        return bool(fills and sum(f["quantity"] if f["side"] == "BUY" else -f["quantity"]
                                  for f in fills) == 0)

    @staticmethod
    def _protection_query_unavailable(exc):
        """Timeout/transport loss or superseded socket waits; server rejection still fails."""
        if not isinstance(exc, ProtectionEvidenceQueryError):
            return False
        cause = exc
        while cause is not None:
            if isinstance(cause, OSError) or "generation changed" in str(cause):
                return True
            cause = cause.__cause__
        return False

    def _protection_discard_superseded_evidence(self, now, row, exc, *, steady):
        if isinstance(exc, ProtectionEvidenceContradiction):
            return False
        if isinstance(exc, ProtectionEvidenceSuperseded):
            self._protection_notice_evidence.pop(row["position_id"], None)
            self._protection_priority_due = True
            self.store.audit(now, "PROTECT_QUERY_SUPERSEDED_BY_TP_FILL", position_id=row["position_id"],
                intent_id=row["intent_id"], full_error=str(exc))
            return True
        current = self.store.protection(row["position_id"])
        pos = self.store.position(row["position_id"])
        if (current is not None and pos is not None and pos["status"] == "OPEN" and
                self._protection_verification_binding(current) == self._protection_verification_binding(row)):
            return False
        if self._protection_closed_by_own_fill(row):
            self._protection_query_failure(now, row, exc, steady=steady)
        elif (pos is None or pos["status"] != "OPEN" or self._protection_emergency or
                any(o.intent_id != row["intent_id"] and o.symbol in (row["symbol"], "*") and
                    o.side in ("SELL", "") and o.remaining > 0 for o in self._working.values())):
            return False  # known short/other SELL or an unproven close still needs independent P0
        else:
            self._protection_notice_evidence.pop(row["position_id"], None)
            self._protection_priority_due = True
            self.store.audit(now, "PROTECT_QUERY_SUPERSEDED_BY_STATE", position_id=row["position_id"],
                intent_id=row["intent_id"], error_type=type(exc).__name__, full_error=str(exc))
        return True  # no conclusion about the callback's successor from an old snapshot

    def _protection_query_failure(self, now: datetime, row, exc: Exception, *, steady: bool) -> None:
        if not isinstance(exc, ProtectionEvidenceContradiction) and self._protection_closed_by_own_fill(row):
            current = self.store.protection(row["position_id"])
            self._fresh_held.pop(row["symbol"], None)
            self._protection_finish_evidence_episode(now, current,
                "确认的本系统退出成交已平仓；旧证据事件结束", closed=True)
            self.store.audit(now, "PROTECT_QUERY_SUPERSEDED_BY_FILL", position_id=row["position_id"],
                intent_id=row["intent_id"], full_error=str(exc), error_type=type(exc).__name__)
            return  # an in-flight pre-fill P snapshot cannot resurrect a closed incident
        self._protection_verification_failure(now, row)
        if not isinstance(exc, ProtectionEvidenceQueryError):
            # Complete broker evidence contradicted the ledger or contained
            # invalid identity/quantity data. Block all new entry immediately;
            # a pure request timeout keeps the separate F4 retry clock.
            self._suspect(row["symbol"])
            if self.store.control(f"frozen:{row['symbol']}") is None:
                self._reconcile_due = True
        if not steady or not isinstance(exc, ProtectionEvidenceQueryError):
            self._protection_page(now, "P0", "evidence incomplete", row,
                                  f"持仓、工作单、执行三项证据查询失败：{type(exc).__name__}")
            return
        key = f"protect_evidence_failure:{row['position_id']}"
        raw = self.store.control(key)
        if raw is None:
            state = {"since": now.isoformat(), "paged": False}
        else:
            try:
                state = json.loads(raw)
                since = datetime.fromisoformat(state["since"])
                if since.tzinfo is None or since > now:
                    raise ValueError("invalid evidence failure clock")
            except (ValueError, KeyError, TypeError):
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      "持久证据失败计时无法核实")
                return
        elapsed = (now - datetime.fromisoformat(state["since"])).total_seconds()
        log.warning("[PROTECT_EVIDENCE_RETRY] symbol=%s elapsed=%.1fs cause=%s",
                    row["symbol"], elapsed, type(exc).__name__)
        self.store.set_control(now, key, json.dumps(state, sort_keys=True))
        self._protection_evidence_deadline(now, row)
        # The failure control keeps steady proof eligible, subject to the
        # shared retry deadline. Only an actual exec/status callback claims
        # urgent priority; timeouts must not starve ordinary exits and scans.

    def _protection_progress(self, now: datetime, *, steady_budget: bool = False, phase: str = "all") -> None:
        if not self.cfg.protection.enabled or self.dry_run:
            self._protection_priority_due = False
            return
        if self._in_urgent_callback or not self.broker.is_connected():
            return
        ready = getattr(self.broker, "is_synchronized", None)
        if callable(ready) and not ready():
            self.flags.broker_connected = False
            self.flags.reconcile_ok = False
            return
        if self._protection_busy:
            return
        self._protection_busy = True
        # A callback during a slow broker request sets this flag again. Do not
        # erase work that arrived after this pass began.
        self._protection_priority_due = False
        try:
            rows = list(self.store.protections())
            if phase == "steady" and steady_budget and self._protection_steady_cursor:
                # One slow steady proof per tick. Rotate after the last
                # attempted position, including one whose query failed.
                for index, candidate in enumerate(rows):
                    if candidate["position_id"] == self._protection_steady_cursor:
                        rows = rows[index + 1:] + rows[:index + 1]
                        break
            steady_attempted = False
            for initial in rows:
                row = self.store.protection(initial["position_id"])
                pos = self.store.position(row["position_id"])
                if pos is None:
                    continue
                if not self._protection_type_allowed(now, row):
                    continue
                cleanup = self.store.control(f"retired_fill_cleanup:{row['position_id']}")
                if cleanup:
                    self._protection_cleanup_retired_fill(self.clock.now(), row)
                    row = self.store.protection(row["position_id"])
                if pos["status"] != "OPEN":
                    if cleanup and row["state"] == "CANCEL_PENDING":
                        self._protection_progress_one(self.clock.now(), row, pos)
                    continue
                external_cached = any(o.symbol == row["symbol"] and o.side == "SELL" and not o.ours
                                      for o in self._working.values())
                steady = self._protection_steady(row) and not external_cached
                if phase == "active" and steady:
                    continue
                if phase == "steady" and not steady:
                    continue
                if phase == "steady" and steady_budget and steady_attempted:
                    break
                if steady_budget and steady:
                    current = self.clock.now()
                    if (self.cfg.protection.overnight_tp == "constraint" and
                            self._overnight_position(row, current) and
                            not self._overnight_refresh_due(current, row)):
                        continue
                    frozen = self.store.control(f"frozen:{row['symbol']}") is not None
                    # A persistent contradiction already froze the symbol and
                    # paged P0. Pace only repeated steady proof attempts; do
                    # not fake a broker confirmation or slow an open A/B/TP.
                    if frozen:
                        key = f"protect_frozen_last_attempt:{row['position_id']}"
                        prior = self.store.control(key)
                        if prior is not None:
                            try:
                                elapsed = current - datetime.fromisoformat(prior)
                            except ValueError:
                                elapsed = timedelta.max
                            if timedelta(0) <= elapsed < timedelta(
                                    seconds=self.cfg.protection.steady_check_seconds):
                                continue
                        self.store.set_control(current, key, current.isoformat())
                    else:
                        failure = self.store.control(f"protect_evidence_failure:{row['position_id']}")
                        checked = datetime.fromisoformat(row["confirmed_at"]) if row["confirmed_at"] else None
                        if failure is None and checked is not None and \
                                timedelta(0) <= current - checked < timedelta(
                                    seconds=self.cfg.protection.steady_check_seconds):
                            continue
                if phase == "steady" and steady_budget:
                    steady_attempted = True
                    self._protection_steady_cursor = row["position_id"]
                self._protection_steady_evidence_budget = phase == "steady" and steady
                try:
                    self._protection_progress_one(self.clock.now(), row, pos)
                finally:
                    self._protection_steady_evidence_budget = False
        finally:
            self._protection_busy = False
            self._protection_steady_evidence_budget = False

    def _protection_progress_one(self, now: datetime, row, pos) -> None:
        if self._protection_wait_reduction_pair(now, row) or self._final_cancel_wait(now, row):
            return  # nonblocking bounded ACK collection, no new authority
        if not self._protection_type_allowed(now, row) or self._protection_verification_wait(now, row):
            return
        if row["state"] == "PLANNED":
            # A recovered BUY is not a live execDetails callback.  No new
            # broker SELL until two complete, matching three-source snapshots
            # establish that no old GTC or other SELL still exists.
            try:
                held, own, others, complete = self._protection_evidence(row)
                held2, own2, others2, complete2 = self._protection_evidence(row)
                now = self.clock.now()
            except Exception as exc:
                now = self.clock.now()
                self._protection_verification_failure(now, row)
                self._protection_page(now, "P0", "evidence incomplete", row, type(exc).__name__)
                return
            if not (complete and complete2 and held > 0 and held2 == held and not own and
                    not own2 and not others and not others2 and row["con_id"]):
                self._protection_verification_failure(now, row)
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      "回补买入需要两轮稳定完整三项证据后才能提交 GTC")
                return
            self._protection_verification_success(row)
            self._protection_submit_first(now, row, held)
            return
        initial_state = (row["state"], row["modify_generation"], row["intent_id"],
                         row["tp_intent_id"], row["updated_at"])
        steady = self._protection_steady(row)
        try:
            held, own, others, full_exec = self._protection_evidence(row)
            now = self.clock.now()  # confirmation is after the slowest reply
        except Exception as exc:
            if self._protection_discard_superseded_evidence(self.clock.now(), row, exc, steady=steady):
                return
            self._protection_query_failure(self.clock.now(), row, exc, steady=steady)
            if (not self._protection_query_unavailable(exc) and self._overnight_position(row) and
                    self.cfg.protection.overnight_tp == "constraint"):
                self._overnight_failure(self.clock.now(), row, "当日新鲜持仓/工作单/成交核验失败：" + str(exc))
                stage = self._overnight_tp_refresh_pending.get(row["position_id"])
                if stage is not None:
                    self._overnight_record_frozen(self.clock.now(), row, stage)
            return
        current_row = self.store.protection(row["position_id"])
        if current_row is None or (current_row["state"], current_row["modify_generation"],
                                   current_row["intent_id"], current_row["tp_intent_id"],
                                   current_row["updated_at"]) != initial_state:
            self._protection_notice_evidence.pop(row["position_id"], None)
            current_intent = self.store.intent(current_row["intent_id"]) if current_row else None
            if current_row is not None and (current_row["state"] == "UNKNOWN" or
                    (current_intent is not None and current_intent["hold_reason"])):
                self._protection_verification_failure(now, current_row)
            else:
                self._protection_priority_due = True
            return
        # An owned, complete current round can acknowledge an already-issued
        # price write before considering overnight TP permission. The changed
        # binding invalidates this proof; a new a-d round must authorize TP.
        if (len(own) == 1 and not others and held == pos["qty_open"] and
                self._overnight_proof_fresh(now, row) and
                self._protection_pending_price_order(row, pos, own[0]) and
                self._protection_price_request_valid(row, pos)):
            if same_cent_price(own[0].aux_price, row["pending_trigger"]):
                self._protection_commit_price(now, row, own[0], "own complete three-source round")
                return
            if (same_cent_price(own[0].aux_price, row["trigger_price"]) and
                    (self._overnight_authority(now, row, held, own, others, pending_observation=True)
                     if self._overnight_position(row, now) else full_exec)):
                self._protection_old_price_wait(now, row)
                return
        overnight = self._overnight_position(row, now)
        proof = self._protection_notice_evidence.get(row["position_id"], {})
        if (self._overnight_stop_stable(now, row, held, own, others) and
                (full_exec or (proof.get("prior_day_execution_history") and
                               proof.get("current_day_executions_complete")))):
            self._protection_finish_evidence_episode(now, row,
                "当日新鲜持仓、工作单、成交查询恢复；原 STP 强身份及足额覆盖已确认，未授予完整历史权限")
        self._overnight_clear_price_freeze(now, row, held, own, others)
        external = [o for o in others if not o.ours]
        if external:
            if self.store.control(f"frozen:{row['symbol']}") is None:
                self.store.set_control(now, f"frozen:{row['symbol']}", "external_sell")
            self._protection_page(now, "P0", "external sell", row,
                                  f"外部卖单最多={sum(math.ceil(o.remaining) for o in external)} 股；"
                                  f"外部订单号={','.join(o.intent_id for o in external)}")
        overnight_flow = bool(row["tp_stage"] is not None and self._overnight_flow(row))
        workflow_complete = full_exec
        overnight_price_confirmation = False
        overnight_denied = False
        if overnight_flow:
            workflow_complete = self._overnight_flow_authority(now, row, held, own, others)
            overnight_denied = not workflow_complete
        elif overnight:
            # The default freeze policy is a quiet stop-only selection. Explain
            # it when it actually blocks TP (in start_tp) or a needed BE change,
            # not merely because a healthy overnight stop was taken over.
            be_needed = bool(self._overnight_stop_stable(now, row, held, own, others) and
                pos["tp1_done"] and pos["breakeven_stop"] and
                float(row["trigger_price"]) != float(pos["breakeven_stop"]))
            overnight_denied = not self._overnight_authority(now, row, held, own, others, announce=True,
                explain=self.cfg.protection.overnight_tp == "constraint" or be_needed)
            if overnight_denied:
                if row["state"] == "PRICE_PENDING" and (len(own) != 1 or
                        not self._protection_order_valid(row, own[0]) or
                        own[0].total_quantity != row["pending_qty"] or
                        (row["position_id"] not in self._protection_price_requests and
                         not same_cent_price(own[0].aux_price, row["pending_trigger"]))):
                    self.store.set_control(now, f"frozen:{row['symbol']}", "pending BE broker evidence mismatch")
                    self._overnight_record_frozen(now, row, Purpose.TP2)
                    self._protection_page(now, "P0", "unknown", row,
                        "待确认保本修改的工作单身份、价格或数量不一致；保持原单并等待人工核对")
                    return
                # A TP restriction is not a failed stop check. Record only
                # the fresh, unchanged healthy stop observation for 15s pacing;
                # never clear genuine retry/F4/hold facts or claim full_exec.
                if self._overnight_stop_stable(now, row, held, own, others):
                    self.store.update_protection(now, row["position_id"], confirmed_at=now)
                    return
                if len(own) > 1 or (own and not self._protection_order_valid(row, own[0])):
                    self._protection_page(now, "P0", "unknown", row, "GTC STP 保护工作单强身份不一致")
                elif not own:
                    self._protection_page(now, "P0", "unknown", row, "新鲜工作单未发现原 GTC；不自动重建")
            else:
                overnight_price_confirmation = bool(self._protection_notice_evidence.get(
                    row["position_id"], {}).get("overnight_tp_scope", {}).get("price_confirmation"))
        if len(own) > 1:
            self._protection_verification_failure(now, row)
            self._protection_page(now, "P0", "unknown", row, "出现重复保护身份")
            return
        order = own[0] if own else None
        if self._protection_cancel_quantity_proof(now, row):
            self.store.update_protection(now, row["position_id"], confirmed_remaining=0,
                                         pending_max_remaining=0, pending_qty=None)
            row = self.store.protection(row["position_id"])
        if order is not None and not self._protection_order_valid(row, order):
            self._protection_verification_failure(now, row)
            self._protection_page(now, "P0", "unknown", row, "账户、客户端、合约、类型或价格身份不一致")
            return
        intent = self.store.intent(row["intent_id"])
        if intent is not None and intent["hold_reason"]:
            self._protection_verification_failure(now, row)
            return  # complete transport replies cannot release an identity hold
        follow_up = row["state"] in {"TP_CANCEL_PENDING", "PROTECT_CANCEL_PENDING", "TP_NEEDS_RESTORE"}
        terminal_proven = row["state"] != "CANCELLED" or (
            intent is not None and intent["last_raw_status"] in _TERMINAL)
        replacement_required = (full_exec and order is None and row["state"] in {"WORKING", "CANCELLED"} and
                                terminal_proven and held > 0 and not others)
        if (full_exec and order is not None and intent is not None and
                self._protection_native_quantity_valid(row, intent, order)):
            if not follow_up:
                self._protection_verification_success(row)
        elif not full_exec:
            proof = self._protection_notice_evidence.get(row["position_id"], {})
            stable_carry = bool(
                proof.get("prior_day_execution_history") and
                proof.get("current_day_executions_complete") and
                self._protection_steady(row) and order is not None and
                order.status in _WORKING and intent is not None and not intent["hold_reason"] and
                not others and held == math.ceil(order.remaining) == int(row["confirmed_remaining"]))
            if not stable_carry and not overnight_flow and not overnight_price_confirmation:
                self._protection_verification_failure(now, row)
            # Exact, unchanged overnight coverage is healthy holding evidence.
            # full_exec remains False; existing real retry/F4 facts are NOT
            # cleared, and all increase/replacement/TP/release checks remain.
        elif order is None:
            if row["state"] in {"PROTECT_CANCEL_PENDING", "CANCEL_PENDING", "TP_ACTIVE",
                                "TP_CANCEL_PENDING", "TP_NEEDS_RESTORE"}:
                # Expected absence remains proof, but a first round cannot
                # reset failures from this stage's required follow-up round.
                if not follow_up:
                    self._protection_verification_success(row)
            elif not replacement_required:
                terminal = self._protection_terminal_notice(row)
                known_cancel = bool(terminal and terminal.get("complete_identity") and
                                    terminal.get("cancel_class"))
                self._protection_verification_failure(now, row, invalidate=False, notify=not known_cancel)
        if order is not None and (row["broker_order_id"] is None or row["perm_id"] is None):
            # IDs learned from a strict fresh broker snapshot, not from the
            # possibly incomplete synchronous placeOrder acknowledgement.
            self.store.update_protection(now, row["position_id"], broker_order_id=order.broker_order_id,
                                         perm_id=order.perm_id)
            self.store.update_intent(now, row["intent_id"], broker_order_id=order.broker_order_id,
                                     perm_id=order.perm_id)
            row = self.store.protection(row["position_id"])
        if order is not None and order.status in _WORKING:
            self.store.update_protection(now, row["position_id"], confirmed_at=now)
            row = self.store.protection(row["position_id"])
        other_max = sum(self._protection_other_remaining(o) for o in others)
        current = math.ceil(order.remaining) if order is not None else 0
        pending_confirmed = None
        safe_decrease_confirmation = bool(
            row["state"] == "MODIFY_PENDING" and row["pending_qty"] is not None and
            int(row["pending_qty"]) < int(row["requested_qty"]) and
            same_cent_price(row["pending_trigger"], row["trigger_price"]) and
            row["pending_limit"] is None and row["limit_price"] is None)
        # A denied overnight TP cannot block native acknowledgement of an
        # already-requested same-price emergency decrease. This only lowers
        # the conservative S reserve; it never authorizes TP, BE or an increase.
        if order is not None and row["pending_qty"] is not None and (
                not overnight_denied or safe_decrease_confirmation):
            pending = int(row["pending_qty"])
            expected_remaining = max(0, pending - self.store.intent_filled_qty(row["intent_id"]))
            price_ok = same_cent_price(order.aux_price, row["pending_trigger"])
            # A pure price change need not produce another orderStatus.
            # This path has completed our own full round, with an unchanged
            # pending binding; connection/cache snapshots cannot reach it.
            price_snapshot_ack = bool(self._protection_price_pending(row, pos) and
                order.status in _WORKING and order.total_quantity == pending and
                order.filled == self.store.intent_filled_qty(row["intent_id"]) and
                self._overnight_proof_fresh(now, row))
            status_ok = (price_snapshot_ack or (
                int(row["status_seen_generation"]) == int(row["modify_generation"]) and
                self.store.intent(row["intent_id"])["last_raw_status"] in _WORKING))
            if current == expected_remaining and price_ok and status_ok:
                pending_confirmed = (row["state"], row["gap_a_started_at"], row["gap_b_started_at"])
                if price_snapshot_ack:
                    self.store.audit(now, "PROTECT_PRICE_CONFIRMED", position_id=row["position_id"],
                        intent_id=row["intent_id"], generation=int(row["modify_generation"]),
                        source="own complete three-source round", trigger_price=row["pending_trigger"], quantity=pending)
                self.store.update_protection(now, row["position_id"], requested_qty=pending,
                                             confirmed_remaining=current, pending_max_remaining=current,
                                             pending_qty=None, pending_trigger=None, pending_limit=None,
                                             trigger_price=row["pending_trigger"], limit_price=None,
                                             status_seen_generation=int(row["modify_generation"]), state="WORKING")
                self.store.update_protection_intent_quantity(now, row["intent_id"], pending)
                row = self.store.protection(row["position_id"])
                if overnight_price_confirmation:
                    self.store.delete_control(f"overnight_be_flow:{row['position_id']}")
        worst_current = max(current, int(row["pending_max_remaining"]))
        if worst_current + other_max > held:
            # This check precedes every phase-specific return.  A cancelled
            # TP can still send a late fill, and another client can submit a
            # SELL while we are waiting for a protection change.
            target = max(0, held - other_max)
            if order is not None and worst_current > target and row["state"] not in {
                    "CANCEL_PENDING", "PROTECT_CANCEL_PENDING"}:
                if target == 0:
                    self._protection_cancel(now, row, "gross_sell_exceeds_position")
                else:
                    pending_left = (max(0, int(row["pending_qty"]) -
                                        self.store.intent_filled_qty(row["intent_id"]))
                                    if row["pending_qty"] is not None else current)
                    safe_target = min(target, current, int(row["confirmed_remaining"]), pending_left)
                    if safe_target <= 0:
                        self._protection_cancel(now, row, "gross_sell_exceeds_position")
                    else:
                        self._protection_modify(now, row, order, safe_target, increase=False,
                                                state="MODIFY_PENDING")
            self._protection_page(now, "P0", "external sell", row,
                                  f"可能卖出总量={worst_current + other_max} 股，"
                                  f"实际持仓={held} 股；只减保护数量，禁止新增卖单")
            return
        if overnight_denied:
            # Gross-capacity containment above still owns every unsafe SELL
            # state. A missing or short stop retains its original P0; only a
            # stop-stable TP-only restriction is handled as one P1 explanation.
            if order is not None and current < held and row["pending_qty"] is None:
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      f"未保护={held-current} 股；跨日或订单证据缺失")
            return
        if pending_confirmed is not None:
            kind, old_a, old_b = pending_confirmed
            if kind == "MODIFY_PENDING":
                key = f"late_tp_reduce:{row['position_id']}"
                raw = self.store.control(key)
                if raw:
                    try:
                        late = json.loads(raw)
                    except (TypeError, ValueError):
                        late = {}
                    if (late.get("generation") == int(row["modify_generation"]) and
                            late.get("target") == current):
                        self.store.delete_control(key)
                        self.alerter.resolve("P0", f"protection late tp fill {row['symbol']}")
                        self.alerter.resolve("P1", f"protection late tp fill {row['symbol']}")
                        self._protection_page(now, "P1", "late tp fill", row,
                                              f"同代订单状态和新鲜三项证据已确认同一保护单剩余={current} 股；"
                                              "迟到止盈成交后的保护数量已获券商确认")
            if kind == "REDUCE_PENDING":
                self._tp_record(now, row, "reduce_confirm")
                if old_b is None:
                    self.store.update_protection(now, row["position_id"], gap_b_started_at=now)
                if not others:
                    current_row = self.store.protection(row["position_id"])
                    confirmed_proof = dict(self._protection_notice_evidence.get(row["position_id"], {}),
                        authority_binding=self._protection_verification_binding(current_row),
                        reduction_ack_at=now, reduction_continuation=True)
                    self._protection_notice_evidence[row["position_id"]] = confirmed_proof
                    self._protection_send_tp(now, current_row, reduction_proof=confirmed_proof)
                else:
                    self._protection_page(now, "P0", "external sell", row,
                                          "保护减量期间出现其他卖单；止盈暂停")
            elif kind in {"RESTORE_PENDING", "RESTORE_SUBMIT_PENDING"} and current == held and not other_max:
                tp = self.store.intent(row["tp_intent_id"]) if row["tp_intent_id"] else None
                outcome = ("tp_not_sent" if tp is None or tp["status"] == IntentStatus.REJECTED
                           else "protection restored after TP cancel")
                self._tp_record(now, row, "restore_confirm")
                self._protection_close_b(now, row, outcome)
            elif old_a and current >= held and not other_max:
                for exec_id, seconds in self.store.close_protection_gaps_a(
                        now, row["position_id"], old_a):
                    log.info("[PROTECT_GAP] kind=A secs=%.3f symbol=%s ref=%s exec_id=%s",
                             seconds, row["symbol"], row["intent_id"], exec_id or "legacy")
            self._protection_resolve_if_covered(now, row, order, held, others, full_exec)
            return
        if row["state"] == "TP_CANCEL_PENDING":
            tp_id = row["tp_intent_id"]
            tp = self.store.intent(tp_id) if tp_id else None
            tp_working = any(o.intent_id == tp_id for o in others)
            if tp is not None and tp["status"] in {IntentStatus.CANCEL_SEEN, IntentStatus.CANCELLED} \
                    and not tp_working and workflow_complete:
                binding = self._protection_verification_binding(row)
                try:
                    held2, own2, others2, complete2 = self._protection_evidence(row)
                    now = self.clock.now()
                except Exception:
                    now = self.clock.now()
                    complete2 = False
                    held2, own2, others2 = None, [], []
                complete2 = complete2 or (overnight_flow and self._overnight_flow_authority(
                    now, self.store.protection(row["position_id"]), held2, own2, others2))
                stable = complete2 and held2 == held and not others2 and \
                    ((order is None and not own2) or
                     (order is not None and len(own2) == 1 and own2[0].remaining == order.remaining))
                tp = self.store.intent(tp_id)
                latest = self.store.protection(row["position_id"])
                stable = (stable and latest is not None and tp is not None and
                          self._protection_verification_binding(latest) == binding and
                          not tp["hold_reason"] and tp["status"] in {
                              IntentStatus.CANCEL_SEEN, IntentStatus.CANCELLED} and
                          (order is None or self._protection_order_valid(latest, own2[0])))
                if not stable:
                    self._protection_verification_failure(now, latest or row)
                    self._protection_page(now, "P0", "evidence incomplete", latest or row,
                                          "止盈撤单或成交数量未在两轮新鲜快照中稳定确认")
                    return
                self._tp_record(now, row, "restore_request", target_remaining=held)
                self.store.update_intent(now, tp_id, status=IntentStatus.CANCELLED, note=tp["note"])
                if order is not None:
                    if full_exec:
                        self._protection_verification_success(latest)
                    self._protection_modify(now, latest, own2[0], held, increase=True, state="RESTORE_PENDING")
                elif not others:
                    self._protection_replace_after_cancel(now, row, held)
            return
        if row["state"] == "PROTECT_CANCEL_PENDING":
            if order is None and workflow_complete and self.store.intent(row["intent_id"])["last_raw_status"] in _TERMINAL:
                binding = self._protection_verification_binding(row)
                try:
                    held2, own2, others2, complete2 = self._protection_evidence(row)
                    now = self.clock.now()
                except Exception:
                    now = self.clock.now()
                    complete2 = False
                    held2, own2, others2 = None, [], []
                latest = self.store.protection(row["position_id"])
                complete2 = complete2 or (overnight_flow and self._overnight_flow_authority(
                    now, latest, held2, own2, others2))
                current_intent = self.store.intent(latest["intent_id"]) if latest is not None else None
                if (not complete2 or held2 != held or own2 or others2 or
                        self._protection_verification_binding(latest) != binding or
                        current_intent is None or current_intent["hold_reason"] or
                        current_intent["last_raw_status"] not in _TERMINAL):
                    self._protection_verification_failure(now, latest or row)
                    self._protection_page(now, "P0", "evidence incomplete", latest or row,
                                          "末股 GTC 撤单需要两轮稳定完整三项证据")
                    return
                if full_exec:
                    self._protection_verification_success(latest)
                self._final_cancel_confirm(now, row, "two own complete absence rounds")
                self.store.update_intent(now, row["intent_id"], status=IntentStatus.CANCELLED,
                                         note="confirmed GTC cancellation before final TP")
                self.store.update_protection(now, row["position_id"], requested_qty=0,
                                             confirmed_remaining=0, pending_max_remaining=0,
                                             pending_qty=None, state="WORKING",
                                             gap_b_started_at=self.store.protection(row["position_id"])["gap_b_started_at"] or now)
                self._tp_record(now, self.store.protection(row["position_id"]), "reduce_confirm")
                self._protection_send_tp(now, self.store.protection(row["position_id"]))
            return
        if row["state"] == "TP_NEEDS_RESTORE":
            tp = self.store.intent(row["tp_intent_id"]) if row["tp_intent_id"] else None
            # No intent means the TP was rejected by a local pre-send gate.
            # A definite broker rejection is also safe; UNKNOWN/maybe_sent is
            # never permission to increase another live SELL.
            if tp is not None and (tp["hold_reason"] or tp["status"] not in {IntentStatus.REJECTED, IntentStatus.CANCELLED,
                                                       IntentStatus.CANCEL_SEEN}):
                if full_exec:
                    self._protection_verification_failure(now, row)
                self._protection_page(now, "P0", "unknown", row,
                                      "止盈结果尚未核实；禁止新增第二张卖单")
                return
            if not workflow_complete or others:
                if full_exec:
                    self._protection_verification_failure(now, row)
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      "恢复保护需要完整三项证据且无其他工作卖单")
                return
            if tp is not None and tp["status"] != IntentStatus.REJECTED:
                binding = self._protection_verification_binding(row)
                try:
                    held2, own2, others2, complete2 = self._protection_evidence(row)
                    now = self.clock.now()
                except Exception as exc:
                    now = self.clock.now()
                    latest = self.store.protection(row["position_id"])
                    self._protection_verification_failure(now, latest or row)
                    self._protection_page(now, "P0", "evidence incomplete", latest or row, str(exc))
                    return
                latest = self.store.protection(row["position_id"])
                complete2 = complete2 or (overnight_flow and self._overnight_flow_authority(
                    now, latest, held2, own2, others2))
                tp = self.store.intent(tp["intent_id"])
                if (not complete2 or held2 != held or others2 or
                    self._protection_verification_binding(latest) != binding or tp is None or
                    tp["status"] not in {IntentStatus.CANCEL_SEEN, IntentStatus.CANCELLED} or
                    (order is None and own2) or
                    (order is not None and (len(own2) != 1 or not self._protection_order_valid(latest, own2[0]) or
                                            own2[0].remaining != order.remaining)) or tp["hold_reason"]):
                    self._protection_verification_failure(now, latest or row)
                    return
                self.store.update_intent(now, tp["intent_id"], status=IntentStatus.CANCELLED,
                                         note=tp["note"])
            self._tp_record(now, row, "restore_request", target_remaining=held)
            if order is not None:
                # Non-final TP: keep the original GTC identity and increase
                # only after a fresh position/order/execution proof.
                if full_exec:
                    self._protection_verification_success(self.store.protection(row["position_id"]))
                self._protection_modify(now, row, order, held, increase=True, state="RESTORE_PENDING")
            else:
                # Final-share TP: old GTC was cancelled before the TP attempt.
                self._protection_replace_after_cancel(now, row, held)
            return
        if row["state"] == "CANCEL_PENDING":
            if order is None and self.store.intent(row["intent_id"])["last_raw_status"] in _TERMINAL:
                self.store.update_protection(now, row["position_id"], requested_qty=0,
                                             confirmed_remaining=0, pending_max_remaining=0,
                                             pending_qty=None, state="CANCELLED")
            if held > 0:
                self._protection_page(now, "P0", "unknown", row,
                                      "持仓尚存且 GTC 撤单待确认；不得视作已平仓")
            return
        if order is None:
            if row["state"] in {"UNKNOWN", "REJECTED", "MODIFY_PENDING", "WORKING", "CANCELLED"}:
                self._protection_page(now, "P0", "rejected" if row["state"] == "REJECTED" else "unknown", row,
                                      "完整工作单快照未发现原 GTC；禁止重复提交")
            old_intent = self.store.intent(row["intent_id"])
            terminal_proven = row["state"] != "CANCELLED" or (
                old_intent is not None and old_intent["last_raw_status"] in _TERMINAL)
            if row["state"] in {"WORKING", "CANCELLED"} and terminal_proven and \
                    full_exec and held > 0 and not others:
                self._protection_replace_after_cancel(now, row, held, for_tp_restore=False)
            return
        # A status hint alone is insufficient.  Until strict status+snapshot
        # confirmation above, preserve the old possible maximum and wait.
        if row["pending_qty"] is not None:
            return
        if row["state"] != "WORKING":
            return
        if current != int(row["confirmed_remaining"]):
            self.store.update_protection(now, row["position_id"], confirmed_remaining=current,
                                         pending_max_remaining=max(current, int(row["pending_max_remaining"])))
            row = self.store.protection(row["position_id"])
        self._protection_resolve_if_covered(now, row, order, held, others, full_exec)
        if pos["tp1_done"] and pos["breakeven_stop"] and current > 0 and not others and \
                float(row["trigger_price"]) != float(pos["breakeven_stop"]):
            if overnight:
                if not self._overnight_authority(now, row, held, own, others, announce=True):
                    return
            elif not full_exec:
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      "保本价修改需要新鲜、完整的当日持仓、工作单和成交证据")
                return
            try:
                trigger, limit = protection_prices(pos["breakeven_stop"])
            except Exception as exc:
                self._protection_page(now, "P0", "invalid price", row, type(exc).__name__)
                return
            self._protection_modify(now, row, order, current, increase=False, state="PRICE_PENDING",
                                    trigger=trigger, limit=limit)
            return
        if held > current + other_max:
            if full_exec and not others and row["tp_stage"] is None:
                self._protection_modify(now, row, order, held, increase=True)
            else:
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      f"未保护={held-current-other_max} 股；跨日或订单证据缺失")

    def _protection_modify(self, now: datetime, row, order, outstanding: int, *, increase: bool,
                           state: str = "MODIFY_PENDING", trigger: float | None = None,
                           limit: float | None = None) -> bool:
        if self.dry_run:
            log.info("[DRY_RUN] WOULD_PROTECT_MODIFY %s remaining=%d", row["symbol"], outstanding)
            return False
        if not self._protection_type_allowed(now, row) or limit is not None:
            return False
        if outstanding <= 0:
            self._protection_page(now, "P0", "unknown", row,
                                  "零股保护需明确撤单确认；禁止盲目重挂")
            return False
        filled = self.store.intent_filled_qty(row["intent_id"])
        if order.filled != filled:
            self._protection_page(now, "P0", "evidence incomplete", row,
                                  "券商与账本保护成交数量不一致；禁止修改")
            return False
        total = filled + outstanding
        req = ProtectionRequest(row["intent_id"], row["position_id"], row["symbol"], total,
                                float(row["trigger_price"] if trigger is None else trigger),
                                None,
                                row["broker_order_id"], row["perm_id"], row["con_id"],
                                row["client_id"], row["account"] or "",
                                allow_lookup=not self._in_urgent_callback)
        if (not increase and row["state"] in {"MODIFY_PENDING", "REDUCE_PENDING", "PRICE_PENDING"} and
                row["pending_qty"] == total and row["pending_trigger"] == req.trigger_price and
                row["pending_limit"] == req.limit_price):
            log.info("[PROTECT_MODIFY_WAIT] symbol=%s remaining=%d generation=%d",
                     row["symbol"], outstanding, int(row["modify_generation"]))
            return True  # same request remains in flight; do not invalidate its confirmation
        if increase:
            self.store.update_protection_intent_quantity(now, row["intent_id"], total)
        self.store.update_protection(now, row["position_id"], pending_qty=total,
                                     pending_max_remaining=max(int(row["pending_max_remaining"]),
                                                               int(row["confirmed_remaining"]), outstanding),
                                     pending_trigger=req.trigger_price, pending_limit=req.limit_price,
                                     modify_generation=int(row["modify_generation"]) + 1,
                                     status_seen_generation=-1, state=state)
        if state == "PRICE_PENDING":
            self._protection_price_requests[row["position_id"]] = (int(row["modify_generation"])+1, now)
            self.store.set_control(now, f"protect_price_requested:{row['position_id']}",
                json.dumps({"generation": int(row["modify_generation"])+1, "at": now.isoformat()}))
        if state == "PRICE_PENDING" and self._overnight_position(row, now):
            self._overnight_begin_price(now, self.store.protection(row["position_id"]))
        if state == "REDUCE_PENDING":
            self._protection_arm_reduction(now, self.store.protection(row["position_id"]),
                self._protection_notice_evidence.get(row["position_id"]))
        try:
            self.broker.modify_protection(req)
        except BrokerError as exc:
            self.store.audit(self.clock.now(), "PROTECT_BROKER_ERROR", source="modify_protection",
                intent_id=row["intent_id"], position_id=row["position_id"], symbol=row["symbol"],
                full_error=str(exc), error_type=type(exc).__name__, maybe_sent=exc.maybe_sent)
            self.store.update_protection(now, row["position_id"], state="UNKNOWN")
            self._protection_page(now, "P0", "unknown" if exc.maybe_sent else "rejected",
                                  self.store.protection(row["position_id"]), type(exc).__name__)
            return False
        log.info("[PROTECT_MODIFY] symbol=%s remaining=%d ref=%s generation=%d",
                 row["symbol"], outstanding, row["intent_id"], int(row["modify_generation"]) + 1)
        return True

    def _protection_cancel(self, now: datetime, row, reason: str) -> None:
        """Dedicated GTC cancel, only when its full possible S must become 0."""
        if self.dry_run:
            log.info("[DRY_RUN] WOULD_PROTECT_CANCEL %s reason=%s", row["intent_id"], reason)
            return
        if not self._protection_type_allowed(now, row):
            return
        if not (row["broker_order_id"] and row["perm_id"] and row["con_id"] and
                row["client_id"] == self.cfg.client_id and row["account"] == self.cfg.account):
            self._protection_page(now, "P0", "unknown", row, "无法核实 GTC 撤单身份")
            return
        final_tp = reason == "final_TP" and row["tp_stage"] in {Purpose.TP1, Purpose.TP2, Purpose.TP3}
        self.store.update_protection(now, row["position_id"],
            state="PROTECT_CANCEL_PENDING" if final_tp else "CANCEL_PENDING")
        token = self._overnight_flow(row)
        if token is not None and reason == "final_TP":
            token["original_cancel_requested"] = True
            self.store.set_control(now, f"overnight_tp_flow:{row['position_id']}", json.dumps(token, sort_keys=True))
        if final_tp:
            self._final_cancel_request(now, self.store.protection(row["position_id"]))
        try:
            self.broker.cancel(row["intent_id"], expected_order_id=row["broker_order_id"],
                               expected_perm_id=row["perm_id"], expected_con_id=row["con_id"],
                               allow_lookup=not self._in_urgent_callback)
        except BrokerError as exc:
            self.store.audit(self.clock.now(), "PROTECT_BROKER_ERROR", source="cancel_protection",
                intent_id=row["intent_id"], position_id=row["position_id"], symbol=row["symbol"],
                full_error=str(exc), error_type=type(exc).__name__, maybe_sent=exc.maybe_sent)
            self._protection_final_cancel_proofs.pop(row["position_id"], None)
            self.store.update_protection(now, row["position_id"], state="UNKNOWN")
            self._protection_page(now, "P0", "unknown", self.store.protection(row["position_id"]),
                                  f"GTC 撤单失败：{type(exc).__name__}")

    def _protection_begin_tp(self, now: datetime, position_id: str, stage: Purpose) -> None:
        if self.dry_run:
            log.info("[DRY_RUN] WOULD_PROTECT_TP %s %s", position_id, stage)
            return
        backoff = (position_id, str(stage))
        until = self._exit_backoff.get(backoff)
        if until is not None and now < until:
            return
        row = self.store.protection(position_id)
        pos = self.store.position(position_id)
        if row is None or pos is None or row["state"] != "WORKING" or row["tp_stage"] is not None:
            return
        if self._tp_start_deferred(now, position_id, stage):
            return
        if not self._protection_type_allowed(now, row):
            return
        if self._tp_paused(position_id, row["symbol"]):
            return
        first_quote = self.broker.quote(row["symbol"])
        if not self._tp_quote_ok(now, first_quote):
            self._tp_skip_quote(now, row, first_quote)
            return
        proof_fields = ("order_type", "intent_id", "broker_order_id", "perm_id", "con_id",
                        "modify_generation", "status_seen_generation", "confirmed_remaining",
                        "pending_max_remaining", "pending_qty", "trigger_price", "limit_price",
                        "confirmed_at")
        proof_generation = tuple(row[name] for name in proof_fields)
        # The reduction/TP/cancel/restore sequence owns account-wide SELL
        # authority.  A second symbol may begin only after the first closes B.
        if any(p["position_id"] != position_id and p["tp_stage"] is not None
               for p in self.store.protections()):
            log.info("[PROTECT_TP_DEFER] symbol=%s stage=%s another symbol owns TP workflow",
                     row["symbol"], stage)
            return
        try:
            held, own, others, full_exec = self._protection_evidence(row)
        except Exception as exc:
            now = self.clock.now()
            if self._protection_discard_superseded_evidence(now, row, exc, steady=False):
                return
            self._exit_backoff[backoff] = now + timedelta(seconds=30)
            self._protection_page(now, "P0", "evidence incomplete", row, type(exc).__name__)
            if self._overnight_position(row) and not self._protection_query_unavailable(exc):
                self._overnight_failure(now, row, "当日新鲜止盈证据查询失败：" + str(exc))
            return
        now = self.clock.now()
        latest_row = self.store.protection(position_id)
        latest_pos = self.store.position(position_id)
        latest_quote = self.broker.quote(row["symbol"])
        if latest_row is not None and latest_pos is not None and latest_pos["status"] == "OPEN":
            from v6lite.risk import EXIT_QUOTE_AGE_MULTIPLIER, check_quote
            if check_quote(self.cfg, latest_quote, now, require_bid_ask=False,
                           max_age_seconds=self.cfg.max_quote_age_seconds * EXIT_QUOTE_AGE_MULTIPLIER).allowed:
                self._protection_on_mark(now, latest_row, latest_quote.mark())
        overnight = self._overnight_position(row, now)
        tp_complete = full_exec
        if overnight:
            tp_complete = self._overnight_authority(now, row, held, own, others, announce=True)
        if overnight and not tp_complete:
            self._exit_backoff[backoff] = now + timedelta(seconds=30)
            # Restrict TP without turning a healthy original stop into a P0.
            # Real absence, identity failure and insufficient coverage remain
            # protection incidents, independently of the one-time TP card.
            if len(own) != 1 or not self._protection_order_valid(row, own[0]):
                self._protection_page(now, "P0", "unknown", row, "原 GTC 保护工作单缺失或强身份不符")
            elif own[0].remaining < held:
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      f"未保护={held-math.ceil(own[0].remaining)} 股；保护数量不足")
            return
        if not tp_complete or len(own) != 1 or not self._protection_order_valid(row, own[0]) or others:
            self._exit_backoff[backoff] = now + timedelta(seconds=30)
            self._protection_page(now, "P0", "evidence incomplete", row, "止盈需要无冲突的完整三项证据")
            return
        def still_current():
            latest_row = self.store.protection(position_id)
            latest_pos = self.store.position(position_id)
            latest_stage = self.store.stage(position_id, stage)
            if latest_row is None or latest_pos is None or latest_stage is None:
                return None
            if latest_pos["status"] != "OPEN" or int(latest_pos["qty_open"]) != held or \
                    latest_row["state"] != "WORKING" or latest_row["tp_stage"] is not None or \
                    latest_row["pending_qty"] is not None or latest_stage["status"] != "PENDING" or \
                    tuple(latest_row[name] for name in proof_fields) != proof_generation or \
                    math.ceil(own[0].remaining) != int(latest_row["confirmed_remaining"]):
                return None
            return latest_row, latest_pos

        current = still_current()
        if current is None:
            self._protection_priority_due = True
            log.warning("[PROTECT_TP_DEFER] position=%s stage=%s ledger changed during evidence",
                        position_id, stage)
            return
        row, pos = current
        # A queued TP can wait behind three slow broker queries.  Recheck the
        # current mark before reducing the GTC; a stop crossing stays with the
        # existing broker protection instead of becoming a new DAY sell.
        from v6lite.exits import breakeven_stop, decide_exit
        from v6lite.risk import EXIT_QUOTE_AGE_MULTIPLIER, check_quote
        from v6lite.store import Store
        quote = self.broker.quote(row["symbol"])
        if not self._tp_quote_ok(now, quote):
            self._tp_skip_quote(now, row, quote)
            return
        decision = decide_exit(Store.view(pos), quote.mark())
        current = still_current()
        if current is None:
            self._protection_priority_due = True
            log.warning("[PROTECT_TP_DEFER] position=%s stage=%s ledger changed after latest quote",
                        position_id, stage)
            return
        row, pos = current
        if decision is None or decision.stage is not stage:
            pending = self.store.stage(position_id, stage)
            if pending is not None and pending["status"] == "PENDING":
                if int(pending["filled_qty"]) == 0:
                    self.store.conn.execute("DELETE FROM exit_stages WHERE position_id=? AND stage=?",
                                            (position_id, str(stage)))
                    self.store.audit(now, "STAGE_ABANDONED", position_id=position_id,
                                     stage=str(stage), at_send=True)
                else:
                    breakeven = breakeven_stop(self.cfg, pos["entry_price"]) if stage is Purpose.TP1 else None
                    self.store.finish_stage(now, position_id, stage, "DONE", breakeven=breakeven)
            log.info("[PROTECT_TP_DEFER] symbol=%s stage=%s latest stage=%s",
                     row["symbol"], stage, decision.stage if decision else "none")
            return
        self._exit_backoff.pop(backoff, None)
        st = self.store.stage(position_id, stage)
        qty = min(int(st["target_qty"]) - int(st["filled_qty"]), int(pos["qty_open"])) if st is not None else 0
        if qty <= 0 or qty > held:
            return
        target = held - qty
        workflow = self._tp_start_workflow(now, row, stage)
        if overnight:
            self._overnight_begin_flow(now, row, stage, workflow)
        self._tp_record(now, row, "reduce_request", quote=quote, quantity=qty, target_remaining=target)
        if target <= 0:
            self._test_timeout_drop_final(now, row)
            # Last TP: broker-confirm GTC cancellation before submitting the
            # final sell.  Both orders are never live together at full size.
            self.store.update_protection(now, position_id, tp_stage=str(stage), tp_qty=qty,
                                         state="PROTECT_CANCEL_PENDING")
            self._protection_cancel(now, self.store.protection(position_id), "final_TP")
            # Dedicated cancel must preserve the final-TP phase marker, while
            # its broker call remains behind the common dry-run/identity gate.
            if self.store.protection(position_id)["state"] == "CANCEL_PENDING":
                self.store.update_protection(now, position_id, state="PROTECT_CANCEL_PENDING")
            return
        self.store.update_protection(now, position_id, tp_stage=str(stage), tp_qty=qty)
        self._protection_modify(now, self.store.protection(position_id), own[0], target,
                                increase=False, state="REDUCE_PENDING")

    def _protection_tp_not_sent(self, now: datetime, row, reason: str) -> None:
        """Persist a definite unsent TP before any evidence-based restoration."""
        if self.dry_run:
            return
        self.store.update_protection(now, row["position_id"], state="TP_NEEDS_RESTORE")
        self._protection_page(now, "P1", "tp not sent", self.store.protection(row["position_id"]), reason)
        self._protection_priority_due = True

    def _protection_send_tp(self, now: datetime, row, *, reduction_proof=None) -> None:
        if self.dry_run:
            return
        if not row["tp_stage"] or row["tp_intent_id"] or row["state"] != "WORKING":
            return
        if self._overnight_flow(row):
            try:
                if (reduction_proof is self._protection_notice_evidence.get(row["position_id"]) and
                        reduction_proof and (reduction_proof.get("reduction_continuation") or
                            reduction_proof.get("cancel_continuation")) and
                        self._overnight_proof_fresh(now, row)):
                    held, own, others = (reduction_proof[k] for k in ("held", "own", "others"))
                else:
                    held, own, others, _ = self._protection_evidence(row)
                now = self.clock.now()
            except Exception as exc:
                if self._protection_discard_superseded_evidence(self.clock.now(), row, exc, steady=False):
                    return
                self._protection_query_failure(self.clock.now(), row, exc, steady=False)
                if not self._protection_query_unavailable(exc):
                    self._overnight_failure(self.clock.now(), row, "止盈发出前新鲜证据失败：" + str(exc))
                return
            if not self._overnight_flow_authority(now, row, held, own, others):
                return
        from v6lite.exits import sell_limit
        quote = self.broker.quote(row["symbol"])
        if quote is None:
            self._protection_tp_not_sent(now, row, "没有有效止盈报价；未发出止盈，恢复保护")
            return
        if not self._tp_quote_ok(now, quote):
            self._protection_tp_not_sent(now, row, "保护减量确认后 bid/ask 无效或报价超过止盈新鲜度；未发出止盈，恢复保护")
            return
        price = sell_limit(quote.bid, self.cfg.exit_limit_band_pct)
        if price is None or price > quote.bid:
            self._protection_tp_not_sent(now, row, "基于新鲜 bid 的止盈限价无法立即执行；未发出止盈，恢复保护")
            return
        req = OrderRequest(new_id("v6l"), row["symbol"], Side.SELL, int(row["tp_qty"]),
                           price, Purpose(row["tp_stage"]), row["position_id"],
                           wire_deadline=quote.ts + timedelta(seconds=self.cfg.tp_max_quote_age_seconds))
        workflow = self._tp_workflow(row)
        send = self._tp_record(now, row, "send", intent_id=req.intent_id, quote=quote, persist=False,
                              quantity=req.quantity, calculated_price=price, sent_price=price)
        workflow.update(send)
        self.store.set_control(now, f"tp_workflow:{row['position_id']}", json.dumps(workflow))
        self.store.set_control(now, f"tp_send:{req.intent_id}", json.dumps(workflow))
        # Persist the phase before sending so a crash cannot duplicate TP.
        self._overnight_bind_tp(now, row, req.intent_id)
        self._final_cancel_bind_tp(now, row, req.intent_id)
        self.store.update_protection(now, row["position_id"], tp_intent_id=req.intent_id, state="TP_ACTIVE")
        result = self._send(now, req, atr=None, count_entry_day=None)
        received = self._protection_b_status_mono.pop(row["position_id"], None)
        if received is not None:
            log.info("[PROTECT_DISPATCH] kind=B callback_to_tp_ms=%.3f symbol=%s ref=%s",
                     (time.monotonic() - received) * 1000, row["symbol"], req.intent_id)
        if result == "rejected" and self.store.control(f"tp_rejection:{req.intent_id}") is not None:
            self.store.update_protection(now, row["position_id"], state="TP_NEEDS_RESTORE")
            self._protection_priority_due = True
        elif result not in {"sent", "unknown"}:
            self._protection_tp_not_sent(now, self.store.protection(row["position_id"]),
                                         f"止盈未发出（{result}）；正在核验恢复券商端保护")
        else:
            self.store.bump_stage_attempt(now, row["position_id"], Purpose(row["tp_stage"]))

    def _protection_on_mark(self, now: datetime, row, mark: float | None) -> None:
        """A confirmed local crossing persists until real remainder ends.

        A rebound does not untrigger STP. A missing quote can advance an
        existing episode but can never create a new crossing. Overnight
        time is excluded by requiring a new session's fresh observation.
        """
        if self.dry_run or not is_rth(now) or not self._protection_type_allowed(now, row):
            return
        key = f"protect_trigger:{row['position_id']}"
        remaining = max(int(row["confirmed_remaining"]), int(row["pending_max_remaining"]))
        if row["state"] == "FILLED":
            self.store.delete_control(key)
            return
        if remaining <= 0:
            return  # cancellation is not a fill; keep old episode until proven replacement
        fresh_mark = mark is not None and math.isfinite(mark) and mark > 0
        raw = self.store.control(key)
        if raw is None:
            episode = None
        else:
            try:
                episode = json.loads(raw)
                started = datetime.fromisoformat(episode["started_at"])
                if started.tzinfo is None or now < started:
                    raise ValueError("invalid trigger episode clock")
                if trading_day(started) != trading_day(now):
                    episode = None
                    self.store.delete_control(key)
            except (ValueError, KeyError, TypeError):
                self._protection_page(now, "P0", "unknown", row,
                                      "持久止损触发计时无法核实")
                return
        if episode is None:
            if not fresh_mark or mark > float(row["trigger_price"]):
                return
            episode = {"started_at": now.isoformat(), "session_day": trading_day(now).isoformat(),
                       "intent_id": row["intent_id"], "remaining": remaining,
                       "p1_sent": False, "p0_sent": False}
        if episode.get("intent_id", row["intent_id"]) != row["intent_id"]:
            self._protection_page(now, "P0", "unknown", row,
                                  f"退休止损事件 {episode.get('intent_id')} 需要旧撤单和新工作单的强证据")
            return
        if row["state"] != "WORKING":
            self._protection_page(now, "P0", "unknown", row,
                                  f"已有止损触发事件，但 GTC 状态={row['state']} 尚未确认")
            return
        if remaining < int(episode["remaining"]):
            episode["remaining"] = remaining  # partial fill or rebound never resets deadline
        elif remaining > int(episode["remaining"]):
            self._protection_page(now, "P0", "unknown", row,
                                  "止损触发期间工作单剩余数量增加")
            return
        elapsed = (now - datetime.fromisoformat(episode["started_at"])).total_seconds()
        if not episode["p1_sent"]:
            episode["p1_sent"] = True
            self._protection_page(now, "P1", "trigger crossing", row,
                                  f"已到止损价={row['trigger_price']:.2f}；STP 剩余={remaining} 股，券商成交尚待核实")
        # Always revisit the durable incident once overdue. It records actual
        # delivery, retries failures and limits unchanged reminders to 30 min.
        # The episode flag means overdue, never proof of notification delivery.
        if elapsed >= self.cfg.protection.trigger_unfilled_p0_seconds:
            episode["p0_sent"] = True
            self._protection_page(now, "P0", "trigger unfilled", row,
                                  f"首次到止损价后持续 {elapsed:.1f} 秒，保护单剩余仍为 {remaining} 股；"
                                  "可能停牌、交易暂停或券商异常；报价" +
                                  (f"={mark:.2f}" if fresh_mark else "缺失或过期（未据此新建穿越）"))
        self.store.set_control(now, key, json.dumps(episode, sort_keys=True))

    def _protection_timers(self, now: datetime) -> None:
        self._hard_protection_deadlines(now)

    def _hard_protection_deadlines(self, now: datetime) -> None:
        """Nonblocking A/B/F4 deadlines, also scheduled on ib_insync's loop.

        A slow three-source request may be awaiting replies when this runs.
        Never start a nested broker query here. The only broker write is a
        cached DAY TP cancellation at its 10-second deadline; GTC is untouched.
        """
        if not self.cfg.protection.enabled or self.dry_run or self._shutting_down:
            return
        for row in self.store.protections():
            pos = self.store.position(row["position_id"])
            if pos is None or pos["status"] != "OPEN":
                continue
            if not self._protection_type_allowed(now, row):
                continue
            self._protection_evidence_deadline(now, row)
            if row["state"] == "REJECTED":
                continue
            if self.store.control(f"protect_trigger:{row['position_id']}") is not None:
                self._protection_on_mark(now, row, None)
            if row["gap_a_started_at"]:
                secs = (now - datetime.fromisoformat(row["gap_a_started_at"])).total_seconds()
                base = (row["position_id"], "A", row["gap_a_started_at"])
                if secs >= self.cfg.protection.gap_a_p1_seconds and \
                        (base + ("P1",)) not in self._protection_deadline_fired:
                    self._protection_page(now, "P1", "gap A warning", row, f"A 空窗 {secs:.1f}s")
                    self._protection_deadline_fired.add(base + ("P1",))
                if secs >= self.cfg.protection.gap_a_p0_seconds and \
                        (base + ("P0",)) not in self._protection_deadline_fired:
                    self._protection_page(now, "P0", "gap A overdue", row, f"A 空窗 {secs:.1f}s")
                    self._protection_deadline_fired.add(base + ("P0",))
            if row["gap_b_started_at"]:
                secs = (now - datetime.fromisoformat(row["gap_b_started_at"])).total_seconds()
                base = (row["position_id"], "B", row["gap_b_started_at"])
                if secs >= self.cfg.protection.gap_b_p0_seconds:
                    if (base + ("P0",)) not in self._protection_deadline_fired:
                        self._protection_page(now, "P0",
                                              "gap B overdue" if self.took_over else "preflight B overdue",
                                              row, f"B 空窗 {secs:.1f}s")
                        self._protection_deadline_fired.add(base + ("P0",))
                if secs >= self.cfg.protection.gap_b_cancel_seconds and row["state"] in {
                        "TP_ACTIVE", "FILLED"} and row["tp_stage"] and row["tp_intent_id"]:
                    iid = row["tp_intent_id"]
                    if iid and self.store.intent(iid) is not None and self.store.intent(iid)["status"] not in {
                            IntentStatus.CANCEL_REQUESTED, IntentStatus.CANCEL_SEEN, IntentStatus.CANCELLED,
                            IntentStatus.FILLED}:
                        intent = self.store.intent(iid)
                        if not (self.flags.broker_connected and self.took_over and
                                intent["broker_order_id"] and intent["perm_id"] and row["con_id"]):
                            self._protection_page(now, "P0", "preflight B overdue", row,
                                                  "止盈已达 10 秒撤单门槛，但账户或订单身份未核实；"
                                                  "接管完成前禁止向券商撤单")
                            continue
                        # Durable cancel request precedes the nonblocking IB
                        # cancelOrder call.  No openOrders fallback is allowed
                        # from this event-loop deadline callback.
                        self._test_timeout_notice(now, row)
                        self._tp_record(now, row, "cancel_request", intent_id=iid)
                        self.store.update_protection(now, row["position_id"], state="TP_CANCEL_PENDING")
                        self.store.update_intent(now, iid, status=IntentStatus.CANCEL_REQUESTED,
                                                 cancel_requested_at=now, note="protection_tp_timeout")
                        try:
                            self.broker.cancel(iid, expected_order_id=intent["broker_order_id"],
                                               expected_perm_id=intent["perm_id"],
                                               expected_con_id=row["con_id"], allow_lookup=False)
                        except BrokerError as exc:
                            self.store.audit(self.clock.now(), "PROTECT_BROKER_ERROR", source="cancel_tp_timeout",
                                intent_id=iid, position_id=row["position_id"], symbol=row["symbol"],
                                full_error=str(exc), error_type=type(exc).__name__, maybe_sent=exc.maybe_sent)
                            self._protection_page(now, "P0", "unknown", self.store.protection(row["position_id"]),
                                                  f"止盈 10 秒撤单请求失败：{type(exc).__name__}")
                        self._protection_priority_due = True

    def _protection_after_tp_fill(self, now: datetime, position_id: str) -> None:
        row = self.store.protection(position_id)
        if row is None or not row["tp_intent_id"]:
            return
        iid = row["tp_intent_id"]
        intent = self.store.intent(iid)
        if intent is None:
            return
        filled = self.store.intent_filled_qty(iid)
        if filled >= intent["quantity"]:
            self._protection_close_b(now, row, "TP fully filled")
            if row["tp_stage"] == str(Purpose.TP1):
                self._protection_priority_due = True

    def _protection_cleanup_retired_fill(self, now, row):
        """A retired GTC fill reduces P, never the replacement's possible S."""
        pos = self.store.position(row["position_id"])
        held = int(pos["qty_open"]) if pos is not None and pos["status"] == "OPEN" else 0
        worst = max(int(row["confirmed_remaining"]), int(row["pending_max_remaining"]))
        if worst <= held:
            return
        self._protection_priority_due = True
        if row["state"] in {"CANCEL_PENDING", "PROTECT_CANCEL_PENDING"}:
            return
        if row["state"] in {"MODIFY_PENDING", "REDUCE_PENDING", "PRICE_PENDING"} and row["pending_qty"] is not None:
            pending_left = max(0, int(row["pending_qty"]) - self.store.intent_filled_qty(row["intent_id"]))
            if pending_left <= held:
                return  # wait for this reduction's exact status+snapshot generation
        if held == 0 or row["state"] in {"SUBMIT_PENDING", "RESTORE_SUBMIT_PENDING"}:
            self._protection_cancel(now, row, "retired_gtc_fill_no_capacity")
        else:
            self._protection_on_sell_fill(now, row["position_id"], notify=False)
        self._protection_page(now, "P0", "unknown", self.store.protection(row["position_id"]),
                              f"旧 GTC 迟到成交后持仓={held} 股；替代保护可能={worst} 股，已请求减量或撤单，待券商核实")

    def _protection_on_sell_fill(self, now: datetime, position_id: str, *, notify: bool = True) -> None:
        """Reduce capacity after a late TP, or after an exact retired GTC fill.

        The retired-GTC caller owns its correctly attributed incident card.

        Use durable P and the largest known remaining T/X even when the last
        open-order snapshot is old.  Staleness prompts evidence reconciliation,
        never a blind cancellation of a GTC that may be filling normally.
        """
        if not self.cfg.protection.enabled or self.dry_run:
            return
        row = self.store.protection(position_id)
        pos = self.store.position(position_id)
        if row is None or pos is None:
            return
        held = int(pos["qty_open"]) if pos["status"] == "OPEN" else 0
        worst = max(int(row["confirmed_remaining"]), int(row["pending_max_remaining"]))
        stale = self._working_at is None or now - self._working_at > timedelta(seconds=5)
        other_by_ref: dict[str, int] = {}
        # An old list can over-reserve a seller that was cancelled, which is
        # safe.  A newly introduced external seller remains UNKNOWN until the
        # priority fresh query, so entries stay blocked in that interval.
        for order in self._working.values():
            if order.intent_id != row["intent_id"] and order.symbol in (row["symbol"], "*") and \
                    order.side in ("SELL", ""):
                other_by_ref[order.intent_id] = max(other_by_ref.get(order.intent_id, 0),
                                                    self._protection_other_remaining(order))
        for intent in self.store.active_intents():
            if intent["symbol"] == row["symbol"] and intent["side"] == Side.SELL and \
                    intent["intent_id"] != row["intent_id"]:
                remaining = max(0, int(intent["quantity"]) -
                                self.store.intent_filled_qty(intent["intent_id"]))
                other_by_ref[intent["intent_id"]] = max(other_by_ref.get(intent["intent_id"], 0),
                                                         remaining)
        if row["tp_stage"] is not None and row["tp_intent_id"]:
            tp = self.store.intent(row["tp_intent_id"])
            if tp is not None:
                remaining = max(0, int(tp["quantity"]) - self.store.intent_filled_qty(tp["intent_id"]))
                other_by_ref[tp["intent_id"]] = max(other_by_ref.get(tp["intent_id"], 0), remaining)
        other_max = sum(other_by_ref.values())
        target = max(0, held - other_max)
        if stale:
            self._protection_emergency = True
            self._protection_priority_due = True
            if notify:
                self._protection_page(now, "P0", "evidence incomplete", row,
                                      "止盈迟到成交且外部卖单证据过期；需要新鲜对账")
        if worst <= target:
            return
        detail = (f"迟到止盈成交后持仓={held} 股，旧保护可能最多={worst} 股，"
                  f"其他在途卖单最多={other_max} 股；不要另下卖单")
        if row["state"] not in {"WORKING", "MODIFY_PENDING", "REDUCE_PENDING",
                                "RESTORE_PENDING", "PRICE_PENDING", "TP_ACTIVE", "TP_CANCEL_PENDING"}:
            self._protection_priority_due = True
            if notify:
                self._protection_page(now, "P0", "unknown", row, detail)
            return
        if target == 0:
            # This is a TP execution, not the GTC's own execution.
            self._protection_cancel(now, row, "late_tp_no_remaining_capacity")
            latest = self.store.protection(row["position_id"])
            if notify:
                self._protection_page(now, "P0", "late tp fill", latest,
                                      detail + "；已请求撤销原保护单，券商确认待核实")
            return
        from types import SimpleNamespace
        pending_left = (max(0, int(row["pending_qty"]) -
                            self.store.intent_filled_qty(row["intent_id"]))
                        if row["pending_qty"] is not None else target)
        outstanding = min(target, int(row["confirmed_remaining"]), pending_left)
        if outstanding <= 0:
            self._protection_cancel(now, row, "late_tp_pending_zero")
            latest = self.store.protection(row["position_id"])
            if notify:
                self._protection_page(now, "P0", "late tp fill", latest,
                                      detail + "；已请求撤销原保护单，券商确认待核实")
            return
        observed = SimpleNamespace(filled=self.store.intent_filled_qty(row["intent_id"]))
        submitted = self._protection_modify(now, row, observed, outstanding, increase=False,
                                            state="MODIFY_PENDING")
        latest = self.store.protection(row["position_id"])
        if submitted:
            if notify:
                self.store.set_control(now, f"late_tp_reduce:{row['position_id']}",
                                       json.dumps({"generation": int(latest["modify_generation"]),
                                                   "target": outstanding}, sort_keys=True))
            if notify:
                self._protection_page(now, "P0", "late tp fill", latest,
                                      detail + "；同一保护单的减量请求已提交，券商确认待核实")
        else:
            if latest["state"] != "UNKNOWN":
                self.store.update_protection(now, row["position_id"], state="UNKNOWN")
                latest = self.store.protection(row["position_id"])
            if notify:
                self._protection_page(now, "P0", "late tp fill", latest,
                                      detail + "；减量请求未获确认，保护单状态无法核实")

    def _protection_on_tp_status(self, now: datetime, event) -> None:
        if self.dry_run:
            return
        row = next((p for p in self.store.protections() if p["tp_intent_id"] == event.intent_id), None)
        intent = self.store.intent(event.intent_id)
        if row is None:
            if intent is not None and event.raw_status in {"Cancelled", "ApiCancelled"}:
                self._test_timeout_event(now, self._tp_context(intent), "cancel_confirm", event.intent_id)
            # The caller has already enforced the unchanged TP native identity
            # gate. Ordinary DAY TP diagnosis never creates or enables STP.
            if (intent is not None and not self.cfg.protection.enabled and
                    intent["purpose"] in {Purpose.TP1, Purpose.TP2, Purpose.TP3}):
                self._tp_status_evidence(now, event, intent)
                if (event.raw_status in _TERMINAL and event.raw_status != "Filled" and
                        not intent["cancel_requested_at"] and price_rejection(event.error_code, event.message)):
                    self._tp_price_rejected(now, intent, event.error_code, event.message)
            return
        self._tp_status_evidence(now, event, intent)
        if event.raw_status in _TERMINAL:
            if event.raw_status != "Filled":
                self._tp_record(now, row, "cancel_confirm", intent_id=event.intent_id)
                if not intent["cancel_requested_at"] and row["state"] != "TP_CANCEL_PENDING":
                    if price_rejection(event.error_code, event.message):
                        self._tp_price_rejected(now, intent, event.error_code, event.message)
                    self.store.update_protection(now, row["position_id"], state="TP_NEEDS_RESTORE")
            self._protection_priority_due = True
            if not self._in_urgent_callback:
                self._protection_progress(self.clock.now())

    def _protection_replace_after_cancel(self, now: datetime, row, held: int,
                                         *, for_tp_restore: bool = True) -> None:
        """A cancelled final-share GTC cannot be modified; new ID only after
        two stable complete snapshots prove the old ID absent and TP cancelled.
        """
        now = self.clock.now()
        if not self._protection_type_allowed(now, row) or self._protection_verification_wait(now, row):
            return
        if held <= 0:
            self._protection_close_b(now, row, "flat after TP cancel")
            return
        binding = self._protection_verification_binding(row)
        try:
            held2, old, others, complete = self._protection_evidence(row)
            now = self.clock.now()
        except Exception as exc:
            now = self.clock.now()
            latest = self.store.protection(row["position_id"])
            self._protection_verification_failure(now, latest or row)
            self._protection_page(now, "P0", "evidence incomplete", latest or row, type(exc).__name__)
            return
        latest = self.store.protection(row["position_id"])
        overnight_restore = bool(for_tp_restore and self._overnight_flow(latest))
        complete_for_restore = complete or (overnight_restore and self._overnight_flow_authority(
            now, latest, held2, old, others))
        current_intent = self.store.intent(latest["intent_id"]) if latest is not None else None
        if (held2 != held or old or others or not complete_for_restore or
                self._protection_verification_binding(latest) != binding or
                current_intent is None or current_intent["hold_reason"]):
            self._protection_verification_failure(now, latest or row)
            self._protection_page(now, "P0", "evidence incomplete", latest or row,
                                  "末股止盈恢复需要两轮稳定完整三项证据")
            return
        if complete:
            self._protection_verification_success(latest)
        old_id = row["intent_id"]
        try:
            self.store.archive_protection_identity(now, row)
        except Exception as exc:
            self._protection_page(now, "P0", "unknown", row, f"无法持久保留退休身份：{type(exc).__name__}")
            return
        new_ref = new_id("v6l-protect")
        if overnight_restore:
            token = self._overnight_flow(latest)
            token["replacement_intent_id"] = new_ref
            self.store.set_control(now, f"overnight_tp_flow:{row['position_id']}", json.dumps(token, sort_keys=True))
        old_intent = self.store.intent(old_id)
        if old_intent is not None and old_intent["status"] != IntentStatus.FILLED:
            self.store.update_intent(now, old_id, status=IntentStatus.CANCELLED,
                                     note="confirmed absent before protection replacement")
        self.store.update_protection(now, row["position_id"], intent_id=new_ref,
                                     state="PLANNED", broker_order_id=None, perm_id=None,
                                     confirmed_at=None,
                                     requested_qty=0, confirmed_remaining=0,
                                     pending_max_remaining=0, pending_qty=None,
                                     status_seen_generation=-1)
        fresh = self.store.protection(row["position_id"])
        self._protection_submit_first(now, fresh, held)
        # A replacement closes B when the new broker GTC is confirmed, not
        # when the local placeOrder call returns.
        if for_tp_restore and self.store.protection(row["position_id"])["state"] == "SUBMIT_PENDING":
            self.store.update_protection(now, row["position_id"], state="RESTORE_SUBMIT_PENDING",
                                         gap_a_started_at=None)

    def _protection_close_b(self, now: datetime, row, outcome: str) -> None:
        final_tp_closed = self._final_tp_close_fact(now, row)
        self.store.delete_control(f"overnight_tp_flow:{row['position_id']}")
        self.alerter.resolve("P1", f"protection tp not sent {row['symbol']}")
        if row["gap_b_started_at"]:
            secs = (now - datetime.fromisoformat(row["gap_b_started_at"])).total_seconds()
            log.info("[PROTECT_GAP] kind=B secs=%.3f symbol=%s ref=%s outcome=%s",
                     secs, row["symbol"], row["intent_id"], outcome)
            workflow = self._tp_workflow(row)
            self.store.audit(now, "PROTECT_GAP", gap_kind="B", secs=secs, symbol=row["symbol"],
                             position_id=row["position_id"], workflow_id=workflow.get("workflow_id"),
                             tp_intent_id=row["tp_intent_id"], started_at=row["gap_b_started_at"],
                             ended_at=now.isoformat(), intent_id=row["intent_id"], outcome=outcome)
            self._tp_record(now, row, "gap_close", gap_b_secs=secs, ended_at=now.isoformat(), outcome=outcome)
        pos = self.store.position(row["position_id"])
        final_state = ("CANCELLED" if final_tp_closed else
                       "FILLED" if row["state"] == "FILLED" and
                       (pos is None or pos["status"] != "OPEN") else "WORKING")
        self.store.update_protection(now, row["position_id"], gap_b_started_at=None,
                                     tp_stage=None, tp_qty=None, tp_intent_id=None, state=final_state)

"""Closed-trade commission estimates and read-only broker repair.

No sizing, daily-stop, order, protection or TP authority lives here. A repair
can only update the commission of an already-booked, identity-matching fill.
"""
from __future__ import annotations
import hashlib
import json
import logging
import math
from datetime import datetime, timedelta
from statistics import median
from v6lite.alerts import AlertCard, INFO_GREEN
from v6lite.market_calendar import ET, session_for, trading_day
from v6lite.telemetry import redact_accounts

log = logging.getLogger("v6lite.engine")
BASIS = "closed_commission_basis:"
LATCH = "cooldown_triggered_until"
RECHECK = timedelta(minutes=5)


def _json(store, key):
    try:
        value = json.loads(store.control(key) or "null")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _fill_identity(fill):
    return [fill[name] for name in ("symbol", "side", "quantity", "price", "broker_order_id", "perm_id")]


def estimate_fill_commission(store, cfg, fill, at):
    """Bounded actual comparable executions; zero/rebates never erase the floor."""
    floor = cfg.commission_estimate_min_usd if cfg is not None else 1.0
    samples = store.conn.execute(
        "SELECT f.exec_id,c.commission FROM fills f JOIN commissions c ON c.exec_id=f.exec_id "
        "WHERE f.symbol=? AND f.side=? AND f.quantity=? AND julianday(f.time)>=julianday(?) "
        "AND julianday(f.time)<=julianday(?) AND f.exec_id!=? AND c.commission>=0 "
        "ORDER BY julianday(f.time) DESC,f.exec_id DESC LIMIT 20",
        (fill["symbol"],fill["side"],fill["quantity"],(at-timedelta(days=30)).isoformat(),
         at.isoformat(),fill["exec_id"])).fetchall()
    samples = [s for s in samples if isinstance(s["commission"],(int,float)) and math.isfinite(s["commission"])]
    return {"exec_id":fill["exec_id"],"identity":_fill_identity(fill),
        "amount":max(floor,median(s["commission"] for s in samples)) if samples else floor,
        "source":"recent_same_symbol_side_quantity_median" if samples else "config_minimum",
        "minimum_usd":floor,"sample_exec_ids":[s["exec_id"] for s in samples]}


def fee_breakdown(trade, store=None, cfg=None):
    """Actual reported fees plus per-execution estimates, never the sizing reserve.

    Runtime callers supply Store/config. Context-free legacy callers use the
    default accounting floor only; they cannot infer historical samples.
    """
    floor = cfg.commission_estimate_min_usd if cfg is not None else 1.0
    basis = _json(store, BASIS+trade["position_id"]) if store is not None and isinstance(trade["position_id"],str) else {}
    frozen = basis.get("estimates", {}) if basis.get("version") == 30 else {}
    if not isinstance(frozen,dict):
        frozen = {}
    rows = list(store.conn.execute(
        "SELECT f.* FROM fills f LEFT JOIN commissions c ON c.exec_id=f.exec_id "
        "WHERE f.position_id=? AND c.commission IS NULL ORDER BY f.time,f.exec_id",
        (trade["position_id"],))) if store is not None else []
    estimates = []
    for fill in rows:
        saved = frozen.get(fill["exec_id"], {})
        if (isinstance(saved,dict) and saved.get("identity") == _fill_identity(fill) and
                _positive(saved.get("amount"))):
            estimate = dict(saved)  # immutable close basis, even if future config/history changes
        else:
            estimate = estimate_fill_commission(store,cfg,fill,trade["closed_at"])
        estimates.append(estimate)
    if not rows and trade["missing"]:
        # Compatibility for an isolated trade dictionary, with no ledger context.
        estimates = [{"amount":floor,"source":"config_minimum","exec_id":None,
                      "minimum_usd":floor,"sample_exec_ids":[]} for _ in trade["missing"]]
    estimated = sum(e["amount"] for e in estimates)
    return {"reported_net":trade["gross"]-trade["fees"],"estimated_net":trade["gross"]-trade["fees"]-estimated,
            "estimated_fees":estimated,"estimates":estimates,"missing_count":len(trade["missing"])}


def freeze_estimate(store, cfg, now, trade):
    """Freeze what was known at close/first recovery; later history cannot rewrite it."""
    result = fee_breakdown(trade, store, cfg)
    key = BASIS+trade["position_id"]
    if store.control(key) is None:
        snapshot = dict(result, version=30, position_id=trade["position_id"],symbol=trade["symbol"],
                        observed_at=now.isoformat(),gross=trade["gross"],reported_fees=trade["fees"],
                        estimates={e["exec_id"]:e for e in result["estimates"] if e["exec_id"]})
        with store.tx():
            store.set_control(now,key,json.dumps(snapshot,sort_keys=True))
            store.audit(now,"COMMISSION_ESTIMATE_BASIS",**snapshot)
    return result


def effective_cooldown(store, derived):
    values = [derived] if derived is not None else []
    for key in (LATCH,"cooldown_until"):
        try:
            value = datetime.fromisoformat(store.control(key) or "")
            if value.tzinfo is not None:
                values.append(value)
        except (ValueError,TypeError):
            pass
    return max(values) if values else None


def closed_trade_detail(trade, result):
    if result["missing_count"]:
        return (f"毛盈亏 {trade['gross']:.2f} USD；佣金已回报 {trade['fees']:.2f} + "
                f"估算 {result['estimated_fees']:.2f} USD；已回报净值 {result['reported_net']:.2f} USD；"
                f"估算净值 {result['estimated_net']:.2f} USD（{result['missing_count']} 笔佣金未回报）")
    return f"毛盈亏 {trade['gross']:.2f} USD；佣金 {trade['fees']:.2f} USD；最终净值 {result['reported_net']:.2f} USD（佣金已全部回报）"


class CommissionMixin:
    def _arm_close_card_deadline(self, now, position_id):
        pending = _json(self.store, "close_card_pending:"+position_id)
        schedule = getattr(self.broker, "schedule_local_deadline", None)
        if not pending or not callable(schedule):
            return
        deadline = datetime.fromisoformat(pending['deadline'])
        def deliver():
            if not self._shutting_down:
                self._hard_protection_deadlines(self.clock.now())
                self._flush_closed_cards(self.clock.now())
        schedule(max(0., (deadline-now).total_seconds()), deliver)

    def _flush_closed_cards(self, now):
        # A local notification continuation, never a broker query or sleep.
        for key in self.store.controls_with_prefix("close_card_pending:"):
            pending = _json(self.store, key)
            if not pending:
                continue
            pid = pending['position_id']
            sent = "close_card_sent:"+pid
            if self.store.control(sent) is not None:
                self.store.delete_control(key)
                continue
            trade = next((t for t in self.store.closed_trades() if t['position_id']==pid), None)
            if trade is None:
                continue
            fee = self.store.conn.execute(
                "SELECT c.commission FROM fills f JOIN commissions c ON c.exec_id=f.exec_id "
                "WHERE f.position_id=? AND f.exec_id=?", (pid,pending['closing_exec_id'])).fetchone()
            if fee is None and now < datetime.fromisoformat(pending['deadline']):
                continue
            result = fee_breakdown(trade,self.store,self.cfg)
            detail = trade['symbol']+" "+closed_trade_detail(trade,result)
            card = AlertCard("🟢 持仓已关闭", INFO_GREEN,
                (("标的与持仓", f"{trade['symbol']}；positionId={pid}；剩余持仓 0 股"),
                 ("盈亏", detail),
                 ("说明", "平仓佣金最多等待 3 秒；缺佣金按近期同标的同方向同数量实际佣金估算，配置最低值兜底；实际回报后复算，已触发冷却不自动解除。")))
            ticket = self.alerter.send(now,'INFO',f"{trade['symbol']} position closed {pid}",detail,card=card)
            with self.store.tx():
                self.store.set_control(now,sent,json.dumps({'at':now.isoformat(),'delivery_id':ticket}))
                self.store.delete_control(key)
                self.store.audit(now,'CLOSE_CARD_SENT',position_id=pid,closing_exec_id=pending['closing_exec_id'],
                    final=result['missing_count']==0,deadline=pending['deadline'],closing_fee_received=fee is not None)

    def _commission_updated(self, now, exec_id):
        self._flush_closed_cards(now)
        row = self.store.conn.execute("SELECT position_id,trading_day FROM fills WHERE exec_id=?",(exec_id,)).fetchone()
        if row is None:
            return  # a commission may legitimately precede local fill booking
        current_day = trading_day(now).isoformat()
        if row["trading_day"]==current_day and self.store.control("report:"+current_day):
            self.store.set_control(now,"commission_report_dirty:"+current_day,exec_id)
        trade = next((t for t in self.store.closed_trades() if t["position_id"]==row["position_id"]),None)
        if trade is None:
            return
        day = trade["closed_at"].astimezone(ET).date().isoformat()
        if day==trading_day(now).isoformat() and self.store.control("report:"+day):
            # Local report refresh is deferred to the ordinary report phase,
            # never written from a broker callback and never re-sent remotely.
            self.store.set_control(now,"commission_report_dirty:"+day,exec_id)
        if trade["missing"]:
            return
        basis = _json(self.store,BASIS+trade["position_id"])
        if not basis or not _positive(basis.get("estimated_fees")):
            return
        final = trade["gross"]-trade["fees"]
        key = "commission_final_net:"+trade["position_id"]
        previous = _json(self.store,key)
        prior = previous.get("net",basis["estimated_net"])
        sign = lambda n: (n>0)-(n<0)
        if previous.get("net") == final:
            return
        with self.store.tx():
            self.store.set_control(now,key,json.dumps({"net":final,"fees":trade["fees"]}))
            self.store.audit(now,"COMMISSION_FINALIZED",position_id=trade["position_id"],symbol=trade["symbol"],
                             estimated_net=basis["estimated_net"],final_net=final)
            if sign(prior)!=sign(final):
                self.store.audit(now,"COMMISSION_NET_SIGN_CHANGED",position_id=trade["position_id"],
                    symbol=trade["symbol"],previous_net=prior,estimated_net=basis["estimated_net"],
                    final_net=final,cooldown_until=self.store.control("cooldown_until"),auto_release=False)
        if sign(prior)!=sign(final):
            detail = (f"{trade['symbol']} positionId={trade['position_id']}；佣金补齐后净值符号改变，"
                      f"原估算净值 {basis['estimated_net']:.2f}，最终净值 {final:.2f} USD；"
                      f"已触发冷却不自动解除，原截止 {self.store.control('cooldown_until') or '无'}。"
                      "真实费用形成新亏损时原风控继续适用。")
            revision = hashlib.sha256(f"{trade['position_id']}:{trade['fees']}".encode()).hexdigest()[:12]
            self.alerter.send(now,"INFO",f"佣金补齐，净值符号改变 {trade['position_id']} {revision}",detail,
                card=AlertCard("🟢 佣金补齐，净值符号改变",INFO_GREEN,(("发生了什么",detail),
                    ("你要做什么","核对最终账本；是否解除既有冷却由操作员复核，系统不自动解除。"))))

    def _refresh_commission_report(self, now, day, *, at_shutdown=False):
        key = "commission_report_dirty:"+day.isoformat()
        if (self.report_dir is None or self.store.control(key) is None or
                (self._protection_priority_due and not at_shutdown)):
            return
        attempt = "commission_report_refresh_at:"+day.isoformat()
        prior = self.store.control(attempt)
        if prior:
            try:
                if now-datetime.fromisoformat(prior)<timedelta(minutes=1):
                    return
            except (ValueError,TypeError):
                return
        self.store.set_control(now,attempt,now.isoformat())
        try:
            from v6lite.report import write_daily_report
            path,_ = write_daily_report(self.store,self.cfg,day,self.report_dir,
                quotes=self.quote_availability_stats(day,include_scope=True))
            with self.store.tx():
                self.store.delete_control(key)
                self.store.delete_control(attempt)  # only failures are throttled; new actual fees may flush immediately
                self.store.audit(now,"COMMISSION_REPORT_REFRESHED",day=day.isoformat(),
                                 path=path.name,remote_card_sent=False)
        except Exception as exc:
            log.warning("local commission report refresh deferred: %s",redact_accounts(str(exc),self.cfg.account))
            # A local artifact failure cannot interfere with protection or the
            # original acknowledged daily-delivery state; retry at most 1/min.

    def _commission_idle(self, now):
        session = session_for(trading_day(now))
        if (not self._started or self.dry_run or self._in_urgent_callback or self._shutting_down or self._recovering or
                self._queue or self._processing_queue or
                self._recovery_pending or self._protection_priority_due or not self.broker.is_connected() or
                not self.broker.is_synchronized() or session is None or now>=session.close_utc):
            return False
        if any(i["purpose"]!="PROTECT" for i in self.store.active_intents()):
            return False
        for pos in self.store.open_positions():
            row = self.store.protection(pos["position_id"])
            if row is None or not self._protection_steady(row) or row["gap_a_started_at"] or row["gap_b_started_at"]:
                return False
        return True

    def _refresh_missing_commissions(self, now):
        """One durable attempt per 300s through actual exchange close, only in idle turns."""
        if not self._commission_idle(now):
            return
        day = trading_day(now).isoformat()
        def pending():
            return [t for t in self.store.closed_trades() if t["missing"] and
                    t["closed_at"].astimezone(ET).date().isoformat()==day]
        trades = pending()
        if not trades:
            return
        key = "commission_recheck_at:"+day
        prior = self.store.control(key)
        if prior:
            try:
                if now-datetime.fromisoformat(prior)<RECHECK:
                    return
            except (ValueError,TypeError):
                return  # invalid durable rate-limit state never authorizes a query storm
        self._protect("query")
        now = self.clock.now()
        if not self._commission_idle(now) or trading_day(now).isoformat()!=day:
            return
        trades = pending()
        if not trades:
            return
        for trade in trades:
            freeze_estimate(self.store,self.cfg,now,trade)
        pids = {t["position_id"] for t in trades}
        targets = {r["exec_id"]:dict(r) for r in self.store.conn.execute(
            "SELECT f.* FROM fills f LEFT JOIN commissions c ON c.exec_id=f.exec_id "
            "WHERE c.commission IS NULL") if r["position_id"] in pids}
        with self.store.tx():
            # The slot is committed before wire; a failure/restart also consumes it.
            latest = self.store.control(key)
            if latest:
                try:
                    if now-datetime.fromisoformat(latest)<RECHECK:
                        return
                except (ValueError,TypeError):
                    return
            self.store.set_control(now,key,now.isoformat())
            self.store.audit(now,"COMMISSION_RECHECK_REQUESTED",day=day,exec_ids=sorted(targets))
        changed = []
        try:
            rows = self.broker.executions_today()  # existing paced executions channel
            for fill in rows:
                known = targets.get(fill.exec_id)
                if known is None or fill.commission is None:
                    continue
                identity = [fill.symbol,str(fill.side),fill.quantity,fill.price,fill.broker_order_id,fill.perm_id]
                if (identity!=_fill_identity(known) or fill.intent_id!=known["intent_id"] or
                        not isinstance(fill.commission,(int,float)) or isinstance(fill.commission,bool) or
                        not math.isfinite(fill.commission) or fill.commission>=1e9):
                    self.store.audit(self.clock.now(),"COMMISSION_RECHECK_MISMATCH",exec_id=fill.exec_id,
                                     reason="known execution identity or commission invalid; no economic/fee write")
                    continue
                with self.store.tx():
                    added = self.store.record_commission(self.clock.now(),fill.exec_id,fill.commission)
                if added:
                    changed.append(fill.exec_id)
                    self._commission_updated(self.clock.now(),fill.exec_id)
            self.store.audit(self.clock.now(),"COMMISSION_RECHECK_RESULT",day=day,changed=changed,
                             remaining=sum(len(t["missing"]) for t in pending()))
        except Exception as exc:
            reason = redact_accounts(str(exc),self.cfg.account)
            self.store.audit(self.clock.now(),"COMMISSION_RECHECK_FAILED",day=day,reason=reason)
            log.warning("commission repair query deferred: %s",reason)

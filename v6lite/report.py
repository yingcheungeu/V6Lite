"""End-of-day report: replaces the lapsed manual daily-review habit with an
automatic one (written to <state_dir>/reports/ and sent through alerts)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from v6lite.alerts import AlertCard, INFO_GREEN, P1_ORANGE
from v6lite.config import LiteConfig
from v6lite.market_calendar import ET
from v6lite.store import Store
from v6lite.report_evidence import injected_gap, fill_bid_comparison
from v6lite.commissions import fee_breakdown, closed_trade_detail, estimate_fill_commission
from v6lite.execution_time import historical_receipt_before
from v6lite.telemetry import mask_account, redact_accounts


def _event_payload(event):
    """The full local report keeps every durable original diagnostic."""
    raw = event["payload"]
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return raw  # invalid evidence cannot safely be summarized as complete
    def has_reason(value):
        if isinstance(value, dict):
            return any((key in {"reason", "full_error", "broker_reason", "rejection_reason", "message", "note", "detail"} and
                        isinstance(item, str) and bool(item)) or has_reason(item)
                       for key, item in value.items())
        return isinstance(value, list) and any(has_reason(item) for item in value)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) if event["kind"] == "TP_EVIDENCE" or has_reason(payload) else raw[:160]


def _protection_health(store: Store, day: date, *, now: datetime | None = None) -> str:
    if now is None:
        clock = getattr(store, "report_clock", None)
        if clock is not None:
            now = clock.now()
        else:
            # A Store reopened independently of Engine has no process clock.
            # Durable audit timestamps still establish that confirmation has
            # already happened, even when replaying a future-dated fixture.
            now = datetime.now(timezone.utc)
            # Deduplicated observation rows no longer move MAX(audit.ts) on
            # every refresh. The identical durable row timestamps and the
            # committed process observation clock preserve that replay cutoff.
            latest = store.conn.execute("SELECT MAX(ts) FROM audit").fetchone()[0]
            row_times = store.conn.execute("SELECT MAX(updated_at),MAX(confirmed_at) FROM protections").fetchone()
            for stamp in (latest, *row_times):
                if stamp:
                    now = max(now, datetime.fromisoformat(stamp))
            observed = getattr(getattr(store, "_observations", None), "latest", None)
            if observed is not None:
                now = max(now, observed)
    held = store.open_positions()
    parts = []
    for pos in held:
        row = store.protection(pos["position_id"])
        if row is None:
            parts.append(f"{pos['symbol']} 缺失 P={pos['qty_open']}")
        else:
            # A local WORKING row is not proof that a GTC still exists.
            # The report is after close; only a recent same-day snapshot and
            # an explicitly connected status can be described as evidence.
            confirmed_today = False
            if row["confirmed_at"]:
                try:
                    confirmed_today = datetime.fromisoformat(row["confirmed_at"]).astimezone(ET).date() == day
                except ValueError:
                    pass
            confirmed = (f"截至 {row['confirmed_at']} 最近一次券商快照确认，当前仍需核实"
                         if confirmed_today else "券商存在性未核实或证据过期")
            stop_label = ("止损价（触发后市价成交）" if row["order_type"] == "STP" else "旧合同止损价（需人工处理）")
            parts.append(f"{pos['symbol']} {confirmed}，本地状态={row['state']} "
                         f"P={pos['qty_open']} S={row['confirmed_remaining']} "
                         f"类型={row['order_type']} {stop_label}={row['trigger_price']:.2f} "
                         f"orderId={row['broker_order_id'] or '-'}")
    start = datetime.combine(day, time(0, 0), tzinfo=ET).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=ET).astimezone(timezone.utc)
    # A closed cross-midnight gap is audited on its end date. Reconstruct its
    # interval so the preceding day's report includes the actual exposure.
    # An open gap is capped at this report's date end (or now for today), and
    # a future start is never counted in an earlier historical report.
    cutoff = min(now, end)
    maxima = {"A": 0.0, "B": 0.0, "B_test": 0.0}
    recovered_notes: list[str] = []
    for event in store.conn.execute(
            "SELECT ts,payload FROM audit WHERE kind='PROTECT_GAP' AND ts>=? ORDER BY id",
            (start.isoformat(),)).fetchall():
        try:
            payload = json.loads(event["payload"])
            kind = payload.get("gap_kind")
            if kind in maxima:
                closed_at = datetime.fromisoformat(event["ts"])
                full_seconds = float(payload["secs"])
                opened_at = closed_at - timedelta(seconds=full_seconds)
                overlap = (min(closed_at, cutoff) - max(opened_at, start)).total_seconds()
                if overlap > 0:
                    # Attribute a cross-midnight gap to every day it touches,
                    # but evaluate the G3 limit with its full duration. A
                    # six-second window must not appear as two safe three-
                    # second windows in adjacent daily reports.
                    bucket = "B_test" if kind=="B" and injected_gap(store,payload) else kind
                    maxima[bucket] = max(maxima[bucket], full_seconds)
                    if payload.get("recovered_unverified"):
                        recovered_notes.append(f"{kind} 回补窗口未验证")
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    open_a = store.open_protection_gaps_a()
    earliest_open_a: dict[str, datetime] = {}
    ongoing: dict[str, list[str]] = {"A": [], "B": [], "B_test": []}

    def include_open(kind: str, started_text: str, position_id: str,
                     exec_id: str | None = None) -> None:
        try:
            started = datetime.fromisoformat(started_text)
            if cutoff <= start or started >= cutoff:
                return
            elapsed = max(0.0, (cutoff - started).total_seconds())
            maxima[kind] = max(maxima[kind], elapsed)
            cross_day = "，跨日" if started.astimezone(ET).date() < day else ""
            recovered = ("，回补窗口未验证" if exec_id and
                         store.control(f"recovered_gap_unverified:{exec_id}") is not None else "")
            ongoing[kind].append(f"{position_id} {elapsed:.3f}s{cross_day}{recovered}")
        except (TypeError, ValueError):
            ongoing[kind].append(f"{position_id} 起点待核实")

    for item in open_a:
        try:
            opened = datetime.fromisoformat(item["started_at"])
            prior = earliest_open_a.get(item["position_id"])
            if prior is None or opened < prior:
                earliest_open_a[item["position_id"]] = opened
        except ValueError:
            pass
        include_open("A", item["started_at"], item["position_id"], item["exec_id"])
    for row in store.protections():
        if row["gap_a_started_at"]:
            try:
                legacy_start = datetime.fromisoformat(row["gap_a_started_at"])
                first_exec = earliest_open_a.get(row["position_id"])
                if first_exec is None or legacy_start < first_exec:
                    include_open("A", row["gap_a_started_at"], row["position_id"])
            except ValueError:
                include_open("A", row["gap_a_started_at"], row["position_id"])
        if row["gap_b_started_at"]:
            workflow = {}
            try:
                workflow = json.loads(store.control("tp_workflow:"+row["position_id"]) or "{}")
            except (ValueError,TypeError):
                pass
            gap = dict(row);gap["workflow_id"] = workflow.get("workflow_id") if isinstance(workflow,dict) else None
            kind = "B_test" if injected_gap(store,gap) else "B"
            include_open(kind, row["gap_b_started_at"], row["position_id"])

    suffix = ""
    if any(ongoing.values()):
        pending = "; ".join(f"{kind}: {', '.join(items)}" for kind, items in ongoing.items() if items)
        suffix = f"；空窗进行中/未闭合（券商终态未核实）：{pending}"
    if recovered_notes:
        suffix += "；" + "、".join(sorted(set(recovered_notes)))
    executions = []
    # Attribute delayed callbacks/query replays to the execution's ledger day,
    # not the audit receive date. PROTECT_FILL includes current and retired IDs.
    for event in store.conn.execute(
            "SELECT a.payload FROM audit a JOIN fills f ON json_extract(a.payload,'$.exec_id')=f.exec_id "
            "WHERE a.kind='PROTECT_FILL' AND f.trading_day=? ORDER BY f.time,a.id", (day.isoformat(),)):
        item = json.loads(event["payload"])
        stop, usd, bps = item.get("stop_price"), item.get("slippage_usd"), item.get("slippage_bps")
        price = float(item["fill_price"])
        stop_text = f"{stop:.2f}" if stop is not None else "未知（改价确认未定）"
        slip_text = f"{usd:.2f} USD / {bps:.2f} bps" if usd is not None and bps is not None else "未知"
        executions.append(f"{item['symbol']} execId={item['exec_id']} 数量={item['quantity']} "
                          f"止损价={stop_text} 成交价={price:.2f} "
                          f"成交均价={float(item['avg_fill_price']):.2f} 滑点={slip_text}" +
                          ("（退休保护单）" if item.get("retired") else ""))
    if executions:
        suffix += "；当日止损成交：" + "；".join(executions)
    suffix += _tp_health(store, day, start, cutoff)
    compliance = "超限" if maxima["B"]>5 else ("待闭合（当前未超限）" if ongoing["B"] else "合规")
    return (("；".join(parts) or "无持仓") +
            f"；空窗统计 A={maxima['A']:.3f}s；B（正常）最大 {maxima['B']:.3f}s；"
            f"B（测试注入）{maxima['B_test']:.3f}s；正常止盈 B ≤ 5 秒：{compliance}" + suffix)


def _tp_health(store, day, start, cutoff):
    """TP evidence follows its workflow and execution ledger day, not receive day."""
    lines = []
    for event in store.conn.execute(
            "SELECT a.payload,f.* FROM audit a JOIN fills f ON json_extract(a.payload,'$.exec_id')=f.exec_id "
            "WHERE a.kind='TP_EVIDENCE' AND json_extract(a.payload,'$.phase')='fill' "
            "AND f.trading_day=? ORDER BY f.time,a.id", (day.isoformat(),)):
        value = json.loads(event["payload"])
        bid, difference, unknown_reason = fill_bid_comparison(store,value,event)
        bid_text = f"{bid:.2f}" if bid is not None else "未知"
        delta = f"{difference:+.4f} USD/股" if difference is not None else f"未知（{unknown_reason}）"
        gap = f"{value['gap_b_secs']:.3f}s" if value.get("gap_b_secs") is not None else "未知"
        lines.append(f"{value['symbol']} TP orderRef={value['intent_id']} execId={value['exec_id']} "
                     f"数量={value['quantity']} 成交价={value['fill_price']:.2f} "
                     f"发送时 bid={bid_text} 成交相对 bid={delta} B空窗={gap}")
    for event in store.conn.execute("SELECT ts,payload FROM audit WHERE kind='TP_EVIDENCE' ORDER BY id"):
        value = json.loads(event["payload"])
        if value.get("phase") != "gap_close" or not value.get("gap_b_started_at"):
            continue
        opened = datetime.fromisoformat(value["gap_b_started_at"])
        closed = datetime.fromisoformat(value.get("ended_at", event["ts"]))
        if min(closed, cutoff) > max(opened, start):
            lines.append(f"TP workflow={value.get('workflow_id')} orderRef={value.get('intent_id')} "
                         f"B空窗={value['gap_b_secs']:.3f}s（完整窗口，含查询耗时）")
    for key, raw in store.controls_with_prefix(f"tp_quote_skip:{day.isoformat()}:").items():
        try:
            count = json.loads(raw)["count"]
        except (ValueError, TypeError, KeyError):
            count = "未知"
        lines.append(f"{key.split(':',2)[2]} 报价不新鲜/无效跳过轮数={count}（不是日志条数）")
    if not lines:
        return ""
    return "；当日止盈留证：" + "；".join(lines)


def execution_label(mode: str, *, dry_run: bool) -> str:
    """Display actual order permission independently of Paper test sizing."""
    if dry_run:
        return "演练（不下单）"
    if mode == "paper":
        return "真实下单（Paper）"
    if mode == "live":
        return "真实下单（Live）"
    raise ValueError("unknown execution mode")


def _quote_scope(store: Store, day: date, quotes: dict) -> dict:
    start = datetime.combine(day, time(0, 0), tzinfo=ET).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=ET).astimezone(timezone.utc)
    starts, first = store.conn.execute(
        "SELECT COUNT(*),MIN(ts) FROM audit WHERE kind='STARTUP' AND ts>=? AND ts<?",
        (start.isoformat(), end.isoformat())).fetchone()
    # A previous-day process can run this morning and restart only once today.
    # Require durable runtime evidence before today's first STARTUP; yesterday's
    # STARTUP alone must not label an ordinary daily launch as a same-day restart.
    earlier_runtime = first is not None and store.conn.execute(
        "SELECT 1 FROM audit WHERE ts>=? AND ts<? AND kind IN "
        "('SCAN','DRY_RUN','FILL','PROTECT_FILL','ORDER_PRICE_OBSERVATION',"
        "'PROTECT_STATE','OBSERVATION_SUMMARY','SHUTDOWN','SHUTDOWN_REQUESTED',"
        "'RECONNECT_ATTEMPT','RECONNECT_BACKOFF','RECONNECT_STOPPED','BROKER_QUERY_ERROR') LIMIT 1",
        (start.isoformat(), first)).fetchone() is not None
    return {**quotes, "restarted_today": starts > 1 or earlier_runtime}


def _quote_availability_line(cfg: LiteConfig, quotes: dict) -> str:
    total, usable = quotes.get("total", 0), quotes.get("usable", 0)
    rate = f"{100 * usable / total:.0f}%" if total else "n/a"
    first = quotes.get("started_at")
    since = (first.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S PT")
             if first is not None else "暂无采样起始时间")
    restart = "；当天有过重启" if quotes.get("restarted_today") else ""
    return (f"{rate} RTH availability · subscribed {quotes.get('subscribed', 0)} "
            f"· {usable}/{total} symbol-samples · {quotes.get('samples', 0)} samples "
            f"(this process today; 采样起始 {since}{restart}; "
            f"{'DELAYED' if cfg.market_data_type == 3 else 'LIVE'})")


# Level is not uniformly persisted. Classify a conservative safety superset
# ahead of ordinary scan/dry observations; the full attachment retains both.
_ORDINARY_EVENT_KINDS = ('SCAN','DRY_RUN','EXECUTION_TIME_OBSERVED',
    'CONNECTION_SNAPSHOT_PRICE','CONNECTION_SNAPSHOT_STATUS','PROTECT_BROKER_ECHO')


def _event_groups_sql():
    from v6lite.report_artifacts import OBSERVATION_EXCLUSION
    ordinary = ','.join("'"+kind+"'" for kind in _ORDINARY_EVENT_KINDS)
    return """WITH candidates AS (
        SELECT id,ts,kind,payload,CASE WHEN kind IN ("""+ordinary+""") THEN 0 ELSE 1 END AS critical
        FROM audit WHERE ts>=? AND ts<? AND """+OBSERVATION_EXCLUSION+"""
          AND (kind NOT IN ('FILL','PROTECT_FILL','PROTECT_RETIRED_FILL','CLOSE_CARD_SENT')
            OR NOT EXISTS (SELECT 1 FROM fills f
              WHERE f.exec_id=COALESCE(
                json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.exec_id'),
                json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.closing_exec_id'))
                AND f.trading_day!=substr(?1,1,10)))
      ), boundaries AS (
        SELECT *,CASE WHEN LAG(kind) OVER (ORDER BY id)=kind AND
          LAG(payload) OVER (ORDER BY id)=payload THEN 0 ELSE 1 END AS boundary FROM candidates
      ), numbered AS (
        SELECT *,SUM(boundary) OVER (ORDER BY id) AS grp FROM boundaries
      ), grouped AS (
        SELECT MIN(id) AS first_id,MIN(ts) AS ts,MAX(ts) AS last_ts,kind,payload,
          COUNT(*) AS copies,MAX(critical) AS critical FROM numbered GROUP BY grp
      ) """


def _clip_utf8(text, budget, suffix=' …（完整记录见附件或状态库）'):
    raw=text.encode('utf-8')
    if len(raw)<=budget:
        return text
    ending=suffix.encode('utf-8')
    return raw[:max(0,budget-len(ending))].decode('utf-8',errors='ignore')+suffix


def _event_line(event):
    clock=event['ts'][11:19]+'Z'
    if event['copies']>1:
        clock+='–'+event['last_ts'][11:19]+'Z'
    return ('- '+clock+' '+event['kind']+' '+_event_payload(event)+
            (f" ×{event['copies']}" if event['copies']>1 else ''))


def _events_section(store,start,end,*,byte_budget,account=None):
    """SQLite aggregates and at most the main report's display-row budget."""
    args=(start.isoformat(),end.isoformat())
    lines=['','| Kind | Count | First UTC | Last UTC |','|---|---:|---|---|']
    for row in store.conn.execute('SELECT kind,COUNT(*) AS n,MIN(ts) AS first_at,MAX(ts) AS last_at '
            'FROM audit WHERE ts>=? AND ts<? GROUP BY kind ORDER BY kind',args):
        if len(lines)>=100:
            lines.append('- 其余类型统计及全部记录见状态库 audit 表。');break
        lines.append(redact_accounts(f"| {row['kind']} | {row['n']:,} | {row['first_at']} | {row['last_at']} |",account))
    lines+=['','### Important events']
    sql=_event_groups_sql()
    total=store.conn.execute(sql+'SELECT COALESCE(SUM(copies),0) FROM grouped',args).fetchone()[0]
    shown=0
    used=len(('\n'.join(lines)+'\n').encode('utf-8'))
    # The newline immediately following '## Events' is also a rendered line.
    # Reserve a final omission line and a spare boundary line under 200.
    slots=max(0,198-len(lines)-1)
    selected=[]
    for event in store.conn.execute(sql+'SELECT * FROM grouped ORDER BY critical DESC,ts,first_id LIMIT ?',(*args,slots)):
        # Retain full durable broker text whenever the actual remaining
        # report budget allows it. An arbitrary per-row cap loses valid reasons.
        allowance=byte_budget-used-180
        if allowance<180:
            break
        # Budget the exact output text: masking a supported short account
        # token can expand it, so counting raw payload bytes is unsafe.
        line=_clip_utf8(redact_accounts(_event_line(event),account),allowance)
        selected.append((event['ts'],event['first_id'],line))
        used+=len((line+'\n').encode('utf-8'));shown+=event['copies']
    # Safety priority decides bounded selection/byte allocation; display the
    # chosen rows chronologically, including out-of-order audit insertion.
    lines.extend(line for _,_,line in sorted(selected))
    if total>shown:
        lines.append(f'- 另有 {total-shown} 条，完整记录在附件及状态库 audit 表。')
    elif total==0:
        lines.append('- none')
    return lines


def _bounded_prefix(lines,account,budget=24*1024):
    output=[];used=0
    for line in lines:
        line=_clip_utf8(redact_accounts(line,account),1800)
        cost=len((line+'\n').encode('utf-8'))
        if used+cost+180>budget:
            output.append('- 主报前置明细已压缩；完整关键事实见附件及状态库 audit/fills。')
            break
        output.append(line);used+=cost
    return output


_RENDER_ONLY_NOTE = '- 本次仅渲染主报，未导出附件；完整关键事实保留在状态库 audit/fills（可用 report 命令导出）。'


def _attachment_metadata(attachment):
    return [f"- 完整审计与账本附件：[{attachment['filename']}]({attachment['filename']})（共 {attachment['count']} 条事实，"
            f"审计 {attachment.get('audit_rows',0)} 条，账本 {attachment.get('ledger_rows',0)} 条；保守安全超集，并非全部为 P0）",
            f"- 附件 SHA-256：{attachment['sha256']}"]


def write_daily_report(store,cfg,day,directory,*,quotes=None):
    from pathlib import Path
    from v6lite.report_artifacts import write_fact_attachment
    # Preserve one actual render before local mkdir/write can fail. Reserve
    # sufficient metadata space without rendering twice or inventing an export.
    text=daily_report(store,cfg,day,quotes=quotes)
    metadata=write_fact_attachment(store,cfg,day,directory)
    published='\n'.join(_attachment_metadata(metadata))
    if _RENDER_ONLY_NOTE in text:
        text=text.replace(_RENDER_ONLY_NOTE,published,1)
    else:
        text=published+'\n'+text
    text=_clip_utf8(redact_accounts(text,cfg.account),100*1024)
    path=Path(directory)/(day.isoformat()+'.md')
    path.write_text(text,encoding='utf-8')
    return path,text


def _test_tp_timeout_lines(store, start, end):
    """Bounded per-request lifecycle summary; complete events remain in audit."""
    rows = store.conn.execute("""
        WITH facts AS (
          SELECT id,ts,CASE WHEN json_valid(payload) THEN payload ELSE '{}' END AS p
          FROM audit WHERE kind='TEST_TP_TIMEOUT_RESULT' AND ts>=? AND ts<?
        ), ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY COALESCE(json_extract(p,'$.request_id'),'unbound:'||id),
                         json_extract(p,'$.restored_S') IS NOT NULL
            ORDER BY id DESC) AS restored_rank FROM facts
        )
        SELECT MIN(ts) AS at,MAX(ts) AS last_at,COUNT(*) OVER() AS total,
          substr(COALESCE(json_extract(p,'$.request_id'),'unbound:'||id),1,48) AS request_id,
          substr(MAX(json_extract(p,'$.symbol')),1,16) AS symbol,
          group_concat(DISTINCT json_extract(p,'$.phase')) AS phases,
          MAX(json_extract(p,'$.ask')) AS ask,MAX(json_extract(p,'$.limit_price')) AS limit_price,
          MAX(json_extract(p,'$.orderId')) AS orderId,
          MAX(CASE WHEN json_extract(p,'$.phase')='cancel_request' THEN ts END) AS cancel_request,
          MAX(CASE WHEN json_extract(p,'$.phase')='cancel_confirm' THEN ts END) AS cancel_confirm,
          MAX(CASE WHEN json_extract(p,'$.phase')='restore_confirm' THEN ts END) AS restore_confirm,
          MAX(CASE WHEN restored_rank=1 THEN json_extract(p,'$.restored_S') END) AS restored_S,
          MAX(json_extract(p,'$.gap_b_seconds')) AS gap_b_seconds
        FROM ranked GROUP BY COALESCE(json_extract(p,'$.request_id'),'unbound:'||id)
        ORDER BY MAX(id) DESC LIMIT 12
    """, (start.isoformat(),end.isoformat())).fetchall()
    if not rows:
        return ["- none"]
    fields = ("request_id", "phases", "ask", "limit_price", "orderId", "cancel_request",
              "cancel_confirm", "restore_confirm", "restored_S", "gap_b_seconds")
    lines = ["- " + str(row["at"]) + " " + str(row["symbol"]) + " " +
             " ".join(name+"="+str(row[name])[:160] for name in fields)
             for row in sorted(rows,key=lambda r:r["at"])]
    if rows[0]["total"] > len(rows):
        lines.append(f"- 另有 {rows[0]['total']-len(rows)} 个请求；完整生命周期和拒绝原因见 TEST_TP_TIMEOUT_RESULT audit。")
    return lines


def _tp_open_delay_lines(store, start, end):
    """One durable deferred episode per position/day; actual starts are facts."""
    rows = store.conn.execute(
        "SELECT id,ts,payload FROM audit WHERE kind='TP_START_DEFERRED_OPEN' "
        "AND ts>=? AND ts<? ORDER BY id", (start.isoformat(), end.isoformat())).fetchall()
    lines = [f"- 开盘止盈延后：{len(rows)} 次"]
    for row in rows:
        fact = json.loads(row['payload'])
        actual = store.conn.execute(
            "SELECT ts,json_extract(payload,'$.stage') AS stage FROM audit "
            "WHERE (kind='STAGE_BEGIN' OR (kind='TP_EVIDENCE' "
            "AND json_extract(payload,'$.phase')='reduce_request')) AND ts>=? AND ts<? "
            "AND json_extract(payload,'$.position_id')=? "
            "AND json_extract(payload,'$.stage') IN ('TP1','TP2','TP3') "
            "ORDER BY ts,id LIMIT 1", (row['ts'], end.isoformat(), fact['position_id'])).fetchone()
        outcome = (f"实际开始 {actual['ts']}（{actual['stage']}）" if actual else "尚未开始")
        lines.append(f"- {fact['symbol']} positionId={fact['position_id']} {fact['stage']} "
                     f"延后于 {row['ts']}；预计 {fact['scheduled_start']}；{outcome}")
    return lines


def _commission_day_summary(store, cfg, key):
    rows = list(store.conn.execute("SELECT f.* FROM fills f LEFT JOIN commissions c ON c.exec_id=f.exec_id "
                                  "WHERE f.trading_day=? AND c.commission IS NULL",(key,)))
    amounts = {}
    trades = {t["position_id"]:t for t in store.closed_trades()}
    for fill in rows:
        pid = fill["position_id"]
        clock = getattr(store,"report_clock",None)
        at = clock.now() if clock else datetime.combine(date.fromisoformat(key),time(23,59,59),tzinfo=ET).astimezone(timezone.utc)
        if pid is None:
            amounts.setdefault(None,{})[fill["exec_id"]] = estimate_fill_commission(store,cfg,fill,at)["amount"]
            continue
        if pid not in amounts:
            trade = trades.get(pid) or {"position_id":pid,"symbol":fill["symbol"],"closed_at":at,
                                       "gross":0,"fees":0,"missing":[(fill["quantity"],fill["price"])]}
            result = fee_breakdown(trade,store,cfg)
            amounts[pid] = {e["exec_id"]:e["amount"] for e in result["estimates"]}
    estimated = sum(amounts[f["position_id"]].get(f["exec_id"],cfg.commission_estimate_min_usd) for f in rows)
    return len(rows),estimated


def daily_report(store: Store, cfg: LiteConfig, day: date, *, quotes: dict | None = None,
                 attachment: dict | None = None) -> str:
    key = day.isoformat()
    start = datetime.combine(day, time(0, 0), tzinfo=ET).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    fills = store.conn.execute("SELECT * FROM fills WHERE trading_day=? ORDER BY time LIMIT 200",(key,)).fetchall()
    fill_count=store.conn.execute("SELECT COUNT(*) FROM fills WHERE trading_day=?",(key,)).fetchone()[0]
    realized = store.realized_for_day(key)
    fees = store.fees_for_day(key)
    missing_fees,estimated_fees = _commission_day_summary(store,cfg,key)
    risk = store.risk_day(key)
    releases = store.conn.execute(
        "SELECT symbol, at, ledger_qty, operator, reason FROM releases WHERE at >= ? AND at < ? ORDER BY at",
        (start.isoformat(), end.isoformat())).fetchall()
    frozen = sorted(k.split(":", 1)[1] for k in store.controls_with_prefix("frozen:"))
    ident = store.identity()
    startup = store.conn.execute(
        "SELECT payload FROM audit WHERE kind='STARTUP' AND ts < ? ORDER BY ts DESC LIMIT 1",
        (end.isoformat(),)).fetchone()
    try:
        startup_sha = json.loads(startup["payload"]).get("code_sha", "unknown") if startup else "unknown"
    except (TypeError, ValueError):
        startup_sha = "unknown"
    lines = [
        f"# V6-Lite daily report {key} ({cfg.mode})" + (" TEST" if cfg.test.enabled else ""),
        "",
        f"- account: {mask_account(ident.get('account'))}   state role: {ident.get('role') or 'formal'}",
        f"- code_sha: {startup_sha}",
        *(["- test=1   mode: 🧪 测试模式（仅 Paper）   "
           f"qty={cfg.test.qty}   stop/tp1/tp2/tp3 ATR="
           f"{cfg.test.stop_mult}/{cfg.test.tp1_mult}/{cfg.test.tp2_mult}/{cfg.test.tp3_mult}"]
          if cfg.test.enabled else []),
        f"- market data: {_market_data_line(store, cfg, key, quotes=quotes)}",
        f"- last health market data (instantaneous, not RTH availability): {_instant_market_data_line(store, cfg, key)}",
        "",
        (f"- realized P&L: {realized:.2f}   fees: {fees:.2f}；已回报净值 {realized-fees:.2f}；"
         f"估算净值 {realized-fees-estimated_fees:.2f}；佣金未回报 {missing_fees} 笔，按估算 ${estimated_fees:.2f} 计入"
         if missing_fees else f"- realized P&L: {realized:.2f}   fees: {fees:.2f}   net: {realized-fees:.2f}（最终佣金口径）"),
        f"- entries submitted: {risk['entries_submitted'] if risk else 0} / {cfg.max_daily_entries}",
        f"- daily stop latched: {bool(risk and risk['daily_stop_latched'])}",
        f"- open positions: " + (", ".join(
            f"{r['symbol']} {r['qty_open']}@{r['entry_price']:.2f} stop {r['breakeven_stop'] or r['stop']:.2f}"
            for r in store.open_positions()) or "none"),
        "- 券商端保护: " + _protection_health(store, day),
        f"- kill switch: {store.kill_state() or 'off'}",
        f"- open anomalies: {list(store.anomalies()) or 'none'}",
        f"- frozen symbols (operator must flatten, then `release`): {', '.join(frozen) or 'none'}",
        "- released positions (P&L of the operator's TWS trades is NOT in the figures above): " + (
            "; ".join(f"{r['symbol']} {r['ledger_qty']} sh at {r['at'][11:19]}Z by {r['operator']} ({r['reason']})"
                      for r in releases) or "none"),
        "",
        "## Test TP timeout / 测试止盈超时",
    ]
    lines += _test_tp_timeout_lines(store, start, end)
    lines += ["", "## Opening TP deferrals / 开盘止盈延后"]
    lines += _tp_open_delay_lines(store, start, end)
    lines += ["", "## Commission reconciliation / 佣金核对"]
    closed = [t for t in store.closed_trades() if t["closed_at"].astimezone(ET).date()==day]
    lines += ["- "+t["symbol"]+" positionId="+t["position_id"]+"；"+
              closed_trade_detail(t,fee_breakdown(t,store,cfg)) for t in closed[-12:]] or ["- 当日无已关闭持仓"]
    if len(closed)>12:
        lines.append(f"- 另有 {len(closed)-12} 笔关闭持仓；完整记录见账本。")
    lines += ["", "## Fills"]
    lines += [f"- {f['time'][11:19]}Z {f['symbol']} {f['side']} {f['quantity']} @ {f['price']:.2f}"
              f" realized {f['realized_pnl']:.2f}" for f in fills] or ["- none"]
    unverified = store.conn.execute(
        "SELECT f.*,c.value AS marker,p.con_id FROM fills f "
        "JOIN controls c ON c.key='execution_time_unverified:'||f.exec_id "
        "LEFT JOIN protections p ON p.position_id=f.position_id "
        "WHERE f.trading_day=? OR (f.side='BUY' AND f.position_id IN "
        "(SELECT position_id FROM positions WHERE status='OPEN')) ORDER BY f.time LIMIT 200", (key,)).fetchall()
    if fill_count>len(fills):
        lines += [f"- 另有 {fill_count-len(fills)} 条成交，完整记录见附件及状态库 fills。"]
    lines += ["", "## Execution time verification / 成交时间核实"]
    if not unverified:
        lines += ["- 当日无未核实成交时间"]
    today_basis = {}
    for event in store.conn.execute(
            "SELECT payload FROM audit WHERE kind='OVERNIGHT_CURRENT_DAY_TIME_BASIS' AND ts>=? AND ts<? ORDER BY id",
            (start.isoformat(), end.isoformat())):
        try:
            fact = json.loads(event["payload"])
            if fact.get("day") == key:
                for exec_id, snapshot in fact.get("snapshots", {}).items():
                    today_basis[exec_id] = snapshot
        except (ValueError, TypeError, AttributeError):
            continue
    for fill in unverified:
        note = "成交时间未核实；收到回报时间 " + fill["time"]
        current_snapshot = [fill["time"], fill["trading_day"], fill["intent_id"], fill["symbol"], fill["side"],
                            fill["quantity"], fill["price"], fill["broker_order_id"], fill["perm_id"]]
        if today_basis.get(fill["exec_id"]) == current_snapshot:
            note = "当日成交时间按当日成交查询判定为今天（精确时间未核实）"
        if fill["trading_day"] < key:
            try:
                note = historical_receipt_before(fill, fill["marker"], fill["con_id"], start,
                    pinned_con_id=store.control(f"conid:{fill['symbol']}"))
            except (ValueError, TypeError, KeyError, OverflowError):
                pass
        lines.append(f"- {fill['symbol']} execId={fill['exec_id']}：{note}")
    # Attachments are produced by write_daily_report, not this pure renderer.
    if attachment:
        metadata=_attachment_metadata(attachment)
    else:
        metadata=[_RENDER_ONLY_NOTE]
    lines=lines[:4]+metadata+lines[4:]
    lines=_bounded_prefix(lines,cfg.account)
    lines += ["", "## Events"]
    footer=["", "## Operator notes", "", "- (market context, manual actions, things to review)"]
    reserved=len(("\n".join(lines+footer)+"\n").encode('utf-8'))
    lines+=_events_section(store,start,end,byte_budget=min(64*1024,max(0,100*1024-reserved-1024)),account=cfg.account)
    lines+=footer
    # Final UTF-8 guard is after output-only account masking, including preamble.
    rendered=redact_accounts("\n".join(lines)+"\n",cfg.account)
    return _clip_utf8(rendered,100*1024)


def daily_health_report(store: Store, cfg: LiteConfig, day: date, *,
                        status: dict, gateway: dict, alerts: dict, quotes: dict) -> str:
    """One-screen remote summary; `daily_report` remains the complete local file.

    Scan results and dry-run gate decisions come from the day's durable audit
    and controls. A formal run does not persist every rejected gate decision;
    those details remain in the full operational log.
    """
    start = datetime.combine(day, time(0, 0), tzinfo=ET).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=ET).astimezone(timezone.utc)
    events = store.audit_for_day(start.isoformat(), end.isoformat(), ("SCAN", "DRY_RUN"))
    dry_decisions = []
    scan_signals = []
    for event in events:
        try:
            payload = json.loads(event["payload"])
        except (TypeError, ValueError):
            continue
        if event["kind"] == "SCAN":
            slot = datetime.fromisoformat(event["ts"]).astimezone(ET).strftime("%H:%M ET")
            found = ", ".join(f"{symbol}({score})" for symbol, score in payload.get("signals", ()))
            scan_signals.append(f"{slot} {found or 'none'}")
            continue
        message = payload.get("message", "")
        if message.startswith("WOULD_BUY"):
            parts = message.split()
            symbol = parts[1] if len(parts) > 1 else "?"
            gate = message.partition(" gate=")[2] or "unknown"
            dry_decisions.append(f"WOULD_BUY {symbol} gate={gate}")

    scans = []
    for slot in cfg.scan_times_et:
        persisted = store.control(f"scan:{day.isoformat()}:{slot}") or "not run"
        # Exception text can contain semicolons, newlines or secrets. Keep it
        # in durable evidence/logs, never in the remote phone summary.
        visible = "扫描失败（详见日志）" if persisted.startswith("error:") else persisted
        scans.append(f"{slot} ET {visible}")
    positions = status.get("open_positions") or []
    anomalies = list(store.anomalies())
    problems = []
    if not status.get("broker_connected"):
        problems.append("Gateway disconnected")
    if not status.get("reconcile_ok") or status.get("entry_blocks") and any(
            "mismatch" in str(block) for block in status["entry_blocks"]):
        problems.append("position drift")
    if not status.get("alerts_healthy"):
        problems.append("alerts unhealthy")
    if gateway.get("not_responding"):
        problems.append("Gateway heartbeat missing")
    if gateway.get("clock_skew_suspected"):
        problems.append("Gateway clock skew")
    if anomalies:
        problems.append(f"{len(anomalies)} open anomaly")
    quote_total, quote_usable = quotes.get("total", 0), quotes.get("usable", 0)
    if not quotes.get("samples"):
        problems.append("no RTH quote samples")
    elif quote_usable < quote_total:
        problems.append(f"RTH quotes {quote_usable}/{quote_total}")
    result = "OK" if not problems else "ATTENTION: " + "; ".join(problems[:3])
    order_count = store.conn.execute(
        "SELECT COUNT(*) FROM intents WHERE sent_at>=? AND sent_at<?",
        (start.isoformat(), end.isoformat())).fetchone()[0]
    cancel_count = store.conn.execute(
        "SELECT COUNT(*) FROM intents WHERE cancel_requested_at>=? AND cancel_requested_at<?",
        (start.isoformat(), end.isoformat())).fetchone()[0]
    test_timeout_count = store.conn.execute(
        "SELECT COUNT(*) FROM audit WHERE kind='TEST_TP_TIMEOUT_RESULT' AND ts>=? AND ts<? "
        "AND json_valid(payload) AND json_extract(payload,'$.phase')='claimed'",
        (start.isoformat(),end.isoformat())).fetchone()[0]
    position_text = ", ".join(f"{p[0]} {p[1]}" for p in positions) or "none"
    decision_text = "; ".join(dry_decisions[:3]) if dry_decisions else (
        "gate=not persisted for formal orders (see full log)"
        if not status.get("dry_run") else "no WOULD_BUY; gate n/a")
    if len(dry_decisions) > 3:
        decision_text += f"; +{len(dry_decisions)-3} more"
    lines = [
        result,
        "Scans    " + "; ".join(scans),
        "Signals  " + ("; ".join(scan_signals[:3]) or "none") +
        (f"; +{len(scan_signals)-3} more" if len(scan_signals) > 3 else ""),
        "Decisions " + decision_text,
        f"Orders   {order_count} placed · {cancel_count} canceled · 测试止盈超时 "
        f"{test_timeout_count} 次（完整生命周期见本机日报/附件）",
        f"Positions {position_text} · drift {'none' if status.get('reconcile_ok') else 'ATTENTION'}"
        f" · broker protection {_protection_health(store, day)}",
        f"Gateway  this process today, through report time: replies {gateway.get('replies', 0)} "
        f"· max verified {gateway.get('max_latency_seconds', 0):g}s "
        f"· misses {gateway.get('misses', 0)}",
        f"Alerts   this process today, through report time: sent {alerts.get('sent', 0)} "
        f"· failed {alerts.get('failed', 0)}",
        "Quotes   " + _quote_availability_line(cfg, _quote_scope(store, day, quotes)),
        f"code_sha {status.get('code_sha') or 'unknown'}" + (" test=1" if cfg.test.enabled else ""),
    ]
    # Audit/control text (notably a scan exception) can contain line breaks.
    # Keep the remote summary's ten-record contract without changing the
    # complete local report or the stored evidence.
    return "\n".join(" ".join(line.splitlines()) for line in lines)


def daily_health_card(remote_text: str, day: date, *, test_mode: bool = False,
                      mode: str = "paper", dry_run: bool = False) -> AlertCard:
    """Present daily metrics; legacy defaults mean Paper with orders enabled.

    The engine always supplies its actual mode and dry_run explicitly.
    """
    lines = remote_text.splitlines()
    if len(lines) != 10:
        raise ValueError("daily health summary shape changed")
    problem_map = {
        "Gateway disconnected": "IBKR 断线",
        "position drift": "持仓漂移",
        "alerts unhealthy": "告警通道异常",
        "Gateway heartbeat missing": "IBKR 心跳未应答",
        "Gateway clock skew": "IBKR 时钟偏差",
        "no RTH quote samples": "缺少常规交易时段行情样本",
    }
    label = execution_label(mode, dry_run=dry_run)
    if lines[0] == "OK":
        title, color = f"✅ 正常 · {label} · V6-Lite 每日健康报告 {day}", INFO_GREEN
    else:
        reasons = lines[0].partition("ATTENTION: ")[2].split("; ")
        translated = []
        for reason in reasons:
            if reason.startswith("RTH quotes "):
                translated.append("行情可用率不足 " + reason.removeprefix("RTH quotes "))
            elif reason.endswith(" open anomaly"):
                translated.append(reason.split()[0] + " 项未清异常")
            else:
                translated.append(problem_map.get(reason, "运行状态需核对"))
        title, color = f"⚠️ 需关注 · {label}：" + "、".join(translated), P1_ORANGE
    if test_mode or lines[9].endswith("test=1"):
        title = "TEST · " + title
    plan = lines[1].removeprefix("Scans    ")
    plan = re.sub(r"signals=(\d+) entered=([^; ]+)",
                  lambda match: f"信号 {match[1]} · 已入场 {match[2] if match[2] != '-' else '无'}", plan)
    plan = plan.replace("late_skipped", "超时跳过").replace("not run", "未运行").replace(
        "started", "进行中").replace("blocked:", "入场已阻止：")
    signals = lines[2].removeprefix("Signals  ").replace("none", "无")
    decisions = lines[3].removeprefix("Decisions ").replace(
        "gate=not persisted for formal orders (see full log)",
        "gate=未持久化（正式订单的具体门控结果请查本机完整日志）").replace(
        "no WOULD_BUY; gate n/a", "无 WOULD_BUY；gate=不适用")
    decisions = re.sub(r"; \+(\d+) more", lambda match: f"；另有 {match[1]} 项", decisions)
    scan = "计划：" + plan + "\n信号：" + signals + "\n结果：" + decisions
    orders = lines[4].removeprefix("Orders   ").replace("placed", "次下单").replace("canceled", "次撤单")
    positions_raw, _, protection = lines[5].partition(" · broker protection ")
    positions = positions_raw.removeprefix("Positions ").replace("none", "无").replace("drift 无", "漂移：无").replace(
        "drift ATTENTION", "漂移：需关注")
    gateway = lines[6].removeprefix("Gateway  ").replace(
        "this process today, through report time", "本进程今日截至报告时").replace(
        "replies", "应答").replace("max verified", "最长已验证延迟").replace("misses", "未应答")
    alerts = lines[7].removeprefix("Alerts   ").replace(
        "this process today, through report time", "本进程今日截至报告时").replace(
        "sent", "发出").replace("failed", "失败")
    gateway = re.sub(r"(\d+(?:\.\d+)?)s\b", r"\1 秒", gateway)
    quotes = lines[8].removeprefix("Quotes   ").replace("RTH availability", "常规交易时段可用率").replace(
        "subscribed", "已订阅").replace("symbol-samples", "标的样本").replace("samples", "次采样").replace(
        "this process today", "本进程今日").replace("DELAYED", "延迟行情").replace("LIVE", "实时行情")
    quotes = quotes.replace("; ", "；").replace("n/a", "暂无数据")
    return AlertCard(title, color,
                     (("扫描", scan), ("下单/撤单", orders), ("持仓与漂移", positions),
                      ("券商端保护", protection or "待核实"),
                      ("IBKR 心跳", gateway), ("告警送达", alerts), ("行情可用率", quotes),
                      ("code_sha", lines[9].removeprefix("code_sha "))))


def _market_data_line(store: Store, cfg: LiteConfig, day_key: str, *,
                      quotes: dict | None = None) -> str:
    """RTH statistics from this process; a CLI report cannot invent samples."""
    snapshot = _instant_market_data_line(store, cfg, day_key)
    parts = snapshot.split(" | ", 1)
    prefix = parts[0]
    if quotes is None:
        # Preserve subscription/refusal diagnostics for standalone reports,
        # but never present a stale final quote as the day's availability.
        if len(parts) > 1:
            details = [item for item in parts[1].split("; ")
                       if not item.startswith("requested real-time; no usable quotes")
                       and not item.startswith("requested real-time")
                       and not item.startswith("no usable quotes")]
            if details:
                prefix += " | " + "; ".join(details)
        return prefix + " | n/a RTH availability (no runtime RTH samples supplied)"
    return prefix + " | " + _quote_availability_line(cfg, _quote_scope(store, date.fromisoformat(day_key), quotes))


def _instant_market_data_line(store: Store, cfg: LiteConfig, day_key: str) -> str:
    """Configured type plus what the engine last observed (health snapshot)."""
    configured = "real-time (1)" if cfg.market_data_type == 1 else \
        "DELAYED (3) -- rehearsal only, prices ~15 min old, not evidence for quotes"
    try:
        health = json.loads(store.control("health") or "{}")
    except ValueError:
        health = {}
    # what was observed is only known for the day of the latest health snapshot
    try:
        seen_day = datetime.fromisoformat(health["at"]).astimezone(ET).date().isoformat()
    except (KeyError, TypeError, ValueError):
        seen_day = None
    md = (health.get("market_data") or {}) if seen_day == day_key else {}
    problems = []
    if md.get("no_subscription"):
        problems.append("no API subscription: " + ", ".join(md["no_subscription"]))
    if md.get("delayed_symbols"):
        problems.append("delayed ticks: " + ", ".join(md["delayed_symbols"]))
    if (cfg.market_data_type == 1 and md.get("quote_symbols")
            and md.get("usable_quotes") == 0):
        total = md["quote_symbols"]
        problems.append(f"requested real-time; no usable quotes {total}/{total}")
    return configured + (" | " + "; ".join(problems) if problems else "")

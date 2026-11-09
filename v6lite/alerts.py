"""Multi-channel alerting: log (always) + email + Discord webhook.

Closes the long-open V6 gap "alerts are Discord-only". Email uses the
standard library (no new dependency). Secrets come from environment variables
named in the config, never from the config file itself.

`healthy` is False when no remote channel has delivered recently; in live mode
the engine blocks NEW entries while alerting is unhealthy (exits continue).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import smtplib
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, replace
from email.message import EmailMessage
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from v6lite.config import AlertConfig

log = logging.getLogger("v6lite.alerts")
ET = ZoneInfo("America/New_York")
INFO_GREEN = 0x2ECC71
P1_ORANGE = 0xF39C12
P0_RED = 0xE74C3C
P2_YELLOW = 0xF1C40F
_DETAIL_MARKER = "详见日志"


def _units(text: str) -> int:
    """Conservative Discord length: astral characters count as UTF-16 pairs."""
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def _clip(text: str, limit: int) -> str:
    text = str(text)
    if _units(text) <= limit:
        return text or "—"
    if limit <= _units(_DETAIL_MARKER):
        return _DETAIL_MARKER[:limit]
    budget = limit - _units(_DETAIL_MARKER)
    prefix = []
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if width > budget:
            break
        prefix.append(char)
        budget -= width
    return "".join(prefix) + _DETAIL_MARKER


@dataclass(frozen=True)
class AlertCard:
    """A phone-sized presentation; original alert text remains in the log."""

    title: str
    color: int
    fields: tuple[tuple[str, str], ...]
    description: str = ""

    def as_embed(self) -> dict:
        raw_fields = [(str(name or "—"), str(value or "—")) for name, value in self.fields[:25]]
        if len(self.fields) > 25 and raw_fields:
            name, value = raw_fields[-1]
            raw_fields[-1] = (name, value + "\n" + _DETAIL_MARKER)
        title = _clip(self.title, 256)
        # Reserve a name and the full truncation marker for each field.
        minimum = len(raw_fields) * (1 + _units(_DETAIL_MARKER))
        description = _clip(self.description, min(4096, max(1, 6000 - _units(title) - minimum))) \
            if self.description else ""
        remaining = 6000 - _units(title) - _units(description)
        fields = []
        for index, (raw_name, raw_value) in enumerate(raw_fields):
            reserve = (len(raw_fields) - index - 1) * (1 + _units(_DETAIL_MARKER))
            name = _clip(raw_name, min(256, max(1, remaining - reserve - _units(_DETAIL_MARKER))))
            remaining -= _units(name)
            value = _clip(raw_value, min(1024, max(_units(_DETAIL_MARKER), remaining - reserve)))
            remaining -= _units(value)
            fields.append({"name": name, "value": value, "inline": False})
        embed = {"title": title, "color": self.color, "fields": fields}
        if description:
            embed["description"] = description
        return embed

    def as_plain_text(self) -> str:
        embed = self.as_embed()
        lines = [embed["title"]]
        if embed.get("description"):
            lines.append(embed["description"])
        lines.extend(f"{field['name']}：{field['value']}" for field in embed["fields"])
        return "\n".join(lines)


def _page_token(value: str) -> str:
    """Copy only bounded identifiers, never arbitrary broker/OS exception text."""
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", str(value).strip())
    token = match.group() if match else ""
    return token if not re.search(r"webhook|token|https?", token, re.I) else ""


def _page_ids(text: str) -> list[str]:
    ids = []
    for raw in re.findall(r"\b(?:v6l|combo)-[A-Za-z0-9_.:-]+", text, re.I):
        iid = _page_token(raw.rstrip(".,;:))"))
        if iid and iid not in ids:
            ids.append(iid)
    return ids


def _page_list(items: list[str], *, maximum: int = 5) -> str:
    items = list(dict.fromkeys(item for item in items if item))
    shown = "、".join(items[:maximum]) or "无可列出的编号"
    return shown + (f"；另有 {len(items) - maximum} 个" if len(items) > maximum else "")


def _page_number(pattern: str, body: str, group: int = 1) -> str:
    match = re.search(pattern, body, re.I)
    return match.group(group) if match else ""


def _page_symbol(subject: str) -> str:
    excluded = {"IBKR", "TWS", "STOP", "SELL", "BUY", "API", "P0", "P1", "V6"}
    return next((s for s in re.findall(r"\b[A-Z]{1,5}\b", subject) if s not in excluded), "")


def _page_freeze_orders(body: str) -> list[str]:
    """Only the explicitly listed NOT-cancelled orders, in broker-list order."""
    marker = re.search(r"these orders were NOT cancelled and can still trade [A-Z]{1,5}:\s*", body)
    if marker is None:
        return []
    section = body[marker.end():].split(". Confirm that no order", 1)[0]
    orders = []
    for item in section.split("; "):
        match = re.match(r"\s*([A-Za-z0-9_.:-]+)\s+(SELL|BUY|\?)\s+(-?\d+(?:\.\d+)?)", item)
        if match:
            iid = _page_token(match.group(1))
            if iid:
                side = {"SELL": "卖", "BUY": "买", "?": "方向未知"}[match.group(2)]
                orders.append(f"{iid} {side} {match.group(3)} 股")
        else:
            match = re.match(r"\s*([A-Za-z0-9_.:-]+)\s+\(held", item)
            if match and (iid := _page_token(match.group(1))):
                orders.append(f"{iid} 状态待核实")
    return orders


def _page_freeze_own(body: str) -> list[str]:
    marker = "cancel requests were sent for V6-Lite's own working orders: "
    if marker not in body:
        return []
    section = body.split(marker, 1)[1].split(" | ", 1)[0].split(". Confirm", 1)[0]
    return _page_ids(section)


def _bounded_page(level: str, title: str, what: str, impact: str, action: str) -> AlertCard:
    """Keep critical facts/actions ahead of explanations within phone limits.

    All known families construct short numbered steps. The final bounds are a
    guard for unexpectedly long identifiers; only then is a local-log marker
    added. UTF-16 is used because Discord counts astral characters twice.
    """
    prefix = "🔴" if level == "P0" else "🟠"
    title = _clip(f"{prefix} {level} · {title}", 100)
    what, impact, action = _clip(what, 600), _clip(impact, 600), _clip(action, 600)
    names = ("发生了什么", "影响", "你要做什么")
    total = _units(title) + sum(_units(name) + _units(value) for name, value in
                                zip(names, (what, impact, action)))
    if total > 1500:
        impact = _clip(impact, max(1, 600 - (total - 1500)))
        total = _units(title) + sum(_units(name) + _units(value) for name, value in
                                    zip(names, (what, impact, action)))
    if total > 1500:
        what = _clip(what, max(1, 600 - (total - 1500)))
    return AlertCard(title, P0_RED if level == "P0" else P1_ORANGE,
                     tuple(zip(names, (what, impact, action))))


def _release_reason(body: str) -> str:
    """Translate only our known release gates; never copy broker exception text."""
    gates = (
        ("completed proof local binding changed", "两次快照期间本地持仓、保护单绑定或成交记录改变，证明已失效。"),
        ("dry-run state", "dry-run 状态不能释放。"),
        ("no managed open position", "没有本系统的未平持仓，也没有待解除的冻结。"),
        ("orders are still working", "仍有在途订单，尚未完成撤单核验。"),
        ("managed protection record missing", "缺少持仓对应的保护单记录。"),
        ("旧类型保护单", "保护单仍为旧类型 STP LMT，需先人工核对。"),
        ("identity is held or missing", "保护单身份记录缺失，或仍被人工 hold。"),
        ("cancel/identity unverified", "保护单的身份或 Cancelled/ApiCancelled 撤单证据未被核实。"),
        ("local identity unverified", "保护单本地 account/clientId/conId/orderId/permId 绑定未通过核验。"),
        ("cancel unverified", "保护单缺少已核实的 Cancelled/ApiCancelled 撤单证据。"),
        ("account execution evidence unavailable", "缺少本账户跨客户端的完整成交查询结果。"),
        ("lacks complete execution evidence", "缺少完整的账户成交证据，或自有成交与账户回报不一致。"),
        ("reappeared during release", "两次快照之间再次出现工作单。"),
        ("execution refresh incomplete", "自有成交刷新或正常入账尚未完成。"),
        ("broker position changed", "两次快照之间券商持仓发生变化。"),
        ("execution evidence changed", "两次快照之间出现新成交或成交签名改变。"),
        ("still in flight", "账本仍有未终结的自有订单。"),
        ("broker still holds", "新鲜券商快照仍显示非零持仓。"),
        ("position stream still shows", "持仓推送仍显示非零持仓。"),
        ("after the last fill", "最后一次成交后尚未满足静默期。"),
        ("broker evidence unavailable", "新鲜券商查询未完成，缺少完整快照。"),
    )
    for marker, explanation in gates:
        if marker in body:
            oid = _page_number(r"protected GTC (\d+) ", body)
            return (f"保护单 {oid}：" if oid else "") + explanation
    return "释放核验未完成，具体查询错误见本机 RELEASE_REFUSED 日志。"


def _page_card(level: str, subject: str, body: str) -> AlertCard:
    """Render each P0/P1 event family with its own title and actionable facts.

    The original subject/body remain untouched for local English logs and
    deduplication. No untrusted exception message is copied to the phone.
    """
    key = subject.lower()
    symbol = _page_symbol(subject)
    ids = _page_ids(body)
    if key.startswith("broker data anomaly "):
        code = _page_token(subject[len("broker data anomaly "):]) or "代码待核实"
        title, what = "券商数据事件异常", f"券商数据异常代码 {code}。"
        # Some broker event details include a symbol, but others do not.
        found = re.match(r"([A-Z]{1,5})\b", body)
        if found:
            what += f" 标的 {found.group(1)}。"
        impact = "券商证据需要人工核实；新入场暂停。"
        action = (f"1）在 TWS 核对相关成交/订单；2）本机运行 status；3）核实后运行 clear-anomaly {code}。"
                  if code != "代码待核实" else
                  "1）在 TWS 核对相关成交/订单；2）本机运行 status 查异常代码；3）核实后运行 clear-anomaly <代码>。")
    elif key.startswith("fill anomaly "):
        code = _page_token(subject[len("fill anomaly "):]) or "代码待核实"
        title, what = "成交入账异常", f"成交异常代码 {code}。"
        impact = "新入场暂停，账本成交需与券商核对。"
        action = (f"1）在 TWS 核对成交和持仓；2）本机运行 status；3）核实后运行 clear-anomaly {code}。"
                  if code != "代码待核实" else
                  "1）在 TWS 核对成交和持仓；2）本机运行 status 查异常代码；3）核实后运行 clear-anomaly <代码>。")
    elif key.startswith("late exit fill "):
        stage = _page_token(body.split(" ", 1)[0]) or "退出"
        iid = ids[0] if ids else "编号见本机日志"
        qty = _page_number(r"\bfilled\s+(-?\d+(?:\.\d+)?)", body)
        pulled = _page_ids(body.split("Retries pulled:", 1)[1]) if "Retries pulled:" in body else []
        pulled = [item for item in pulled if item != iid]
        visible_pulled = pulled[:4] if ids else pulled[:5]
        title = "退出成交迟到"
        what = f"{symbol} {stage} 订单 {iid} 已结算后又成交 {qty or '未知'} 股。"
        if pulled:
            what += f" 已请求撤销重试单：{_page_list(visible_pulled)}"
            if len(pulled) > len(visible_pulled):
                what += f"；另有 {len(pulled) - len(visible_pulled)} 个"
            what += "。"
        impact = "可能超卖或形成空头；新入场暂停。"
        action = "1）在 TWS 核对券商持仓及所有在途 SELL；2）确认/撤销冲突订单；3）若已形成空头，人工处理并核对本机 status。"
    elif key == "ibkr disconnected":
        title, what = "IBKR 连接断开", "IBKR API 连接已断开。"
        impact = "券商状态无法验证；新入场暂停，退出需待恢复及对账。"
        action = "1）检查 IBKR 登录和 API 连接；2）在本机运行 status；3）确认重连和对账结果。"
    elif key == "live approval no longer valid":
        title, what = "实盘审批失效", "实盘运行审批无效或过期。"
        impact = "新入场暂停；退出继续。"
        action = "1）本机运行 status 核实原因；2）完成审批复核；3）在本机重新运行 approve-live。"
    elif key == "ib gateway not responding":
        misses = _page_number(r"(\d+)\s+consecutive", body)
        seconds = _page_number(r"exceeded\s+(\d+)s", body)
        title, what = "IBKR 心跳无应答", f"IBKR 当前时间心跳连续 {misses or '多'} 次超过 {seconds or '规定'} 秒。"
        impact = "IBKR 响应无法证实；行情与账户数据可能失联。"
        action = "1）检查 IBKR 登录及 API 响应；2）本机运行 status；3）等待新的有效心跳解除告警。"
    elif key == "gateway 时钟偏差":
        offset = _page_number(r"server_minus_host_seconds=([+-]?\d+(?:\.\d+)?)", body)
        streak = _page_number(r"(\d+)\s+consecutive", body)
        title, what = "IBKR 时钟偏差", f"连续 {streak or '多'} 次异常 currentTime 回报；服务器时间减本机接收时间为 {offset or '未知'} 秒。"
        impact = "回报时间异常；此项告警不代表 IBKR 无响应，也不能据此认定本机时钟错误。"
        action = "1）在同一 TWS 核对连接和时间回报；2）查看本机日志中的逐次偏差秒数；3）等待连续正常回报自动解除，不要另开 Paper 登录。"
    elif "旧 v6" in key or key.startswith("old v6"):
        pid_section = re.search(r"PID\s+([\d,\s]+)", body)
        pids = re.findall(r"\d+", pid_section.group(1)) if pid_section else []
        pid_text = _page_list(pids) if pids else "未知"
        title, what = "旧 V6 隔离状态异常", f"旧 V6 进程运行或无法核实（PID {pid_text}）。"
        impact = "账户隔离无法确认；正式运行拒绝启动，dry-run 不作为放行依据。"
        action = (f"1）确认 PID {pid_text} 是什么进程；2）关闭旧 V6 或该进程后重试；3）本机运行 status 核对。"
                  if pids else "1）在本机确认无法读取的 Python 进程 PID；2）关闭或查明该进程后重试；3）运行 status 核对。")
    elif key == "unresolved combo order working":
        head = body.split(": every", 1)[0]
        combos = [_page_token(raw) for raw in head.split(", ")]
        combos = [item for item in combos if item]
        title, what = "组合挂单阻断退出", f"组合挂单仍在途：{_page_list(combos)}。"
        impact = "所有 Lite 卖出、止损、release 与新入场受阻。"
        action = f"1）在 TWS 核对并撤销/确认组合单 {_page_list(combos)}；2）确认无冲突挂单；3）本机运行 status 核对。"
    elif key.startswith("held order is working "):
        iid = ids[0] if ids else "编号见本机日志"
        stage = _page_number(r"\(([A-Z][A-Z0-9]*)\) was held", body)
        left = _page_number(r"working with\s+(-?\d+(?:\.\d+)?)\s+left", body)
        title, what = "保留订单仍在途", f"{symbol} {stage or '退出'} 保留订单 {iid} 仍在途，剩余 {left or '未知'} 股。"
        impact = "这些股数已预留；重复卖出可能超卖。"
        action = f"1）在 TWS 核对订单 {iid} 及成交；2）先确认/撤销在途单，再人工处理持仓；3）本机运行 status。"
    elif key.startswith("orphan v6-lite order "):
        iid = ids[0] if ids else "编号见本机日志"
        left = _page_number(r"remaining\s+(-?\d+(?:\.\d+)?)", body)
        code = f"orphan_order:{iid}" if ids else "代码需从本机 status 核实"
        title, what = "账本外 Lite 订单在途", f"{symbol} 订单 {iid} 不在本账本，剩余 {left or '未知'} 股；异常代码 {code}。"
        impact = "订单可能继续成交；新入场暂停。"
        action = (f"1）在 TWS 核对并撤销订单 {iid}，或恢复正确状态目录；2）确认不再在途；3）本机运行 clear-anomaly {code}。"
                  if ids else
                  "1）在 TWS 核对账本外订单，或恢复正确状态目录；2）本机运行 status 查异常代码；"
                  "3）确认订单不再在途后运行 clear-anomaly <代码>。")
    elif key == "recovery failed":
        title, what = "恢复对账失败", "重启/重连后，挂单与成交证据查询失败。"
        impact = "无法安全确认订单状态；新入场暂停。"
        action = "1）在 TWS 核对在途订单、成交和持仓；2）检查 IBKR 连接；3）本机运行 status 并复核恢复结果。"
    elif key.startswith("unverifiable order from a prior session "):
        iid = _page_token(subject.rsplit(" ", 1)[-1]) or "编号见本机日志"
        match = re.match(r"([A-Z]{1,5})\s+([A-Z][A-Z0-9]*)\s+(\d+):\s+filled\s+(\d+)", body)
        facts = (f"{match.group(1)} {match.group(2)} 订单 {iid} 原定 {match.group(3)} 股，"
                 f"今日证据已成交 {match.group(4)} 股" if match else f"跨交易日订单 {iid}")
        title, what = "跨交易日订单无法核实", facts + "，历史成交仍需核对。"
        impact = "订单保持人工保留；新入场暂停。"
        action = (f"1）在 TWS 查订单 {iid} 的跨日成交；2）核实后本机运行 resolve-intent {iid if iid != '编号见本机日志' else '<意图ID>'}；"
                  "3）若持仓仍有差异，先人工平仓，再按条件运行 release。")
    elif key.startswith("order state unknown "):
        tail = _page_token(subject[len("order state unknown "):])
        is_symbol = bool(re.fullmatch(r"[A-Z]{1,5}", tail))
        iid = ids[0] if ids else (tail if not is_symbol else "")
        qty = _page_number(r"\b(?:accepted|for)\s+(\d+)\s+shares", body)
        title = "提交回执不明" if is_symbol else "订单状态不明"
        what = (f"{tail} 下单回执不明" if is_symbol else "订单状态无法证实")
        if iid:
            what += f"：{iid}"
        if qty:
            what += f"，可能涉及 {qty} 股"
        what += "。"
        impact = "可能已在券商成交；新入场暂停，不能凭超时重发。"
        action = (f"1）在 TWS 核对订单 {iid or tail} 及成交；2）本机运行 status 查关联意图 ID；"
                  f"3）证据核实后运行 resolve-intent {iid or '<意图ID>'}。")
    elif key == "broker/runtime mismatch" or "drift" in key:
        diffs = []
        for match in re.finditer(r"\b([A-Z]{1,5}):\s+(?:expected|ledger)\s+(-?\d+(?:\.\d+)?),?\s+broker\s+(-?\d+(?:\.\d+)?)", body, re.I):
            diffs.append(f"{match.group(1)} 账本 {match.group(2)} 股 / 券商 {match.group(3)} 股")
        for match in re.finditer(r"\b([A-Z]{1,5}):\s+unmanaged broker holding\s+(-?\d+(?:\.\d+)?)", body):
            diffs.append(f"{match.group(1)} 券商账外持仓 {match.group(2)} 股")
        title, what = "持仓账本与券商不符", f"对账差异：{_page_list(diffs)}。" if diffs else "券商持仓与账本不符，具体数字需查本机 status。"
        impact = "受影响标的新入场暂停；差异持续会冻结持仓。"
        action = "1）本机运行 status，对照 TWS 实际持仓；2）账外持仓核实后用 ignore；3）托管持仓差异持续时先平仓，再按条件 release。"
        if _units(what) > 600:
            # A reconciliation may contain five independent, very large
            # quantities. Treat each symbol+ledger+broker tuple as an atomic
            # fact and distribute it across the three fixed card fields.
            entries = [re.sub(r" 账本 (-?\d+(?:\.\d+)?) 股 / 券商 (-?\d+(?:\.\d+)?) 股$",
                              r" 账\1/券\2", item) for item in diffs[:5]]
            entries = [re.sub(r" 券商账外持仓 (-?\d+(?:\.\d+)?) 股$", r" 账外\1", item)
                       for item in entries]

            def take(heading: str, remaining: list[str], limit: int) -> tuple[str, list[str]]:
                selected = []
                for item in remaining:
                    candidate = heading + "、".join(selected + [item]) + "。"
                    if _units(candidate) > limit:
                        break
                    selected.append(item)
                return heading + "、".join(selected) + "。", remaining[len(selected):]

            what, remaining = take("对账差异：", entries, 560)
            impact, remaining = take("新入场暂停；其余差异：", remaining, 560)
            if impact == "新入场暂停；其余差异：。":
                impact = "新入场暂停；差异持续会冻结持仓。"
            action = "1）本机 status；2）在 TWS 核对股数；3）账外核实后 ignore，托管差异持续先平仓再 release。"
            page_title = f"{'🔴' if level == 'P0' else '🟠'} {level} · {title}"
            names = ("发生了什么", "影响", "你要做什么")
            action_limit = min(600, 1500 - _units(page_title) - sum(map(_units, names))
                               - _units(what) - _units(impact))
            if remaining:
                suffix, remaining = take("；待复核：", remaining, action_limit - _units(action))
                if suffix != "；待复核：。":
                    action += suffix
            omitted = max(0, len(diffs) - 5) + len(remaining)
            if omitted:
                note = f"另有 {omitted} 项完整数字见本机日志。"
                if _units(action + note) <= action_limit:
                    action += note
                elif (_units(impact + note) <= 600 and
                      _units(page_title) + sum(map(_units, names)) + _units(what) +
                      _units(impact + note) + _units(action) <= 1500):
                    impact += note
    elif key.endswith(" freeze check deferred"):
        title, what = "冻结核查暂缓", f"{symbol} 持仓数量差异持续，券商成交或持仓证据不完整。"
        impact = "该标的订单暂停，暂不能判定冻结或恢复。"
        action = f"1）在 TWS 核对 {symbol} 持仓与成交；2）恢复 IBKR 查询；3）本机运行 status，等待完整证据复核。"
    elif key.startswith("v6-lite no longer manages "):
        diff = re.search(r"\b(?:ledger|expected)\s+(-?\d+(?:\.\d+)?),?\s+broker\s+(-?\d+(?:\.\d+)?)", body, re.I)
        own = _page_freeze_own(body)
        orders = _page_freeze_orders(body)
        # Uncancelled orders are the immediate risk; spend the five visible ID
        # slots on them first, then on our own cancellation requests.
        visible_own = own[:max(0, 5 - min(5, len(orders)))]
        hidden_own = len(own) - len(visible_own)
        listing_unavailable = "working-order list could not be read" in body
        title = "V6-Lite 停止管理持仓"
        what = f"{symbol} 已冻结；V6-Lite 不再管理。"
        if diff:
            what += f"账本 {diff.group(1)} 股 / 券商 {diff.group(2)} 股。"
        if own:
            what += "自有单已请求撤销、结果未确认"
            if visible_own:
                what += f"：{_page_list(visible_own)}"
            if hidden_own:
                what += f"；另有 {hidden_own} 个"
            what += "。"
        if orders:
            what += f"另有未撤销且可能成交的订单：{_page_list(orders)}。"
        if listing_unavailable:
            what += "券商挂单列表未能读取。"
        impact = "自动止损不再发送；未撤销订单仍可能成交，卖单可能导致超卖。"
        split_orders = False
        if _units(what) > 600:
            # Each identifier stays paired with its full remaining quantity.
            # If five long pairs exceed one field, continue the list in the
            # impact field. Never let generic clipping cut a number or ID.
            compact_orders = [re.sub(r" (卖|买|方向未知) (-?\d+(?:\.\d+)?) 股$",
                                     r"/\1\2", item) for item in orders[:5]]
            diff_text = f"账{diff.group(1)}/券{diff.group(2)}；" if diff else ""
            heading = f"{symbol}冻结；{diff_text}未撤单：" if orders else f"{symbol}冻结；{diff_text}"
            first, remainder = [], []
            for entry in compact_orders:
                candidate = heading + "、".join(first + [entry]) + "。"
                (first if not remainder and _units(candidate) <= 600 else remainder).append(entry)
            what = heading + "、".join(first) + "。"
            impact = "止损停发；未撤单可能成交。"
            omitted = len(orders) - 5
            if remainder:
                impact += "其余未撤单："
                shown = 0
                for entry in remainder:
                    candidate = impact + ("、" if shown else "") + entry + "。"
                    if _units(candidate) <= 540:
                        impact = candidate[:-1]
                        shown += 1
                    else:
                        omitted += 1
                impact += "。"
            if omitted:
                impact += f"另有 {omitted} 个订单过长或未列出，完整编号见本机日志。"
            if visible_own:
                own_text = "自有单撤销待确认：" + _page_list(visible_own) + "。"
                if _units(impact + own_text) <= 600:
                    impact += own_text
                else:
                    hidden_own += len(visible_own)
            if hidden_own:
                impact += f"另有 {hidden_own} 个自有单撤销待确认。"
            if listing_unavailable:
                impact += "挂单列表不可读。"
            if _units(impact) > 600:
                # Last-resort diagnostic is whole-record omission, never a
                # clipped identifier or quantity.
                impact = f"止损停发；未撤单可能成交；其余编号无法装入卡片，请核对本机日志（至少另有 {omitted + hidden_own} 个）。"
            split_orders = True
        if orders:
            first = f"1）在 TWS 确认或撤销仍在途的 {symbol} 订单（这些订单未撤销）：{_page_list(orders)}；"
        else:
            first = f"1）在 TWS 核对 {symbol} 的所有在途订单，确认或撤销；"
        if own:
            first = first[:-1] + "；并确认自有单撤销结果；"
        action = (first + f"2）确认 {symbol} 没有任何在途订单之后，再人工保护或平仓；"
                  f"3）券商显示 0 股且无在途订单后，在本机运行 release {symbol}。")
        if split_orders or _units(action) > 600:
            # Long broker IDs appear in the facts above. Repeating them in
            # step 1 would clip the final release step, so refer back to the
            # complete bounded list while keeping the mandatory sequence.
            first = (f"1）在 TWS 逐一确认或撤销上述 {len(orders)} 个未撤销订单"
                     if orders else f"1）在 TWS 核对 {symbol} 的所有在途订单")
            if own:
                first += "，并确认自有单撤销结果"
            action = (first + f"；2）确认 {symbol} 没有任何在途订单之后，再人工保护或平仓；"
                      f"3）券商显示 0 股且无在途订单后，在本机运行 release {symbol}。")
    elif key.startswith("release refused "):
        held = _page_number(r"\b(?:has|held|broker)\s+(-?\d+(?:\.\d+)?)\s+shares", body)
        streamed = _page_number(r"position stream still shows\s+(-?\d+(?:\.\d+)?)", body)
        title, what = "人工释放被拒", f"{symbol} 的 release 被拒绝。具体原因：{_release_reason(body)}"
        if held:
            what += f"券商仍有 {held} 股。"
        if streamed:
            what += f"持仓推送仍显示 {streamed} 股，需核对是否滞后。"
        if ids:
            what += f"在途订单：{_page_list(ids)}。"
        impact = "冻结继续，V6-Lite 不会重新管理该持仓。"
        action = (f"1）在 TWS 核对 {symbol} 持仓及订单{(' '+_page_list(ids)) if ids else ''}；"
                  f"2）确认 0 股、无在途单且满足静默期；3）本机重新运行 release {symbol}。")
    elif key == "positions without fresh prices":
        raw = body.split(":", 1)[0]
        symbols = re.findall(r"\b[A-Z]{1,5}\b", raw)
        title, what = "持仓报价过期", f"持仓 {_page_list(symbols)} 没有新鲜价格，止损无法评估。"
        impact = "自动退出保护可能延迟。"
        action = f"1）检查 IBKR 行情权限；2）在 TWS 核对 {_page_list(symbols)} 的持仓保护；3）恢复报价后运行 status。"
    elif key == "daily loss stop latched":
        loss = _page_number(r"loss\s+(-?\d+(?:\.\d+)?)", body)
        limit = _page_number(r">=\s+(-?\d+(?:\.\d+)?)", body)
        title, what = "当日亏损门槛触发", f"当日亏损 {loss or '未知'}，达到门槛 {limit or '未知'}。"
        impact = "本交易日暂停新入场；退出继续，锁定到下一交易日。"
        action = "1）本机运行 status；2）核对 report 和 TWS 成交；3）下一交易日再评估。"
    elif key.endswith(" not filled"):
        stage = _page_token(subject.split()[1]) if len(subject.split()) > 1 else "退出"
        qty = _page_number(r"(-?\d+(?:\.\d+)?)\s+shares still open", body)
        stop = stage == "STOP"
        title = "止损重试耗尽" if stop else "止盈重试耗尽"
        what = f"{symbol} {stage} {'止损' if stop else '退出'}未完成，仍有 {qty or '未知'} 股未卖。"
        impact = "自动退出重试耗尽；未卖股数仍暴露于市场风险。"
        action = f"1）在 TWS 核对 {symbol} 持仓和在途卖单；2）确认无冲突订单后人工{'设止损或平仓' if stop else '处理剩余仓位'}；3）本机运行 status。"
    elif key.startswith("exit blocked "):
        stage = _page_token(subject.rsplit(" ", 1)[-1]) or "退出"
        stop = stage == "STOP"
        mismatch = re.search(r"fresh broker position\s+(-?\d+(?:\.\d+)?)\s+but ledger\s+(-?\d+(?:\.\d+)?)", body)
        wanted = re.search(r"(-?\d+(?:\.\d+)?)\s+to sell but only\s+(-?\d+(?:\.\d+)?)", body)
        title = "止损下单被拦截" if stop else "退出下单被拦截"
        what = f"{symbol} {stage} {'止损' if stop else '退出'}订单未发出。"
        if mismatch:
            what += f"券商 {mismatch.group(1)} 股 / 账本 {mismatch.group(2)} 股。"
        if wanted:
            what += f"需卖 {wanted.group(1)} 股，可卖 {wanted.group(2)} 股。"
        if ids:
            what += f"在途卖单：{_page_list(ids)}。"
        impact = "持仓缺少本次自动退出保护；证据不足时禁止猜测数量。"
        action = (f"1）在 TWS 核对 {symbol} 持仓和在途单{(' '+_page_list(ids)) if ids else ''}；"
                  f"2）确认冲突单后人工{'设止损或平仓' if stop else '处理持仓'}；3）本机运行 status。")
    elif " stop reduced to " in key:
        capacity = _page_number(r"reduced to\s+(-?\d+(?:\.\d+)?)", subject)
        wanted = _page_number(r"(-?\d+(?:\.\d+)?)\s+wanted", body)
        covered = _page_number(r"already cover\s+(-?\d+(?:\.\d+)?)", body)
        title, what = "止损数量缩减", f"{symbol} STOP 原需卖 {wanted or '未知'} 股，可卖 {capacity or '未知'} 股；在途卖单覆盖 {covered or '未知'} 股。"
        if ids:
            what += f"在途订单：{_page_list(ids)}。"
        impact = "止损单只能覆盖可卖股数；重复卖出可能超卖。"
        action = (f"1）在 TWS 核对 {symbol} 持仓及在途卖单{(' '+_page_list(ids)) if ids else ''}；"
                  "2）确认已预留股数的保护范围；3）必要时人工补足未保护部分。")
    elif key.startswith("exit refused "):
        stage = _page_token(subject.rsplit(" ", 1)[-1]) or "退出"
        stop = stage == "STOP"
        wanted = _page_number(r"sell\s+(-?\d+(?:\.\d+)?)", body)
        free = _page_number(r"free shares\s+(-?\d+(?:\.\d+)?)", body)
        title = "止损未发出" if stop else "退出订单未发出"
        what = f"{symbol} {stage} {'止损' if stop else '退出订单'}未发出：需卖 {wanted or '未知'} 股，可卖 {free or '未知'} 股。"
        if ids:
            what += f"在途卖单：{_page_list(ids)}。"
        impact = "持仓没有本次自动退出保护；贸然重卖可能超卖。"
        action = (f"1）在 TWS 核对 {symbol} 持仓与在途卖单{(' '+_page_list(ids)) if ids else ''}；"
                  f"2）确认可卖股数后，必要时人工{'设止损' if stop else '处理退出'}；3）本机运行 status。")
    elif key.startswith("exit rejected "):
        stage = _page_token(subject.rsplit(" ", 1)[-1]) or "退出"
        iid = ids[0] if ids else ""
        qty = _page_number(r"(-?\d+(?:\.\d+)?)\s+shares", body)
        price = _page_number(r"\bat\s+(-?\d+(?:\.\d+)?)", body)
        title, what = "券商拒绝退出订单", f"{symbol} {stage} 退出单被券商拒绝。"
        if iid:
            what += f"订单 {iid}。"
        if qty:
            what += f"数量 {qty} 股。"
        if price:
            what += f"价格 {price}。"
        impact = "该次退出没有生效；持仓可能缺少保护。"
        action = f"1）在 TWS 核对 {symbol} 拒单{(' '+iid) if iid else ''}及持仓；2）确认无冲突在途单；3）必要时人工设保护。"
    elif key == "shutdown: working orders unknown":
        title, what = "停机时挂单状态不可查", "停机时无法读取券商挂单列表。"
        impact = "不能证明 Lite 订单已撤销，停机后仍可能成交。"
        action = "1）在 TWS 查看全部 v6l- 在途订单；2）确认或撤销每一笔；3）核对成交与持仓后再考虑重启。"
    elif key == "shutdown with unresolved orders":
        ledger = _page_ids(body.split("broker working:", 1)[0])
        broker = _page_ids(body.split("broker working:", 1)[1]) if "broker working:" in body else []
        visible_broker = broker[:5]
        remaining_ledger = [item for item in ledger if item not in visible_broker]
        visible_ledger = remaining_ledger[:max(0, 5 - len(visible_broker))]
        hidden_ledger = len(remaining_ledger) - len(visible_ledger)
        title, what = "停机后仍有未结订单", f"券商仍在途：{_page_list(broker)}；账本未结：{_page_list(visible_ledger)}"
        if hidden_ledger:
            what += f"；另有 {hidden_ledger} 个"
        what += "。"
        impact = "订单可能在 Lite 停止后成交，数量会改变。"
        action = (f"1）在 TWS 核对/撤销在途单 {_page_list(visible_broker)}；"
                  f"2）核对账本未结单{(' '+_page_list(visible_ledger)) if visible_ledger else ''}与成交；"
                  "3）本机运行 status 查看其余编号。")
    elif key == "no real-time market data":
        missing = re.search(r"real-time API data for\s+([^;]+);", body)
        delayed = re.search(r"delayed/frozen ticks for\s+([^.;]+)", body)
        no_live = re.findall(r"\b[A-Z]{1,5}\b", missing.group(1)) if missing else []
        stale = re.findall(r"\b[A-Z]{1,5}\b", delayed.group(1)) if delayed else []
        if not no_live and not stale:
            fallback = re.match(r"([A-Z]{1,5}):", body)
            if fallback:
                no_live = [fallback.group(1)]
        title, what = "实时行情订阅异常", f"实时行情拒绝：{_page_list(no_live)}；仅延迟/冻结行情：{_page_list(stale)}。"
        impact = "受影响标的无法取得合格实时报价；入场暂停，按价退出可能延迟。"
        action = "1）检查 IBKR API 行情权限；2）核对实时订阅及 Paper 共享；3）重新登录并在本机运行 status。"
    elif key == "no delayed market data":
        missing = re.search(r"no subscription for\s+([^;]+);", body)
        unusable = re.search(r"unusable symbols\s+([^.;]+)", body)
        symbols = re.findall(r"\b[A-Z]{1,5}\b", missing.group(1)) if missing else []
        bad = re.findall(r"\b[A-Z]{1,5}\b", unusable.group(1)) if unusable else []
        if not symbols and not bad:
            fallback = re.match(r"([A-Z]{1,5}):", body)
            if fallback:
                symbols = [fallback.group(1)]
        ratio = _page_number(r"usable delayed quotes\s+(\d+/\d+)", body)
        title, what = "延迟行情订阅异常", f"延迟可用报价 {ratio or '未知'}；无订阅 {_page_list(symbols)}；不可用 {_page_list(bad)}。"
        impact = "无有效价格的标的不能入场；演练扫描/退出检查可能暂停。"
        action = "1）检查 IBKR 延迟行情 API 权限；2）核对不可用标的；3）恢复后本机运行 status。"
    else:
        # Unknown future pages still say what needs checking, but never echo
        # arbitrary subject/body (which may contain broker exception secrets).
        title, what = "未分类安全告警", (f"收到 {symbol} 未分类 P0/P1 运行异常；事件编号见本机日志。"
                                     if symbol else "收到未分类 P0/P1 运行异常；事件编号见本机日志。")
        impact = "状态无法核实，相关交易需人工复核。"
        action = "1）在本机运行 status 并查完整日志；2）涉及订单时在 TWS 核对在途单与成交。"
    return _bounded_page(level, title, what, impact, action)


def _notice_card(level: str, subject: str) -> AlertCard:
    """INFO/P2 also use embeds; operational detail remains in local logs."""
    key = subject.lower()
    if key == "alert self-test":
        title, event, action = "告警通道自检", "V6-Lite 正在验证远程告警送达。", "无需操作。"
    elif key == "ibkr reconnected":
        title, event, action = "IBKR 已重新连接", "券商连接恢复。", "在本机运行 status 核对持仓与对账状态。"
    elif key.endswith("position closed"):
        title, event, action = "持仓已关闭", f"{subject.split()[0]} 持仓已关闭。", "核对 TWS 成交与本机 report。"
    elif key.startswith("buy "):
        title, event, action = "买单已提交", f"已提交 {subject.split()[-1]} 买单。", "在 TWS 核对订单与成交。"
    elif "loss cooldown" in key:
        title, event, action = "亏损后冷却", "亏损后冷却保护生效。", "在本机运行 status 查看恢复条件。"
    else:
        symbol = next((token for token in re.findall(r"\b[A-Z]{1,5}\b", subject)
                       if token not in {"IBKR", "TWS", "STOP", "SELL", "BUY", "API", "V6"}), "")
        title = "V6-Lite 运行提醒" if level == "P2" else "V6-Lite 运行通知"
        event = "运行状态有新变化" + (f"（{symbol}）" if symbol else "") + "；详情见本机日志。"
        action = "在本机运行 status，按需查看完整日志。"
    prefix, color = ("🟡", P2_YELLOW) if level == "P2" else ("🟢", INFO_GREEN)
    return AlertCard(f"{prefix} {title}", color,
                     (("发生了什么", event), ("下一步", action)))


def _page_remote_body(subject: str, body: str) -> str:
    """Three phone-sized action lines. The original detail stays in the log."""
    key = subject.lower()
    what = " ".join(subject.split())[:100]
    if any(k in key for k in ("market data", "mismatch", "no longer manages", "exit blocked")):
        # These pages are often scoped to symbols only in the detailed body.
        # Keep its first clause, never the multiline diagnostics or exception.
        scope = " ".join(body.split(";", 1)[0].splitlines()[0].split())[:90] if body else ""
        if scope:
            what += f" — {scope}"
    if "daily loss stop latched" in key:
        impact = "New entries stop for this trading day; exits continue."
        action = "Review status and report; reassess at the next trading day."
    elif "positions without fresh prices" in key:
        impact = "Stop checks lack fresh prices, so exit protection may be delayed."
        action = "Check IBKR market data and restore fresh position quotes."
    elif "late exit fill" in key:
        impact = "A second SELL may oversell the holding or create a short position."
        action = "Check the TWS broker position and working SELL orders; cover any short manually."
    elif "market data" in key:
        impact = "Affected symbols lack usable quotes; entries and priced exits may pause."
        action = "Check IBKR market-data status and API subscription permissions."
    elif "gateway" in key or "ibkr disconnected" in key:
        impact = "Broker state cannot be verified; new entries pause."
        action = "Check IBKR login, API connection and the next heartbeat."
    elif "exit" in key or "stop" in key or "no longer manages" in key:
        impact = "An open position may lack automated exit protection."
        action = "Check the position and working orders in TWS; protect it manually if needed."
    elif "order" in key or "intent" in key:
        impact = "Order state is uncertain; new entries may pause."
        action = "Check working orders and fills in TWS, then resolve the intent."
    elif "mismatch" in key or "drift" in key or "position" in key:
        impact = "Broker and ledger holdings differ; affected entries pause."
        action = "Run status, compare holdings in TWS and review reconciliation logs."
    elif "approval" in key:
        impact = "New live entries pause until approval is valid."
        action = "Review status and renew approval with approve-live."
    else:
        impact = "Trading safety or monitoring requires operator review."
        action = "Run status, inspect the full log and check TWS if orders are involved."
    return f"What: {what}\nImpact: {impact}\nAction: {action}"


class Channel(Protocol):
    name: str

    def send(self, subject: str, body: str) -> None: ...


class EmailChannel:
    name = "email"

    def __init__(self, cfg: AlertConfig, password: str):
        self.cfg = cfg
        self.password = password

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.cfg.email_from or self.cfg.smtp_user
        msg["To"] = self.cfg.email_to
        msg.set_content(body)
        with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if self.cfg.smtp_user:
                smtp.login(self.cfg.smtp_user, self.password)
            smtp.send_message(msg)


class DiscordWebhookChannel:
    name = "discord_webhook"

    def __init__(self, url: str):
        self.url = url

    # Discord's edge (Cloudflare) answers 403 to Python's default
    # "Python-urllib/x.y" User-Agent (G2: PowerShell delivered, the engine got
    # 403); Discord asks API clients to identify themselves like this
    USER_AGENT = "DiscordBot (https://github.com/v6lite, 1.0) V6-Lite-Alerts"

    def send(self, subject: str, body: str) -> None:
        content = f"**{subject}**\n{body}"[:1900]
        self._post({"content": content})

    def send_card(self, card: AlertCard) -> None:
        self._post({"embeds": [card.as_embed()]})

    def _post(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data,
                                     headers={"Content-Type": "application/json", "User-Agent": self.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https webhook
                if resp.status >= 300:
                    raise RuntimeError(f"webhook status {resp.status}")
        except urllib.error.HTTPError as exc:
            # Some servers echo the secret webhook URL in the response body.
            raise RuntimeError(f"webhook HTTP {exc.code}") from None


def build_channels(cfg: AlertConfig, env: Callable[[str], str | None] = os.environ.get) -> list[Channel]:
    channels: list[Channel] = []
    if cfg.email_to and cfg.smtp_host:
        channels.append(EmailChannel(cfg, env(cfg.smtp_password_env) or ""))
    webhook = env(cfg.discord_webhook_env) if cfg.discord_webhook_env else None
    if webhook:
        channels.append(DiscordWebhookChannel(webhook))
    return channels


class Alerter:
    """Log every alert; deliver remote alerts on a background worker so a slow
    SMTP/webhook call can never delay the trading loop.

    Health = at least one channel AND the most recent delivery attempt
    succeeded AND a success happened within HEALTH_WINDOW. A single failed
    attempt makes alerting unhealthy immediately (live entries then block).
    """

    DEDUPE_WINDOW = timedelta(minutes=5)
    # an UNCHANGED condition (same subject and body) is repeated less often:
    # G2 day 1 paged the same persistent mismatch 117 times (every 5 min)
    REPEAT_WINDOW = {"P0": timedelta(minutes=10)}
    REPEAT_WINDOW_DEFAULT = timedelta(minutes=30)
    HEALTH_WINDOW = timedelta(hours=24)

    def __init__(self, channels: list[Channel], *, prefix: str = "[V6-Lite]", synchronous: bool = False):
        self.channels = channels
        self.prefix = prefix
        self.synchronous = synchronous
        self.last_remote_success: datetime | None = None
        self.last_attempt_ok: bool | None = None
        self._recent: dict[str, tuple[datetime, str | None]] = {}
        self._recent_body: dict[str, str] = {}
        self._results: dict[str, bool] = {}
        self._issued: set[str] = set()
        self._delivery_day: dict[str, str] = {}
        self._daily_attempts: dict[str, int] = {}
        self._daily_failures: dict[str, int] = {}
        self._seq = 0
        self._token = uuid.uuid4().hex[:8]     # delivery ids are unique across processes
        self._lock = threading.Lock()
        self._queue: queue.Queue | None = None
        self._worker: threading.Thread | None = None
        if channels and not synchronous:
            self._queue = queue.Queue()
            self._worker = threading.Thread(target=self._run, name="v6lite-alerts", daemon=True)
            self._worker.start()

    # ------------------------------------------------------------ public
    def send(self, now: datetime, level: str, subject: str, body: str = "", *, remote: bool = True,
             dedupe: bool = True, card: AlertCard | None = None) -> str | None:
        """Returns a delivery id when a remote delivery was attempted/queued
        (poll it with `delivery()`), else None (log-only or deduplicated)."""
        key = f"{level}|{subject}"
        last = self._recent.get(key)
        if last and last[1] is None and remote and self.channels:
            last = None             # a log-only record never mutes a later remote page
        window = self.DEDUPE_WINDOW
        if last and self._recent_body.get(key) == body:
            window = max(window, self.REPEAT_WINDOW.get(level, self.REPEAT_WINDOW_DEFAULT))
        if dedupe and last and now - last[0] < window:
            # only suppress a repeat of an alert that was delivered or is still
            # in flight; a FAILED alert must be retried, not deduplicated
            if last[1] is None or self.delivery(last[1]) is not False:
                return None
        self._recent_body[key] = body
        full_subject = (f"{self.prefix} started" if level == "INFO" and subject == "started"
                        else f"{self.prefix} {level}: {subject}")
        logger = log.error if level in {"P0", "P1"} else log.warning if level == "P2" else log.info
        logger("%s | %s", full_subject, body.replace("\n", " | "))
        if not remote or not self.channels:
            self._recent[key] = (now, None)
            return None
        with self._lock:
            self._seq += 1
            delivery_id = f"{self._token}-{self._seq}"
            self._issued.add(delivery_id)
            day = now.astimezone(ET).date().isoformat()
            self._delivery_day[delivery_id] = day
            self._daily_attempts[day] = self._daily_attempts.get(day, 0) + 1
        self._recent[key] = (now, delivery_id)
        if card is None and level in {"P0", "P1"}:
            card = _page_card(level, subject, body)
        elif card is None and level == "INFO" and subject == "IB Gateway responding":
            card = AlertCard("🟢 IBKR 已恢复应答", INFO_GREEN,
                             (("发生了什么", "IBKR 已重新回应当前时间心跳。"),
                              ("影响", "心跳告警解除；其他交易门控仍按实时状态判断。"),
                              ("你要做什么", "在本机运行 status 并核对 IBKR 状态。")))
        elif card is None and level == "INFO" and subject == "Gateway 时钟偏差已恢复":
            offset = _page_number(r"server_minus_host_seconds=([+-]?\d+(?:\.\d+)?)", body)
            card = AlertCard("🟢 IBKR 时钟偏差已恢复", INFO_GREEN,
                             (("发生了什么", f"连续正常 currentTime 回报；最新服务器时间减本机接收时间为 {offset or '未知'} 秒。"),
                              ("影响", "时钟偏差告警已解除；其他交易门控仍按实时状态判断。"),
                              ("你要做什么", "继续使用同一 TWS Paper 会话并观察后续心跳。")))
        elif card is None:
            card = _notice_card(level, subject)
        if "TEST" in self.prefix and "TEST" not in card.title:
            card = replace(card, title="TEST · " + card.title)
        remote_body = body or subject
        if self._queue is not None:
            self._queue.put((delivery_id, full_subject, remote_body, card))
        else:
            self._deliver(delivery_id, full_subject, remote_body, now, card)
        return delivery_id

    def delivery_stats(self, day) -> dict[str, int]:
        """Remote attempts and all-channel failures for an ET trading day."""
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        with self._lock:
            return {"sent": self._daily_attempts.get(key, 0),
                    "failed": self._daily_failures.get(key, 0)}

    def resolve(self, level: str, subject: str) -> None:
        """The condition behind this alert has cleared: if it comes back,
        page it again at once (even with the same text)."""
        key = f"{level}|{subject}"
        self._recent.pop(key, None)
        self._recent_body.pop(key, None)

    def delivery(self, delivery_id: str | None) -> bool | None:
        """True delivered, False failed on every channel, None still pending/unknown id."""
        if delivery_id is None:
            return None
        with self._lock:
            return self._results.get(delivery_id)

    def knows(self, delivery_id: str | None) -> bool:
        """True if this process issued the id (ids from an earlier process can
        never be answered and must be treated as undelivered)."""
        with self._lock:
            return delivery_id in self._issued

    def healthy(self, now: datetime) -> bool:
        with self._lock:
            return bool(self.channels and self.last_attempt_ok and self.last_remote_success
                        and now - self.last_remote_success < self.HEALTH_WINDOW)

    def self_test(self, now: datetime, *, wait: float = 20.0) -> bool:
        if not self.channels:
            return False
        delivery_id = self.send(now, "INFO", "alert self-test",
                                f"V6-Lite alert path check at {now.isoformat()}", dedupe=False)
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            result = self.delivery(delivery_id)
            if result is not None or time.monotonic() >= deadline:
                return bool(result)
            time.sleep(0.05)

    def close(self, timeout: float = 5.0) -> None:
        if self._queue is not None and self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout)

    # ------------------------------------------------------------ worker
    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            delivery_id, subject, body, card = item
            self._deliver(delivery_id, subject, body, None, card)

    def _deliver(self, delivery_id: str, subject: str, body: str, now: datetime | None,
                 card: AlertCard | None = None) -> None:
        delivered = False
        for channel in self.channels:
            started = time.monotonic()
            name = channel.name if channel.name in {"email", "discord_webhook", "memory"} else "other"
            ok = False
            try:
                if card is not None:
                    send_card = getattr(channel, "send_card", None)
                    if send_card is not None:
                        send_card(card)
                    else:
                        channel.send(card.title, card.as_plain_text())
                else:
                    channel.send(subject, body)
                delivered = True
                ok = True
            except Exception as exc:  # never let alerting crash trading
                # Exception text can contain the webhook URL or credentials.
                log.error("alert channel %s failed (%s)", name, type(exc).__name__)
            finally:
                log.info("[ALERT_DELIVERY] channel=%s %s ms=%.1f", name,
                         "ok" if ok else "fail", (time.monotonic() - started) * 1000)
        with self._lock:
            self._results[delivery_id] = delivered
            self.last_attempt_ok = delivered
            if delivered:
                self.last_remote_success = now or datetime.now(timezone.utc)
            else:
                day = self._delivery_day.get(delivery_id)
                if day:
                    self._daily_failures[day] = self._daily_failures.get(day, 0) + 1
            self._delivery_day.pop(delivery_id, None)


class MemoryChannel:
    """Test double."""

    name = "memory"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.messages: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        if self.fail:
            raise RuntimeError("channel down")
        self.messages.append((subject, body))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

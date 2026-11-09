"""Execution-clock provenance before ib_insync can assume the host timezone."""
from __future__ import annotations
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

def safe_raw_time(value, account=None):
    """Retain only a bounded, single-line time sample; never a full SDK row."""
    import re
    if value is None:
        return None
    value = str(value)
    if account:
        value = value.replace(str(account), "<account>")
    value = re.sub(r"(?<![A-Za-z0-9])(?:DU|DFA|DF|U|F|I)\d{4,}(?![A-Za-z0-9])", "<account>", value)
    return value.replace("\r", "\\r").replace("\n", "\\n")[:256]


def raw_time_category(raw):
    from ib_insync.util import parseIBDatetime
    try:
        parsed = parseIBDatetime(raw)
        if not isinstance(parsed, datetime):
            return "unparseable"
        return "zoned" if parsed.tzinfo is not None else "naive"
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return "unparseable"


class _ExecutionLogFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.accounts = set()

    def filter(self, record):
        message = record.getMessage()
        if message.startswith(("execDetails ", "execDetails:", "commissionReport:")):
            for account in self.accounts:
                message = message.replace(account, "<account>")
            record.msg, record.args = message, ()
            if record.levelno == logging.INFO:
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
                return logging.getLogger().isEnabledFor(logging.DEBUG)
        return True


def install_execution_log_filter(account=None):
    logger = logging.getLogger("ib_insync.wrapper")
    current = next((item for item in logger.filters if isinstance(item, _ExecutionLogFilter)), None)
    if current is None:
        current = _ExecutionLogFilter()
        logger.addFilter(current)
    if account:
        current.accounts.add(str(account))


def install_execution_clock_hooks(ib):
    """Instance-only provenance/diagnostics; preserve the SDK time parser.

    Failed decoder rows remain unbooked exactly as before. Their time field
    is reported separately without letting SDK.interpret print all fields.
    """
    decoder = getattr(getattr(ib, "client", None), "decoder", None)
    wrapper = getattr(ib, "wrapper", None)
    if decoder is None or wrapper is None or getattr(decoder, "_v6lite_clock_hook", False):
        return
    handlers = getattr(decoder, "handlers", {})
    original_decode = handlers.get(11)
    original_exec = getattr(wrapper, "execDetails", None)
    if original_decode is None or original_exec is None:
        return
    from ib_insync.util import parseIBDatetime
    context = []
    def decode(fields):
        raw = fields[15] if len(fields) > 15 else None
        try:
            req_id = int(fields[1])
        except (ValueError, TypeError, IndexError):
            req_id = -1
        source = "reqExecutions" if req_id in wrapper._futures else "callback"
        category = raw_time_category(raw)
        item = {"raw": raw, "source": source, "category": category, "invalid": False}
        context.append(item)
        try:
            result = original_decode(fields)
        except Exception:
            # SDK.interpret would otherwise swallow this exception and dump
            # the full wire row/account. Preserve no-Fill semantics, replacing
            # that unsafe log with the isolated diagnostic below.
            item["invalid"] = True
            result = None
        finally:
            context.pop()
        observe = getattr(ib, "_v6lite_execution_time_observer", None)
        if callable(observe):
            observe(req_id, str(fields[14]) if len(fields) > 14 else "", raw,
                    source, category, item["invalid"])
        return result
    def execution(req_id, contract, ex):
        source = "reqExecutions" if req_id in wrapper._futures else "callback"
        item = context[-1] if context else {}
        raw = item.get("raw")
        uncertain = False
        occurred = getattr(ex, "time", None)
        if raw is not None:
            try:
                occurred = parseIBDatetime(raw)
                if not isinstance(occurred, datetime):
                    raise ValueError("execution timestamp is not a datetime")
                if occurred.tzinfo is None:
                    zone = getattr(ib, "TimezoneTWS", "")
                    if not zone:
                        raise ValueError("naive execution timestamp has no explicit TWS zone")
                    occurred = occurred.replace(tzinfo=ZoneInfo(str(zone)))
                occurred = occurred.astimezone(timezone.utc)
            except (ValueError, TypeError, KeyError):
                uncertain = True
                received = getattr(wrapper, "lastTime", None)
                occurred = (received.astimezone(timezone.utc) if isinstance(received, datetime)
                            and received.tzinfo is not None else datetime.now(timezone.utc))
        ex.time = occurred
        ex._v6lite_time_source = source
        ex._v6lite_time_uncertain = uncertain
        ex._v6lite_raw_time = raw
        return original_exec(req_id, contract, ex)
    handlers[11] = decode
    wrapper.execDetails = execution
    decoder._v6lite_clock_hook = True


def receipt_identity(fill, raw, con_id, *, pinned_con_id=None):
    """Strict marker/ledger/contract binding shared by the two day proofs."""
    import json
    import math
    pending = json.loads(raw)
    if not isinstance(pending, dict) or not isinstance(pending.get("identity"), list):
        raise ValueError("账本成交时间标记无法解析")
    identity = pending["identity"]
    if (len(identity) != 9 or any(not isinstance(identity[i], str) or not identity[i] for i in range(4)) or
            any(type(identity[i]) is not int or identity[i] <= 0 for i in (4, 6, 7, 8)) or
            type(identity[5]) not in (int, float) or not math.isfinite(identity[5]) or identity[5] <= 0):
        raise ValueError("账本成交时间标记 identity 字段类型或数值无效")
    if type(con_id) is not int or con_id <= 0 or pinned_con_id != str(con_id):
        raise ValueError("账本成交缺少一致的持久化合约 pin 与保护身份")
    expected = [fill["exec_id"], fill["intent_id"], fill["symbol"], fill["side"],
                fill["quantity"], fill["price"], fill["broker_order_id"], fill["perm_id"], con_id]
    if identity != expected:
        raise ValueError("账本成交时间标记 identity 与持久化成交/合约不一致")
    if pending.get("provisional_time") != fill["time"]:
        raise ValueError("账本成交时间标记 provisional_time 与成交行不一致")
    received = datetime.fromisoformat(pending["provisional_time"])
    if received.tzinfo is None:
        raise ValueError("账本成交时间尚未核实：收到时间缺少时区")
    return received


def historical_receipt_before(fill, raw, con_id, now, *, pinned_con_id=None):
    """Narrow read-only receipt bound; never an exact broker-time verification.

    fills has no conId column. The ninth marker field is therefore compared
    with the persisted protection contract, not invented from a fill row.
    """
    from v6lite.market_calendar import ET
    received = receipt_identity(fill, raw, con_id, pinned_con_id=pinned_con_id)
    cutoff = datetime.combine(now.astimezone(ET).date(), time(0), tzinfo=ET) - timedelta(hours=1)
    if received.tzinfo is None or not received < cutoff:
        raise ValueError("账本成交时间尚未核实：收到时间未早于今日零点前一小时")
    return ("入场成交时间按收到回报时间判定为 " + now.astimezone(ET).date().isoformat() +
            " 之前（未经券商查询核实）")

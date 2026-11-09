"""Read-only daily evidence interpretation, with no order/protection authority."""
from __future__ import annotations
import json
import math
from decimal import DecimalException
from v6lite.test_tp_timeout import CLAIM, timeout_price


def _positive(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>0


def injected_gap(store, gap):
    """Only a historical bound claim AND actual sent intent exempt a B from normal metrics."""
    try:
        iid = gap.get("tp_intent_id")
        intent = store.intent(iid) if iid else None
        fact = json.loads(store.control(CLAIM+str(iid)) or "null")
        if not isinstance(fact,dict) or intent is None or fact.get("version")!=29:
            return False
        pricing = fact["pricing"]
        if (not isinstance(pricing,dict) or not isinstance(fact.get("request_id"),str) or
                not fact["request_id"].strip() or not isinstance(fact.get("workflow_id"),str) or
                type(fact.get("quantity")) is not int or type(fact.get("version")) is not int):
            return False
        if (not intent["sent_at"] or not _positive(intent["broker_order_id"]) or
                not fact.get("request_id") or not fact.get("workflow_id") or
                fact["workflow_id"]!=gap.get("workflow_id") or
                fact["position_id"]!=gap.get("position_id") or fact["position_id"]!=intent["position_id"] or
                fact["symbol"]!=intent["symbol"] or fact["symbol"]!=gap.get("symbol") or
                fact["intent_id"]!=iid or fact["quantity"]!=intent["quantity"] or
                fact["stage"]!=intent["purpose"] or fact["stage"] not in {"TP1","TP2","TP3"} or
                fact["protection_intent_id"]!=gap.get("intent_id") or not _positive(fact.get("S")) or
                fact["limit_price"]!=intent["limit_price"] or not _positive(pricing.get("ask")) or
                fact["limit_price"]!=timeout_price(pricing["ask"]) or
                pricing.get("workflow_id")!=fact["workflow_id"] or
                pricing.get("test_timeout_request_id")!=fact["request_id"]):
            return False
        claimed = store.conn.execute(
            "SELECT 1 FROM audit WHERE kind='TEST_TP_TIMEOUT_RESULT' AND json_valid(payload) "
            "AND json_extract(payload,'$.phase')='claimed' AND json_extract(payload,'$.request_id')=? "
            "AND json_extract(payload,'$.intent_id')=? AND json_extract(payload,'$.workflow_id')=? "
            "AND json_extract(payload,'$.quantity')=? AND json_extract(payload,'$.limit_price')=? LIMIT 1",
            (fact["request_id"],iid,fact["workflow_id"],intent["quantity"],intent["limit_price"])).fetchone()
        return claimed is not None
    except (ValueError,TypeError,KeyError,IndexError,OverflowError,DecimalException):
        return False  # incomplete/mismatching test labels stay in normal compliance


def fill_bid_comparison(store, value, fill):
    """Recompute from original send bid and current verified ledger; do not rewrite audit."""
    bid = value.get("bid_at_send")
    if not _positive(bid):
        # A lost diagnostic control is recoverable from the exact immutable send.
        bids = set()
        intent = store.intent(fill["intent_id"])
        for row in store.conn.execute(
            "SELECT payload FROM audit WHERE kind='TP_EVIDENCE' AND json_valid(payload) "
            "AND json_extract(payload,'$.phase')='send' AND json_extract(payload,'$.intent_id')=?",
            (fill["intent_id"],)):
            sent = json.loads(row["payload"])
            if (isinstance(sent,dict) and intent is not None and
                    sent.get("position_id")==fill["position_id"] and sent.get("symbol")==fill["symbol"] and
                    sent.get("stage")==intent["purpose"] and sent.get("quantity")==intent["quantity"] and
                    sent.get("sent_price")==intent["limit_price"] and
                    isinstance(sent.get("workflow_id"),str) and bool(sent["workflow_id"]) and
                    (value.get("workflow_id") is None or sent["workflow_id"]==value["workflow_id"]) and
                    _positive(sent.get("bid"))):
                bids.add((sent["workflow_id"],float(sent["bid"])))
        if len(bids)==1:
            _,bid = bids.pop()
        elif len(bids)>1:
            return None,None,"发送 bid 证据不一致"
    if not _positive(bid):
        diagnostics = value.get("evidence_diagnostics") or []
        reason = ("发送 bid 缺失或无效" if value.get("bid_raw") not in (None,"None") or
                  any("bid" in str(d) for d in diagnostics) else "发送快照缺失")
        return None,None,reason
    if store.control("execution_time_unverified:"+fill["exec_id"]) is not None:
        return bid,None,"成交时间未核实（等待完整成交查询）"
    if value.get("time_uncertain"):
        verified = store.conn.execute(
            "SELECT 1 FROM audit WHERE kind='EXECUTION_TIME_VERIFIED' AND json_valid(payload) "
            "AND json_extract(payload,'$.exec_id')=? AND json_extract(payload,'$.verified')=? LIMIT 1",
            (fill["exec_id"],fill["time"])).fetchone()
        if verified is None:
            return bid,None,"缺少当前账本成交时间的完整查询核验证据"
    difference = fill["price"]-bid
    if not math.isfinite(difference):
        return bid,None,"成交价或 bid 无效"
    return bid,difference,None

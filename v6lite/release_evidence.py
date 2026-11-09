"""Completed cancel proof under the operator's explicit B32 Addendum contract.

Pure validation only. No adoption of native IDs, CANCEL_SEEN writes, or fills.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from v6lite.models import CompletedCancelProof, CompletedOrdersSnapshot, Purpose


class CompletedEvidenceError(ValueError):
    def __init__(self, oid, code, detail):
        super().__init__(f"protected GTC {oid} completed evidence {code}; release refused")
        self.operator_reason = f"保护单 {oid} 的撤单未被核实：{detail}"
        self.evidence_code = code


def _price_equal(left, right):
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return not isinstance(left, bool) and a.is_finite() and b.is_finite() and a > 0 and a == b
    except (InvalidOperation, ValueError, TypeError):
        return False


def completed_cancel_proof(snapshot, protection, intent, position, booked, now):
    row, intent, position = dict(protection), dict(intent), dict(position)
    oid = row['broker_order_id']
    def refuse(code, detail):
        raise CompletedEvidenceError(oid, code, detail)
    if not isinstance(snapshot, CompletedOrdersSnapshot) or snapshot.query_request_id <= 0:
        refuse('incomplete_query', '缺少本轮完整 completed orders 请求及 End。')
    if (not oid or not row['perm_id'] or row['perm_id'] != intent['perm_id'] or
            oid != intent['broker_order_id'] or intent['purpose'] != Purpose.PROTECT or
            intent['side'] != 'SELL' or intent['hold_reason'] or intent['intent_id'] != row['intent_id'] or
            intent['position_id'] != row['position_id'] or intent['symbol'] != row['symbol'] or
            position['position_id'] != row['position_id'] or position['symbol'] != row['symbol']):
        refuse('prior_binding', '缺少之前已持久化的保护单 orderId/permId 强身份绑定，或记录被 hold。')
    try:
        confirmed = datetime.fromisoformat(row['confirmed_at'])
        if confirmed.tzinfo is None or confirmed > snapshot.query_started_at:
            raise ValueError('not prior')
    except (ValueError, TypeError, KeyError):
        refuse('prior_confirmation', '缺少本轮查询之前的强身份确认时间。')
    rows = [order for order in snapshot.orders if order.perm_id == row['perm_id']]
    if not rows:
        if any(order.intent_id == row['intent_id'] for order in snapshot.orders):
            refuse('perm_id', 'completed orders 的原生 permId 缺失或与持久化绑定不同。')
        refuse('missing_record', 'completed orders 中未找到同 permId 的 Cancelled/ApiCancelled 记录。')
    first = rows[0]
    if any(order.raw_signature != first.raw_signature for order in rows[1:]):
        refuse('conflicting_rows', '本轮同一 permId 出现内容不一致的多条原生记录。')
    for field, expected in [('account', row['account']), ('con_id', row['con_id']),
                            ('intent_id', row['intent_id']), ('symbol', row['symbol'])]:
        if getattr(first, field) != expected or expected in (None, ''):
            refuse(field, f"completed orders 的 {dict(account='account',con_id='conId',intent_id='orderRef',symbol='symbol')[field]} 与持久化记录不同或缺失。")
    if not first.native_order_id_absent and first.broker_order_id != oid:
        refuse('order_id', '原生 orderId 与之前持久化的 orderId 不同。')
    if not first.native_client_id_absent and first.client_id != row['client_id']:
        refuse('client_id', '非零原生 clientId 与持久化客户端绑定不同。')
    if (first.sec_type != 'STK' or first.currency != 'USD' or first.action != 'SELL' or
            first.order_type != 'STP' or first.tif != 'GTC' or first.outside_rth is not False or
            row['order_type'] != 'STP' or row['limit_price'] is not None or row['pending_limit'] is not None):
        refuse('contract', '合约或订单内容不一致，必须是 STK/USD、SELL、STP、GTC、outsideRth=false。')
    pairs = [('confirmed', row['requested_qty'], row['trigger_price'])]
    if row['pending_qty'] is not None:
        pairs.append(('pending', row['pending_qty'], row['pending_trigger'] if row['pending_trigger'] is not None else row['trigger_price']))
    matches = [name for name, qty, price in pairs if qty and first.total_quantity == qty and _price_equal(first.aux_price, price)]
    if not matches:
        refuse('quantity_or_aux', '数量与 auxPrice 未同时匹配最后确认值或本次待确认修改值。')
    if first.status not in {'Cancelled', 'ApiCancelled'}:
        refuse('status', '原生状态不是 Cancelled/ApiCancelled（Filled 等其他状态不能证明撤单）。')
    if first.filled is None or first.filled != booked or first.filled > first.total_quantity:
        refuse('filled', '原生 filledQuantity 缺失或与这张保护单已正常入账的成交量不同。')
    if (first.completed_time is None or first.completed_time <= confirmed or
            first.completed_time > snapshot.completed_at or snapshot.completed_at > now):
        refuse('completed_time', 'completedTime 缺失、时区不明、未晚于最后确认时间，或时间在未来。')
    return CompletedCancelProof(snapshot, first, tuple(sorted(row.items())), tuple(sorted(intent.items())),
        tuple(sorted(position.items())), booked, len(rows)-1, matches[0])


def proof_audit(proof):
    order=proof.order; row=dict(proof.protection)
    return dict(position_id=row['position_id'], intent_id=row['intent_id'], symbol=row['symbol'],
        native_order_id_absent=order.native_order_id_absent, native_order_id=order.broker_order_id,
        native_client_id_absent=order.native_client_id_absent, native_client_id=order.client_id,
        persisted_order_id=row['broker_order_id'], persisted_perm_id=row['perm_id'],
        binding_source='persisted_previously_confirmed_protection', prior_confirmed_at=row['confirmed_at'],
        native_perm_id=order.perm_id, status=order.status, filled=order.filled,
        quantity=order.total_quantity, aux_price=str(order.aux_price), matched_values=proof.matched_values,
        completed_time=order.completed_time.isoformat(), duplicate_rows=proof.duplicate_rows,
        query_started_at=proof.snapshot.query_started_at.isoformat(),
        query_completed_at=proof.snapshot.completed_at.isoformat(),
        socket_generation=proof.snapshot.socket_generation, query_request_id=proof.snapshot.query_request_id)

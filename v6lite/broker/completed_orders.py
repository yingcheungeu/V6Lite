"""Project only the completedOrder callback's native fields, never SDK caches."""
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import json, re
from zoneinfo import ZoneInfo
from v6lite.broker.open_orders import _encoded
from v6lite.models import CompletedOrder


def _integer(value, *, positive=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or number < (1 if positive else 0) or
                number >= Decimal('1e308') or number != number.to_integral_value()):
            return None
        return int(number)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _price(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and 0 < number < Decimal('1e308') else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _completed_time(raw, tws_zone):
    # Completed timestamps must consume the entire native string. SDK's general
    # parser truncates unknown suffixes; that is not safe for terminal proof.
    try:
        if re.fullmatch(r'\d{9,12}', raw):
            return datetime.fromtimestamp(int(raw), timezone.utc)
        match = re.fullmatch(
            r'(\d{8}|\d{4}-\d{2}-\d{2})[ -]{1,2}(\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?: ([A-Za-z][A-Za-z0-9_+/-]*))?', raw)
        if match is None:
            return None
        day, clock, fraction, named_zone = match.groups()
        fmt = '%Y%m%d %H:%M:%S' if '-' not in day else '%Y-%m-%d %H:%M:%S'
        naive = datetime.strptime(day+' '+clock, fmt)
        if fraction:
            naive = naive.replace(microsecond=int(fraction.ljust(6,'0')))
        zone_name = named_zone or tws_zone
        if not zone_name:
            return None
        zone = ZoneInfo(str(zone_name))
        candidates = set()
        for fold in (0, 1):
            aware = naive.replace(tzinfo=zone, fold=fold)
            utc = aware.astimezone(timezone.utc)
            if utc.astimezone(zone).replace(tzinfo=None) == naive:
                candidates.add(utc)
        return candidates.pop() if len(candidates) == 1 else None
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, OSError):
        return None


def native_completed_order(contract, order, state, *, tws_zone='', native_fields=None, raw_order=None):
    # ib_insync 0.9.86 does not decode orderId in this message: its default 0
    # becomes None. Neither a local order binding nor Trade.orderStatus.filled
    # may fill a missing native field. filledQuantity is the wire aggregate.
    raw_order = raw_order or {}
    raw_time = str(getattr(state, 'completedTime', '') or '')
    signature = tuple(sorted((prefix + '.' + key, _encoded(value))
        for prefix, obj in [('contract', contract), ('order', order), ('state', state)]
        for key, value in vars(obj).items()))
    if native_fields is not None:
        signature += (('wire.tokens', json.dumps(tuple(native_fields),ensure_ascii=True)),)
    return CompletedOrder(
        str(getattr(order, 'account', '') or ''),
        _integer(getattr(contract, 'conId', None), positive=True),
        _integer(raw_order.get('orderId', getattr(order, 'orderId', None)), positive=True),
        _integer(raw_order.get('permId', getattr(order, 'permId', None)), positive=True),
        str(getattr(order, 'orderRef', '') or ''),
        str(getattr(contract, 'symbol', '') or '').upper(),
        str(getattr(state, 'status', '') or ''),
        _integer(raw_order.get('filledQuantity', getattr(order, 'filledQuantity', None))),
        _integer(raw_order.get('totalQuantity', getattr(order, 'totalQuantity', None)), positive=True),
        str(getattr(order, 'orderType', '') or ''),
        str(getattr(order, 'action', '') or ''),
        str(getattr(contract, 'secType', '') or ''),
        str(getattr(contract, 'currency', '') or ''),
        _integer(raw_order.get('clientId', getattr(order, 'clientId', None)), positive=True),
        str(getattr(order, 'tif', '') or ''),
        _price(raw_order.get('auxPrice', getattr(order, 'auxPrice', None))),
        getattr(order, 'outsideRth', None) if type(getattr(order, 'outsideRth', None)) is bool else None,
        _completed_time(raw_time, tws_zone), raw_time, signature,
        getattr(order, 'orderId', None) is None or type(getattr(order, 'orderId', None)) is int and order.orderId == 0,
        getattr(order, 'clientId', None) is None or type(getattr(order, 'clientId', None)) is int and order.clientId == 0)

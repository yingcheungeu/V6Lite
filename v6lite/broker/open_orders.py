"""Canonicalize native own-query rows before any lossy adapter projection."""
from __future__ import annotations
import json
from decimal import Decimal
from v6lite.models import OpenOrdersNormalizationEvent

_ORDER_FIELDS=('orderId','permId','clientId','account','orderRef','action','orderType',
    'tif','outsideRth','auxPrice','lmtPrice','totalQuantity')

def _encoded(value):
    # Compare exact native numeric values, not int/float formatting. Never
    # coerce via float: large integer identities must remain distinguishable.
    if type(value) in (int,float,Decimal):
        number=Decimal(str(value))
        if number.is_finite():
            sign,digits,exponent=number.as_tuple()
            if not any(digits):return 'number:0'
            digits=list(digits)
            while digits[-1]==0:digits.pop();exponent+=1
            return 'number:'+json.dumps((sign,digits,exponent))
    return json.dumps((type(value).__name__,value),sort_keys=True,default=repr)

def _signature(trade):
    fields={'contract.'+k:_encoded(v) for k,v in vars(trade.contract).items()}
    fields.update({'order.'+k:_encoded(getattr(trade.order,k,None)) for k in _ORDER_FIELDS})
    fields['status']=_encoded(getattr(trade.orderStatus,'status',None))
    fields['filled']=_encoded(getattr(trade,'_native_filled_raw',getattr(trade.orderStatus,'filled',None)))
    fields['remaining']=_encoded(getattr(trade.orderStatus,'remaining',None))
    return fields

def _ids(rows,field):
    return tuple(sorted({v for t in rows if type(v:=getattr(t.order,field,None)) is int and v>0}))

def _unreliable_fields(row):
    o,c=row.order,row.contract;bad=set()
    if not (all(type(v) is int and v>0 for v in (getattr(o,'orderId',None),getattr(o,'permId',None),getattr(c,'conId',None)))
            and type(getattr(o,'clientId',None)) is int and o.clientId>=0
            and isinstance(getattr(o,'account',None),str) and bool(o.account)):
        bad.add('incomplete_strong_identity')
    values=(getattr(o,'totalQuantity',None),
        getattr(row,'_native_filled_raw',getattr(row.orderStatus,'filled',None)),
        getattr(row.orderStatus,'remaining',None))
    counts=[]
    for value in values:
        if type(value) not in (int,float,Decimal):break  # bool is never a quantity
        number=Decimal(str(value))
        if not number.is_finite() or number<0 or number!=number.to_integral_value():break
        counts.append(int(number))
    if len(counts)!=3 or counts[0]<=0 or counts[1]+counts[2]>counts[0]:
        bad.add('incomplete_native_quantity')
    for field in ('auxPrice','lmtPrice'):
        value=getattr(o,field,None)
        if type(value) in (int,float,Decimal) and not Decimal(str(value)).is_finite():
            bad.add('incomplete_native_price')
    return bad

def normalize_open_order_rows(trades,*,at,generation,request_id):
    """Only exact, strongly identified own duplicates collapse; no last wins.

    Any contradiction is returned as an event. The caller MUST reject the
    entire query if any event has conflicting_fields, before emitting an ACK.
    """
    rows=list(trades)
    if not rows:return rows,()
    groups={}
    for i,t in enumerate(rows):
        groups.setdefault(str(getattr(t.order,'orderId',None)),[]).append((i,t))
    events=[];drop=set();refs={}
    def event(group,fields=(),duplicates=0):
        ownrefs=tuple(sorted({str(getattr(t.order,'orderRef','') or '') for t in group if str(getattr(t.order,'orderRef','') or '').startswith('v6l-')}))
        return OpenOrdersNormalizationEvent(_ids(group,'orderId'),_ids(group,'permId'),ownrefs,
            len(group),duplicates,tuple(sorted(fields)),at,generation,request_id)
    for group in groups.values():
        values=[t for _,t in group]
        if not any(str(getattr(t.order,'orderRef','') or '').startswith('v6l-') for t in values):continue
        for t in values:
            ref=str(getattr(t.order,'orderRef','') or '')
            if ref.startswith('v6l-'):refs.setdefault(ref,[]).append(t)
        if len(values)<2:
            if hasattr(values[0],'_native_filled_raw') and 'incomplete_native_quantity' in _unreliable_fields(values[0]):
                events.append(event(values,('incomplete_native_quantity',)))
            continue
        signatures=[_signature(t) for t in values]
        keys=set().union(*(s.keys() for s in signatures))
        fields={k for k in keys if len({s.get(k) for s in signatures})!=1}
        fields.update(set().union(*(_unreliable_fields(t) for t in values)))
        if not all(hasattr(t,'_native_filled_raw') for t in values):
            fields.add('unpreserved_native_rows')
        if fields:events.append(event(values,fields))
        else:
            drop.update(i for i,_ in group[1:])
            events.append(event(values,duplicates=len(values)-1))
    for ref,values in refs.items():
        if len({str(getattr(t.order,'orderId',None)) for t in values})>1:
            events.append(event(values,('order.orderId',)))
    return [t for i,t in enumerate(rows) if i not in drop],tuple(events)

"""Pinned real SDK decoder: completed does not carry native API orderId."""
import json
from pathlib import Path
from ib_insync import IB
from ib_insync.decoder import Decoder

def test_completed_decoder_exposes_missing_ID_and_actual_filled_field():
    data=json.loads(Path(__file__).with_name('completed_native_probe_fields.json').read_text())
    ib=IB()
    try:
        for quantity in [0,1]:
            fields=list(data['fields'])
            fields[data['field_names'].index('o.filledQuantity')]=str(quantity)
            future=ib.wrapper.startReq('completedOrders')
            decoder=Decoder(ib.wrapper,data['server_version'])
            decoder.completedOrder(fields)
            ib.wrapper.completedOrdersEnd()
            assert future.done()
            [trade]=future.result()
            assert trade.order.orderId==0 and trade.order.clientId==0
            assert trade.order.permId==753795445 and trade.contract.conId==265598
            assert trade.order.account=='SYNTHETIC-ACCOUNT'
            assert trade.order.orderRef=='v6l-b32a-offline-cancel'
            assert trade.orderStatus.status=='Cancelled'
            assert trade.order.filledQuantity==quantity
            assert trade.orderStatus.filled==0, 'SDK default remains 0 even when native completed filledQuantity=1'
    finally:
        ib.client.reset()

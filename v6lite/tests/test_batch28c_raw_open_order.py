"""Raw openOrder contract/status must survive SDK's selective Trade merge."""
from dataclasses import replace
import pytest

from v6lite.tests.test_batch26_overnight_evidence import _no_network, _tp_requests, _writes, _safety
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run, _tick_until
from v6lite.tests.test_batch28c_price_confirmation import price_run, _be_pending


@pytest.mark.parametrize('source', ['live', 'owned-round'])
@pytest.mark.parametrize('wrong', ['Inactive', 'PendingCancel', 'account', 'conId', 'side',
                                  'tif', 'outsideRth', 'currency', 'secType', 'clientId'])
def test_B28c_raw_openOrder_conflict_cannot_be_hidden_by_existing_SDK_Trade(price_run, monkeypatch, source, wrong):
    rig, pid, wire = price_run()
    pending = _be_pending(rig, pid, wire)
    value = wire.apply_pending()
    native = wire.native
    def corrupt(row):
        contract, order, state = native(row)
        if row.intent_id == pending['intent_id']:
            if wrong in {'Inactive', 'PendingCancel'}: state.status = wrong
            elif wrong == 'account': order.account = 'DU0000000'
            elif wrong == 'conId': contract.conId += 7
            elif wrong == 'side': order.action = 'BUY'
            elif wrong == 'tif': order.tif = 'DAY'
            elif wrong == 'outsideRth': order.outsideRth = True
            elif wrong == 'currency': contract.currency = 'CAD'
            elif wrong == 'secType': contract.secType = 'OPT'
            elif wrong == 'clientId': order.clientId += 1
        return contract, order, state
    monkeypatch.setattr(wire, 'native', corrupt)
    before = _writes(rig)
    rig.advance(microseconds=1000)
    if source == 'live':
        wire.open_order(value)
        rig.engine._drain(rig.clock.now())
        assert rig.store.protection(pid)['state'] != 'WORKING', 'SDK cached identity/status cannot authorize ACK'
    rounds, ends = len(wire.sources), len(wire.ends)
    if wrong in {'currency', 'secType'}:
        # STP outside STK/USD is rejected by open_orders before later sources.
        # Require an actual native own-End and rejection, never a fake proof.
        _tick_until(rig, lambda: len(wire.ends) > ends and
                    any(level == 'P1' for level, _ in rig._b26_alerts), seconds=45)
    else:
        _tick_until(rig, lambda: any(proof and name == 'account_executions_today_scoped'
                    for name, _, _, proof in wire.sources[rounds:]), seconds=45)
    row = rig.store.protection(pid)
    assert row['state'] != 'WORKING' and row['pending_qty'] == 2
    assert row['trigger_price'] == pending['trigger_price']
    assert len(_tp_requests(rig)) == 1 and _writes(rig) == before
    _safety(rig)

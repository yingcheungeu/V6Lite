"""Seven user-mandated predicates and unchanged release guards, actual decoder."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
import pytest
from v6lite.models import FillEvent, Side, BrokerPosition
from v6lite.tests.test_batch32_offline_release import (
    offline_oct29, historical_perm_binding, oct29, overnight_run, _no_network)
from v6lite.tests.test_batch16_f6_release import _request_release
from v6lite.tests.test_batch28b_overnight_refresh import _audits


def _economics(rig):
    tables=('positions','intents','protections','fills','commissions','exit_stages','releases','risk_days')
    return {name:tuple(tuple(r) for r in rig.store.conn.execute('SELECT * FROM '+name+' ORDER BY rowid')) for name in tables}


def _refused_without_proof(rig,pid,before,fragment):
    assert _economics(rig)==before
    assert rig.store.position(pid)['status']=='OPEN' and rig.store.control('frozen:AAPL') is not None
    assert not _audits(rig,'PROTECT_CANCEL_PROVEN_COMPLETED')
    assert rig.store.intent(rig.store.protection(pid)['intent_id'])['cancel_seen_at'] is None
    [refusal]=_audits(rig,'RELEASE_REFUSED')
    assert [body for title,body in rig.channel.messages if '人工释放被拒' in title and fragment in body]
    assert not [title for title,body in rig.channel.messages if '已解除冻结' in title]


@pytest.mark.parametrize('field,value,fragment',[
    ('o.permId','753795446','permId'),('o.permId','0','permId'),
    ('o.account','SYNTHETIC-WRONG','account'),('c.conId','265599','conId'),
    ('o.orderRef','v6l-wrong','orderRef'),('st.status','Filled','原生状态'),
    ('o.filledQuantity','1','filledQuantity'),('o.filledQuantity','NaN','filledQuantity'),
    ('o.totalQuantity','3','数量与 auxPrice'),('o.auxPrice','344.58','数量与 auxPrice'),
    ('o.auxPrice','344.570000001','数量与 auxPrice'),
    ('o.action','BUY','合约或订单内容'),('o.orderType','STP LMT','合约或订单内容'),
    ('o.tif','DAY','合约或订单内容'),('o.outsideRth','1','合约或订单内容'),
    ('st.completedTime','20261028 15:50:00 UTC','completedTime'),
    ('st.completedTime','','completedTime'),('st.completedTime','20261029 15:50:00','时区不明'),
    ('st.completedTime','20261030 15:50:00 UTC','completedTime'),
])
def test_B32A_missing_identity_content_status_fill_or_time_refuses_unchanged(offline_oct29,field,value,fragment):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    responses[0][data['field_names'].index(field)]=value
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,fragment)


def test_B32A_missing_completed_record_is_not_proven_by_absence_from_openOrders(offline_oct29,record_property):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    responses.clear();before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'未找到同 permId')
    [body]=[body for title,body in rig.channel.messages if '人工释放被拒' in title]
    record_property('b32a_refusal_card',body)


@pytest.mark.parametrize('field,value',[('st.status','Filled'),('o.auxPrice','344.58'),('o.totalQuantity','3'),('st.completedStatus','DIFFERENT')])
def test_B32A_contradictory_duplicate_perm_rejects_all_rows(offline_oct29,field,value):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    other=list(responses[0]);other[data['field_names'].index(field)]=value
    responses.append(other);before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'内容不一致')


def test_B32A_exact_completed_duplicates_collapse_only_with_audited_count(offline_oct29):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    responses.extend([list(responses[0]),list(responses[0])]);_request_release(rig)
    assert rig.store.position(pid)['status']=='RELEASED'
    [proof]=_audits(rig,'PROTECT_CANCEL_PROVEN_COMPLETED')
    assert proof['duplicate_rows']==2 and proof['persisted_perm_id']==753795445


def test_B32A_missing_prior_confirmation_cannot_be_minted_by_completed_query(offline_oct29):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    rig.store.update_protection(rig.clock.now(),pid,confirmed_at=None)
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'缺少本轮查询之前')


def test_B32A_native_decode_error_with_End_rejects_partial_success(offline_oct29):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    bad=list(responses[0]);bad[data['field_names'].index('o.filledQuantity')]='bad'
    responses.append(bad);before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'原生解码未完整结束')


def test_B32A_account_execution_between_snapshots_refuses_without_fake_cancel(offline_oct29,monkeypatch):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    query=rig.broker.account_executions_today;calls=[]
    def changing():
        rows=query();calls.append(1)
        if len(calls)>1:
            rows=list(rows)+[replace(external[0],exec_id='external-new-between-snapshots',quantity=1,intent_id='')]
        return rows
    monkeypatch.setattr(rig.broker,'account_executions_today',changing)
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'成交签名改变')


def test_B32A_first_position_must_already_be_flat_even_if_second_flat(offline_oct29,monkeypatch):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    original=rig.broker.positions_fresh;calls=[]
    def positions():
        rows=original();calls.append(1)
        if len(calls)==1:return [BrokerPosition('AAPL',2,344.40,rig.cfg.account,265598)]
        return rows
    monkeypatch.setattr(rig.broker,'positions_fresh',positions)
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'持仓发生变化')


def test_B32A_local_binding_change_at_final_commit_rolls_back_proof_audit(offline_oct29,monkeypatch):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    commit=rig.store._commit_completed_cancel_proofs
    def changed(now,symbol,proofs):
        # Deliberately change only inside the final transaction: its exception
        # must roll this back together with any staged proof/release mutation.
        rig.store.conn.execute('UPDATE intents SET hold_reason=? WHERE intent_id=?',('new hold',ref))
        return commit(now,symbol,proofs)
    monkeypatch.setattr(rig.store,'_commit_completed_cancel_proofs',changed)
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'本地持仓、保护单绑定或成交记录改变')


@pytest.mark.parametrize('offline_oct29',['pending'],indirect=True)
def test_B32A_completed_pending_modification_values_are_accepted_as_one_pair(offline_oct29):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    assert rig.store.protection(pid)['pending_trigger']==344.57
    assert rig.store.protection(pid)['trigger_price']==340.58
    _request_release(rig)
    assert rig.store.position(pid)['status']=='RELEASED'
    [proof]=_audits(rig,'PROTECT_CANCEL_PROVEN_COMPLETED')
    assert proof['matched_values']=='pending'


def test_B32A_final_release_failure_rolls_back_already_written_completed_audit(offline_oct29,monkeypatch):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    audit=rig.store.audit;seen=[]
    def fail(now,kind,**payload):
        if kind=='PROTECT_CANCEL_PROVEN_COMPLETED':seen.append(kind)
        if kind=='POSITION_RELEASED':raise ValueError('synthetic final commit failure')
        return audit(now,kind,**payload)
    monkeypatch.setattr(rig.store,'audit',fail)
    before=_economics(rig);_request_release(rig)
    assert seen==['PROTECT_CANCEL_PROVEN_COMPLETED']
    _refused_without_proof(rig,pid,before,'核验未完成')


def test_B32A_completed_aggregate_never_replaces_real_own_execution_booking(offline_oct29):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    # A real native client execution is discovered before aggregate comparison.
    # It must be booked even though the subsequent release is refused.
    fill=rig.broker.fill(ref,qty=1,price=345.,emit=False,exec_id='b32-own-stop.01.01')
    responses[0][data['field_names'].index('o.filledQuantity')]='1'
    _request_release(rig)
    booked=rig.store.conn.execute('SELECT * FROM fills WHERE exec_id=?',(fill.exec_id,)).fetchone()
    assert booked and booked['intent_id']==ref and booked['position_id']==pid and booked['quantity']==1
    assert rig.store.position(pid)['qty_open']==1 and rig.store.position(pid)['status']=='OPEN'
    assert not _audits(rig,'PROTECT_CANCEL_PROVEN_COMPLETED')
    assert _audits(rig,'RELEASE_REFUSED')
    assert not any(rig.store.conn.execute('SELECT 1 FROM fills WHERE exec_id=?',(e.exec_id,)).fetchone() for e in external)


def test_B32A_identity_must_not_change_during_completed_query(offline_oct29,monkeypatch):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    query=rig.broker.completed_orders
    def rebinding():
        result=query()
        rig.store.conn.execute('UPDATE protections SET perm_id=? WHERE position_id=?',(753795446,pid))
        return result
    monkeypatch.setattr(rig.broker,'completed_orders',rebinding)
    _request_release(rig)
    assert _audits(rig,'RELEASE_REFUSED')[-1]['reason'].find('binding_changed')>=0
    assert not _audits(rig,'PROTECT_CANCEL_PROVEN_COMPLETED')
    assert rig.store.position(pid)['status']=='OPEN'


def test_B32A_optional_native_client_and_order_ID_cannot_contradict_prior_binding(offline_oct29,monkeypatch):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    from v6lite.release_evidence import completed_cancel_proof,CompletedEvidenceError
    snapshot=rig.broker.completed_orders();[native]=snapshot.orders
    # Current legacy decoder returns both as absent. Test the explicit optional
    # populated-field branch without pretending these are legacy wire fields.
    for field,changes,code in [
        ('client',dict(client_id=999,native_client_id_absent=False),'client_id'),
        ('order',dict(broker_order_id=2891,native_order_id_absent=False),'order_id')]:
        wrong=replace(native,**changes)
        with pytest.raises(CompletedEvidenceError,match=code):
            completed_cancel_proof(replace(snapshot,orders=(wrong,)),rig.store.protection(pid),rig.store.intent(ref),rig.store.position(pid),0,rig.clock.now())
    good=replace(native,client_id=rig.cfg.client_id,native_client_id_absent=False)
    proof=completed_cancel_proof(replace(snapshot,orders=(good,)),rig.store.protection(pid),rig.store.intent(ref),rig.store.position(pid),0,rig.clock.now())
    assert proof.order.client_id==rig.cfg.client_id


@pytest.mark.parametrize('field,value,fragment',[
    ('o.auxPrice','344.570000000000000001','数量与 auxPrice'),
    ('o.totalQuantity','2.000000000000000001','数量与 auxPrice'),
    ('o.filledQuantity','0.00000000000000000001','filledQuantity'),
])
def test_B32A_pre_parse_native_precision_is_not_rounded_into_equality(offline_oct29,field,value,fragment):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    responses[0][data['field_names'].index(field)]=value
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,fragment)


def test_B32A_unknown_native_tail_cannot_be_hidden_by_decoder_in_duplicate(offline_oct29):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    responses.append(list(responses[0])+['unrecognized-native-tail'])
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'内容不一致')


def test_B32A_malformed_broker_result_is_a_specific_refusal(offline_oct29,monkeypatch):
    rig,pid,wire,ref,external,responses,data=offline_oct29
    monkeypatch.setattr(rig.broker,'completed_orders',lambda:None)
    before=_economics(rig);_request_release(rig)
    _refused_without_proof(rig,pid,before,'未返回完整原生快照')

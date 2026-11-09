"""Capture B25 real Engine/Alerter cards using isolated FakeBroker state only."""
from pathlib import Path
import asyncio, json, os, socket, sys, tempfile
from unittest.mock import patch
from run_batch25_checks import ROOT, EV, guard, package_sha, sha

def forbidden(*args, **kwargs):
    raise AssertionError('Batch25 render evidence must never open a network connection')
async def forbidden_async(*args, **kwargs):
    forbidden(*args, **kwargs)

def capture(name, channel, expected):
    cards=[c for c in channel.accepted if expected in c['title']]
    assert len(cards)==1, (name, 'expected exactly one real card')
    titles={c['title'] for c in cards}
    return {'scenario':name,'cards':cards,'plain_messages':[{'title':t,'body':b} for t,b in channel.messages if t in titles]}

def stop_sample(tmp, mp):
    from v6lite.tests.helpers import Rig, et
    from v6lite.config import ProtectionConfig
    from v6lite.broker.base import BrokerError
    from v6lite.models import ConnectionEvent
    from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
    from v6lite.tests.test_batch23_contracts import _writes
    rig=Rig(tmp, protection=ProtectionConfig(enabled=True));rig.start();rig.at(et(10,1))
    try:
        channel=_card_channel(rig);attempts=[]
        base='SDK synchronization incomplete; registered End missing: openOrders'
        def fail():
            attempts.append(rig.clock.now())
            n=len(attempts)
            raise BrokerError(base+f' [generation={n} PID={9000+n} reqId={700+n} elapsed={n+.125:.3f}s attempt={n}]')
        mp.setattr(rig.broker,'connect',fail)
        rig.broker.connected=False
        rig.engine._on_connection(rig.clock.now(),ConnectionEvent(False,'synthetic socket reset'))
        channel.accepted.clear();channel.messages.clear()
        before=_writes(rig)
        for seconds in (0,30,60,300,300):
            rig.advance(seconds=seconds);rig.engine._maintain_connection(rig.clock.now())
        assert len(attempts)==2 and _writes(rig)==before
        assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
        result=capture('同一原因两次重连后停止；动态编号不重置预算；300秒后不自动解锁',channel,'连接反复重建，已停止自动重连')
        result['automatic_connect_attempts']=len(attempts)
        return result
    finally: rig.store.close()

def query_sample(tmp):
    from v6lite.tests.test_batch23_contracts import _working, _writes
    from v6lite.tests.test_batch22_item9_broker_cancel import _card_channel
    rig,pid=_working(tmp)
    try:
        channel=_card_channel(rig);before=_writes(rig);rig.broker.fail_open_orders=True
        rig.advance(seconds=rig.cfg.protection.steady_check_seconds+1)
        for seconds in (0,.5,.5,2,4,8):
            rig.advance(seconds=seconds);rig.engine._protection_progress(rig.clock.now())
        assert _writes(rig)==before
        result=capture('同步完成后真正核验失败，重复查询只发一张P1',channel,'券商查询已限速')
        result['position_id']=pid
        return result
    finally: rig.store.close()

def quiet_A_sample(tmp,mp):
    from v6lite.tests.test_batch24_stp_echo import _pending_rig
    from v6lite.tests.test_batch23_contracts import _writes
    rig,pid,_=_pending_rig(tmp,mp)
    try:
        before=_writes(rig)
        for seconds in (0,1,3.999,.001):
            rig.advance(seconds=seconds);rig.engine.process_protection_priority()
        pages=[(t,b) for t,b in rig.channel.messages if '券商查询已限速' in t]
        assert not pages and _writes(rig)==before
        assert rig.store.protection(pid)['gap_a_started_at'] is not None
        assert not rig.engine.status_snapshot(rig.clock.now())['entries_allowed']
        return {'scenario':'首次A空窗0至5秒等待确认，不发查询限速P1，仍阻断入场','query_limit_cards':0,'entry_blocked':True,'gap_a_preserved':True}
    finally: rig.store.close()

def main():
    guard();assert not sys.flags.optimize
    for key in tuple(os.environ):
        if key.upper().startswith(('V6LITE_','IBKR_','TRADING_')) or any(word in key.upper() for word in ('TOKEN','WEBHOOK','PASSWORD','SECRET','API_KEY')):
            os.environ.pop(key,None)
    sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
    # Create only the local asyncio socketpair before denying external connects.
    policy=asyncio.get_event_loop_policy()
    try: loop=policy.get_event_loop()
    except RuntimeError: loop=asyncio.new_event_loop();policy.set_event_loop(loop)
    if loop.is_closed(): policy.set_event_loop(asyncio.new_event_loop())
    from ib_insync import IB
    import pytest
    before=package_sha(ROOT/'v6lite')
    base=EV/'test_tmp';base.mkdir(exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='render_',dir=base))
    assert tmp.resolve().is_relative_to(base.resolve())
    with patch.object(socket.socket,'connect',forbidden),patch.object(socket.socket,'connect_ex',forbidden),patch.object(socket,'create_connection',forbidden),patch.object(IB,'connect',forbidden),patch.object(IB,'connectAsync',forbidden_async):
        with pytest.MonkeyPatch.context() as mp: stop=stop_sample(tmp/'stop',mp)
        query=query_sample(tmp/'query')
        with pytest.MonkeyPatch.context() as mp: quiet=quiet_A_sample(tmp/'A',mp)
    assert package_sha(ROOT/'v6lite')==before
    record={'source_code_sha':before,'source_unchanged':True,'network_connections_allowed':False,'render_origin':'real Engine -> Alerter -> AlertCard.as_embed/as_plain_text -> local memory channel','samples':[stop,query],'quiet_scenarios':[quiet]}
    (EV/'render_samples.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    lines=['# Batch 25 actual Alerter card samples','','Source SHA: '+before,'']
    for sample in record['samples']:
        lines+=['## '+sample['scenario'],'']
        for message in sample['plain_messages']:lines+=[message['body'],'']
    (EV/'render_samples.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    guard()
    print(json.dumps({'code_sha':before,'rendered_cards':2,'quiet_A_cards':0,'render_sha256':sha(EV/'render_samples.json')},ensure_ascii=False))

if __name__=='__main__':main()
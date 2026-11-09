"""Render real Engine/FakeBroker cards through MemoryChannel, never a webhook."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json,sys
import pytest
from v6lite.alerts import Alerter
from v6lite.tests import test_batch26_overnight_evidence as spec
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent

def main():
    assert len(sys.argv) in (2,3)
    stem=sys.argv[2] if len(sys.argv)==3 else "render_samples"
    assert stem and all(c.isalnum() or c=="_" for c in stem)
    assert not (EV/(stem+".json")).exists() and not (EV/(stem+".md")).exists()
    temp=Path(sys.argv[1]).resolve();assert temp.is_relative_to(ROOT.resolve()) and not temp.exists()
    temp.mkdir(parents=True)
    all_cases=[]
    cases=['restore_INFO','freeze_policy','manual_sell_one','cancelled_gtc','missing_entry_exec','unknown_today_exec','position_mismatch']
    for name in cases:
        case_dir=temp/name;case_dir.mkdir()
        mp=pytest.MonkeyPatch();spec._no_network.__wrapped__(mp)
        rendered=[];original=Alerter.send
        def capture(self,now,level,subject,body='',**kwargs):
            result=original(self,now,level,subject,body,**kwargs)
            card=kwargs.get('card');plain=card.as_plain_text() if card else ''
            if '过夜' in (subject+body+plain):
                rendered.append({'level':level,'subject':subject,'body':body,'plain_text':plain,
                    'embed':card.as_embed() if card else None,'queued_delivery_id':result,
                    'memory_delivery':self.delivery(result) if result else None})
            return result
        mp.setattr(Alerter,'send',capture)
        fixture=spec.seed_overnight.__wrapped__(case_dir,mp);seed=next(fixture)
        try:
            if name=='restore_INFO':
                rig,pid=seed();spec._restart(rig,case_dir,mp);spec._activate(rig,pid)
                assert len(spec._restore_infos(rig))==1
            elif name=='freeze_policy':
                rig,pid=seed(enabled=False);spec._restart(rig,case_dir,mp)
                rig.px('AAPL',float(rig.store.position(pid)['tp1'])+.20)
                rig.engine._protection_progress(rig.clock.now())
                rig.engine._evaluate_exits(rig.clock.now());rig.engine._run_queue();rig.engine._drain(rig.clock.now())
                assert not spec._tp_requests(rig)
            else:
                spec.test_B26_each_required_overnight_evidence_failure_freezes_TP_with_specific_P1_and_no_auto_repair(seed,case_dir,mp,name)
            required='INFO' if name=='restore_INFO' else 'P1'
            assert any(item['level']==required and item['plain_text'] for item in rendered),(name,rendered)
            for item in rendered:
                if item['level'] in {'INFO','P1'}:
                    assert all(word in item['plain_text'] for word in ('运行约束','A','B','John','停机','撤单','平仓','release'))
            all_cases.append({'case':name,'engine_generated':True,'cards':rendered})
        finally:
            try:next(fixture)
            except StopIteration:pass
            mp.undo()
    record={'generated_at_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),
        'synthetic':True,'source':'Real Engine paths, SQLite reopen, Alerter.send, AlertCard.as_embed/as_plain_text, MemoryChannel',
        'phone_delivery_performed':False,'network_and_IB_connect_forbidden':True,'cases':all_cases}
    text='# 实际FakeBroker卡片样例（合成账户/持仓；仅MemoryChannel）\n\n'
    text+='这些文本来自真实引擎触发及渲染，不是手工拼出的预期，也未发到手机。\n\n'
    for case in all_cases:
        text+='## '+case['case']+'\n\n'
        for card in case['cards']:
            text+='```text\n'+card['plain_text']+'\n```\n\n'
    (EV/(stem+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    (EV/(stem+'.md')).write_text(text,encoding='utf-8',newline='\n')
    print(json.dumps({'actual_cases':len(all_cases),'cards':sum(len(c['cards']) for c in all_cases),'phone_delivery':False}))

if __name__=='__main__':main()

"""Run only synthetic render fixtures in a sanitized dev child process."""
from pathlib import Path
from datetime import datetime
import json,os,sys
from run_batch26_checks import ROOT,EV,PT,guard,sha,package_sha
from acceptance_process import run_guarded


def main():
    counts=guard();code=package_sha(ROOT/'v6lite');index=1
    label='render_actual'
    while (EV/(label+'.json')).exists() or (EV/'test_tmp'/label).exists():
        index+=1;label='render_actual_'+str(index)
    temp=EV/'test_tmp'/label;temp.mkdir(parents=True)
    profile=temp/'isolated_profile'
    (profile/'AppData/Roaming').mkdir(parents=True);(profile/'AppData/Local').mkdir(parents=True)
    env=dict(os.environ)
    for key in list(env):
        if key.upper().startswith(('V6LITE_','PYTEST_','PYTHON','IBKR_','TRADING_')) or any(w in key.upper() for w in ('TOKEN','WEBHOOK','PASSWORD','SECRET','API_KEY')):
            env.pop(key,None)
    env.update(USERPROFILE=str(profile),HOME=str(profile),APPDATA=str(profile/'AppData/Roaming'),LOCALAPPDATA=str(profile/'AppData/Local'),TEMP=str(temp),TMP=str(temp),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',PYTHONPATH=str(ROOT),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    script=EV/'render_samples.py';stem=label+'_samples'
    before={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts'),'renderer':sha(script)}
    program="import sys;assert sys.stdin.read(1)=='G';assert not sys.flags.optimize\nimport runpy\nsys.argv="+repr([str(script),str(temp/'data'),stem])+"\nrunpy.run_path("+repr(str(script))+",run_name='__main__')\n"
    start=datetime.now(PT).isoformat()
    result=run_guarded([sys.executable,'-u','-B','-c',program],cwd=ROOT,env=env,timeout=900)
    out=EV/(label+'.txt');out.write_text(result.stdout,encoding='utf-8',newline='\n')
    after={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts'),'renderer':sha(script)}
    assert before==after
    record={'label':label,'code_sha':code,'start_pt':start,'end_pt':datetime.now(PT).isoformat(),'returncode':result.returncode,'source_unchanged':before==after,'source_sha256':before,'process_counts':counts,'stdout_sha256':sha(out),'network':'Forbidden by real fixture; MemoryChannel only','outputs':{p.name:sha(p) for p in (EV/(stem+'.json'),EV/(stem+'.md')) if p.exists()}}
    (EV/(label+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    guard();print(json.dumps(record,ensure_ascii=False));assert result.returncode==0
    selection={'code_sha':code,'receipt_path':label+'.json','json_path':stem+'.json','markdown_path':stem+'.md','outputs':record['outputs']}
    (EV/'final_render_selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

if __name__=='__main__':main()

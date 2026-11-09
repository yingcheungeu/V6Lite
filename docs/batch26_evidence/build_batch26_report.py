"""Build the B26 report/manifest from immutable offline receipts; never stage or commit."""
from pathlib import Path
import argparse, hashlib, json, os
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from run_batch26_checks import ROOT, EV, guard, package_sha, sha



def checked_path(base, relative):
    assert not Path(relative).is_absolute() and '..' not in Path(relative).parts
    p=base/relative
    assert p.resolve().is_relative_to(base.resolve()) and not p.is_symlink()
    return p


def time_window(r):
    from zoneinfo import ZoneInfo
    start=datetime.fromisoformat(r['start_pt']).astimezone(ZoneInfo('America/Los_Angeles'))
    end=datetime.fromisoformat(r['end_pt']).astimezone(ZoneInfo('America/Los_Angeles'))
    assert start<=end
    if not r['full']: return
    day=start.replace(hour=0,minute=0,second=0,microsecond=0)
    while day.date()<=end.date():
        if day.weekday()<5:
            block_start=day.replace(hour=6); block_end=day.replace(hour=13,minute=30)
            assert end<=block_start or start>=block_end, 'full test overlaps trading block'
        day+=timedelta(days=1)

def latest(prefix):
    candidates=[]
    for p in EV.glob(prefix+'*.json'):
        try: r=json.loads(p.read_text(encoding='utf-8'))
        except (ValueError, OSError): continue
        if 'totals' in r and 'end_pt' in r and r.get('label','').startswith(prefix):
            candidates.append((datetime.fromisoformat(r['end_pt']),r,p))
    assert candidates, prefix
    return max(candidates,key=lambda item:item[0])[1:]


def raw_receipt(r):
    label=r['label']
    assert label and all(c.isalnum() or c=='_' for c in label), 'unsafe receipt label'
    for suffix,key in (('.txt','stdout_sha256'),('_inventory.json','inventory_sha256')):
        assert sha(EV/(label+suffix))==r[key], 'raw receipt hash mismatch: '+label+suffix
    observed=json.loads((EV/(label+'_inventory.json')).read_text(encoding='utf-8'))
    assert observed==r['inventory'], 'inventory receipt mismatch'
    if r['collect_only']:
        assert r['junit_sha256'] is None and observed['collected'] and not observed['executed']
        return
    xml=EV/(label+'.xml')
    assert sha(xml)==r['junit_sha256'], 'JUnit raw hash mismatch'
    tree=ET.parse(xml).getroot()
    totals={name:sum(int(x.attrib.get(name,0)) for x in tree.iter('testsuite')) for name in ('tests','failures','errors','skipped')}
    cases=[]
    for case in tree.iter('testcase'):
        f=case.find('failure'); e=case.find('error'); skipped=case.find('skipped')
        item=f if f is not None else e if e is not None else skipped
        cases.append({'classname':case.attrib.get('classname'),'name':case.attrib.get('name'),
                      'outcome':'failure' if f is not None else 'error' if e is not None else 'skipped' if skipped is not None else 'passed',
                      'message':item.attrib.get('message','') if item is not None else ''})
    assert totals==r['totals'] and cases==r['cases'], 'actual JUnit disagrees with receipt'
    assert len(cases)==totals['tests']>0
    assert sorted(observed['collected'])==sorted(observed['executed']) and len(observed['executed'])==totals['tests']


def valid(r, code):
    raw_receipt(r)
    assert r['source_code_sha']==code and r['worktree_code_sha']==code and not r['baseline'] and not r['collect_only']
    assert r['platform'].startswith('Windows-') and Path(r['python_executable'])==ROOT/'.venv/Scripts/python.exe'
    assert r['returncode']==0 and not r['timed_out'] and r['totals']['failures']==r['totals']['errors']==0
    assert r['v6lite_runtime_processes']==r['uninspectable_python_processes']==0
    assert r['source_unchanged'] and r['worktree_unchanged'] and r['specs_unchanged'] and r['source_trees_unchanged']
    assert r['source_trees']=={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts')}
    assert all(Path(item['path']).resolve().is_relative_to(ROOT.resolve()) and sha(Path(item['path']))==item['sha256'] for item in r['specs'])
    time_window(r)
    return r['totals']['tests']-r['totals']['skipped'],r['totals']['skipped']


def allowed_manifest_path(p):
    assert p.resolve().is_relative_to(ROOT.resolve()) and not p.is_symlink()
    relative=p.relative_to(ROOT).as_posix()
    assert not any(part in {'__pycache__','.pytest_cache','test_tmp','baseline22a'} for part in p.relative_to(ROOT).parts)
    assert p.suffix.lower() not in {'.db','.sqlite','.sqlite3','.wal','.shm','.pyc','.zip'}, relative
    assert not p.name.lower().endswith(('-wal','-shm')) and p.name not in {'.env','V6_LITE_OPERATOR_MANUAL.md'}, relative
    assert not(p.parent==EV and p.suffix.lower()=='.toml'), relative
    return relative


def red_valid(record):
    raw_receipt(record);time_window(record)
    assert record['returncode']==1 and record['totals']['failures']>0 and record['totals']['errors']==0
    assert not record['full'] and not record['collect_only'] and not record['timed_out']
    assert record['source_unchanged'] and record['worktree_unchanged'] and record['specs_unchanged'] and record['source_trees_unchanged']
    assert record['v6lite_runtime_processes']==record['uninspectable_python_processes']==0

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true',required=True);args=parser.parse_args()
    guard();code=package_sha(ROOT/'v6lite')
    cp=json.loads((EV/'acceptance_checkpoint.json').read_text(encoding='utf-8'))
    checkpoint_digest=sha(EV/'acceptance_checkpoint.json')
    assert cp['code_sha']==code and cp['status']=='ready for serial full3'
    assert all(sha(checked_path(ROOT,p))==digest for p,digest in cp['locked_files'].items())
    review=json.loads((EV/'independent_candidate_review.json').read_text(encoding='utf-8'))
    assert review['code_sha']==code and not review['unresolved_substantive_findings']
    from run_batch26_checks import BASE
    baseline_tests={p.relative_to(BASE).as_posix():sha(p) for p in (BASE/'v6lite/tests').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    assert all(sha(ROOT/p)==h for p,h in baseline_tests.items()),'legacy test bytes changed'
    assert sha(ROOT/'scripts')==sha(BASE/'scripts'),'scripts changed outside task'
    reds=[]
    for name in cp['valid_red_receipts']:
        p=checked_path(EV,name);r=json.loads(p.read_text(encoding='utf-8'));red_valid(r)
        assert r['baseline'] and r['source_code_sha']=='ea71abe782e4cf68'
        reds.append((r,p))
    candidate_reds=[]
    for name in cp.get('candidate_red_receipts',[]):
        p=checked_path(EV,name);r=json.loads(p.read_text(encoding='utf-8'));red_valid(r)
        assert not r['baseline'];candidate_reds.append((r,p))
    greens=[]
    for name in cp['green_receipts']:
        p=checked_path(EV,name);r=json.loads(p.read_text(encoding='utf-8'));valid(r,code);greens.append((r,p))
    collection,collection_path=latest('full_collection');raw_receipt(collection);time_window(collection)
    assert collection['candidate_checkpoint_sha256']==checkpoint_digest
    assert collection['full'] and collection['collect_only'] and collection['returncode']==0
    assert collection['source_code_sha']==code and collection['source_trees']=={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts')}
    assert collection['source_unchanged'] and collection['worktree_unchanged'] and collection['source_trees_unchanged'] and collection['specs_unchanged']
    expected=collection['inventory']['collected'];assert len(expected)==len(set(expected))==cp['expected_tests']
    import subprocess
    prior_raw=subprocess.check_output(['git','show','v6lite-batch25:docs/batch25_evidence/full_1_inventory.json'],cwd=ROOT)
    prior=json.loads(prior_raw);assert len(prior['collected'])==1609 and set(prior['collected']).issubset(expected)
    assert len(set(expected)-set(prior['collected']))==cp['new_cases']
    full=[]
    for n in range(1,4):
        r,p=latest('full_'+str(n));valid(r,code)
        assert r['candidate_checkpoint_sha256']==checkpoint_digest
        assert r['full'] and not r['collect_only'] and r['inventory']==collection['inventory']|{'executed':expected}
        assert r['totals']=={'tests':cp['expected_tests'],'failures':0,'errors':0,'skipped':1}
        full.append((r,p))
    assert all(full[n-1][0]['end_pt']<=full[n][0]['start_pt'] for n in (1,2))
    assert all(r['cases']==full[0][0]['cases'] and r['source_trees']==full[0][0]['source_trees'] and r['specs']==full[0][0]['specs'] for r,p in full)
    skip=[c for c in full[0][0]['cases'] if c['outcome']=='skipped']
    assert len(skip)==1 and skip[0]['name']=='test_scoring_matches_legacy_scanner_on_identical_bars' and skip[0]['message']=='legacy market_scanner.py not present'
    render=json.loads((EV/'final_render_selection.json').read_text(encoding='utf-8'))
    assert render['code_sha']==code
    rr=json.loads(checked_path(EV,render['receipt_path']).read_text(encoding='utf-8'))
    assert rr['returncode']==0 and rr['source_unchanged'] and rr['code_sha']==code and rr['outputs']==render['outputs']
    assert rr['source_sha256']=={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts'),'renderer':sha(EV/'render_samples.py')}
    assert sha(EV/(rr['label']+'.txt'))==rr['stdout_sha256']
    assert all(sha(checked_path(EV,p))==h for p,h in render['outputs'].items())
    paths=[]
    for folder in ('v6lite','scripts'):
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts)
    paths.extend(ROOT/p for p in ('docs/V6_LITE_BATCH26_INSTRUCTIONS.md','docs/V6_LITE_BATCH26_INVESTIGATION_2026-10-14.md','docs/V6_LITE_BATCH26_EVIDENCE_RULING_2026-10-14.md','docs/V6_LITE_BATCH26_RULING_ADDENDUM_2026-10-15.md'))
    paths.extend(p for p in EV.iterdir() if p.is_file() and p.name!='sha256_manifest.json' and not p.name.endswith('.staged'))
    paths=sorted(set(paths),key=lambda p:p.relative_to(ROOT).as_posix())
    manifest={allowed_manifest_path(p):sha(p) for p in paths}
    manifest_text=json.dumps({'code_sha':code,'status':'full accepted; pending Claude review','sha256':manifest},ensure_ascii=False,indent=2)+'\n'
    manifest_digest=hashlib.sha256(manifest_text.encode()).hexdigest()
    text='# V6-Lite 第二十六批修复报告（2026-10-15）\n\n'
    text+='状态：Windows默认全量连续3次通过；dev交付后冻结，等待Claude复核。未授权合并main。\n\n'
    text+='最终code_sha=`'+code+'`。算法：按v6lite相对POSIX字符串大小写敏感排序，依次路径UTF-8、NUL、原始字节、NUL，SHA-256前16位；排除__pycache__，包含测试/样例。\n\n'
    text+=(EV/'report_body.md').read_text(encoding='utf-8')+'\n'
    text+='\n## 串行红绿与Windows全量收据\n\n|收据|源码code_sha|通过/失败/错误/跳过|PT开始 → 结束|\n|---|---|---|---|\n'
    for r,p in [*reds,*candidate_reds,*greens,*full]:
        t=r['totals'];passed=t['tests']-t['failures']-t['errors']-t['skipped']
        text+=f"|{p.name}|{r['source_code_sha']}|{passed}/{t['failures']}/{t['errors']}/{t['skipped']}|{r['start_pt']} → {r['end_pt']}|\n"
    text+=f'\nB25完整集合1609项全部保留；新增{cp["new_cases"]}项，最终{cp["expected_tests"]}项。三次collection/executed nodeid、JUnit数量/结果、源码/测试哈希及唯一旧skip一致；所有全量运行均在交易时段外。Windows JobObject只保护本次验收子进程树，下一交易时段前5秒截止。\n'
    text+='\n## 实际FakeBroker卡片样例\n\n'+checked_path(EV,render['markdown_path']).read_text(encoding='utf-8')+'\n'
    text+='\n## SHA-256 清单\n\n'+str(len(manifest))+'项；机器清单docs/batch26_evidence/sha256_manifest.json SHA-256：`'+manifest_digest+'`。报告与manifest自身不进清单，避免循环；报告SHA另行交付。\n\n|文件|SHA-256|\n|---|---|\n'
    for p,h in manifest.items():text+='|'+p+'|`'+h+'`|\n'
    guard();assert package_sha(ROOT/'v6lite')==code
    assert all(sha(ROOT/p)==h for p,h in cp['locked_files'].items()) and all(sha(ROOT/p)==h for p,h in manifest.items())
    report=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND26_2026-10-15.md';mp=EV/'sha256_manifest.json'
    rt=report.with_suffix('.md.staged');mt=mp.with_suffix('.json.staged');assert not rt.exists() and not mt.exists()
    rt.write_text(text,encoding='utf-8',newline='\n');mt.write_text(manifest_text,encoding='utf-8',newline='\n')
    os.replace(rt,report);os.replace(mt,mp)
    print(json.dumps({'code_sha':code,'manifest_items':len(manifest),'report_sha256':sha(report),'manifest_sha256':sha(mp)},ensure_ascii=False))

if __name__=='__main__':main()

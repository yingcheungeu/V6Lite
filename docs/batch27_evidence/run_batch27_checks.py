"""B27 sections1-3 offline serial acceptance. Native doubles and FakeBroker only."""
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import argparse, hashlib, json, os, platform, re, subprocess, sys
import xml.etree.ElementTree as ET
from acceptance_process import run_guarded
from legacy_query_contract import expected_hashes
if sys.flags.optimize:
    raise RuntimeError('Acceptance guards require Python optimization disabled')
ROOT=Path(__file__).resolve().parents[2]
EV=Path(__file__).resolve().parent
BASES={'b26':ROOT/'.pytest_cache'/'batch27_baseline_26','b27b':ROOT/'.pytest_cache'/'batch27_baseline_27b'}
BASE_COMMIT='54a893ba655c8e04ef64cfcbb6d49a2ebcdcddaa'
PT=ZoneInfo('America/Los_Angeles')
PRESERVED={'docs/V6_LITE_BATCH27_INSTRUCTIONS.md':'3cfb4765819725d083268218c0e94db523e037ca9cc3147161af95a8a2a5875b','docs/V6_LITE_OPERATOR_MANUAL.md':'768e4df71b8f7f996aa3fb81aa27ef8845f11502fcfc1429cf131118bf64de62','docs/batch27_evidence/g3t_20261015_volume.md':'26b1edc06f75bc2186d86615d2f57f94ea5ed6eb9f1313de7882fd505dd23bf6'}


def sha(path):
    if path.is_file(): return hashlib.sha256(path.read_bytes()).hexdigest()
    digest=hashlib.sha256()
    for item in sorted((p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(path).parts),key=lambda p:p.relative_to(path).as_posix()):
        digest.update(item.relative_to(path).as_posix().encode());digest.update(b'\0');digest.update(item.read_bytes());digest.update(b'\0')
    return digest.hexdigest()


def package_sha(path): return sha(path)[:16]


def guard():
    assert ROOT==Path('C:/Dev/V6Lite-dev')
    assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='dev'
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==BASE_COMMIT,'unexpected HEAD'
    assert subprocess.check_output(['git','rev-parse','v6lite-batch27b^{}'],cwd=ROOT,text=True).strip()==BASE_COMMIT
    assert not subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT,text=True).strip(),'staging changed'
    assert all(sha(ROOT/p)==h for p,h in PRESERVED.items()),'preserved input/manual bytes changed'
    baseline=expected_hashes(ROOT,json.loads((EV/'preflight.json').read_text(encoding='utf-8'))['baseline_file_hashes'])
    assert all(sha(ROOT/p)==h for p,h in baseline.items() if p.startswith(('v6lite/tests/','scripts/'))),'original tests/scripts changed'
    assert package_sha(BASES['b26']/'v6lite')=='579d13832e3f4237'
    assert package_sha(BASES['b27b']/'v6lite')=='d6dd37c12701ce95'
    command=r"$ErrorActionPreference='Stop'; $all=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|v6lite)(\.exe)?$' }); $runtime=@($all | Where-Object { $_.CommandLine -match '(?i)(?:-m\s+v6lite(?:\s|$|\.tools\.g2_probe)|v6lite[\\/]__main__|v6lite(?:\.exe)?\s+(?:run|g2[_-]probe))' }); $unknown=@($all | Where-Object { [string]::IsNullOrWhiteSpace($_.CommandLine) }); @{runtime=$runtime.Count;unknown=$unknown.Count} | ConvertTo-Json -Compress"
    counts=json.loads(subprocess.check_output(['powershell','-NoProfile','-NonInteractive','-Command',command],text=True))
    assert counts['runtime']==counts['unknown']==0,'runtime present or uninspectable Python'
    return counts


def full_timeout():
    now=datetime.now(PT)
    assert not(now.weekday()<5 and (6,0)<=(now.hour,now.minute)<(13,30)), 'full suite forbidden during trading'
    boundary=now.replace(hour=6,minute=0,second=0,microsecond=0)
    if boundary<=now: boundary+=timedelta(days=1)
    while boundary.weekday()>=5: boundary+=timedelta(days=1)
    seconds=boundary.timestamp()-now.timestamp()-5
    assert seconds>=900, 'less than 15 minutes remain before the trading block'
    return seconds


def full_candidate_guard():
    path=EV/'acceptance_checkpoint.json'
    assert path.exists(), 'full acceptance requires a locked independently reviewed candidate'
    cp=json.loads(path.read_text(encoding='utf-8'))
    assert cp['status']=='ready for serial full3' and cp['code_sha']==package_sha(ROOT/'v6lite')
    for relative,digest in cp['locked_files'].items():
        item=ROOT/relative
        assert not Path(relative).is_absolute() and '..' not in Path(relative).parts
        assert item.resolve().is_relative_to(ROOT.resolve()) and not item.is_symlink() and sha(item)==digest
    return sha(path)


def run(label,paths,baseline=False,full=False,collect=False,expected_nodes=None):
    counts=guard()
    checkpoint_hash=full_candidate_guard() if full else None
    source=BASES[baseline] if baseline else ROOT
    if full: full_timeout()
    assert re.fullmatch(r'[a-zA-Z0-9_]+',label)
    original_label=label;index=1
    while any((EV/(label+suffix)).exists() for suffix in ('.json','.txt','.xml','_inventory.json')):
        index+=1;label=original_label+'_attempt'+str(index)
    for p in paths: assert p.resolve().is_relative_to(ROOT.resolve()) and p.exists()
    before=package_sha(source/'v6lite');worktree=package_sha(ROOT/'v6lite')
    source_trees={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts')}
    specs=[{'path':str(p),'sha256':sha(p)} for p in paths]
    env=dict(os.environ)
    for key in list(env):
        if key.upper().startswith(('V6LITE_','PYTEST_','PYTHON','IBKR_','TRADING_')) or any(word in key.upper() for word in ('TOKEN','WEBHOOK','PASSWORD','SECRET','API_KEY')): env.pop(key,None)
    temp=ROOT/'.pytest_cache'/'batch27_tmp'/label
    assert temp.resolve().is_relative_to(ROOT.resolve()) and not temp.exists()
    temp.mkdir(parents=True)
    profile=temp/'isolated_profile'
    (profile/'AppData/Roaming').mkdir(parents=True)
    (profile/'AppData/Local').mkdir(parents=True)
    env.update(USERPROFILE=str(profile),HOME=str(profile),APPDATA=str(profile/'AppData/Roaming'),LOCALAPPDATA=str(profile/'AppData/Local'))
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',PYTHONPATH=str(source),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',TEMP=str(temp),TMP=str(temp))
    xml=EV/(label+'.xml');out=EV/(label+'.txt');inventory=EV/(label+'_inventory.json')
    args=['-q','--tb=short','-p','no:cacheprovider','--import-mode=importlib','--basetemp',str(temp/'pytest'),'--rootdir',str(ROOT)]
    args+=(['--collect-only'] if collect else ['--junitxml',str(xml)])
    args += [str(p) for p in paths]
    program="import sys\nassert sys.stdin.read(1) == 'G'\nimport pathlib,json,sys,v6lite,pytest\nassert not sys.flags.optimize\nassert pathlib.Path(v6lite.__file__).resolve().parent == pathlib.Path("+repr(str(source/'v6lite'))+")\n"
    capture = label.startswith('samples')
    if capture:
        program+='import sys\nsys.path.insert(0,'+repr(str(EV))+')\nfrom sample_capture import Capture\ncapture=Capture('+repr(str(EV/(label+'_cards.json')))+')\ncapture.install()\n'
    if baseline:
        program+='import v6lite.tests\nv6lite.tests.__path__.append('+repr(str(ROOT/'v6lite/tests'))+')\n'
    program+='''class Inventory:
    def __init__(self): self.collected=[]; self.executed=[]; self.failed=0
    def pytest_collection_finish(self, session):
        self.collected=[item.nodeid for item in session.items]
        self.progress()
    def progress(self):
        pathlib.Path('''+repr(str(EV/(label+'_progress.json')))+''').write_text(json.dumps({'collected':len(self.collected),'executed':len(self.executed),'failed':self.failed}),encoding='utf-8')
    def pytest_runtest_logreport(self, report):
        if report.when=='call' or (report.when=='setup' and (report.failed or report.skipped)):
            self.executed.append(report.nodeid)
            self.failed+=int(report.failed)
            if len(self.executed)%50==0: self.progress()
plugin=Inventory()
result=pytest.main('''+repr(args)+''',plugins=[plugin'''+(',capture' if capture else '')+'''])
'''+('capture.write()\n' if capture else '')+'''plugin.progress()

pathlib.Path('''+repr(str(inventory))+''').write_text(json.dumps({'collected':plugin.collected,'executed':plugin.executed},indent=2)+'\\n',encoding='utf-8')
raise SystemExit(result)
'''
    compile(program, '<offline pytest child>', 'exec')
    assert not full or full_candidate_guard()==checkpoint_hash, 'candidate/checkpoint changed before spawn'
    timeout=full_timeout() if full else None  # immediately before spawning
    start=datetime.now(PT).isoformat()
    timed_out=False
    try:
        result=run_guarded([sys.executable,'-u','-B','-c',program],cwd=source,env=env,timeout=timeout)
        stdout=result.stdout;returncode=result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout=exc.stdout or b''
        if isinstance(stdout,bytes): stdout=stdout.decode('utf-8','replace')
        stdout+='\nAcceptance interrupted before the next trading block.\n';returncode=124;timed_out=True
    out.write_text(stdout,encoding='utf-8',newline='\n')
    tree=ET.parse(xml).getroot() if xml.exists() else None
    totals={name:sum(int(s.attrib.get(name,0)) for s in tree.iter('testsuite')) if tree is not None else 0 for name in ('tests','failures','errors','skipped')}
    cases=[]
    if tree is not None:
        for case in tree.iter('testcase'):
            f=case.find('failure');e=case.find('error');s=case.find('skipped')
            item=f if f is not None else e if e is not None else s
            cases.append({'classname':case.attrib.get('classname'),'name':case.attrib.get('name'),'outcome':'failure' if f is not None else 'error' if e is not None else 'skipped' if s is not None else 'passed','message':item.attrib.get('message','') if item is not None else ''})
    observed=json.loads(inventory.read_text(encoding='utf-8')) if inventory.exists() else {'collected':[],'executed':[]}
    record={'candidate_checkpoint_sha256':checkpoint_hash,'label':label,'baseline':baseline,'full':full,'collect_only':collect,'platform':platform.platform(),'python_executable':sys.executable,'python_version':sys.version,'v6lite_runtime_processes':counts['runtime'],'uninspectable_python_processes':counts['unknown'],'start_pt':start,'end_pt':datetime.now(PT).isoformat(),'returncode':returncode,'timed_out':timed_out,'process_tree_guard':'Windows Job Object kill-on-close; pre-pytest stdin gate','source_code_sha':before,'source_unchanged':package_sha(source/'v6lite')==before,'worktree_code_sha':worktree,'worktree_unchanged':package_sha(ROOT/'v6lite')==worktree,'source_trees':source_trees,'source_trees_unchanged':source_trees=={'v6lite':sha(ROOT/'v6lite'),'scripts':sha(ROOT/'scripts')},'specs':specs,'specs_unchanged':specs==[{'path':str(p),'sha256':sha(p)} for p in paths],'totals':totals,'cases':cases,'inventory':observed,'stdout_sha256':sha(out),'junit_sha256':sha(xml) if xml.exists() else None,'inventory_sha256':sha(inventory) if inventory.exists() else None}
    (EV/(label+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    guard()
    assert record['source_unchanged'] and record['worktree_unchanged'] and record['specs_unchanged'] and record['source_trees_unchanged'],'concurrent edit during verification'
    print(json.dumps({k:v for k,v in record.items() if k not in ('cases','inventory')},ensure_ascii=False),flush=True)
    assert not timed_out
    if collect:
        assert returncode==0 and observed['collected'] and not observed['executed'],'invalid collection inventory'
    else:
        assert tree is not None and totals['tests']>0 and len(cases)==totals['tests'],'missing/empty/malformed JUnit'
        assert len(observed['executed'])==totals['tests'] and sorted(observed['executed'])==sorted(observed['collected']),'incomplete test execution'
        if expected_nodes is not None:
            assert sorted(observed['collected'])==sorted(expected_nodes)==sorted(observed['executed']),'full inventory changed or filtered'
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['red','green','focused','full']);parser.add_argument('--spec',action='append');parser.add_argument('--label');parser.add_argument('--baseline',choices=['b26','b27b'],default='b27b');ns=parser.parse_args()
    specs=[ROOT/path for path in ns.spec] if ns.spec else sorted((ROOT/'v6lite/tests').glob('test_batch27_*.py'))
    assert specs
    if ns.phase=='full':
        paths=[ROOT/'v6lite/tests',ROOT/'scripts/tests']
        locked=run('full_collection',paths,full=True,collect=True)
        old_nodes=json.loads((ROOT/'docs/batch27b_evidence/public/full_1.json').read_text(encoding='utf-8'))['inventory']['collected']
        assert len(old_nodes)==1848 and set(old_nodes)<=set(locked['inventory']['collected']),'baseline 27b collection lost or changed'
        checkpoint_hash=full_candidate_guard()
        runs=[]
        for n in range(1,4):
            assert full_candidate_guard()==checkpoint_hash, 'checkpoint changed between consecutive full runs'
            r=run('full_'+str(n),paths,full=True,expected_nodes=locked['inventory']['collected'])
            assert r['returncode']==0 and r['totals']['failures']==r['totals']['errors']==0
            assert r['source_trees']==locked['source_trees'] and r['specs']==locked['specs']
            runs.append(r)
        assert len({r['source_code_sha'] for r in runs})==1
        assert all(r['totals']==runs[0]['totals'] for r in runs)
        assert all(r['cases']==runs[0]['cases'] for r in runs),'case inventory differs between consecutive full runs'
        expected_skip=json.loads((ROOT/'docs/batch27b_evidence/public/full_1.json').read_text(encoding='utf-8'))['cases']
        expected_skip=[case for case in expected_skip if case['outcome']=='skipped']
        assert len(expected_skip)==1 and expected_skip[0]['name']=='test_scoring_matches_legacy_scanner_on_identical_bars'
        assert expected_skip[0]['message']=='legacy market_scanner.py not present'
        assert all([case for case in r['cases'] if case['outcome']=='skipped']==expected_skip for r in runs),'legacy skip identity/reason changed'
        (EV/'full_acceptance.json').write_text(json.dumps({'code_sha':runs[0]['source_code_sha'],'runs':[r['label'] for r in runs],'collection_label':locked['label'],'totals':runs[0]['totals'],'checkpoint_sha256':checkpoint_hash},indent=2)+'\n',encoding='utf-8',newline='\n')
    elif ns.phase=='red':
        r=run(ns.label or ('red_'+ns.baseline),specs,baseline=ns.baseline)
        assert r['source_code_sha']=={'b26':'579d13832e3f4237','b27b':'d6dd37c12701ce95'}[ns.baseline]
        assert r['totals']['failures']>0 and r['totals']['errors']==0,'red must be natural behavior failure without errors'
    else:
        if ns.phase=='focused':
            for prefix in ('test_batch27b','test_batch27a','test_batch26','test_batch25','test_batch24','test_batch23','test_batch22'):
                specs+=sorted((ROOT/'v6lite/tests').glob(prefix+'*.py'))
            specs+=[ROOT/'v6lite/tests'/name for name in ('test_engine.py','test_ibkr_adapter.py','test_batch16_f6_steady_timeout.py','test_batch16_f8_backoff.py','test_batch17_heartbeat_account.py','test_review_round5.py','test_review_round5_selfreview.py')]
            specs=list(dict.fromkeys(specs))
        r=run(ns.label or ns.phase,specs)
        assert r['returncode']==0 and r['totals']['failures']==r['totals']['errors']==0

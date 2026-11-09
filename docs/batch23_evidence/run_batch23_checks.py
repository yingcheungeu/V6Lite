"""B23 offline serial red/green/full evidence. FakeBroker and native wire doubles only."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse,hashlib,json,os,platform,subprocess,sys,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
BASELINE_COMMIT='d9f57dca5ed73f001f9c68052f5fe27b8abc933e'
PRESERVED={'docs/V6_LITE_OPERATOR_MANUAL.md':'5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf','docs/V6_LITE_BATCH22_ITEM8_RESEARCH_2026-10-09.md':'62dc4b6a3128f925fd339f9fe03ef88551620f8a5b44345eb7448328edd610b8','docs/V6_LITE_BATCH23_INSTRUCTIONS.md':'04708b69d6b3c410b487fb440f97a98790ce47bc0ad8dd3b23d471dc0592860c','docs/V6_LITE_BATCH22_ITEM9_FIX_REPORT_2026-10-09.md':'e0df7b68600349591b1a2c1fd3223ea413d7c3903e6b15b2625a9b1025a5eecf','docs/batch22_item9_evidence/sha256_manifest.json':'53c79f52d84f7c376cbc477fc91d85a4ddf5c4e532340db48fbdbaf90a3e0357','docs/V6_LITE_BATCH22A_FULL_VALIDATION_2026-10-09.md':'2cc9477f84ce0f949711a8d51b6e5a070f294a8b999cb1f35a0a9a9f902c5a38','docs/batch22a_evidence/sha256_manifest.json':'df38d46202bf5897e0541829a6e8b14a24f61d08de7f4c116af6c64fba39f0a5'}
def sha(p):
 if p.is_file():return hashlib.sha256(p.read_bytes()).hexdigest()
 h=hashlib.sha256()
 for c in sorted((p for p in p.rglob('*') if p.is_file() and '__pycache__' not in p.parts),key=lambda c:c.relative_to(p).as_posix()):
  h.update(c.relative_to(p).as_posix().encode());h.update(b'\0');h.update(c.read_bytes());h.update(b'\0')
 return h.hexdigest()
def package_sha(p):return sha(p)[:16]
def preserve():
 assert ROOT==Path('C:/Dev/V6Lite-dev')
 assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='dev'
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==BASELINE_COMMIT
 assert subprocess.check_output(['git','rev-parse','v6lite-batch22a^{}'],cwd=ROOT,text=True).strip()==BASELINE_COMMIT
 assert not subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT,text=True).strip()
 assert all(sha(ROOT/p)==h for p,h in PRESERVED.items()),'preserved bytes changed'
 assert package_sha(EV/'baseline22a/v6lite')=='f27e144de9eb406b'
def process_preflight():
 # Read process metadata locally; never print command lines or secrets.
 command=r"""$all=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|v6lite)(\.exe)?$' }); $runtime=@($all | Where-Object { $_.CommandLine -match '(?i)(?:-m\s+v6lite(?:\s|$|\.tools\.g2_probe)|v6lite[\\/]__main__|v6lite(?:\.exe)?\s+(?:run|g2[_-]probe))' }); $unknown=@($all | Where-Object { [string]::IsNullOrWhiteSpace($_.CommandLine) }); @{runtime=$runtime.Count;unknown=$unknown.Count} | ConvertTo-Json -Compress"""
 value=json.loads(subprocess.check_output(['powershell','-NoProfile','-NonInteractive','-Command',command],text=True))
 assert value['runtime']==0 and value['unknown']==0,'v6lite runtime present or Python process metadata unavailable'
 return value

def run(label,source_root,paths,full=False,only=None):
 preserve()
 process_count=process_preflight()
 if full:
  now=datetime.now(ZoneInfo('America/Los_Angeles'))
  assert not(now.weekday()<5 and (6,0)<=(now.hour,now.minute)<(13,30)),'full suite forbidden during trading window'
 base=label;index=1
 while (EV/(label+'.json')).exists() or (EV/(label+'.txt')).exists():index+=1;label=base+'_run'+str(index)
 before=package_sha(source_root/'v6lite');wt_before=package_sha(ROOT/'v6lite')
 specs_before=[{'path':str(p),'sha256':sha(p)} for p in paths]
 env=dict(os.environ)
 for k in list(env):
  if k.startswith('V6LITE_') and any(s in k for s in ('WEBHOOK','PASSWORD','TOKEN','DIAGNOSTIC')):env.pop(k,None)
 temp_root=EV/'test_tmp'/label
 assert temp_root.resolve().is_relative_to(ROOT.resolve()) and not temp_root.exists()
 temp_root.mkdir(parents=True)
 env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONPATH=str(source_root),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',TEMP=str(temp_root),TMP=str(temp_root))
 xml=EV/(label+'.xml');out=EV/(label+'.txt')
 args=['-q','--tb=short','-p','no:cacheprovider','--import-mode=importlib','--basetemp',str(temp_root/'pytest'),'--rootdir',str(source_root),'--junitxml',str(xml),*[str(p) for p in paths]]
 if only:args += ['-k',only]
 code='import pathlib,v6lite,pytest; assert pathlib.Path(v6lite.__file__).resolve().parent == pathlib.Path('+repr(str(source_root/'v6lite'))+'); raise SystemExit(pytest.main('+repr(args)+'))'
 start=datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()
 result=subprocess.run([sys.executable,'-u','-B','-c',code],cwd=source_root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8')
 out.write_text(result.stdout,encoding='utf-8',newline='\n')
 suites=list(ET.parse(xml).getroot().iter('testsuite')) if xml.exists() else []
 totals={n:sum(int(s.attrib.get(n,0)) for s in suites) for n in ('tests','failures','errors','skipped')}
 cases=[]
 if xml.exists():
  for c in ET.parse(xml).getroot().iter('testcase'):
   f=c.find('failure');e=c.find('error');s=c.find('skipped')
   item=f if f is not None else e if e is not None else s
   cases.append({'classname':c.attrib.get('classname'),'name':c.attrib.get('name'),'outcome':'failure' if f is not None else 'error' if e is not None else 'skipped' if s is not None else 'passed','message':item.attrib.get('message','') if item is not None else ''})
 record=dict(label=label,select_expression=only,v6lite_runtime_processes=process_count['runtime'],uninspectable_python_processes=process_count['unknown'],platform=platform.platform(),python_executable=sys.executable,python_version=sys.version,start_pt=start,end_pt=datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),returncode=result.returncode,source_code_sha=before,source_unchanged=package_sha(source_root/'v6lite')==before,worktree_code_sha=wt_before,worktree_unchanged=package_sha(ROOT/'v6lite')==wt_before,specs=specs_before,specs_unchanged=specs_before==[{'path':str(p),'sha256':sha(p)} for p in paths],totals=totals,cases=cases,stdout_sha256=sha(out),junit_sha256=sha(xml) if xml.exists() else None)
 (EV/(label+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 preserve();assert record['source_unchanged'] and record['worktree_unchanged'] and record['specs_unchanged'],'concurrent source write during serial validation'
 print(json.dumps({k:v for k,v in record.items() if k!='cases'},ensure_ascii=False),flush=True)
 return record
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['red','green','focused','full','review-red-candidate','review-red-baseline','review-green','boundary-red-candidate','boundary-red-baseline','boundary-green','diagnostic-red-candidate','diagnostic-red-baseline','fact-red-candidate','fact-red-baseline','recovery-red-candidate','recovery-red-baseline','legacy-capacity']);ns=parser.parse_args();preserve()
 specs=sorted((ROOT/'v6lite/tests').glob('test_batch23*.py'));assert specs
 if ns.phase=='red':
  specs=[p for p in specs if p.name in {'test_batch23_contracts.py','test_batch23_tp_evidence.py'}]
  index=1
  while (EV/('red_specs_run'+str(index))).exists():index+=1
  specdir=EV/('red_specs_run'+str(index));specdir.mkdir()
  copied=[]
  for p in specs:q=specdir/p.name;q.write_bytes(p.read_bytes());copied.append(q)
  r=run('red_22a',EV/'baseline22a',copied)
  assert r['totals']['tests']>0 and r['totals']['failures']>0 and r['totals']['errors']==0,'red must be behavioral, no collection errors'
 elif ns.phase.startswith('review-'):
  spec=ROOT/'v6lite/tests/test_batch23_error_provenance.py';assert spec.is_file()
  if ns.phase=='review-red-baseline':
   target=EV/'review_specs'/sha(spec)[:12]/spec.name
   assert not target.exists()
   target.parent.mkdir(parents=True);target.write_bytes(spec.read_bytes())
   r=run('review_red_22a',EV/'baseline22a',[target])
   assert r['totals']['tests']>0 and r['totals']['errors']==0
  elif ns.phase=='review-red-candidate':
   r=run('review_red_candidate',ROOT,[spec])
   assert r['totals']['tests']>0 and r['totals']['failures']>0 and r['totals']['errors']==0
  else:
   r=run('review_green',ROOT,[spec])
   assert r['returncode']==0 and r['totals']['failures']==r['totals']['errors']==0
 elif ns.phase.startswith('boundary-'):
  spec=ROOT/'v6lite/tests/test_batch23_unprotected.py';assert spec.is_file()
  if ns.phase=='boundary-red-baseline':
   target=EV/'boundary_specs'/sha(spec)[:12]/spec.name
   assert not target.exists()
   target.parent.mkdir(parents=True);target.write_bytes(spec.read_bytes())
   r=run('boundary_red_22a',EV/'baseline22a',[target])
   assert r['totals']['tests']==8 and r['totals']['errors']==0
  elif ns.phase=='boundary-red-candidate':
   r=run('boundary_red_candidate',ROOT,[spec])
   assert r['totals']['tests']==8 and r['totals']['failures']>0 and r['totals']['errors']==0
  else:
   r=run('boundary_green',ROOT,[spec])
   assert r['returncode']==0 and r['totals']['failures']==r['totals']['errors']==0
 elif ns.phase.startswith('diagnostic-'):
  spec=ROOT/'v6lite/tests/test_batch23_broker_diagnostics.py';assert spec.is_file()
  if ns.phase=='diagnostic-red-baseline':
   target=EV/'diagnostic_specs'/sha(spec)[:12]/spec.name
   assert not target.exists();target.parent.mkdir(parents=True);target.write_bytes(spec.read_bytes())
   r=run('diagnostic_red_22a',EV/'baseline22a',[target])
  else:r=run('diagnostic_red_candidate',ROOT,[spec])
  assert r['totals']['tests']==6 and r['totals']['failures']>0 and r['totals']['errors']==0
 elif ns.phase.startswith('fact-'):
  spec=ROOT/'v6lite/tests/test_batch23_terminal_fact.py';assert spec.is_file()
  if ns.phase=='fact-red-baseline':
   target=EV/'fact_specs'/sha(spec)[:12]/spec.name
   assert not target.exists();target.parent.mkdir(parents=True);target.write_bytes(spec.read_bytes())
   r=run('fact_red_22a',EV/'baseline22a',[target])
   assert r['totals']=={'tests':1,'failures':0,'errors':0,'skipped':0}
  else:
   r=run('fact_red_candidate',ROOT,[spec])
   assert r['totals']=={'tests':1,'failures':1,'errors':0,'skipped':0}
 elif ns.phase.startswith('recovery-'):
  spec=ROOT/'v6lite/tests/test_batch23_recovery_diagnostic.py';assert spec.is_file()
  if ns.phase=='recovery-red-baseline':
   target=EV/'recovery_specs'/sha(spec)[:12]/spec.name
   assert not target.exists();target.parent.mkdir(parents=True);target.write_bytes(spec.read_bytes())
   r=run('recovery_red_22a',EV/'baseline22a',[target])
  else:r=run('recovery_red_candidate',ROOT,[spec])
  assert r['totals']=={'tests':1,'failures':1,'errors':0,'skipped':0}
 elif ns.phase=='legacy-capacity':
  r=run('legacy_capacity',ROOT,[ROOT/'v6lite/tests/test_review_round2.py'],only='test_race_final_sell_boundary_counts_other_working_sells')
  assert r['returncode']==0 and r['totals']=={'tests':1,'failures':0,'errors':0,'skipped':0}
 elif ns.phase=='full':
  for i in range(1,4):
   r=run('full_'+str(i),ROOT,[ROOT/'v6lite/tests',ROOT/'scripts/tests'],True)
   assert r['returncode']==0 and r['totals']['failures']==r['totals']['errors']==0
 else:
  paths=specs
  if ns.phase=='focused':
   paths+=sorted(p for p in (ROOT/'v6lite/tests').glob('test_batch22*.py'))+sorted(p for p in (ROOT/'v6lite/tests').glob('test_batch17*.py'))
   paths += [ROOT/'v6lite/tests'/name for name in ('test_batch16.py','test_batch16_f8_backoff.py','test_batch20_card_provenance.py','test_batch20_protection_equivalents.py','test_review_fixes.py')]
  r=run(ns.phase,ROOT,paths);assert r['returncode']==0 and r['totals']['failures']==r['totals']['errors']==0

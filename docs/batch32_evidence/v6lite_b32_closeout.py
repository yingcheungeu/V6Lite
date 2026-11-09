"""Freeze/finalize B32 offline evidence; no broker or Git mutations."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
import ast,hashlib,json,subprocess,sys,xml.etree.ElementTree as ET
ROOT=Path('C:/Dev/V6Lite-dev');OUT=ROOT/'docs/batch32_evidence'
REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND32_2026-10-29.md'
BASE='4456af6d1e70be1daa32d5d7870f34d422373383'
ADDENDUM='9f054900e0fe9843904685696dce0017c2a68c09c8538463739e312b6cd516ce'
INSTRUCTION='b22c24d2ab99765b4332446df20b781ea300eb90b37cb7ad588575daa8ca4c64'
PT=ZoneInfo('America/Los_Angeles')
sys.path.insert(0,str(ROOT))
from v6lite.version import code_sha
from v6lite_b32_inputs import verify_inputs
OWNED=['v6lite/alerts.py', 'v6lite/broker/base.py', 'v6lite/broker/completed_orders.py', 'v6lite/broker/ibkr.py', 'v6lite/broker/open_orders.py', 'v6lite/broker/pacing.py', 'v6lite/engine.py', 'v6lite/models.py', 'v6lite/release_evidence.py', 'v6lite/store.py', 'v6lite/tests/test_batch32_open_orders.py', 'v6lite/tests/test_batch32_normalization_guards.py', 'v6lite/tests/test_batch32_release.py', 'v6lite/tests/test_batch32_completed_query.py', 'v6lite/tests/test_batch32_offline_release.py', 'v6lite/tests/test_batch32_offline_release_guards.py', 'v6lite/tests/fixtures/b32_completed_native_fields.json', 'docs/V6_LITE_BATCH32_INSTRUCTIONS.md', 'docs/V6_LITE_BATCH32_ADDENDUM_A.md', 'docs/V6_LITE_BATCH32_OPEN_TP_PROPOSAL_2026-10-29.md']
TOOLS=['v6lite_b32_validate.py','v6lite_b32_progress.py','v6lite_b32_inputs.py',
 'v6lite_b32_closeout.py','v6lite_b32_git_deliver.py','report_template.md','scope_resolution.json','addendum_A_capability.json','addendum_A_capability.md']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def inventory():
 return {p.relative_to(ROOT).as_posix():sha(p) for b in ('v6lite','scripts') for p in sorted((ROOT/b).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def package_sha():
 h=hashlib.sha256()
 for name in sorted(inventory()):
  if name.startswith('v6lite/'):
   h.update(name[7:].encode());h.update(b'\0');h.update((ROOT/name).read_bytes());h.update(b'\0')
 return h.hexdigest()
def dependencies():
 import importlib.metadata
 d=importlib.metadata.distribution('ib_insync')
 return {'version':d.version,'sources':{n:sha(Path(d.locate_file('ib_insync/'+n))) for n in ('wrapper.py','decoder.py','client.py','ib.py','order.py','util.py')}}
def check_base():
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==BASE
 assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='dev'
 assert subprocess.check_output(['git','rev-parse','v6lite-batch31^{commit}'],cwd=ROOT,text=True).strip()==BASE
 assert sha(ROOT/'docs/V6_LITE_BATCH32_INSTRUCTIONS.md')==INSTRUCTION
 assert sha(ROOT/'docs/V6_LITE_BATCH32_ADDENDUM_A.md')==ADDENDUM
 assert verify_inputs()
 baseline=json.loads((OUT/'baseline31_source.json').read_text(encoding='utf-8'))['original_inventory']
 assert all(sha(OUT/'baseline_b31'/n)==h for n,h in baseline.items()),'archived b31 original files changed'
def receipt(label,full=False):
 v=json.loads((OUT/(label+'.json')).read_text(encoding='utf-8'))
 assert v['returncode']==v['failures']==v['errors']==0 and v['source_unchanged'] and not v['incomplete'],label
 root=ET.parse(OUT/(label+'.xml')).getroot();cases=list(root.iter('testcase'))
 assert len(cases)==v['tests'] and not list(root.iter('failure')) and not list(root.iter('error'))
 assert sum(c.find('skipped') is not None for c in cases)==v['skipped']
 assert sha(OUT/(label+'.txt'))==v['delivered_log_sha256'] and sha(OUT/(label+'.xml'))==v['delivered_XML_sha256']
 if full:
  assert v['dependencies_unchanged'] and v['dependencies']==dependencies()
  assert v['args']==['v6lite/tests','scripts/tests'] and v['platform'].startswith('Windows-') and v['read_only_inputs_unchanged']
  at=datetime.fromisoformat(v['start_pt']).astimezone(PT)
  assert at>=datetime(2026,10,29,13,30,tzinfo=PT)
  assert not(at.weekday()<5 and (6,0)<=(at.hour,at.minute)<(13,30))
 return v

def report(f,runs):
 focused=receipt(f['focused_label'])
 props={p.get('name'):p.get('value') for p in ET.parse(OUT/(f['focused_label']+'.xml')).iter('property')}
 cards={k:v for k,v in props.items() if k.startswith(('b32_','b32a_')) and 'card' in k}
 write(OUT/'addendum_A_samples.json',{'source_receipt':f['focused_label'],'refusal_card':cards.get('b32a_refusal_card'),'proof':json.loads(props['offline_cancel_proof']) if 'offline_cancel_proof' in props else None})
 write(OUT/'cards_sample.json',{'source_receipt':f['focused_label'],'XML_sha256':focused['delivered_XML_sha256'],'cards':cards})
 history=[]
 for p in sorted(OUT.glob('*.json')):
  v=json.loads(p.read_text(encoding='utf-8'))
  if isinstance(v,dict) and 'label' in v and 'returncode' in v:
   history.append(f"| {v['label']} | rc={v['returncode']}, passed={v.get('tests',0)-v.get('failures',0)-v.get('errors',0)-v.get('skipped',0)}, fail={v.get('failures',0)}, error={v.get('errors',0)}, skip={v.get('skipped',0)} | {v.get('source_unchanged')} |")
 values={'STATUS':'三次Windows串行全量通过，准备dev本地提交/tag。' if runs else '候选冻结；等待13:30 PT后三次Windows串行全量，尚未提交/tag。',
  'CODE_SHA':f['code_sha'],'PACKAGE_SHA':f['package_sha256'],
  'FOCUSED':f"{focused['label']}：{focused['tests']-focused['skipped']} passed / {focused['skipped']} skipped，{focused['seconds']}秒，source_unchanged=True。",
  'SOURCE_CHECKS':f"{len(f['inventory'])}文件inventory；{f['compile_count']}个Python源码compile/UTF8 AST/无BOM；预期全量{f['expected_test_count']}例（b31 2265+本批{f['new_test_count']}）。",
  'FULL_RUNS':'\n'.join(f"| {v['label']} | {v['tests']-v['skipped']} passed / {v['skipped']} skipped | {v['seconds']} | {v['start_pt']} → {v['end_pt']} |" for v in runs) or '| pending | 尚未执行 | - | - |',
  'CARD':cards.get('b32_release_card_title','')+'\n'+cards.get('b32_release_card_body',''),
  'HISTORY':'\n'.join(history)}
 t=(OUT/'report_template.md').read_text(encoding='utf-8')
 for k,v in values.items():t=t.replace('{{'+k+'}}',v)
 assert '{{' not in t
 REPORT.write_text(t,encoding='utf-8',newline='\n')

mode=sys.argv[1];check_base()
if mode=='prepare':
 assert not (OUT/'freeze.json').exists(),'retain prior revision before refreezing'
 focused=receipt(sys.argv[2]);before=inventory();count=0
 for n in before:
  if n.endswith('.py'):
   raw=(ROOT/n).read_bytes();assert not raw.startswith(b'\xef\xbb\xbf'),n
   compile(raw,n,'exec');ast.parse(raw.decode('utf-8'),filename=n);count+=1
 baseline=json.loads((OUT/'baseline31_source.json').read_text(encoding='utf-8'))['original_inventory']
 assert not {n for n,h in before.items() if baseline.get(n)!=h and n not in OWNED},'unowned source delta'
 assert focused['inventory_sha256']==hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
 new={(c.get('classname'),c.get('name')) for c in ET.parse(OUT/(focused['label']+'.xml')).iter('testcase') if 'test_batch32_' in c.get('classname','')}
 assert {'v6lite/tests/test_batch32_open_orders.py','v6lite/tests/test_batch32_normalization_guards.py','v6lite/tests/test_batch32_release.py'}<=set(focused['args'])
 assert (OUT/'open_orders_replay_spec.py').read_bytes()==(ROOT/'v6lite/tests/test_batch32_open_orders.py').read_bytes()
 assert (OUT/'offline_release_replay_spec.py').read_bytes()==(ROOT/'v6lite/tests/test_batch32_offline_release.py').read_bytes()
 assert {'v6lite/tests/test_batch32_offline_release.py','v6lite/tests/test_batch32_offline_release_guards.py','v6lite/tests/test_batch32_completed_query.py'}<=set(focused['args'])
 f={'status':'candidate frozen; three serial full runs pending','frozen_at_pt':datetime.now(PT).isoformat(),
  'base_commit':BASE,'base_tag':'v6lite-batch31','instructions_sha256':INSTRUCTION,'addendum_sha256':ADDENDUM,
  'focused_label':focused['label'],'new_test_count':len(new),'expected_test_count':2265+len(new),
  'code_sha':code_sha(),'package_sha256':package_sha(),'inventory':before,
  'inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
  'compile_count':count,'owned_files':{n:sha(ROOT/n) for n in OWNED},'dependencies':dependencies(),
  'validation_tools':{n:sha(OUT/n) for n in TOOLS},'read_only_inputs':verify_inputs(),
  'addendum_contract':'four native identities + previously confirmed persisted orderId; seven human conditions',
  'opening_TP_approved_for_batch33_only':True,
  'opening_TP_proposal_only':True,'operator_manual_excluded':True,'original_evidence_excluded':True,
  'full_tests_not_run_before_1330':True,'collection_method':'focused XML only before 13:30; full identity verified afterward'}
 assert before==inventory();write(OUT/'freeze.json',f);report(f,[])
 print(json.dumps({k:f[k] for k in ('code_sha','package_sha256','inventory_sha256','expected_test_count','compile_count')}))
elif mode=='final':
 f=json.loads((OUT/'freeze.json').read_text(encoding='utf-8'))
 assert inventory()==f['inventory'] and code_sha()==f['code_sha'] and package_sha()==f['package_sha256']
 assert dependencies()==f['dependencies'] and verify_inputs()==f['read_only_inputs']
 assert all(sha(OUT/n)==h for n,h in f['validation_tools'].items())
 assert all(sha(ROOT/n)==h for n,h in f['owned_files'].items())
 labels=sys.argv[2:] or ['release_full_1','release_full_2','release_full_3'];assert len(labels)==len(set(labels))==3
 runs=[receipt(n,True) for n in labels]
 assert all(v['code_sha']==f['code_sha'] and v['tests']==f['expected_test_count'] and v['inventory_sha256']==f['inventory_sha256'] for v in runs)
 assert all(datetime.fromisoformat(v['start_pt'])>=datetime.fromisoformat(f['frozen_at_pt']) for v in runs)
 assert all(json.loads((OUT/(v['label']+'_inventory.json')).read_text(encoding='utf-8'))==f['inventory'] for v in runs)
 assert all(runs[i]['end_pt']<runs[i+1]['start_pt'] for i in (0,1))
 identities=[];skips=[]
 for v in runs:
  cases=list(ET.parse(OUT/(v['label']+'.xml')).iter('testcase'))
  identities.append(sorted((c.get('classname'),c.get('name')) for c in cases))
  skips.append(sorted((c.get('classname'),c.get('name'),c.find('skipped').get('message')) for c in cases if c.find('skipped') is not None))
 assert identities[0]==identities[1]==identities[2] and skips[0]==skips[1]==skips[2]
 failures=Counter();receipts=set()
 for p in OUT.glob('*.json'):
  v=json.loads(p.read_text(encoding='utf-8'))
  if not isinstance(v,dict) or 'label' not in v or 'returncode' not in v:continue
  receipts.add(v['label'])
  if v.get('args')==['v6lite/tests','scripts/tests']:
   if v.get('inventory_sha256')==f['inventory_sha256']:failures.update((c['classname'],c['name']) for c in v.get('failed_cases',[]) if c['kind'] in ('failure','error'))
   if runs[0]['start_pt']<=v.get('start_pt','')<=runs[-1]['start_pt']:assert v['returncode']==0,'intervening full failure'
 assert not any(n>=3 for n in failures.values()),'third same-candidate failure requires repair/refreeze or explicit user redefinition'
 ownership=json.loads((OUT/'artifact_ownership.json').read_text(encoding='utf-8'))
 allowed_labels=set(ownership['receipt_labels'])|set(labels)
 assert receipts<=allowed_labels,'register every preserved receipt; do not omit failures'
 report(f,runs)
 names=set(ownership['static_files'])|{n+s for n in allowed_labels for s in ('.json','.txt','.xml','_inventory.json')}
 selected=[OUT/n for n in sorted(names) if (OUT/n).is_file()]
 assert all((OUT/n).is_file() for n in ownership['static_files'])
 files=[ROOT/n for n in OWNED]+[REPORT]+selected
 assert not any('batch32_claude_evidence' in p.parts or 'baseline_b31' in p.parts for p in files)
 write(OUT/'SHA256SUMS.json',{'code_sha':f['code_sha'],'package_sha256':f['package_sha256'],'files':{p.relative_to(ROOT).as_posix():sha(p) for p in files}})
 write(OUT/'closeout.json',{'base_commit':BASE,'intended_tag':'v6lite-batch32','code_sha':f['code_sha'],'package_sha256':f['package_sha256'],
  'report_sha256':sha(REPORT),'full_labels':labels,'serial_full_runs':3,'source_unchanged':True,'all_full_after_1330_pt':True,
  'merge_push_deploy':False,'manifest_sha256':sha(OUT/'SHA256SUMS.json'),'unowned_output_files_excluded':sorted(p.name for p in OUT.iterdir() if p.is_file() and p.name not in names|{'SHA256SUMS.json','closeout.json'})})
 files += [OUT/'SHA256SUMS.json',OUT/'closeout.json']
 write(Path('C:/tmp/v6lite_b32_stage_paths.json'),[p.relative_to(ROOT).as_posix() for p in files])
 print(json.dumps({'files':len(files),'report_sha256':sha(REPORT),'code_sha':f['code_sha']}))
else:raise SystemExit('prepare <focused-label> or final [three-full-labels]')

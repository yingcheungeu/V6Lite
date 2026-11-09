"""Freeze/finalize batch31 offline evidence. Does not connect a broker or commit."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import ast,hashlib,json,subprocess,sys,xml.etree.ElementTree as ET
from collections import Counter
ROOT=Path('C:/Dev/V6Lite-dev');OUT=ROOT/'docs/batch31_evidence'
REPORT=ROOT/'docs/V6_LITE_FIX_REPORT_ROUND31_2026-10-27.md'
BASE='86cbb435db0ab501d5bcb5cc242e2e1fac881555'
INSTRUCTION='dffca92d2370d9d608bd555fca3de77ad03fa553945cbfc2a38949c0237a56cc'
PT=ZoneInfo('America/Los_Angeles')
sys.path.insert(0,str(ROOT))
from v6lite.version import code_sha
OWNED=['v6lite/broker/ibkr.py','v6lite/commissions.py','v6lite/engine.py',
 'v6lite/overnight_evidence.py','v6lite/protection_confirmations.py','v6lite/protection_engine.py',
 'v6lite/tp_evidence.py','v6lite/tests/test_batch27a_lifecycle.py',
 'v6lite/tests/test_batch28b_overnight_refresh.py','v6lite/tests/test_batch28c_price_edges.py',
 'v6lite/tests/test_batch30_commission.py','v6lite/tests/test_batch31_overnight.py',
 'v6lite/tests/test_batch31_close_card.py','v6lite/tests/test_batch31_commission_interleave.py',
 'docs/V6_LITE_BATCH31_INSTRUCTIONS.md']
TOOLS=['v6lite_b31_validate.py','v6lite_b31_progress.py','v6lite_b31_closeout.py','v6lite_b31_git_deliver.py','report_template.md']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def inventory():
 return {p.relative_to(ROOT).as_posix():sha(p) for base in ('v6lite','scripts')
  for p in sorted((ROOT/base).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def package_sha():
 h=hashlib.sha256()
 for name,digest in sorted(inventory().items()):
  if name.startswith('v6lite/'):
   h.update(name[len('v6lite/'):].encode());h.update(b'\0');h.update((ROOT/name).read_bytes());h.update(b'\0')
 return h.hexdigest()
def dependencies():
 import importlib.metadata
 dist=importlib.metadata.distribution('ib_insync')
 return {'version':dist.version,'sources':{n:sha(Path(dist.locate_file('ib_insync/'+n))) for n in ('wrapper.py','decoder.py','client.py','ib.py')}}
def check_base():
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==BASE
 assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='dev'
 assert sha(ROOT/'docs/V6_LITE_BATCH31_INSTRUCTIONS.md')==INSTRUCTION
 assert subprocess.check_output(['git','rev-parse','v6lite-batch30^{commit}'],cwd=ROOT,text=True).strip()==BASE

def receipt(label,full=False):
 v=json.loads((OUT/(label+'.json')).read_text())
 assert v['returncode']==v['failures']==v['errors']==0 and v['source_unchanged'] and not v['incomplete'],label
 xml=ET.parse(OUT/(label+'.xml')).getroot();cases=list(xml.iter('testcase'))
 assert len(cases)==v['tests'] and not list(xml.iter('failure')) and not list(xml.iter('error'))
 assert sum(c.find('skipped') is not None for c in cases)==v['skipped']
 assert sha(OUT/(label+'.txt'))==v['delivered_log_sha256']
 assert sha(OUT/(label+'.xml'))==v['delivered_XML_sha256']
 if full:
  assert v['args']==['v6lite/tests','scripts/tests'] and v['platform'].startswith('Windows-')
  at=datetime.fromisoformat(v['start_pt']).astimezone(PT)
  assert at>=datetime(2026,10,27,13,30,tzinfo=PT)
  assert not(at.weekday()<5 and (6,0)<=(at.hour,at.minute)<(13,30))
 return v

def report(freeze,runs):
 focused=receipt(freeze['focused_label'])
 properties={p.get('name'):p.get('value') for p in ET.parse(OUT/(freeze['focused_label']+'.xml')).iter('property')}
 cards={k:v for k,v in properties.items() if k.startswith('b31_') and 'card' in k}
 write(OUT/'cards_sample.json',{'source_receipt':freeze['focused_label'],'XML_sha256':focused['delivered_XML_sha256'],'cards':cards})
 history=[]
 for p in sorted(OUT.glob('*.json')):
  v=json.loads(p.read_text(encoding='utf-8'))
  if isinstance(v,dict) and 'label' in v and 'returncode' in v:
   history.append(f"| {v['label']} | rc={v['returncode']}, passed={v.get('tests',0)-v.get('failures',0)-v.get('errors',0)-v.get('skipped',0)}, fail={v.get('failures',0)}, error={v.get('errors',0)}, skip={v.get('skipped',0)} | {v.get('source_unchanged')} |")
 full='\n'.join(f"| {v['label']} | {v['tests']-v['skipped']} passed / {v['skipped']} skipped | {v['seconds']}s | {v['start_pt']} → {v['end_pt']} |" for v in runs)
 resolution_path=OUT/'scope_resolution.json'
 resolution=json.loads(resolution_path.read_text(encoding='utf-8')) if resolution_path.exists() else {}
 resolved=resolution.get('authorized_by')=='user' and resolution.get('decision')=='accept_conservative_legacy_freeze' and bool(resolution.get('quote'))
 values={'STATUS':'三次Windows串行全量通过；原指令§3.4旧冻结迁移验收仍待裁定，尚未提交/tag。' if runs else '候选源码冻结，尚未提交或打tag；等待13:30 PT后连续三次Windows全量；原指令§3.4旧冻结迁移仍待裁定。',
  'SCOPE_RESOLUTION':('用户明确裁定：'+resolution['quote']+'；来源='+resolution.get('source','direct user message')) if resolved else '未收到用户裁定；原§3.4验收缺口保持，不提交/tag。',
  'CODE_SHA':freeze['code_sha'],'PACKAGE_SHA':freeze['package_sha256'],
  'FOCUSED':f"{focused['label']}：{focused['tests']-focused['skipped']} passed / {focused['skipped']} skipped，{focused['seconds']}秒，源码不变。",
  'FULL_RUNS':full or '| pending | 尚未执行 | - | - |',
  'SOURCE_CHECKS':f"{len(freeze['inventory'])}文件inventory；{freeze['compile_count']}个Python源码compile/UTF8 AST/无BOM；预期完整测试{freeze['expected_test_count']}例（b30的2222 + 本批{freeze['new_test_count']}）。",
  'CARD':cards.get('b31_oct27_final_close_card','见cards_sample.json'),
  'HISTORY':'\n'.join(history)}
 if resolved and runs:
  values['STATUS']='三次Windows串行全量通过；用户已明确接受来源不明旧冻结保留的保守规则，准备dev本地提交/tag。'
 text=(OUT/'report_template.md').read_text(encoding='utf-8')
 for k,v in values.items():text=text.replace('{{'+k+'}}',v)
 assert '{{' not in text
 REPORT.write_text(text,encoding='utf-8',newline='\n')

mode=sys.argv[1];check_base()
if mode=='prepare':
 assert not (OUT/'freeze.json').exists(),'never overwrite an existing freeze; retain a revision first'
 focused=receipt(sys.argv[2]);before=inventory();count=0
 for name in before:
  if name.endswith('.py'):
   raw=(ROOT/name).read_bytes();assert not raw.startswith(b'\xef\xbb\xbf'),name
   compile(raw,name,'exec');ast.parse(raw.decode('utf-8'),filename=name);count+=1
 baseline=json.loads((OUT/'baseline30_source.json').read_text())['original_inventory']
 assert not {p for p,h in before.items() if baseline.get(p)!=h and p not in OWNED},'unowned source delta'
 assert focused['inventory_sha256']==hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
 new={(c.get('classname'),c.get('name')) for c in ET.parse(OUT/(focused['label']+'.xml')).iter('testcase') if 'test_batch31_' in c.get('classname','')}
 assert new and {'v6lite/tests/test_batch31_overnight.py','v6lite/tests/test_batch31_close_card.py'} <= set(focused['args'])
 freeze={'status':'candidate frozen; three serial full runs pending','frozen_at_pt':datetime.now(PT).isoformat(),
  'base_commit':BASE,'base_tag':'v6lite-batch30','instructions_sha256':INSTRUCTION,
  'acceptance_blockers':['Original instruction 3.4: unambiguous legacy b30 modification-only freeze provenance unavailable; exact upgrade integration not satisfied'],
  'focused_label':focused['label'],'expected_test_count':2222+len(new),'new_test_count':len(new),
  'code_sha':code_sha(),'package_sha256':package_sha(),'inventory':before,
  'inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
  'owned_files':{p:sha(ROOT/p) for p in OWNED},'compile_count':count,'dependencies':dependencies(),
  'validation_tools':{p:sha(OUT/p) for p in TOOLS},'operator_manual_excluded':True,
  'full_tests_not_run_before_1330':True,'collection_method':'only focused XML inspected before 13:30; full case identity checked after 13:30'}
 assert before==inventory()
 write(OUT/'freeze.json',freeze);report(freeze,[])
 print(json.dumps({k:freeze[k] for k in ('code_sha','package_sha256','expected_test_count','compile_count','inventory_sha256')}))
elif mode=='final':
 freeze=json.loads((OUT/'freeze.json').read_text());assert inventory()==freeze['inventory']
 assert code_sha()==freeze['code_sha'] and package_sha()==freeze['package_sha256']
 assert dependencies()==freeze['dependencies']
 assert all(sha(OUT/p)==h for p,h in freeze['validation_tools'].items())
 labels=sys.argv[2:] or ['final_full_1','final_full_2','final_full_3'];assert len(labels)==len(set(labels))==3
 runs=[receipt(n,True) for n in labels]
 assert all(v['code_sha']==freeze['code_sha'] and v['tests']==freeze['expected_test_count'] and v['inventory_sha256']==freeze['inventory_sha256'] for v in runs)
 assert all(datetime.fromisoformat(v['start_pt'])>=datetime.fromisoformat(freeze['frozen_at_pt']) for v in runs)
 assert all(json.loads((OUT/(v['label']+'_inventory.json')).read_text())==freeze['inventory'] for v in runs)
 assert all(runs[i]['end_pt']<runs[i+1]['start_pt'] for i in (0,1))
 identities=[sorted((c.get('classname'),c.get('name')) for c in ET.parse(OUT/(v['label']+'.xml')).iter('testcase')) for v in runs]
 assert identities[0]==identities[1]==identities[2]
 assert len({(v['tests'],v['skipped']) for v in runs})==1
 failures=Counter()
 for p in OUT.glob('*.json'):
  v=json.loads(p.read_text())
  if isinstance(v,dict) and v.get('args')==['v6lite/tests','scripts/tests'] and v.get('inventory_sha256')==freeze['inventory_sha256']:
   failures.update((c['classname'],c['name']) for c in v.get('failed_cases',[]) if c['kind'] in ('failure','error'))
  if isinstance(v,dict) and v.get('args')==['v6lite/tests','scripts/tests'] and runs[0]['start_pt']<=v.get('start_pt','')<=runs[-1]['start_pt']:
   assert v.get('returncode')==0,'cannot hide an intervening full failure'
 assert not any(n>=3 for n in failures.values()), 'third failure on unchanged candidate requires repair/refreeze or explicit operator gate redefinition'
 report(freeze,runs)
 selected=[p for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in ('SHA256SUMS.json','closeout.json')]
 files=[ROOT/p for p in OWNED]+[REPORT]+selected
 write(OUT/'SHA256SUMS.json',{'code_sha':freeze['code_sha'],'package_sha256':freeze['package_sha256'],
  'files':{p.relative_to(ROOT).as_posix():sha(p) for p in files}})
 write(OUT/'closeout.json',{'base_commit':BASE,'intended_tag':'v6lite-batch31','code_sha':freeze['code_sha'],
  'package_sha256':freeze['package_sha256'],'report_sha256':sha(REPORT),'full_labels':labels,'serial_full_runs':3,
  'source_unchanged':True,'all_full_after_1330_pt':True,'merge_push_deploy':False,
  'manifest_sha256':sha(OUT/'SHA256SUMS.json')})
 files += [OUT/'SHA256SUMS.json',OUT/'closeout.json']
 write(Path('C:/tmp/v6lite_b31_stage_paths.json'),[p.relative_to(ROOT).as_posix() for p in files])
 print(json.dumps({'files':len(files),'report_sha256':sha(REPORT),'code_sha':freeze['code_sha']}))
else:raise SystemExit('prepare <focused-label> or final [three-full-labels]')

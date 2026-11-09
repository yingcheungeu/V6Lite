"""Offline, serial B22 item9 red/green evidence; no broker or user-state IO."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse,hashlib,json,os,subprocess,sys,tempfile,zipfile,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2]
EV=Path(__file__).resolve().parent
SPEC=ROOT/'v6lite/tests/test_batch22_item9_broker_cancel.py'
PRESERVED={'docs/V6_LITE_OPERATOR_MANUAL.md':'5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf',
'docs/V6_LITE_FIX_REPORT_ROUND22_2026-10-09.md':'cbd40da3fe3dd2cc8f6e3e1b114d4f9e2e38cbd48799ac02ee3c44b6fe978240',
'docs/batch22_evidence/sha256_manifest.json':'208ef222f7a3cb1aae2c90e40a81aefb8638fa5cf4c5a465b4c3a0ebe9f55187',
'docs/V6_LITE_BATCH22_ITEM8_RESEARCH_2026-10-09.md':'62dc4b6a3128f925fd339f9fe03ef88551620f8a5b44345eb7448328edd610b8'}
def sha(p):
 if p.is_file():return hashlib.sha256(p.read_bytes()).hexdigest()
 h=hashlib.sha256()
 for child in sorted((c for c in p.rglob('*') if c.is_file() and '__pycache__' not in c.parts),key=lambda c:c.relative_to(p).as_posix()):
  h.update(child.relative_to(p).as_posix().encode());h.update(b'\0');h.update(child.read_bytes());h.update(b'\0')
 return h.hexdigest()
def package_sha(package):
 h=hashlib.sha256()
 for p in sorted((p for p in package.rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(package).parts),key=lambda p:p.relative_to(package).as_posix()):
  h.update(p.relative_to(package).as_posix().encode());h.update(b'\0');h.update(p.read_bytes());h.update(b'\0')
 return h.hexdigest()[:16]
def preserve():
 assert ROOT==Path('C:/Dev/V6Lite-dev')
 assert package_sha(ROOT/'v6lite')=='f27e144de9eb406b', 'frozen source drift'
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()=='42c77da139ce308ca133eb7e2e40beaf33ecac9d'
 assert subprocess.check_output(['git','rev-parse','v6lite-batch22'],cwd=ROOT,text=True).strip()=='18f44ab40bc934327c6cacd921e6717ff21c3a80'
 assert sha(ROOT/'docs/V6_LITE_BATCH22_ITEM9_FIX_REPORT_2026-10-09.md')=='e0df7b68600349591b1a2c1fd3223ea413d7c3903e6b15b2625a9b1025a5eecf'
 assert sha(ROOT/'docs/batch22_item9_evidence/sha256_manifest.json')=='53c79f52d84f7c376cbc477fc91d85a4ddf5c4e532340db48fbdbaf90a3e0357'
 assert sha(ROOT/'docs/V6_LITE_BATCH23_INSTRUCTIONS.md')=='04708b69d6b3c410b487fb440f97a98790ce47bc0ad8dd3b23d471dc0592860c'
 assert all(sha(ROOT/p)==h for p,h in PRESERVED.items()),'preserved doc drift'
 assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='dev'
 assert not subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT,text=True).strip(),'index not empty'
def run(label,source_root,spec,extra=()):
 preserve()
 base_label=label
 index=1
 while (EV/(label+'.json')).exists() or (EV/(label+'.txt')).exists():
  index+=1;label=base_label+'_run'+str(index)
 before=package_sha(source_root/'v6lite')
 env=dict(os.environ)
 for key in ('V6LITE_DISCORD_WEBHOOK','V6LITE_PROTECTION_DIAGNOSTIC','V6LITE_SMTP_PASSWORD'):
  env.pop(key,None)
 env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONPATH=str(source_root),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
 xml=EV/(label+'.xml');out=EV/(label+'.txt')
 args=['-q','--tb=short','-p','no:cacheprovider','--import-mode=importlib','--rootdir',str(source_root),'--junitxml',str(xml),str(spec),*extra]
 code='import pathlib,v6lite,pytest; assert pathlib.Path(v6lite.__file__).resolve().parent == pathlib.Path('+repr(str(source_root/'v6lite'))+'); raise SystemExit(pytest.main('+repr(args)+'))'
 start=datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()
 completed=subprocess.run([sys.executable,'-u','-B','-c',code],cwd=source_root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8')
 out.write_text(completed.stdout,encoding='utf-8',newline='\n')
 suites=list(ET.parse(xml).getroot().iter('testsuite')) if xml.exists() else []
 totals={name:sum(int(s.attrib.get(name,0)) for s in suites) for name in ('tests','failures','errors','skipped')}
 cases=[]
 if xml.exists():
  for c in ET.parse(xml).getroot().iter('testcase'):
   failed=c.find('failure');error=c.find('error')
   cases.append({'name':c.attrib.get('name'),'outcome':'failure' if failed is not None else 'error' if error is not None else 'skipped' if c.find('skipped') is not None else 'passed','message':(failed.attrib.get('message','') if failed is not None else error.attrib.get('message','') if error is not None else '')})
 record=dict(label=label,start_pt=start,end_pt=datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),returncode=completed.returncode,source_code_sha=before,source_unchanged=package_sha(source_root/'v6lite')==before,spec_sha256=sha(spec),totals=totals,cases=cases,stdout_sha256=sha(out),junit_sha256=sha(xml) if xml.exists() else None)
 (EV/(label+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 preserve();assert record['source_unchanged']
 print(json.dumps({k:v for k,v in record.items() if k not in ('cases',)},ensure_ascii=False),flush=True)
 return record

if __name__=='__main__':
 preserve()
 for index in range(1,4):
  now=datetime.now(ZoneInfo('America/Los_Angeles'));assert not(now.weekday()<5 and (6,0)<=(now.hour,now.minute)<(13,30)),'full suite prohibited during trading window'
  r=run('full_'+str(index),ROOT,ROOT/'v6lite/tests',[str(ROOT/'scripts/tests')]);assert r['returncode']==0 and r['totals']['errors']==0 and r['totals']['failures']==0

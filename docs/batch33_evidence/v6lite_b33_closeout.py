"""Integrity closeout after three consecutive serial Windows full suites."""
from pathlib import Path
import hashlib,json,sys,xml.etree.ElementTree as ET
R=Path('C:/Dev/V6Lite-dev');O=R/'docs/batch33_evidence';sys.path[:0]=[str(R),str(O)]
from v6lite_b33_validate import inventory,dependencies
from v6lite_b33_inputs import verify_inputs
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def check():
 f=json.loads((O/'freeze.json').read_text(encoding='utf-8'))
 assert inventory()==f['inventory'] and dependencies()==f['dependencies']
 assert verify_inputs()==f['read_only_inputs']
 assert all(sha(O/n)==h for n,h in f['validation_tools'].items())
 assert sha(R/'docs/V6_LITE_OPERATOR_MANUAL.md')==f['operator_manual_sha256']
 assert sha(R/'docs/V6_LITE_BATCH33_INSTRUCTIONS.md')==f['instructions_sha256']
 original=json.loads((O/'baseline32_source.json').read_text(encoding='utf-8'))['inventory']
 assert all(sha(O/'baseline_b32'/n)==h for n,h in original.items())
 full=[];identities=[];skips=[]
 for number in (1,2,3):
  label=f'final_full_{number}';r=json.loads((O/(label+'.json')).read_text(encoding='utf-8'))
  assert r['returncode']==r['failures']==r['errors']==0 and not r['incomplete'],label
  assert r['source_unchanged'] and r['dependencies_unchanged'] and r['read_only_inputs_unchanged'],label
  assert r['inventory_sha256']==f['inventory_sha256'] and r['code_sha']==f['code_sha']
  assert sha(O/(label+'.txt'))==r['delivered_log_sha256'] and sha(O/(label+'.xml'))==r['delivered_XML_sha256']
  cases=ET.parse(O/(label+'.xml')).getroot().findall('.//testcase')
  ids=[(c.get('classname'),c.get('name')) for c in cases];identities.append(ids)
  skips.append([(c.get('classname'),c.get('name'),c.find('skipped').get('message')) for c in cases if c.find('skipped') is not None])
  assert len(ids)==r['tests']
  if full: assert full[-1]['end_pt']<=r['start_pt']
  full.append(r)
 assert identities[0]==identities[1]==identities[2]
 assert skips[0]==skips[1]==skips[2]
 out={'code_sha':f['code_sha'],'source_unchanged':True,'dependencies_unchanged':True,'read_only_inputs_unchanged':True,'original_baseline_unchanged':True,'operator_manual_unchanged':True,'serial_full_runs':[{k:r[k] for k in ('label','tests','failures','errors','skipped','seconds','start_pt','end_pt')} for r in full],'case_identity_sha256':hashlib.sha256(json.dumps(identities[0]).encode()).hexdigest(),'skipped_cases':skips[0]}
 dump(O/'closeout.json',out);print(json.dumps(out,indent=2));return out
if __name__=='__main__':check()

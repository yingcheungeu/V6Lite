"""Exact allowlist local commit/tag, only after offline closeout succeeds."""
from pathlib import Path
import hashlib,json,subprocess,sys
R=Path('C:/Dev/V6Lite-dev');O=R/'docs/batch33_evidence';sys.path[:0]=[str(R),str(O)]
from v6lite_b33_closeout import check
PRODUCTION=['v6lite/config.py','v6lite/engine.py','v6lite/protection_engine.py','v6lite/report.py','v6lite/test_tp_timeout.py','v6lite/tp_start.py','v6lite/tests/test_batch33_open_delay.py','v6lite/tests/test_batch33_controls.py']
REPORT='docs/V6_LITE_FIX_REPORT_ROUND33_2026-10-31.md'
def git(*args,raw=False):
 b=subprocess.check_output(['git',*args],cwd=R)
 return b if raw else b.decode('utf-8').strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding='utf-8')
def main():
 check();f=json.loads((O/'freeze.json').read_text(encoding='utf-8'))
 assert git('branch','--show-current')=='dev'
 assert git('rev-parse','HEAD')==f['base_commit']
 assert not git('tag','--list','v6lite-batch33')
 assert not git('diff','--cached','--name-only'),'preexisting staged files must not be committed'
 assert (R/REPORT).is_file()
 owned=set(PRODUCTION+[REPORT,'docs/V6_LITE_BATCH33_INSTRUCTIONS.md'])
 for p in O.iterdir():
  if p.is_file() and p.suffix in {'.py','.json','.xml','.txt'} and not p.name.endswith('_progress.json') and p.name!='initial_status.txt':
   owned.add(p.relative_to(R).as_posix())
 for p in (O/'samples').rglob('*'):
  if p.is_file():owned.add(p.relative_to(R).as_posix())
 owned.update(['docs/batch33_evidence/allowlist.json','docs/batch33_evidence/SHA256SUMS.json'])
 allow=sorted(owned)
 assert all('baseline_b32/' not in n and 'test_tmp/' not in n and 'claude_evidence/' not in n and 'OPERATOR_MANUAL' not in n for n in allow)
 dump(O/'allowlist.json',{'files':allow,'exclusions':['operator manual and preexisting edits','raw Claude exports','baseline archive/extraction','test_tmp','progress state','runtime repositories/configs/state']})
 dump(O/'SHA256SUMS.json',{'algorithm':'SHA-256','files':{n:sha(R/n) for n in allow if n!='docs/batch33_evidence/SHA256SUMS.json'},'self_excluded':True})
 subprocess.run(['git','add','--',*allow],cwd=R,check=True)
 staged=git('diff','--cached','--name-only').splitlines();assert sorted(staged)==allow,(set(staged)-owned,owned-set(staged))
 subprocess.run(['git','diff','--cached','--check'],cwd=R,check=True)
 assert sha(R/'docs/V6_LITE_OPERATOR_MANUAL.md')==f['operator_manual_sha256']
 subprocess.run(['git','commit','-m','fix: defer new take-profit stages after session open (batch33)'],cwd=R,check=True)
 commit=git('rev-parse','HEAD');assert git('rev-parse','HEAD^')==f['base_commit']
 subprocess.run(['git','tag','-a','v6lite-batch33','-m','Batch 33: opening TP start barrier; three serial Windows full suites; frozen for review'],cwd=R,check=True)
 assert git('rev-parse','v6lite-batch33^{commit}')==commit
 inventory={n:hashlib.sha256(git('show','v6lite-batch33:'+n,raw=True)).hexdigest() for n in f['inventory']}
 assert inventory==f['inventory'],'tag source bytes differ from tested source'
 manifest=json.loads(git('show','v6lite-batch33:docs/batch33_evidence/SHA256SUMS.json'))
 assert all(hashlib.sha256(git('show','v6lite-batch33:'+n,raw=True)).hexdigest()==h for n,h in manifest['files'].items())
 assert not git('diff','--name-only','--','v6lite','scripts')
 print(json.dumps({'commit':commit,'tag':'v6lite-batch33','tag_object':git('rev-parse','v6lite-batch33'),'code_sha':f['code_sha'],'manifest_sha256':sha(O/'SHA256SUMS.json'),'tested_source_tag_verified':True,'manifest_blobs_verified':len(manifest['files']),'no_push':True,'frozen':True},indent=2))
if __name__=='__main__':main()

"""Explicit local-only 31 commit/tag and byte-for-byte archive verification."""
from pathlib import Path,PurePosixPath
import hashlib,io,json,subprocess,sys,tarfile

ROOT=Path('C:/Dev/V6Lite-dev');OUT=ROOT/'docs/batch31_evidence'
TAG='v6lite-batch31';BASE='86cbb435db0ab501d5bcb5cc242e2e1fac881555'
sys.path.insert(0,str(ROOT))
from v6lite.telemetry import redact_accounts
from v6lite_b31_inputs import verify_inputs
def git(*args,check=True):
 result=subprocess.run(['git',*args],cwd=ROOT,check=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if check and result.returncode:
  raise SystemExit('git '+args[0]+' failed: '+redact_accounts((result.stdout+result.stderr).decode('utf-8',errors='replace')))
 return result
def sha(b):return hashlib.sha256(b).hexdigest()
def files():
 return {p.relative_to(ROOT).as_posix():sha(p.read_bytes()) for base in ('v6lite','scripts')
  for p in sorted((ROOT/base).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def archive(ref):
 raw=git('archive','--format=tar',ref,'v6lite','scripts').stdout
 with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
  return {m.name:tar.extractfile(m).read() for m in tar.getmembers() if m.isfile() and '__pycache__' not in PurePosixPath(m.name).parts}
def main():
 mode=sys.argv[1];assert mode in ('commit','verify')
 freeze=json.loads((OUT/'freeze.json').read_text());close=json.loads((OUT/'closeout.json').read_text())
 manifest_path=OUT/'SHA256SUMS.json';manifest=json.loads(manifest_path.read_text())
 assert files()==freeze['inventory']
 assert verify_inputs()==freeze['read_only_inputs']
 assert json.loads((OUT/'scope_resolution.json').read_text(encoding='utf-8'))==freeze['scope_resolution']
 assert close['base_commit']==BASE and close['serial_full_runs']==3 and close['source_unchanged']
 assert close['code_sha']==freeze['code_sha']==manifest['code_sha']
 assert close['manifest_sha256']==sha(manifest_path.read_bytes())
 assert all(sha((OUT/n).read_bytes())==h for n,h in freeze['validation_tools'].items())
 for name,h in manifest['files'].items():
  assert not name.startswith('docs/batch31_claude_evidence/'),'read-only evidence must never be staged'
  assert sha((ROOT/name).read_bytes())==h,name
 if mode=='commit':
  if freeze.get('acceptance_blockers'):
   resolution_path=OUT/'scope_resolution.json'
   assert resolution_path.exists(), 'Original instruction 3.4 remains unmet: no commit/tag without actual legacy evidence or explicit user scope resolution'
   resolution=json.loads(resolution_path.read_text(encoding='utf-8'))
   assert resolution.get('authorized_by')=='user' and resolution.get('decision')=='accept_conservative_legacy_freeze' and resolution.get('quote'), 'Do not fabricate acceptance resolution; it must record a direct user instruction'
  assert git('branch','--show-current').stdout.decode().strip()=='dev'
  assert git('rev-parse','HEAD').stdout.decode().strip()==BASE
  assert not git('diff','--cached','--name-only').stdout.strip(),'foreign staged files: do not unstage them'
  assert git('show-ref','--verify','--quiet','refs/tags/'+TAG,check=False).returncode==1,'tag already exists: never overwrite'
  paths=json.loads(Path('C:/tmp/v6lite_b31_stage_paths.json').read_text())
  assert len(paths)==len(set(paths))
  expected=set(manifest['files'])|{'docs/batch31_evidence/SHA256SUMS.json','docs/batch31_evidence/closeout.json'}
  assert set(paths)==expected
  for name in paths:
   rel=PurePosixPath(name)
   assert not rel.is_absolute() and '..' not in rel.parts and ':' not in name
   assert name in freeze['owned_files'] or name.startswith('docs/batch31_evidence/') or name=='docs/V6_LITE_FIX_REPORT_ROUND31_2026-10-27.md'
   assert name!='docs/V6_LITE_OPERATOR_MANUAL.md'
   assert not name.startswith('docs/batch31_claude_evidence/')
   assert (ROOT/name).is_file()
  changed=set(git('diff','--name-only').stdout.decode().splitlines())
  assert not {p for p in changed if p.startswith(('v6lite/','scripts/')) and p not in paths},'unowned package/source changes: inspect before continuing'
  git('add','--',*paths)
  assert set(git('diff','--cached','--name-only').stdout.decode().splitlines())==set(paths)
  # Preserve the hash-verified operator instruction byte for byte, including
  # intentional Markdown hard-break spaces. Raw test receipts are immutable.
  git('-c','core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol','diff','--cached','--check','--',*[p for p in paths if p.startswith('v6lite/') or p=='docs/V6_LITE_FIX_REPORT_ROUND31_2026-10-27.md'])
  git('commit','-m','fix: confirm overnight stop amendments and defer closing cards (batch31)',
      '-m',f"code_sha={freeze['code_sha']}; report SHA-256={close['report_sha256']}; three serial Windows full runs passed. Local dev delivery only.")
  git('tag','-a',TAG,'-m',f"V6-Lite batch31; code_sha={freeze['code_sha']}; report SHA-256={close['report_sha256']}; no merge/push/deploy.")
 assert git('cat-file','-t',TAG).stdout.decode().strip()=='tag'
 commit=git('rev-parse',TAG+'^{commit}').stdout.decode().strip()
 assert commit==git('rev-parse','HEAD').stdout.decode().strip()
 contents=archive(TAG)
 assert {name:sha(data) for name,data in contents.items()}==freeze['inventory']
 h=hashlib.sha256()
 for name,data in sorted(contents.items()):
  if name.startswith('v6lite/'):
   h.update(name[len('v6lite/'):].encode());h.update(b'\0');h.update(data);h.update(b'\0')
 assert h.hexdigest()==freeze['package_sha256']==close['package_sha256']
 assert h.hexdigest()[:16]==freeze['code_sha']
 for name,digest in manifest['files'].items():
  assert sha(git('show',TAG+':'+name).stdout)==digest,('tag blob mismatch',name)
 assert sha(git('show',TAG+':docs/batch31_evidence/SHA256SUMS.json').stdout)==close['manifest_sha256']
 assert sha(git('show',TAG+':docs/V6_LITE_FIX_REPORT_ROUND31_2026-10-27.md').stdout)==close['report_sha256']
 result={'commit':commit,'tag':TAG,'annotated':True,'code_sha':freeze['code_sha'],
  'package_sha256':h.hexdigest(),'archive_inventory_matches':True,
  'report_sha256':close['report_sha256'],'manifest_sha256':close['manifest_sha256'],
  'tag_blobs_match_manifest':True,'merge_push_deploy':False,
  'tracked_status_after':git('status','--short','--untracked-files=no').stdout.decode().splitlines()}
 Path('C:/tmp/v6lite_b31_delivery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()

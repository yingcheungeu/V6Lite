"""Audit and optionally stage only the authorized batch22a allowlist."""
from pathlib import Path
import argparse,hashlib,json,re,subprocess
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
SOURCE={'v6lite/models.py','v6lite/broker/ibkr.py','v6lite/engine.py','v6lite/protection_engine.py','v6lite/protection_incidents.py','v6lite/protection_cancellations.py','v6lite/tests/test_batch20_card_provenance.py','v6lite/tests/test_batch20_protection_incident.py','v6lite/tests/test_batch22_config_broker.py','v6lite/tests/test_batch22_protection_lifecycle.py','v6lite/tests/test_batch22_item9_broker_cancel.py'}
DOCS={'docs/V6_LITE_BATCH22_ITEM9_FIX_REPORT_2026-10-09.md','docs/V6_LITE_BATCH22A_FULL_VALIDATION_2026-10-09.md'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT).decode('utf-8').strip()
def names(*args):return {p for p in subprocess.check_output(['git',args[0],'-z',*args[1:]],cwd=ROOT).decode('utf-8').split('\0') if p}
parser=argparse.ArgumentParser();parser.add_argument('--stage',action='store_true');ns=parser.parse_args()
assert ROOT==Path('C:/Dev/V6Lite-dev') and git('branch','--show-current')=='dev'
assert git('rev-parse','HEAD')=='42c77da139ce308ca133eb7e2e40beaf33ecac9d'
assert git('rev-parse','v6lite-batch22')=='18f44ab40bc934327c6cacd921e6717ff21c3a80'
assert not names('diff','--cached','--name-only'),'index must start empty'
plan=json.loads((EV/'commit_plan.json').read_text(encoding='utf-8'))
assert plan['code_sha']=='f27e144de9eb406b' and plan['intended_tag']=='v6lite-batch22a'
paths=set()
for i in plan['files']:
 p=ROOT/i['path']; assert p.resolve().is_relative_to(ROOT.resolve()) and p.relative_to(ROOT).as_posix()==p.resolve().relative_to(ROOT.resolve()).as_posix()
 assert p.is_file() and p.stat().st_size==i['bytes'] and sha(p)==i['sha256'],i['path']
 paths.add(i['path'])
paths.add((EV/'commit_plan.json').relative_to(ROOT).as_posix())
receipt=EV/'independent_review_receipt.json'
assert receipt.is_file(),'independent full-suite receipt required before staging'
review=json.loads(receipt.read_text(encoding='utf-8'));assert review['verdict']=='PASS' and review['code_sha']==plan['code_sha']
paths.add(receipt.relative_to(ROOT).as_posix())
manifest=json.loads((EV/'sha256_manifest.json').read_text(encoding='utf-8'))
assert manifest['full_gate']=='PASS' and all((ROOT/i['path']).stat().st_size==i['bytes'] and sha(ROOT/i['path'])==i['sha256'] for i in manifest['entries'])
for path in paths:
 assert path in SOURCE or path in DOCS or path.startswith(('docs/batch22_item9_evidence/','docs/batch22a_evidence/')),path
 assert '__pycache__' not in Path(path).parts and Path(path).suffix.lower() in {'.py','.md','.json','.txt','.xml','.log'},path
 assert (ROOT/path).stat().st_size<10*1024*1024,'large file: '+path
patterns={'discord_webhook':r'https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{25,}','github_token':r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}','private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'}
findings=[]
for path in paths:
 content=(ROOT/path).read_text(encoding='utf-8-sig')
 findings.extend({'path':path,'kind':kind} for kind,pattern in patterns.items() if re.search(pattern,content))
assert not findings,json.dumps(findings)
all_source=names('diff','--name-only','--','v6lite')|names('ls-files','--others','--exclude-standard','--','v6lite')
assert all_source==SOURCE,'unexpected/missing source scope: '+str(sorted(all_source^SOURCE))
if ns.stage:
 subprocess.run(['git','-c','core.autocrlf=false','add','-f','--',*sorted(paths)],cwd=ROOT,check=True)
 assert names('diff','--cached','--name-only')==paths,'staged range differs from allowlist'
 for path in sorted(paths):
  assert hashlib.sha256(subprocess.check_output(['git','show',':'+path],cwd=ROOT)).hexdigest()==sha(ROOT/path),'staged bytes differ: '+path
 subprocess.run(['git','-c','core.whitespace=cr-at-eol','diff','--cached','--check'],cwd=ROOT,check=True)
print(json.dumps({'staged':ns.stage,'allowlisted_files':len(paths),'source_files':len(SOURCE),'secret_findings':len(findings),'largest_file_bytes':max((ROOT/p).stat().st_size for p in paths),'intended_tag':plan['intended_tag']},ensure_ascii=False))

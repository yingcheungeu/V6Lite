"""Batch23 exact-scope hash/secrets/size gate; optional explicit stage, no commit."""
from pathlib import Path
import argparse,hashlib,json,re,subprocess,sys
ROOT=Path(__file__).resolve().parents[2];EV=Path(__file__).resolve().parent
sys.path.insert(0,str(EV))
from run_batch23_checks import sha,package_sha,PRESERVED,BASELINE_COMMIT
PRODUCTION={
 'v6lite/config.py','v6lite/models.py','v6lite/broker/ibkr.py','v6lite/engine.py',
 'v6lite/protection_engine.py','v6lite/protection_incidents.py','v6lite/tp_evidence.py',
 'v6lite/store.py','v6lite/report.py','v6lite/__main__.py','v6lite/tools/g2_probe.py',
 'v6lite/examples/live.toml','v6lite/examples/paper.toml','v6lite/examples/paper_delayed.toml',
 'v6lite/examples/paper_g3n.toml','v6lite/examples/paper_test.toml'}
TEST_ALLOW={'v6lite/tests/test_review_round2.py','v6lite/tests/test_batch23_recovery_diagnostic.py','v6lite/tests/test_batch23_terminal_fact.py','v6lite/tests/test_batch23_broker_diagnostics.py','v6lite/tests/test_batch23_tp_evidence.py','v6lite/tests/test_batch23_contracts.py','v6lite/tests/test_batch23_error_provenance.py','v6lite/tests/test_batch23_unprotected.py',
 'v6lite/tests/test_batch16.py','v6lite/tests/test_batch16_f8_backoff.py',
 'v6lite/tests/test_batch20_card_provenance.py','v6lite/tests/test_batch20_protection_equivalents.py',
 'v6lite/tests/test_review_fixes.py','v6lite/tests/test_batch22_item9_broker_cancel.py','v6lite/tests/test_batch22_config_broker.py','v6lite/tests/test_batch22_protection_lifecycle.py'}
DOCS={'docs/V6_LITE_BATCH23_INSTRUCTIONS.md','docs/V6_LITE_FIX_REPORT_ROUND23_2026-10-09.md'}
EXCLUDED_PARTS={'__pycache__','.pytest_cache','baseline22a','test_tmp','sample_tmp'}
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT).decode('utf-8').strip()
def names(*args):return {p for p in subprocess.check_output(['git',args[0],'-z',*args[1:]],cwd=ROOT).decode('utf-8').split('\0') if p}
parser=argparse.ArgumentParser();parser.add_argument('--stage',action='store_true');ns=parser.parse_args()
assert ROOT==Path('C:/Dev/V6Lite-dev') and git('branch','--show-current')=='dev'
assert git('rev-parse','HEAD')==BASELINE_COMMIT and git('rev-parse','v6lite-batch22a^{}')==BASELINE_COMMIT
assert not names('diff','--cached','--name-only'),'index must start empty'
assert not subprocess.run(['git','show-ref','--verify','--quiet','refs/tags/v6lite-batch23'],cwd=ROOT).returncode==0,'new tag already exists'
assert all(sha(ROOT/p)==h for p,h in PRESERVED.items()),'preserved bytes changed'
plan=json.loads((EV/'commit_plan.json').read_text(encoding='utf-8'))
assert plan['intended_tag']=='v6lite-batch23' and plan['code_sha']==package_sha(ROOT/'v6lite')
paths=set()
for item in plan['files']:
 p=ROOT/item['path']
 assert p.resolve().is_relative_to(ROOT.resolve()) and p.relative_to(ROOT).as_posix()==p.resolve().relative_to(ROOT.resolve()).as_posix()
 assert p.is_file() and p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],item['path']
 paths.add(item['path'])
paths.add((EV/'commit_plan.json').relative_to(ROOT).as_posix())
receipt=EV/'independent_final_receipt.json';assert receipt.is_file()
review=json.loads(receipt.read_text(encoding='utf-8'))
assert review['verdict']=='PASS' and review['code_sha']==plan['code_sha']
assert review['report_sha256']==sha(ROOT/'docs/V6_LITE_FIX_REPORT_ROUND23_2026-10-09.md')
assert review['manifest_sha256']==sha(EV/'sha256_manifest.json')
assert review['commit_plan_sha256']==sha(EV/'commit_plan.json')
paths.add(receipt.relative_to(ROOT).as_posix())
manifest=json.loads((EV/'sha256_manifest.json').read_text(encoding='utf-8'))
assert manifest['full_gate']=='PASS' and manifest['code_sha']==plan['code_sha']
for i in manifest['entries']:
 m=ROOT/i['path']
 assert not Path(i['path']).is_absolute() and m.resolve().is_relative_to(ROOT.resolve())
 assert m.relative_to(ROOT).as_posix()==m.resolve().relative_to(ROOT.resolve()).as_posix()
 assert m.is_file() and m.stat().st_size==i['bytes'] and sha(m)==i['sha256']
sealed_evidence={i['path'] for i in manifest['entries'] if i['path'].startswith('docs/batch23_evidence/')}
required=PRODUCTION|DOCS|sealed_evidence|{'docs/batch23_evidence/sha256_manifest.json','docs/batch23_evidence/commit_plan.json','docs/batch23_evidence/independent_final_receipt.json'}
assert required<=paths,'missing sealed mandatory evidence'
assert paths<=PRODUCTION|TEST_ALLOW|DOCS|sealed_evidence|{'docs/batch23_evidence/sha256_manifest.json','docs/batch23_evidence/commit_plan.json','docs/batch23_evidence/independent_final_receipt.json'},'unsealed added files'
for path in paths:
 assert path in PRODUCTION|TEST_ALLOW|DOCS or path.startswith('docs/batch23_evidence/'),path
 assert not EXCLUDED_PARTS.intersection(Path(path).parts),path
 assert Path(path).suffix.lower() in ({'.py','.md','.json','.txt','.xml','.log'} if path.startswith('docs/batch23_evidence/') else {'.py','.md','.toml'}),path
 assert (ROOT/path).stat().st_size<10*1024*1024,'large file: '+path
 assert Path(path).name.lower() not in {'.env','v6lite.paper.toml','v6lite.g3t.toml','v6lite.g3n.toml'}
patterns={'discord_webhook':r'https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{25,}',
 'github_token':r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}',
 'discord_bot_token':r'(?:[MN][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}|mfa\.[A-Za-z0-9_-]{80,})',
 'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'}
findings=[]
for path in paths:
 content=(ROOT/path).read_text(encoding='utf-8-sig')
 findings.extend({'path':path,'kind':kind} for kind,pattern in patterns.items() if re.search(pattern,content))
assert not findings,json.dumps(findings)
source=names('diff','--name-only','--','v6lite','scripts')|names('ls-files','--others','--exclude-standard','--','v6lite','scripts')
expected={p for p in paths if p.startswith(('v6lite/','scripts/'))}
assert source==expected,'unaccounted source scope: '+str(sorted(source^expected))
assert PRODUCTION<=expected and expected<=PRODUCTION|TEST_ALLOW
if ns.stage:
 subprocess.run(['git','-c','core.autocrlf=false','add','-f','--',*sorted(paths)],cwd=ROOT,check=True)
 assert names('diff','--cached','--name-only')==paths,'staged scope differs'
 for path in paths:assert hashlib.sha256(subprocess.check_output(['git','show',':'+path],cwd=ROOT)).hexdigest()==sha(ROOT/path),'index bytes differ: '+path
 current_tools={'run_batch23_checks.py','evidence_validation.py','seal_report.py','audit_commit.py','render_samples.py'}
 authored=[p for p in paths if p.startswith('v6lite/') or Path(p).parent.as_posix()=='docs/batch23_evidence' and Path(p).name in current_tools]
 subprocess.run(['git','-c','core.whitespace=cr-at-eol','diff','--cached','--check','--',*sorted(authored)],cwd=ROOT,check=True)
print(json.dumps({'staged':ns.stage,'allowlisted_files':len(paths),'source_files':len(source),'secret_findings':0,'largest_file_bytes':max((ROOT/p).stat().st_size for p in paths),'code_sha':plan['code_sha'],'tag':plan['intended_tag']},ensure_ascii=False))

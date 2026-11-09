"""Local B28c commit/tag only after checked closeout; never push or merge."""
from pathlib import Path
import hashlib,io,json,subprocess,zipfile

ROOT=Path('C:/Dev/V6Lite-dev');OUT=ROOT/'docs/batch28c_evidence'
TAG='v6lite-batch28c'
def get(*args):return subprocess.check_output(args,cwd=ROOT)
def run(*args):return subprocess.run(args,cwd=ROOT,check=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def package_digest(ref):
    digest=hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(get('git','archive','--format=zip',ref,'v6lite'))) as bundle:
        for name in sorted(n for n in bundle.namelist() if not n.endswith('/') and '__pycache__' not in n.split('/')):
            digest.update(name[len('v6lite/'):].encode()+b'\0'+bundle.read(name)+b'\0')
    return digest.hexdigest()

paths=json.loads(Path('C:/tmp/v6lite_b28c_stage_paths.json').read_text())
freeze=json.loads((OUT/'freeze.json').read_text())
closing=json.loads((OUT/'closeout.json').read_text())
manifest=json.loads((OUT/'SHA256SUMS.json').read_text())
assert get('git','branch','--show-current').decode().strip()=='dev'
assert get('git','rev-parse','HEAD').decode().strip()==freeze['base_commit']
assert not get('git','tag','--list',TAG).strip(), 'existing tag must be reviewed, never overwritten'
existing_staged=set(get('git','diff','--cached','--name-only').decode().splitlines())
note='docs/batch28c_evidence/commit_check_note.json'
assert existing_staged in (set(),set(paths),set(paths)-{note}), 'unrelated staged work must be preserved'
assert closing['serial_full_runs']==3 and closing['same_inventory'] and closing['source_unchanged']
assert closing['all_full_after_1330_pt'] and not closing['operator_manual_staged']
assert sha(OUT/'SHA256SUMS.json')==closing['manifest_sha256']
assert sha(ROOT/'docs/V6_LITE_FIX_REPORT_ROUND28C_2026-10-21.md')==closing['report_sha256']
assert len(paths)==len(set(paths))
assert 'docs/V6_LITE_OPERATOR_MANUAL.md' not in paths
assert set(paths)==set(manifest['files'])|{'docs/batch28c_evidence/SHA256SUMS.json','docs/batch28c_evidence/closeout.json'}
for name,digest in manifest['files'].items():assert sha(ROOT/name)==digest,name
manual_before=sha(ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md')
run('git','add','-f','--',*paths)
staged=set(get('git','diff','--cached','--name-only').decode().splitlines())
assert staged==set(paths),(staged-set(paths),set(paths)-staged)
for name in paths:assert get('git','show',':'+name)==(ROOT/name).read_bytes(),name
exclusions=json.loads((ROOT/note).read_text())['raw_log_whitespace_exclusions']
assert all(p in paths and p.startswith('docs/batch28c_evidence/') and p.endswith('.txt') for p in exclusions)
run('git','-c','core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
    'diff','--cached','--check','--','.',*[f':(exclude){p}' for p in exclusions])
tree=get('git','write-tree').decode().strip()
assert package_digest(tree)==freeze['package_sha256'], 'index package must equal tested package'
message='fix: confirm overnight stop amendments and bound TP handoff latency (batch28c)'
run('git','commit','--only','-m',message,'--',*paths)
commit=get('git','rev-parse','HEAD').decode().strip()
changed=set(get('git','diff-tree','--no-commit-id','--name-only','-r',commit).decode().splitlines())
assert changed==set(paths),(changed-set(paths),set(paths)-changed)
annotation=f"V6-Lite batch28c; code_sha {freeze['code_sha']}; three consecutive Windows full passes after 13:30 PT"
run('git','tag','-a',TAG,'-m',annotation)
assert get('git','cat-file','-t',TAG).decode().strip()=='tag'
assert get('git','rev-parse',TAG+'^{commit}').decode().strip()==commit
actual=package_digest(TAG)
assert actual==freeze['package_sha256']
assert sha(ROOT/'docs/V6_LITE_OPERATOR_MANUAL.md')==manual_before
for name,digest in manifest['files'].items():
    assert hashlib.sha256(get('git','show',TAG+':'+name)).hexdigest()==digest,name
assert not get('git','diff','--cached','--name-only').strip()
receipt={'commit':commit,'tag':TAG,'annotated_tag':True,'code_sha':freeze['code_sha'],
 'package_sha256':actual,'tag_archive_matches_tested_package':True,
 'report_sha256':closing['report_sha256'],'manifest_sha256':closing['manifest_sha256'],
 'consecutive_full_runs':3,'all_full_after_1330_pt':True,'manual_untouched_and_unstaged':True,
 'merged':False,'pushed':False,'deployed':False}
Path('C:/tmp/v6lite-b28c-final-delivery.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2))

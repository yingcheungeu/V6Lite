from pathlib import Path
import hashlib,io,json,subprocess,zipfile
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28_evidence'
paths=json.loads(Path('C:/tmp/v6lite_b28_section2_stage_paths.json').read_text())
freeze=json.loads((out/'freeze.json').read_text())
def run(*args,**kwargs):return subprocess.run(args,cwd=root,check=True,**kwargs)
def get(*args):return subprocess.check_output(args,cwd=root)
def package_digest(ref):
    archive=get('git','archive','--format=zip',ref,'v6lite')
    digest=hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for name in sorted(n for n in bundle.namelist() if not n.endswith('/') and '__pycache__' not in n.split('/')):
            digest.update(name[len('v6lite/'):].encode()+b'\0'+bundle.read(name)+b'\0')
    return digest.hexdigest()
assert get('git','branch','--show-current').decode().strip()=='dev'
assert get('git','rev-parse','HEAD').decode().strip()==freeze['baseline_commit']
assert not get('git','tag','--list','v6lite-batch28').strip()
manifest=json.loads((out/'SHA256SUMS.json').read_text())
for name,digest in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
run('git','add','--',*paths)
for name in paths:
    assert get('git','show',':'+name)==(root/name).read_bytes(),name
assert 'docs/V6_LITE_OPERATOR_MANUAL.md' not in paths
planned_tree=get('git','write-tree').decode().strip()
assert package_digest(planned_tree)==freeze['package_sha256']
run('git','commit','--only','-m','fix: stabilize V6-Lite account probes and daily audit summaries (batch28)','--',*paths)
commit=get('git','rev-parse','HEAD').decode().strip()
changed=set(get('git','diff-tree','--no-commit-id','--name-only','-r',commit).decode().splitlines())
assert changed==set(paths),(changed-set(paths),set(paths)-changed)
run('git','tag','-a','v6lite-batch28','-m',f"V6-Lite batch28 section2 on accepted batch28a; code_sha {freeze['code_sha']}; three consecutive Windows full passes")
assert get('git','rev-parse','v6lite-batch28^{commit}').decode().strip()==commit
actual_digest=package_digest('v6lite-batch28')
assert actual_digest==freeze['package_sha256'],(actual_digest,freeze['package_sha256'])
receipt={'commit':commit,'tag':'v6lite-batch28','code_sha':freeze['code_sha'],'package_sha256':actual_digest,'tag_archive_matches_tested_package':True,'report_sha256':hashlib.sha256((root/'docs/V6_LITE_FIX_REPORT_ROUND28_SECTION2_2026-10-20.md').read_bytes()).hexdigest(),'merged':False,'pushed':False,'deployed':False}
Path('C:/tmp/v6lite-b28-final-delivery.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2))

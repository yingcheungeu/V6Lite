from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,io,json,os,subprocess,sys,zipfile
import xml.etree.ElementTree as ET
root=Path('C:/Dev/V6Lite-dev');out=root/'docs/batch28c_evidence'
label=sys.argv[1]; base=Path('C:/tmp')/('v6lite-b28c-baseline-'+label)
assert not base.exists() and not (out/f'{label}.json').exists()
base.mkdir()
raw=subprocess.check_output(['git','archive','--format=zip','v6lite-batch28b','v6lite','scripts','.gitignore','.gitattributes'],cwd=root)
zipfile.ZipFile(io.BytesIO(raw)).extractall(base)
specs=sorted((root/'v6lite/tests').glob('test_batch28c_*.py'))
assert specs
hashes={}
for spec in specs:
    rel=spec.relative_to(root);data=spec.read_bytes();(base/rel).write_bytes(data)
    hashes[rel.as_posix()]=hashlib.sha256(data).hexdigest()
start=datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()
cmd=[sys.executable,'-B','-m','pytest',*[p.relative_to(root).as_posix() for p in specs],'-q','--tb=short',
     f'--basetemp=C:/tmp/v6lite-b28c-red-tests-{label}',f'--junitxml={out}/{label}.xml']
proc=subprocess.run(cmd,cwd=base,env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'},stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
sys.path.insert(0,str(root))
from v6lite.telemetry import redact_accounts
stdout=redact_accounts(proc.stdout.decode('utf-8',errors='replace'))
(out/f'{label}.txt').write_text(stdout,encoding='utf-8')
xmlpath=out/f'{label}.xml'
counts={}
if xmlpath.exists():
    xmlpath.write_text(redact_accounts(xmlpath.read_text(encoding='utf-8')),encoding='utf-8')
    xml=ET.parse(xmlpath).getroot();suites=[xml] if xml.tag=='testsuite' else list(xml)
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
receipt={'base':'v6lite-batch28b','base_commit':subprocess.check_output(['git','rev-parse','v6lite-batch28b^{}'],cwd=root,text=True).strip(),
         'test_sha256':hashes,'start_pt':start,'end_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),
         'returncode':proc.returncode,'archive_sha256':hashlib.sha256(raw).hexdigest(),
         'stdout_raw_sha256':hashlib.sha256(proc.stdout).hexdigest(),**counts}
(out/f'{label}.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(stdout);print(json.dumps(receipt))

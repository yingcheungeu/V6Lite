import hashlib,io,json,os,subprocess,sys,zipfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
root=Path('C:/Dev/V6Lite-dev')
out=root/'docs/batch28_evidence'
target=Path('C:/tmp/v6lite-b28-section2-baseline')
target.mkdir(exist_ok=True)
spec=(root/'v6lite/tests/test_batch28_section2.py').read_bytes()
assert hashlib.sha256(spec).hexdigest()=='9c79d611507e78d6a3d7dfb51987be97312cc2ae95c0961f7bc734ddb5ea6779'
archive=subprocess.check_output(['git','archive','--format=zip','v6lite-batch28a','v6lite','scripts','.gitignore','.gitattributes'],cwd=root)
with zipfile.ZipFile(io.BytesIO(archive)) as bundle:bundle.extractall(target)
(target/'v6lite/tests/test_batch28_section2.py').write_bytes(spec)
cmd=[sys.executable,'-B','-m','pytest','v6lite/tests/test_batch28_section2.py','-k','not candidate','-q','--tb=short',f'--basetemp={target}/test_tmp',f'--junitxml={out}/baseline_28a.xml']
start=datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()
proc=subprocess.run(cmd,cwd=target,env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True)
raw=proc.stdout+proc.stderr
(out/'baseline_28a.txt').write_bytes(raw)
record={'tag':'v6lite-batch28a','commit':subprocess.check_output(['git','rev-parse','v6lite-batch28a^{commit}'],cwd=root,text=True).strip(),'spec_sha256':hashlib.sha256(spec).hexdigest(),'archive_sha256':hashlib.sha256(archive).hexdigest(),'returncode':proc.returncode,'start_pt':start,'end_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),'excluded_candidate_controls':2}
(out/'baseline_28a.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(raw.decode('utf-8','replace')[-6500:])
print(json.dumps(record))

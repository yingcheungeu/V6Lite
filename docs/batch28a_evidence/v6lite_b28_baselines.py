import hashlib, io, json, os, shutil, subprocess, sys, zipfile
from pathlib import Path
root=Path('C:/Dev/V6Lite-dev')
out=root/'docs/batch28a_evidence/native_connection_lost'
out.mkdir(parents=True,exist_ok=True)
spec=(root/'v6lite/tests/test_batch28_lifecycle.py').read_bytes()
records=[]
env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'}
for tag in ('v6lite-batch25','v6lite-batch26','v6lite-batch27b','v6lite-batch27'):
    target=Path('C:/tmp/v6lite-b28-baselines')/tag
    target.mkdir(parents=True,exist_ok=True)
    archive=subprocess.run(['git','archive','--format=zip',tag,'v6lite','scripts','.gitignore','.gitattributes'],cwd=root,check=True,capture_output=True).stdout
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        z.extractall(target)
    (target/'v6lite/tests/test_batch28_lifecycle.py').write_bytes(spec)
    result=subprocess.run([sys.executable,'-B','-m','pytest','v6lite/tests/test_batch28_lifecycle.py','-k','real_socket_disconnect','-q','--tb=short',f'--basetemp={target}/test_tmp',f'--junitxml={out}/{tag}.xml'],cwd=target,env=env,capture_output=True)
    (out/f'{tag}.txt').write_bytes(result.stdout+result.stderr)
    item={'tag':tag,'commit':subprocess.check_output(['git','rev-parse',tag+'^{commit}'],cwd=root,text=True).strip(),'archive_sha256':hashlib.sha256(archive).hexdigest(),'exit_code':result.returncode,'replay_sha256':hashlib.sha256(spec).hexdigest(),'output_tail':(result.stdout+result.stderr).decode('utf-8','replace').splitlines()[-8:]}
    records.append(item)
    print(json.dumps(item,ensure_ascii=False),flush=True)
(out/'baseline_matrix.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')

"""Serial offline pytest receipts for batch 28 section 2; no operational entrypoints."""
from pathlib import Path
import hashlib, json, os, subprocess, sys, time, xml.etree.ElementTree as ET
from datetime import datetime
import platform
from zoneinfo import ZoneInfo

root=Path('C:/Dev/V6Lite-dev')
out=root/'docs/batch28_evidence'
out.mkdir(exist_ok=True)
label=sys.argv[1]
args=sys.argv[2:]
Path('C:/tmp/v6lite-b28-final-tests').mkdir(parents=True,exist_ok=True)
if args==['previous']:
    args=[str(p.relative_to(root)) for p in sorted((root/'v6lite/tests').glob('test_batch2[2-7]*.py'))]
elif args==['full']:
    args=['v6lite/tests','scripts/tests']
    current=datetime.now(ZoneInfo('America/Los_Angeles'))
    if current.weekday()<5 and (6,0)<=(current.hour,current.minute)<(13,30):
        raise SystemExit('full suite blocked during 06:00-13:30 PT')
def inventory():
    return {str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
            for base in ('v6lite','scripts') for p in sorted((root/base).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}
before=inventory()
(out/f'{label}_inventory.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
start=datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()
cmd=[sys.executable,'-B','-m','pytest','-p','v6lite_b28_progress',*args,'-q','--tb=short',
     f'--basetemp=C:/tmp/v6lite-b28-final-tests/{label}',f'--junitxml={out}/{label}.xml']
env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'}
env['PYTHONPATH']='C:/tmp'+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
env['V6LITE_B28_PROGRESS']=str(out/f'{label}_current_test.json')
began=time.monotonic()
with (out/f'{label}.txt').open('w',encoding='utf-8') as log:
    proc=subprocess.Popen(cmd,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          text=True,encoding='utf-8',errors='replace',bufsize=1)
    for line in proc.stdout:
        log.write(line);log.flush()
        print(line.rstrip(),flush=True)
        (out/f'{label}_progress.json').write_text(json.dumps({'last_line':line.rstrip(),'seconds':round(time.monotonic()-began,2)}),encoding='utf-8')
    rc=proc.wait()
after=inventory()
xml_path=out/f'{label}.xml'
xml=ET.parse(xml_path).getroot() if xml_path.exists() else None
suites=([xml] if xml.tag=='testsuite' else list(xml)) if xml is not None else []
result={'label':label,'platform':platform.platform(),'python':platform.python_version(),'start_pt':start,'end_pt':datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),
        'seconds':round(time.monotonic()-began,3),'returncode':rc,'args':args,
        'tests':sum(int(s.get('tests',0)) for s in suites),
        'failures':sum(int(s.get('failures',0)) for s in suites),
        'errors':sum(int(s.get('errors',0)) for s in suites),
        'skipped':sum(int(s.get('skipped',0)) for s in suites),
        'source_unchanged':before==after,
        'inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()}
result['incomplete']=xml is None
result['failed_cases']=[{'classname':case.get('classname'),'name':case.get('name'),'kind':child.tag,'message':child.get('message','')} for suite in suites for case in suite.findall('testcase') for child in case if child.tag in ('failure','error','skipped')]
(out/f'{label}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result),flush=True)
raise SystemExit(rc)

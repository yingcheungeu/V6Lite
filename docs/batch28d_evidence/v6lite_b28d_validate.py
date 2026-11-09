"""Serial offline B28d receipts; full suite is blocked during the trading window."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib, json, os, platform, subprocess, sys, time
import xml.etree.ElementTree as ET

ROOT=Path('C:/Dev/V6Lite-dev')
OUT=ROOT/'docs/batch28d_evidence'
PT=ZoneInfo('America/Los_Angeles')
sys.path.insert(0,str(ROOT))
from v6lite.telemetry import redact_accounts
from v6lite.version import code_sha

def inventory():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for base in ('v6lite','scripts') for p in sorted((ROOT/base).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}

def main():
    label,*args=sys.argv[1:]
    current=datetime.now(PT)
    if args==['full']:
        if current.weekday()<5 and (6,0)<=(current.hour,current.minute)<(13,30):
            raise SystemExit('Full suite blocked during 06:00-13:30 PT')
        args=['v6lite/tests','scripts/tests']
    elif not args or any(not x.split('::',1)[0].endswith('.py') for x in args):
        raise SystemExit('Focused validation requires explicit test-file paths')
    OUT.mkdir(exist_ok=True)
    Path('C:/tmp/v6lite-b28d-tests').mkdir(parents=True,exist_ok=True)
    if any((OUT/f'{label}{suffix}').exists() for suffix in
           ('.json', '.txt', '.xml', '_inventory.json')):
        raise SystemExit('Receipt or partial artifact already exists; use a new label')
    lock=Path('C:/tmp/v6lite-b28d-validation.lock')
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        raise SystemExit('Another serial B28d validation owns the lock')
    os.write(fd,str(os.getpid()).encode());os.close(fd)
    before=inventory()
    (OUT/f'{label}_inventory.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
    cmd=[sys.executable,'-B','-m','pytest','-p','v6lite_b28d_progress',*args,'-q','--tb=short',
        f'--basetemp=C:/tmp/v6lite-b28d-tests/{label}',f'--junitxml={OUT}/{label}.xml']
    env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'}
    env['PYTHONPATH']=str(OUT)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    env['V6LITE_B28D_PROGRESS']=f'C:/tmp/v6lite-b28d-progress-{label}.json'
    raw_log_digest=hashlib.sha256()
    raw_xml_digest=None
    start=datetime.now(PT).isoformat();began=time.monotonic()
    try:
        with (OUT/f'{label}.txt').open('w',encoding='utf-8') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  text=True,encoding='utf-8',errors='replace',bufsize=1)
            for line in proc.stdout:
                raw_log_digest.update(line.encode('utf-8'))
                safe=redact_accounts(line)
                log.write(safe);log.flush();print(safe.rstrip(),flush=True)
            rc=proc.wait()
        xmlpath=OUT/f'{label}.xml'
        if xmlpath.exists():
            raw_xml_digest=hashlib.sha256(xmlpath.read_bytes()).hexdigest()
            xmlpath.write_text(redact_accounts(xmlpath.read_text(encoding='utf-8')),encoding='utf-8')
            xml=ET.parse(xmlpath).getroot()
            suites=[xml] if xml.tag=='testsuite' else list(xml)
        else:
            suites=[]
        result={'label':label,'platform':platform.platform(),'python':platform.python_version(),
            'start_pt':start,'end_pt':datetime.now(PT).isoformat(),
            'seconds':round(time.monotonic()-began,3),'returncode':rc,'args':args,
            'code_sha':code_sha(), 'source_unchanged':before==inventory(),
            'inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
            'incomplete':not suites,
            'raw_stdout_normalized_utf8_sha256':raw_log_digest.hexdigest(),
            'XML_sha256_before_masking':raw_xml_digest,
            'delivered_log_sha256':hashlib.sha256((OUT/f'{label}.txt').read_bytes()).hexdigest(),
            'delivered_XML_sha256':hashlib.sha256(xmlpath.read_bytes()).hexdigest() if xmlpath.exists() else None,
            **{key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')},
            'failed_cases':[{'classname':case.get('classname'),'name':case.get('name'),'kind':child.tag,
                             'message':redact_accounts(child.get('message',''))}
                            for suite in suites for case in suite.findall('testcase') for child in case
                            if child.tag in ('failure','error','skipped')]}
        (OUT/f'{label}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='failed_cases'}),flush=True)
        if not result['source_unchanged']:
            raise SystemExit('Source changed during validation')
        raise SystemExit(rc)
    finally:
        lock.unlink(missing_ok=True)

if __name__=='__main__':main()
